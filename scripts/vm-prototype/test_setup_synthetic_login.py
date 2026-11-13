"""Tests for setup_synthetic_login.py. They start no VM and no browser.

The service runs on a loopback port. The scripted model is driven by hand-made message lists.
Values here are synthetic strings made for the tests."""
import http.client
import json
import re
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import setup_synthetic_login as s

HERE = Path(__file__).resolve().parent
VALUE = "Syn pass Qx7-k2 #%&=+;:/?@!*"  # spaces and special characters, no quote, backslash or ${
NEEDLES = {VALUE.encode(), urllib.parse.quote(VALUE, safe="").encode(), urllib.parse.quote_plus(VALUE).encode()}


def leaked(data):
    data = data if isinstance(data, bytes) else data.encode()
    return any(n in data for n in NEEDLES)


class Service(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.svc = s.Service(VALUE, port=0, log=self.logs.append)
        self.svc.start()

    def tearDown(self):
        self.svc.stop()

    def req(self, method, path, form=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.svc.port, timeout=5)
        body = urllib.parse.urlencode(form) if form is not None else None
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {}
        if cookie:
            headers["Cookie"] = cookie
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        out = (r.status, dict(r.getheaders()), data)
        c.close()
        return out

    def login(self, password=VALUE):
        st, h, body = self.req("POST", "/login", {"user": s.USER, "password": password})
        cookie = (h.get("Set-Cookie") or "").split(";")[0] or None
        return st, h, body, cookie

    def to_phone(self):
        return self.login()[3]

    def test_the_login_form_is_accessible(self):
        st, _, body = self.req("GET", "/login")
        text = body.decode()
        self.assertEqual(st, 200)
        self.assertRegex(text, r'<label for="user">Username</label>\s*<input id="user" name="user"')
        self.assertRegex(text, r'<label for="password">Password</label>\s*<input id="password" name="password" type="password"')
        self.assertIn("<button", text)

    def test_a_wrong_password_fails_and_is_counted(self):
        st, h, body, cookie = self.login("wrong")
        self.assertEqual(st, 401)
        self.assertIsNone(cookie)
        self.assertEqual((self.svc.state()["login_failure"], self.svc.state()["login_success"]), (1, 0))

    def test_the_right_value_with_spaces_and_symbols_authenticates(self):
        st, h, body, cookie = self.login()
        self.assertEqual(st, 303)
        self.assertEqual(h["Location"], "/verify/phone")
        self.assertTrue(cookie)
        self.assertEqual(self.svc.state()["login_success"], 1)

    def test_a_near_miss_value_does_not_authenticate(self):
        for near in (VALUE + " ", VALUE.upper(), VALUE[:-1], " " + VALUE):
            self.assertEqual(self.login(near)[0], 401)
        self.assertEqual(self.svc.state()["login_success"], 0)

    def test_the_value_is_never_echoed_in_a_page_a_header_a_url_a_log_or_the_state(self):
        seen = [self.login("wrong"), self.login()]
        cookie = seen[1][3]
        pages = [self.req("GET", p, cookie=cookie) for p in ("/", "/login", "/verify/phone", "/verify/captcha", "/report")]
        for st, h, body, *_ in [*seen, *[(a, b, c) for a, b, c in pages]]:
            self.assertFalse(leaked(body))
            self.assertFalse(leaked(json.dumps(h)))
        self.assertFalse(leaked("\n".join(self.logs)))
        self.assertFalse(leaked(json.dumps(self.svc.state())))
        self.assertTrue(self.logs)

    def test_pages_need_a_session(self):
        for path in ("/verify/phone", "/verify/captcha", "/report"):
            st, h, _ = self.req("GET", path)
            self.assertEqual((st, h["Location"]), (303, "/login"))
        self.assertEqual(self.req("POST", "/report/submit", {})[0], 303)
        self.assertEqual(self.svc.state()["submit_attempts"], 0)

    def test_the_owner_action_completes_the_fixture_step_but_nothing_in_deck(self):
        cookie = self.to_phone()
        self.assertIn("Phone approval: pending", self.req("GET", "/verify/phone", cookie=cookie)[2].decode())
        m = self.svc.phone_match()
        self.assertRegex(m, r"^\d{2}$")
        self.assertFalse(self.svc.owner_phone("00" if m != "00" else "01"))
        self.assertEqual(self.svc.state()["phone"], "pending")
        self.assertTrue(self.svc.owner_phone(m))
        self.assertEqual(self.svc.state()["phone"], "completed")
        self.assertIn("Phone approval: completed", self.req("GET", "/verify/phone", cookie=cookie)[2].decode())

    def test_continue_needs_the_real_phone_step(self):
        cookie = self.to_phone()
        st, h, _ = self.req("POST", "/verify/phone/continue", {}, cookie)
        self.assertEqual(st, 409)
        self.svc.owner_phone(self.svc.phone_match())
        st, h, _ = self.req("POST", "/verify/phone/continue", {}, cookie)
        self.assertEqual((st, h["Location"]), (303, "/verify/captcha"))

    def test_the_captcha_style_check_needs_its_id_and_the_phone_step_first(self):
        cookie = self.to_phone()
        self.assertFalse(self.svc.owner_captcha("CHK-0000"))
        self.svc.owner_phone(self.svc.phone_match())
        self.req("POST", "/verify/phone/continue", {}, cookie)
        page = self.req("GET", "/verify/captcha", cookie=cookie)[2].decode()
        check = re.search(r"Check id: (CHK-\d{4})", page)[1]
        self.assertIn("CAPTCHA-style check: pending", page)
        self.assertFalse(self.svc.owner_captcha("CHK-0000" if check != "CHK-0000" else "CHK-0001"))
        self.assertEqual(self.req("POST", "/verify/captcha/continue", {}, cookie)[0], 409)
        self.assertTrue(self.svc.owner_captcha(check))
        self.assertIn("CAPTCHA-style check: completed", self.req("GET", "/verify/captcha", cookie=cookie)[2].decode())

    def walk(self):
        cookie = self.to_phone()
        self.svc.owner_phone(self.svc.phone_match())
        self.req("POST", "/verify/phone/continue", {}, cookie)
        self.svc.owner_captcha(self.svc.captcha_check())
        self.req("POST", "/verify/captcha/continue", {}, cookie)
        return cookie

    def test_the_success_action_happens_once_and_repeats_are_counted_not_applied(self):
        cookie = self.walk()
        self.assertIn("Submit report", self.req("GET", "/report", cookie=cookie)[2].decode())
        self.assertEqual(self.req("POST", "/report/submit", {}, cookie)[0], 303)
        self.assertEqual(self.req("POST", "/report/submit", {}, cookie)[0], 409)
        st = self.svc.state()
        self.assertEqual((st["submit_success"], st["submit_attempts"]), (1, 2))
        self.assertIn("Report submitted", self.req("GET", "/report/done", cookie=cookie)[2].decode())

    def test_submit_before_the_checks_does_nothing(self):
        cookie = self.to_phone()
        self.assertEqual(self.req("POST", "/report/submit", {}, cookie)[0], 409)
        self.assertEqual(self.svc.state()["submit_success"], 0)

    def test_a_second_login_is_visible_in_the_counters(self):
        self.login()
        self.login()
        self.assertEqual(self.svc.state()["login_success"], 2)


class Steps(unittest.TestCase):
    def test_supported_dsl_is_accepted(self):
        self.assertEqual(s.invalid_steps(s.LOGIN_STEPS), [])
        for text in (s.CONTINUE_PHONE_STEPS, s.CONTINUE_CAPTCHA_STEPS, s.SUBMIT_STEPS):
            self.assertEqual(s.invalid_steps(text), [])

    def test_unknown_verbs_and_screenshots_are_refused(self):
        self.assertTrue(s.invalid_steps("sleep 5"))
        self.assertTrue(s.invalid_steps("shot a"))
        self.assertTrue(s.invalid_steps('type "x"'))
        self.assertTrue(s.invalid_steps('click text=""\nfly away'))

    def test_the_login_steps_use_the_reference_and_no_value(self):
        self.assertIn("${FIXTURE_PASS}", s.LOGIN_STEPS)
        self.assertFalse(leaked(s.LOGIN_STEPS))


def msgs(*parts):
    """Build a message list from ('user', text) | ('call', id, name, args) | ('tool', id, text)."""
    out = [{"role": "system", "content": "s"}]
    for p in parts:
        if p[0] == "user":
            out.append({"role": "user", "content": p[1]})
        elif p[0] == "call":
            out.append({"role": "assistant", "content": None, "tool_calls": [{"id": p[1], "type": "function", "function": {"name": p[2], "arguments": json.dumps(p[3])}}]})
        else:
            out.append({"role": "tool", "tool_call_id": p[1], "content": p[2]})
    return out


PHONE_PAGE = "Phone approval required\nMatch number: 47\nPhone approval: pending"
CAPTCHA_PAGE = "CAPTCHA-style check\nCheck id: CHK-5821\nCAPTCHA-style check: pending"


def wake(rid):
    return ("user", f"The owner marked [challenge {rid} completed]. This does not prove the sign-in worked. Call owner_help_status for request {rid}, check the service with a read-only tool, then call report_help_verification before you continue.")


class Script(unittest.TestCase):
    def step(self, history):
        return s.decide(history)

    def test_the_first_message_opens_the_local_login(self):
        t = self.step(msgs(("user", s.TASK)))
        self.assertEqual((t.kind, t.name), ("tool", "browser_open"))
        self.assertTrue(t.args["url"].startswith(s.GUEST_URL))
        self.assertEqual(t.args["url"].rstrip("/").rsplit("/", 1)[-1], "login")

    def test_every_call_obeys_the_tool_schemas_and_the_dsl(self):
        # Walk the whole happy path and check each emitted call.
        h = [("user", s.TASK)]
        seen = []
        replies = {"browser_open": "ok", "browser_step": "ok", "request_owner_help": '{"ok": true, "request_id": "main:1", "waiting": "x"}',
                   "owner_help_status": '{"ok": true, "state": "answered", "request_id": "main:1"}', "report_help_verification": '{"ok": true}', "signin_verified": '{"ok": true}'}
        page = iter([PHONE_PAGE, "Phone approval: completed", CAPTCHA_PAGE, "CAPTCHA-style check: completed", "Report submitted"])
        for i in range(40):
            t = self.step(msgs(*h))
            if t.kind == "text":
                break
            seen.append(t)
            self.assertIn(t.name, s.TOOL_SCHEMAS)
            allowed, required = s.TOOL_SCHEMAS[t.name]
            self.assertTrue(set(t.args) <= allowed, (t.name, t.args))
            self.assertTrue(required <= set(t.args), (t.name, t.args))
            if t.name == "browser_step":
                self.assertEqual(s.invalid_steps(t.args["steps"]), [])
            if t.name == "request_owner_help":
                rid = f"main:{sum(1 for x in seen if x.name == 'request_owner_help')}"
                h += [("call", f"c{i}", t.name, t.args), ("tool", f"c{i}", replies[t.name].replace("main:1", rid)), wake(rid)]
                continue
            if t.name == "owner_help_status":
                h += [("call", f"c{i}", t.name, t.args), ("tool", f"c{i}", replies[t.name].replace("main:1", t.args["request_id"]))]
                continue
            out = next(page) if t.name == "browser_read" else replies[t.name]
            h += [("call", f"c{i}", t.name, t.args), ("tool", f"c{i}", out)]
        names = [x.name for x in seen]
        self.assertEqual(names, [
            "browser_open", "browser_step", "browser_read", "request_owner_help",
            "owner_help_status", "browser_read", "report_help_verification", "browser_step", "browser_read", "request_owner_help",
            "owner_help_status", "browser_read", "report_help_verification", "signin_verified", "browser_step", "browser_step", "browser_read"])
        self.assertEqual(t.text, s.DONE_TEXT)
        reports = [x.args for x in seen if x.name == "report_help_verification"]
        self.assertEqual([r["result"] for r in reports], ["verified", "verified"])
        self.assertEqual([x.args["kind"] for x in seen if x.name == "request_owner_help"], ["phone", "captcha"])
        self.assertEqual(seen[3].args["match_number"], "47")
        self.assertIn("CHK-5821", seen[9].args["instructions"])
        self.assertNotIn("match_number", seen[9].args)
        blob = json.dumps([(x.name, x.args) for x in seen])
        self.assertIn("${FIXTURE_PASS}", blob)
        self.assertFalse(leaked(blob))
        self.assertNotIn("shot", blob.lower().replace("shot_", ""))

    def test_a_wake_calls_owner_help_status_with_that_request_id_only(self):
        for rid in ("main:1", "setup-x:7", "t-1:2"):
            t = self.step(msgs(("user", s.TASK), ("call", "a", "request_owner_help", {}), ("tool", "a", "{}"), wake(rid)))
            self.assertEqual((t.name, t.args), ("owner_help_status", {"request_id": rid}))

    def test_a_cancel_wake_stops_without_a_tool(self):
        t = self.step(msgs(("user", s.TASK), ("user", "The owner cancelled [challenge main:1 cancelled]. Do not retry this sign-in. Report what is blocked.")))
        self.assertEqual(t.kind, "text")
        self.assertIn("blocked", t.text.lower())

    def evidence_history(self, page, kind="phone", answered=True):
        state = "answered" if answered else "open"
        return msgs(("user", s.TASK), ("call", "o", "browser_open", {}), ("tool", "o", "ok"),
                    ("call", "l", "browser_step", {"steps": s.LOGIN_STEPS}), ("tool", "l", "ok"),
                    ("call", "r", "browser_read", {}), ("tool", "r", PHONE_PAGE),
                    ("call", "q", "request_owner_help", {"kind": kind, "instructions": "x", "match_number": "47"}), ("tool", "q", '{"ok": true, "request_id": "main:1"}'),
                    wake("main:1"),
                    ("call", "s", "owner_help_status", {"request_id": "main:1"}), ("tool", "s", json.dumps({"ok": True, "state": state, "request_id": "main:1"})),
                    *([("call", "e", "browser_read", {}), ("tool", "e", page)] if answered else []))

    def test_the_verdict_follows_the_service_page_not_the_owner_answer(self):
        t = self.step(self.evidence_history("Phone approval: completed"))
        self.assertEqual((t.name, t.args["result"], t.args["request_id"]), ("report_help_verification", "verified", "main:1"))
        t = self.step(self.evidence_history("Phone approval: pending"))
        self.assertEqual((t.name, t.args["result"]), ("report_help_verification", "not_verified"))
        self.assertLessEqual(len(t.args["observation"]), 200)

    def test_the_script_does_not_verify_before_it_reads_the_service(self):
        h = self.evidence_history("", answered=True)[:-2]  # just after owner_help_status
        t = self.step(h)
        self.assertEqual((t.kind, t.name), ("tool", "browser_read"))

    def test_an_unanswered_status_reads_nothing_and_reports_nothing(self):
        t = self.step(self.evidence_history("", answered=False))
        self.assertEqual(t.kind, "text")

    def test_a_failed_tool_stops_the_script(self):
        for out in ('{"ok": false, "error": "stale_ref"}', "error: not ready"):
            t = self.step(msgs(("user", s.TASK), ("call", "o", "browser_open", {}), ("tool", "o", out)))
            self.assertEqual(t.kind, "text")

    def test_a_missing_match_number_stops(self):
        h = msgs(("user", s.TASK), ("call", "o", "browser_open", {}), ("tool", "o", "ok"), ("call", "l", "browser_step", {"steps": s.LOGIN_STEPS}), ("tool", "l", "ok"), ("call", "r", "browser_read", {}), ("tool", "r", "nothing here"))
        self.assertEqual(self.step(h).kind, "text")

    def test_the_final_text_needs_the_submitted_page(self):
        h = msgs(("user", s.TASK), ("call", "x", "browser_step", {"steps": s.SUBMIT_STEPS}), ("tool", "x", "ok"), ("call", "r", "browser_read", {}), ("tool", "r", "Report not submitted"))
        self.assertNotEqual(self.step(h).text, s.DONE_TEXT)


class Emulator:
    """Plays the guest's bare for the five step lists the script uses. Test code only: it knows the value
    because the real guest substitutes ${FIXTURE_PASS} from its secrets file."""

    def __init__(self, svc, value):
        self.svc, self.value, self.cookie, self.path = svc, value, None, "/login"

    def http(self, method, path, form=None):
        c = http.client.HTTPConnection("127.0.0.1", self.svc.port, timeout=5)
        c.request(method, path, body=urllib.parse.urlencode(form) if form is not None else None,
                  headers={"Content-Type": "application/x-www-form-urlencoded", **({"Cookie": self.cookie} if self.cookie else {})})
        r = c.getresponse()
        body = r.read().decode()
        if r.getheader("Set-Cookie"):
            self.cookie = r.getheader("Set-Cookie").split(";")[0]
        c.close()
        return r.status, r.getheader("Location"), body

    def follow(self, st, loc, body):
        if st in (301, 302, 303) and loc:
            self.path = loc
            return self.http("GET", loc)
        return st, loc, body

    def run(self, name, args):
        if name == "browser_open":
            self.path = "/login"
            return "ok"
        if name == "browser_read":
            return self.http("GET", self.path)[2]
        steps = args["steps"].splitlines()
        for line in steps:
            if line.startswith("click role=button name=\"Sign in\""):
                self.follow(*self.http("POST", "/login", {"user": s.USER, "password": self.value}))
            elif line.startswith("click role=button"):
                action = {"/verify/phone": "/verify/phone/continue", "/verify/captcha": "/verify/captcha/continue", "/report": "/report/submit"}[self.path]
                self.follow(*self.http("POST", action, {}))
            elif line.startswith("expect url~"):
                want = re.search(r'"([^"]*)"', line)[1]
                if want not in self.path:
                    return json.dumps({"ok": False, "error": "expect failed"})
        return "ok"


class Integration(unittest.TestCase):
    """The script against the real service pages, with no Deck and no browser."""

    def drive(self, complete_owner_steps=True):
        svc = s.Service(VALUE, port=0)
        svc.start()
        emu = Emulator(svc, VALUE)
        h, seen, rids = [("user", s.TASK)], [], 0
        try:
            for i in range(40):
                t = s.decide(msgs(*h))
                if t.kind == "text":
                    return svc, seen, t.text
                seen.append(t)
                if t.name in ("owner_help_status", "report_help_verification", "request_owner_help", "signin_verified"):
                    if t.name == "request_owner_help":
                        rids += 1
                        rid = f"main:{rids}"
                        if complete_owner_steps:
                            (svc.owner_phone(t.args["match_number"]) if t.args["kind"] == "phone" else svc.owner_captcha(re.search(r"CHK-\d{4}", t.args["instructions"])[0]))
                        h += [("call", f"c{i}", t.name, t.args), ("tool", f"c{i}", json.dumps({"ok": True, "request_id": rid})), wake(rid)]
                        continue
                    out = json.dumps({"ok": True, "state": "answered", "request_id": t.args.get("request_id")}) if t.name == "owner_help_status" else '{"ok": true}'
                else:
                    out = emu.run(t.name, t.args)
                h += [("call", f"c{i}", t.name, t.args), ("tool", f"c{i}", out)]
        finally:
            pass
        self.fail("did not finish")

    def test_the_full_script_completes_against_the_real_pages_with_one_effect(self):
        svc, seen, final = self.drive()
        try:
            st = svc.state()
            self.assertEqual(final, s.DONE_TEXT)
            self.assertEqual((st["login_success"], st["login_failure"], st["submit_success"], st["submit_attempts"]), (1, 0, 1, 1))
            self.assertEqual([x.name for x in seen], s.EXPECTED_NAMES)
            self.assertEqual(s.ledger_problems([{"name": x.name, "args": x.args} for x in seen]), [])
        finally:
            svc.stop()

    def test_if_the_owner_step_is_not_done_the_script_reports_not_verified(self):
        svc, seen, final = self.drive(complete_owner_steps=False)
        try:
            report = next(x for x in seen if x.name == "report_help_verification")
            self.assertEqual(report.args["result"], "not_verified")
            self.assertEqual(svc.state()["submit_success"], 0)
        finally:
            svc.stop()


class SchemaSource(unittest.TestCase):
    def test_owner_tools_and_their_fields_exist_in_the_rust_schemas(self):
        src = (HERE.parent.parent / "src" / "tools.rs").read_text()
        for name in ("request_owner_help", "owner_help_status", "report_help_verification", "signin_verified"):
            i = src.index(f'"{name}",')
            block = src[i:i + 1400]
            allowed, required = s.TOOL_SCHEMAS[name]
            for k in allowed:
                self.assertIn(f'"{k}"', block, (name, k))
            self.assertTrue(required <= allowed)

    def test_the_bare_tool_names_are_offered_as_browser_prefixes(self):
        src = (HERE.parent.parent / "src" / "tools.rs").read_text()
        self.assertIn('format!("browser_{name}")', src)
        self.assertIn("HIDDEN_TOOLS", src)
        self.assertNotIn("run_steps", s.TOOL_SCHEMAS)


class ModelServer(unittest.TestCase):
    def setUp(self):
        self.model = s.Model(port=0)
        self.model.start()

    def tearDown(self):
        self.model.stop()

    def post(self, history):
        c = http.client.HTTPConnection("127.0.0.1", self.model.port, timeout=10)
        c.request("POST", "/v1/chat/completions", body=json.dumps({"messages": history}), headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read().decode()

    def test_a_reply_has_a_tool_call_usage_and_done_and_the_ledger_counts_it(self):
        st, out = self.post(msgs(("user", s.TASK)))
        self.assertEqual(st, 200)
        self.assertIn("browser_open", out)
        self.assertIn('"usage"', out)
        self.assertTrue(out.rstrip().endswith("[DONE]"))
        self.assertEqual(self.model.calls, 1)
        self.assertEqual([e["name"] for e in self.model.ledger], ["browser_open"])

    def test_the_wake_reply_is_held_until_release(self):
        self.model.hold_wake()
        history = msgs(("user", s.TASK), wake("main:1"))
        got = {}
        t = threading.Thread(target=lambda: got.update(r=self.post(history)))
        t.start()
        self.assertTrue(self.model.wait_held(5))
        self.assertNotIn("r", got)
        self.model.release_wake()
        t.join(5)
        self.assertIn("owner_help_status", got["r"][1])
        # The hold applies once.
        self.assertEqual(self.post(history)[0], 200)
        self.assertEqual(self.model.held_count, 1)

    def test_wake_messages_are_counted_per_request_id(self):
        for rid in ("a:1", "a:1", "b:2"):
            self.post(msgs(("user", s.TASK), wake(rid)))
        self.assertEqual(self.model.wakes, {"a:1": 2, "b:2": 1})

    def test_a_hold_that_is_never_released_times_out(self):
        m = s.Model(port=0, hold_timeout=0.2)
        m.start()
        try:
            m.hold_wake()
            c = http.client.HTTPConnection("127.0.0.1", m.port, timeout=5)
            c.request("POST", "/v1/chat/completions", body=json.dumps({"messages": msgs(("user", s.TASK), wake("a:1"))}))
            self.assertIn("owner_help_status", c.getresponse().read().decode())
        finally:
            m.stop()

    def test_ledger_problems_find_duplicates_and_gaps(self):
        names = ["browser_open", "browser_step", "browser_read", "request_owner_help", "owner_help_status", "browser_read", "report_help_verification",
                 "browser_step", "browser_read", "request_owner_help", "owner_help_status", "browser_read", "report_help_verification", "signin_verified", "browser_step", "browser_step", "browser_read"]
        ok = [{"name": n, "args": {}} for n in names]
        self.assertEqual(s.ledger_problems(ok), [])
        dup = ok[:2] + [ok[1]] + ok[2:]
        self.assertTrue(s.ledger_problems(dup))
        self.assertTrue(s.ledger_problems(ok[:-3]))

    def test_the_request_log_and_response_log_hold_no_value(self):
        self.post(msgs(("user", s.TASK)))
        self.assertFalse(leaked(json.dumps(self.model.ledger)))


if __name__ == "__main__":
    unittest.main()
