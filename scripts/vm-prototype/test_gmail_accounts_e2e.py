import contextlib
import fcntl
import io
import json
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


class Classify(unittest.TestCase):
    def probe(self, **kw):
        return json.dumps({"host": "mail.google.com", "proto": "https:", "n": 1, "label": "Google Account: X (second.user@gmail.com)", **kw})

    def test_trusted_control_names_the_account_and_output_has_no_path(self):
        r = g.classify(self.probe(), ACC)
        self.assertEqual(r, {"identity": "B", "host_class": "mailbox"})

    def test_double_encoded_probe_is_read(self):
        self.assertEqual(g.classify(json.dumps(self.probe()), ACC)["identity"], "B")

    def test_wrong_host_and_redirect_are_blocked_even_with_a_matching_label(self):
        for host in ("accounts.google.com", "mail.google.com.evil.example", "evil.example"):
            self.assertEqual(g.classify(self.probe(host=host), ACC)["identity"], "blocked")
        self.assertEqual(g.classify(self.probe(proto="http:"), ACC)["identity"], "blocked")

    def test_title_is_never_proof(self):
        r = g.classify(self.probe(label=None, title="Inbox (first.user@gmail.com) - Gmail"), ACC)
        self.assertEqual(r["identity"], "unknown")

    def test_ambiguous_identity(self):
        self.assertEqual(g.classify(self.probe(label=f"{ACC['A']} {ACC['B']}"), ACC)["identity"], "ambiguous")
        self.assertEqual(g.classify(self.probe(n=2), ACC)["identity"], "ambiguous")

    def test_other_address_missing_control_and_bad_probe(self):
        self.assertEqual(g.classify(self.probe(label="Google Account: X (z@example.org)"), ACC)["identity"], "other")
        self.assertEqual(g.classify(self.probe(n=0, label=None), ACC)["identity"], "unknown")
        for bad in ("not json", "[]", "null", None):
            self.assertEqual(g.classify(bad, ACC)["identity"], "unknown")

    def test_address_inside_a_longer_address_is_not_a_match(self):
        r = g.classify(self.probe(label="Google Account: X (xsecond.user@gmail.com)"), ACC)
        self.assertEqual(r["identity"], "other")


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
    """env.call scripted: eval host probes pop from `hosts`; other tools return `result`."""

    def __init__(self, hosts, result=(False, "INBOX first.user@gmail.com secret subject"), identity=None):
        self.hosts, self.result, self.identity, self.tools = list(hosts), result, identity, []
        self.a = SimpleNamespace(keep_disk=False)

    def call(self, tool, **kw):
        if tool == "eval" and kw.get("js") == "location.hostname":
            h = self.hosts.pop(0)
            return (True, "boom") if h is None else (False, h)
        if tool == "eval" and "querySelectorAll" in kw.get("js", ""):
            return self.identity.pop(0)
        self.tools.append((tool, kw))
        return self.result


def idprobe(label, host="mail.google.com", n=1):
    return (False, json.dumps({"host": host, "proto": "https:", "n": n, "label": label}))


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
        env = FakeCallEnv([], identity=[idprobe("Google Account: X (first.user@gmail.com)")])
        rc, out, _ = self.run_cmd(env, "identity")
        self.assertEqual(json.loads(out), {"identity": "A", "host_class": "mailbox"})
        self.assertEqual(rc, 0)

    def test_identity_wrong_host_misleading_title_ambiguous_and_probe_error(self):
        cases = [idprobe("Google Account: X (first.user@gmail.com)", host="accounts.google.com"),
                 (False, json.dumps({"host": "mail.google.com", "proto": "https:", "n": 0, "label": None, "title": "first.user@gmail.com"})),
                 idprobe("first.user@gmail.com second.user@gmail.com"), (True, "INBOX first.user@gmail.com")]
        for probe in cases:
            env = FakeCallEnv([], identity=[probe])
            rc, out, err = self.run_cmd(env, "identity")
            self.assertNotEqual(json.loads(out)["identity"], "A")
            self.assertNotIn("INBOX", out + err)
            self.assertNotEqual(rc, 0)

    def test_identity_without_a_configured_control_is_blocked_and_runs_nothing(self):
        env = FakeCallEnv([], identity=[])
        with mock.patch.object(g, "attach", lambda a, **k: env), mock.patch.object(g, "load_creds", lambda items, run=None: CREDS), \
                mock.patch.object(g, "read_refs", lambda a: ({"A": "ia", "B": "ib"}, None)):
            rc, out, _ = run_captured(g.main, self.argv + ["identity"])
        self.assertEqual(json.loads(out)["identity"], "blocked")
        self.assertNotEqual(rc, 0)

    # switch
    def test_switch_navigates_by_index_and_succeeds_only_when_identity_is_verified(self):
        env = FakeCallEnv([], (False, "INBOX private"), identity=[idprobe("Google Account: X (second.user@gmail.com)")])
        rc, out, err = self.run_cmd(env, "switch", "--to", "B", "--index", "1")
        self.assertEqual(env.tools, [("step", {"steps": "goto https://mail.google.com/mail/u/1/", "compact": True})])
        self.assertEqual(json.loads(out), {"switched": True, "identity": "B", "host_class": "mailbox"})
        self.assertEqual(rc, 0)
        self.assertNotIn("INBOX", out + err)

    def test_switch_mismatch_is_a_failure_and_prints_no_raw_result(self):
        env = FakeCallEnv([], (True, "INBOX private error"), identity=[idprobe("Google Account: X (first.user@gmail.com)")])
        rc, out, err = self.run_cmd(env, "switch", "--to", "B", "--index", "1")
        self.assertEqual(json.loads(out)["switched"], False)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("INBOX", out + err)

    def test_switch_needs_a_small_index(self):
        env = FakeCallEnv([], identity=[])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_cmd(env, "switch", "--to", "B", "--index", "99")


class OperationLock(unittest.TestCase):
    def test_a_second_command_is_refused_while_one_holds_the_trial_lock(self):
        with tempfile.TemporaryDirectory() as d:
            held = g.op_lock(SimpleNamespace(out=d))
            try:
                with mock.patch.object(g, "attach", side_effect=AssertionError("must not attach")):
                    for sub in (["call", "read"], ["identity"], ["restart-mcp"], ["down"]):
                        argv = ["--out", d] + (["--base", "/b.qcow2"] if sub == ["down"] else []) + sub
                        rc, out, err = run_captured(g.main, argv)
                        self.assertEqual(rc, 4, sub)
                        self.assertIn("busy", err)
            finally:
                held.close()


class Help(unittest.TestCase):
    def test_doc_states_staged_ownership(self):
        self.assertIn("staged ownership", g.__doc__.lower())


if __name__ == "__main__":
    unittest.main()
