#!/usr/bin/env python3
"""Controller for one unmocked setup run: Deck web UI -> review and approve -> private VM -> protected
form -> synthetic phone and CAPTCHA owner answers -> first task. See docs/setup-ui-vm-plan.md.

`prepare` checks the arguments and prints the plan and the start command. It starts nothing.
`serve` starts the real Deck, the scripted model, the local synthetic service, a streaming loopback proxy
and a control API. It starts no VM: Deck starts the VM only after the UI approves the setup.

A browser worker (bare) drives the page at the proxy URL. This controller never approves, never delivers
a secret and never answers an owner wait. Its Deck client can only read (GET). The owner actions it
offers touch only the local fixture service. The protected value stays in this process. One same-origin
route on the proxy origin hands it to page JavaScript once, for the pending credential request.

All pure logic is reused from setup_e2e.py and setup_synthetic_login.py. Only synthetic data.
"""
import argparse
import hmac
import http.client
import json
import re
import secrets
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import setup_e2e as se
import setup_synthetic_login as sl
import vmproto as v

HERE = Path(__file__).resolve().parent
OWNER = "e2e-owner"  # the user the proxy asserts to Deck
JOB = "Sign in to the local synthetic service"
LABEL = se.CREDENTIAL
UI_GATES = ["ui_review_screen_seen", "ui_secret_input_check", "ui_privacy_viewer", "ui_screenshot_privacy"]
KINDS = {"phone": "PhoneApproval", "captcha": "Captcha"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "remote-user", "host"}

# Runs in the page. The value never leaves the page: it is set on the real password input and only a
# hash prefix, a length and a boolean come back. Replace __REQUEST_ID__ through fill_js().
FILL_JS = """(async()=>{const el=document.querySelector('input[name="deck-protected-value"]');
if(!el)return{filled:false,why:"no protected input on the page"};
const r=await fetch("/__e2e/secret?request_id=__REQUEST_ID__&label=FIXTURE_PASS",{credentials:"omit",cache:"no-store"});
if(!r.ok)return{filled:false,why:"refused "+r.status};
const v=(await r.json()).value;
Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(el,v);
el.dispatchEvent(new Event("input",{bubbles:true}));
const h=[...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(v)))].map(b=>b.toString(16).padStart(2,"0")).join("");
return{filled:el.value===v,length:el.value.length,sha256:h.slice(0,12)}})()"""


def fill_js(request_id):
    if not re.fullmatch(r"[0-9A-Za-z_:-]{1,128}", request_id):
        raise ValueError("bad request id")
    return FILL_JS.replace("__REQUEST_ID__", request_id)


class Refused(Exception):
    def __init__(self, code, why=""):
        super().__init__(why)
        self.code, self.why = code, why


# ---------- the one-use value ----------

class SecretBox:
    """Holds the value in memory. `take` releases it once per pending credential request, for the expected label only."""

    def __init__(self, value, label, pending):
        self.value, self.label, self.pending = value, label, pending
        self.used = set()
        self._lock = threading.Lock()

    def take(self, request_id, label):
        with self._lock:
            w = self.pending()
            if not w or w["request_id"] != request_id or w["label"] != self.label or label != self.label:
                raise Refused(409, "no such pending request")
            if request_id in self.used:
                raise Refused(410, "already used")
            self.used.add(request_id)
            return self.value


# ---------- the streaming proxy ----------

