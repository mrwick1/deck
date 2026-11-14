"""Tests for the guest secret channel. The fake QGA runs the real guest script under a temporary HOME."""
import contextlib
import io
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import secretchan as sc
import vmproto as v

BOT = "bot-1"


class FakeQga:
    """Runs guest commands locally. Records argv and every output so tests can look for leaks."""

    def __init__(self, home, fail=None):
        self.home = home
        self.fail = fail  # None, "transport", "leaky"
        self.argv_log = []
        self.output_log = []

    def exec(self, argv, stdin=None, timeout=30):
        self.argv_log.append(list(argv))
        if self.fail == "transport":
            raise RuntimeError(f"qga guest-exec: input was {stdin}")
        if self.fail == "leaky":
            return 1, "", f"boom {stdin}"
        assert argv[:4] == ["runuser", "-u", "deck", "--"], argv
        r = subprocess.run(argv[4:], input=stdin, capture_output=True, text=True,
                           env={**os.environ, "HOME": self.home})
        self.output_log += [r.stdout, r.stderr]
        return r.returncode, r.stdout, r.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        ident = Path(self.home, ".local/state/deck-executor/identity.json")
        ident.parent.mkdir(parents=True)
        ident.write_text(json.dumps({"bot_id": BOT, "guest_id": "g", "max_epoch": 0}))
        self.qga = FakeQga(self.home)
        self.value = secrets.token_urlsafe(18)

    def deliver(self, request_id="req-1", label="API_TOKEN", value=None, bot=BOT, qga=None):
        return sc.deliver(qga or self.qga, bot, request_id, label, self.value if value is None else value)

    def env(self):
        return Path(self.home, ".config/deck/secrets.env")

    def assert_no_leak(self, *extra):
        blob = json.dumps(self.qga.argv_log) + "".join(self.qga.output_log) + "".join(extra)
        self.assertTrue(self.value not in blob, "protected value escaped")


class EnvName(unittest.TestCase):
    def test_valid_uppercase_name_is_kept(self):
        self.assertEqual(sc.env_name("API_TOKEN"), "API_TOKEN")

    def test_display_label_gets_hashed_name(self):
        import hashlib
        n = sc.env_name("Gmail password")
        self.assertEqual(n, "DECK_" + hashlib.sha256(b"Gmail password").hexdigest()[:16].upper())

    def test_empty_or_long_label_rejected(self):
        for bad in ("", "x" * 201, 5, "a\x00b"):
            with self.assertRaises(sc.SecretError):
                sc.env_name(bad)


