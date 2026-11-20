import contextlib
import fcntl
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import gmail_accounts_e2e as g
import secretchan
import setup_e2e as e
import shared_profile_e2e as sp
import vmproto as v

ACC = {"A": "first.user@gmail.com", "B": "second.user@gmail.com"}
SECRET = "Sup3r-Secret-pw"
CREDS = {"A": ("first.user@gmail.com", SECRET), "B": ("second.user@gmail.com", "other-pw-9")}
SEL = "[data-trial-account]"


def run_captured(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue(), err.getvalue()


class Sanitize(unittest.TestCase):
    def test_maps_known_addresses_hides_others_and_masks_secrets(self):
        t = g.sanitize("Inbox - First.User@gmail.com; second.user@gmail.com; stranger@example.org; pw hunter2x", ACC, ["hunter2x"])
        self.assertEqual(t, "Inbox - <A>; <B>; <other-address>; pw [secret]")

    def test_cuts_long_output(self):
        self.assertIn("more characters", g.sanitize("x" * 50, ACC, limit=10))


class HostClass(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(g.host_class("mail.google.com"), "mailbox")
        self.assertEqual(g.host_class('"mail.google.com"'), "mailbox")
        self.assertEqual(g.host_class("accounts.google.com"), "other")
        for bad in (None, "", "a b", "x" * 300, "<script>"):
            self.assertEqual(g.host_class(bad), "unknown")


class Delivery(unittest.TestCase):
    def test_plan_has_four_distinct_names_and_request_ids(self):
        plan = g.delivery_plan({"A": ("ua", "pa"), "B": ("ub", "pb")})
        self.assertEqual(len({n for _, n, _ in plan}), 4)
        self.assertEqual(len({r for r, _, _ in plan}), 4)

    def test_values_travel_only_on_stdin(self):
        calls = []

        class Qga:
            def exec(self, argv, stdin=None, timeout=0):
                calls.append((argv, stdin))
                return 0, json.dumps({"ok": True, "bot_id": "b", "request_id": argv[-1], "label": "X"}), ""

        done = g.deliver_all(Qga(), "b", {"A": ("ua@x.y", "pw-A1"), "B": ("ub@x.y", "pw-B2")})
        self.assertTrue(all(done.values()) and len(done) == 4)
        for argv, _ in calls:
            self.assertFalse(any(x in " ".join(argv) for x in ("pw-A1", "pw-B2", "ua@x.y", "ub@x.y")))

    def test_failure_names_the_secret_not_the_value(self):
        class Qga:
            def exec(self, *a, **k):
                return 1, json.dumps({"ok": False, "error": "unsafe_path"}), ""

        with self.assertRaises(g.TrialError) as r:
            g.deliver_all(Qga(), "b", {"A": ("u", "topsecret")})
        self.assertNotIn("topsecret", str(r.exception))


class Vault(unittest.TestCase):
    def run_with(self, out, code=0):
        return lambda argv, **k: SimpleNamespace(returncode=code, stdout=out, stderr="")

    def rec(self, **d):
        return json.dumps({"data": {"username": "u", "password": "p", **d}})

    def test_reads_user_and_password_with_a_bounded_timeout(self):
        seen = {}

        def run(argv, **k):
            seen.update(k, argv=argv)
            return SimpleNamespace(returncode=0, stdout=self.rec(), stderr="")

        self.assertEqual(g.vault_credentials("id", run), ("u", "p"))
        self.assertTrue(0 < seen["timeout"] <= 60)

    def test_bad_records_are_refused(self):
        bad = [self.run_with("", 1), self.run_with(self.rec(password="")), self.run_with("garbage"),
               self.run_with(self.rec(password=7)), self.run_with(self.rec(username=None)),
               self.run_with(self.rec(password="a\nb")), self.run_with(self.rec(password="a\x00b")),
               self.run_with(self.rec(password="x" * 5000)), self.run_with(json.dumps([1]))]
        for r in bad:
            with self.assertRaises(g.TrialError):
                g.vault_credentials("id", r)

    def test_exceptions_carrying_a_secret_never_surface(self):
        for exc in (RuntimeError(SECRET), g.subprocess.TimeoutExpired(["rbw", SECRET], 5), OSError(SECRET)):
            def run(argv, exc=exc, **k):
                raise exc

            with self.assertRaises(g.TrialError) as r:
                g.vault_credentials("id", run)
            self.assertNotIn(SECRET, str(r.exception))
            self.assertIsNone(r.exception.__cause__)
            self.assertTrue(r.exception.__suppress_context__)

    def test_item_ids_are_explicit_and_not_defaults(self):
        self.assertFalse(hasattr(g, "ITEMS"))
        src = Path(g.__file__).read_text()
        self.assertNotRegex(src, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

    def test_load_creds_needs_two_distinct_items_and_accounts(self):
        recs = {"ia": ("a@x.y", "pa"), "ib": ("b@x.y", "pb")}
        with mock.patch.object(g, "vault_credentials", lambda i, run=None: recs[i]):
            self.assertEqual(set(g.load_creds({"A": "ia", "B": "ib"})), {"A", "B"})
            with self.assertRaises(g.TrialError):
                g.load_creds({"A": "ia", "B": "ia"})
        with mock.patch.object(g, "vault_credentials", lambda i, run=None: ("same@x.y", "p")):
            with self.assertRaises(g.TrialError):
                g.load_creds({"A": "ia", "B": "ib"})


class Parse(unittest.TestCase):
    def test_up_needs_explicit_item_ids(self):
        base = ["--out", "/x/o", "--base", "/x/b.qcow2", "up", "--bare-candidate", "/x/c", "--expect-bare-sha256", "0" * 64]
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            g.parse(base)
        a = g.parse(base + ["--item-a", "ia", "--item-b", "ib"])
        self.assertEqual((a.item_a, a.item_b), ("ia", "ib"))


class Rebind(unittest.TestCase):
    BASE = ["--out", "/x/o", "--base", "/x/b.qcow2", "up", "--bare-candidate", "/x/c", "--expect-bare-sha256", "0" * 64, "--item-a", "ia", "--item-b", "ib"]

    def test_rebind_options_come_as_a_pair_and_are_validated(self):
        a = g.parse(self.BASE)
        self.assertEqual((a.rebind_guest_id, a.rebind_epoch), (None, None))
        a = g.parse(self.BASE + ["--rebind-guest-id", "ab" * 16, "--rebind-epoch", "17"])
        self.assertEqual((a.rebind_guest_id, a.rebind_epoch), ("ab" * 16, 17))
        for bad in (["--rebind-guest-id", "ab" * 16], ["--rebind-epoch", "3"], ["--rebind-guest-id", "xyz", "--rebind-epoch", "3"],
                    ["--rebind-guest-id", "ab" * 16, "--rebind-epoch", "-1"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                g.parse(self.BASE + bad)

    def test_boot_guest_passes_the_rebind_to_install_executor(self):
        for a, want in ((SimpleNamespace(base="/b", fixture_port=1, rebind_guest_id=None, rebind_epoch=None), {}),
                        (SimpleNamespace(base="/b", fixture_port=1, rebind_guest_id="ab" * 16, rebind_epoch=17), {"guest_id": "ab" * 16, "max_epoch": 17})):
            t = g.Trial.__new__(g.Trial)
            t.a, t.p, t.register = a, mock.MagicMock(), lambda pid: None
            t.guest_sha = lambda path: "x"
            with mock.patch.object(g.v, "create_overlay"), mock.patch.object(g.v, "start_qemu", return_value=1), mock.patch.object(g.v, "Qga"), \
                    mock.patch.object(g.v, "install_executor") as ie:
                t.boot_guest()
            ie.assert_called_once_with(t.p, sp.BOT, **want)


# ---------- scenario fakes ----------

class FakeEnv:
    def __init__(self, a, mode="0o444", base_ok=True, installed=None, boom=None, log=None):
        self.a, self.mode, self._base_ok, self.installed, self.boom = a, mode, base_ok, installed, boom
        self.log = log if log is not None else []
        self.boots = self.cleaned = 0
        self.pid = 4242
        self.stack = SimpleNamespace(owned={})
        self.p = SimpleNamespace(vm=SimpleNamespace(read_text=lambda: json.dumps({"internet": True})), qga="/q")
        self.keep_at_cleanup = None

    def pressure(self):
        return None

    def head(self):
        return "head"

    def base_identity(self):
        return {"id": "i", "virtual_size": 1, "mode": self.mode}

    def base_ok(self):
        return self._base_ok

    def host(self):
        return {}

    def boot_guest(self):
        self.boots += 1
        self.log.append("boot")
        if self.boom:
            raise self.boom
        return {"adapter_sha256": "x", "bare_mcp_sha256": "y"}

    def install_candidate(self, path, sha):
        return {"installed_sha256": self.installed or sha}

    def cleanup(self):
        self.cleaned += 1
        self.keep_at_cleanup = self.a.keep_disk
        return []


class UpScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.a = SimpleNamespace(out=str(root / "o"), lock=str(root / "lock"), base="/b.qcow2", bare_candidate="/c", expect_bare_sha256="a" * 64,
                                 fixture_port=1, keep_disk=True, item_a="ia", item_b="ib", account_control_selector=SEL,
                                 min_avail_mib=1, max_load=1.0)
        self.log = []
        self.env = None
        self.deliver = lambda qga, bot, creds: {n: True for _, n, _ in g.delivery_plan(creds)}
        self.pin = lambda s, x, d: {"sha256": "a" * 64, "size": 1, "override": True}

    def run_up(self, **env_kw):
        def mk(a):
            self.env = FakeEnv(a, log=self.log, **env_kw)
            return self.env

        def creds(items, run=None):
            self.log.append("creds")
            return CREDS

        with mock.patch.object(g, "Trial", mk), mock.patch.object(g, "load_creds", creds), \
                mock.patch.object(g, "deliver_all", lambda *x: self.deliver(*x)), mock.patch.object(g.v, "Qga", lambda p: None), \
                mock.patch.object(sp, "check_candidate", lambda s, x: 1), mock.patch.object(sp, "pin_candidate", lambda s, x, d: self.pin(s, x, d)), \
                mock.patch.object(e, "socket_path_ok", lambda o: True), mock.patch.object(e, "capture_processes", lambda roots: {4242: "99"}):
            res = run_captured(g.cmd_up, self.a)
        text = res[1] + res[2]
        mf = Path(self.a.out) / "manifest-up.json"
        if mf.exists():
            text += mf.read_text()
        self.assertNotIn(SECRET, text)
        self.assertNotIn("second.user", text)
        self.assertNotIn("first.user", text)
        return res

    def test_happy_path_keeps_the_guest_and_loads_vault_before_boot(self):
        rc, out, _ = self.run_up()
        self.assertEqual(rc, 0)
        self.assertEqual(self.log, ["creds", "boot"])
        self.assertEqual(self.env.cleaned, 0)
        owned = json.loads((Path(self.a.out) / g.OWNED).read_text())
        self.assertEqual(owned["pid"], 4242)
        refs = json.loads((Path(self.a.out) / g.REFS).read_text())
        self.assertEqual(refs["items"], {"A": "ia", "B": "ib"})

    def test_unfrozen_or_unverified_base_never_boots(self):
        for kw in ({"mode": "0o644"}, {"base_ok": False}):
            self.tearDown_out()
            rc, _, _ = self.run_up(**kw)
            self.assertNotEqual(rc, 0)
            self.assertEqual(self.env.boots, 0, kw)
            self.assertEqual(self.env.cleaned, 1, kw)

    def tearDown_out(self):
        import shutil
        shutil.rmtree(self.a.out, ignore_errors=True)

    def test_candidate_hash_mismatch_aborts_and_cleans(self):
        rc, _, _ = self.run_up(installed="b" * 64)
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.cleaned, 1)
        self.assertFalse(self.env.keep_at_cleanup)

    def test_delivery_receipt_failure_aborts_and_cleans(self):
        self.deliver = lambda qga, bot, creds: {n: n != "GMAIL_B_PASS" for _, n, _ in g.delivery_plan(creds)}
        rc, _, _ = self.run_up()
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.cleaned, 1)

    def test_delivery_exception_with_a_secret_aborts_cleans_and_does_not_leak(self):
        def boom(*x):
            raise RuntimeError(SECRET)

        self.deliver = boom
        rc, out, err = self.run_up()
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.cleaned, 1)

    def test_internet_not_recorded_aborts(self):
        def mk(a):
            env = FakeEnv(a, log=self.log)
            env.p.vm = SimpleNamespace(read_text=lambda: json.dumps({"internet": False}))
            self.env = env
            return env

        with mock.patch.object(g, "Trial", mk), mock.patch.object(g, "load_creds", lambda i, run=None: CREDS), \
                mock.patch.object(g, "deliver_all", lambda *x: self.deliver(*x)), mock.patch.object(g.v, "Qga", lambda p: None), \
                mock.patch.object(sp, "check_candidate", lambda s, x: 1), mock.patch.object(sp, "pin_candidate", lambda s, x, d: self.pin(s, x, d)), \
                mock.patch.object(e, "socket_path_ok", lambda o: True), mock.patch.object(e, "capture_processes", lambda roots: {4242: "99"}):
            rc, _, _ = run_captured(g.cmd_up, self.a)
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.cleaned, 1)

    def test_boot_exception_with_a_secret_is_cleaned_and_not_printed(self):
        rc, _, _ = self.run_up(boom=RuntimeError(f"bad {SECRET} first.user@gmail.com"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.cleaned, 1)

    def test_pre_delivery_runtime_error_shows_a_sanitized_reason(self):
        rc, out, err = self.run_up(boom=RuntimeError(f"qga exec failed {SECRET} first.user@gmail.com"))
        self.assertEqual(rc, 1)
        self.assertIn("qga exec failed", out + err)
        self.assertNotIn(SECRET, out + err)
        self.assertNotIn("first.user@gmail.com", out + err)

    def test_systemexit_from_the_vm_layer_is_controlled(self):
        rc, _, _ = self.run_up(boom=SystemExit(f"qemu died {SECRET}"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.cleaned, 1)

    def test_pin_errors_after_the_directory_exists_are_controlled(self):
        def pin(s, x, d):
            raise sp.CandidateError("the private copy differs")

        self.pin = pin
        rc, _, _ = self.run_up()
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.boots, 0)
        self.assertEqual(self.env.cleaned, 1)

        def pin2(s, x, d):
            raise OSError(SECRET)

        self.tearDown_out()
        self.pin = pin2
        rc, _, _ = self.run_up()
        self.assertEqual(rc, 1)

    def test_a_failing_cleanup_is_reported_without_its_text(self):
        def mk(a):
            env = FakeEnv(a, boom=RuntimeError("x"), log=self.log)
            env.cleanup = mock.Mock(side_effect=RuntimeError(SECRET))
            self.env = env
            return env

        with mock.patch.object(g, "Trial", mk), mock.patch.object(g, "load_creds", lambda i, run=None: CREDS), \
                mock.patch.object(g.v, "Qga", lambda p: None), mock.patch.object(sp, "check_candidate", lambda s, x: 1), \
                mock.patch.object(sp, "pin_candidate", lambda s, x, d: self.pin(s, x, d)), mock.patch.object(e, "socket_path_ok", lambda o: True):
            rc, out, err = run_captured(g.cmd_up, self.a)
        self.assertEqual(rc, 1)
        self.assertNotIn(SECRET, out + err + (Path(self.a.out) / "manifest-up.json").read_text())


# ---------- ownership, request ids ----------

class Attach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = SimpleNamespace(out=self.tmp.name, lock=str(Path(self.tmp.name) / "lock"), item_a=None, item_b=None, account_control_selector=None)
        g.write_private(Path(self.tmp.name) / g.OWNED, {"pid": 4242, "processes": {"4242": "99", "4300": "55"}})

    def attach(self, record, ok=True, **kw):
        with mock.patch.object(e, "process_record", lambda pid: record.get(pid)), mock.patch.object(v, "proc_ok", lambda p, pid: ok):
            return g.attach(self.a, **kw)

    def test_owned_guest_attaches_and_registers_only_matching_processes(self):
        env = self.attach({4242: (1, "99", "S"), 4300: (1, "other", "S")})
        self.assertEqual(env.pid, 4242)
        self.assertEqual(env.stack.owned, {4242: "99"})

    def test_gone_reused_foreign_zombie_are_refused(self):
        for rec, ok in (({}, True), ({4242: (1, "100", "S")}, True), ({4242: (1, "99", "S")}, False), ({4242: (1, "99", "Z")}, True)):
            with self.assertRaises(g.TrialError):
                self.attach(rec, ok)

    def test_down_may_attach_to_a_gone_guest_with_nothing_owned(self):
        env = self.attach({}, True, allow_gone=True)
        self.assertEqual(env.stack.owned, {})

    def test_bad_registry_is_refused(self):
        (Path(self.tmp.name) / g.OWNED).write_text("{bad")
        with self.assertRaises(g.TrialError):
            self.attach({})
        g.write_private(Path(self.tmp.name) / g.OWNED, {"pid": "x"})
        with self.assertRaises(g.TrialError):
            self.attach({})

    def test_no_guest_command_or_vault_read_before_ownership_is_proved(self):
        argv = ["--out", self.tmp.name, "call", "read"]
        for rec in ({}, {4242: (1, "100", "S")}):
            with mock.patch.object(e, "process_record", lambda pid, rec=rec: rec.get(pid)), mock.patch.object(v, "proc_ok", lambda p, pid: True), \
                    mock.patch.object(g.Trial, "run", side_effect=AssertionError("guest command")), \
                    mock.patch.object(g, "load_creds", side_effect=AssertionError("vault")):
                rc, out, err = run_captured(g.main, argv)
            self.assertNotEqual(rc, 0)
            self.assertIn("STOPPED", err)


class GuestCall(unittest.TestCase):
    """Trial.call against a fake guest file system: every command gets its own output directory."""

    class Guest:
        def __init__(self, fs, reply):
            self.fs, self.reply, self.dirs = fs, reply, []

        def __call__(self, argv, timeout=30):
            if argv[:2] == ["/usr/local/bin/as-deck", "mkdir"] and argv[-1].endswith(tuple("0123456789abcdef")) and "-p" not in argv:
                self.dirs.append(argv[-1])
                return 0, "", ""
            if "env" in argv:
                out = next(x for x in argv if x.startswith("DECK_OUT=")).split("=", 1)[1]
                if self.reply is not None:
                    self.fs[out + "/reply.json"] = self.reply
                return 0, "", ""
            if argv[0] == "/bin/cat":
                return (0, self.fs[argv[1]], "") if argv[1] in self.fs else (1, "", "")
            return 0, "", ""

    def trial(self, fs, reply):
        t = g.Trial(SimpleNamespace(out="/tmp/x-unused", base=None, fixture_port=0, lock=None, min_avail_mib=1, max_load=1.0, keep_disk=False))
        t.run = self.Guest(fs, reply)
        return t

    def test_two_commands_never_share_an_output_directory_and_a_stale_reply_is_not_read(self):
        fs = {}
        ok = json.dumps({"ok": True, "result": {"content": [{"text": "stale page"}]}})
        first = self.trial(fs, ok)
        self.assertEqual(first.call("read"), (False, "stale page"))
        second = self.trial(fs, None)
        err, text = second.call("read")
        self.assertTrue(err)
        self.assertNotIn("stale", text)
        self.assertNotEqual(first.run.dirs, second.run.dirs)

    def test_a_non_json_reply_is_a_stable_error(self):
        err, text = self.trial({}, "<html>mail first.user@gmail.com").call("read")
        self.assertTrue(err)
        self.assertNotIn("first.user", text)


# ---------- output boundary ----------

class FakeCallEnv:
    """env.call scripted: eval host probes pop from `hosts`; identity checks (`step` with `expect js`) answer from `checks`;
    other tools return `result`."""

    def __init__(self, hosts, result=(False, "INBOX first.user@gmail.com secret subject"), checks=None):
        self.hosts, self.result, self.checks, self.tools, self.check_calls = list(hosts), result, checks or {}, [], []
        self.a = SimpleNamespace(keep_disk=False)

    def call(self, tool, **kw):
        if tool == "eval" and kw.get("js") == "location.hostname":
            h = self.hosts.pop(0)
            return (True, "boom") if h is None else (False, h)
        if tool == "step" and "expect js" in kw.get("steps", ""):
            self.check_calls.append(kw)
            return self.checks.get("A" if "GMAIL_A_USER" in kw["steps"] else "B", (True, "no"))
        self.tools.append((tool, kw))
        return self.result


# The real compact receipts of bare: a failed expectation is NOT an error flag. Only the text tells.
PASS_, FAIL_ = (False, "ok (12 ms)"), (False, "FAILED expect: the script gave false: first.user@gmail.com Sup3r-Secret-pw (time limit 8000 ms)")


class OutputBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.argv = ["--out", self.tmp.name]

    def run_cmd(self, env, *args, creds=CREDS):
        with mock.patch.object(g, "attach", lambda a, **k: env), mock.patch.object(g, "load_creds", lambda items, run=None: creds), \
                mock.patch.object(g, "read_refs", lambda a: ({"A": "ia", "B": "ib"}, SEL)):
            return run_captured(g.main, self.argv + list(args))

    def test_plain_page_is_printed_sanitized(self):
        env = FakeCallEnv(["accounts.google.com", "accounts.google.com"])
        rc, out, _ = self.run_cmd(env, "call", "read")
        self.assertEqual(rc, 0)
        self.assertIn("<A>", out)
        self.assertNotIn("first.user", out)

    def test_host_probe_failure_before_withholds_every_page_tool(self):
        for tool in ("read", "snapshot", "eval", "screenshot", "find", "step", "run_steps", "act"):
            env = FakeCallEnv([None, "accounts.google.com"])
            rc, out, err = self.run_cmd(env, "call", tool)
            self.assertEqual(env.tools, [], tool)
            self.assertNotIn("INBOX", out + err)

    def test_mailbox_before_allows_no_mutating_or_reading_tool(self):
        for tool in ("act", "step", "run_steps", "eval", "read", "snapshot", "screenshot", "find", "network", "storage", "console"):
            env = FakeCallEnv(["mail.google.com", "mail.google.com"])
            rc, out, err = self.run_cmd(env, "call", tool)
            self.assertEqual(env.tools, [], tool)
            self.assertNotIn("INBOX", out + err)

    def test_navigation_into_the_mailbox_withholds_result_and_error(self):
        for result in ((False, "INBOX first.user@gmail.com secret subject"), (True, "INBOX secret subject failed")):
            env = FakeCallEnv(["accounts.google.com", "mail.google.com"], result)
            rc, out, err = self.run_cmd(env, "call", "open", '{"url": "https://mail.google.com/"}')
            self.assertEqual(len(env.tools), 1)
            self.assertNotIn("INBOX", out + err)
            self.assertNotIn("first.user", out + err)
            self.assertIn("WITHHELD", out)

    def test_navigation_out_of_the_mailbox_withholds_result(self):
        env = FakeCallEnv(["mail.google.com", "accounts.google.com"], (False, "Sign in INBOX leftovers"))
        rc, out, err = self.run_cmd(env, "call", "open", '{"url": "https://accounts.google.com/"}')
        self.assertEqual(len(env.tools), 1)
        self.assertNotIn("INBOX", out + err)

    def test_host_probe_failure_after_withholds(self):
        env = FakeCallEnv(["accounts.google.com", None])
        rc, out, err = self.run_cmd(env, "call", "read")
        self.assertNotIn("INBOX", out + err)
        self.assertIn("WITHHELD", out)

    def test_save_state_and_close_are_allowed_inside_the_mailbox_without_output(self):
        for tool in ("save_state", "close"):
            env = FakeCallEnv(["mail.google.com", "mail.google.com"], (False, "INBOX"))
            rc, out, err = self.run_cmd(env, "call", tool)
            self.assertEqual(len(env.tools), 1)
            self.assertNotIn("INBOX", out + err)

    # identity
    def test_identity_reports_only_the_verified_account(self):
        for checks, want in (({"A": PASS_, "B": FAIL_}, "A"), ({"A": FAIL_, "B": PASS_}, "B")):
            env = FakeCallEnv(["mail.google.com"], checks=checks)
            rc, out, err = self.run_cmd(env, "identity")
            self.assertEqual(json.loads(out), {"identity": want, "host_class": "mailbox"})
            self.assertEqual(rc, 0)
            self.assertNotIn("first.user", out + err)
            self.assertNotIn(SECRET, out + err)

    def test_identity_neither_match_is_unknown_and_both_is_ambiguous(self):
        for checks, want in (({"A": FAIL_, "B": FAIL_}, "unknown"), ({"A": PASS_, "B": PASS_}, "ambiguous")):
            env = FakeCallEnv(["mail.google.com"], checks=checks)
            rc, out, err = self.run_cmd(env, "identity")
            self.assertEqual(json.loads(out)["identity"], want)
            self.assertNotEqual(rc, 0)

    def test_identity_off_the_mailbox_or_with_a_failed_host_probe_runs_no_check(self):
        for hosts, want in ((["accounts.google.com"], "blocked"), ([None], "unknown"), (["mail.google.com.evil.example"], "blocked")):
            env = FakeCallEnv(hosts, checks={"A": PASS_, "B": PASS_})
            rc, out, err = self.run_cmd(env, "identity")
            self.assertEqual(json.loads(out)["identity"], want)
            self.assertEqual(env.check_calls, [])
            self.assertNotEqual(rc, 0)

    def test_identity_check_sends_only_placeholders_and_reads_no_vault(self):
        env = FakeCallEnv(["mail.google.com"], checks={"A": PASS_, "B": FAIL_})
        with mock.patch.object(g, "attach", lambda a, **k: env), mock.patch.object(g, "load_creds", side_effect=AssertionError("vault")), \
                mock.patch.object(g, "read_refs", lambda a: ({"A": "ia", "B": "ib"}, SEL)):
            rc, out, err = run_captured(g.main, self.argv + ["identity"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(env.check_calls), 2)
        for kw in env.check_calls:
            self.assertEqual(set(kw), {"steps", "compact"})
            self.assertTrue("${GMAIL_A_USER}" in kw["steps"] or "${GMAIL_B_USER}" in kw["steps"])
            for s in ("first.user", "second.user", SECRET, "other-pw-9"):
                self.assertNotIn(s, kw["steps"])

    def test_only_the_exact_success_receipt_counts_as_a_pass(self):
        for res in ((False, "ok (0 ms)"), (False, "ok (1234 ms)\n")):
            self.assertTrue(g.check_passed(res), res)
        for res in ((True, "ok (5 ms)"), (False, "FAILED expect: the script gave false: x"), (False, ""), (False, "ok"), (False, "ok (5 ms) first.user@gmail.com"),
                    (False, "not ok (5 ms)"), (False, "no variable or secret named GMAIL_A_USER"), (False, None)):
            self.assertFalse(g.check_passed(res), res)

    def test_a_failed_expectation_with_no_error_flag_is_not_a_match(self):
        env = FakeCallEnv(["mail.google.com"], checks={"A": FAIL_, "B": FAIL_})
        rc, out, err = self.run_cmd(env, "identity")
        self.assertEqual(json.loads(out)["identity"], "unknown")
        self.assertNotEqual(rc, 0)

    def test_identity_check_errors_and_raw_results_never_print(self):
        env = FakeCallEnv(["mail.google.com"], checks={"A": (True, f"expect js {ACC['A']} {SECRET} INBOX"), "B": (False, f"INBOX {ACC['B']}")})
        rc, out, err = self.run_cmd(env, "identity")
        for s in ("INBOX", "first.user", "second.user", SECRET, "expect js"):
            self.assertNotIn(s, out + err)

    def test_identity_without_a_configured_control_is_blocked_and_runs_nothing(self):
        env = FakeCallEnv(["mail.google.com"], checks={"A": PASS_})
        with mock.patch.object(g, "attach", lambda a, **k: env), mock.patch.object(g, "read_refs", lambda a: ({"A": "ia", "B": "ib"}, None)):
            rc, out, _ = run_captured(g.main, self.argv + ["identity"])
        self.assertEqual(json.loads(out)["identity"], "blocked")
        self.assertEqual(env.check_calls, [])
        self.assertNotEqual(rc, 0)

    def test_a_selector_that_could_expand_a_secret_is_refused(self):
        for bad in ("a[href*='${GMAIL_A_PASS}']", "a\nb", "", "x" * 500):
            env = FakeCallEnv(["mail.google.com"], checks={"A": PASS_})
            with mock.patch.object(g, "attach", lambda a, **k: env), mock.patch.object(g, "read_refs", lambda a, bad=bad: ({"A": "ia", "B": "ib"}, bad)):
                rc, out, _ = run_captured(g.main, self.argv + ["identity"])
            self.assertEqual(json.loads(out)["identity"], "blocked", bad)
            self.assertEqual(env.check_calls, [])

    def test_controls_prints_only_whitelisted_counts_and_never_text(self):
        raw = json.dumps({"host": "mail.google.com", "proto": "https:", "ready": "complete", "u_path": True,
                          "counts": {"all": 900, "aria_label": 40, "aria_at": 1, "label_google_account": 1, "title": "INBOX first.user@gmail.com"},
                          "text": "INBOX secret subject first.user@gmail.com", "extra": 5})
        env = FakeCallEnv([])
        env.call = lambda tool, **kw: (False, raw)
        rc, out, err = self.run_cmd(env, "controls")
        d = json.loads(out)
        self.assertEqual(d["host_class"], "mailbox")
        self.assertEqual(d["counts"], {"all": 900, "aria_label": 40, "aria_at": 1, "label_google_account": 1})
        self.assertNotIn("INBOX", out + err)
        self.assertNotIn("first.user", out + err)
        self.assertNotIn("text", d)

    def test_controls_on_another_host_or_probe_error_shows_nothing(self):
        for probe in ((False, json.dumps({"host": "accounts.google.com", "proto": "https:", "counts": {"all": 3}})), (True, "INBOX x")):
            env = FakeCallEnv([])
            env.call = lambda tool, probe=probe, **kw: probe
            rc, out, err = self.run_cmd(env, "controls")
            self.assertNotIn("counts", json.loads(out))
            self.assertNotIn("INBOX", out + err)

    # switch
    def test_switch_navigates_by_index_and_succeeds_only_when_identity_is_verified(self):
        env = FakeCallEnv(["mail.google.com"], (False, "INBOX private"), checks={"A": FAIL_, "B": PASS_})
        rc, out, err = self.run_cmd(env, "switch", "--to", "B", "--index", "1")
        self.assertEqual(env.tools, [("step", {"steps": "goto https://mail.google.com/mail/u/1/", "compact": True})])
        self.assertEqual(json.loads(out), {"switched": True, "identity": "B", "host_class": "mailbox"})
        self.assertEqual(rc, 0)
        self.assertNotIn("INBOX", out + err)

    def test_switch_mismatch_is_a_failure_and_prints_no_raw_result(self):
        env = FakeCallEnv(["mail.google.com"], (True, "INBOX private error"), checks={"A": PASS_, "B": FAIL_})
        rc, out, err = self.run_cmd(env, "switch", "--to", "B", "--index", "1")
        self.assertEqual(json.loads(out)["switched"], False)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("INBOX", out + err)

    def test_switch_needs_a_small_index(self):
        env = FakeCallEnv([])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_cmd(env, "switch", "--to", "B", "--index", "99")

    # add-account and restore
    def test_add_account_is_one_fixed_goto_in_the_existing_session_with_no_raw_output(self):
        for hosts, result, host_class in ((["accounts.google.com"], (False, "Sign in INBOX"), "other"), (["mail.google.com"], (True, "INBOX boom"), "mailbox")):
            env = FakeCallEnv(hosts, result)
            rc, out, err = self.run_cmd(env, "add-account")
            self.assertEqual(env.tools, [("step", {"steps": f"goto {g.ADD_ACCOUNT_URL}", "compact": True})])
            self.assertEqual(json.loads(out)["host_class"], host_class)
            self.assertNotIn("INBOX", out + err)
            self.assertNotIn("http", out + err)

    def test_add_account_url_is_the_google_add_session_page_only(self):
        self.assertRegex(g.ADD_ACCOUNT_URL, r"^https://accounts\.google\.com/AddSession\?")
        self.assertNotRegex(g.ADD_ACCOUNT_URL, r"[@\s]|\$\{")

    def test_generic_step_stays_blocked_on_the_mailbox(self):
        env = FakeCallEnv(["mail.google.com", "mail.google.com"])
        rc, out, err = self.run_cmd(env, "call", "step", '{"steps": "goto https://accounts.google.com/AddSession"}')
        self.assertEqual(env.tools, [])

    def test_restore_is_one_explicit_state_open_with_no_raw_output(self):
        env = FakeCallEnv(["mail.google.com"], (False, "INBOX private"))
        rc, out, err = self.run_cmd(env, "restore")
        self.assertEqual(env.tools, [("open", {"url": g.RESTORE_URL, "state": g.PROFILE})])
        self.assertEqual(json.loads(out), {"restored": True, "host_class": "mailbox"})
        self.assertNotIn("INBOX", out + err)
        env = FakeCallEnv(["unknown?"], (True, "INBOX boom"))
        rc, out, err = self.run_cmd(env, "restore")
        self.assertEqual(json.loads(out)["restored"], False)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("INBOX", out + err)


class OperationLock(unittest.TestCase):
    def test_a_second_command_is_refused_while_one_holds_the_trial_lock(self):
        with tempfile.TemporaryDirectory() as d:
            held = g.op_lock(SimpleNamespace(out=d))
            try:
                with mock.patch.object(g, "attach", side_effect=AssertionError("must not attach")):
                    for sub in (["call", "read"], ["identity"], ["restart-mcp"], ["add-account"], ["restore"], ["down"]):
                        argv = ["--out", d] + (["--base", "/b.qcow2"] if sub == ["down"] else []) + sub
                        rc, out, err = run_captured(g.main, argv)
                        self.assertEqual(rc, 4, sub)
                        self.assertIn("busy", err)
            finally:
                held.close()


def run_step_js(steps, env, page):
    """Evaluate the `expect js` of a step text the way bare does (unquote, expand ${NAME}), against a stub page, in node."""
    line = steps.splitlines()[-1]
    assert line.startswith('expect js "'), line
    out, it = [], iter(line[len('expect js "'):])
    for c in it:
        if c == '"':
            break
        out.append(next(it) if c == "\\" else c)
    js = "".join(out)
    for k, val in env.items():
        js = js.replace("${" + k + "}", val)
    assert "${" not in js
    stub = ("globalThis.location = %s; const els = %s.map(l => ({getAttribute: n => n === 'aria-label' ? l : null}));"
            "globalThis.document = {querySelectorAll: sel => (globalThis.seen = sel, els)}; process.stdout.write(JSON.stringify([eval(%s), globalThis.seen]));"
            % (json.dumps({"hostname": page.get("host", "mail.google.com"), "protocol": page.get("proto", "https:")}),
               json.dumps(page["labels"]), json.dumps(js)))
    r = subprocess.run(["node", "-e", stub], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node is needed to run the page check")
class PageCheck(unittest.TestCase):
    VARS = {"GMAIL_A_USER": ACC["A"], "GMAIL_B_USER": ACC["B"]}

    def check(self, who, labels, **page):
        return run_step_js(g.account_check_steps(SEL, who), self.VARS, {"labels": labels, **page})

    def test_a_complete_email_token_of_the_wanted_account_passes_case_insensitively(self):
        self.assertEqual(self.check("A", ["Google Account: X (first.user@gmail.com)"]), [True, SEL])
        self.assertEqual(self.check("A", ["Google Account: X (First.User@GMAIL.com)"])[0], True)
        self.assertEqual(self.check("B", ["Google Account: X (first.user@gmail.com)"])[0], False)

    def test_tokens_must_be_complete_at_both_ends(self):
        for label in ("(xfirst.user@gmail.com)", "(first.user@gmail.com.evil.org)", "(first.user@gmail.comx)", "(firstXuser@gmail.com)", "(user@gmail.com)",
                      "first.user", "gmail.com", ""):
            self.assertEqual(self.check("A", [label])[0], False, label)

    def test_both_accounts_in_one_label_pass_both_checks(self):
        label = f"{ACC['A']} {ACC['B']}"
        self.assertEqual((self.check("A", [label])[0], self.check("B", [label])[0]), (True, True))

    def test_exactly_one_control_on_the_mailbox_over_https(self):
        good = "Google Account: X (first.user@gmail.com)"
        self.assertEqual(self.check("A", [good, good])[0], False)
        self.assertEqual(self.check("A", [])[0], False)
        for page in ({"host": "accounts.google.com"}, {"host": "mail.google.com.evil.example"}, {"proto": "http:"}):
            self.assertEqual(self.check("A", [good], **page)[0], False, page)

    def test_the_step_is_one_quoted_line_with_only_placeholders_and_a_bounded_wait(self):
        for who in ("A", "B"):
            steps = g.account_check_steps(SEL, who)
            self.assertRegex(steps, r'^timeout [0-9]{3,5}\nexpect js "[^\n]*"$')
            self.assertIn("${GMAIL_%s_USER}" % who, steps)
            self.assertNotIn("PASS", steps)
            self.assertNotIn("gmail.com'", steps)

    def test_a_selector_with_quotes_is_escaped_for_the_step_line(self):
        sel = 'a[href*="SignOutOptions"]'
        self.assertEqual(run_step_js(g.account_check_steps(sel, "A"), self.VARS, {"labels": [ACC["A"]]}), [True, sel])


@unittest.skipUnless(shutil.which("node"), "node is needed to run the page check")
class UniqueCheck(unittest.TestCase):
    """One expect for all registered addresses: pass only when exactly one registered address is in the label and it is the wanted one."""
    VARS = {"GMAIL_A_USER": ACC["A"], "GMAIL_B_USER": ACC["B"], "GMAIL_C_USER": "third.user@gmail.com"}
    GOOD = "Google Account: X (first.user@gmail.com)"

    def check(self, who, labels, env=None, **page):
        return run_step_js(g.unique_check_steps(SEL, who, "ABC"), env or self.VARS, {"labels": labels, **page})

    def test_wanted_alone_passes_for_every_account(self):
        for who, addr in (("A", "first.user@gmail.com"), ("B", "second.user@gmail.com"), ("C", "Third.User@GMAIL.com")):
            self.assertEqual(self.check(who, [f"Google Account: X ({addr})"]), [True, SEL], who)

    def test_another_registered_account_fails(self):
        self.assertEqual(self.check("B", [self.GOOD])[0], False)
        self.assertEqual(self.check("C", [self.GOOD])[0], False)

    def test_two_registered_addresses_in_one_label_fail_for_each_wanted(self):
        label = f"{ACC['A']} third.user@gmail.com"
        for who in "ABC":
            self.assertEqual(self.check(who, [label])[0], False, who)

    def test_duplicate_registered_address_fails_closed(self):
        dup = {**self.VARS, "GMAIL_B_USER": ACC["A"]}
        for who in "ABC":
            self.assertEqual(self.check(who, [self.GOOD], dup)[0], False, who)

    def test_unknown_foreign_and_partial_tokens_fail(self):
        for label in ("stranger@example.org", "(xfirst.user@gmail.com)", "(first.user@gmail.com.evil.org)", "(first.user@gmail.comx)", ""):
            self.assertEqual(self.check("A", [label])[0], False, label)
        self.assertEqual(self.check("A", [f"{self.GOOD} stranger@example.org"])[0], True)  # an unregistered address is not a registered match

    def test_one_control_and_https_mailbox_only(self):
        self.assertEqual(self.check("A", [self.GOOD, self.GOOD])[0], False)
        self.assertEqual(self.check("A", [])[0], False)
        for page in ({"host": "accounts.google.com"}, {"host": "mail.google.com.evil.example"}, {"proto": "http:"}):
            self.assertEqual(self.check("A", [self.GOOD], **page)[0], False, page)

    def test_the_step_holds_only_placeholders_and_a_bounded_wait(self):
        steps = g.unique_check_steps(SEL, "B", "ABC")
        self.assertRegex(steps, r'^timeout [0-9]{3,5}\nexpect js "[^\n]*"$')
        for w in "ABC":
            self.assertIn("${GMAIL_%s_USER}" % w, steps)
        self.assertNotIn("PASS", steps)
        self.assertNotIn("@", steps.replace("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", ""))

    def test_the_wanted_account_must_be_registered(self):
        for who, labels in (("D", "ABC"), ("A", ""), ("A", "BC"), ("A", "AA")):
            with self.assertRaises(g.TrialError):
                g.unique_check_steps(SEL, who, labels)


class VerifyIdentityOneCheck(unittest.TestCase):
    class Env:
        def __init__(self, host="mail.google.com", ok=True):
            self.host, self.ok, self.calls = host, ok, []

        def call(self, tool, **kw):
            self.calls.append((tool, kw))
            return (False, self.host) if tool == "eval" else (False, "ok (5 ms)" if self.ok else "expectation failed")

    def test_wanted_costs_one_expect_and_one_host_probe(self):
        env = self.Env()
        r = g.verify_identity(env, SEL, "ABC", "B")
        self.assertEqual(r["identity"], "B")
        self.assertEqual([t for t, _ in env.calls], ["eval", "step"])
        self.assertIn("${GMAIL_C_USER}", env.calls[1][1]["steps"])

    def test_failed_check_is_unknown_never_the_wanted_account(self):
        self.assertEqual(g.verify_identity(self.Env(ok=False), SEL, "ABC", "A")["identity"], "unknown")

    def test_foreign_page_and_bad_selector_run_no_expect(self):
        env = self.Env(host="accounts.google.com")
        self.assertEqual(g.verify_identity(env, SEL, "ABC", "A")["identity"], "blocked")
        self.assertEqual([t for t, _ in env.calls], ["eval"])
        self.assertEqual(g.verify_identity(self.Env(), "a${X}", "ABC", "A")["identity"], "blocked")

    def test_without_a_wanted_account_every_account_is_still_checked(self):
        env = self.Env()
        self.assertEqual(g.verify_identity(env, SEL, "AB")["identity"], "ambiguous")
        self.assertEqual([t for t, _ in env.calls], ["eval", "step", "step"])


class Resume(unittest.TestCase):
    """cmd_resume against a temporary retained trial directory and a fake guest."""
    BASE_ID = "a90de2e085a7ad52"
    CAND = hashlib.sha256(b"pinned").hexdigest()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "gt"
        priv = self.out / "private"
        priv.mkdir(parents=True)
        vm = self.out / "computers" / sp.BOT
        vm.mkdir(parents=True)
        for d in (self.out, priv):
            d.chmod(0o700)
        g.write_private(self.out / "manifest-up.json", {"gates": {"guest_internet_recorded_in_vm_state": True, "bare_candidate_installed_and_ready": True,
                         "base_frozen_and_verified": True, "both_accounts_delivered_as_protected_secrets": True},
                         "identity": {"base": {"id": self.BASE_ID}, "candidate": {"sha256": self.CAND}}})
        g.write_private(self.out / g.OWNED, {"pid": 4242, "processes": {"4242": "99"}})
        g.write_private(self.out / g.REFS, {"items": {"A": "ia", "B": "ib"}, "account_control_selector": SEL})
        (priv / sp.PINNED_NAME).write_bytes(b"pinned")
        (priv / sp.PINNED_NAME).chmod(0o500)
        (vm / "disk.qcow2").write_bytes(b"disk")
        (vm / "disk.json").write_text(json.dumps({"kind": "overlay", "base": {"id": self.BASE_ID, "path": "/b.qcow2", "fingerprint": {}}}))
        (vm / "executor.json").write_text(json.dumps({"bot_id": sp.BOT, "guest_id": "gid"}))
        self.disk = vm / "disk.qcow2"
        self.up_before = (self.out / "manifest-up.json").read_bytes()
        self.a = SimpleNamespace(out=str(self.out), lock=str(self.root / "lock"), base="/b.qcow2", expect_bare_sha256=self.CAND, min_avail_mib=6144,
                                 max_load=float("inf"), account_control_selector=None, fixture_port=0, keep_disk=True, bare_candidate=None)
        self.env = None

    def run_resume(self, running=(), base_ok=True, base_id=None, mode="0o444", pin=False, **env_kw):
        def mk(a):
            self.env = FakeResumeEnv(a, self.out, **env_kw)
            return self.env

        with mock.patch.object(g, "Trial", mk), mock.patch.object(g, "running_vms", lambda p: list(running)), \
                mock.patch.object(g, "free_port", lambda: 5555), mock.patch.object(g, "verify_identity", lambda env, sel: env.verified_identity()), \
                mock.patch.object(g.time, "sleep", lambda s: None), \
                mock.patch.object(e, "base_identity", lambda b: {"id": base_id or self.BASE_ID, "mode": mode, "virtual_size": 1, "sha256": "x"}), \
                mock.patch.object(e, "verify_base", lambda b: base_ok), mock.patch.object(v, "check_disk", lambda p: None), \
                mock.patch.object(g, "load_creds", side_effect=AssertionError("vault")), mock.patch.object(g, "deliver_all", side_effect=AssertionError("deliver")), \
                mock.patch.object(secretchan, "deliver", side_effect=AssertionError("deliver")), mock.patch.object(v, "install_executor", side_effect=AssertionError("rebind")), \
                mock.patch.object(v, "create_overlay", side_effect=AssertionError("overlay")), mock.patch.object(sp, "pin_candidate", **({"wraps": sp.pin_candidate} if pin else {"side_effect": AssertionError("pin")})):
            res = run_captured(g.cmd_resume, self.a)
        text = res[1] + res[2]
        for f in self.out.rglob("manifest-resume.json"):
            text += f.read_text()
        for s_ in (SECRET, "first.user", "second.user", "INBOX"):
            self.assertNotIn(s_, text)
        if not pin:
            self.assertEqual((self.out / "manifest-up.json").read_bytes(), self.up_before)
        return res

    def assertNothingStarted(self, rc, disk=True):
        self.assertEqual(rc, 2)
        self.assertEqual((self.env.starts, self.env.cleaned), (0, 0))
        self.assertEqual(self.disk.exists(), disk)

    def test_happy_path_starts_the_existing_overlay_restores_state_and_proves_a(self):
        rc, out, _ = self.run_resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.env.log, ["start", "ready", "sha", "files", "open", "identity"])
        self.assertEqual(self.env.calls, [("open", {"url": g.RESTORE_URL, "state": g.PROFILE})])
        self.assertEqual(self.env.cleaned, 0)
        owned = json.loads((self.out / g.OWNED).read_text())
        self.assertEqual(owned["pid"], 777)
        self.assertEqual(owned["processes"], {"777": "birth"})
        self.assertTrue((self.out / "private" / "owned-before-resume.json").exists())
        res = json.loads((self.out / "private" / "manifest-resume.json").read_text())
        self.assertTrue(all(res["gates"].values()))
        self.assertEqual(res["identity"], "A")
        self.assertTrue(json.loads(out.splitlines()[-1])["guest_running"])

    def use_candidate(self, data=b"newer"):
        f = self.root / "bare-mcp"
        f.write_bytes(data)
        f.chmod(0o755)
        self.a.bare_candidate, self.a.expect_bare_sha256 = str(f), hashlib.sha256(data).hexdigest()
        return self.a.expect_bare_sha256

    def test_a_guest_with_another_bare_mcp_gets_the_file_installed_once_and_the_record_follows(self):
        new = self.use_candidate()
        rc, out, _ = self.run_resume(pin=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.env.installs, [(self.out / "private" / sp.PINNED_NAME, new)])
        self.assertEqual(self.env.log, ["start", "ready", "sha", "install", "files", "open", "identity"])
        self.assertEqual((self.out / "private" / sp.PINNED_NAME).read_bytes(), b"newer")
        self.assertEqual(json.loads((self.out / "manifest-up.json").read_text())["identity"]["candidate"]["sha256"], new)
        self.assertTrue(all(json.loads((self.out / "private" / "manifest-resume.json").read_text())["gates"].values()))

    def test_a_guest_that_already_holds_the_file_installs_nothing(self):
        new = self.use_candidate()
        rc, out, _ = self.run_resume(pin=True, sha=new)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.env.installs, [])
        self.assertEqual((self.out / "private" / sp.PINNED_NAME).read_bytes(), b"newer")  # the stale pinned copy still follows the file

    def test_a_matching_record_and_guest_change_nothing_at_all(self):
        self.use_candidate(b"pinned")
        rc, out, _ = self.run_resume(pin=False)  # a pin would raise
        self.assertEqual((rc, self.env.installs), (0, []), out)

    def test_a_missing_file_or_a_digest_that_differs_stops_before_anything_starts(self):
        new = self.use_candidate()
        (self.root / "bare-mcp").write_bytes(b"other")
        rc, _, err = self.run_resume(pin=True)
        self.assertNothingStarted(rc)
        self.assertIn("bare-mcp file", err)
        (self.root / "bare-mcp").unlink()
        rc, _, err = self.run_resume(pin=True)
        self.assertNothingStarted(rc)
        self.assertEqual(self.env.installs, [])

    def test_the_resume_lock_is_separate_and_busy_stops_everything(self):
        held = e.take_lock(self.a.lock)
        try:
            rc, out, err = self.run_resume()
        finally:
            held.close()
        self.assertEqual(rc, 4)
        self.assertIsNone(self.env)

    def test_memory_guard_stays_on_but_the_load_limit_may_be_infinite(self):
        rc, _, err = self.run_resume(pressure="MemAvailable 100 MiB is below 6144 MiB")
        self.assertEqual(rc, 3)
        self.assertEqual((self.env.starts, self.env.cleaned), (0, 0))
        self.assertEqual(g.parse(["--out", "o", "--max-load", "inf", "--base", "b", "resume", "--expect-bare-sha256", "0" * 64]).max_load, float("inf"))
        self.assertEqual(g.parse(["--out", "o", "--base", "b", "resume", "--expect-bare-sha256", "0" * 64]).min_avail_mib, 6144)

    def test_disk_missing_or_not_a_private_regular_file_is_refused(self):
        self.disk.unlink()
        rc, _, err = self.run_resume()
        self.assertNothingStarted(rc, disk=False)
        self.disk.symlink_to(self.root / "elsewhere")
        rc, _, err = self.run_resume()
        self.assertNothingStarted(rc, disk=False)
        self.disk.unlink()
        self.disk.write_bytes(b"d")
        self.disk.chmod(0o666)
        rc, _, err = self.run_resume()
        self.assertNothingStarted(rc)

    def test_wrong_or_unverified_base_is_refused(self):
        for kw in ({"base_id": "0" * 16}, {"base_ok": False}, {"mode": "0o644"}):
            rc, _, _ = self.run_resume(**kw)
            self.assertNothingStarted(rc)
        meta = self.disk.with_suffix(".json")
        meta.write_text(json.dumps({"kind": "overlay", "base": {"id": "f" * 16}}))
        rc, _, _ = self.run_resume()
        self.assertNothingStarted(rc)

    def test_overlay_check_failure_is_refused_without_its_text(self):
        with mock.patch.object(v, "check_disk", side_effect=SystemExit(f"base changed {SECRET}")):
            def mk(a):
                self.env = FakeResumeEnv(a, self.out)
                return self.env

            with mock.patch.object(g, "Trial", mk), mock.patch.object(g, "running_vms", lambda p: []), \
                    mock.patch.object(e, "base_identity", lambda b: {"id": self.BASE_ID, "mode": "0o444"}), mock.patch.object(e, "verify_base", lambda b: True):
                rc, out, err = run_captured(g.cmd_resume, self.a)
        self.assertEqual(rc, 2)
        self.assertNotIn(SECRET, out + err)
        self.assertEqual(self.env.starts, 0)

    def test_manifest_without_an_explicit_internet_record_or_with_a_failed_gate_is_refused(self):
        for gates in ({"guest_internet_recorded_in_vm_state": False}, {"base_frozen_and_verified": False}, {}):
            m = json.loads((self.out / "manifest-up.json").read_text())
            m["gates"] = {**m["gates"], **gates} if gates else {}
            g.write_private(self.out / "manifest-up.json", m)
            self.up_before = (self.out / "manifest-up.json").read_bytes()
            rc, _, _ = self.run_resume()
            self.assertNothingStarted(rc)

    def test_candidate_digest_must_match_the_record_and_the_pinned_copy(self):
        self.a.expect_bare_sha256 = "e" * 64
        rc, _, _ = self.run_resume()
        self.assertNothingStarted(rc)
        self.a.expect_bare_sha256 = self.CAND
        (self.out / "private" / sp.PINNED_NAME).chmod(0o600)
        (self.out / "private" / sp.PINNED_NAME).write_bytes(b"other")  # the pinned copy hashes to something else
        rc, _, _ = self.run_resume()
        self.assertNothingStarted(rc)

    def test_permissions_are_checked_before_anything_starts(self):
        self.out.chmod(0o755)
        rc, _, _ = self.run_resume()
        self.assertNothingStarted(rc)
        self.out.chmod(0o700)
        (self.out / g.REFS).chmod(0o644)
        rc, _, _ = self.run_resume()
        self.assertNothingStarted(rc)

    def test_a_running_or_unowned_vm_is_refused_and_never_touched(self):
        rc, _, err = self.run_resume(running=[31337])
        self.assertNothingStarted(rc)
        self.assertIn("running", err)

    def test_a_recorded_owner_that_is_still_ours_is_a_duplicate(self):
        with mock.patch.object(e, "process_record", lambda pid: (1, "99", "S")), mock.patch.object(v, "proc_ok", lambda p, pid: True):
            rc, _, err = self.run_resume()
        self.assertNothingStarted(rc)

    def test_a_wrong_executor_binding_is_refused(self):
        (self.disk.parent / "executor.json").write_text(json.dumps({"bot_id": "someone-else", "guest_id": "gid"}))
        rc, _, _ = self.run_resume()
        self.assertNothingStarted(rc)

    def test_failures_after_the_start_clean_up_and_keep_the_authenticated_disk(self):
        cases = {"not ready": {"not_ready": True}, "wrong candidate": {"sha": "0" * 64}, "network": {"internet": False},
                 "start error": {"boom_start": RuntimeError(SECRET)}, "secret file": {"secret_rows": []}, "state file": {"state_rows": []}}
        for name, kw in cases.items():
            (self.out / "private" / "manifest-resume.json").unlink(missing_ok=True)
            rc, out, err = self.run_resume(**kw)
            self.assertEqual(rc, 1, name)
            self.assertEqual(self.env.cleaned, 1, name)
            self.assertTrue(self.env.keep_at_cleanup, name)
            self.assertTrue(self.disk.exists(), name)
            self.assertEqual(self.env.calls, [], name)
            self.assertTrue((self.out / "private" / "manifest-resume.json").exists(), name)

    def test_a_failed_cleanup_is_reported_without_its_text_and_still_keeps_the_disk(self):
        rc, out, err = self.run_resume(not_ready=True, cleanup_boom=RuntimeError(SECRET))
        self.assertEqual(rc, 1)
        self.assertTrue(self.disk.exists())

    def test_real_cleanup_with_keep_disk_never_deletes_the_disk(self):
        env = g.Trial(SimpleNamespace(out=str(self.out), base=None, fixture_port=0, lock=None, min_avail_mib=1, max_load=1.0, keep_disk=True))
        with mock.patch.object(env.stack, "cleanup", lambda: []), mock.patch.object(env.stack, "vm_dirs", lambda: [self.disk.parent]):
            self.assertEqual(env.cleanup(), [])
        self.assertTrue(self.disk.exists())

    def test_session_gates_leave_the_guest_running_for_a_person(self):
        for kw in ({"identity": "unknown"}, {"open_error": True}):
            rc, out, err = self.run_resume(**kw)
            self.assertEqual(rc, 1)
            self.assertEqual(self.env.cleaned, 0)
            self.assertTrue(json.loads(out.splitlines()[-1])["guest_running"])
            self.assertEqual(json.loads((self.out / "private" / "manifest-resume.json").read_text())["identity"], kw.get("identity", "unknown"))

    def test_the_selector_option_is_stored_privately_and_read_first(self):
        self.a.account_control_selector = '[aria-label*="Google Account"]'
        self.run_resume()
        self.assertEqual(g.read_refs(self.a)[1], '[aria-label*="Google Account"]')
        self.assertEqual(json.loads((self.out / g.REFS).read_text())["account_control_selector"], SEL)


class FakeResumeEnv:
    def __init__(self, a, out, pressure=None, not_ready=False, sha=None, internet=True, boom_start=None, secret_rows=None, state_rows=None,
                 identity="A", open_error=False, cleanup_boom=None):
        self.a, self.out = a, out
        self.kw = dict(pressure=pressure, not_ready=not_ready, sha=sha, internet=internet, boom_start=boom_start, secret_rows=secret_rows,
                       state_rows=state_rows, identity=identity, open_error=open_error, cleanup_boom=cleanup_boom)
        self.log, self.calls, self.installs = [], [], []
        self.guest = sha or Resume.CAND
        self.starts = self.cleaned = 0
        self.keep_at_cleanup = None
        self.pid = None
        self.stack = SimpleNamespace(owned={})
        self.p = v.Paths(out / "computers" / sp.BOT)

    def pressure(self):
        return self.kw["pressure"]

    def host(self):
        return {}

    def head(self):
        return "head"

    def resume_guest(self):
        self.starts += 1
        self.log.append("start")
        if self.kw["boom_start"]:
            raise self.kw["boom_start"]
        self.pid = 777
        self.stack.owned[777] = "birth"
        self.p.run.mkdir(parents=True, exist_ok=True)
        self.p.vm.write_text(json.dumps({"pid": 777, "internet": self.kw["internet"]}))

    def guest_ready(self):
        self.log.append("ready")
        if self.kw["not_ready"]:
            raise v.NotReady("not_ready")

    def guest_sha(self, path):
        self.log.append("sha")
        return self.guest

    def install_candidate(self, pinned, sha256):
        self.log.append("install")
        self.installs.append((Path(pinned), sha256))
        self.guest = sha256
        return {"installed_sha256": sha256}

    def stat_files(self):
        self.log.append("files")
        ok_secret = {"path": "secrets.env", "kind": "regular file", "mode": "600", "owner": "deck", "links": 1}
        ok_state = {"path": "main.state", "kind": "regular file", "mode": "600", "owner": "deck", "links": 1}
        return (ok_secret if self.kw["secret_rows"] is None else self.kw["secret_rows"], ok_state if self.kw["state_rows"] is None else self.kw["state_rows"])

    def call(self, tool, **kw):
        self.log.append("open")
        self.calls.append((tool, kw))
        return (self.kw["open_error"], "INBOX private " + SECRET)

    def verified_identity(self):
        self.log.append("identity")
        return {"identity": self.kw["identity"], "host_class": "mailbox"}

    def cleanup(self):
        self.cleaned += 1
        self.keep_at_cleanup = self.a.keep_disk
        if self.kw["cleanup_boom"]:
            raise self.kw["cleanup_boom"]
        return []


ADDR_C = "third.user@gmail.com"
CREDS3 = {**CREDS, "C": (ADDR_C, "third-pw-7")}
ITEMS_AB = {"A": "ia", "B": "ib"}


class AccountCRefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = SimpleNamespace(out=self.tmp.name)
        g.write_private(Path(self.tmp.name) / g.REFS, {"items": ITEMS_AB, "account_control_selector": SEL})

    def test_without_a_registration_only_a_and_b_exist(self):
        self.assertEqual(g.read_refs(self.a), (ITEMS_AB, SEL))

    def test_a_registered_c_is_read_from_its_own_private_file_and_the_first_record_is_untouched(self):
        before = (Path(self.tmp.name) / g.REFS).read_bytes()
        g.write_private(Path(self.tmp.name) / g.ACCOUNT_C, {"item": "ic"})
        self.assertEqual(g.read_refs(self.a)[0], {**ITEMS_AB, "C": "ic"})
        self.assertEqual((Path(self.tmp.name) / g.REFS).read_bytes(), before)

    def test_a_bad_c_record_is_refused(self):
        for bad in ({"item": ""}, {"item": 5}, {}, []):
            g.write_private(Path(self.tmp.name) / g.ACCOUNT_C, bad)
            with self.assertRaises(g.TrialError):
                g.read_refs(self.a)

    def test_a_vault_user_without_a_domain_becomes_the_gmail_address_and_other_accounts_stay_as_they_are(self):
        def run(cmd, **kw):
            user = {"ia": "first.user@gmail.com", "ic": "third.user"}[cmd[-1]]
            return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"username": user, "password": "pw-" + user}}))
        creds = g.load_creds({"A": "ia", "C": "ic"}, run)
        self.assertEqual((creds["A"][0], creds["C"][0]), ("first.user@gmail.com", ADDR_C))

    def test_a_third_account_with_the_same_address_as_a_or_b_is_not_distinct(self):
        def run(cmd, **kw):
            user = "first.user@gmail.com" if cmd[-1] == "ia" else "first.user"
            return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"username": user, "password": "pw-" + cmd[-1]}}))
        with self.assertRaises(g.TrialError):
            g.load_creds({"A": "ia", "C": "ic"}, run)


class ThreeAccountIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.argv = ["--out", self.tmp.name]

    def run_cmd(self, env, *args, items=None):
        with mock.patch.object(g, "attach", lambda a, **k: env), mock.patch.object(g, "load_creds", lambda i, run=None: CREDS3), \
                mock.patch.object(g, "read_refs", lambda a: (items or {**ITEMS_AB, "C": "ic"}, SEL)):
            return run_captured(g.main, self.argv + list(args))

    def env(self, checks):
        env = FakeCallEnv(["mail.google.com"], checks=checks)
        orig = env.call

        def call(tool, **kw):
            if tool == "step" and "GMAIL_C_USER" in kw.get("steps", ""):
                env.check_calls.append(kw)
                return checks["C"]
            return orig(tool, **kw)
        env.call = call
        return env

    def test_c_is_verified_by_its_own_placeholder_and_exactly_one_must_pass(self):
        for checks, want in (({"A": FAIL_, "B": FAIL_, "C": PASS_}, "C"), ({"A": PASS_, "B": FAIL_, "C": FAIL_}, "A"),
                             ({"A": FAIL_, "B": PASS_, "C": FAIL_}, "B")):
            env = self.env(checks)
            rc, out, err = self.run_cmd(env, "identity")
            self.assertEqual(json.loads(out), {"identity": want, "host_class": "mailbox"})
            self.assertEqual((rc, len(env.check_calls)), (0, 3))
            for s_ in (ADDR_C, "third-pw-7", "first.user"):
                self.assertNotIn(s_, out + err + "".join(k["steps"] for k in env.check_calls))

    def test_two_or_zero_verified_identities_are_never_one(self):
        for checks, want in (({"A": PASS_, "B": FAIL_, "C": PASS_}, "ambiguous"), ({"A": FAIL_, "B": PASS_, "C": PASS_}, "ambiguous"),
                             ({"A": PASS_, "B": PASS_, "C": PASS_}, "ambiguous"), ({"A": FAIL_, "B": FAIL_, "C": FAIL_}, "unknown")):
            rc, out, _ = self.run_cmd(self.env(checks), "identity")
            self.assertEqual(json.loads(out)["identity"], want)
            self.assertNotEqual(rc, 0)

    def test_without_a_registered_c_only_two_checks_run(self):
        env = self.env({"A": PASS_, "B": FAIL_, "C": PASS_})
        rc, out, _ = self.run_cmd(env, "identity", items=ITEMS_AB)
        self.assertEqual((json.loads(out)["identity"], len(env.check_calls)), ("A", 2))

    def test_switch_to_c_needs_the_verified_c_identity(self):
        env = self.env({"A": FAIL_, "B": FAIL_, "C": PASS_})
        rc, out, _ = self.run_cmd(env, "switch", "--to", "C", "--index", "2")
        self.assertEqual(json.loads(out), {"switched": True, "identity": "C", "host_class": "mailbox"})
        self.assertEqual(env.tools, [("step", {"steps": "goto https://mail.google.com/mail/u/2/", "compact": True})])
        env = self.env({"A": PASS_, "B": FAIL_, "C": FAIL_})
        rc, out, _ = self.run_cmd(env, "switch", "--to", "C", "--index", "2")
        self.assertEqual((json.loads(out)["switched"], rc), (False, 1))

    def test_switch_to_an_unregistered_c_stops_before_any_navigation(self):
        env = self.env({"A": FAIL_, "B": FAIL_, "C": PASS_})
        rc, out, err = self.run_cmd(env, "switch", "--to", "C", "--index", "2", items=ITEMS_AB)
        self.assertNotEqual(rc, 0)
        self.assertEqual(env.tools, [])

    def test_a_and_b_switching_is_unchanged(self):
        env = self.env({"A": FAIL_, "B": PASS_, "C": FAIL_})
        rc, out, _ = self.run_cmd(env, "switch", "--to", "B", "--index", "1", items=ITEMS_AB)
        self.assertEqual((json.loads(out)["switched"], rc, len(env.check_calls)), (True, 0, 2))


