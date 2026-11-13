#!/usr/bin/env python3
"""Setup end-to-end acceptance: the real server API, one private VM, a scripted model, a synthetic fixture.

`baseline` drives POST /api/setups -> approve -> private overlay -> executor -> first task -> server
restart, then checks identity, receipts, idle quiet, cleanup. It needs QEMU/KVM, a frozen base and a
release or debug `deck` binary. It is functional acceptance, not a benchmark.

`protected` checks the frozen protected-input and challenge endpoints. It marks each gate `pending`
when this checkout does not offer the capability. It never reports a mock as a real pass.

`synthetic-login` is the full owner flow on a local login service: protected delivery, a real sign-in
through the guest's bare, a phone approval and a CAPTCHA-style owner wait, a Deck restart during a wait,
and one success action. See docs/setup-synthetic-login.md. It reports `ok` only when every required gate
passes. A skipped or pending gate is a failure there.

Only synthetic data. The secret is generated in memory, sent in one JSON body and never written to a
file, argv or log by this script. Raw evidence stays in --out (outside Git). The manifest holds
identifiers, counts and statuses, never a value.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import setup_synthetic_login as sl
import stage3
import vmproto as v

HERE = Path(__file__).resolve().parent
LOCK = "/tmp/deck-setup-e2e-vm.lock"
TASK = stage3.TASK1
PASS, FAIL, PENDING, NOT_TESTED = "pass", "fail", "pending", "not_tested"


# ---------- pure helpers (unit tested) ----------

def pressure(meminfo, loadavg, min_avail_mib, max_load):
    """None when the host has room for one VM, else the reason to stop."""
    avail = next(int(l.split()[1]) // 1024 for l in meminfo.splitlines() if l.startswith("MemAvailable:"))
    load = float(loadavg.split()[0])
    if avail < min_avail_mib:
        return f"MemAvailable {avail} MiB is below {min_avail_mib} MiB"
    if load > max_load:
        return f"load average {load} is above {max_load}"
    return None


def socket_path_ok(out):
    """QEMU unix sockets must stay under 108 bytes. A setup id is 32 hex characters."""
    longest = Path(out) / "computers" / ("0" * 32) / "run" / "qga.sock"
    return len(str(longest).encode()) < 108


def take_lock(path=LOCK):
    """Exclusive, non-blocking. Keep the returned file open until exit."""
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"another setup end-to-end run holds {path}")
    return f


def variants(value):
    """The byte forms a value could take in a log, a URL, JSON or base64. Kept in memory only."""
    raw = value.encode()
    out = {raw, json.dumps(value).strip('"').encode(), urllib.parse.quote(value, safe="").encode(), urllib.parse.quote_plus(value).encode(),
           base64.b64encode(raw), base64.urlsafe_b64encode(raw), raw.hex().encode(), raw.hex().upper().encode()}
    return {x for x in out if x}


def scan_tree(roots, needles, skip=()):
    """Count files that contain any needle. Report relative paths and counts, never the needle."""
    hits, scanned = [], 0
    for root in map(Path, roots):
        files = [root] if root.is_file() else [f for f in root.rglob("*") if f.is_file() and not f.is_symlink()]
        for f in files:
            if any(f.match(s) for s in skip):
                continue
            scanned += 1
            data = f.read_bytes()
            if any(n in data for n in needles):
                hits.append(str(f.relative_to(root) if root.is_dir() else f.name))
    return {"files_scanned": scanned, "files_with_match": len(hits), "matches": sorted(hits)}


def argv_hits(needles, proc=Path("/proc")):
    """Number of live processes whose argv holds a needle."""
    n = 0
    for d in proc.iterdir():
        if d.name.isdigit():
            try:
                cmd = (d / "cmdline").read_bytes()
            except OSError:
                continue
            n += any(x in cmd for x in needles)
    return n


class ArgvWatcher(threading.Thread):
    """Samples every process's argv for the whole run. Keeps only a count."""

    def __init__(self, needles, every=0.2):
        super().__init__(daemon=True)
        self.needles, self.every, self.hits, self.samples = needles, every, 0, 0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            self.hits += argv_hits(self.needles)
            self.samples += 1
            self.stop.wait(self.every)

    def finish(self):
        self.stop.set()
        self.join()
        return {"samples": self.samples, "argv_hits": self.hits}


class Stop(Exception):
    """A gate failed or an outcome is unknown. Stop the flow; keep the evidence."""


class Manifest:
    def __init__(self, mode):
        self.data = {"mode": mode, "started": time.time(), "gates": {}, "evidence": {}}
        self.needles = set()

    def scrub(self, text):
        """The text with every form of the protected value removed. Used for any text that may hold it."""
        raw = text.encode(errors="replace")
        for n in self.needles:
            raw = raw.replace(n, b"[redacted]")
        return raw.decode(errors="replace")

    def gate(self, name, status, detail=None):
        if detail is not None and self.needles:
            seen = (json.dumps(detail) + str(detail)).encode(errors="replace")
            if any(n in seen for n in self.needles):
                status, detail = FAIL, "[redacted: the gate detail held the protected value]"
        self.data["gates"][name] = {"status": status, **({"detail": detail} if detail is not None else {})}
        print(f"{status.upper():11} {name}" + (f": {detail}" if detail else ""), flush=True)

    def ok(self):
        return not any(g["status"] == FAIL for g in self.data["gates"].values())

    def open_gates(self):
        return sorted(n for n, g in self.data["gates"].items() if g["status"] in (PENDING, NOT_TESTED))

    def require(self, required, optional=()):
        """One closing gate. PASS only when each required gate passed, nothing failed and nothing else was skipped.
        A missing gate, a pending gate or an unlisted not-tested gate is a failure: a skipped dependency is never a pass."""
        gates = self.data["gates"]
        problems = [f"{n}: absent" for n in required if n not in gates]
        problems += [f"{n}: {gates[n]['status']}" for n in required if n in gates and gates[n]["status"] != PASS]
        problems += [f"{n}: {g['status']}" for n, g in gates.items() if g["status"] in (PENDING, NOT_TESTED) and n not in required and n not in optional]
        problems += [f"{n}: fail" for n, g in gates.items() if g["status"] == FAIL and n not in required]
        self.gate("acceptance_complete", FAIL if problems else PASS, "; ".join(sorted(set(problems))) if problems else f"{len(required)} required gates passed")

    def write(self, path):
        self.data["finished"] = time.time()
        self.data["ok"] = self.ok()
        self.data["open_gates"] = self.open_gates()
        text = json.dumps(self.data, indent=1)
        if any(n in text.encode() for n in self.needles):  # last guard: never write the value
            self.data["ok"] = False
            text = self.scrub(text)
        Path(path).write_text(text)


