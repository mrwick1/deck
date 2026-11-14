"""Tests for shared browser state with several accounts in ONE browser session.

Product rule: one browser session and profile hold every account. The site's own account switcher
selects the account. Deck never makes a session or profile per account. The saved state name
identifies that one shared profile.

Part 1 (always runs): `guest/bare-call` `prepare()` fixes the state file path.
Part 2 (set DECK_LIVE_BARE=1): the real `bare-mcp` against a local synthetic service that simulates
multi-account cookie behavior. It does not show how Google behaves. Cookies are generated at run time.
No real account, mailbox or network is used. D33 applies: a trusted guest shell is out of scope."""
import importlib.machinery
import importlib.util
import json
import os
import queue
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bc = load(HERE / "guest" / "bare-call", "bare_call_multi")


class Home:
    """A temporary HOME for the process. Cleans only its own directory."""

    def home_setup(self, prefix):
        self.tmp = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = Path(self.tmp, "home")
        (self.home / ".config/deck").mkdir(parents=True)
        self.out = Path(self.tmp, "out")
        self.ws = Path(self.tmp, "ws")
        self.out.mkdir()
        self.ws.mkdir()
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ.get("HOME", ""))
        os.environ["HOME"] = str(self.home)
        self.browser = self.home / ".local/state/deck/browser"

    def prep(self, tool, **args):
        return bc.prepare({"name": tool, "arguments": args}, str(self.out), str(self.ws))["arguments"]