class FakeRegEnv:
    def __init__(self, secret=True, state=True):
        row = {"kind": "regular file", "mode": "600", "owner": sp.OWNER, "links": 1}
        self.rows = (dict(row) if secret else None, dict(row) if state else None)
        self.p = SimpleNamespace(qga="qga.sock")
        self.log = []

    def stat_files(self):
        self.log.append("files")
        return self.rows


class RegisterC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        g.write_private(self.out / g.REFS, {"items": ITEMS_AB, "account_control_selector": SEL})
        self.first = (self.out / g.REFS).read_bytes()
        self.delivered, self.receipts, self.log = [], [], []

    def run_reg(self, env=None, creds=None, receipt_fail=(), deliver_fail=None, address=ADDR_C, extra=()):
        env = env or FakeRegEnv()

        def deliver(qga, bot, rid, name, value):
            self.log.append(("deliver", rid, name))
            self.delivered.append((rid, name))
            if deliver_fail:
                raise secretchan.SecretError(deliver_fail)

        def receipt(qga, bot, rid):
            self.log.append(("receipt", rid))
            self.receipts.append(rid)
            if rid in receipt_fail:
                raise secretchan.SecretError("not_delivered")
            return {"ok": True}

        def load(items, run=None):
            self.log.append(("vault", tuple(sorted(items))))
            return creds or CREDS3
        with mock.patch.object(g, "attach", lambda a, **k: self.log.append("attach") or env), mock.patch.object(g, "load_creds", load), \
                mock.patch.object(v, "Qga", lambda s: "qga"), mock.patch.object(secretchan, "deliver", deliver), mock.patch.object(secretchan, "receipt", receipt):
            res = run_captured(g.main, ["--out", str(self.out), "register-c", "--item-c", "ic", "--address", address, *extra])
        for s_ in (ADDR_C, "third-pw-7", SECRET, "other-pw-9", "first.user", "second.user"):
            self.assertNotIn(s_, res[1] + res[2])
        return res

    def test_it_delivers_only_c_and_proves_a_and_b_receipts_before_and_after(self):
        rc, out, _ = self.run_reg()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.delivered, [("gmail-trial-c-user", "GMAIL_C_USER"), ("gmail-trial-c-pass", "GMAIL_C_PASS")])
        ab = [f"gmail-trial-{k}-{p}" for k in "ab" for p in ("user", "pass")]
        first_deliver = next(i for i, x in enumerate(self.log) if x[0] == "deliver")
        before = [x[1] for x in self.log[:first_deliver] if x[0] == "receipt"]
        self.assertEqual(sorted(before), sorted(ab))
        self.assertTrue(set(ab + ["gmail-trial-c-user", "gmail-trial-c-pass"]) <= set(self.receipts[len(ab):]))
        self.assertEqual(json.loads((self.out / g.ACCOUNT_C).read_text()), {"item": "ic"})
        self.assertEqual(oct((self.out / g.ACCOUNT_C).stat().st_mode & 0o777), "0o600")
        self.assertEqual((self.out / g.REFS).read_bytes(), self.first)
        self.assertEqual(json.loads(out)["registered"], True)

    def test_ownership_is_proved_before_the_vault_is_read(self):
        self.run_reg()
        self.assertEqual(self.log[0], "attach")
        self.assertLess(self.log.index("attach"), next(i for i, x in enumerate(self.log) if x[0] == "vault"))

    def test_the_vault_item_must_name_the_requested_address(self):
        rc, out, err = self.run_reg(creds={**CREDS3, "C": ("someone.else@gmail.com", "pw")})
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.delivered, [])
        self.assertFalse((self.out / g.ACCOUNT_C).exists())

    def test_a_missing_or_foreign_secret_or_state_file_stops_before_any_delivery_or_vault_read(self):
        for env in (FakeRegEnv(secret=False), FakeRegEnv(state=False)):
            self.log.clear()
            rc, *_ = self.run_reg(env)
            self.assertNotEqual(rc, 0)
            self.assertEqual([x for x in self.log if isinstance(x, tuple)], [])
            self.assertFalse((self.out / g.ACCOUNT_C).exists())

    def test_an_invalid_a_or_b_receipt_stops_before_any_delivery(self):
        rc, *_ = self.run_reg(receipt_fail=("gmail-trial-b-pass",))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.delivered, [])
        self.assertFalse((self.out / g.ACCOUNT_C).exists())

    def test_a_failed_delivery_or_receipt_registers_nothing(self):
        rc, *_ = self.run_reg(deliver_fail="guest_error")
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.out / g.ACCOUNT_C).exists())
        self.receipts.clear()
        rc, *_ = self.run_reg(receipt_fail=("gmail-trial-c-pass",))
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.out / g.ACCOUNT_C).exists())

    def test_an_already_registered_c_is_refused_with_no_vault_read(self):
        g.write_private(self.out / g.ACCOUNT_C, {"item": "ic"})
        self.log.clear()
        rc, *_ = self.run_reg()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.delivered, [])
        self.assertEqual([x for x in self.log if isinstance(x, tuple)], [])

    def test_the_delivery_plan_for_c_never_reuses_an_a_or_b_name_or_request_id(self):
        plan = g.delivery_plan({k: CREDS3[k] for k in "ABC"})
        self.assertEqual((len({n for _, n, _ in plan}), len({r for r, _, _ in plan})), (6, 6))
        self.assertEqual([n for _, n, _ in g.delivery_plan({"C": CREDS3["C"]})], ["GMAIL_C_USER", "GMAIL_C_PASS"])