class Deliver(Base):
    def test_writes_private_file_and_returns_metadata_only(self):
        r = self.deliver()
        self.assertEqual(r, {"ok": True, "bot_id": BOT, "request_id": "req-1", "label": "API_TOKEN"})
        self.assertTrue(self.env().read_text() == f"API_TOKEN={self.value}\n", "stored value differs")
        self.assertEqual(stat.S_IMODE(self.env().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.env().parent.stat().st_mode), 0o700)
        self.assert_no_leak(json.dumps(r))

    def test_other_names_kept_and_same_name_replaced(self):
        self.deliver("r1", "A_ONE", "one")
        self.deliver("r2", "B_TWO", "two")
        self.deliver("r3", "A_ONE", "three")
        self.assertEqual(sorted(self.env().read_text().split()), ["A_ONE=three", "B_TWO=two"])

    def test_wrong_bot_rejected_and_nothing_written(self):
        with self.assertRaises(sc.SecretError) as e:
            self.deliver(bot="other")
        self.assertEqual(e.exception.code, "bot_mismatch")
        self.assertFalse(self.env().exists())

    def test_setup_wait_identity_can_reach_the_guest(self):
        request_id = BOT + ":1"
        result = self.deliver(request_id=request_id)
        self.assertEqual(result["request_id"], request_id)
        self.assertTrue(sc.receipt(self.qga, BOT, request_id)["ok"])

    def test_bad_request_id_and_value_rejected_before_guest_call(self):
        for rid in ("", "a b", "x" * 129, "../x"):
            with self.assertRaises(sc.SecretError):
                self.deliver(request_id=rid)
        for val in ("", "a\nb", "a\rb", "a\x00b", "x" * 4097, 7):
            with self.assertRaises(sc.SecretError):
                self.deliver(value=val)
        self.assertEqual(self.qga.argv_log, [])

    def test_duplicate_identity_is_idempotent(self):
        self.deliver()
        self.assertEqual(self.deliver()["ok"], True)
        self.assertEqual(self.env().read_text().count("API_TOKEN="), 1)

    def test_changed_value_under_completed_identity_does_not_overwrite(self):
        self.deliver()
        with self.assertRaises(sc.SecretError) as e:
            self.deliver(value=secrets.token_urlsafe(18))
        self.assertEqual(e.exception.code, "request_mismatch")
        self.assertTrue(self.env().read_text() == f"API_TOKEN={self.value}\n", "stored value differs")

    def test_same_request_other_label_is_mismatch(self):
        self.deliver()
        with self.assertRaises(sc.SecretError) as e:
            self.deliver(label="OTHER")
        self.assertEqual(e.exception.code, "request_mismatch")

    def test_symlinked_config_dir_refused(self):
        target = Path(self.home, "elsewhere")
        target.mkdir()
        Path(self.home, ".config").mkdir()
        os.symlink(target, Path(self.home, ".config/deck"))
        with self.assertRaises(sc.SecretError) as e:
            self.deliver()
        self.assertEqual(e.exception.code, "unsafe_path")
        self.assertEqual(list(target.iterdir()), [])

    def test_symlinked_env_file_refused(self):
        self.env().parent.mkdir(parents=True)
        victim = Path(self.home, "victim")
        victim.write_text("keep")
        os.symlink(victim, self.env())
        with self.assertRaises(sc.SecretError):
            self.deliver()
        self.assertEqual(victim.read_text(), "keep")

    def test_loose_existing_permissions_are_tightened(self):
        self.env().parent.mkdir(parents=True, mode=0o755)
        self.env().write_text("OLD=1\n")
        os.chmod(self.env(), 0o644)
        self.deliver()
        self.assertEqual(stat.S_IMODE(self.env().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.env().parent.stat().st_mode), 0o700)
        self.assertTrue("OLD=1" in self.env().read_text(), "existing reference missing")

    def test_transport_failure_hides_value(self):
        self.qga.fail = "transport"
        with self.assertRaises(sc.SecretError) as e:
            self.deliver()
        self.assertEqual(e.exception.code, "transport_error")
        self.assertTrue(self.value not in str(e.exception), "protected value escaped in error")

    def test_guest_error_output_hides_value(self):
        self.qga.fail = "leaky"
        with self.assertRaises(sc.SecretError) as e:
            self.deliver()
        self.assertTrue(self.value not in str(e.exception), "protected value escaped in error")

    def test_garbage_guest_output_is_an_error(self):
        class Junk(FakeQga):
            def exec(self, argv, stdin=None, timeout=30):
                return 0, "not json", ""
        with self.assertRaises(sc.SecretError):
            self.deliver(qga=Junk(self.home))


