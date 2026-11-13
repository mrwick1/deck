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
import setup_synthetic_login as sl
import stage3

SECRET = "e2e-synthetic-Zk9-value"


class LiveHarnessRegressions(unittest.TestCase):
    def test_guest_value_check_uses_the_qga_text_stdin_contract(self):
        from unittest.mock import patch
        import secrets
        value = secrets.token_urlsafe(24)
        seen = []
        def run(argv, stdin=None):
            if not isinstance(stdin, str):
                raise TypeError("QGA stdin must be text")
            seen.append(stdin == value and all(value not in arg for arg in argv))
            return 0, '{"value_matches":true,"mode":"0o600"}', ''
        with tempfile.TemporaryDirectory() as d, patch.object(e.v, "Qga") as qga:
            qga.return_value.exec.side_effect = run
            result = e.Guest(Path(d)).check_secret_file("FIXTURE_PASS",value)
        self.assertEqual(seen,[True])
        self.assertTrue(result["value_matches"])

    def test_fixture_visits_count_the_actual_fixture_output(self):
        import socket, time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "private").mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0))
                port=sock.getsockname()[1]
            stack=e.Stack(SimpleNamespace(out=d,server_port=0,model_port=0,fixture_port=port))
            try:
                stack.start_helpers()
                deadline=time.monotonic()+5
                while True:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/",timeout=1) as res:
                            self.assertIn(b"Deck fixture",res.read())
                        break
                    except OSError:
                        if time.monotonic()>=deadline: raise
                        time.sleep(0.02)
                self.assertEqual(stack.fixture_visits(),1)
            finally:
                stack.cleanup()
                stack.fixture_log.close()


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


def real_row(wait=None, resolved=(), phase="Ready", version=7, answered=False):
    """The shape GET /api/setups returns. The phase is on the outer record. `setup` has no phase field."""
    w = None if wait is None else {"request_id": wait[0], "step": {"Login": "setup-s1"}, "kind": wait[1], "expires_at": None, "ttl_secs": None,
                                    "instructions": wait[2] if len(wait) > 2 else None, "match_number": wait[3] if len(wait) > 3 else None, "answered": answered}
    return {"version": version, "chat_id": 3, "phase": phase, "runtime_ready": True,
            "setup": {"id": "s1", "wait": w, "wait_seq": 1, "resolved": [{"request_id": r, "outcome": o} for r, o in resolved]}}


