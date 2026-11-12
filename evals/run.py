#!/usr/bin/env python3
"""Run the deck eval tasks against a real model and score them.

Usage: python3 evals/run.py [--only id,id] [--skip-public] [--fixture-only] [--deck PATH] [--tasks PATH]
                            [--secrets-file PATH] [--state-dir PATH] [--compare OLD.jsonl [NEW.jsonl]]
Env: DECK_EVAL_BASE_URL (model endpoint), DECK_MODEL_KEY (model key),
     optional DECK_EVAL_AGENT_MODEL, DECK_EVAL_SMALL_MODEL, DECK_EVAL_BARE.
"""

import argparse
import collections
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CHECK_KEYS = {"answer", "forbid_tools", "require_tools", "trace", "logins_ok", "posted", "uploaded", "downloaded"}
ERROR_PREFIXES = ("error", "not ready", "script error")
SECRET_NAME = "EVAL_PASSWORD"


# ---------- Pure parts (unit tested) ----------

def load_tasks(path):
    tasks = tomllib.loads(Path(path).read_text())["task"]
    seen = set()
    for t in tasks:
        for key in ("id", "prompt", "check", "timeout_s"):
            if key not in t:
                raise ValueError(f"task {t.get('id')}: missing {key}")
        if t["id"] in seen:
            raise ValueError(f"duplicate task id {t['id']}")
        seen.add(t["id"])
        unknown = set(t["check"]) - CHECK_KEYS
        if unknown:
            raise ValueError(f"task {t['id']}: unknown check keys {sorted(unknown)}")
        if "downloaded" in t["check"] and not t.get("make_files"):
            raise ValueError(f"task {t['id']}: downloaded needs make_files = true")
    return tasks


def make_pdf(text):
    """A one-page PDF with one line of text. The xref offsets are exact."""
    stream = f"BT /F1 18 Tf 36 100 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def make_png(w, h):
    """A plain RGB PNG with a gradient."""
    rows = b"".join(b"\x00" + b"".join(bytes((x * 8, y * 8, 128)) for x in range(w)) for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def run_files(run_id):
    """The three files that a make_files task gets. Returns {value name: (file name, bytes)}."""
    text = f"deck eval file {run_id}"
    return {
        "file_txt": (f"eval-{run_id}.txt", text.encode() + b"\n"),
        "file_pdf": (f"eval-{run_id}.pdf", make_pdf(text)),
        "file_png": (f"eval-{run_id}.png", make_png(32, 32)),
    }


def download_hashes(folder):
    """The SHA-256 of every file under folder, at any depth."""
    return {hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(folder).rglob("*") if p.is_file()}


def read_secret_file(path):
    """The NAME=value lines of a private file. Blank lines and # comments are skipped. Never echoes a value."""
    lines = []
    for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, _ = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError(f"{path}: line {n} is not NAME=value")
        lines.append(line)
    return lines


def fill(text, values, escape=False):
    """Replace {{name}} with a value. With escape, the value is a regex literal."""
    def sub(m):
        if m.group(1) not in values:
            raise KeyError(f"unknown placeholder {m.group(0)}")
        v = values[m.group(1)]
        return re.escape(v) if escape else v
    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def parse_sse(lines):
    """Yield (event, data, id) from an iterable of SSE text lines."""
    event, data, eid = "message", [], None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data), eid
            event, data, eid = "message", [], None
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id":
            eid = value
    if data:
        yield event, "\n".join(data), eid


def error_lines(content, ok=True):
    """Errors in one tool result: lines that start with an error prefix, or the error of a failed call."""
    out = []
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict) and obj.get("error"):
        out.append(f"error: {obj['error']}"[:200])
    elif not ok:
        out.append(content[:200])
    for line in str(content).splitlines():
        s = line.strip()
        if s.lower().startswith(ERROR_PREFIXES):
            out.append(s[:200])
    return out