def http(base, method, path, body=None, timeout=30):
    """(status, bytes) without raising on 4xx or 5xx."""
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, method=method, headers={"Remote-User": "e2e", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, ConnectionError):
        return 0, b""


class Client:
    def __init__(self, base):
        self.base = base

    def raw(self, method, path, body=None):
        return http(self.base, method, path, body)

    def json(self, method, path, body=None):
        code, raw = self.raw(method, path, body)
        if code >= 300:
            raise SystemExit(f"FAILED: {method} {path} -> {code}")
        return json.loads(raw) if raw else None

    def setup(self, sid):
        return next((s for s in self.json("GET", "/api/setups") if s["setup"]["id"] == sid), None)

    def until(self, what, cond, timeout, every=0.5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = cond()
            if got:
                return got
            time.sleep(every)
        raise SystemExit(f"FAILED: timed out waiting for {what}")


# ---------- scripted model with usage ----------

class ScriptedModel(stage3.Model):
    """stage3's script, plus a request log (raw evidence) and a usage chunk so accounting settles."""
    log_path = None

    def do_POST(self):
        import io
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if ScriptedModel.log_path:
            with stage3.Model.lock, open(ScriptedModel.log_path, "ab") as f:
                f.write(body + b"\n")
        self.rfile = io.BytesIO(body)
        out = self.wfile
        usage = b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110}}\n\ndata: [DONE]'

        class Wire:  # stage3 frames the reply with Content-Length; HTTP/1.0 closes instead
            def write(_, data):
                return out.write(data.replace(b"data: [DONE]", usage))

            def __getattr__(_, name):
                return getattr(out, name)

        self.wfile = Wire()
        send_header = self.send_header
        self.send_header = lambda k, val: None if k == "Content-Length" else send_header(k, val)
        super().do_POST()


# ---------- the stack under test ----------

def server_log_path(private, n):
    return Path(private) / f"server-{n}-{int(time.time())}.log"


def processes_left(out, proc_root=Path("/proc"), pid=None):
    """Find run-path references after cleanup, excluding this process and its launch ancestors.
    Orphans remain visible even when reparented outside the original process tree."""
    excluded = set()
    parent = os.getpid() if pid is None else pid
    while parent and parent not in excluded:
        excluded.add(parent)
        try:
            status = (proc_root / str(parent) / "status").read_text()
            parent = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
        except (OSError, ValueError, StopIteration):
            break
    left = []
    for entry in proc_root.iterdir():
        if entry.name.isdigit() and int(entry.name) not in excluded:
            try:
                if str(out).encode() in (entry / "cmdline").read_bytes():
                    left.append(entry.name)
            except OSError:
                pass
    return sorted(left, key=int)


class Stack:
    def __init__(self, a):
        self.a = a
        self.starts = 0
        self.service = self.model = None
        self.out = Path(a.out)
        self.rt = self.out / "computers"
        self.procs = {}
        self.cfg = self.out / "private" / "deck.toml"
        self.client = Client(f"http://127.0.0.1:{a.server_port}")

    def write_config(self):
        a = self.a
        self.cfg.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cfg.write_text(f'''listen = "127.0.0.1:{a.server_port}"
data_dir = "{self.out / "data"}"
trusted_proxies = ["127.0.0.1"]
[model]
base_url = "http://127.0.0.1:{a.model_port}/v1"
api_key_env = "DECK_MODEL_KEY"
agent_model = "scripted"
small_model = "scripted"
[bare]
command = "unused"
[agent]
max_steps = 12
[provisioning]
base = "{a.base}"
runtime_root = "{self.rt}"
max_bots = 1
[provisioning.template]
version = 1
bot_id = "template"
runtime_dir = "unused"
harness = "{HERE / "vmproto.py"}"
fixture_port = {a.fixture_port}
idle_secs = {a.idle_secs}
lifecycle_secs = 240
screen_interval_ms = 1000
[provisioning.resources]
cpus = 2
memory_mib = 2048
disk_gib = {a.disk_gib}
[provisioning.limits]
max_cpus = 2
max_memory_mib = 2048
max_disk_gib = {a.disk_gib}
[provisioning.accounting]
currency = "TST"
exponent = 2
period = "e2e"
policy_version = "e2e-1"
ceiling = 1000
allowance = 100
increase_increment = 100
price_version = "e2e"
input_per_million = 1
output_per_million = 1
extra_per_call = 0
max_input_tokens = 200000
max_input_bytes = 1000000
max_output_tokens = 4096
max_response_bytes = 1000000
completion_token_field = false
request_usage = true
''')
        self.cfg.chmod(0o600)

    def start_helpers(self, value=None):
        a = self.a
        if value is not None:  # synthetic-login: the service and the model live in this process, so the value stays in memory
            priv = self.out / "private"
            self.fixture_log = open(priv / "fixture.log", "ab")
            lock = threading.Lock()

            def log(line):
                with lock:
                    self.fixture_log.write(line.encode() + b"\n")
                    self.fixture_log.flush()

            self.service = sl.Service(value, port=a.fixture_port, log=log)
            self.model = sl.Model(port=a.model_port, request_log=priv / "model-requests.jsonl", response_log=priv / "model-responses.jsonl")
            self.service.start()
            self.model.start()
            return
        ScriptedModel.log_path = self.out / "private" / "model-requests.jsonl"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", a.model_port), ScriptedModel)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.fixture_log = open(self.out / "private" / "fixture.log", "ab")
        self.procs["fixture"] = subprocess.Popen([sys.executable, str(HERE / "fixture.py"), "--port", str(a.fixture_port)], stdout=self.fixture_log, stderr=subprocess.STDOUT)

    def start_server(self):
        env = dict(os.environ, DECK_CONFIG=str(self.cfg), DECK_MODEL_KEY="scripted-key")
        self.starts += 1
        log = open(server_log_path(self.out / "private", self.starts), "wb")
        self.procs["server"] = subprocess.Popen([self.a.deck], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=self.out)
        self.client.until("the server", lambda: self.client.raw("GET", "/api/setup-options")[0] == 200 if self.alive("server") else sys.exit("FAILED: the server exited"), 60)

    def alive(self, name):
        p = self.procs.get(name)
        return bool(p) and p.poll() is None

    def stop(self, name, wait=10):
        p = self.procs.get(name)
        if p and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(wait)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def fixture_visits(self):
        self.fixture_log.flush()
        return len(re.findall(rb'"GET / HTTP', (self.out / "private" / "fixture.log").read_bytes()))

    def model_requests(self):
        return stage3.Model.calls

    def vm_dirs(self):
        return [d for d in self.rt.glob("*") if d.is_dir()] if self.rt.exists() else []

    def cleanup(self):
        """Stop only what this run started. Returns what is left."""
        self.stop("server")
        for d in self.vm_dirs():
            subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(d), "kill"], stdin=subprocess.DEVNULL, capture_output=True)
        self.stop("fixture")
        for helper in (self.service, self.model):
            if helper:
                helper.stop()
        if getattr(self, "httpd", None):
            self.httpd.shutdown()
            self.httpd.server_close()
        return processes_left(self.out)