class Reconcile(Base):
    def test_receipt_after_delivery(self):
        self.deliver()
        r = sc.receipt(self.qga, BOT, "req-1")
        self.assertEqual(r, {"ok": True, "bot_id": BOT, "request_id": "req-1", "label": "API_TOKEN"})

    def test_completed_receipt_is_invalid_after_a_later_request_replaces_the_value(self):
        self.deliver()
        self.deliver("req-2", "API_TOKEN", secrets.token_urlsafe(18))
        with self.assertRaises(sc.SecretError) as error:
            sc.receipt(self.qga, BOT, "req-1")
        self.assertEqual(error.exception.code, "not_delivered")

    def test_receipt_tightens_existing_private_files(self):
        self.deliver()
        receipt = Path(self.home, ".local/state/deck-secrets/receipts/req-1.json")
        lock = receipt.parent.parent / "lock"
        for path in (self.env(), receipt, lock):
            os.chmod(path, 0o644)
        sc.receipt(self.qga, BOT, "req-1")
        for path in (self.env(), receipt, lock):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_receipt_unknown_request_is_not_delivered(self):
        with self.assertRaises(sc.SecretError) as e:
            sc.receipt(self.qga, BOT, "nope")
        self.assertEqual(e.exception.code, "not_delivered")

    def test_receipt_wrong_bot(self):
        self.deliver()
        with self.assertRaises(sc.SecretError) as e:
            sc.receipt(self.qga, "other", "req-1")
        self.assertEqual(e.exception.code, "bot_mismatch")

    def _crash_after_env_write(self):
        """Run the guest with a kill point between the env write and the receipt completion."""
        orig = self.qga.exec

        def crashing(argv, stdin=None, timeout=30):
            argv = argv[:4] + [argv[4], "-c", "import os\n_r=os.rename\n"
                               "def rn(a,b):\n    _r(a,b)\n    if b.endswith('secrets.env'): os._exit(9)\n"
                               "os.rename=rn\n" + argv[6]] + argv[7:]
            return orig(argv, stdin, timeout)
        self.qga.exec = crashing
        with self.assertRaises(sc.SecretError):
            self.deliver()
        self.qga.exec = orig

    def test_crash_after_write_before_receipt_reconciles_without_value(self):
        self._crash_after_env_write()
        self.assertTrue("API_TOKEN=" in self.env().read_text(), "expected secret reference missing")
        r = sc.receipt(self.qga, BOT, "req-1")
        self.assertTrue(r["ok"])
        self.assertEqual(sc.receipt(self.qga, BOT, "req-1"), r)

    def test_crash_then_retry_with_same_value_completes(self):
        self._crash_after_env_write()
        self.assertTrue(self.deliver()["ok"])

    def test_stale_receipt_cannot_clear_overwritten_request(self):
        self._crash_after_env_write()
        self.deliver("req-2", "API_TOKEN", "newer-value")  # a later request replaced the value
        with self.assertRaises(sc.SecretError) as e:
            sc.receipt(self.qga, BOT, "req-1")
        self.assertEqual(e.exception.code, "not_delivered")

    def test_receipt_files_are_private_and_hold_no_plain_value(self):
        self.deliver()
        files = list(Path(self.home, ".local/state/deck-secrets").rglob("*"))
        self.assertTrue(any(f.is_file() for f in files))
        for f in files:
            if f.is_file():
                self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
                self.assertTrue(self.value not in f.read_text(), "protected value escaped in receipt")


class Cli(Base):
    def run_cli(self, argv, stdin):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                patch.object(sys, "stdin", io.StringIO(stdin)), patch.object(v, "Qga", lambda path: self.qga):
            try:
                v.main(["--dir", self.home, *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        Path(self.home, "executor.json").write_text(json.dumps({"bot_id": BOT, "guest_id": "g"}))

    def test_deliver_secret_command(self):
        code, out, err = self.run_cli(["deliver-secret", "--bot", BOT, "--request-id", "r1"],
                                      json.dumps({"label": "Site password", "value": self.value}))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        j = json.loads(out)
        self.assertEqual(set(j), {"ok", "bot_id", "request_id", "label"})
        self.assertEqual(j["label"], sc.env_name("Site password"))
        self.assert_no_leak(out, err)
        self.assertEqual(self.qga.argv_log[0][0], "runuser")

    def test_secret_receipt_command(self):
        self.run_cli(["deliver-secret", "--bot", BOT, "--request-id", "r1"], json.dumps({"label": "A", "value": self.value}))
        code, out, _ = self.run_cli(["secret-receipt", "--bot", BOT, "--request-id", "r1"], "")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_host_executor_binding_checked(self):
        Path(self.home, "executor.json").write_text(json.dumps({"bot_id": "other", "guest_id": "g"}))
        code, out, err = self.run_cli(["deliver-secret", "--bot", BOT, "--request-id", "r1"],
                                      json.dumps({"label": "A", "value": self.value}))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {"ok": False, "error": "bot_mismatch"})
        self.assertEqual(self.qga.argv_log, [])

    def test_bad_stdin_fails_safely(self):
        for bad in ("not json", json.dumps({"label": "A"}), json.dumps({"label": "A", "value": "x", "extra": 1}),
                    json.dumps({"label": "A", "value": "a\nb"})):
            code, out, err = self.run_cli(["deliver-secret", "--bot", BOT, "--request-id", "r1"], bad)
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out)["ok"])
            self.assertNotIn("a\nb", out + err)

    def test_transport_failure_output_has_no_value(self):
        self.qga.fail = "transport"
        code, out, err = self.run_cli(["deliver-secret", "--bot", BOT, "--request-id", "r1"],
                                      json.dumps({"label": "A", "value": self.value}))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {"ok": False, "error": "transport_error"})
        self.assert_no_leak(out, err)


if __name__ == "__main__":
    unittest.main()