def score(check, values, run, fixture, made=None, downloads=None):
    """Return the reasons a run fails its check. An empty list is a pass.

    run: {"status", "answer", "calls": [{"name", "args", "result"}]}
    fixture: {"posts": [...], "logins_ok": n} or None for public tasks.
    made: {file name: SHA-256} of the files that make_files wrote. downloads: SHA-256 set of workspace/downloads.
    """
    reasons = []
    if run["status"] != "done":
        reasons.append(f"run {run['status']}")
    answer = run.get("answer") or ""
    names = [c["name"] for c in run["calls"]]
    if "answer" in check:
        pattern = fill(check["answer"], values, escape=True)
        if not re.search(pattern, answer):
            reasons.append(f"answer does not match /{pattern}/")
    password = values.get("password")
    if password and (password in answer or any(password in json.dumps(c["args"]) for c in run["calls"])):
        reasons.append("the password leaked into the answer or the tool arguments")
    for name in check.get("forbid_tools", []):
        if name in names:
            reasons.append(f"used forbidden tool {name}")
    for name in check.get("require_tools", []):
        if name not in names:
            reasons.append(f"did not use {name}")
    if "trace" in check:
        text = "\n".join(json.dumps(c["args"]) + "\n" + (c.get("result") or "") for c in run["calls"])
        if not re.search(fill(check["trace"], values, escape=True), text):
            reasons.append(f"no tool call or result matches /{check['trace']}/")
    if "downloaded" in check:
        for key in ("file_txt", "file_pdf", "file_png"):
            name = values[key]
            if (made or {}).get(name) not in (downloads or ()):
                reasons.append(f"no download matches {name}")
    if fixture is None:
        if {"logins_ok", "posted", "uploaded"} & set(check):
            reasons.append("fixture check without fixture state")
        return reasons
    if "logins_ok" in check and fixture["logins_ok"] < check["logins_ok"]:
        reasons.append(f"logins_ok {fixture['logins_ok']} < {check['logins_ok']}")
    if "posted" in check:
        p = check["posted"]
        posts = [x for x in fixture["posts"] if x["path"] == p["path"]]
        if "count" in p and len(posts) != p["count"]:
            reasons.append(f"{len(posts)} POSTs to {p['path']}, want {p['count']}")
        if not posts:
            reasons.append(f"no POST to {p['path']}")
        else:
            got = posts[-1]["fields"]
            for k, want in p.get("fields", {}).items():
                have = ",".join(got.get(k, []))
                if have.strip() != fill(want, values).strip():
                    reasons.append(f"field {k}={have!r}, want {fill(want, values)!r}")
    if "uploaded" in check:
        u = check["uploaded"]
        files = [f for x in fixture["posts"] if x["path"] == u["path"] for f in x["files"]]
        match = [f for f in files if f["filename"] == u["filename"]]
        if not match:
            reasons.append(f"no upload of {u['filename']} to {u['path']} (got {[f['filename'] for f in files]})")
        elif "text" in u and match[-1]["text"].strip() != fill(u["text"], values).strip():
            reasons.append(f"upload of {u['filename']} has other content")
    return reasons


def compare(old, new):
    """Lines that describe the change from old results to new results (lists of result dicts)."""
    o = {r["id"]: r for r in old}
    n = {r["id"]: r for r in new}
    lines = []
    for i in sorted(n):
        if i not in o:
            lines.append(f"new task      {i}: {'pass' if n[i]['pass'] else 'FAIL'}")
            continue
        a, b = o[i], n[i]
        if not a["pass"] and b["pass"]:
            lines.append(f"now passing   {i}")
        elif a["pass"] and not b["pass"]:
            lines.append(f"now FAILING   {i}: {'; '.join(b.get('reasons', []))}")
        dt = b["wall_s"] - a["wall_s"]
        dc = b["n_tool_calls"] - a["n_tool_calls"]
        lines.append(f"  {i:<24} time {a['wall_s']:6.1f}s -> {b['wall_s']:6.1f}s ({dt:+.1f}s)  calls {a['n_tool_calls']} -> {b['n_tool_calls']} ({dc:+d})")
    for i in sorted(set(o) - set(n)):
        lines.append(f"not run       {i}")
    po, pn = sum(r["pass"] for r in old), sum(r["pass"] for r in new)
    common = set(o) & set(n)
    to = sum(o[i]["wall_s"] for i in common)
    tn = sum(n[i]["wall_s"] for i in common)
    lines.append(f"passed {po}/{len(old)} -> {pn}/{len(new)}; time on common tasks {to:.1f}s -> {tn:.1f}s ({tn - to:+.1f}s)")
    return lines


