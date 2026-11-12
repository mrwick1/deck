#!/usr/bin/env python3
"""Stage 3 end-to-end run: the real deck server, a real guest VM, a scripted model and a synthetic site.

It creates a private overlay on a frozen base, binds the guest executor to the bot, starts the fixture,
a scripted model and the server, then runs one task (browser, file, shell), an idle save and a wake.
It records CPU, RSS, model calls, captures and workers for the working, awake-idle and saved states.
It writes results.json to the runtime directory and always stops what it started.
No real model, no real account, no real data.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import vmproto as v

HERE = Path(__file__).resolve().parent
BOT = "main"
TASK1 = "scripted task one: read the fixture, write a report, send it"
TASK2 = "second task: reply with one word"
TICK = os.sysconf("SC_CLK_TCK")


def sse(chunk):
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


class Model(BaseHTTPRequestHandler):
    """Scripted by what the last message is. Counts its requests."""
    calls = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with Model.lock:
            Model.calls += 1
        msgs = body["messages"]
        last = msgs[-1]
        names = {}
        for m in msgs:
            for c in m.get("tool_calls", []) or []:
                names[c["id"]] = c["function"]["name"]

        def call(name, args):
            return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"k{Model.calls}", "function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]}

        def text(t):
            return {"choices": [{"delta": {"content": t}, "finish_reason": "stop"}]}

        if last["role"] == "user":
            out = call("browser_open", {"url": "http://10.0.2.100/", "snapshot": True}) if "task one" in last["content"] else text("second ok")
        else:
            prev = names.get(last.get("tool_call_id"), "")
            if prev == "browser_open":
                out = call("shell", {"script": "sleep 14; echo 'report for the fixture' > $DECK_OUT/report.txt; id -un; uname -n"})
            elif prev == "shell":
                m = re.search(r'(/[^"\\\\]*report\.txt)', last["content"])
                out = call("send_file", {"path": m.group(1) if m else "missing", "caption": "report"})
            else:
                out = text("task one done")
        data = sse(out)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def cpu_ticks(pid):
    st = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(st[11]) + int(st[12])


def tree_ticks(pid):
    return sum(cpu_ticks(q) for q in v.tree_pids(pid) if Path(f"/proc/{q}").exists())


class Api:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method, path, body=None, timeout=30):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method, headers={"Remote-User": "e2e", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as res:
            raw = res.read()
        return json.loads(raw) if raw else None

    def wait(self, what, cond, timeout):
        t = time.monotonic()
        while time.monotonic() - t < timeout:
            s = self.call("GET", "/api/bot")
            if cond(s):
                return s
            time.sleep(0.5)
        raise SystemExit(f"FAILED: timed out waiting for {what}: {self.call('GET', '/api/bot')}")


def window(label, secs, pid_server, p, api):
    """CPU and RSS of the VM tree and of the server over a window, with the bot counters at both ends."""
    vm = v.read_vm(p)
    vpid = vm["pid"] if vm and v.proc_ok(p, vm["pid"]) else None
    s0, c0 = api.call("GET", "/api/bot"), time.monotonic()
    t_srv0 = cpu_ticks(pid_server)
    t_vm0 = tree_ticks(vpid) if vpid else 0
    time.sleep(secs)
    wall = time.monotonic() - c0
    s1 = api.call("GET", "/api/bot")
    vm_cpu = (tree_ticks(vpid) - t_vm0) / TICK if vpid and v.proc_ok(p, vpid) else 0.0
    srv_cpu = (cpu_ticks(pid_server) - t_srv0) / TICK
    return {
        "label": label, "seconds": round(wall, 1),
        "vm_cpu_percent": round(100 * vm_cpu / wall, 2) if vpid else None,
        "vm_rss_mib": round(v.tree_rss_kib(vpid) / 1024, 1) if vpid and v.proc_ok(p, vpid) else 0,
        "server_cpu_percent": round(100 * srv_cpu / wall, 2),
        "server_rss_mib": round(int(re.search(r"VmRSS:\s+(\d+)", Path(f"/proc/{pid_server}/status").read_text()).group(1)) / 1024, 1),
        "model_calls_delta": s1["counters"]["model_calls"] - s0["counters"]["model_calls"],
        "captures_delta": s1["screen"]["captures"] - s0["screen"]["captures"],
        "screen_worker": s1["screen"]["worker"],
        "computer": s1["computer"],
    }


def tool_stdout(m):
    c = json.loads(m["content"])
    if c.get("role") != "tool":
        return ""
    try:
        return json.loads(c["content"]).get("stdout", "")
    except (ValueError, AttributeError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--dir", default=os.environ.get("DECK_VM_DIR", str(Path.home() / ".cache/deck/deck-stage3")))
    ap.add_argument("--deck", default=str(HERE.parent.parent / "target/debug/deck"))
    ap.add_argument("--fixture-port", type=int, default=28941)
    ap.add_argument("--server-port", type=int, default=28942)
    ap.add_argument("--model-port", type=int, default=28943)
    ap.add_argument("--idle-secs", type=int, default=25)
    a = ap.parse_args()
    p = v.Paths(a.dir)
    p.mk()
    work = Path(tempfile.mkdtemp(prefix="deck-stage3-"))
    results = {"steps": {}, "windows": []}
    fixture = server = httpd = None
    try:
        v.create_overlay(p, a.base)
        v.start_qemu(p, "run", a.fixture_port)
        v.Qga(p.qga).ping_ready(90)
        info = v.install_executor(p, BOT)
        v.Qga(p.qga).sh("sync")  # the kill must not lose the install
        v.kill_vm(p)  # the server cold boots it: the executor service starts at boot
        fixture = subprocess.Popen([sys.executable, str(HERE / "fixture.py"), "--port", str(a.fixture_port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        httpd = ThreadingHTTPServer(("127.0.0.1", a.model_port), Model)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        cfg = work / "deck.toml"
        cfg.write_text(f'''listen = "127.0.0.1:{a.server_port}"
data_dir = "{work / "data"}"
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
[vm]
version = 1
bot_id = "{BOT}"
runtime_dir = "{p.root}"
harness = "{HERE / "vmproto.py"}"
fixture_port = {a.fixture_port}
idle_secs = {a.idle_secs}
screen_interval_ms = 1000
''')
        env = dict(os.environ, DECK_CONFIG=str(cfg), DECK_MODEL_KEY="scripted-key")
        server = subprocess.Popen([a.deck], env=env, stdout=open(work / "server.log", "w"), stderr=subprocess.STDOUT, cwd=work)
        api = Api(a.server_port)
        for _ in range(60):
            try:
                api.call("GET", "/api/bot")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("FAILED: the server did not start")
        s = api.call("GET", "/api/bot")
        assert s["computer"] == "saved", s
        results["windows"].append(window("saved (before any task)", 8, server.pid, p, api))

        # Task one: cold boot, browser, shell, file.
        t0 = time.monotonic()
        r = api.call("POST", f"/api/chats/{s['chat_id']}/messages", {"text": TASK1, "mode": "queue"})
        task1 = r["task_id"]
        api.wait("the computer to wake", lambda s: s["computer"] == "awake", 180)
        woke = time.monotonic() - t0
        results["windows"].append(window("working (no viewer)", 5, server.pid, p, api))
        # A viewer for a few seconds: capture runs only now.
        before = api.call("GET", "/api/bot")["screen"]
        viewer = urllib.request.urlopen(urllib.request.Request(api.base + "/api/bot/screen", headers={"Remote-User": "e2e"}))
        time.sleep(4)
        during = api.call("GET", "/api/bot")["screen"]
        viewer.close()
        time.sleep(2.5)
        after = api.call("GET", "/api/bot")["screen"]
        time.sleep(3)
        later = api.call("GET", "/api/bot")["screen"]
        results["steps"]["screen"] = {"captures_before_viewer": before["captures"], "captures_with_viewer": during["captures"], "delivered_with_viewer": during["delivered"],
                                      "unchanged": during["unchanged"], "worker_after_close": later["worker"], "captures_stable_after_close": after["captures"] == later["captures"]}
        s = api.wait("task one to end", lambda s: not s["working"] and any(t["id"] == task1 and t["state"] in ("done", "failed") for t in s["tasks"]), 240)
        t1 = next(t for t in s["tasks"] if t["id"] == task1)
        task_s = time.monotonic() - t0
        msgs = api.call("GET", f"/api/chats/{s['chat_id']}/messages")
        blob = json.dumps(msgs)
        results["steps"]["task_one"] = {"state": t1["state"], "detail": t1["detail"], "wake_to_awake_s": round(woke, 1), "task_total_s": round(task_s, 1),
                                        "saw_fixture_page": "Deck fixture" in blob, "shell_ran_as_deck": any(tool_stdout(m).startswith("deck\n") for m in msgs), "file_message_sent": any(json.loads(m["content"]).get("type") == "file" for m in msgs),
                                        "model_calls": s["counters"]["model_calls"]}
        assert t1["state"] == "done", results["steps"]["task_one"]
        results["windows"].append(window("awake, idle", min(15, a.idle_secs - 8), server.pid, p, api))

        # Idle save.
        s = api.wait("the idle save", lambda s: s["computer"] == "saved", a.idle_secs + 60)
        results["steps"]["idle_save"] = {"saves": s["counters"]["saves"], "computer": s["computer"]}
        results["windows"].append(window("saved", 10, server.pid, p, api))

        # Task two: wake from the saved state; the chat continues.
        t0 = time.monotonic()
        before_msgs = len(api.call("GET", f"/api/chats/{s['chat_id']}/messages"))
        task2 = api.call("POST", f"/api/chats/{s['chat_id']}/messages", {"text": TASK2, "mode": "queue"})["task_id"]
        s = api.wait("task two", lambda s: any(t["id"] == task2 and t["state"] in ("done", "failed") for t in s["tasks"]), 180)
        t2 = next(t for t in s["tasks"] if t["id"] == task2)
        msgs = api.call("GET", f"/api/chats/{s['chat_id']}/messages")
        results["steps"]["wake_round_trip"] = {"state": t2["state"], "seconds": round(time.monotonic() - t0, 1), "messages_before": before_msgs, "messages_after": len(msgs),
                                               "wakes": s["counters"]["wakes"], "saves": s["counters"]["saves"], "connects": s["counters"]["connects"], "model_calls": s["counters"]["model_calls"],
                                               "task_one_visible_in_chat": TASK1 in json.dumps(msgs)}
        assert t2["state"] == "done", results["steps"]["wake_round_trip"]
        results["final_status"] = {k: s[k] for k in ("computer", "counters", "screen", "unresolved")}
        results["ok"] = True
    finally:
        results.setdefault("ok", False)
        if server and server.poll() is None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
        if httpd:
            httpd.shutdown()
        if fixture:
            fixture.terminate()
        vm = v.read_vm(p)
        if vm and v.proc_ok(p, vm["pid"]):
            v.kill_vm(p)
        results["model_requests"] = Model.calls
        (p.root / "results.json").write_text(json.dumps(results, indent=1))
        print(json.dumps(results, indent=1))
        if not results["ok"]:
            print((work / "server.log").read_text()[-3000:] if (work / "server.log").exists() else "", file=sys.stderr)


if __name__ == "__main__":
    main()