class ChallengeEvaluation(unittest.TestCase):
    def test_answer_states_come_from_the_wait_and_resolved_list(self):
        self.assertEqual(e.owner_answer_state(real_row(("r1", "PhoneApproval")), "r1"), "open")
        self.assertEqual(e.owner_answer_state(real_row(("r1", "PhoneApproval"), answered=True), "r1"), "answered")
        self.assertEqual(e.owner_answer_state(real_row(None, [("r1", "verified")]), "r1"), "verified")
        self.assertEqual(e.owner_answer_state(real_row(None, [("r1", "owner_completed")]), "r1"), "closed")
        self.assertEqual(e.owner_answer_state(real_row(None, [("r1", {"rejected": "x"})]), "r1"), "closed")
        self.assertEqual(e.owner_answer_state(real_row(None), "r1"), "absent")
        self.assertEqual(e.owner_answer_state(real_row(("r2", "PhoneApproval")), "r1"), "absent")

    def test_wait_kind_reads_strings_and_credential_objects(self):
        self.assertEqual(e.wait_kind({"kind": "Captcha"}), "Captcha")
        self.assertEqual(e.wait_kind({"kind": {"Credential": {"label": "X"}}}), "Credential")
        self.assertIsNone(e.wait_kind(None))

    def test_login_wait_needs_kind_and_task(self):
        row = real_row(("r1", "PhoneApproval"))
        self.assertEqual(e.login_wait(row, "PhoneApproval", "setup-s1")["request_id"], "r1")
        self.assertIsNone(e.login_wait(row, "Captcha", "setup-s1"))
        self.assertIsNone(e.login_wait(row, "PhoneApproval", "other-task"))

    def test_challenges_on_a_real_shaped_row_does_not_index_setup_phase(self):
        class C:
            def __init__(self):
                self.row = real_row(("r1", "PhoneApproval"))

            def setup(self, sid):
                return self.row

            def raw(self, method, path, body=None):
                if body["version"] != 7 or body["request_id"] != "r1" or "value" in body:
                    return 409, b""
                self.row = real_row(("r1", "PhoneApproval"), answered=True, version=8)
                return 204, b""

        m = e.Manifest("t")
        e.challenges(C(), m, "s1", 0)
        st = {n: g["status"] for n, g in m.data["gates"].items()}
        self.assertEqual(st["completion_is_not_service_success"], e.PASS, st)
        self.assertNotIn(e.FAIL, st.values())

    def test_owner_completion_that_marks_the_service_verified_fails(self):
        class C:
            def __init__(self):
                self.row = real_row(("r1", "PhoneApproval"))

            def setup(self, sid):
                return self.row

            def raw(self, method, path, body=None):
                if body["version"] != 7 or body["request_id"] != "r1" or "value" in body:
                    return 409, b""
                self.row = real_row(None, [("r1", "verified")], version=8)
                return 204, b""

        m = e.Manifest("t")
        e.challenges(C(), m, "s1", 0)
        self.assertEqual(m.data["gates"]["completion_is_not_service_success"]["status"], e.FAIL)

    def test_challenges_with_no_wait_stays_pending(self):
        class C:
            def setup(self, sid):
                return real_row(None)

        m = e.Manifest("t")
        e.challenges(C(), m, "s1", 0)
        self.assertEqual(m.data["gates"]["challenge_owner_response"]["status"], e.PENDING)

    def test_a_cleared_wait_with_no_verified_record_is_not_success(self):
        # The old check passed on `wait is None`. A vanished wait is not a verification.
        self.assertNotEqual(e.owner_answer_state(real_row(None), "r1"), "verified")


class OwnershipEvidence(unittest.TestCase):
    def snap(self, **kw):
        row = real_row(("r1", "PhoneApproval", "Approve it", "47"), version=kw.pop("version", 7))
        status = {"tasks": [{"id": "setup-s1", "state": kw.pop("task_state", "waiting")}], "working": False}
        ex = {"bot_id": "s1", "guest_id": kw.pop("guest", "g1")}
        row["setup"]["wait"]["request_id"] = kw.pop("rid", "r1")
        return e.ownership(row, status, ex, "setup-s1")

    def test_equal_snapshots_have_no_diff_and_ignore_the_version(self):
        self.assertEqual(e.ownership_diff(self.snap(), self.snap(version=9)), [])

    def test_each_identity_change_is_named(self):
        base = self.snap()
        self.assertEqual(e.ownership_diff(base, self.snap(rid="r2")), ["request_id"])
        self.assertEqual(e.ownership_diff(base, self.snap(task_state="running")), ["task_state"])
        self.assertEqual(e.ownership_diff(base, self.snap(guest="g2")), ["guest_id"])

    def test_a_missing_task_is_a_difference(self):
        a = self.snap()
        row = real_row(("r1", "PhoneApproval", "Approve it", "47"))
        b = e.ownership(row, {"tasks": []}, {"bot_id": "s1", "guest_id": "g1"}, "setup-s1")
        self.assertIn("task_state", e.ownership_diff(a, b))

    def test_counts_changed_names_each_counter(self):
        a = {"tasks": 1, "messages": 4, "model_calls": 3, "login_success": 1}
        self.assertEqual(e.counts_changed(a, dict(a)), [])
        self.assertEqual(e.counts_changed(a, {**a, "model_calls": 4, "login_success": 2}), ["login_success", "model_calls"])