class Proxy:
    """Loopback reverse proxy to the owned Deck. It sets Remote-User and streams bodies as they arrive."""

    def __init__(self, upstream_port, secret_fn):
        self.upstream, self.log, self._lock, self._active = upstream_port, [], threading.Lock(), set()
        proxy = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def note(self, route, code):
                with proxy._lock:
                    if len(proxy.log) < 20000:
                        proxy.log.append([self.command, route, code])

            def plain(self, route, code, body=b"", ctype="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                self.note(route, code)

            def serve(self):
                route, _, query = self.path.partition("?")
                if self.headers.get("Host") not in (f"127.0.0.1:{proxy.port}", f"localhost:{proxy.port}"):
                    return self.plain(route, 421)
                if route.startswith("/__e2e/"):
                    return self.e2e(route, query)
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1 << 20:
                    return self.plain(route, 413)
                body = self.rfile.read(n) if n else None
                headers = {k: val for k, val in self.headers.items() if k.lower() not in HOP and k.lower() != "content-length"}
                headers["Remote-User"] = OWNER
                up = http.client.HTTPConnection("127.0.0.1", proxy.upstream, timeout=30)
                try:
                    up.connect()
                    sock = up.sock  # http.client drops its own reference for a close-delimited body
                    up.request(self.command, self.path, body, headers)
                    resp = up.getresponse()
                except (OSError, http.client.HTTPException):
                    up.close()
                    return self.plain(route, 502)
                with proxy._lock:
                    proxy._active.add(sock)
                done = threading.Event()
                try:
                    sock.settimeout(None)
                    self.send_response(resp.status)
                    for k, val in resp.getheaders():
                        if k.lower() not in HOP:
                            self.send_header(k, val)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.note(route, resp.status)
                    if resp.getheader("Content-Length") is None:
                        threading.Thread(target=self.watch_client, args=(sock, done), daemon=True).start()
                    if self.command != "HEAD":
                        while chunk := resp.read1(8192):
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    pass  # the client left or Deck stopped: end this stream and nothing else
                finally:
                    done.set()
                    with proxy._lock:
                        proxy._active.discard(sock)
                    up.close()
                self.close_connection = True

            def watch_client(self, sock, done):
                """An idle stream sees a client disconnect only at the next write. Close the upstream at once instead."""
                while not done.is_set():
                    if select.select([self.connection], [], [], 0.3)[0]:
                        try:
                            gone = self.connection.recv(1, socket.MSG_PEEK) == b""
                        except OSError:
                            gone = True
                        if gone:
                            try:
                                sock.shutdown(socket.SHUT_RDWR)
                            except OSError:
                                pass
                            return
                        done.wait(0.3)

            def e2e(self, route, query):
                if route != "/__e2e/secret" or self.command != "GET":
                    return self.plain(route, 404)
                if self.headers.get("Sec-Fetch-Site") != "same-origin":
                    return self.plain(route, 403)
                q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                try:
                    value = secret_fn(q.get("request_id", ""), q.get("label", ""))
                except Refused as r:
                    return self.plain(route, r.code)
                self.plain(route, 200, json.dumps({"value": value}).encode(), "application/json")

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_port

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        with self._lock:
            active = list(self._active)
        for sock in active:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------- the control API ----------

ROUTES = {("GET", "/plan"): "plan", ("GET", "/state"): "state_view", ("POST", "/owner/phone"): "owner_phone", ("POST", "/owner/captcha"): "owner_captcha",
          ("POST", "/model/hold"): "hold", ("POST", "/model/release"): "release", ("POST", "/deck/restart"): "restart_route", ("POST", "/gate"): "gate_route",
          ("POST", "/finish"): "finish_route", ("POST", "/stop"): "stop_route"}


class Control:
    def __init__(self, ctl):
        self.ctl = ctl
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def serve(self):
                path = self.path.split("?", 1)[0]
                if self.headers.get("Host") not in (f"127.0.0.1:{me.port}", f"localhost:{me.port}"):
                    return self.reply(421, {"error": "host"})
                if not hmac.compare_digest(self.headers.get("X-Control-Token", ""), ctl.token):
                    return self.reply(401, {"error": "token"})
                name = ROUTES.get((self.command, path))
                if not name:
                    return self.reply(404, {"error": "no such route"})
                body = {}
                if self.command == "POST":
                    n = min(int(self.headers.get("Content-Length") or 0), 4096)
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}")
                    except ValueError:
                        return self.reply(400, {"error": "json"})
                    if not isinstance(body, dict):
                        return self.reply(400, {"error": "object"})
                try:
                    self.reply(*getattr(ctl, name)(body))
                except Refused as r:
                    self.reply(r.code, {"error": r.why})

            do_GET = do_POST = serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_port

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ReadOnly(se.Client):
    """Deck as the controller sees it. A write is a programming error, not a request."""

    def raw(self, method, path, body=None):
        if method != "GET":
            raise PermissionError(f"the controller never writes to Deck: {method} {path}")
        return super().raw(method, path, body)

    def get(self, path):
        code, raw = self.raw("GET", path)
        try:
            return json.loads(raw) if code == 200 else None
        except ValueError:
            return None


