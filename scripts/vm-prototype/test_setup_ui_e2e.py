"""Tests for setup_ui_e2e.py. They start no VM, no Deck binary and no browser.

A fake Deck (a loopback HTTP server) stands in for the product. The real local service and the real
scripted model run. The test plays the owner's browser by calling the proxy the way the page would.
Values here are synthetic."""
import http.client
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import setup_e2e as se
import setup_synthetic_login as sl
import setup_ui_e2e as ui

SID = "a" * 32
TASK_ID = f"setup-{SID}"
VALUE = "Syn pass Qx7-k2 #%&=+;:/?@!*"


def conn(port, timeout=5):
    return http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)


def call(port, method, path, body=None, headers=None):
    c = conn(port)
    data = None if body is None else json.dumps(body)
    c.request(method, path, body=data, headers=headers or {})
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, raw


class FakeDeck(BaseHTTPRequestHandler):
    """Only what the controller and the page need. `state` is shared and set per test."""
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *a):
        pass

    def reply(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s = FakeDeck.state
        s["seen"].append(("GET", self.path, self.headers.get("Remote-User")))
        if self.path == "/api/setups":
            return self.reply(200, json.dumps(s["rows"]()).encode())
        if self.path == f"/api/bots/{SID}":
            return self.reply(200, json.dumps({"tasks": s["tasks"], "working": False}).encode())
        if self.path == "/api/chats/1/messages":
            return self.reply(200, json.dumps(s["messages"]).encode())
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"5\r\nfirst\r\n")
            self.wfile.flush()
            s["first_sent"].set()
            s["finish_stream"].wait(10)
            self.wfile.write(b"4\r\nlast\r\n0\r\n\r\n")
            return
        if self.path == "/idle":  # one event, then silence until the peer closes
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: one\n\n")
            self.wfile.flush()
            self.connection.settimeout(5)
            try:
                if self.connection.recv(1) == b"":
                    s["upstream_saw_close"].set()
            except OSError:
                pass
            return
        if self.path == "/wait-for-close":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"data: tick\n\n")
                    self.wfile.flush()
                    time.sleep(0.02)
            except OSError:
                s["upstream_saw_close"].set()
                return
        self.reply(404)

    def do_POST(self):
        s = FakeDeck.state
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        s["seen"].append(("POST", self.path, self.headers.get("Remote-User")))
        s["posts"].append(self.path)
        if self.path.endswith("/approve"):
            s["on_approve"]()
            return self.reply(204)
        if self.path.endswith("/secret"):
            ok = body.get("value") == s["value"]() and body.get("request_id") == s["credential_rid"]
            if ok:
                s["on_secret"]()
            return self.reply(204 if ok else 409)
        if self.path.endswith("/challenge/respond"):
            s["on_respond"](body["request_id"])
            return self.reply(204)
        self.reply(404)


def free_port():
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


def row(wait=None, phase="Waiting", resolved=(), ready=False):
    return {"version": 3, "chat_id": 1, "runtime_ready": ready, "phase": phase,
            "setup": {"id": SID, "phase": phase, "wait": wait, "resolved": list(resolved), "draft": {}}}


def cred_wait(rid="c1", answered=False):
    return {"request_id": rid, "step": {"Credential": "FIXTURE_PASS"}, "kind": {"Credential": {"label": "FIXTURE_PASS"}}, "answered": answered}


def login_wait(kind, rid, svc, answered=False):
    w = {"request_id": rid, "step": {"Login": TASK_ID}, "kind": kind, "answered": answered}
    if kind == "PhoneApproval":
        w["match_number"] = svc.phone_match()
        w["instructions"] = "Approve the synthetic sign-in."
    else:
        w["instructions"] = f"Complete the synthetic CAPTCHA-style check {svc.captcha_check()} on the local service."
    return w


