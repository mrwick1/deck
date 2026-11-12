"""Deterministic tests for the guest relay helpers. A fake bare-mcp replaces the real one. Every wait is bounded."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

G = Path(__file__).resolve().parent / "guest"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(G / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


FAKE = r'''
import json, sys, os
out = sys.argv[1]
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    meth = m["method"]
    if meth == "initialize":
        r = {"protocolVersion": "x"}
    elif meth == "tools/list":
        r = {"tools": [{"name": "open"}, {"name": "screenshot"}]}
    else:
        a = m["params"]["arguments"]
        n = m["params"]["name"]
        rep = {"ok": True, "args": a}
        if n == "screenshot":
            open(a["path"], "wb").write(b"\x89PNG-fake")
        if n == "step":
            open("/tmp/never-used-by-test", "a").close()
            p = os.environ["FAKE_NOTE"]
            rep["steps"] = [{"line": 1, "note": p}]
        r = {"content": [{"type": "text", "text": json.dumps(rep)}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''


class Relay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deck-relay-test-")
        self.rt = f"{self.tmp}/run"
        self.ws = f"{self.tmp}/ws"
        self.out = f"{self.tmp}/out"
        for d in (self.rt, self.ws, self.out, f"{self.rt}/bare"):
            os.makedirs(d)
        d = f"{self.rt}/bare"
        os.mkfifo(f"{d}/in")
        Path(f"{self.tmp}/fake.py").write_text(FAKE)
        self.note = f"{self.tmp}/note.txt"
        Path(self.note).write_text("hello")
        # Same shape as bare-session: a FIFO in, a file out.
        self.fifo = os.open(f"{d}/in", os.O_RDWR)
        outf = open(f"{d}/out", "wb")
        self.proc = subprocess.Popen([sys.executable, f"{self.tmp}/fake.py", self.out], stdin=self.fifo, stdout=outf,
                                     env={**os.environ, "FAKE_NOTE": self.note})
        outf.close()
        self.env = {**os.environ, "XDG_RUNTIME_DIR": self.rt, "DECK_OUT": self.out, "DECK_WORKSPACE": self.ws}

    def tearDown(self):
        self.proc.kill()
        self.proc.wait(5)
        os.close(self.fifo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, req):
        r = subprocess.run([sys.executable, str(G / "bare-call"), json.dumps(req)], env=self.env, timeout=30, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(Path(self.out, "reply.json").read_text())

    def test_list_and_initialize_once(self):
        r = self.call({"method": "tools/list", "timeout": 10})
        self.assertTrue(r["ok"])
        self.assertEqual([t["name"] for t in r["result"]["tools"]], ["open", "screenshot"])
        self.assertTrue(Path(self.rt, "bare", "init").exists())
        self.call({"method": "tools/list", "timeout": 10})  # a second call must not initialize again

    def test_screenshot_path_is_set_by_the_guest(self):
        r = self.call({"method": "tools/call", "params": {"name": "screenshot", "arguments": {"path": "/etc/shadow"}}, "timeout": 10})
        text = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(text["args"]["path"].startswith(self.out + "/shot-"))
        self.assertTrue(os.path.exists(text["args"]["path"]))

    def test_upload_outside_the_workspace_is_refused(self):
        Path(self.ws, "ok.txt").write_text("x")
        Path(self.tmp, "secret.txt").write_text("s")
        r = self.call({"method": "tools/call", "params": {"name": "act", "arguments": {"files": ["ok.txt"]}}, "timeout": 10})
        self.assertTrue(r["ok"])
        for bad in ("../secret.txt", f"{self.tmp}/secret.txt"):
            r = self.call({"method": "tools/call", "params": {"name": "act", "arguments": {"files": [bad]}}, "timeout": 10})
            self.assertFalse(r["ok"])
            self.assertIn("not allowed", r["error"])

    def test_files_named_by_a_report_are_copied_next_to_the_reply(self):
        r = self.call({"method": "tools/call", "params": {"name": "step", "arguments": {"steps": "shot"}}, "timeout": 10})
        self.assertEqual(list(r["files"]), [self.note])
        self.assertEqual(Path(self.out, r["files"][self.note]).read_text(), "hello")

    def test_missing_session_is_an_error_reply_not_a_crash(self):
        shutil.rmtree(f"{self.rt}/bare")
        r = self.call({"method": "tools/list"})
        self.assertFalse(r["ok"])

    def test_publish_copies_only_workspace_files(self):
        Path(self.ws, "a b.txt").write_text("data")
        Path(self.tmp, "outside.txt").write_text("no")
        os.symlink(f"{self.tmp}/outside.txt", f"{self.ws}/link.txt")
        run = lambda p: subprocess.run([sys.executable, str(G / "deck-publish"), p], env=self.env, capture_output=True, text=True, timeout=10)
        ok = run(f"{self.ws}/a b.txt")
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, "a_b.txt"))
        self.assertEqual(Path(self.out, "a_b.txt").read_text(), "data")
        for bad in (f"{self.tmp}/outside.txt", f"{self.ws}/link.txt", f"{self.ws}/../outside.txt", self.ws):
            self.assertNotEqual(run(bad).returncode, 0, bad)


if __name__ == "__main__":
    unittest.main()
