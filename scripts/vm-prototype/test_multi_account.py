"""Probe tests for multi-account use of one bot computer. They change no production code.

Part 1 (always runs): the guest adapter `guest/bare-call` `prepare()` with two named sessions.
Part 2 (set DECK_LIVE_BARE=1): the real `bare-mcp` against a local synthetic same-origin service.
Cookies are generated at run time. No real account, mailbox or network is used.

A failing test here is evidence of a gap, not a harness fault. Tests named `gap_` document a missing
guarantee: the message of each says what the guest adapter lacks."""
import importlib.machinery
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bc = load(HERE / "guest" / "bare-call", "bare_call_multi")


class GuestAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deck-multi-test-")
        self.home = Path(self.tmp, "home")
        (self.home / ".config/deck").mkdir(parents=True)
        self.secrets = self.home / ".config/deck/secrets.env"
        self.secrets.write_text("ACCT_A_PASS=gen-a\nACCT_B_PASS=gen-b\n")
        self.out = Path(self.tmp, "out")
        self.ws = Path(self.tmp, "ws")
        self.out.mkdir()
        self.ws.mkdir()
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)

    def tearDown(self):
        os.environ["HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prep(self, tool, **args):
        return bc.prepare({"name": tool, "arguments": args}, str(self.out), str(self.ws))["arguments"]

    def test_session_and_name_pass_through_unchanged_for_two_accounts(self):
        for acct in ("a", "b"):
            a = self.prep("ensure_session", session=acct, name=f"gmail-{acct}", url="https://x.test")
            self.assertEqual((a["session"], a["name"]), (acct, f"gmail-{acct}"))
        a = self.prep("step", session="b", steps="goto https://x.test")
        self.assertEqual(a["session"], "b")

    def test_one_shared_secrets_file_goes_to_both_sessions(self):
        for acct in ("a", "b"):
            a = self.prep("ensure_session", session=acct, name=acct)
            self.assertEqual(a["secrets_file"], str(self.secrets))
        # Credential binding is by variable name in one file; the adapter has no per-session file.
        self.assertEqual(self.prep("open", session="a")["secrets_file"], str(self.secrets))

    def test_model_cannot_pick_secrets_file_or_state_path(self):
        a = self.prep("ensure_session", session="a", secrets_file="/etc/shadow")
        self.assertEqual(a["secrets_file"], str(self.secrets))
        # `file` is a model argument at this layer; the host strips it (HIDDEN_ARGS) before relay.
        self.assertIn("file", self.prep("save_state", session="a", name="a", file="/etc/passwd"))

    def test_gap_guest_save_state_gets_no_durable_file(self):
        for tool in ("save_state", "ensure_session"):
            a = self.prep(tool, session="a", name="gmail-a")
            self.assertIn("file", a, f"guest {tool} gets no state file: a login survives only in bare-mcp memory")

    def test_gap_open_with_state_gets_no_file(self):
        a = self.prep("open", session="a", state="gmail-a", url="https://x.test")
        self.assertIn("file", a, "guest open state=... cannot restore from disk after bare-mcp restarts")

    def test_downloads_dir_is_shared_between_sessions(self):
        a = self.prep("open", session="a")
        b = self.prep("open", session="b")
        self.assertEqual(a["downloads_dir"], b["downloads_dir"])  # characterization, not a verdict


# ---------- live part ----------

class Service:
    """Same origin, cookie identity. GET /login/<user> sets a random token; /me names the user; /logout drops it."""

    def __init__(self):
        self.tokens = {}
        svc = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body="", cookie=None, location=None):
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                self.send_header("Cache-Control", "no-store")
                if cookie:
                    self.send_header("Set-Cookie", f"sid={cookie}; Path=/")
                if location:
                    self.send_header("Location", location)
                data = body.encode()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def sid(self):
                jar = dict(p.strip().split("=", 1) for p in self.headers.get("Cookie", "").split(";") if "=" in p)
                return jar.get("sid")

            def do_GET(self):
                p = self.path
                if p.startswith("/login/"):
                    tok = secrets.token_hex(8)
                    svc.tokens[tok] = p.rsplit("/", 1)[1]
                    return self.send(303, cookie=tok, location="/me")
                who = svc.tokens.get(self.sid())
                if p == "/logout":
                    svc.tokens.pop(self.sid(), None)
                    who = None
                if p in ("/me", "/logout"):
                    return self.send(200, f"<html><body><p>{'user=' + who if who else 'anonymous'}</p></body></html>")
                self.send(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Mcp:
    def __init__(self):
        self.p = subprocess.Popen(["bare-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "multi-test", "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, tool_name, /, **args):
        r = self.rpc("tools/call", {"name": tool_name, "arguments": args})["result"]
        return r.get("content", [{}])[0].get("text", "")

    base = ""

    def me(self, session):
        self.tool("step", session=session, steps=f"goto {self.base}/me", compact=True)
        return self.tool("read", session=session).strip()

    def stop(self):
        self.p.kill()
        self.p.wait(timeout=10)
        self.p.stdin.close()
        self.p.stdout.close()


@unittest.skipUnless(os.environ.get("DECK_LIVE_BARE") == "1" and shutil.which("bare-mcp"),
                     "set DECK_LIVE_BARE=1 with bare-mcp on PATH")
class LiveBare(unittest.TestCase):
    def setUp(self):
        self.svc = Service()
        self.tmp = tempfile.mkdtemp(prefix="deck-multi-live-")
        self.m = Mcp()
        self.m.base = self.svc.url

    def tearDown(self):
        self.m.stop()
        self.svc.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self, session, user):
        self.m.tool("open", session=session, url=f"{self.svc.url}/login/{user}")

    def test_two_isolated_sessions_alternate_logout_and_reopen(self):
        m = self.m
        self.login("a", "alice")
        self.login("b", "bob")
        self.assertIn("user=alice", m.me("a"))
        self.assertIn("user=bob", m.me("b"))
        self.assertIn("user=alice", m.me("a"))  # alternating read
        self.assertIn("a", m.tool("sessions"))
        # save both to files, then log out A
        fa, fb = f"{self.tmp}/a.state", f"{self.tmp}/b.state"
        m.tool("save_state", session="a", name="gmail-a", file=fa)
        m.tool("save_state", session="b", name="gmail-b", file=fb)
        self.assertEqual(oct(os.stat(fa).st_mode & 0o777), "0o600")
        m.tool("step", session="a", steps=f"goto {self.svc.url}/logout", compact=True)
        self.assertIn("anonymous", m.me("a"))
        self.assertIn("user=bob", m.me("b"))  # B stays usable
        # close both, restart bare-mcp (new process memory), reopen from files only
        m.tool("close", session="a")
        m.tool("close", session="b")
        m.stop()
        self.m = m = Mcp()
        m.base = self.svc.url
        m.tool("open", session="a2", url=f"{self.svc.url}/me", state="gmail-a", file=fa)
        m.tool("open", session="b2", url=f"{self.svc.url}/me", state="gmail-b", file=fb)
        self.assertIn("anonymous", m.me("a2"))  # the service revoked A's token at logout: a restored file cannot revive it
        self.assertIn("user=bob", m.me("b2"))

    def test_gap_state_is_not_restored_by_name_without_file_after_restart(self):
        m = self.m
        self.login("a", "alice")
        m.tool("save_state", session="a", name="gmail-a")  # memory only: what the guest adapter sends today
        m.stop()
        self.m = m = Mcp()
        m.base = self.svc.url
        m.tool("open", session="a2", url=f"{self.svc.url}/me", state="gmail-a")
        self.assertIn("user=alice", m.me("a2"), "memory-only state does not survive a bare-mcp restart")


if __name__ == "__main__":
    unittest.main()