# ---------- baseline gates ----------

def base_identity(base):
    info = json.loads(Path(base).with_suffix(".json").read_text())
    return {"id": info["id"], "sha256": info["sha256"], "virtual_size": info["virtual_size"], "mode": oct(os.stat(base).st_mode & 0o777)}


def verify_base(base):
    return v.sha256_file(Path(base)) == json.loads(Path(base).with_suffix(".json").read_text())["sha256"]


def create_and_approve(c, job, credentials=(), task=TASK):
    row = c.json("POST", "/api/setups", {"job": job})
    sid = row["setup"]["id"]
    draft = dict(row["setup"]["draft"], task=task, credentials=list(credentials))
    code, _ = c.raw("POST", f"/api/setups/{sid}", {"version": row["version"], "draft": draft})
    assert code == 204, f"edit -> {code}"
    row = c.setup(sid)
    code, _ = c.raw("POST", f"/api/setups/{sid}/approve", {"version": row["version"], "revision": row["setup"]["draft"]["revision"]})
    assert code == 204, f"approve -> {code}"
    return sid


def bot_status(c, sid):
    return c.json("GET", f"/api/bots/{sid}")


def baseline(stack, m, base_before):
    a, c = stack.a, stack.client
    t0 = time.monotonic()
    sid = create_and_approve(c, "Read the synthetic fixture and send one report")
    m.data["evidence"]["setup_id"] = sid
    bot_dir = stack.out / "data" / "bots" / sid
    if a.diagnostic_precreate_bot_dir:
        # NOT product behavior: the server should create this directory. Recorded so a pass is never read as clean.
        bot_dir.mkdir(parents=True, exist_ok=True)
        m.data["workarounds"] = ["pre-created the bot data directory before provisioning"]
    row = c.until("setup ready", lambda: (r := c.setup(sid)) and r["runtime_ready"] and r, a.ready_timeout)
    m.gate("setup_ready_through_api", PASS if row["phase"] == "Ready" else FAIL, f"{time.monotonic() - t0:.0f}s, phase {row['phase']}")
    task = f"setup-{sid}"
    m.gate("bot_data_dir_created_by_server", PASS if bot_dir.is_dir() and not a.diagnostic_precreate_bot_dir else (FAIL if not bot_dir.is_dir() else NOT_TESTED), "the artifact and journal path of a provisioned bot must exist" if not bot_dir.is_dir() else ("harness pre-created it" if a.diagnostic_precreate_bot_dir else "created by the server"))
    s = c.until("first task", lambda: (st := bot_status(c, sid)) and any(t["id"] == task and t["state"] in ("done", "failed") for t in st["tasks"]) and not st["working"] and st, a.task_timeout)
    t1 = next(t for t in s["tasks"] if t["id"] == task)
    msgs = c.json("GET", f"/api/chats/{row['chat_id']}/messages")
    blob = json.dumps(msgs)
    sent = sum(1 for x in msgs if json.loads(x["content"]).get("type") == "file")
    m.gate("first_task_done", PASS if t1["state"] == "done" else FAIL, t1.get("detail") or t1["state"])
    m.gate("first_task_effects", PASS if "Deck fixture" in blob and sent == 1 else FAIL, f"fixture page seen={'Deck fixture' in blob}, files sent={sent}")
    snap = subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(stack.rt / sid), "exec",
                           "ps -eo pid,etimes,user,comm | grep -E 'sway|bare|executor' | grep -v grep; echo --; ls -la /run/user/1001/bare 2>&1; echo --; uptime -s; cat /proc/uptime"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    (stack.out / "private" / "guest-after-task.txt").write_text(snap.stdout + snap.stderr)
    m.data["evidence"]["guest_snapshot_file"] = "private/guest-after-task.txt"
    before = {"tasks": len(s["tasks"]), "model": stack.model_requests(), "visits": stack.fixture_visits(), "messages": len(msgs)}
    m.data["evidence"]["before_restart"] = before
    m.gate("fixture_visited", PASS if before["visits"] >= 1 else FAIL, f"{before['visits']} visit(s) before restart")

    # Identity, receipts, artifacts.
    d = stack.rt / sid
    ex = json.loads((d / "executor.json").read_text())
    st = json.loads(subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(d), "status"], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout)
    ok = ex["bot_id"] == sid and bool(ex.get("guest_id")) and st.get("base") == base_before["id"]
    m.gate("image_and_executor_identity", PASS if ok else FAIL, f"base {st.get('base')} expected {base_before['id']}; executor bound to bot={ex['bot_id'] == sid}")
    arts = [f for f in (stack.out / "data").rglob("*") if f.is_file() and "artifact" in str(f).lower()]
    m.data["evidence"]["artifact_files"] = len(arts)
    m.gate("artifact_evidence", PASS if arts else FAIL, f"{len(arts)} artifact file(s) on the host")

    # Restart the server after dispatch. The VM keeps running; nothing may run twice.
    stack.stop("server")
    stack.start_server()
    c.until("runtime ready after restart", lambda: (r := c.setup(sid)) and r["runtime_ready"] and r, a.ready_timeout)
    s2 = c.until("status after restart", lambda: bot_status(c, sid), 60)
    time.sleep(a.settle)
    msgs2 = c.json("GET", f"/api/chats/{row['chat_id']}/messages")
    after = {"tasks": len(bot_status(c, sid)["tasks"]), "model": stack.model_requests(), "visits": stack.fixture_visits(), "messages": len(msgs2)}
    m.data["evidence"]["after_restart"] = after
    same = after == before
    m.gate("restart_no_duplicate_task", PASS if same else FAIL, f"before {before} after {after}")
    task_ids = [t["id"] for t in s2["tasks"]]
    m.gate("restart_task_identity", PASS if task_ids.count(task) == 1 else FAIL, f"{task_ids.count(task)} task(s) named for the setup")

    # Quiet while idle or saved: no extra model calls.
    q0 = stack.model_requests()
    s3 = c.until("idle save", lambda: (x := bot_status(c, sid)) and x["computer"] == "saved" and x, a.idle_secs + 90, every=2)
    time.sleep(a.settle)
    m.gate("idle_save", PASS, f"computer {s3['computer']}")
    m.gate("no_model_calls_while_idle", PASS if stack.model_requests() == q0 else FAIL, f"{stack.model_requests() - q0} extra call(s)")
    st = json.loads(subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(d), "status"], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout)
    m.gate("saved_means_no_vm_process", PASS if not st["running"] and st["state_saved"] else FAIL, json.dumps({k: st[k] for k in ("running", "state_saved")}))
    return sid