class GuestAdapter(Home, unittest.TestCase):
    def setUp(self):
        self.home_setup("deck-multi-test-")
        self.secrets = self.home / ".config/deck/secrets.env"
        self.secrets.write_text("ACCT_A_PASS=gen-a\nACCT_B_PASS=gen-b\n")

    def mkbrowser(self):
        self.browser.mkdir(parents=True, mode=0o700)
        for d in (self.browser, self.browser.parent):
            d.chmod(0o700)

    def test_save_state_and_ensure_session_get_the_fixed_file(self):
        want = str(self.browser / "shared.state")
        for tool in ("save_state", "ensure_session"):
            self.assertEqual(self.prep(tool, name="shared")["file"], want)
        self.assertEqual(stat.S_IMODE(self.browser.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.browser.parent.stat().st_mode), 0o700)
        self.assertFalse(Path(want).exists())  # bare creates it; the adapter leaves no empty file

    def test_open_with_state_gets_the_fixed_file_by_state_name(self):
        a = self.prep("open", state="shared", url="https://x.test")
        self.assertEqual(a["file"], str(self.browser / "shared.state"))

    def test_plain_open_is_unchanged(self):
        a = self.prep("open", url="https://x.test")
        self.assertNotIn("file", a)
        self.assertFalse((self.home / ".local").exists())  # no state directory without a state name
        self.assertIn("downloads_dir", a)

    def test_adapter_overrides_the_caller_file(self):
        want = str(self.browser / "shared.state")
        self.assertEqual(self.prep("save_state", name="shared", file="/etc/passwd")["file"], want)
        self.assertEqual(self.prep("ensure_session", name="shared", file="/etc/passwd")["file"], want)
        self.assertEqual(self.prep("open", state="shared", file="/etc/passwd")["file"], want)
        self.assertEqual(self.prep("ensure_session", secrets_file="/etc/shadow", name="shared")["secrets_file"], str(self.secrets))

    def test_session_argument_is_not_used_to_pick_the_file(self):
        # One shared profile: the session label does not change the state path.
        a = self.prep("save_state", name="shared", session="main")["file"]
        b = self.prep("save_state", name="shared", session="other")["file"]
        self.assertEqual(a, b)

    def test_bad_names_are_rejected(self):
        for bad in ("", "a/b", "../x", "a.b", " a", "a\n", "é", "x" * 65, None, 7, ["a"]):
            for tool, key in (("save_state", "name"), ("ensure_session", "name"), ("open", "state")):
                if tool == "open" and bad is None:
                    continue  # state absent: a plain open
                with self.subTest(tool=tool, bad=bad), self.assertRaises(ValueError):
                    self.prep(tool, **{key: bad})
        self.assertEqual(self.prep("save_state", name="a-B_9" + "x" * 59)["file"][-6:], ".state")  # 64 chars pass

    def test_existing_valid_file_is_kept(self):
        self.mkbrowser()
        f = self.browser / "shared.state"
        f.write_text("secret-data")
        f.chmod(0o600)
        for tool, key in (("save_state", "name"), ("ensure_session", "name"), ("open", "state")):
            self.assertEqual(self.prep(tool, **{key: "shared"})["file"], str(f))
        self.assertEqual(f.read_text(), "secret-data")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_unsafe_existing_files_are_rejected_and_untouched(self):
        self.mkbrowser()
        f = self.browser / "shared.state"
        target = Path(self.tmp, "target")
        target.write_text("x")
        target.chmod(0o600)
        f.symlink_to(target)
        self.assertRaises(ValueError, self.prep, "save_state", name="shared")
        self.assertEqual(target.read_text(), "x")
        f.unlink()
        f.symlink_to(Path(self.tmp, "missing"))  # dangling symlink
        self.assertRaises(ValueError, self.prep, "save_state", name="shared")
        f.unlink()
        f.write_text("x")
        f.chmod(0o644)
        self.assertRaises(ValueError, self.prep, "open", state="shared")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o644)  # not repaired silently
        f.chmod(0o600)
        os.link(f, Path(self.tmp, "hard"))
        self.assertRaises(ValueError, self.prep, "open", state="shared")
        os.unlink(Path(self.tmp, "hard"))
        f.unlink()
        f.mkdir()
        self.assertRaises(ValueError, self.prep, "ensure_session", name="shared")
        f.rmdir()
        f.write_text("x")
        f.chmod(0o600)
        with mock.patch.object(bc.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertRaises(ValueError, self.prep, "open", state="shared")  # foreign owner

    def test_unsafe_directories_are_rejected(self):
        elsewhere = Path(self.tmp, "elsewhere")
        elsewhere.mkdir(mode=0o700)
        for link in (self.home / ".local", ):
            link.symlink_to(elsewhere)
            self.assertRaises(ValueError, self.prep, "save_state", name="shared")
            link.unlink()
        (self.home / ".local/state").mkdir(parents=True)
        deck = self.home / ".local/state/deck"
        deck.symlink_to(elsewhere)
        self.assertRaises(ValueError, self.prep, "save_state", name="shared")
        deck.unlink()
        deck.mkdir(mode=0o700)
        (deck / "browser").symlink_to(elsewhere)
        self.assertRaises(ValueError, self.prep, "save_state", name="shared")
        (deck / "browser").unlink()
        (deck / "browser").mkdir(mode=0o755)
        (deck / "browser").chmod(0o755)
        self.assertRaises(ValueError, self.prep, "open", state="shared")
        self.assertEqual(stat.S_IMODE((deck / "browser").stat().st_mode), 0o755)  # not repaired silently
        self.assertEqual(list(elsewhere.iterdir()), [])
        with mock.patch.object(bc.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertRaises(ValueError, self.prep, "open", state="shared")  # foreign owner

    def test_downloads_dir_stays_shared(self):
        self.assertEqual(self.prep("open")["downloads_dir"], self.prep("open", session="b")["downloads_dir"])


# ---------- live part ----------

class Service:
    """Simulates multi-account cookie behavior. This is a fixture, not Google.

    ONE cookie `sid` names one server record: the set of signed-in accounts and the selected one.
    /login/<u>     adds u to the record of the current cookie (new record without a cookie); selects u.
    /switch/<u>    selects u when u is signed in; else 403.
    /logout        fixture semantics: signs out the selected account only; the rest stay.
    /logout-all    fixture semantics: drops the whole record.
    /me            shows `user=<selected>` and `accounts=<a,b>`, or `anonymous`."""

    def __init__(self):
        self.records = {}
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
                p, sid = self.path, self.sid()
                rec = svc.records.get(sid)
                if p.startswith("/login/"):
                    u = p.rsplit("/", 1)[1]
                    if rec is None:
                        sid = secrets.token_hex(8)
                        rec = svc.records[sid] = {"accounts": [], "selected": None}
                    if u not in rec["accounts"]:
                        rec["accounts"].append(u)
                    rec["selected"] = u
                    return self.send(303, cookie=sid, location="/me")
                if p.startswith("/switch/"):
                    u = p.rsplit("/", 1)[1]
                    if rec is None or u not in rec["accounts"]:
                        return self.send(403, "<html><body><p>not signed in</p></body></html>")
                    rec["selected"] = u
                    return self.send(303, location="/me")
                if p == "/logout" and rec:
                    rec["accounts"].remove(rec["selected"])
                    rec["selected"] = rec["accounts"][0] if rec["accounts"] else None
                    if not rec["accounts"]:
                        svc.records.pop(sid)
                if p == "/logout-all":
                    svc.records.pop(sid, None)
                if p in ("/me", "/logout", "/logout-all"):
                    rec = svc.records.get(sid)
                    text = f"user={rec['selected']} accounts={','.join(rec['accounts'])}" if rec else "anonymous"
                    return self.send(200, f"<html><body><p>{text}</p></body></html>")
                self.send(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Mcp:
    """One bare-mcp process in its own process group. Every wait is bounded. A tool error raises."""
    WAIT = 30.0

    def __init__(self):
        self.p = subprocess.Popen(["bare-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.pid = self.p.pid
        self.q = queue.Queue()
        threading.Thread(target=lambda: [self.q.put(l) for l in self.p.stdout], daemon=True).start()
        self.n = 0
        try:
            self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "multi-test", "version": "1"}})
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            self.p.stdin.flush()
        except BaseException:
            self.stop()
            raise

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        try:
            line = self.q.get(timeout=self.WAIT)
        except queue.Empty:
            raise TimeoutError(f"bare-mcp gave no answer to {method} in {self.WAIT}s")
        return json.loads(line)

    def call(self, tool_name, **args):
        """Raw call: returns (is_error, text)."""
        r = self.rpc("tools/call", {"name": tool_name, "arguments": args})
        if "error" in r:
            raise AssertionError(f"{tool_name}: rpc error {r['error']}")
        res = r["result"]
        return bool(res.get("isError")), (res.get("content") or [{}])[0].get("text", "")

    def tool(self, tool_name, /, **args):
        err, text = self.call(tool_name, **args)
        if err:
            raise AssertionError(f"{tool_name} failed: {text[:300]}")
        return text

    def go(self, url):
        self.tool("step", steps=f"goto {url}", compact=True)
        return self.tool("read").strip()

    def stop(self):
        if self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.p.wait(timeout=10)
        self.p.stdin.close()
        self.p.stdout.close()


class HarnessCleanup(unittest.TestCase):
    def test_failed_initialize_stops_the_owned_process(self):
        with mock.patch.object(Mcp, "rpc", side_effect=TimeoutError("no handshake")), mock.patch.object(Mcp, "stop") as stop:
            process = mock.Mock()
            process.stdout = iter(())
            with mock.patch.object(subprocess, "Popen", return_value=process):
                with self.assertRaises(TimeoutError):
                    Mcp()
            stop.assert_called_once()


class Prep:
    """Send a tool through the production `bare-call prepare()`, as the guest relay does."""

    def __init__(self, test, mcp):
        self.t, self.m = test, mcp

    def call(self, tool, **args):
        p = self.t.prep(tool, **args)
        return self.m.tool(tool, **p)


@unittest.skipUnless(os.environ.get("DECK_LIVE_BARE") == "1" and shutil.which("bare-mcp"),
                     "set DECK_LIVE_BARE=1 with bare-mcp on PATH")
class LiveBare(Home, unittest.TestCase):
    def setUp(self):
        self.home_setup("deck-multi-live-")
        self.svc = Service()
        self.addCleanup(self.svc.stop)
        self.m = None

    def start(self):
        self.m = Mcp()
        self.addCleanup(self.m.stop)
        return self.m

    def only_main(self):
        # One-session evidence: bare lists exactly one live session, and the service holds one record.
        self.assertEqual(len(self.svc.records), 1, "one cookie record holds every account")
        text = self.m.tool("sessions")
        self.assertIn("main", text)
        self.assertEqual(text.count("http://127.0.0.1"), 1, f"exactly one live session expected: {text}")

    def test_one_session_holds_two_accounts_and_survives_a_restart(self):
        base, m = self.svc.url, self.start()
        pids = {m.pid}
        m.tool("open", url=f"{base}/login/alice")  # session main, no per-account session
        self.assertIn("user=alice accounts=alice", m.go(f"{base}/me"))
        self.assertIn("user=bob accounts=alice,bob", m.go(f"{base}/login/bob"))  # B is added; A is not overwritten
        for _ in range(2):  # alternate through the site's own switcher
            self.assertIn("user=alice accounts=alice,bob", m.go(f"{base}/switch/alice"))
            self.assertIn("user=bob accounts=alice,bob", m.go(f"{base}/switch/bob"))
        self.only_main()
        Prep(self, m).call("save_state", name="shared")
        f = self.browser / "shared.state"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        tok = next(iter(self.svc.records))
        self.assertIn(tok, f.read_text(), "the one cookie token is on disk")
        # restart: new process memory, restore through prepare() from the file only
        m.tool("close")
        m.stop()
        m = self.start()
        pids.add(m.pid)
        self.assertEqual(len(pids), 2)
        Prep(self, m).call("open", state="shared", url=f"{base}/me")
        self.assertIn("accounts=alice,bob", m.go(f"{base}/me"))
        self.assertIn("user=alice", m.go(f"{base}/switch/alice"))
        self.assertIn("user=bob", m.go(f"{base}/switch/bob"))
        self.assertIn("user=alice", m.go(f"{base}/switch/alice"))
        self.only_main()

    def test_ensure_session_restores_the_shared_profile_from_disk(self):
        base, m = self.svc.url, self.start()
        m.tool("open", url=f"{base}/login/alice")
        m.go(f"{base}/login/bob")
        Prep(self, m).call("save_state", name="shared")
        m.stop()
        m = self.start()
        # `login` fails on purpose: a restore from the file must pass the check before any login runs.
        out = Prep(self, m).call("ensure_session", name="shared", url=f"{base}/me", login="expect js \"false\"", check='expect css="p" text="user=bob accounts=alice,bob"')
        self.assertIn("restored", out)
        self.assertIn("user=alice", m.go(f"{base}/switch/alice"))
        self.assertIn("user=bob", m.go(f"{base}/switch/bob"))
        self.only_main()

    def test_logout_selected_account_vs_logout_all_fixture_semantics(self):
        base, m = self.svc.url, self.start()
        m.tool("open", url=f"{base}/login/alice")
        m.go(f"{base}/login/bob")
        self.assertIn("user=alice accounts=alice", m.go(f"{base}/logout"))  # signs out bob (selected) only
        self.assertIn("user=alice", m.go(f"{base}/switch/alice"))
        m.go(f"{base}/login/bob")
        self.assertIn("anonymous", m.go(f"{base}/logout-all"))  # fixture semantics: whole record gone
        self.assertEqual(self.svc.records, {})
        self.assertIn("anonymous", m.go(f"{base}/me"))

    def test_characterization_direct_bare_without_file_is_memory_only(self):
        # What the adapter sent before: save_state with a name and no file. It does not survive a restart.
        base, m = self.svc.url, self.start()
        m.tool("open", url=f"{base}/login/alice")
        m.tool("save_state", name="shared")
        m.stop()
        m = self.start()
        err, text = m.call("open", state="shared", url=f"{base}/me")
        self.assertTrue(err or "user=alice" not in text, f"unexpected restore: {text[:200]}")
        if not err:
            self.assertNotIn("user=alice", m.go(f"{base}/me"))


if __name__ == "__main__":
    unittest.main()