def stage_of(row, status):
    """A coarse stage from the Deck row. Names only, never a value."""
    if not row:
        return "no_setup"
    phase = row["phase"]
    if phase in ("Investigate", "Exhausted"):
        return "unknown_outcome"
    if phase == "Draft":
        return "draft"
    sid = row["setup"]["id"]
    w, kind = se.wait_of(row), se.wait_kind(se.wait_of(row))
    if kind == "Credential":
        return "credential_wait"
    for short, wk in KINDS.items():
        if se.login_wait(row, wk, f"setup-{sid}"):
            return f"{short}_{'answered' if w.get('answered') else 'wait'}"
    task = next((t for t in (status or {}).get("tasks", []) if t["id"] == f"setup-{sid}"), None)
    if task and task["state"] == "done":
        return "done"
    if task and task["state"] == "failed":
        return "task_failed"
    return "provisioning" if phase == "Provisioning" else "running"


class Controller:
    def __init__(self, stack, value, manifest, guest=None):
        self.stack, self.value, self.m, self.guest = stack, value, manifest, guest
        self.m.needles = se.variants(value)
        self.token = secrets.token_urlsafe(16)
        self.deadline = float("inf")
        self.sid = None
        self.seen = {}  # kind -> request id of the owner action
        self.restarting = self.started = self.finished = False
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._done = None
        self.watch = None

    # --- lifecycle ---
    def start(self, deck_port):
        self.deck = ReadOnly(self.stack.client.base)
        self.box = SecretBox(self.value, LABEL, self.pending_credential)
        self.proxy, self.control = Proxy(deck_port, self.box.take), Control(self)
        self.watch = se.ArgvWatcher(se.variants(self.value))
        self.watch.start()
        self.proxy.start()
        self.control.start()
        self.started = True

    def close_listeners(self):
        for srv in (getattr(self, "proxy", None), getattr(self, "control", None)):
            if srv:
                try:
                    srv.close()
                except OSError:
                    pass

    def lost(self):
        """Why this run must stop now, or None. An unknown outcome is never retried."""
        if time.monotonic() > self.deadline:
            return "deadline reached"
        if self.started and not self.finished and not self.restarting and not self.stack.alive("server"):
            return "Deck process lost"
        row = self.current()
        if row and row["phase"] in ("Investigate", "Exhausted"):
            return f"unknown outcome: setup phase {row['phase']}"
        return None

    # --- reading Deck ---
    def rows(self):
        return self.deck.get("/api/setups") or []

    def current(self):
        rows = self.rows()
        if len(rows) == 1:
            self.sid = rows[0]["setup"]["id"]
            return rows[0]
        return None

    def pending_credential(self):
        row = self.current()
        w = se.wait_of(row)
        if row and se.wait_kind(w) == "Credential":
            return {"request_id": w["request_id"], "label": w["kind"]["Credential"]["label"]}
        return None

    def status(self, sid):
        return self.deck.get(f"/api/bots/{sid}")

    def counters(self, row, status):
        sid, svc, model = row["setup"]["id"], self.stack.service.state(), self.stack.model
        msgs = self.deck.get(f"/api/chats/{row['chat_id']}/messages") or []
        return {"tasks": len((status or {}).get("tasks", [])), "messages": len(msgs), "model_calls": model.calls,
                **{k: svc[k] for k in ("login_success", "login_failure", "submit_attempts", "submit_success")}}

    # --- routes (each returns (code, object)) ---
    def plan(self, _=None):
        return 200, {"job": JOB, "task": sl.TASK, "credential_label": LABEL, "task_id_pattern": "setup-<setup_id>", "done_text": sl.DONE_TEXT,
                     "ui_url": f"http://127.0.0.1:{self.proxy.port}/", "fill_js_template": FILL_JS, "worker_gates": UI_GATES}

    def state_view(self, _=None):
        rows = self.rows()
        row = self.current()
        sid = row["setup"]["id"] if row else None
        status = self.status(sid) if sid else None
        w = se.wait_of(row)
        out = {"stage": "ambiguous_setups" if len(rows) > 1 else stage_of(row, status), "setup_id": sid, "task_id": f"setup-{sid}" if sid else None,
               "phase": row["phase"] if row else None, "runtime_ready": bool(row and row["runtime_ready"]), "deck_starts": self.stack.starts,
               "wait": {"kind": se.wait_kind(w), "request_id": w["request_id"], "answered": bool(w.get("answered"))} if w else None,
               "model": {"held": self.stack.model._held.is_set(), "armed": self.stack.model._armed, "held_count": self.stack.model.held_count},
               "service": {k: self.stack.service.state()[k] for k in ("stage", "phone", "captcha")},
               "gates": {n: g["status"] for n, g in self.m.data["gates"].items()}}
        if row:
            out["counts"] = {**self.counters(row, status), "tool_calls": len(self.stack.model.ledger), "wakes": sum(self.stack.model.wakes.values())}
        else:
            out["counts"] = {"model_calls": self.stack.model.calls, **{k: self.stack.service.state()[k] for k in ("login_success", "login_failure", "submit_attempts", "submit_success")}}
        return 200, out

    def owner(self, kind, body):
        if set(body) != {"request_id"} or not isinstance(body["request_id"], str):
            raise Refused(400, "body must be exactly {request_id}")
        row = self.current()
        w = se.login_wait(row, KINDS[kind], f"setup-{self.sid}") if row else None
        if not w or w["request_id"] != body["request_id"] or w.get("answered") or kind in self.seen and self.seen[kind] == body["request_id"]:
            raise Refused(409, "not the current unanswered wait of this kind")
        svc = self.stack.service
        act = svc.owner_phone if kind == "phone" else svc.owner_captcha
        wrong = act("wrong") is False
        ident = w.get("match_number") if kind == "phone" else (re.search(r"CHK-\d{4}", w.get("instructions") or "") or [""])[0]
        done = bool(act(ident)) and svc.state()[kind] == "completed"
        self.m.gate(f"{kind}_service_refuses_wrong_owner_id", se.PASS if wrong else se.FAIL)
        self.m.gate(f"{kind}_owner_action_completed_in_service", se.PASS if done else se.FAIL)
        self.m.gate(f"{kind}_wait_raised", se.PASS if done and ident else se.FAIL, "the wait shows the service's own id")
        if not done:
            raise Refused(409, "the service did not accept the id Deck stored")
        self.seen[kind] = body["request_id"]
        return 200, {"ok": True}

    def owner_phone(self, body):
        return self.owner("phone", body)

    def owner_captcha(self, body):
        return self.owner("captcha", body)

    def hold(self, _):
        if not self.state_view()[1]["stage"] in ("phone_wait", "captcha_wait"):
            raise Refused(409, "no open phone or CAPTCHA wait")
        self.stack.model.hold_wake()
        return 200, {"armed": True}

    def release(self, _):
        model = self.stack.model
        if not model.wait_held(0):
            raise Refused(409, "no held wake")
        row = self.current()
        w = se.wait_of(row)
        kind = next((k for k, wk in KINDS.items() if se.wait_kind(w) == wk), "unknown")
        pending = bool(w) and w.get("answered") is True and self.stack.service.state().get(kind) == "completed"
        self.m.gate(f"{kind}_resume_reached_the_model", se.PASS)
        self.m.gate(f"{kind}_answer_alone_leaves_verification_pending", se.PASS if pending else se.FAIL,
                    f"service step {self.stack.service.state().get(kind)}; Deck answered={bool(w) and w.get('answered')}. The owner answer alone is not verification")
        model.release_wake()
        return 200, {"released": True}

    def restart_route(self, _):
        self.restart()
        return 200, {"ok": True, "deck_starts": self.stack.starts}

    def restart(self):
        """Stop and start only the owned Deck. Compare ownership and work before and after."""
        row = self.current()
        a = self.stack.a
        before = counts0 = stage = None
        if row:
            status = self.status(row["setup"]["id"])
            stage = stage_of(row, status)
            before, counts0 = self.ownership(row, status), self.counters(row, status)
        self.restarting = True
        try:
            self.stack.stop("server")
            try:
                self.stack.start_server()
                if row and row["runtime_ready"]:
                    self.stack.client.until("runtime ready after restart", lambda: (r := self.current()) and r["runtime_ready"], a.ready_timeout)
            except SystemExit as x:
                raise Refused(500, str(x)[:200]) from None
            time.sleep(a.settle)
        finally:
            self.restarting = False
        if not row:
            return
        row = self.current()
        status = self.status(row["setup"]["id"])
        after, counts1 = self.ownership(row, status), self.counters(row, status)
        if stage in ("phone_wait", "captcha_wait"):
            self.m.gate("restart_preserves_waiting_ownership", se.PASS if not se.ownership_diff(before, after) else se.FAIL, f"changed: {se.ownership_diff(before, after)}")
            self.m.gate("restart_adds_no_work", se.PASS if not se.counts_changed(counts0, counts1) else se.FAIL, f"changed: {se.counts_changed(counts0, counts1)}")
        elif stage == "done":
            self.m.gate("restart_after_completion_no_duplicate", se.PASS if not se.counts_changed(counts0, counts1) and counts1["submit_success"] == 1 else se.FAIL, f"changed: {se.counts_changed(counts0, counts1)}")

    def ownership(self, row, status):
        sid = row["setup"]["id"]
        try:
            executor = json.loads((self.stack.rt / sid / "executor.json").read_text())
        except (OSError, ValueError):
            executor = {}
        return se.ownership(row, status or {}, executor, f"setup-{sid}")

    def gate_route(self, body):
        d = body.get("detail", "")
        if set(body) - {"name", "status", "detail"} or {"name", "status"} - set(body) or body["name"] not in UI_GATES or body["status"] not in (se.PASS, se.FAIL) \
                or not isinstance(d, str) or len(d) > 300:
            raise Refused(400, "fixed fields: name (one of the UI checks), status pass|fail, detail up to 300 characters")
        self.m.gate(body["name"], body["status"], d or None)
        return 200, {"ok": True}

    def finish_route(self, _):
        return 200, self.finish()

    def stop_route(self, _):
        return 200, self.stop()

    # --- the end ---
    def finish(self):
        """Server-side checks from what Deck, the service and the model saw. Idempotent."""
        with self._lock:
            if self._done is None:
                self.finished = True
                self._done = {}
                try:
                    self._evaluate()
                except BaseException as e:  # keep the evidence, then report
                    self.m.gate("finish_completed", se.FAIL, f"{type(e).__name__}: {self.m.scrub(str(e))[:200]}")
                self._done = {"ok": self.m.ok(), "gates": {n: g["status"] for n, g in self.m.data["gates"].items()}}
            return self._done

    def _evaluate(self):
        stack, m, svc, model = self.stack, self.m, self.stack.service, self.stack.model
        g = lambda name, ok, detail=None: m.gate(name, se.PASS if ok else se.FAIL, detail)  # noqa: E731
        argv = self.watch.finish()
        g("secret_never_in_argv", argv["argv_hits"] == 0, f"{argv['samples']} samples")
        row = self.current()
        sid = self.sid
        status = self.status(sid) if sid else None
        task = next((t for t in (status or {}).get("tasks", []) if t["id"] == f"setup-{sid}"), None)
        st = svc.state()
        if row:
            g("setup_ready_through_api", row["phase"] == "Ready" and row["runtime_ready"], f"phase {row['phase']}, runtime ready {row['runtime_ready']}")
            msgs = self.deck.get(f"/api/chats/{row['chat_id']}/messages") or []
            g("task_final_report", sl.DONE_TEXT in json.dumps(msgs), "the task's closing text is in the chat")
            resolved = {r.get("request_id"): r.get("outcome") for r in row["setup"].get("resolved") or []}
            cred = next(iter(self.box.used), None)
            w = se.wait_of(row)
            g("wait_cleared_only_after_delivery", bool(cred) and not (w and w["request_id"] == cred), "the credential wait cleared after the one release of the value")
        else:
            g("setup_ready_through_api", False, "no single setup")
            g("task_final_report", False)
            g("wait_cleared_only_after_delivery", False)
            resolved = {}
        g("bot_data_dir_created_by_server", bool(sid) and (stack.out / "data" / "bots" / sid).is_dir())
        refs = [e for e in model.ledger if e["name"] == "browser_step" and sl.REF in json.dumps(e["args"])]
        g("service_login_succeeded_with_reference", st["login_success"] == 1 and st["login_failure"] == 0 and bool(refs), f"service logins {st['login_success']} ok / {st['login_failure']} failed; reference steps {len(refs)}")
        g("special_characters_authenticated_in_service", st["login_success"] == 1)
        g("model_uses_the_reference_not_the_value", bool(refs))
        g("model_turns_hold_no_value", not any(n in json.dumps(model.ledger + model.texts).encode() for n in m.needles))
        g("first_task_done", bool(task) and task["state"] == "done", task and (task.get("detail") or task["state"]))
        g("single_success_action", st["submit_success"] == 1 and st["submit_attempts"] == 1 and st["phone"] == st["captcha"] == "completed",
          f"submit success {st['submit_success']} of {st['submit_attempts']} attempt(s)")
        problems = sl.ledger_problems(model.ledger)
        g("scripted_calls_exactly_once", not problems, "; ".join(problems) or f"{len(model.ledger)} tool calls, each once")
        for kind in KINDS:
            rid = self.seen.get(kind)
            g(f"{kind}_single_wake", bool(rid) and model.wakes.get(rid, 0) == 1, f"wake messages for the request: {model.wakes.get(rid, 0) if rid else 'no owner action'}")
            g(f"{kind}_verified_only_after_service_evidence", bool(rid) and resolved.get(rid) == "verified" and not problems and st[kind] == "completed",
              "Deck resolved the request as verified, the scripted calls ran in order and the service shows the step completed")
        log = [tuple(e) for e in self.proxy.log]
        ok204 = lambda suffix: sum(1 for meth, route, code in log if meth == "POST" and route.startswith("/api/setups/") and route.endswith(suffix) and code == 204)  # noqa: E731
        g("ui_approval_through_proxy", ok204("/approve") >= 1, f"{ok204('/approve')} approve POST(s) came through the proxy with status 204")
        g("ui_secret_form_posted_204", ok204("/secret") >= 1 and bool(self.box.used), f"{ok204('/secret')} protected POST(s) came through the proxy with status 204; the value was released {len(self.box.used)} time(s)")
        g("ui_phone_answer_posted_204", ok204("/challenge/respond") >= 1, f"{ok204('/challenge/respond')} challenge answer(s) came through the proxy")
        g("ui_captcha_answer_posted_204", ok204("/challenge/respond") >= 2, "the second challenge answer came through the proxy")
        g("secret_absent_from_proxy_log", not any(n in json.dumps(self.proxy.log).encode() for n in m.needles), f"{len(log)} proxied calls, routes and status codes only")
        try:
            guest = self.guest or se.Guest(stack.rt / sid)
            r = guest.check_secret_file(se.derived_name(LABEL), self.value)
            g("guest_file_protected", bool(r.get("value_matches")) and r.get("mode") == "0o600", {k: r.get(k) for k in ("exists", "mode", "owner", "name_present", "value_matches")})
            g("special_characters_preserved_in_guest_file", bool(r.get("value_matches")))
        except Exception as e:  # no guest to ask is a failure, not a skip
            g("guest_file_protected", False, f"{type(e).__name__}")
            g("special_characters_preserved_in_guest_file", False)
        stack.stop("server")
        priv = stack.out / "private"
        leak = se.scan_tree([stack.out / "data", priv, stack.rt], m.needles, skip=("disk.qcow2", "vm.state", "*.sock"))
        m.data["evidence"]["leak_scan"] = leak
        g("secret_absent_from_host_evidence", leak["files_with_match"] == 0, f"{leak['files_scanned']} files scanned; matches: {leak['matches']}")
        logs = sorted(priv.glob("server-*.log"))
        g("logs_accounted_for_each_start", len(logs) == stack.starts, f"{len(logs)} server log(s) for {stack.starts} start(s)")

    def required(self):
        rounds = [f"{k}_{n}" for k in KINDS for n in ("wait_raised", "service_refuses_wrong_owner_id", "owner_action_completed_in_service", "resume_reached_the_model",
                                                      "answer_alone_leaves_verification_pending", "single_wake", "verified_only_after_service_evidence")]
        return (["base_frozen_and_verified", "setup_ready_through_api", "bot_data_dir_created_by_server", "wait_cleared_only_after_delivery", "guest_file_protected",
                 "special_characters_preserved_in_guest_file", "special_characters_authenticated_in_service", "service_login_succeeded_with_reference",
                 "restart_preserves_waiting_ownership", "restart_adds_no_work", "first_task_done", "single_success_action", "scripted_calls_exactly_once",
                 "task_final_report", "restart_after_completion_no_duplicate", "model_uses_the_reference_not_the_value", "model_turns_hold_no_value",
                 "secret_never_in_argv", "secret_absent_from_host_evidence", "secret_absent_from_proxy_log", "logs_accounted_for_each_start",
                 "ui_approval_through_proxy", "ui_secret_form_posted_204", "ui_phone_answer_posted_204", "ui_captcha_answer_posted_204",
                 "base_unchanged", "no_owned_process_left"] + rounds + UI_GATES)

    def stop(self):
        """Finish if needed, then stop only what this run started. Idempotent. Returns the survivors."""
        with self._lock:
            if self.stopped.is_set():
                return self._stopped
        if not self.finished:
            self.finish()
        stack, m = self.stack, self.m
        sizes = {d.name: v.allocated_bytes(d / "disk.qcow2") for d in stack.vm_dirs() if (d / "disk.qcow2").exists()}
        left = stack.cleanup()
        if not getattr(stack.a, "keep_disk", False):
            for d in stack.vm_dirs():
                for f in (d / "disk.qcow2", d / "state" / "vm.state"):
                    f.unlink(missing_ok=True)
        m.data["evidence"]["overlay_allocated_bytes"] = sizes
        try:
            m.gate("base_unchanged", se.PASS if se.verify_base(stack.a.base) else se.FAIL)
        except (OSError, ValueError, KeyError) as e:
            m.gate("base_unchanged", se.FAIL, type(e).__name__)
        m.data["evidence"]["cleanup"] = {"registered": {str(p): b for p, b in stack.owned.items()}, "survivors": left,
                                         "method": "registered subprocess and bound VM identities plus descendants"}
        m.gate("no_owned_process_left", se.PASS if not left else se.FAIL, f"{len(left)} registered identities still running; survivors: {left}")
        m.require(self.required())
        m.write(stack.out / "manifest.json")
        self.close_listeners()
        with self._lock:
            self._stopped = {"ok": m.ok(), "survivors": left, "manifest": str(stack.out / "manifest.json")}
            self.stopped.set()
            return self._stopped