def fail_closed_probes(c, sid, m):
    """Without a pending request the new endpoints must refuse. 404 on the route is a pending capability."""
    code, body = c.raw("POST", f"/api/setups/{sid}/secret", {"version": 1, "request_id": "none", "value": "probe"})
    if code in (404, 405):
        m.gate("secret_endpoint_fails_closed", PENDING, f"route absent here ({code}); no delivery was faked")
    else:
        m.gate("secret_endpoint_fails_closed", PASS if code >= 400 and b"probe" not in body else FAIL, f"status {code}")
    code, _ = c.raw("POST", f"/api/setups/{sid}/challenge/respond", {"version": 1, "request_id": "none", "decision": "completed"})
    if code in (404, 405):
        m.gate("challenge_endpoint_fails_closed", PENDING, f"route absent here ({code})")
    else:
        m.gate("challenge_endpoint_fails_closed", PASS if code >= 400 else FAIL, f"status {code}")


# ---------- protected input and challenges ----------

class Guest:
    """What the harness may ask a real guest. The default reads the real VM through QGA."""

    def __init__(self, runtime_dir):
        self.p = v.Paths(runtime_dir)

    def check_secret_file(self, name, value):
        """Metadata only: is NAME=value in the delivered file, and who may read it. The value goes on stdin."""
        script = ("import json,os,sys\nv=sys.stdin.read()\nf=os.path.expanduser('~deck/.config/deck/secrets.env')\n"
                  "t=open(f).read().splitlines() if os.path.exists(f) else []\nn=sys.argv[1]\n"
                  "print(json.dumps({'exists':os.path.exists(f),'mode':oct(os.stat(f).st_mode&0o777) if os.path.exists(f) else None,"
                  "'owner':os.stat(f).st_uid if os.path.exists(f) else None,'name_present':any(l.split('=',1)[0]==n for l in t),"
                  "'value_matches':(n+'='+v) in t}))\n")
        code, out, _ = v.Qga(self.p.qga).exec(["/usr/bin/python3", "-c", script, name], stdin=value)
        return json.loads(out) if code == 0 else {"error": code}


def synthetic_value(profile):
    """A fresh synthetic password. `special` keeps a space and symbols, and avoids characters that would break
    the step line or the secrets file by design (quote, backslash, `$`, `{`, line breaks). `plain` is for diagnosis only."""
    if profile == "plain":
        return "e2e-" + secrets.token_urlsafe(24)
    return "Syn " + secrets.token_urlsafe(9) + " #%&=+;:/?@!*"


def derived_name(label):
    return label if re.fullmatch(r"[A-Z_][A-Z0-9_]*", label) else "DECK_" + hashlib.sha256(label.encode()).hexdigest()[:16].upper()


def protected(c, m, guest, sid, label, value, watch):
    """Frozen contract: POST /api/setups/{id}/secret {version,request_id,value}. All checks are metadata-only."""
    row = c.until("a credential wait", lambda: (r := c.setup(sid)) and (r["setup"].get("wait") or {}).get("kind", {}) and "Credential" in r["setup"]["wait"]["kind"] and r, 120)
    w = row["setup"]["wait"]
    m.gate("credential_wait_label_from_request", PASS if w["kind"]["Credential"]["label"] == label else FAIL)
    bad = [("stale_version", {"version": row["version"] - 1, "request_id": w["request_id"], "value": value}),
           ("wrong_request", {"version": row["version"], "request_id": "wrong", "value": value}),
           ("newline_value", {"version": row["version"], "request_id": w["request_id"], "value": value + "\nX=1"}),
           ("label_in_body", {"version": row["version"], "request_id": w["request_id"], "value": value, "label": "OTHER"})]
    for name, body in bad:
        code, resp = c.raw("POST", f"/api/setups/{sid}/secret", body)
        leaked = any(n in resp for n in variants(value))
        m.gate(f"refuses_{name}", PASS if 400 <= code < 500 and not leaked else FAIL, f"status {code}, echoed={leaked}")
    code, resp = c.raw("POST", f"/api/setups/{sid}/control", {"version": row["version"], "command": {"action": "complete_wait", "request_id": w["request_id"]}})
    m.gate("ordinary_completion_refuses_credentials", PASS if code >= 400 else FAIL, f"status {code}")
    still = c.setup(sid)["setup"].get("wait")
    m.gate("failed_attempts_keep_the_wait", PASS if still and still["request_id"] == w["request_id"] else FAIL)
    code, resp = c.raw("POST", f"/api/setups/{sid}/secret", {"version": row["version"], "request_id": w["request_id"], "value": value})
    leaked = any(n in resp for n in variants(value))
    m.gate("delivery_returns_204", PASS if code == 204 and not leaked else FAIL, f"status {code}, echoed={leaked}")
    after = c.setup(sid)
    cleared = not (after["setup"].get("wait") and after["setup"]["wait"]["request_id"] == w["request_id"])
    m.gate("wait_cleared_only_after_delivery", PASS if code == 204 and cleared else FAIL)
    g = guest.check_secret_file(derived_name(label), value)
    m.gate("guest_file_protected", PASS if g.get("value_matches") and g.get("mode") == "0o600" else FAIL, {k: g.get(k) for k in ("exists", "mode", "owner", "name_present", "value_matches")})
    code, resp = c.raw("POST", f"/api/setups/{sid}/secret", {"version": after["version"], "request_id": w["request_id"], "value": value})
    m.gate("replay_of_a_cleared_request_refused", PASS if 400 <= code < 500 else FAIL, f"status {code}")
    m.data["evidence"]["argv_watch"] = watch