class RealGuestSecretFile(unittest.TestCase):
    """The real guest script: adding C rewrites the file with A and B kept, and the A/B receipts stay valid without a replay."""

    def test_adding_c_keeps_a_and_b_and_their_receipts(self):
        import test_secretchan as ts
        with tempfile.TemporaryDirectory() as home:
            ident = Path(home, ".local/state/deck-executor/identity.json")
            ident.parent.mkdir(parents=True)
            ident.write_text(json.dumps({"bot_id": "bot-1", "guest_id": "g", "max_epoch": 0}))
            qga = ts.FakeQga(home)
            g.deliver_all(qga, "bot-1", {k: CREDS[k] for k in "AB"})
            envfile = Path(home, ".config/deck/secrets.env")
            before = envfile.read_text()
            qga.argv_log.clear()
            done = g.deliver_all(qga, "bot-1", {"C": CREDS3["C"]})
            self.assertEqual(done, {"GMAIL_C_USER": True, "GMAIL_C_PASS": True})
            lines = envfile.read_text().splitlines()
            self.assertTrue(set(before.splitlines()) <= set(lines))
            self.assertEqual(len(lines), 6)
            self.assertEqual(oct(envfile.stat().st_mode & 0o777), "0o600")
            c_only = [x[-1] for x in qga.argv_log]
            self.assertEqual(len(c_only), 4)  # C deliveries and receipts only. No A or B replay.
            self.assertTrue(all(r.startswith("gmail-trial-c-") for r in c_only))
            for k in "ab":
                for part in ("user", "pass"):
                    self.assertTrue(secretchan.receipt(qga, "bot-1", f"gmail-trial-{k}-{part}")["ok"])


class Help(unittest.TestCase):
    def test_doc_states_staged_ownership(self):
        self.assertIn("staged ownership", g.__doc__.lower())


if __name__ == "__main__":
    unittest.main()