# ---------- entry ----------

def free_port():
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["prepare", "serve"])
    ap.add_argument("--base", required=True, help="frozen base .qcow2 with its sidecar")
    ap.add_argument("--deck", default=str(HERE.parent.parent / "target/release/deck"))
    ap.add_argument("--out", default=str(Path.home() / ".cache/deck" / time.strftime("ui-%m%d-%H%M%S")))
    for name in ("server", "fixture", "model"):
        ap.add_argument(f"--{name}-port", type=int, default=0, help="0 picks a free loopback port")
    ap.add_argument("--idle-secs", type=int, default=40)
    ap.add_argument("--disk-gib", type=int, default=16)
    ap.add_argument("--ready-timeout", type=int, default=300)
    ap.add_argument("--settle", type=int, default=8)
    ap.add_argument("--hold-timeout", type=int, default=600, help="longest the model holds a wake, in seconds")
    ap.add_argument("--max-secs", type=int, default=3600, help="hard deadline for the whole run")
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    ap.add_argument("--wait", action="store_true", help="serve until the first task is done, then finish and stop. Without it, serve until /stop")
    ap.add_argument("--keep-disk", action="store_true")
    a = ap.parse_args(argv)
    used = set()
    for name in ("server", "fixture", "model"):
        p = getattr(a, f"{name}_port")
        while not p or p in used:
            p = free_port()
        used.add(p)
        setattr(a, f"{name}_port", p)
    a.task_timeout = 240
    a.credential_profile = "special"
    a.diagnostic_precreate_bot_dir = False
    return a