def wait_of(row):
    return ((row or {}).get("setup") or {}).get("wait")


def wait_kind(w):
    """`PhoneApproval`, `Captcha` or `Credential`. The wire form is a string or a one-key object."""
    k = (w or {}).get("kind")
    return k if isinstance(k, str) else next(iter(k), None) if isinstance(k, dict) else None


def login_wait(row, kind, task_id):
    w = wait_of(row)
    return w if w and wait_kind(w) == kind and w.get("step") == {"Login": task_id} else None


def owner_answer_state(row, rid):
    """open | answered | verified | closed | absent. Only a `verified` outcome in the resolved list is service success.
    A wait that merely disappeared is `absent`, never success."""
    s = row["setup"]
    w = s.get("wait")
    if w and w.get("request_id") == rid:
        return "answered" if w.get("answered") else "open"
    for r in s.get("resolved") or []:
        if r.get("request_id") == rid:
            return "verified" if r.get("outcome") == "verified" else "closed"
    return "absent"


def ownership(row, status, executor, task_id):
    """The durable identity of one waiting task. The row version is left out: it may change on a restart."""
    w = wait_of(row) or {}
    t = next((x for x in status.get("tasks", []) if x["id"] == task_id), None)
    return {"setup_id": row["setup"]["id"], "chat_id": row["chat_id"], "request_id": w.get("request_id"), "step": w.get("step"), "kind": wait_kind(w),
            "match_number": w.get("match_number"), "instructions": w.get("instructions"), "answered": bool(w.get("answered")),
            "task_id": t["id"] if t else None, "task_state": t["state"] if t else None, "tasks": len(status.get("tasks", [])),
            "bot_id": executor.get("bot_id"), "guest_id": executor.get("guest_id")}


def ownership_diff(a, b):
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def counts_changed(a, b):
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def challenges(c, m, sid, wait_s):
    """Phone or CAPTCHA waits that exist when this is called. The `protected` mode has no fixture that raises one."""
    row = c.setup(sid)
    w = wait_of(row)
    if wait_kind(w) not in ("PhoneApproval", "Captcha"):
        m.gate("challenge_owner_response", PENDING, "no fixture trigger raised a PhoneApproval or Captcha wait in this mode; use synthetic-login")
        return
    for name, body in [("stale", {"version": row["version"] - 1, "request_id": w["request_id"], "decision": "completed"}),
                       ("wrong_request", {"version": row["version"], "request_id": "wrong", "decision": "completed"}),
                       ("value_field", {"version": row["version"], "request_id": w["request_id"], "decision": "completed", "value": "123456"})]:
        code, _ = c.raw("POST", f"/api/setups/{sid}/challenge/respond", body)
        m.gate(f"challenge_refuses_{name}", PASS if 400 <= code < 500 else FAIL, f"status {code}")
    code, _ = c.raw("POST", f"/api/setups/{sid}/challenge/respond", {"version": row["version"], "request_id": w["request_id"], "decision": "completed"})
    m.gate("challenge_completed_response", PASS if code == 204 else FAIL, f"status {code}")
    state = owner_answer_state(c.setup(sid), w["request_id"])
    m.gate("completion_is_not_service_success", PASS if state in ("answered", "closed") else FAIL, f"state after the owner answer: {state}; only a verified outcome is service success")


def need(m, name, ok, detail=None):
    """Record a gate. A failure stops the flow, so no later gate runs on top of it."""
    m.gate(name, PASS if ok else FAIL, detail)
    if not ok:
        raise Stop(name)


KINDS = {"phone": "PhoneApproval", "captcha": "Captcha"}


def await_owner_wait(c, m, svc, sid, task_id, kind, seen, timeout, status):
    """Wait for this task's new owner wait of `kind`. Check it against the service, not only against itself."""
    wk = KINDS[kind]

    def pick():
        row = c.setup(sid)
        if row and row.get("phase") in ("Investigate", "Exhausted"):
            m.gate(f"{kind}_wait_raised", FAIL, f"setup phase {row['phase']}: an outcome is unknown, nothing was retried")
            raise Stop("unknown outcome")
        w = login_wait(row, wk, task_id)
        return row if w and w["request_id"] not in seen else None

    try:
        row = c.until(f"the {kind} wait", pick, timeout)
    except SystemExit as x:
        m.gate(f"{kind}_wait_raised", FAIL, str(x)[:200])
        raise Stop(f"{kind} wait") from None
    w = login_wait(row, wk, task_id)
    task = next((t for t in status()["tasks"] if t["id"] == task_id), None)
    want = svc.phone_match() if kind == "phone" else svc.captcha_check()
    shown = w.get("match_number") == want if kind == "phone" else bool(want) and want in (w.get("instructions") or "")
    ok = shown and not w.get("answered") and bool(task) and task["state"] == "waiting"
    need(m, f"{kind}_wait_raised", ok, f"request {w['request_id']}, task {task and task['state']}, wait shows the service's id={shown}, answered={w.get('answered')}")
    return row, w


