"""Tests for gmail_live_acceptance.py. They start no VM and no browser."""
import json
import unittest

import gmail_live_acceptance as g


class Programs(unittest.TestCase):
    def test_step_text_with_a_banned_word_or_placeholder_is_refused(self):
        for t in ('click text="Archive"', "click css=x # send", "type x ${GMAIL_A_USER}", "", "mark as unread"):
            self.assertFalse(g.safe_step(t), t)
        self.assertTrue(g.safe_step('click role=button name="Labels"'))

    def test_the_shape_program_quotes_ids_and_returns_no_text(self):
        js = g.js_shape("0000000000000001", "00000000000000a1")
        self.assertIn('"0000000000000001"', js)
        self.assertIn("msg_has_expected", js)

    def test_probes_are_fixed_and_name_only_deck_labels(self):
        for name, fn in g.PROBES.items():
            self.assertTrue(fn().startswith("(() =>"), name)
        self.assertIn(json.dumps(list(g.LABELS)), g.js_menu())


if __name__ == "__main__":
    unittest.main()