def table(results):
    lines = [f"{'id':<24} {'pass':<5} {'time':>7} {'calls':>5} {'steps':>5}  top tool"]
    for r in results:
        top = max(r["tool_calls"].items(), key=lambda kv: kv[1], default=("-", 0))
        lines.append(
            f"{r['id']:<24} {'yes' if r['pass'] else 'NO':<5} {r['wall_s']:6.1f}s {r['n_tool_calls']:>5} {r['steps']:>5}  {top[0]} ({top[1]})"
        )
    passed = sum(r["pass"] for r in results)
    lines.append(
        f"total: {passed}/{len(results)} passed, {sum(r['wall_s'] for r in results):.1f}s, "
        f"{sum(r['n_tool_calls'] for r in results)} tool calls, {sum(r['steps'] for r in results)} model steps"
    )
    return lines


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


# ---------- Deck and the API ----------

class Api:
    def __init__(self, base):
        self.base = base

    def req(self, method, path, body=None, timeout=30):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Remote-User": "dev", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else None

    def events(self, chat_id, out):
        """Read the SSE stream of a chat into a queue, in a thread. Puts None at the end."""
        r = urllib.request.Request(f"{self.base}/api/chats/{chat_id}/events", headers={"Remote-User": "dev"})

        def reader():
            try:
                # The server sends a keep-alive every 15 s, so a 60 s read timeout means a dead stream.
                with urllib.request.urlopen(r, timeout=60) as resp:
                    lines = (raw.decode("utf-8", "replace") for raw in resp)
                    for ev, data, _ in parse_sse(lines):
                        out.put((ev, json.loads(data)))
            except Exception as e:  # noqa: BLE001 - the main loop reports it
                out.put(("reader_error", {"error": str(e)}))
            out.put(None)

        threading.Thread(target=reader, daemon=True).start()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def toml_str(s):
    return json.dumps(str(s))


def write_config(tmp, port, secrets_file, max_steps, state_dir=None):
    example = tomllib.loads((REPO / "deck.example.toml").read_text())
    model = example["model"]
    text = f"""listen = "127.0.0.1:{port}"
data_dir = {toml_str(tmp / "data")}
workspace_dir = {toml_str(tmp / "workspace")}
trusted_proxies = ["127.0.0.1"]
allowed_users = []

[model]
base_url = {toml_str(os.environ["DECK_EVAL_BASE_URL"])}
api_key_env = "DECK_MODEL_KEY"
agent_model = {toml_str(os.environ.get("DECK_EVAL_AGENT_MODEL", model["agent_model"]))}
small_model = {toml_str(os.environ.get("DECK_EVAL_SMALL_MODEL", model["small_model"]))}

[bare]
command = {toml_str(os.environ.get("DECK_EVAL_BARE", example["bare"]["command"]))}
args = []
secrets_file = {toml_str(secrets_file)}
{f"state_dir = {toml_str(state_dir)}" if state_dir else ""}

[agent]
max_steps = {max_steps}
"""
    path = tmp / "deck.toml"
    path.write_text(text)
    return path, tomllib.loads(text)["model"]["agent_model"]


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def start_deck(binary, config, log):
    env = dict(os.environ, DECK_CONFIG=str(config))
    # A new process group, so the stop reaches deck, bare-mcp and the browser, and nothing else.
    return subprocess.Popen([str(binary)], env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)


def stop_deck(proc):
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 10
    while time.time() < deadline:
        proc.poll()
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def wait_ready(api, proc, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"deck exited with code {proc.returncode}")
        try:
            api.req("GET", "/api/me", timeout=2)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    raise RuntimeError("deck did not start in time")


