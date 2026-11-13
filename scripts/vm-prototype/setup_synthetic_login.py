"""A local synthetic login service and its scripted model, for the setup end-to-end run.

Both run in the harness process, so the synthetic password stays in harness memory. It reaches the
service by an in-process compare, Deck by the protected endpoint and the guest by the named reference
${FIXTURE_PASS}. The service never echoes it, in a page, a header, a URL, a log or its state.

The service has three owner-facing steps: sign in, phone approval (a match number) and a CAPTCHA-style
check (a check id). The owner action is a local method call. It is not CAPTCHA solving. A final report
can be submitted once. Counters prove each effect happened once.

The model is a script over the message history. It calls only tools that exist in src/tools.rs and
only bare step lines that bare documents. No VM and no browser start here.
"""
import hmac
import json
import re
import secrets
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

USER = "synthetic-user"
GUEST_URL = "http://10.0.2.100"  # how the guest reaches the harness fixture port
REF = "FIXTURE_PASS"
TASK_MARK = "synthetic login task"
TASK = "synthetic login task: sign in to the local service, finish the owner checks, then submit the report once"
DONE_TEXT = "synthetic login task done"

# ---------- the service ----------

PAGE = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{t}</title></head><body><main><h1>{t}</h1>{b}</main></body></html>'
FORM = '<form method="post" action="{a}"><button type="submit">{b}</button></form>'


class Service:
    def __init__(self, value, port=0, log=None):
        self._value = value.encode()
        self._lock = threading.Lock()
        self._log = log or (lambda line: None)
        self._sessions = set()
        self._match = self._check = None
        self._st = {"stage": "login", "phone": "pending", "captcha": "pending", "login_success": 0, "login_failure": 0,
                    "submit_attempts": 0, "submit_success": 0}
        svc = self

        class H(BaseHTTPRequestHandler):
            def log_request(self, code="-", size="-"):
                svc._log(f"{self.command} {self.path.split('?', 1)[0]} {code}")

            def log_message(self, *a):
                pass

            def send(self, code, body="", location=None, cookie=None):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                if location:
                    self.send_header("Location", location)
                if cookie:
                    self.send_header("Set-Cookie", f"fx_session={cookie}; Path=/; HttpOnly; SameSite=Strict")
                self.end_headers()
                self.wfile.write(data)

            def authed(self):
                jar = dict(p.strip().split("=", 1) for p in self.headers.get("Cookie", "").split(";") if "=" in p)
                with svc._lock:
                    return jar.get("fx_session") in svc._sessions

            def do_GET(self):
                svc._get(self, self.path.split("?", 1)[0])

            def do_POST(self):
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=10)
                svc._post(self, self.path.split("?", 1)[0], form)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_port

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # Owner actions. Local, and they change only this service.
    def owner_phone(self, match):
        with self._lock:
            if self._st["stage"] != "phone" or self._st["phone"] != "pending" or not hmac.compare_digest(str(match), self._match or ""):
                return False
            self._st["phone"] = "completed"
            return True

    def owner_captcha(self, check):
        with self._lock:
            if self._st["stage"] != "captcha" or self._st["captcha"] != "pending" or not hmac.compare_digest(str(check), self._check or ""):
                return False
            self._st["captcha"] = "completed"
            return True

    def phone_match(self):
        return self._match

    def captcha_check(self):
        return self._check

    def state(self):
        with self._lock:
            return {**self._st, "sessions": len(self._sessions)}

    # Routes.
    def _get(self, h, path):
        if path == "/health":
            return h.send(200, "ok")
        if path == "/":
            return h.send(200, PAGE.format(t="Deck synthetic service", b='<p>Local test service.</p><a href="/login">Sign in</a>'))
        if path == "/login":
            return h.send(200, PAGE.format(t="Sign in", b=f'<form method="post" action="/login"><label for="user">Username</label> <input id="user" name="user" autocomplete="off"> <label for="password">Password</label> <input id="password" name="password" type="password" autocomplete="off"> <button type="submit">Sign in</button></form>'))
        if not h.authed():
            return h.send(303, location="/login")
        st = self.state()
        if path == "/verify/phone":
            return h.send(200, PAGE.format(t="Phone approval required", b=f"<p>Match number: {self._match}</p><p>Phone approval: {st['phone']}</p>" + FORM.format(a="/verify/phone/continue", b="Continue")))
        if path == "/verify/captcha":
            if st["stage"] not in ("captcha", "report"):
                return h.send(303, location="/verify/phone")
            return h.send(200, PAGE.format(t="CAPTCHA-style check", b=f"<p>Check id: {self._check}</p><p>CAPTCHA-style check: {st['captcha']}</p>" + FORM.format(a="/verify/captcha/continue", b="Continue")))
        if path == "/report":
            if st["stage"] != "report":
                return h.send(303, location="/verify/captcha")
            return h.send(200, PAGE.format(t="Report", b=FORM.format(a="/report/submit", b="Submit report")))
        if path == "/report/done":
            return h.send(200, PAGE.format(t="Report", b="<p>Report submitted</p>" if st["submit_success"] else "<p>Report not submitted</p>"))
        return h.send(404, PAGE.format(t="Not found", b=""))

    def _post(self, h, path, form):
        if path == "/login":
            user = form.get("user", [""])[0].encode()
            password = form.get("password", [""])[0].encode()
            ok = hmac.compare_digest(user, USER.encode()) & hmac.compare_digest(password, self._value)
            with self._lock:
                if not ok:
                    self._st["login_failure"] += 1
                    return h.send(401, PAGE.format(t="Sign-in failed", b="<p>The username or password was not accepted.</p>"))
                self._st["login_success"] += 1
                token = secrets.token_hex(16)
                self._sessions.add(token)
                if self._match is None:
                    self._match = f"{secrets.randbelow(90) + 10}"
                    self._check = f"CHK-{secrets.randbelow(9000) + 1000}"
                    self._st["stage"] = "phone"
            return h.send(303, location="/verify/phone", cookie=token)
        if not h.authed():
            return h.send(303, location="/login")
        with self._lock:
            st = self._st
            if path == "/verify/phone/continue":
                if st["phone"] != "completed":
                    return h.send(409, PAGE.format(t="Phone approval required", b="<p>Phone approval is still pending.</p>"))
                if st["stage"] == "phone":
                    st["stage"] = "captcha"
                return h.send(303, location="/verify/captcha")
            if path == "/verify/captcha/continue":
                if st["captcha"] != "completed":
                    return h.send(409, PAGE.format(t="CAPTCHA-style check", b="<p>The check is still pending.</p>"))
                if st["stage"] == "captcha":
                    st["stage"] = "report"
                return h.send(303, location="/report")
            if path == "/report/submit":
                st["submit_attempts"] += 1
                if st["stage"] != "report" or st["submit_success"]:
                    return h.send(409, PAGE.format(t="Report", b="<p>The report was not accepted.</p>"))
                st["submit_success"] = 1
                return h.send(303, location="/report/done")
        return h.send(404, PAGE.format(t="Not found", b=""))