def answer_owner_wait(c, m, svc, model, sid, task_id, kind, row, w, timeout, settle=0.0):
    """Failed answers, the owner action in the service, the answer, the held resume, then verification and a duplicate answer."""
    wk, rid, path = KINDS[kind], w["request_id"], f"/api/setups/{sid}/challenge/respond"
    base = {"version": row["version"], "request_id": rid, "decision": "completed"}
    bad = [("stale", {**base, "version": row["version"] - 1}), ("wrong_request", {**base, "request_id": "wrong"}), ("value_field", {**base, "value": "123456"})]
    codes = {name: c.raw("POST", path, body)[0] for name, body in bad}
    for name, code in codes.items():
        m.gate(f"{kind}_refuses_{name}", PASS if 400 <= code < 500 else FAIL, f"status {code}")
    if any(not 400 <= code < 500 for code in codes.values()):
        raise Stop(f"{kind} refusals")
    after = c.setup(sid)
    still = login_wait(after, wk, task_id)
    need(m, f"{kind}_failed_answers_keep_the_wait", bool(still) and still["request_id"] == rid and not still.get("answered") and owner_answer_state(after, rid) == "open")
    act = svc.owner_phone if kind == "phone" else svc.owner_captcha
    need(m, f"{kind}_service_refuses_wrong_owner_id", act("wrong") is False)
    ident = w.get("match_number") if kind == "phone" else (re.search(r"CHK-\d{4}", w.get("instructions") or "") or [""])[0]
    # The id comes from what Deck stored, so a lost match number or check id fails here.
    need(m, f"{kind}_owner_action_completed_in_service", bool(act(ident)) and svc.state()[kind] == "completed", f"service {kind} step: {svc.state()[kind]}")
    start = len(model.ledger)
    model.hold_wake()
    fresh = c.setup(sid)
    code, _ = c.raw("POST", path, {**base, "version": fresh["version"]})
    need(m, f"{kind}_answer_accepted", code == 204, f"status {code}")
    need(m, f"{kind}_resume_reached_the_model", model.wait_held(timeout), "the wake did not reach the model before the timeout")
    # The model call is held. The owner action is done in the service. Deck must still be waiting for verification.
    held = c.setup(sid)
    state = owner_answer_state(held, rid)
    need(m, f"{kind}_answer_alone_leaves_verification_pending", state == "answered" and svc.state()[kind] == "completed",
         f"service step {svc.state()[kind]}; Deck request {state}. The owner answer alone is not verification")
    model.release_wake()
    try:
        c.until(f"{kind} verification", lambda: owner_answer_state(c.setup(sid), rid) == "verified", timeout)
    except SystemExit as x:
        m.gate(f"{kind}_verified_only_after_service_evidence", FAIL, str(x)[:200])
        raise Stop("verification") from None
    turns = model.ledger[start:start + 4]
    ok = [x["name"] for x in turns] == ["owner_help_status", "browser_step", "browser_read", "report_help_verification"] and turns[1]["args"].get("steps") == sl.REFRESH_STEPS and turns[3]["args"].get("result") == "verified" and svc.state()[kind] == "completed"
    need(m, f"{kind}_verified_only_after_service_evidence", ok, "the task read the owner help state, refreshed the service page, read it and then reported; the service shows the step completed")
    time.sleep(settle)
    code, _ = c.raw("POST", path, {**base, "version": c.setup(sid)["version"]})
    time.sleep(settle)
    need(m, f"{kind}_duplicate_answer_adds_nothing", code in (200, 204) and model.wakes.get(rid, 0) == 1, f"status {code}, wake messages for the request: {model.wakes.get(rid, 0)}")


def protected_mode(stack, m, a):
    c = stack.client
    opts = c.json("GET", "/api/setup-options")
    cap = bool(opts.get("protected_input"))
    m.data["evidence"]["capabilities"] = {"protected_input": cap}
    if not cap:
        for g in ("credential_delivery_end_to_end", "guest_file_protected", "challenge_owner_response", "raster_secrecy"):
            m.gate(g, PENDING if g != "raster_secrecy" else NOT_TESTED, "protected_input is false in this checkout" if g != "raster_secrecy" else "needs bare screenshot and desktop capture evidence; PNG byte search proves nothing")
        return None
    value = "e2e-" + secrets.token_urlsafe(24)
    needles = variants(value)
    watch = ArgvWatcher(needles)
    watch.start()
    label = "FIXTURE_PASS"
    sid = create_and_approve(c, "Sign in to the synthetic fixture", [label])
    guest = Guest(stack.rt / sid)
    protected(c, m, guest, sid, label, value, None)
    c.until("setup ready", lambda: (r := c.setup(sid)) and r["runtime_ready"] and r, a.ready_timeout)
    challenges(c, m, sid, a.settle)
    res = watch.finish()
    m.gate("secret_never_in_argv", PASS if res["argv_hits"] == 0 else FAIL, f"{res['samples']} samples")
    stack.stop("server")
    leak = scan_tree([stack.out / "data", stack.out / "private", stack.rt], needles, skip=("disk.qcow2", "vm.state", "*.sock"))
    m.data["evidence"]["leak_scan"] = leak
    m.gate("secret_absent_from_host_evidence", PASS if leak["files_with_match"] == 0 else FAIL, f"{leak['files_scanned']} files scanned; guest disk and saved state are excluded because the guest holds the value by design")
    m.gate("raster_secrecy", NOT_TESTED, "needs bare screenshot and desktop capture evidence")
    return sid



# ---------- the synthetic login flow ----------

CREDENTIAL = "FIXTURE_PASS"
PROTECTED_GATES = ["credential_wait_label_from_request", "refuses_stale_version", "refuses_wrong_request", "refuses_newline_value", "refuses_label_in_body",
                   "ordinary_completion_refuses_credentials", "failed_attempts_keep_the_wait", "delivery_returns_204", "wait_cleared_only_after_delivery",
                   "guest_file_protected", "replay_of_a_cleared_request_refused"]
ROUND_GATES = ["wait_raised", "refuses_stale", "refuses_wrong_request", "refuses_value_field", "failed_answers_keep_the_wait", "service_refuses_wrong_owner_id",
               "owner_action_completed_in_service", "answer_accepted", "resume_reached_the_model", "answer_alone_leaves_verification_pending",
               "verified_only_after_service_evidence", "duplicate_answer_adds_nothing"]
OPTIONAL_SYNTHETIC = ["restart_from_saved_state", "second_task_after_wake", "raster_secrecy"]


def required_gates():
    return (["base_frozen_and_verified", "setup_ready_through_api", "bot_data_dir_created_by_server"] + PROTECTED_GATES
            + ["special_characters_preserved_in_guest_file", "special_characters_authenticated_in_service", "service_login_succeeded_with_reference",
               "restart_preserves_waiting_ownership", "restart_adds_no_work"]
            + [f"{k}_{g}" for k in ("phone", "captcha") for g in ROUND_GATES]
            + ["first_task_done", "single_success_action", "scripted_calls_exactly_once", "task_final_report", "restart_after_completion_no_duplicate",
               "model_uses_the_reference_not_the_value", "model_turns_hold_no_value", "secret_never_in_argv", "secret_absent_from_host_evidence",
               "logs_accounted_before_and_after_restart", "base_unchanged", "no_owned_process_left"])


