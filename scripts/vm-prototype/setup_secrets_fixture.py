"""Throwaway stack for the protected-setup acceptance test.

Starts the real `deck` binary with a private config, a scripted local model and the fake guest
harness (`setup_secrets_guest.py`). Everything lives in one temporary directory outside Git.
The sentinel secret is generated per run. It never appears in argv, environment, files of this
repository or any message. Scans report counts only.

Binary: DECK_BIN, else target/debug/deck under CARGO_TARGET_DIR, else ./target/debug/deck.
"""
import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUEST = HERE / "setup_secrets_guest.py"
OWNER = "owner"
BASE_ID = "base-" + "0" * 16
KEY_ENV = "DECK_FIXTURE_MODEL_KEY"


def deck_binary():
    if os.environ.get("DECK_BIN"):
        return Path(os.environ["DECK_BIN"])
    target = Path(os.environ.get("CARGO_TARGET_DIR", HERE.parents[1] / "target"))
    return target / "debug" / "deck"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sentinel:
    """A runtime-generated secret. `variants` are the encodings a leak scan looks for."""

    def __init__(self):
        self.value = "SYN-" + secrets.token_urlsafe(24)
        raw = self.value.encode()
        self.variants = [raw, base64.b64encode(raw), base64.urlsafe_b64encode(raw), raw.hex().encode()]
        self.digest = hashlib.sha256(raw).hexdigest()

    def count(self, data: bytes) -> int:
        return sum(data.count(v) for v in self.variants)

    def count_file(self, path: Path) -> int:
        try:
            return self.count(path.read_bytes())
        except OSError:
            return 0