# ---------- bare step lines and tool schemas ----------

LOC = r'(?:ref=e\d+|css="[^"]*"|role=\w+(?: name[=~]"[^"]*")?|label="[^"]*"|text="[^"]*"|placeholder="[^"]*")'
STEP_FORMS = [
    re.compile(rf'^click {LOC}$'),
    re.compile(rf'^type {LOC} "[^"]*"$'),
    re.compile(rf'^expect {LOC} (?:visible|hidden|enabled|disabled|checked|unchecked|focused|count \d+|text="[^"]*"|value~"[^"]*")$'),
    re.compile(r'^expect (?:url|title)[=~]"[^"]*"$'),
    re.compile(r'^(?:open|goto) \S+$'),
    re.compile(r'^reload$'),
]


def invalid_steps(text):
    """Lines that are not in the bare step grammar (see the bare `step` tool). Screenshots are refused too."""
    return [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#") and not any(f.match(l.strip()) for f in STEP_FORMS)]


LOGIN_STEPS = f'type label="Username" "{USER}"\ntype label="Password" "${{{REF}}}"\nclick role=button name="Sign in"\nexpect url~"/verify/phone"'
REFRESH_STEPS = "reload"
CONTINUE_PHONE_STEPS = 'click role=button name="Continue"\nexpect url~"/verify/captcha"'
CONTINUE_CAPTCHA_STEPS = 'click role=button name="Continue"\nexpect url~"/report"'
SUBMIT_STEPS = 'click role=button name="Submit report"\nexpect url~"/report/done"'

# (allowed, required) argument names the model sees. Source: src/tools.rs and the bare tool list.
TOOL_SCHEMAS = {
    "browser_open": ({"url", "snapshot", "session", "timeout_ms", "width", "height"}, {"url"}),
    "browser_step": ({"steps", "session", "compact"}, {"steps"}),
    "browser_read": ({"max_chars", "offset", "ref", "session"}, set()),
    "request_owner_help": ({"kind", "instructions", "match_number", "ttl_secs"}, {"kind", "instructions"}),
    "owner_help_status": ({"request_id"}, {"request_id"}),
    "report_help_verification": ({"request_id", "result", "observation"}, {"request_id", "result", "observation"}),
    "signin_verified": (set(), set()),
}

EXPECTED_NAMES = [
    "browser_open", "browser_step", "browser_read", "request_owner_help",
    "owner_help_status", "browser_step", "browser_read", "report_help_verification",
    "browser_step", "browser_read", "request_owner_help",
    "owner_help_status", "browser_step", "browser_read", "report_help_verification",
    "signin_verified", "browser_step", "browser_step", "browser_read"]


# ---------- the scripted model ----------

Turn = namedtuple("Turn", "kind name args text")
WAKE = re.compile(r"owner_help_status for request (\S+?),")


def tool_turn(name, args):
    return Turn("tool", name, args, None)


def text_turn(text):
    return Turn("text", None, None, text)


def _content(m):
    c = m.get("content")
    return c if isinstance(c, str) else json.dumps(c)


def failed(out):
    try:
        v = json.loads(out)
    except ValueError:
        return out.lstrip().lower().startswith(("error", "failed"))
    return isinstance(v, dict) and (v.get("ok") is False or "error" in v)


def decide(history):
    """The next model turn, from the message list alone. It never knows the password."""
    calls = {}
    for m in history:
        for c in m.get("tool_calls") or []:
            try:
                args = json.loads(c["function"]["arguments"])
            except ValueError:
                args = {}
            calls[c["id"]] = (c["function"]["name"], args)
    last = history[-1]
    if last["role"] == "user":
        t = _content(last)
        if w := WAKE.search(t):
            return tool_turn("owner_help_status", {"request_id": w[1]})
        if "owner cancelled" in t:
            return text_turn("blocked: the owner cancelled the request. Nothing more was done.")
        if TASK_MARK in t and not calls:
            return tool_turn("browser_open", {"url": f"{GUEST_URL}/login"})
        return text_turn("blocked: no scripted step for this message")
    name, args = calls.get(last.get("tool_call_id"), ("", {}))
    out = _content(last)
    if failed(out):
        return text_turn(f"blocked: {name} failed")
    seq = list(calls.values())
    prev = seq[-2] if len(seq) > 1 else ("", {})
    verified = sum(1 for n, a in seq if n == "report_help_verification" and a.get("result") == "verified")
    if name == "browser_open":
        return tool_turn("browser_step", {"steps": LOGIN_STEPS})
    if name == "browser_step":
        if args.get("steps") == REFRESH_STEPS:
            return tool_turn("browser_read", {})
        s = args.get("steps")
        if s == LOGIN_STEPS or s == CONTINUE_PHONE_STEPS:
            return tool_turn("browser_read", {})
        if s == CONTINUE_CAPTCHA_STEPS:
            return tool_turn("browser_step", {"steps": SUBMIT_STEPS})
        if s == SUBMIT_STEPS:
            return tool_turn("browser_read", {})
    if name == "browser_read":
        if prev[0] == "browser_step" and prev[1].get("steps") == LOGIN_STEPS:
            m = re.search(r"Match number: (\d{1,8})", out)
            if not m:
                return text_turn("blocked: the page showed no match number")
            return tool_turn("request_owner_help", {"kind": "phone", "instructions": "Approve the synthetic sign-in on the local service's phone step.", "match_number": m[1]})
        if prev[0] == "browser_step" and prev[1].get("steps") == CONTINUE_PHONE_STEPS:
            m = re.search(r"Check id: (CHK-\d{4})", out)
            if not m:
                return text_turn("blocked: the page showed no check id")
            return tool_turn("request_owner_help", {"kind": "captcha", "instructions": f"Complete the synthetic CAPTCHA-style check {m[1]} on the local service."})
        if prev[0] == "browser_step" and prev[1].get("steps") == REFRESH_STEPS:
            kind = next((a.get("kind") for n, a in reversed(seq) if n == "request_owner_help"), None)
            done = {"phone": "Phone approval: completed", "captcha": "CAPTCHA-style check: completed"}.get(kind)
            ok = bool(done) and done in out
            return tool_turn("report_help_verification", {"request_id": next((a.get("request_id", "") for n, a in reversed(seq) if n == "owner_help_status"), ""), "result": "verified" if ok else "not_verified",
                                                           "observation": f"The service page shows the {kind} step as {'completed' if ok else 'still pending'}."})
        if prev[0] == "browser_step" and prev[1].get("steps") == SUBMIT_STEPS:
            return text_turn(DONE_TEXT) if "Report submitted" in out else text_turn("blocked: the report page did not confirm")
    if name == "owner_help_status":
        try:
            state = json.loads(out).get("state")
        except ValueError:
            state = None
        return tool_turn("browser_step", {"steps": REFRESH_STEPS}) if state == "answered" else text_turn("blocked: the owner step is not answered")
    if name == "report_help_verification":
        return {1: tool_turn("browser_step", {"steps": CONTINUE_PHONE_STEPS}), 2: tool_turn("signin_verified", {})}.get(verified, text_turn("blocked: unexpected verification state"))
    if name == "signin_verified":
        return tool_turn("browser_step", {"steps": CONTINUE_CAPTCHA_STEPS})
    return text_turn(f"blocked: no scripted step after {name or 'this reply'}")


def ledger_problems(ledger):
    """Why the tool calls the model made are not the expected single run. Empty when they are."""
    names = [e["name"] for e in ledger]
    if names == EXPECTED_NAMES:
        return []
    out = [f"expected {len(EXPECTED_NAMES)} tool calls, saw {len(names)}"]
    i = next((i for i, (a, b) in enumerate(zip(names, EXPECTED_NAMES)) if a != b), min(len(names), len(EXPECTED_NAMES)))
    out.append(f"first difference at call {i}: expected {EXPECTED_NAMES[i] if i < len(EXPECTED_NAMES) else 'none'}, saw {names[i] if i < len(names) else 'none'}")
    return out


USAGE = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}