class FakeStack:
    """The parts of setup_e2e.Stack that the controller uses. No process starts."""

    def __init__(self, out, deck_port, value):
        self.out = Path(out)
        (self.out / "private").mkdir(parents=True, exist_ok=True)
        self.rt = self.out / "computers"
        self.a = Namespace(base="unused", ready_timeout=5, settle=0, hold_timeout=5)
        self.client = se.Client(f"http://127.0.0.1:{deck_port}")
        self.service = sl.Service(value, port=0)
        self.model = sl.Model(port=0, hold_timeout=5)
        self.service.start()
        self.model.start()
        self.starts, self.owned, self.restarts, self.cleaned = 1, {}, 0, 0
        self.server_alive = True

    def alive(self, name):
        return self.server_alive

    def stop(self, name):
        self.server_alive = False

    def start_server(self):
        self.restarts += 1
        self.starts += 1
        self.server_alive = True

    def vm_dirs(self):
        return []

    def cleanup(self):
        self.cleaned += 1
        self.service.stop()
        self.model.stop()
        return []


class Rig(unittest.TestCase):
    """A fake Deck, a fake stack, the controller, the proxy and the control server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rowbox = {"row": None}
        self.ctl_box = {}
        self.st = {"seen": [], "posts": [], "rows": lambda: [self.rowbox["row"]] if self.rowbox["row"] else [], "tasks": [], "messages": [],
                   "first_sent": threading.Event(), "finish_stream": threading.Event(), "upstream_saw_close": threading.Event(),
                   "credential_rid": "c1", "value": lambda: self.ctl_box["ctl"].box.value,
                   "on_approve": lambda: self.rowbox.update(row=row(cred_wait())),
                   "on_secret": lambda: self.rowbox.update(row=row(None, "Provisioning")),
                   "on_respond": self.respond}
        FakeDeck.state = self.st
        self.deck = ThreadingHTTPServer(("127.0.0.1", 0), FakeDeck)
        self.deck.daemon_threads = True
        threading.Thread(target=self.deck.serve_forever, daemon=True).start()
        self.addCleanup(self.deck.server_close)
        self.addCleanup(self.deck.shutdown)
        self.stack = FakeStack(Path(self.tmp.name) / "run", self.deck.server_port, VALUE)
        self.ctl = ui.Controller(self.stack, VALUE, se.Manifest("ui-synthetic-login"), guest=None)
        self.ctl_box["ctl"] = self.ctl
        self.ctl.start(self.deck.server_port)
        self.addCleanup(self.ctl.close_listeners)
        self.ui_port, self.ctl_port = self.ctl.proxy.port, self.ctl.control.port
        self.hdr = {"X-Control-Token": self.ctl.token}

    def respond(self, rid):
        w = self.rowbox["row"]["setup"]["wait"]
        if w and w["request_id"] == rid:
            w["answered"] = True

    def control(self, method, path, body=None, token=True):
        st, raw = call(self.ctl_port, method, path, body, self.hdr if token else {})
        return st, (json.loads(raw) if raw else None)

    def page(self, method, path, body=None, headers=None):
        return call(self.ui_port, method, path, body, headers)

    def state(self):
        return self.control("GET", "/state")[1]


class SecretBoxTest(unittest.TestCase):
    def box(self, pending):
        return ui.SecretBox(VALUE, "FIXTURE_PASS", lambda: pending[0])

    def test_value_is_released_once_for_the_pending_request_and_label(self):
        pending = [{"request_id": "c1", "label": "FIXTURE_PASS"}]
        b = self.box(pending)
        self.assertEqual(b.take("c1", "FIXTURE_PASS"), VALUE)
        with self.assertRaises(ui.Refused) as r:
            b.take("c1", "FIXTURE_PASS")
        self.assertEqual(r.exception.code, 410)

    def test_stale_wrong_label_and_no_pending_request_are_refused_and_do_not_burn_the_value(self):
        pending = [{"request_id": "c2", "label": "FIXTURE_PASS"}]
        b = self.box(pending)
        for rid, label in (("c1", "FIXTURE_PASS"), ("c2", "OTHER")):
            with self.assertRaises(ui.Refused):
                b.take(rid, label)
        pending[0] = None
        with self.assertRaises(ui.Refused):
            b.take("c2", "FIXTURE_PASS")
        pending[0] = {"request_id": "c2", "label": "FIXTURE_PASS"}
        self.assertEqual(b.take("c2", "FIXTURE_PASS"), VALUE)

    def test_a_pending_request_with_another_label_is_refused(self):
        b = self.box([{"request_id": "c1", "label": "OTHER"}])
        with self.assertRaises(ui.Refused):
            b.take("c1", "OTHER")


class ProxyTest(Rig):
    def test_it_binds_loopback_only_on_ephemeral_ports(self):
        for srv in (self.ctl.proxy, self.ctl.control):
            self.assertEqual(srv.httpd.server_address[0], "127.0.0.1")
            self.assertNotEqual(srv.port, 0)
        self.assertNotEqual(self.ui_port, self.ctl_port)

    def test_it_sets_remote_user_and_ignores_the_client_value(self):
        self.page("GET", "/api/setups", headers={"Remote-User": "attacker"})
        self.assertEqual(self.st["seen"][-1][2], ui.OWNER)

    def test_a_foreign_host_header_is_refused(self):
        st, _ = self.page("GET", "/api/setups", headers={"Host": "evil.example"})
        self.assertEqual(st, 421)
        self.assertFalse(self.st["seen"])

    def test_sse_chunks_arrive_before_the_upstream_finishes(self):
        c = conn(self.ui_port, timeout=5)
        c.request("GET", "/events")
        r = c.getresponse()
        self.assertTrue(self.st["first_sent"].wait(3))
        got = r.read1(100)  # must return while the upstream is still open
        self.assertIn(b"first", got)
        self.assertFalse(self.st["finish_stream"].is_set())
        self.st["finish_stream"].set()
        rest = b""
        while chunk := r.read1(100):
            rest += chunk
        self.assertIn(b"last", rest)
        c.close()

    def test_a_client_that_disconnects_closes_the_upstream_stream_without_noise(self):
        errors = []
        self.ctl.proxy.httpd.handle_error = lambda *a: errors.append(a)
        c = conn(self.ui_port, timeout=5)
        c.connect()
        sock = c.sock  # http.client drops its reference for a close-delimited body
        c.request("GET", "/wait-for-close")
        r = c.getresponse()
        self.assertIn(b"tick", r.read1(100))
        sock.shutdown(socket.SHUT_RDWR)
        c.close()
        self.assertTrue(self.st["upstream_saw_close"].wait(5))
        time.sleep(0.2)
        self.assertEqual(errors, [])

    def test_a_client_that_leaves_an_idle_stream_closes_the_upstream_at_once(self):
        c = conn(self.ui_port, timeout=5)
        c.connect()
        sock = c.sock
        c.request("GET", "/idle")
        r = c.getresponse()
        self.assertIn(b"one", r.read1(100))
        sock.shutdown(socket.SHUT_RDWR)
        c.close()
        self.assertTrue(self.st["upstream_saw_close"].wait(3))  # no later write is needed to notice

    def test_a_missing_upstream_is_a_502_and_the_proxy_survives(self):
        self.deck.shutdown()
        self.deck.server_close()
        self.assertEqual(self.page("GET", "/api/setups")[0], 502)
        self.assertEqual(self.control("GET", "/plan")[0], 200)

    def test_the_secret_route_needs_same_origin_and_the_pending_request(self):
        self.rowbox["row"] = row(cred_wait())
        path = "/__e2e/secret?request_id=c1&label=FIXTURE_PASS"
        self.assertEqual(self.page("GET", path)[0], 403)  # no Sec-Fetch-Site
        self.assertEqual(self.page("GET", path, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.page("GET", "/__e2e/secret?request_id=stale&label=FIXTURE_PASS", headers={"Sec-Fetch-Site": "same-origin"})[0], 409)
        st, raw = self.page("GET", path, headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual((st, json.loads(raw)), (200, {"value": VALUE}))
        self.assertEqual(self.page("GET", path, headers={"Sec-Fetch-Site": "same-origin"})[0], 410)
        self.assertEqual(self.page("GET", "/__e2e/other", headers={"Sec-Fetch-Site": "same-origin"})[0], 404)

    def test_the_proxy_log_holds_no_query_and_no_value(self):
        self.rowbox["row"] = row(cred_wait())
        self.page("GET", "/__e2e/secret?request_id=c1&label=FIXTURE_PASS", headers={"Sec-Fetch-Site": "same-origin"})
        text = json.dumps(self.ctl.proxy.log)
        self.assertIn("/__e2e/secret", text)
        self.assertNotIn("request_id", text)
        self.assertNotIn(VALUE, text)
        for needle in se.variants(VALUE):
            self.assertNotIn(needle.decode(), text)


class ControlTest(Rig):
    def test_every_control_call_needs_the_token(self):
        for method, path in ui.ROUTES:
            self.assertEqual(self.control(method, path, {} if method == "POST" else None, token=False)[0], 401, path)

    def test_the_route_table_is_fixed_and_has_no_approval_or_delivery_route(self):
        self.assertEqual(set(ui.ROUTES), {("GET", "/plan"), ("GET", "/state"), ("POST", "/owner/phone"), ("POST", "/owner/captcha"), ("POST", "/model/hold"),
                                          ("POST", "/model/release"), ("POST", "/deck/restart"), ("POST", "/gate"), ("POST", "/finish"), ("POST", "/stop")})
        for _, path in ui.ROUTES:
            self.assertFalse(any(w in path for w in ("approve", "secret", "respond", "deliver")), path)
        self.assertEqual(self.control("GET", "/approve")[0], 404)

    def test_the_deck_client_can_only_read(self):
        with self.assertRaises(PermissionError):
            self.ctl.deck.raw("POST", f"/api/setups/{SID}/approve", {})
        with self.assertRaises(PermissionError):
            self.ctl.deck.raw("DELETE", "/api/setups")
        self.assertEqual(self.ctl.deck.raw("GET", "/api/setups")[0], 200)

    def test_plan_names_the_task_and_labels_but_holds_no_value(self):
        st, plan = self.control("GET", "/plan")
        self.assertEqual((st, plan["task"], plan["credential_label"]), (200, sl.TASK, "FIXTURE_PASS"))
        self.assertIn("job", plan)
        self.assertNotIn(VALUE, json.dumps(plan))

    def test_state_stages_follow_the_deck_rows(self):
        self.assertEqual(self.state()["stage"], "no_setup")
        for r, want in [(row(None, "Draft"), "draft"), (row(cred_wait()), "credential_wait"), (row(None, "Provisioning"), "provisioning"),
                        (row(login_wait("PhoneApproval", "p1", self.stack.service)), "phone_wait"),
                        (row(login_wait("PhoneApproval", "p1", self.stack.service, True)), "phone_answered"),
                        (row(login_wait("Captcha", "k1", self.stack.service)), "captcha_wait"),
                        (row(None, "Investigate"), "unknown_outcome"), (row(None, "Exhausted"), "unknown_outcome")]:
            self.rowbox["row"] = r
            self.assertEqual(self.state()["stage"], want)
        self.st["tasks"] = [{"id": TASK_ID, "state": "done"}]
        self.rowbox["row"] = row(None, "Ready", ready=True)
        self.assertEqual(self.state()["stage"], "done")

    def test_two_setups_are_ambiguous_and_never_guessed(self):
        self.st["rows"] = lambda: [row(cred_wait()), row(cred_wait())]
        self.assertEqual(self.state()["stage"], "ambiguous_setups")
        self.assertEqual(self.page("GET", "/__e2e/secret?request_id=c1&label=FIXTURE_PASS", headers={"Sec-Fetch-Site": "same-origin"})[0], 409)

    def test_state_has_ids_and_counts_but_no_secret_match_number_or_check_id(self):
        svc = self.stack.service
        svc.owner_phone  # the service creates its ids at login
        self.service_login()
        self.rowbox["row"] = row(login_wait("PhoneApproval", "p1", svc))
        text = json.dumps(self.state())
        for banned in (VALUE, svc.phone_match(), svc.captcha_check()):
            self.assertNotIn(banned, text)
        s = self.state()
        self.assertEqual((s["setup_id"], s["task_id"], s["wait"]["request_id"], s["wait"]["kind"]), (SID, TASK_ID, "p1", "PhoneApproval"))
        self.assertEqual(s["counts"]["login_success"], 1)

    def service_login(self):
        c = conn(self.stack.service.port)
        c.request("POST", "/login", body=f"user={sl.USER}&password=" + __import__("urllib.parse").parse.quote(VALUE), headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(c.getresponse().status, 303)
        c.close()

    def test_the_owner_action_needs_the_current_unanswered_wait_of_that_kind(self):
        svc = self.stack.service
        self.service_login()
        self.rowbox["row"] = row(login_wait("PhoneApproval", "p1", svc))
        for path, body in (("/owner/phone", {"request_id": "stale"}), ("/owner/captcha", {"request_id": "p1"}), ("/owner/phone", {"request_id": "p1", "value": "1"}), ("/owner/phone", {})):
            self.assertIn(self.control("POST", path, body)[0], (400, 409), (path, body))
        self.assertEqual(svc.state()["phone"], "pending")
        self.assertEqual(self.control("POST", "/owner/phone", {"request_id": "p1"})[0], 200)
        self.assertEqual(svc.state()["phone"], "completed")
        self.assertEqual(self.state()["gates"]["phone_service_refuses_wrong_owner_id"], "pass")
        self.assertEqual(self.state()["gates"]["phone_owner_action_completed_in_service"], "pass")
        self.assertEqual(self.control("POST", "/owner/phone", {"request_id": "p1"})[0], 409)  # one use

    def test_an_answered_wait_is_refused_for_the_owner_action(self):
        self.service_login()
        self.rowbox["row"] = row(login_wait("PhoneApproval", "p1", self.stack.service, True))
        self.assertEqual(self.control("POST", "/owner/phone", {"request_id": "p1"})[0], 409)
        self.assertEqual(self.stack.service.state()["phone"], "pending")

    def test_the_gate_route_takes_only_the_fixed_ui_checks_with_fixed_fields(self):
        ok = {"name": "ui_privacy_viewer", "status": "pass", "detail": "no value in the viewer"}
        for bad in ({**ok, "name": "first_task_done"}, {**ok, "status": "maybe"}, {**ok, "extra": 1}, {"name": "ui_privacy_viewer"}, {**ok, "detail": "x" * 400}):
            self.assertEqual(self.control("POST", "/gate", bad)[0], 400, bad)
        self.assertEqual(self.control("POST", "/gate", ok)[0], 200)
        self.assertEqual(self.ctl.m.data["gates"]["ui_privacy_viewer"]["status"], "pass")

    def test_a_gate_detail_that_holds_the_value_becomes_a_failure(self):
        self.control("POST", "/gate", {"name": "ui_screenshot_privacy", "status": "pass", "detail": "saw " + VALUE})
        g = self.ctl.m.data["gates"]["ui_screenshot_privacy"]
        self.assertEqual(g["status"], "fail")
        self.assertNotIn(VALUE, json.dumps(g))

    def test_hold_needs_an_open_challenge_and_release_needs_a_held_wake(self):
        self.assertEqual(self.control("POST", "/model/hold")[0], 409)
        self.assertEqual(self.control("POST", "/model/release")[0], 409)

    def test_restart_touches_only_the_owned_deck_and_keeps_ownership_evidence(self):
        self.service_login()
        self.rowbox["row"] = row(login_wait("PhoneApproval", "p1", self.stack.service))
        st, body = self.control("POST", "/deck/restart")
        self.assertEqual((st, self.stack.restarts, self.stack.starts), (200, 1, 2))
        self.assertEqual(self.state()["gates"]["restart_preserves_waiting_ownership"], "pass")
        self.assertEqual(self.state()["gates"]["restart_adds_no_work"], "pass")

    def test_a_restart_that_changes_the_wait_fails_the_gate(self):
        self.service_login()
        self.rowbox["row"] = row(login_wait("PhoneApproval", "p1", self.stack.service))
        orig = self.stack.start_server
        self.stack.start_server = lambda: (orig(), self.rowbox.update(row=row(login_wait("PhoneApproval", "p2", self.stack.service))))
        self.control("POST", "/deck/restart")
        self.assertEqual(self.state()["gates"]["restart_preserves_waiting_ownership"], "fail")


class NoAutomaticActions(Rig):
    def test_controller_made_no_write_to_deck_during_a_whole_ui_flow(self):
        self.drive_flow()
        direct = [s for s in self.st["seen"] if s[0] == "POST" and s[2] != ui.OWNER]
        self.assertEqual(direct, [])
        self.assertEqual(len([p for p in self.st["posts"] if p.endswith("/approve")]), 1)

    def drive_flow(self):
        svc, model = self.stack.service, self.stack.model
        self.rowbox["row"] = row(None, "Draft")
        self.assertEqual(self.state()["stage"], "draft")
        time.sleep(0.2)
        self.assertEqual(self.st["posts"], [])  # nothing approves, nothing delivers on its own
        self.assertEqual(self.page("POST", f"/api/setups/{SID}/approve", {"version": 3, "revision": 1})[0], 204)
        self.assertEqual(self.state()["stage"], "credential_wait")
        st, raw = self.page("GET", "/__e2e/secret?request_id=c1&label=FIXTURE_PASS", headers={"Sec-Fetch-Site": "same-origin"})
        value = json.loads(raw)["value"]
        self.assertEqual(self.page("POST", f"/api/setups/{SID}/secret", {"version": 3, "request_id": "c1", "value": value})[0], 204)
        self.assertEqual(self.state()["stage"], "provisioning")
        c = conn(svc.port)  # the guest's bare signs in
        c.request("POST", "/login", body=f"user={sl.USER}&password=" + __import__("urllib.parse").parse.quote(value), headers={"Content-Type": "application/x-www-form-urlencoded"})
        cookie = c.getresponse().getheader("Set-Cookie").split(";")[0]
        c.close()
        for kind, wk, rid in (("phone", "PhoneApproval", "p1"), ("captcha", "Captcha", "k1")):
            self.rowbox["row"] = row(login_wait(wk, rid, svc))
            self.assertEqual(self.state()["stage"], f"{kind}_wait")
            self.assertEqual(self.control("POST", f"/owner/{kind}", {"request_id": rid})[0], 200)
            self.assertEqual(self.control("POST", "/model/hold")[0], 200)
            self.assertEqual(self.page("POST", f"/api/setups/{SID}/challenge/respond", {"version": 3, "request_id": rid, "decision": "completed"})[0], 204)
            self.assertEqual(self.state()["stage"], f"{kind}_answered")
            out = {}
            t = threading.Thread(target=lambda: out.update(r=self.wake(model, rid)))
            t.start()
            self.assertTrue(model.wait_held(5))
            self.assertTrue(self.state()["model"]["held"])
            self.assertEqual(self.state()["stage"], f"{kind}_answered")  # the answer alone is not verification
            self.assertEqual(self.control("POST", "/model/release")[0], 200)
            t.join(5)
            self.assertEqual(out["r"], 200)
            if kind == "phone":  # the guest moves on to the next page
                g = conn(svc.port)
                g.request("POST", "/verify/phone/continue", body="", headers={"Cookie": cookie})
                self.assertEqual(g.getresponse().status, 303)
                g.close()
            self.rowbox["row"] = row(None, "Waiting", resolved=[{"request_id": r_, "outcome": "verified"} for r_ in ("p1", "k1")[:1 if kind == "phone" else 2]])
        return value

    def wake(self, model, rid):
        hist = [{"role": "user", "content": f"owner_help_status for request {rid}, please"}]
        c = conn(model.port, timeout=10)
        c.request("POST", "/v1/chat/completions", body=json.dumps({"messages": hist}), headers={"Content-Length": str(len(json.dumps({"messages": hist})))})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def test_a_complete_flow_through_both_owner_phases_ends_with_a_passing_finish(self):
        self.drive_flow()
        self.stack.model.ledger = [{"seq": i, "name": n, "args": {"steps": f"type ${{{sl.REF}}}"} if n == "browser_step" else {}} for i, n in enumerate(sl.EXPECTED_NAMES)]
        self.stack.model.texts = [sl.DONE_TEXT]
        self.st["tasks"] = [{"id": TASK_ID, "state": "done"}]
        self.st["messages"] = [{"text": sl.DONE_TEXT}]
        st = self.stack.service._st  # the guest finished its pages
        st.update(login_success=1, submit_attempts=1, submit_success=1)
        self.rowbox["row"] = row(None, "Ready", resolved=[{"request_id": "p1", "outcome": "verified"}, {"request_id": "k1", "outcome": "verified"}], ready=True)
        self.ctl.guest = type("G", (), {"check_secret_file": lambda s, n, v: {"value_matches": v == VALUE, "mode": "0o600", "exists": True, "name_present": True, "owner": 1000}})()
        (self.stack.out / "data" / "bots" / SID).mkdir(parents=True)
        (self.stack.out / "private" / "x.log").write_text("clean")
        for name in ("ui_privacy_viewer", "ui_screenshot_privacy", "ui_secret_input_check"):
            self.control("POST", "/gate", {"name": name, "status": "pass", "detail": "ok"})
        st_, body = self.control("POST", "/finish")
        gates = self.ctl.m.data["gates"]
        bad = {n: g for n, g in gates.items() if g["status"] != "pass" and n not in ("secret_never_in_argv", "logs_accounted_for_each_start")}
        self.assertEqual(st_, 200)
        self.assertEqual(bad, {})
        for name in ("phone_resume_reached_the_model", "phone_answer_alone_leaves_verification_pending", "captcha_answer_alone_leaves_verification_pending",
                     "ui_approval_through_proxy", "ui_secret_form_posted_204", "ui_phone_answer_posted_204", "ui_captcha_answer_posted_204",
                     "first_task_done", "scripted_calls_exactly_once", "guest_file_protected", "secret_absent_from_host_evidence"):
            self.assertEqual(gates[name]["status"], "pass", name)
        self.assertEqual(self.ctl.finish()["ok"], body["ok"])  # idempotent

    def test_finish_fails_when_the_ui_never_posted(self):
        self.rowbox["row"] = row(None, "Ready", ready=True)
        self.control("POST", "/finish")
        for name in ("ui_approval_through_proxy", "ui_secret_form_posted_204", "ui_phone_answer_posted_204"):
            self.assertEqual(self.ctl.m.data["gates"][name]["status"], "fail", name)

    def test_the_value_is_found_in_a_host_file_is_a_failure(self):
        self.rowbox["row"] = row(None, "Ready", ready=True)
        (self.stack.out / "private" / "leak.log").write_text("oops " + VALUE)
        self.control("POST", "/finish")
        self.assertEqual(self.ctl.m.data["gates"]["secret_absent_from_host_evidence"]["status"], "fail")
        self.assertNotIn(VALUE, json.dumps(self.ctl.m.data))

    def test_a_wrong_ledger_fails_the_scripted_calls_gate(self):
        self.stack.model.ledger = [{"seq": 1, "name": "browser_open", "args": {}}]
        self.rowbox["row"] = row(None, "Ready", ready=True)
        self.control("POST", "/finish")
        self.assertEqual(self.ctl.m.data["gates"]["scripted_calls_exactly_once"]["status"], "fail")


class Lifecycle(Rig):
    def test_lost_conditions_are_reported(self):
        self.assertIsNone(self.ctl.lost())
        self.rowbox["row"] = row(None, "Investigate")
        self.assertIn("unknown outcome", self.ctl.lost())
        self.rowbox["row"] = row(None, "Waiting")
        self.assertIsNone(self.ctl.lost())
        self.stack.server_alive = False
        self.assertIn("Deck", self.ctl.lost())

    def test_the_deadline_stops_the_run(self):
        self.ctl.deadline = time.monotonic() - 1
        self.assertIn("deadline", self.ctl.lost())

    def test_stop_cleans_up_once_and_writes_a_manifest_that_is_not_a_pass_without_the_flow(self):
        st, body = self.control("POST", "/stop")
        self.assertEqual((st, body["survivors"]), (200, []))
        self.assertEqual(self.stack.cleaned, 1)
        self.ctl.stop()
        self.assertEqual(self.stack.cleaned, 1)
        manifest = json.loads((self.stack.out / "manifest.json").read_text())
        self.assertFalse(manifest["ok"])
        self.assertEqual(manifest["gates"]["acceptance_complete"]["status"], "fail")
        self.assertEqual(manifest["gates"]["no_owned_process_left"]["status"], "pass")
        self.assertNotIn(VALUE, json.dumps(manifest))

    def test_a_survivor_is_a_failed_gate(self):
        self.stack.cleanup = lambda: ["4242"]
        self.ctl.stop()
        self.assertEqual(self.ctl.m.data["gates"]["no_owned_process_left"]["status"], "fail")

    def test_stop_closes_the_listeners_so_no_port_stays_open(self):
        self.ctl.stop()
        for port in (self.ui_port, self.ctl_port):
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1)


class RealStack(unittest.TestCase):
    """The real setup_e2e.Stack does the cleanup. A controller adds nothing that could be missed."""

    def test_make_stack_is_the_existing_stack_class(self):
        with tempfile.TemporaryDirectory() as d:
            a = ui.parse(["serve", "--base", "x.qcow2", "--deck", "d", "--out", f"{d}/o"])
            self.assertIsInstance(ui.make_stack(a), se.Stack)

    def test_cleanup_stops_the_owned_deck_and_kills_vm_roots_through_the_existing_stack(self):
        with tempfile.TemporaryDirectory() as d:
            a = ui.parse(["serve", "--base", "x.qcow2", "--deck", "d", "--out", f"{d}/o"])
            stack = ui.make_stack(a)
            stack.out.mkdir(parents=True)
            (stack.out / "private").mkdir()
            (stack.rt / SID).mkdir(parents=True)
            stack.procs["server"] = subprocess.Popen(["sleep", "300"])
            pid = stack.procs["server"].pid
            self.assertEqual(stack.cleanup(), [])
            self.assertIsNotNone(stack.procs["server"].poll())
            self.assertIn(pid, stack.owned)

    def test_parse_picks_distinct_ephemeral_ports_and_starts_nothing(self):
        a = ui.parse(["serve", "--base", "x.qcow2", "--deck", "d", "--out", "/tmp/o"])
        self.assertEqual(len({a.server_port, a.fixture_port, a.model_port}), 3)
        self.assertTrue(all(p > 1024 for p in (a.server_port, a.fixture_port, a.model_port)))

    def test_prepare_starts_nothing_and_prints_the_start_command(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o"
            r = subprocess.run(["python3", str(Path(ui.__file__)), "prepare", "--base", "x.qcow2", "--deck", "d", "--out", str(out)], capture_output=True, text=True, cwd=ui.HERE)
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = json.loads(r.stdout)
            self.assertIn("serve", plan["start_command"])
            self.assertFalse(out.exists())


class Pages(unittest.TestCase):
    def test_the_fill_snippet_reads_the_value_inside_the_page_and_returns_only_hash_length_and_boolean(self):
        js = ui.FILL_JS
        self.assertIn("/__e2e/secret", js)
        self.assertIn("deck-protected-value", js)
        self.assertIn('fetch("/__e2e/secret', js)  # relative URL: the page's own origin
        self.assertNotIn("http", js)
        self.assertNotIn("console", js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)
        self.assertNotIn("document.cookie", js)
        self.assertIn("sha256", js.lower())


class RequestScript(unittest.TestCase):
    def test_real_setup_request_identity(self):
        request = SID + ":1"
        self.assertIn("request_id=" + request + "&", ui.fill_js(request))

    def test_query_injection_is_refused(self):
        with self.assertRaises(ValueError):
            ui.fill_js("x&label=other")


if __name__ == "__main__":
    unittest.main()