def read_executor(stack, sid):
    return json.loads((stack.rt / sid / "executor.json").read_text())


def counters(stack, sid, chat_id):
    c, st = stack.client, stack.service.state()
    return {"tasks": len(bot_status(c, sid)["tasks"]), "messages": len(c.json("GET", f"/api/chats/{chat_id}/messages")), "model_calls": stack.model.calls,
            **{k: st[k] for k in ("login_success", "login_failure", "submit_attempts", "submit_success")}}


def restart(stack, sid):
    a, c = stack.a, stack.client
    stack.stop("server")
    stack.start_server()
    c.until("runtime ready after restart", lambda: (r := c.setup(sid)) and r["runtime_ready"] and r, a.ready_timeout)
    c.until("status after restart", lambda: bot_status(c, sid), 60)
    time.sleep(a.settle)


def finish_scans(stack, m, watch, needles):
    """Argv count, the host sinks and the logs of every server start. The guest disk and saved state are not scanned: the guest holds the value by design."""
    res = watch.finish()
    m.gate("secret_never_in_argv", PASS if res["argv_hits"] == 0 else FAIL, f"{res['samples']} samples")
    stack.stop("server")
    priv = stack.out / "private"
    leak = scan_tree([stack.out / "data", priv, stack.rt], needles, skip=("disk.qcow2", "vm.state", "*.sock"))
    m.data["evidence"]["leak_scan"] = leak
    m.data["evidence"]["leak_scan_boundary"] = {"scanned": ["data (database and WAL)", "private (server logs, model request and response logs, service log, config)", "computers (run metadata)", "process argv (sampled)", "model turns (memory)"],
                                                 "excluded": ["disk.qcow2: the guest credential file lives here by design", "vm.state: guest memory", "*.sock"]}
    m.gate("secret_absent_from_host_evidence", PASS if leak["files_with_match"] == 0 else FAIL, f"{leak['files_scanned']} files scanned; matches: {leak['matches']}")
    logs = sorted(priv.glob("server-*.log"))
    mreq = priv / "model-requests.jsonl"
    m.data["evidence"]["logs"] = {"server_logs": [f.name for f in logs], "server_starts": stack.starts, "model_request_bytes": mreq.stat().st_size if mreq.exists() else 0}
    m.gate("logs_accounted_before_and_after_restart", PASS if len(logs) == stack.starts >= 3 and mreq.exists() and mreq.stat().st_size > 0 else FAIL,
           f"{len(logs)} server log(s) for {stack.starts} start(s); the first start, the restart during the wait and the restart after completion need three")


def synthetic_login_mode(stack, m, a, value):
    c, svc, model = stack.client, stack.service, stack.model
    needles = variants(value)
    watch = ArgvWatcher(needles)
    watch.start()
    try:
        sid = create_and_approve(c, "Sign in to the local synthetic service", [CREDENTIAL], task=sl.TASK)
        m.data["evidence"]["setup_id"] = sid
        task_id = f"setup-{sid}"
        chat_id = c.setup(sid)["chat_id"]
        protected(c, m, Guest(stack.rt / sid), sid, CREDENTIAL, value, None)
        special = a.credential_profile == "special"
        m.gate("special_characters_preserved_in_guest_file", m.data["gates"]["guest_file_protected"]["status"] if special else NOT_TESTED, "the guest file line equals NAME=value" if special else "diagnostic plain profile")
        if m.data["gates"]["guest_file_protected"]["status"] == FAIL:
            raise Stop("guest file")
        status = lambda: bot_status(c, sid)  # noqa: E731
        row, w = await_owner_wait(c, m, svc, sid, task_id, "phone", set(), a.ready_timeout + a.task_timeout, status)
        bot_dir = stack.out / "data" / "bots" / sid
        need(m, "setup_ready_through_api", row["phase"] == "Ready" and row["runtime_ready"], f"phase {row['phase']}, runtime ready {row['runtime_ready']}")
        m.gate("bot_data_dir_created_by_server", PASS if bot_dir.is_dir() else FAIL, "created by the server" if bot_dir.is_dir() else "the artifact and journal path of a provisioned bot must exist")
        st = svc.state()
        used_ref = any(sl.REF in json.dumps(e["args"]) for e in model.ledger if e["name"] == "browser_step")
        need(m, "service_login_succeeded_with_reference", st["login_success"] == 1 and st["login_failure"] == 0 and used_ref, f"service logins {st['login_success']} ok / {st['login_failure']} failed; the model typed the reference: {used_ref}")
        m.gate("special_characters_authenticated_in_service", PASS if special and st["login_success"] == 1 else NOT_TESTED, "the service compared the exact value that bare typed from the reference" if special else "diagnostic plain profile")
        # Restart while the phone wait is open.
        before = ownership(row, status(), read_executor(stack, sid), task_id)
        n0 = counters(stack, sid, chat_id)
        m.data["evidence"]["ownership_before_restart"] = before
        restart(stack, sid)
        row = c.setup(sid)
        after = ownership(row, status(), read_executor(stack, sid), task_id)
        n1 = counters(stack, sid, chat_id)
        m.data["evidence"]["ownership_after_restart"] = after
        need(m, "restart_preserves_waiting_ownership", not ownership_diff(before, after), f"changed: {ownership_diff(before, after)}")
        need(m, "restart_adds_no_work", not counts_changed(n0, n1), f"changed: {counts_changed(n0, n1)}")
        w = login_wait(row, "PhoneApproval", task_id)
        need(m, "restart_keeps_the_phone_wait_open", bool(w) and not w.get("answered"))
        answer_owner_wait(c, m, svc, model, sid, task_id, "phone", row, w, 60, a.settle)
        phone_rid = w["request_id"]
        row, w = await_owner_wait(c, m, svc, sid, task_id, "captcha", {phone_rid}, 120, status)
        answer_owner_wait(c, m, svc, model, sid, task_id, "captcha", row, w, 60, a.settle)
        done = c.until("the task to end", lambda: (st := status()) and any(t["id"] == task_id and t["state"] in ("done", "failed") for t in st["tasks"]) and not st["working"] and st, a.task_timeout)
        t1 = next(t for t in done["tasks"] if t["id"] == task_id)
        need(m, "first_task_done", t1["state"] == "done", t1.get("detail") or t1["state"])
        msgs = c.json("GET", f"/api/chats/{chat_id}/messages")
        st = svc.state()
        need(m, "single_success_action", st["submit_success"] == 1 and st["submit_attempts"] == 1 and st["login_success"] == 1 and st["login_failure"] == 0 and st["phone"] == st["captcha"] == "completed",
             f"service: logins {st['login_success']}/{st['login_failure']}, submit success {st['submit_success']} of {st['submit_attempts']} attempt(s)")
        problems = sl.ledger_problems(model.ledger)
        need(m, "scripted_calls_exactly_once", not problems, "; ".join(problems) or f"{len(model.ledger)} tool calls, each once")
        need(m, "task_final_report", sl.DONE_TEXT in json.dumps(msgs), "the task's closing text is in the chat")
        refs = [e for e in model.ledger if e["name"] == "browser_step" and sl.REF in json.dumps(e["args"])]
        m.gate("model_uses_the_reference_not_the_value", PASS if refs else FAIL, f"{len(refs)} step list(s) typed ${{{sl.REF}}}")
        m.gate("model_turns_hold_no_value", PASS if not any(n in json.dumps(model.ledger + model.texts).encode() for n in needles) else FAIL)
        # Restart after the task is done. Nothing may run again.
        n2 = counters(stack, sid, chat_id)
        restart(stack, sid)
        n3 = counters(stack, sid, chat_id)
        need(m, "restart_after_completion_no_duplicate", not counts_changed(n2, n3) and svc.state()["submit_success"] == 1, f"changed: {counts_changed(n2, n3)}")
        m.gate("restart_from_saved_state", NOT_TESTED, "optional: not run in this mode")
        m.gate("second_task_after_wake", NOT_TESTED, "optional: not run in this mode")
        m.gate("raster_secrecy", NOT_TESTED, "needs real bare capture evidence; PNG byte search proves nothing")
    finally:
        try:
            finish_scans(stack, m, watch, needles)
        except BaseException as e:
            m.gate("scans_completed", FAIL, f"{type(e).__name__}: {m.scrub(str(e))[:200]}")