class Acceptance(unittest.TestCase):
    def test_a_complete_run_passes_only_with_every_required_gate_passing(self):
        m = e.Manifest("t")
        for n in ("a", "b"):
            m.gate(n, e.PASS)
        m.gate("opt", e.NOT_TESTED, "later")
        m.require(["a", "b"], optional=["opt"])
        self.assertEqual(m.data["gates"]["acceptance_complete"]["status"], e.PASS)

    def test_a_missing_gate_fails(self):
        m = e.Manifest("t")
        m.gate("a", e.PASS)
        m.require(["a", "b"])
        g = m.data["gates"]["acceptance_complete"]
        self.assertEqual(g["status"], e.FAIL)
        self.assertIn("b", g["detail"])

    def test_a_skipped_dependency_that_is_not_optional_fails(self):
        for status in (e.PENDING, e.NOT_TESTED):
            m = e.Manifest("t")
            m.gate("a", status, "skipped")
            m.require(["a"])
            self.assertEqual(m.data["gates"]["acceptance_complete"]["status"], e.FAIL)

    def test_an_unlisted_pending_gate_also_fails(self):
        m = e.Manifest("t")
        m.gate("a", e.PASS)
        m.gate("extra", e.PENDING, "no route")
        m.require(["a"])
        self.assertEqual(m.data["gates"]["acceptance_complete"]["status"], e.FAIL)

    def test_a_failed_gate_fails_the_run_even_if_required_ones_pass(self):
        m = e.Manifest("t")
        m.gate("a", e.PASS)
        m.gate("x", e.FAIL)
        m.require(["a"])
        self.assertEqual(m.data["gates"]["acceptance_complete"]["status"], e.FAIL)
        self.assertFalse(m.ok())


class Redaction(unittest.TestCase):
    def test_a_detail_that_holds_the_value_is_replaced_and_fails(self):
        m = e.Manifest("t")
        m.needles = e.variants(SECRET)
        m.gate("g", e.PASS, f"saw {SECRET} here")
        g = m.data["gates"]["g"]
        self.assertEqual(g["status"], e.FAIL)
        self.assertNotIn(SECRET, json.dumps(m.data))

    def test_each_encoding_of_the_value_is_caught_in_a_detail(self):
        for form in e.variants(SECRET):
            m = e.Manifest("t")
            m.needles = e.variants(SECRET)
            m.gate("g", e.PASS, {"k": form.decode()})
            self.assertEqual(m.data["gates"]["g"]["status"], e.FAIL)

    def test_scrub_hides_the_value_in_an_exception_text(self):
        m = e.Manifest("t")
        m.needles = e.variants(SECRET)
        self.assertNotIn(SECRET, m.scrub(f"boom {SECRET}"))
        self.assertEqual(m.scrub("fine"), "fine")

    def test_variants_include_hex_and_form_encoding(self):
        v = e.variants("a b+c")
        self.assertIn(b"612062 2b63".replace(b" ", b""), v)
        self.assertIn(b"612062 2B63".replace(b" ", b""), v)
        self.assertIn(b"a+b%2Bc", v)

    def test_a_scan_finds_hex_and_form_encoded_copies(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "hex.log").write_text(SECRET.encode().hex())
            special = "ab c+d#e"
            (Path(d) / "form.log").write_text(__import__("urllib.parse").parse.quote_plus(special))
            self.assertEqual(e.scan_tree([d], e.variants(special))["matches"], ["form.log"])
            self.assertEqual(e.scan_tree([d], e.variants(SECRET))["matches"], ["hex.log"])


class Profiles(unittest.TestCase):
    def test_the_special_value_keeps_spaces_and_symbols_and_avoids_unsafe_characters(self):
        v = e.synthetic_value("special")
        self.assertIn(" ", v)
        self.assertTrue(any(c in v for c in "#%&=+;:/?@!*"))
        for bad in ('"', "\\", "$", "{", "\n", "\r", "\0", "'"):
            self.assertNotIn(bad, v)
        self.assertNotEqual(v, e.synthetic_value("special"))
        self.assertEqual(v, v.strip())

    def test_the_plain_value_is_diagnostic_only(self):
        self.assertRegex(e.synthetic_value("plain"), r"^[A-Za-z0-9_-]{20,}$")


