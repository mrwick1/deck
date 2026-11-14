import json
import unittest
from types import SimpleNamespace

import gmail_accounts_e2e as g
import secretchan

ACC = {"A": "first.user@gmail.com", "B": "second.user@gmail.com"}


class Sanitize(unittest.TestCase):
    def test_maps_known_addresses_hides_others_and_masks_secrets(self):
        t = g.sanitize("Inbox - First.User@gmail.com; second.user@gmail.com; stranger@example.org; pw hunter2x", ACC, ["hunter2x"])
        self.assertEqual(t, "Inbox - <A>; <B>; <other-address>; pw [secret]")

    def test_cuts_long_output(self):
        self.assertIn("more characters", g.sanitize("x" * 50, ACC, limit=10))


class Classify(unittest.TestCase):
    def probe(self, **kw):
        return json.dumps({"host": "mail.google.com", "path": "/mail/u/1/", "hash": "#inbox", "title": "Inbox - mail", "label": None, **kw})

    def test_label_names_the_account(self):
        r = g.classify(self.probe(label="Google Account: Name (second.user@gmail.com)"), ACC)
        self.assertEqual(r, {"identity": "B", "host": "mail.google.com", "view": "/mail/u/1/#inbox"})

    def test_other_address_and_missing_label(self):
        self.assertEqual(g.classify(self.probe(label="Google Account: X (z@example.org)"), ACC)["identity"], "other")
        self.assertEqual(g.classify(self.probe(), ACC)["identity"], "none")

    def test_bad_probe_is_none(self):
        self.assertEqual(g.classify("not json", ACC)["identity"], "none")

    def test_both_addresses_are_not_one_identity(self):
        self.assertEqual(g.classify(self.probe(label=f"{ACC['A']} {ACC['B']}"), ACC)["identity"], "both")


class Delivery(unittest.TestCase):
    def test_plan_has_four_distinct_names_and_request_ids(self):
        plan = g.delivery_plan({"A": ("ua", "pa"), "B": ("ub", "pb")})
        self.assertEqual(len({n for _, n, _ in plan}), 4)
        self.assertEqual(len({r for r, _, _ in plan}), 4)
        self.assertEqual({n for _, n, _ in plan}, {"GMAIL_A_USER", "GMAIL_A_PASS", "GMAIL_B_USER", "GMAIL_B_PASS"})

    def test_values_travel_only_on_stdin(self):
        calls = []

        class Qga:
            def exec(self, argv, stdin=None, timeout=0):
                calls.append((argv, stdin))
                return 0, json.dumps({"ok": True, "bot_id": "b", "request_id": argv[-1], "label": "X"}), ""

        done = g.deliver_all(Qga(), "b", {"A": ("ua@x.y", "pw-A1"), "B": ("ub@x.y", "pw-B2")})
        self.assertTrue(all(done.values()) and len(done) == 4)
        for argv, stdin in calls:
            self.assertFalse(any(v in " ".join(argv) for v in ("pw-A1", "pw-B2", "ua@x.y", "ub@x.y")))
        sent = [s for _, s in calls if s]
        self.assertEqual(len(sent), 4)
        self.assertTrue(any("pw-A1" in s for s in sent))

    def test_failure_names_the_secret_not_the_value(self):
        class Qga:
            def exec(self, *a, **k):
                return 1, json.dumps({"ok": False, "error": "unsafe_path"}), ""

        with self.assertRaises(g.TrialError) as r:
            g.deliver_all(Qga(), "b", {"A": ("u", "topsecret")})
        self.assertNotIn("topsecret", str(r.exception))
        self.assertIn("GMAIL_A_USER", str(r.exception))


class Vault(unittest.TestCase):
    def run_with(self, out, code=0):
        return lambda argv, **k: SimpleNamespace(returncode=code, stdout=out, stderr="")

    def test_reads_user_and_password(self):
        r = self.run_with(json.dumps({"data": {"username": "u", "password": "p"}}))
        self.assertEqual(g.vault_credentials("id", r), ("u", "p"))

    def test_missing_item_or_password_is_refused_without_text_of_record(self):
        for r in (self.run_with("", 1), self.run_with(json.dumps({"data": {"username": "u", "password": ""}})), self.run_with("garbage")):
            with self.assertRaises(g.TrialError):
                g.vault_credentials("id", r)


if __name__ == "__main__":
    unittest.main()
