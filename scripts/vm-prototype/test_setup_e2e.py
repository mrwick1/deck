"""Tests for setup_e2e.py. They start no VM. The contract server is a MOCK: it proves the checks catch
violations, not that Deck implements the contract."""
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import setup_e2e as e
import stage3

SECRET = "e2e-synthetic-Zk9-value"


class Helpers(unittest.TestCase):
    def test_pressure(self):
        mem = "MemTotal: 1 kB\nMemAvailable: 4194304 kB\n"
        self.assertIsNone(e.pressure(mem, "1.0 2 3 1/2 4", 4096, 8))
        self.assertIn("MemAvailable", e.pressure(mem, "1.0 2 3 1/2 4", 8192, 8))
        self.assertIn("load", e.pressure(mem, "18.6 2 3 1/2 4", 4096, 8))

    def test_socket_path_limit(self):
        self.assertTrue(e.socket_path_ok("/home/u/.cache/deck/se-1113-1700"))
        self.assertFalse(e.socket_path_ok("/home/u/.cache/deck/" + "x" * 80))

    def test_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            f = e.take_lock(f"{d}/lock")
            with self.assertRaises(SystemExit):
                e.take_lock(f"{d}/lock")
            f.close()
            e.take_lock(f"{d}/lock").close()

    def test_scan_finds_encoded_forms_and_never_prints_the_value(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "plain.log").write_text(f"x {SECRET} y")
            (root / "json.log").write_text(json.dumps({"v": 'a"b' + SECRET}))
            (root / "b64.log").write_bytes(__import__("base64").b64encode(SECRET.encode()))
            (root / "clean.log").write_text("nothing")
            (root / "disk.qcow2").write_text(SECRET)
            r = e.scan_tree([root], e.variants(SECRET), skip=("disk.qcow2",))
            self.assertEqual(r["matches"], ["b64.log", "json.log", "plain.log"])
            self.assertEqual(r["files_scanned"], 4)
            self.assertNotIn(SECRET, json.dumps(r))

    def test_argv_hits_reads_cmdlines(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, cmd in (("1", b"a\0b"), ("2", f"tool\0--x={SECRET}".encode()), ("x", SECRET.encode())):
                (Path(d) / pid).mkdir()
                (Path(d) / pid / "cmdline").write_bytes(cmd)
            self.assertEqual(e.argv_hits(e.variants(SECRET), Path(d)), 1)

    def test_derived_name_follows_the_frozen_rule(self):
        self.assertEqual(e.derived_name("FIXTURE_PASS"), "FIXTURE_PASS")
        n = e.derived_name("Fixture login")
        self.assertRegex(n, r"^DECK_[0-9A-F]{16}$")

    def test_manifest_counts_pending_as_open_not_failed(self):
        m = e.Manifest("t")
        m.gate("a", e.PASS)
        m.gate("b", e.PENDING, "later")
        self.assertTrue(m.ok())
        self.assertEqual(m.open_gates(), ["b"])
        m.gate("c", e.FAIL)
        self.assertFalse(m.ok())


class ScriptedModelTest(unittest.TestCase):
    def test_logs_the_request_counts_it_and_reports_usage(self):
        with tempfile.TemporaryDirectory() as d:
            e.ScriptedModel.log_path = Path(d) / "req.jsonl"
            stage3.Model.calls = 0
            srv = ThreadingHTTPServer(("127.0.0.1", 0), e.ScriptedModel)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            body = json.dumps({"messages": [{"role": "user", "content": stage3.TASK1}]}).encode()
            r = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
            out = urllib.request.urlopen(r, timeout=10).read().decode()
            srv.shutdown()
            self.assertIn("browser_open", out)
            self.assertIn('"usage"', out)
            self.assertTrue(out.rstrip().endswith("[DONE]"))
            self.assertEqual(stage3.Model.calls, 1)
            self.assertIn(stage3.TASK1, (Path(d) / "req.jsonl").read_text())


class Contract(BaseHTTPRequestHandler):
    """A mock of the frozen endpoints. `leaky` echoes the value; `accepts_stale` ignores versions."""
    state = None

    def log_message(self, *a):
        pass

    def reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def row(self):
        s = Contract.state
        wait = {"request_id": s["rid"], "step": {"Credential": "FIXTURE_PASS"}, "kind": {"Credential": {"label": "FIXTURE_PASS"}}} if not s["delivered"] else None
        return {"version": s["version"], "chat_id": 1, "runtime_ready": True, "phase": "Ready", "setup": {"id": "s1", "phase": "Ready", "wait": wait}}

    def do_GET(self):
        if self.path == "/api/setups":
            self.reply(200, json.dumps([self.row()]).encode())
        else:
            self.reply(404)

    def do_POST(self):
        s = Contract.state
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path.endswith("/control"):
            return self.reply(503, b'{"error":"use the protected endpoint"}')
        if not self.path.endswith("/secret"):
            return self.reply(404)
        v = body.get("value", "")
        if set(body) != {"version", "request_id", "value"} or "\n" in v or "\r" in v or "\0" in v:
            return self.reply(400, json.dumps({"error": "bad", "seen": v if s["leaky"] else ""}).encode())
        if (body["version"] != s["version"] and not s["accepts_stale"]) or body["request_id"] != s["rid"] or s["delivered"]:
            return self.reply(409, b'{"error":"stale"}')
        s["delivered"] = True
        s["version"] += 1
        self.reply(204)


class Guest:
    def check_secret_file(self, name, value):
        return {"exists": True, "mode": "0o600", "owner": 1000, "name_present": True, "value_matches": True}


class ProtectedGates(unittest.TestCase):
    def run_gates(self, **flags):
        Contract.state = {"version": 5, "rid": "r1", "delivered": False, "leaky": False, "accepts_stale": False, **flags}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Contract)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        m = e.Manifest("protected")
        e.protected(e.Client(f"http://127.0.0.1:{srv.server_port}"), m, Guest(), "s1", "FIXTURE_PASS", SECRET, None)
        srv.shutdown()
        srv.server_close()
        return {n: g["status"] for n, g in m.data["gates"].items()}

    def test_a_correct_contract_passes_every_gate(self):
        got = self.run_gates()
        self.assertEqual({k for k, s in got.items() if s != e.PASS}, set(), got)
        self.assertGreaterEqual(len(got), 10)

    def test_accepting_a_stale_version_fails(self):
        self.assertEqual(self.run_gates(accepts_stale=True)["refuses_stale_version"], e.FAIL)

    def test_echoing_the_value_fails(self):
        self.assertEqual(self.run_gates(leaky=True)["refuses_newline_value"], e.FAIL)

    def test_capability_absent_is_pending_never_a_pass(self):
        class C:
            def json(self, *a):
                return {"enabled": True, "protected_input": False}

        m = e.Manifest("protected")
        a = SimpleNamespace(settle=0, ready_timeout=1)
        self.assertIsNone(e.protected_mode(SimpleNamespace(client=C()), m, a))
        st = {n: g["status"] for n, g in m.data["gates"].items()}
        self.assertEqual(st["credential_delivery_end_to_end"], e.PENDING)
        self.assertEqual(st["raster_secrecy"], e.NOT_TESTED)
        self.assertNotIn(e.PASS, st.values())

    def test_missing_routes_are_pending_probes(self):
        class C:
            def raw(self, *a):
                return 404, b""

        m = e.Manifest("baseline")
        e.fail_closed_probes(C(), "s1", m)
        self.assertEqual({g["status"] for g in m.data["gates"].values()}, {e.PENDING})

    def test_a_live_route_that_accepts_with_no_request_fails(self):
        class C:
            def raw(self, *a):
                return 204, b""

        m = e.Manifest("baseline")
        e.fail_closed_probes(C(), "s1", m)
        self.assertEqual({g["status"] for g in m.data["gates"].values()}, {e.FAIL})


if __name__ == "__main__":
    unittest.main()