class Entry(unittest.TestCase):
    def test_the_synthetic_mode_exists(self):
        self.assertEqual(e.parse(["synthetic-login", "--base", "x"]).mode, "synthetic-login")

    def test_the_diagnostic_precreate_switch_is_refused_in_the_synthetic_mode(self):
        self.assertEqual(e.main(["synthetic-login", "--base", "x", "--diagnostic-precreate-bot-dir"]), 2)

    def test_server_logs_never_collide(self):
        with tempfile.TemporaryDirectory() as d:
            names = {e.server_log_path(Path(d), n).name for n in (1, 2)}
            self.assertEqual(len(names), 2)

    def test_load_defaults_are_unchanged(self):
        a = e.parse(["synthetic-login", "--base", "x"])
        self.assertEqual((a.min_avail_mib, a.max_load), (6144, 8.0))


# ---------- the owner round, against a fake Deck ----------

class FakeModel:
    def __init__(self, deck, hold_reached=True):
        self.deck, self.hold_reached = deck, hold_reached
        self.ledger, self.wakes, self.calls = [], {}, 0
        self.armed = False

    def hold_wake(self):
        self.armed = True

    def wait_held(self, timeout):
        if self.hold_reached:
            self.wakes[self.deck.rid] = self.wakes.get(self.deck.rid, 0) + 1
        return self.hold_reached

    def release_wake(self):
        self.ledger += [{"name": "owner_help_status", "args": {}}, {"name": "browser_step", "args": {"steps": sl.REFRESH_STEPS}}, {"name": "browser_read", "args": {}}, {"name": "report_help_verification", "args": {"result": "verified"}}]
        self.deck.verify()


class FakeService:
    def __init__(self):
        self.done = False
        self.tried = []

    def phone_match(self):
        return "47"

    def captcha_check(self):
        return "CHK-1234"

    def owner_phone(self, m):
        self.tried.append(m)
        ok = m == "47"
        self.done |= ok
        return ok

    def owner_captcha(self, c):
        self.tried.append(c)
        ok = c == "CHK-1234"
        self.done |= ok
        return ok

    def state(self):
        return {"phone": "completed" if self.done else "pending", "captcha": "completed" if self.done else "pending", "stage": "phone"}


class FakeDeck:
    """Deck's setup row, answer route and task, with switches for the bugs the gates must catch."""

    def __init__(self, kind="PhoneApproval", **bugs):
        self.kind, self.bugs = kind, bugs
        self.rid, self.version, self.answered, self.verified = "r1", 7, False, False
        self.task = "waiting"
        self.model = None

    def row(self):
        if self.verified:
            return real_row(None, [(self.rid, "verified")], version=self.version)
        if self.bugs.get("vanish"):
            return real_row(None, version=self.version)
        instr = "Approve it" if self.kind == "PhoneApproval" else "Complete the check CHK-1234"
        return real_row((self.rid, self.kind, instr, "47" if self.kind == "PhoneApproval" else None), answered=self.answered, version=self.version)

    def setup(self, sid):
        return self.row()

    def status(self):
        return {"tasks": [{"id": "setup-s1", "state": self.task}], "working": False}

    def raw(self, method, path, body=None):
        if "challenge/respond" not in path:
            return 404, b""
        if set(body) != {"version", "request_id", "decision"} and not self.bugs.get("accepts_extra"):
            return 400, b""
        if body["version"] != self.version and not self.bugs.get("accepts_stale"):
            return 409, b""
        if body["request_id"] != self.rid:
            return 409, b""
        self.answered = True
        self.version += 1
        if self.bugs.get("verify_on_answer"):
            self.verified = True
        return 204, b""

    def verify(self):
        self.verified = True
        self.task = "running"
        self.version += 1

    def until(self, what, cond, timeout, every=0):
        got = cond()
        if not got:
            raise e.Stop(f"timed out waiting for {what}")
        return got


