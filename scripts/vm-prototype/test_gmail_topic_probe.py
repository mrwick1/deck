import unittest

import gmail_label_repair as glr
import gmail_topic_probe as p

SEL = {"account": "account_a", "index": 0, "thread": "a1", "message": "b2"}
FULL = {"dialogs": 1, "title": "New label", "inputs": [{"name": "x", "value_ok": False}],
        "buttons": [{"name": "Cancel", "disabled": False}, {"name": "Create", "disabled": True}]}


class Page:
    def __init__(self, dialog=True, chips=("Deck/Receipts",)):
        self.chips, self.dialog, self.steps, self.typed = list(chips), dialog, [], False

    def step(self, text):
        self.steps.append(text)
        if 'name="Create new"' in text:
            return self.dialog
        if "\ntype " in text:
            self.typed = True
        return True

    def identity(self, index):
        return True

    def eval(self, js):
        if js == p.JS_DIALOG:
            b = [{"name": "Cancel", "disabled": False}, {"name": "Create", "disabled": not self.typed}]
            return {**FULL, "buttons": b, "inputs": [{"name": "x", "value_ok": self.typed}]}
        return None


class Probe(unittest.TestCase):
    def run_probe(self, page, textbox=None):
        glr_locate, glr_open = glr.locate, glr.open_menu
        glr.locate = lambda pg, sel: ({"chips": list(page.chips)}, "inbox", None)
        glr.open_menu = lambda pg, sel, d: ({"menus": 1}, None)
        try:
            return p.probe(page, SEL, textbox)
        finally:
            glr.locate, glr.open_menu = glr_locate, glr_open

    def test_facts(self):
        self.assertTrue(p.facts_ok(FULL))
        self.assertFalse(p.facts_ok({"dialogs": 0}))
        self.assertFalse(p.create_enabled(FULL))

    def test_probe_cancels_and_never_creates(self):
        page = Page()
        r, why = self.run_probe(page, "role=textbox")
        self.assertIsNone(why)
        self.assertTrue(r["dialog_ok"] and r["typed_value_ok"] and r["create_enabled_after_typing"] and r["cancelled"])
        self.assertTrue(r["chips_unchanged"] and r["no_probe_chip"])
        self.assertFalse(any('name="Create"' in s for s in page.steps))

    def test_missing_item_still_reloads(self):
        page = Page(dialog=False)
        r, why = self.run_probe(page)
        self.assertEqual(why, "no_create_item")
        self.assertTrue(page.steps[-1].endswith("reload"))

    def test_bad_field_name_refused(self):
        with self.assertRaises(SystemExit):
            p.cmd_dialog(type("A", (), {"textbox": 'x" "y', "pick": "", "db": "", "service_config": "", "out": "", "base": ""}))


if __name__ == "__main__":
    unittest.main()