class Model:
    """An OpenAI-style chat server that plays `decide`. It can hold the reply to the owner-answer wake."""

    def __init__(self, port=0, request_log=None, response_log=None, hold_timeout=60.0):
        self.calls, self.ledger, self.texts, self.held_count = 0, [], [], 0
        self.wakes = {}  # request id -> number of wake messages the model received for it
        self.hold_timeout = hold_timeout
        self._lock = threading.Lock()
        self._armed = False
        self._held, self._release = threading.Event(), threading.Event()
        m = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                history = json.loads(body)["messages"]
                if request_log:
                    with m._lock, open(request_log, "ab") as f:
                        f.write(body + b"\n")
                turn = decide(history)
                last = history[-1]
                wake = WAKE.search(_content(last)) if last["role"] == "user" else None
                if wake:
                    with m._lock:
                        m.wakes[wake[1]] = m.wakes.get(wake[1], 0) + 1
                    m._hold()
                with m._lock:
                    m.calls += 1
                    n = m.calls
                    if turn.kind == "tool":
                        m.ledger.append({"seq": n, "name": turn.name, "args": turn.args})
                    else:
                        m.texts.append(turn.text)
                delta = ({"tool_calls": [{"index": 0, "id": f"k{n}", "function": {"name": turn.name, "arguments": json.dumps(turn.args)}}]}, "tool_calls") if turn.kind == "tool" else ({"content": turn.text}, "stop")
                chunk = {"choices": [{"delta": delta[0], "finish_reason": delta[1]}]}
                if response_log:
                    with m._lock, open(response_log, "ab") as f:
                        f.write(json.dumps(chunk).encode() + b"\n")
                data = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(USAGE)}\n\ndata: [DONE]\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_port

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self._release.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def hold_wake(self):
        """Hold the next reply to an owner-answer wake until release_wake. One use."""
        self._held.clear()
        self._release.clear()
        self._armed = True

    def _hold(self):
        with self._lock:
            if not self._armed:
                return
            self._armed = False
            self.held_count += 1
        self._held.set()
        self._release.wait(self.hold_timeout)

    def wait_held(self, timeout):
        return self._held.wait(timeout)

    def release_wake(self):
        self._release.set()