class OwnerRound(unittest.TestCase):
    def run_round(self, kind="phone", hold_reached=True, **bugs):
        deck = FakeDeck("PhoneApproval" if kind == "phone" else "Captcha", **bugs)
        deck.model = FakeModel(deck, hold_reached)
        svc = FakeService()
        m = e.Manifest("t")
        err = None
        try:
            row, w = e.await_owner_wait(deck, m, svc, "s1", "setup-s1", kind, set(), 1, deck.status)
            e.answer_owner_wait(deck, m, svc, deck.model, "s1", "setup-s1", kind, row, w, 1)
        except e.Stop as x:
            err = x
        return m, deck, svc, err

    def statuses(self, m):
        return {n: g["status"] for n, g in m.data["gates"].items()}

    def test_a_correct_round_passes_every_gate_for_phone_and_captcha(self):
        for kind in ("phone", "captcha"):
            m, deck, svc, err = self.run_round(kind)
            self.assertIsNone(err)
            st = self.statuses(m)
            self.assertEqual({n for n, s in st.items() if s != e.PASS}, set(), st)
            self.assertIn(f"{kind}_answer_alone_leaves_verification_pending", st)
            self.assertIn(f"{kind}_verified_only_after_service_evidence", st)
            self.assertEqual(svc.tried[0][:2] == "00" or svc.tried[0] == "wrong", True)  # a wrong id was tried first

    def test_owner_answer_that_verifies_at_once_fails_the_pending_gate(self):
        m, deck, svc, err = self.run_round(verify_on_answer=True)
        self.assertEqual(self.statuses(m)["phone_answer_alone_leaves_verification_pending"], e.FAIL)
        self.assertIsNotNone(err)

    def test_a_wait_that_just_vanishes_is_not_a_pass(self):
        m, deck, svc, err = self.run_round(vanish=True)
        self.assertIsNotNone(err)
        self.assertNotIn(e.PASS, [s for n, s in self.statuses(m).items() if n.endswith("verified_only_after_service_evidence")])

    def test_accepting_a_stale_version_fails(self):
        m, deck, svc, err = self.run_round(accepts_stale=True)
        self.assertEqual(self.statuses(m)["phone_refuses_stale"], e.FAIL)

    def test_a_wake_that_never_reaches_the_model_fails_and_stops(self):
        m, deck, svc, err = self.run_round(hold_reached=False)
        self.assertEqual(self.statuses(m)["phone_resume_reached_the_model"], e.FAIL)
        self.assertIsNotNone(err)

    def test_the_owner_action_needs_the_right_service_id(self):
        m, deck, svc, err = self.run_round()
        self.assertEqual(svc.tried[0], "wrong")
        self.assertEqual(svc.tried[1], "47")

    def test_a_double_wake_is_a_duplicate(self):
        deck = FakeDeck()
        deck.model = FakeModel(deck)
        m = e.Manifest("t")
        svc = FakeService()
        row, w = e.await_owner_wait(deck, m, svc, "s1", "setup-s1", "phone", set(), 1, deck.status)
        deck.model.wait_held = lambda timeout: deck.model.wakes.update({deck.rid: 2}) or True
        with self.assertRaises(e.Stop):
            e.answer_owner_wait(deck, m, svc, deck.model, "s1", "setup-s1", "phone", row, w, 1)
        self.assertEqual(self.statuses(m)["phone_duplicate_answer_adds_nothing"], e.FAIL)

    def test_an_old_request_id_is_not_accepted_as_the_new_wait(self):
        deck = FakeDeck()
        deck.model = FakeModel(deck)
        m = e.Manifest("t")
        with self.assertRaises(e.Stop):
            e.await_owner_wait(deck, m, FakeService(), "s1", "setup-s1", "phone", {"r1"}, 1, deck.status)

    def test_a_wait_for_another_task_is_not_ours(self):
        deck = FakeDeck()
        m = e.Manifest("t")
        with self.assertRaises(e.Stop):
            e.await_owner_wait(deck, m, FakeService(), "s1", "other-task", "phone", set(), 1, deck.status)

    def test_a_wrong_match_number_in_the_wait_fails(self):
        deck = FakeDeck()
        svc = FakeService()
        svc.phone_match = lambda: "99"
        m = e.Manifest("t")
        with self.assertRaises(e.Stop):
            e.await_owner_wait(deck, m, svc, "s1", "setup-s1", "phone", set(), 1, deck.status)
        self.assertEqual(self.statuses(m)["phone_wait_raised"], e.FAIL)