# ---------- entry ----------

def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["baseline", "protected", "synthetic-login"])
    ap.add_argument("--base", required=True, help="frozen base .qcow2 with its sidecar")
    ap.add_argument("--deck", default=str(HERE.parent.parent / "target/release/deck"))
    ap.add_argument("--out", default=str(Path.home() / ".cache/deck" / time.strftime("se-%m%d-%H%M%S")))
    ap.add_argument("--fixture-port", type=int, default=28951)
    ap.add_argument("--server-port", type=int, default=28952)
    ap.add_argument("--model-port", type=int, default=28953)
    ap.add_argument("--idle-secs", type=int, default=40)
    ap.add_argument("--disk-gib", type=int, default=16)
    ap.add_argument("--ready-timeout", type=int, default=300)
    ap.add_argument("--task-timeout", type=int, default=240)
    ap.add_argument("--settle", type=int, default=8)
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    ap.add_argument("--diagnostic-precreate-bot-dir", action="store_true", help="diagnosis only: hide the missing-directory gap so later gates can run; recorded as a workaround")
    ap.add_argument("--credential-profile", choices=["special", "plain"], default="special", help="plain is diagnosis only: the special-character gates stay not_tested and the run cannot be a complete pass")
    ap.add_argument("--keep-disk", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    if a.mode == "synthetic-login" and a.diagnostic_precreate_bot_dir:
        print("STOPPED: --diagnostic-precreate-bot-dir hides a product gap and cannot count as a pass. It is not allowed in synthetic-login.", file=sys.stderr)
        return 2
    lock = take_lock()
    why = pressure(Path("/proc/meminfo").read_text(), Path("/proc/loadavg").read_text(), a.min_avail_mib, a.max_load)
    if why:
        print(f"STOPPED: host pressure: {why}. Nothing was started.", file=sys.stderr)
        return 3
    out = Path(a.out)
    if not socket_path_ok(out):
        print(f"STOPPED: --out is too long for QEMU unix sockets: {out}", file=sys.stderr)
        return 2
    out.mkdir(mode=0o700, parents=True)
    (out / "private").mkdir(mode=0o700)
    m = Manifest(a.mode)
    value = synthetic_value(a.credential_profile) if a.mode == "synthetic-login" else None
    if value:
        m.needles = variants(value)
    stack = Stack(a)
    base = base_identity(a.base)
    head = subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    m.data["identity"] = {"head": head, "base": {k: base[k] for k in ("id", "virtual_size", "mode")}, "deck_sha256": v.sha256_file(Path(a.deck)), "fidelity": "real server, real VM, scripted model, local fixture"}
    m.data["host"] = {"loadavg_start": Path("/proc/loadavg").read_text().split()[:3], "mem_available_mib_start": int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) // 1024, "max_load_allowed": a.max_load}
    m.gate("base_frozen_and_verified", PASS if base["mode"] == "0o444" and verify_base(a.base) else FAIL, f"mode {base['mode']}")
    left = []
    try:
        stack.write_config()
        stack.start_helpers(value)
        stack.start_server()
        if a.mode == "baseline":
            sid = baseline(stack, m, base)
            fail_closed_probes(stack.client, sid, m)
        elif a.mode == "synthetic-login":
            synthetic_login_mode(stack, m, a, value)
        else:
            protected_mode(stack, m, a)
    except BaseException as e:  # keep the evidence, then report
        m.gate("run_completed", FAIL, f"{type(e).__name__}: {m.scrub(str(e))[:300]}")
    finally:
        sizes = {d.name: v.allocated_bytes(d / "disk.qcow2") for d in stack.vm_dirs() if (d / "disk.qcow2").exists()}
        left = stack.cleanup()
        m.data["evidence"]["overlay_allocated_bytes"] = sizes
        if not a.keep_disk:
            for d in stack.vm_dirs():
                for f in (d / "disk.qcow2", d / "state" / "vm.state"):
                    f.unlink(missing_ok=True)
        m.data["host"]["loadavg_end"] = Path("/proc/loadavg").read_text().split()[:3]
        m.gate("base_unchanged", PASS if verify_base(a.base) else FAIL)
        m.gate("no_owned_process_left", PASS if not left else FAIL, f"{len(left)} left")
        if a.mode == "synthetic-login":
            m.require(required_gates(), OPTIONAL_SYNTHETIC)
        m.write(out / "manifest.json")
        print(f"manifest: {out / 'manifest.json'}")
    lock.close()
    return 0 if m.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