class ScriptedModel:
    """Answers every call with a fixed reply and logs the raw requests for scanning."""

    def __init__(self, log: Path):
        self.log = log
        self.requests = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with open(outer.log, "ab") as f:
                    f.write(str(self.path).encode() + b"\n" + str(self.headers).encode() + body + b"\n")
                outer.requests += 1
                out = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Stack:
    def __init__(self, allowed_users=(OWNER,)):
        self.dir = Path(tempfile.mkdtemp(prefix="deck-secrets-e2e-"))
        self.sentinel = Sentinel()
        self.wire = self.dir / "responses.log"  # every response body the test saw
        self.model = ScriptedModel(self.dir / "model-requests.log")
        self.port = free_port()
        self.data = self.dir / "data"
        self.runtime_root = self.dir / "runtimes"
        self.log = self.dir / "deck.log"
        self.allowed_users = list(allowed_users)
        self.proc = None
        base = self.dir / "base.img"
        base.write_bytes(b"")
        base.with_suffix(".json").write_text(json.dumps({"kind": "base", "id": BASE_ID, "virtual_size": 10 << 30}))
        self.base = base
        (self.dir / "deck.toml").write_text(self.config())

    def config(self):
        q = lambda p: json.dumps(str(p))
        return f"""
listen = "127.0.0.1:{self.port}"
data_dir = {q(self.data)}
trusted_proxies = ["127.0.0.1"]
allowed_users = {json.dumps(self.allowed_users)}
[model]
base_url = "http://127.0.0.1:{self.model.port}/v1"
api_key_env = "{KEY_ENV}"
agent_model = "scripted"
small_model = "scripted"
[bare]
command = "false"
[provisioning]
base = {q(self.base)}
runtime_root = {q(self.runtime_root)}
max_bots = 4
[provisioning.template]
version = 1
bot_id = "template"
runtime_dir = "unused"
harness = {q(GUEST)}
fixture_port = 1
[provisioning.resources]
cpus = 2
memory_mib = 2048
disk_gib = 10
[provisioning.limits]
max_cpus = 2
max_memory_mib = 2048
max_disk_gib = 10
[provisioning.accounting]
currency = "TST"
exponent = 2
period = "test"
policy_version = "fixture"
ceiling = 100
allowance = 10
increase_increment = 10
price_version = "fixture"
input_per_million = 1
output_per_million = 1
extra_per_call = 0
max_input_tokens = 1000
max_input_bytes = 100000
max_output_tokens = 100
max_response_bytes = 100000
completion_token_field = false
request_usage = false
"""

    # ---- process ----
    def start(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DECK_CONFIG",)}
        env.update(DECK_CONFIG=str(self.dir / "deck.toml"), **{KEY_ENV: "fixture-not-a-key"})
        self.logf = open(self.log, "ab")
        self.proc = subprocess.Popen([str(deck_binary())], env=env, stdout=self.logf, stderr=self.logf, stdin=subprocess.DEVNULL)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"deck exited with {self.proc.returncode}; see log hash {self.hash(self.log)}")
            try:
                if self.call("GET", "/api/setup-options")[0] == 200:
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("deck did not listen")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.logf.close()

    def kill9(self):
        self.proc.kill()
        self.proc.wait()
        self.logf.close()

    def restart(self):
        self.stop()
        self.start()

    def close(self):
        self.stop()
        self.model.stop()

    @staticmethod
    def hash(path):
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
        except OSError:
            return "missing"

    # ---- HTTP ----
    def call(self, method, path, body=None, user=OWNER, raw=None):
        """Returns (status, parsed JSON or None). The response is logged for scanning."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if user:
            headers["Remote-User"] = user
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        out = r.read()
        c.close()
        with open(self.wire, "ab") as f:
            f.write(f"{method} {path} {r.status}\n".encode() + out + b"\n")
        try:
            return r.status, json.loads(out)
        except ValueError:
            return r.status, None

    def setups(self):
        return {s["setup"]["id"]: s for s in self.call("GET", "/api/setups")[1]}

    def setup(self, sid):
        return self.setups()[sid]

    def wait_for(self, pred, timeout=20, step=0.1):
        end = time.time() + timeout
        while time.time() < end:
            v = pred()
            if v:
                return v
            time.sleep(step)
        return None

    def new_waiting_setup(self, label="FIXTURE_PASS", hold_open="HOLD_OPEN"):
        """A real approved setup whose driver reached a credential wait for `label`.
        A second label keeps the setup from reaching a ready runtime, which needs a live VM."""
        st, rec = self.call("POST", "/api/setups", {"job": "Read the synthetic fixture"})
        assert st == 201, f"create returned {st}"
        sid = rec["setup"]["id"]
        draft = rec["setup"]["draft"]
        draft["credentials"] = [label] + ([hold_open] if hold_open else [])
        assert self.call("POST", f"/api/setups/{sid}", {"version": rec["version"], "draft": draft})[0] < 300
        cur = self.setup(sid)
        st = self.call("POST", f"/api/setups/{sid}/approve", {"version": cur["version"], "revision": cur["setup"]["draft"]["revision"]})[0]
        assert st < 300, f"approve returned {st}"
        got = self.wait_for(lambda: (lambda s: s if s["setup"]["wait"] else None)(self.setup(sid)), 30)
        assert got, f"no wait; phase hash {self.hash(self.log)}"
        return sid

    def secret(self, sid, version, request_id, value=None, user=OWNER):
        value = self.sentinel.value if value is None else value
        return self.call("POST", f"/api/setups/{sid}/secret", {"version": version, "request_id": request_id, "value": value}, user=user)

    def respond(self, sid, version, request_id, decision, **extra):
        return self.call("POST", f"/api/setups/{sid}/challenge/respond",
                         {"version": version, "request_id": request_id, "decision": decision, **extra})

    def secret_endpoint_present(self, sid):
        """False when the route is absent (404/405). Probes with a wrong version and a dummy value."""
        st, _ = self.call("POST", f"/api/setups/{sid}/secret", {"version": 0, "request_id": "probe", "value": "x"})
        return st not in (404, 405)

    def respond_endpoint_present(self, sid):
        st, _ = self.call("POST", f"/api/setups/{sid}/challenge/respond", {"version": 0, "request_id": "probe", "decision": "cancel"})
        return st not in (404, 405)

    # ---- state and guest ----
    def rt(self, sid):
        return self.runtime_root / sid

    def journal(self, sid):
        p = self.rt(sid) / "journal.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def calls(self, sid, sub):
        return [j for j in self.journal(sid) if sub in j["argv"]]

    def fault(self, sid, mode, remaining=1):
        self.rt(sid).mkdir(parents=True, exist_ok=True)
        (self.rt(sid) / "fault.json").write_text(json.dumps({"mode": mode, "remaining": remaining}))

    def guest_secrets(self, sid):
        p = self.rt(sid) / "guest" / "secrets.env"
        return p.read_text().splitlines() if p.exists() else []

    def receipts(self, sid):
        p = self.rt(sid) / "guest" / "receipts.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def patch_setup(self, sid, fn):
        """Fixture seeding: rewrite one snapshot body through SQLite and bump its revision."""
        db = sqlite3.connect(self.data / "deck.db", timeout=10)
        body, rev = db.execute("SELECT body, revision FROM bot_setup_drafts WHERE draft_id=?", (sid,)).fetchone()
        obj = json.loads(body)
        fn(obj)
        db.execute("UPDATE bot_setup_drafts SET body=?, revision=? WHERE draft_id=?", (json.dumps(obj), rev + 1, sid))
        db.commit()
        db.close()

    def sql(self, query, args=()):
        db = sqlite3.connect(self.data / "deck.db", timeout=10)
        try:
            return db.execute(query, args).fetchall()
        finally:
            db.close()

    def leak_counts(self, extra_files=()):
        """Sentinel matches per surface. The one private sink, guest/secrets.env, is excluded."""
        s = self.sentinel
        sink = {self.rt(p.name) / "guest" / "secrets.env" for p in self.runtime_root.glob("*")} if self.runtime_root.exists() else set()
        counts = {
            "model-requests": s.count_file(self.model.log),
            "responses": s.count_file(self.wire),
            "deck-log": s.count_file(self.log),
        }
        for name in ("deck.db", "deck.db-wal", "deck.db-shm"):
            counts[name] = s.count_file(self.data / name)
        for f in list(self.data.rglob("*")) + list(self.runtime_root.rglob("*")) + list(extra_files):
            if f.is_file() and f not in sink and f.name not in ("deck.db", "deck.db-wal", "deck.db-shm"):
                counts[f"file:{f.relative_to(self.dir)}"] = s.count_file(f)
        return {k: v for k, v in counts.items() if v}