class WholeModeRegression(unittest.TestCase):
    """Run the orchestration past both restarts. Component protocols have separate tests."""
    def run_mode(self, changed=False):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'data/bots/s1').mkdir(parents=True)
            row = real_row(('r1', 'PhoneApproval', 'Approve it', '47'))
            cap = real_row(('r2', 'Captcha', 'Complete CHK-1234', None))
            state = ['waiting']
            client = SimpleNamespace(setup=lambda sid: row,
                json=lambda *args: [{'content': sl.DONE_TEXT}],
                until=lambda what, cond, timeout: cond())
            model = SimpleNamespace(ledger=[{'name': n, 'args': {'steps': '${FIXTURE_PASS}'} if n == 'browser_step' else {}} for n in sl.EXPECTED_NAMES], texts=[sl.DONE_TEXT])
            service = SimpleNamespace(state=lambda: {'login_success': 1, 'login_failure': 0, 'submit_success': 1, 'submit_attempts': 1, 'phone': 'completed', 'captcha': 'completed'})
            stack = SimpleNamespace(client=client, service=service, model=model, out=root, rt=root/'computers')
            args = SimpleNamespace(credential_profile='special', ready_timeout=1, task_timeout=1, settle=0)
            rounds = []
            def answer(*a):
                rounds.append(a[6])
                if a[6] == 'captcha': state[0] = 'done'
            def status(*a):
                return {'tasks': [{'id': 'setup-s1', 'state': state[0]}], 'working': False}
            counts = [{'calls': 4}, {'calls': 5 if changed else 4}, {'calls': 19}, {'calls': 19}]
            manifest = e.Manifest('synthetic-login')
            with patch.object(e, 'ArgvWatcher'), patch.object(e, 'create_and_approve', return_value='s1'), \
                 patch.object(e, 'protected', side_effect=lambda c, m, *a: m.gate('guest_file_protected',e.PASS)), \
                 patch.object(e, 'await_owner_wait', side_effect=[(row,row['setup']['wait']),(cap,cap['setup']['wait'])]), \
                 patch.object(e, 'bot_status', side_effect=status), patch.object(e, 'read_executor', return_value={'bot_id':'s1','guest_id':'g1'}), \
                 patch.object(e, 'counters', side_effect=counts), patch.object(e, 'restart') as restarts, \
                 patch.object(e, 'answer_owner_wait', side_effect=answer), patch.object(e, 'finish_scans') as scans:
                error = None
                try: e.synthetic_login_mode(stack,manifest,args,e.synthetic_value('special'))
                except e.Stop as exc: error = exc
                scans.assert_called_once()
                return manifest, rounds, restarts.call_count, error

    def test_both_owner_rounds_and_the_completion_restart_run(self):
        manifest, rounds, restarts, error = self.run_mode()
        self.assertIsNone(error)
        self.assertEqual(rounds,['phone','captcha'])
        self.assertEqual(restarts,2)
        self.assertEqual(manifest.data['gates']['restart_after_completion_no_duplicate']['status'],e.PASS)

    def test_changed_counters_stop_before_the_owner_can_answer(self):
        manifest, rounds, restarts, error = self.run_mode(changed=True)
        self.assertIsNotNone(error)
        self.assertEqual(rounds,[])
        self.assertEqual(restarts,1)
        self.assertEqual(manifest.data['gates']['restart_adds_no_work']['status'],e.FAIL)

    def test_cleanup_tracks_owned_identities_not_observers_naming_the_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def proc(pid,parent,birth,state='S'):
                p=root/str(pid);p.mkdir(exist_ok=True)
                fields=[state,str(parent)]+['0']*17+[str(birth)]
                (p/'stat').write_text(f'{pid} (python) '+ ' '.join(fields))
                (p/'cmdline').write_bytes(b'observer /owned/run')
            proc(50,1,10)
            proc(100,50,20)
            proc(101,100,21)
            proc(102,1,22)
            proc(200,50,23)
            registered=e.capture_processes([100,102],root)
            self.assertEqual(set(registered),{100,101,102})
            (root/'100/stat').unlink()
            proc(101,1,21)
            self.assertEqual(e.processes_left(registered,root),['101','102'])
            proc(101,1,999)
            proc(102,1,22,'Z')
            self.assertEqual(e.processes_left(registered,root),[])


if __name__ == "__main__":
    unittest.main()