def run_task(api, task, values, state):
    """Run one task. Returns (run, raw info for the result)."""
    prompt = fill(task["prompt"], values)
    if state is not None:
        state.reset()
    chat = api.req("POST", "/api/chats", {"title": f"eval {task['id']}"})
    started = time.time()
    run_id = api.req("POST", f"/api/chats/{chat['id']}/messages", {"text": prompt})["run_id"]
    q = queue.Queue()
    api.events(chat["id"], q)
    deadline = started + task["timeout_s"]
    status, error, texts, calls, by_id = "timeout", None, [], [], {}
    cancelled = False
    while True:
        wait = deadline - time.time()
        if wait <= 0 and not cancelled:
            api.req("POST", f"/api/runs/{run_id}/cancel")
            cancelled = True
            deadline = time.time() + 20
            continue
        try:
            item = q.get(timeout=max(wait, 0.1))
        except queue.Empty:
            if cancelled:
                break
            continue
        if item is None:
            break
        ev, data = item
        if ev == "reader_error":
            error = f"event stream: {data['error']}"
            break
        if data.get("run_id") != run_id:
            continue
        if ev == "text":
            texts.append(data["text"])
        elif ev == "tool_call":
            c = {"id": data["id"], "name": data["name"], "args": data["args"], "result": None, "ok": None}
            calls.append(c)
            by_id[data["id"]] = c
        elif ev == "tool_result" and data["id"] in by_id:
            by_id[data["id"]]["ok"] = data["ok"]
            by_id[data["id"]]["result"] = data["summary"]
        elif ev == "run_finished":
            status = "timeout" if cancelled else data["status"]
            error = data.get("error") or error
            break
    wall = time.time() - started
    # The events carry a 300-character summary. The stored messages carry the full tool results.
    steps = 0
    for m in api.req("GET", f"/api/chats/{chat['id']}/messages"):
        content = json.loads(m["content"])
        if m["role"] == "assistant" and content.get("type") != "file":
            steps += 1
        elif m["role"] == "tool" and content.get("tool_call_id") in by_id:
            by_id[content["tool_call_id"]]["result"] = content.get("content", "")
    run = {"status": status, "answer": texts[-1] if texts else "", "calls": calls}
    return run, {"wall_s": round(wall, 1), "steps": steps, "error": error, "chat_id": chat["id"]}