def make_stack(a):
    return se.Stack(a)


def start_command(a):
    return ["python3", str(Path(__file__).resolve()), "serve", "--base", a.base, "--deck", a.deck, "--out", a.out, "--wait"]


def main(argv=None):
    a = parse(argv)
    if a.mode == "prepare":
        print(json.dumps({"start_command": start_command(a), "out": a.out, "starts": "nothing", "ports": {"deck": a.server_port, "fixture": a.fixture_port, "model": a.model_port}}))
        return 0
    lock = se.take_lock()
    why = se.pressure(Path("/proc/meminfo").read_text(), Path("/proc/loadavg").read_text(), a.min_avail_mib, a.max_load)
    if why:
        print(f"STOPPED: host pressure: {why}. Nothing was started.", file=sys.stderr)
        return 3
    out = Path(a.out)
    if not se.socket_path_ok(out):
        print(f"STOPPED: --out is too long for QEMU unix sockets: {out}", file=sys.stderr)
        return 2
    out.mkdir(mode=0o700, parents=True)
    (out / "private").mkdir(mode=0o700)
    m = se.Manifest("ui-synthetic-login")
    value = se.synthetic_value("special")
    stack = make_stack(a)
    ctl = Controller(stack, value, m)
    base = se.base_identity(a.base)
    head = subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    m.data["identity"] = {"head": head, "base": {k: base[k] for k in ("id", "virtual_size", "mode")}, "deck_sha256": v.sha256_file(Path(a.deck)),
                          "fidelity": "real Deck web UI driven by bare, real server, real private VM, scripted model, local synthetic service"}
    m.gate("base_frozen_and_verified", se.PASS if base["mode"] == "0o444" and se.verify_base(a.base) else se.FAIL, f"mode {base['mode']}")
    quit = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: quit.set())
    try:
        stack.write_config()
        stack.start_helpers(value)
        stack.model.hold_timeout = a.hold_timeout
        stack.start_server()
        ctl.start(a.server_port)
        ctl.deadline = time.monotonic() + a.max_secs
        print(json.dumps({"ui_url": f"http://127.0.0.1:{ctl.proxy.port}/", "control_url": f"http://127.0.0.1:{ctl.control.port}", "control_token": ctl.token, "out": a.out}), flush=True)
        while not ctl.stopped.is_set() and not quit.is_set():
            reason = ctl.lost()
            if reason:
                m.gate("run_completed", se.FAIL, reason)
                break
            if a.wait and ctl.state_view()[1]["stage"] == "done":
                break
            time.sleep(1)
    except BaseException as e:  # keep the evidence, then report
        m.gate("run_completed", se.FAIL, f"{type(e).__name__}: {m.scrub(str(e))[:300]}")
    finally:
        if ctl.started:
            ctl.stop()
        else:
            stack.cleanup()
            m.write(out / "manifest.json")
        print(f"manifest: {out / 'manifest.json'}")
    lock.close()
    return 0 if m.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
