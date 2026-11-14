"""Tests for the desktop readiness probe: guest `bare-call --ready` and host `vmproto.ready`.

A fake bare-mcp and a fake QGA stand in. No VM, browser or network runs. Every wait is bounded."""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
G = HERE / "guest"


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


v = load(HERE / "vmproto.py", "vmproto_ready")

# Logs each method it receives. Starts answering only after $FAKE_DELAY seconds.
FAKE = r'''
import json, sys, os, time
log, delay = sys.argv[1], float(sys.argv[2])
tools = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None
time.sleep(delay)
for line in sys.stdin:
    m = json.loads(line)
    open(log, "a").write(m["method"] + "\n")
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "x"}
    elif m["method"] == "tools/list":
        r = {"tools": tools if tools is not None else [{"name": "open"}, {"name": "screenshot"}]}
    else:
        r = {"content": [{"type": "text", "text": "{}"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''


def tool(name, *props):
    return {"name": name, "inputSchema": {"type": "object", "properties": {p: {"type": "string"} for p in props}}}


GOOD = [tool("open", "url", "state", "file"), tool("save_state", "name", "file"), tool("ensure_session", "name", "file"), tool("screenshot", "path")]


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deck-ready-test-")
        self.rt = f"{self.tmp}/run"
        self.home = f"{self.tmp}/home"
        os.makedirs(f"{self.rt}/bare")
        st = Path(self.home, ".local/state/deck-executor")
        st.mkdir(parents=True)
        (st / "identity.json").write_text(json.dumps({"bot_id": "bot-1", "guest_id": "g1", "max_epoch": 0}))
        self.log = f"{self.tmp}/log"
        Path(f"{self.tmp}/fake.py").write_text(FAKE)
        self.procs = []
        self.fds = []
        self.env = {**os.environ, "XDG_RUNTIME_DIR": self.rt, "HOME": self.home}

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait(5)
        for fd in self.fds:
            os.close(fd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session(self, delay=0.0, tools=GOOD):
        d = f"{self.rt}/bare"
        Path(f"{self.tmp}/tools.json").write_text(json.dumps(tools))
        os.mkfifo(f"{d}/in")
        fd = os.open(f"{d}/in", os.O_RDWR)
        self.fds.append(fd)
        out = open(f"{d}/out", "wb")
        self.procs.append(subprocess.Popen([sys.executable, f"{self.tmp}/fake.py", self.log, str(delay), f"{self.tmp}/tools.json"], stdin=fd, stdout=out))
        out.close()

    def ready(self, wait="2"):
        r = subprocess.run([sys.executable, str(G / "bare-call"), "--ready", wait], env=self.env, capture_output=True, text=True, timeout=30)
        return r.returncode, json.loads(r.stdout), r.stderr

    def methods(self):
        return Path(self.log).read_text().split() if Path(self.log).exists() else []

    def test_answering_session_is_ready_with_identity_and_tool_count(self):
        self.session()
        code, out, _ = self.ready()
        self.assertEqual(code, 0)
        self.assertEqual(out, {"ready": True, "tools": 4, "bot_id": "bot-1", "guest_id": "g1"})

    def test_probe_uses_only_initialize_and_tools_list(self):
        self.session()
        self.ready()
        self.ready()
        self.assertEqual(self.methods(), ["initialize", "notifications/initialized", "tools/list", "tools/list"])

    def test_no_session_is_not_ready(self):
        shutil.rmtree(f"{self.rt}/bare")
        code, out, _ = self.ready()
        self.assertEqual((code, out), (3, {"ready": False, "reason": "no_session"}))

    def test_fifo_without_reader_does_not_hang(self):
        os.mkfifo(f"{self.rt}/bare/in")
        Path(self.rt, "bare/out").write_text("")
        t = time.monotonic()
        code, out, _ = self.ready()
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual((code, out["ready"], out["reason"]), (3, False, "no_session"))

    def test_session_that_does_not_answer_is_not_ready_within_the_bound(self):
        self.session(delay=30)
        t = time.monotonic()
        code, out, _ = self.ready(wait="1")
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual((code, out), (3, {"ready": False, "reason": "no_answer"}))

    def test_missing_identity_is_not_ready(self):
        self.session()
        os.remove(f"{self.home}/.local/state/deck-executor/identity.json")
        code, out, _ = self.ready()
        self.assertEqual((code, out["reason"]), (3, "no_identity"))

    def incompatible(self, tools, missing):
        self.session(tools=tools)
        code, out, err = self.ready()
        self.assertEqual((code, out), (3, {"ready": False, "reason": "incompatible_browser", "missing": missing}))
        self.assertEqual(self.methods(), ["initialize", "notifications/initialized", "tools/list"])

    def test_old_browser_without_open_file_is_incompatible(self):
        old = [tool("open", "url", "state"), *GOOD[1:]]
        self.incompatible(old, ["open.file"])

    def test_malformed_tool_name_is_incompatible(self):
        self.incompatible([{"name": ["open"]}], ["open", "save_state", "ensure_session"])

    def test_missing_tool_is_incompatible(self):
        self.incompatible([GOOD[0], GOOD[2]], ["save_state"])

    def test_no_required_tool_lists_all_three(self):
        self.incompatible([tool("screenshot", "path")], ["open", "save_state", "ensure_session"])

    def test_missing_ensure_or_save_fields_are_incompatible(self):
        self.incompatible([GOOD[0], tool("save_state", "name"), tool("ensure_session", "file")], ["save_state.file", "ensure_session.name"])

    def test_malformed_schemas_are_incompatible_and_leak_nothing(self):
        bad = [{"name": "open", "inputSchema": "secret-page-text"}, {"name": "save_state", "inputSchema": {"properties": ["name", "file"]}},
               {"name": "ensure_session", "inputSchema": {"properties": {"name": {}, "file": "x"}}}, "junk", None]
        self.session(tools=bad)
        code, out, _ = self.ready()
        self.assertEqual((code, out["ready"], out["reason"]), (3, False, "incompatible_browser"))
        self.assertNotIn("secret-page-text", json.dumps(out))
        self.assertLessEqual(set(out), {"ready", "reason", "missing"})

    def test_incompatible_browser_is_reported_before_identity(self):
        os.remove(f"{self.home}/.local/state/deck-executor/identity.json")
        self.session(tools=[tool("open", "url")])
        self.assertEqual(self.ready()[1]["reason"], "incompatible_browser")

    def test_compatible_browser_without_fresh_is_ready(self):
        self.session(tools=GOOD)
        self.assertEqual(self.ready()[0], 0)


class FakeQga:
    """Answers the two guest commands that the probe runs."""

    def __init__(self, probes, bot="bot-1", guest="g1", service="active"):
        self.probes = list(probes)  # one entry per probe call: a dict or None for not ready
        self.bot, self.guest, self.service = bot, guest, service
        self.calls = []

    def exec(self, argv, stdin=None, timeout=30):
        self.calls.append((argv, stdin))
        if argv[0] == "systemctl":
            return (0, "active\n", "") if self.service == "active" else (3, self.service + "\n", "")
        p = self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
        if p is None:
            return 3, json.dumps({"ready": False, "reason": "no_answer"}), ""
        return 0, json.dumps({"ready": True, "tools": 2, "bot_id": self.bot, "guest_id": self.guest}), ""


class Host(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deck-ready-host-")
        self.p = v.Paths(self.tmp)
        self.p.mk()
        (self.p.root / "executor.json").write_text(json.dumps({"bot_id": "bot-1", "guest_id": "g1"}))
        self.now = [0.0]
        self.sleeps = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sleep(self, s):
        self.sleeps.append(s)
        self.now[0] += s

    def ready(self, qga, bot="bot-1", timeout=10):
        return v.ready(self.p, bot, timeout, qga=qga, sleep=self.sleep, clock=lambda: self.now[0])

    def test_delayed_session_is_ready_only_after_an_answer(self):
        q = FakeQga([None, None, {}])
        out = self.ready(q)
        self.assertEqual(out, {"ok": True, "bot_id": "bot-1", "guest_id": "g1", "tools": 2})
        self.assertEqual(len([c for c in q.calls if c[0][0] != "systemctl"]), 3)

    def test_never_ready_times_out_with_a_stable_code(self):
        q = FakeQga([None])
        with self.assertRaises(v.NotReady) as e:
            self.ready(q, timeout=3)
        self.assertEqual(e.exception.code, "not_ready")
        self.assertLessEqual(sum(self.sleeps), 3)  # polling stays inside the deadline
        self.assertGreater(len(q.calls), 1)

    def test_wrong_bot_is_refused_before_any_guest_call(self):
        q = FakeQga([{}])
        with self.assertRaises(v.NotReady) as e:
            self.ready(q, bot="other")
        self.assertEqual(e.exception.code, "bot_mismatch")
        self.assertEqual(q.calls, [])

    def test_missing_binding_is_refused(self):
        (self.p.root / "executor.json").unlink()
        q = FakeQga([{}])
        with self.assertRaises(v.NotReady) as e:
            self.ready(q)
        self.assertEqual((e.exception.code, q.calls), ("no_binding", []))

    def test_guest_identity_must_match_the_binding_and_is_not_retried(self):
        for q in (FakeQga([{}], bot="other"), FakeQga([{}], guest="g2")):
            with self.assertRaises(v.NotReady) as e:
                self.ready(q)
            self.assertEqual(e.exception.code, "identity_mismatch")
            self.assertEqual(self.sleeps, [])

    def test_executor_service_must_be_active(self):
        with self.assertRaises(v.NotReady) as e:
            self.ready(FakeQga([{}], service="inactive"), timeout=2)
        self.assertEqual(e.exception.code, "not_ready")

    def test_repeated_probe_runs_only_read_only_commands(self):
        q = FakeQga([{}])
        self.ready(q)
        self.ready(q)
        for argv, stdin in q.calls:
            self.assertIn(argv[0], ("systemctl", "/usr/local/bin/as-deck"))
            if argv[0] != "systemctl":
                self.assertEqual(argv[1:], ["/usr/local/bin/bare-call", "--ready", "8"])
            self.assertIsNone(stdin)
        self.assertEqual([c[0][0] for c in q.calls].count("/usr/local/bin/as-deck"), 2)

    def test_cli_prints_one_json_line_and_exits_nonzero_when_not_ready(self):
        (self.p.root / "executor.json").write_text(json.dumps({"bot_id": "bot-1", "guest_id": "g1"}))
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as e:
            v.main(["--dir", self.tmp, "ready", "--bot", "other", "--timeout", "1"])
        self.assertEqual(e.exception.code, "bot_mismatch")
        self.assertEqual(json.loads(buf.getvalue()), {"ok": False, "error": "bot_mismatch"})

    def test_probe_commands_carry_no_credentials_or_page_actions(self):
        src = (G / "bare-call").read_text()
        body = src[src.index("def ready("):src.index("def prepare(")] if "def ready(" in src else ""
        self.assertTrue(body)
        for banned in ("secrets", "screenshot", "tools/call", '"open"', "ensure_session"):
            self.assertNotIn(banned, body)


if __name__ == "__main__":
    unittest.main()