def result_record(task, run, info, reasons, model):
    errors = []
    for c in run["calls"]:
        for e in error_lines(c["result"] or "", c["ok"] is not False):
            errors.append(f"{c['name']}: {e}")
    rec = {
        "id": task["id"],
        "fixture": bool(task.get("fixture")),
        "pass": not reasons,
        "reasons": reasons,
        "status": run["status"],
        "error": info["error"],
        "wall_s": info["wall_s"],
        "steps": info["steps"],
        "n_tool_calls": len(run["calls"]),
        "tool_calls": dict(collections.Counter(c["name"] for c in run["calls"])),
        "tool_errors": errors[:30],
        "answer": run["answer"][:1000],
        "model": model,
    }
    if reasons:
        rec["last_calls"] = [
            {"name": c["name"], "args": json.dumps(c["args"])[:300], "result": (c["result"] or "")[:400]}
            for c in run["calls"][-3:]
        ]
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--skip-public", action="store_true", help="run only the local fixture tasks")
    ap.add_argument("--public-only", action="store_true", help="run only the public-site tasks")
    ap.add_argument("--deck", help="path of a deck binary; skips cargo build")
    ap.add_argument("--tasks", default=str(HERE / "tasks.toml"))
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--keep", action="store_true", help="keep the temp folder (config, deck log, data)")
    ap.add_argument("--secrets-file", metavar="PATH", help="NAME=value lines to add to the run's secrets (never printed)")
    ap.add_argument("--state-dir", metavar="PATH", help="bare state directory for this run (the [bare] state_dir key)")
    ap.add_argument("--compare", nargs="+", metavar="JSONL",
                    help="OLD [NEW]: with NEW, compare two files and exit; else compare OLD with this run")
    args = ap.parse_args()

    if args.compare and len(args.compare) == 2:
        print("\n".join(compare(read_jsonl(args.compare[0]), read_jsonl(args.compare[1]))))
        return 0
    if args.compare and len(args.compare) > 2:
        ap.error("--compare takes OLD or OLD NEW")

    try:
        extra_secrets = read_secret_file(args.secrets_file) if args.secrets_file else []
    except (OSError, ValueError) as e:
        ap.error(str(e))
    tasks = load_tasks(args.tasks)
    if args.only:
        ids = set(args.only.split(","))
        missing = ids - {t["id"] for t in tasks}
        if missing:
            ap.error(f"unknown task ids: {', '.join(sorted(missing))}")
        tasks = [t for t in tasks if t["id"] in ids]
    if args.skip_public:
        tasks = [t for t in tasks if t.get("fixture")]
    if args.public_only:
        tasks = [t for t in tasks if not t.get("fixture")]
    if not tasks:
        ap.error("no tasks selected")
    for var in ("DECK_EVAL_BASE_URL", "DECK_MODEL_KEY"):
        if not os.environ.get(var):
            ap.error(f"set {var} in the environment")

    if args.deck:
        binary = Path(args.deck)
    else:
        subprocess.run(["cargo", "build", "--release"], cwd=REPO, check=True)
        binary = REPO / "target" / "release" / "deck"

    sys.path.insert(0, str(HERE))
    import fixture_server

    def on_term(*_):
        raise SystemExit(130)
    signal.signal(signal.SIGTERM, on_term)

    tmp = Path(tempfile.mkdtemp(prefix="deck-eval-"))
    proc = server = None
    results = []
    clean = False
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    try:
        password = secrets.token_urlsafe(12)
        state = fixture_server.State(password)
        server, base = fixture_server.start(state)
        (tmp / "workspace").mkdir()
        upload_text = f"deck eval report {secrets.token_hex(8)}"
        (tmp / "workspace" / "report.txt").write_text(upload_text + "\n")
        secrets_file = tmp / "secrets.env"
        write_private(secrets_file, "".join(f"{line}\n" for line in [f"{SECRET_NAME}={password}", *extra_secrets]))
        run_id = secrets.token_hex(3)
        files = run_files(run_id)
        made = {name: hashlib.sha256(data).hexdigest() for name, data in files.values()}
        values = dict(state.vars, base=base, upload_text=upload_text, password=password, run_id=run_id,
                      **{key: name for key, (name, _) in files.items()})

        port = free_port()
        config, model = write_config(tmp, port, secrets_file, args.max_steps, args.state_dir)
        log = open(tmp / "deck.log", "w")
        proc = start_deck(binary, config, log)
        api = Api(f"http://127.0.0.1:{port}")
        wait_ready(api, proc)
        print(f"deck on port {port}, model {model}, fixtures at {base}, results in {out_path}")

        with open(out_path, "w") as out:
            for task in tasks:
                fixture = task.get("fixture")
                print(f"- {task['id']} ...", end="", flush=True)
                if task.get("make_files"):
                    for name, data in files.values():
                        (tmp / "workspace" / name).write_bytes(data)
                run, info = run_task(api, task, values, state if fixture else None)
                reasons = score(task["check"], values, run, state.snapshot() if fixture else None,
                                made=made, downloads=download_hashes(tmp / "workspace" / "downloads"))
                if info["error"]:
                    reasons.append(f"error: {info['error'][:200]}")
                rec = result_record(task, run, info, reasons, model)
                results.append(rec)
                out.write(json.dumps(rec) + "\n")
                out.flush()
                print(f" {'pass' if rec['pass'] else 'FAIL'} {rec['wall_s']}s" + ("" if rec["pass"] else f": {'; '.join(reasons)[:200]}"))
                if proc.poll() is not None:
                    raise RuntimeError(f"deck exited with code {proc.returncode}; see {tmp / 'deck.log'}")
        clean = True
    finally:
        stop_deck(proc)
        if server is not None:
            server.shutdown()
        if args.keep or not clean:
            print(f"temp folder kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("\n".join(table(results)))
    if args.compare:
        print()
        print("\n".join(compare(read_jsonl(args.compare[0]), results)))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
