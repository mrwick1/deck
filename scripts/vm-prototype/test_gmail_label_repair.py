"""Tests for gmail_label_repair.py. They start no VM and no browser: a fake mailbox answers the fixed programs and steps."""
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import gmail_label_repair as r
import gmail_live_acceptance as gla

T, M = "1a1e0299fe4ccd65", "1a1e02c107532acd"
SEL = {"account": "account_a", "thread": T, "message": M, "index": 0}
RUST = (Path(__file__).resolve().parents[2] / "src/bot/gmail_cleanup.rs").read_text()
DONE_TEXT = "Rs 500.00 debited from your a/c via UPI. Transaction successful.\nIf you did not do this, call the bank."
OTHER = ["Inbox", "Owner label"]


class Fake:
    """One account page. Saved chips survive a reload. A click on the open menu toggles the client side and saves it."""

    def __init__(self, chips, text=DONE_TEXT, account=0, message_ok=True, lose_receipt=False, drop_other_on_remove=False, save=True,
                 has_message=True, messages=2, crash_on_click=None, parts=None):
        self.saved, self.view, self.text, self.parts = list(chips), list(chips), text, parts
        self.account, self.message_ok, self.lose_receipt = account, message_ok, lose_receipt
        self.drop_other, self.save, self.has_message, self.messages = drop_other_on_remove, save, has_message, messages
        self.crash_on_click = crash_on_click
        self.selected, self.menu, self.url, self.clicks, self.steps = False, False, "", [], []

    # the programs
    def eval(self, js):
        if js == r.js_row(T, M):
            return {"host": r.HOST, "proto": "https:", "ready": "complete", "rows": 1, "pos": 3, "message_ok": self.message_ok,
                    "selected": 1 if self.selected else 0, "chips": list(self.view)}
        if js == r.js_read(T, M):
            return {"host": r.HOST, "proto": "https:", "ready": "complete", "path": f"/mail/u/{self.account}/", "view": "pt", "th": T,
                    "messages": self.messages, "len": len(self.text), "sent": len(self.text), "has_message": self.has_message, "text": self.text, **({"parts": self.parts} if self.parts is not None else {})}
        if js == gla.js_menu():
            items = [{"tag": "div", "role": "menuitemcheckbox", "checked": "true" if n in self.view else "false", "name": n} for n in (r.ADD, r.DROP)]
            return {"menus": 1 if self.menu else 0, "items": 2, "deck_items": items if self.menu else [], "inputs": 0}
        raise AssertionError("unknown program")

    def identity(self, index):
        return self.account == index

    def step(self, text):
        self.steps.append(text)
        if text.startswith("goto"):
            self.view, self.selected, self.menu = list(self.saved), False, False
            return True
        if "click css=" in text:
            self.selected = True
            return True
        if 'name="Labels"' in text:
            self.menu = self.selected
            return self.selected
        m = re.search(r'click role=menuitemcheckbox name="([^"]+)"', text)
        if m and self.menu:
            name = m.group(1)
            self.clicks.append(name)
            if self.crash_on_click == len(self.clicks):
                raise KeyboardInterrupt
            on = name in self.view
            if on:
                self.view.remove(name)
                if self.drop_other:
                    self.view.remove("Owner label")
            else:
                self.view.append(name)
            if self.save:
                self.saved = list(self.view)
            return not self.lose_receipt
        return False


def make_db(path, category="actionable", label="applied"):
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE gmail_triage(bot_id TEXT NOT NULL, account TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
        first_seen_ms INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending', category TEXT, summary TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        last_failure TEXT, triaged_ms INTEGER, label_state TEXT NOT NULL DEFAULT 'none', label_attempts INTEGER NOT NULL DEFAULT 0,
        summarized_day TEXT, origin TEXT NOT NULL DEFAULT 'window', PRIMARY KEY(bot_id, account, thread_id, message_id));
        CREATE TABLE gmail_org_journal(id INTEGER PRIMARY KEY, bot_id TEXT, account TEXT, thread_id TEXT, message_id TEXT, action TEXT, attempt INTEGER, started_ms INTEGER, outcome TEXT, reason TEXT);""")
    c.execute("INSERT INTO gmail_triage(bot_id,account,thread_id,message_id,state,category,summary,label_state) VALUES('shared-profile','account_a',?,?,'triaged',?,'UPI 500 notice',?)",
              (T, M, category, label))
    c.commit()
    c.close()


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.db, self.jp = str(Path(d.name) / "deck.db"), Path(d.name) / "journal.jsonl"
        make_db(self.db)
        self.jr = r.Journal(self.jp, clock=lambda: 1)
        self.orig_sleep = r.time.sleep
        r.time.sleep = lambda _: None
        self.addCleanup(lambda: setattr(r.time, "sleep", self.orig_sleep))

    def cat(self):
        return sqlite3.connect(self.db).execute("SELECT category, label_state FROM gmail_triage").fetchone()


class Evidence(unittest.TestCase):
    def test_word_lists_match_the_rust_rule(self):
        def rust(name):
            body = re.search(rf"const {name}: \[&str; \d+\] = \[(.*?)\];", RUST, re.S).group(1)
            return re.findall(r'"([^"]*)"', body)
        for name, mine in (("ASK_WORDS", r.ASK_WORDS), ("DONE_WORDS", r.DONE_WORDS), ("DONE_PHRASES", r.DONE_PHRASES), ("BOILERPLATE", r.BOILERPLATE), ("OWNER_ACTION", r.OWNER_ACTION)):
            self.assertEqual(rust(name), mine, name)

    def test_a_done_payment_with_bank_boilerplate_is_done_and_asks_nothing(self):
        ev = r.evidence(DONE_TEXT, False)
        self.assertTrue(ev["done_payment"] and ev["upi"] and ev["amount_500"])

    def test_an_ask_or_a_cut_text_or_no_done_word_is_never_a_done_payment(self):
        for t in ("Rs 500 debited. Please verify this payment.", "Rs 500 payment is due", "Hello there"):
            self.assertFalse(r.evidence(t, False)["done_payment"], t)
        self.assertFalse(r.evidence(DONE_TEXT, True)["done_payment"])


class Thread(unittest.TestCase):
    def ev(self, text, cut=False):
        return r.evidence(text, cut)

    def test_an_older_ask_stays_protected_despite_a_later_done_payment(self):
        last = self.ev(DONE_TEXT)
        footer = "\n".join(["Rs 500 debited via UPI."] * 6 + ["Click the secure link to view more."])
        for older in ("Rs 500 debited. Please verify this payment.", "Your EMI payment is due tomorrow.", footer):
            e = self.ev(older)
            self.assertTrue(e["ask"], older)
            self.assertFalse(r.thread_verdict([e, last]), older)
            self.assertFalse(r.thread_verdict([last, e]), older)

    def test_older_boilerplate_and_older_done_notices_are_allowed(self):
        last = self.ev(DONE_TEXT)
        self.assertTrue(r.thread_verdict([last]))
        self.assertTrue(r.thread_verdict([self.ev("Rs 200 debited via UPI. Do not share your OTP. No action required."), last]))
        self.assertTrue(r.thread_verdict([self.ev("Thank you for banking with us."), last]))

    def test_a_cut_or_empty_read_fails_closed(self):
        ok = self.ev(DONE_TEXT)
        self.assertFalse(r.thread_verdict([]))
        self.assertFalse(r.thread_verdict([self.ev(DONE_TEXT, True)]))
        self.assertFalse(r.thread_verdict([self.ev(DONE_TEXT, True), ok]))
        self.assertFalse(r.thread_verdict([self.ev(""), ok]))
        self.assertFalse(r.thread_verdict([self.ev("  \n "), ok]))
        self.assertFalse(r.thread_verdict([ok, self.ev("")]))

    def test_a_last_message_without_a_done_payment_is_refused(self):
        self.assertFalse(r.thread_verdict([self.ev(DONE_TEXT), self.ev("Hello there")]))
        self.assertFalse(r.thread_verdict([self.ev(DONE_TEXT), self.ev("Rs 500 debited. Please verify.")]))


class Repair(Base):
    def run_repair(self, page, **kw):
        return r.repair(page, self.jr, self.db, SEL, **kw)

    def test_the_wrong_action_label_becomes_fyi_and_every_other_chip_stays(self):
        p = Fake(OTHER + [r.DROP])
        self.assertEqual(self.run_repair(p), ("repaired", "updated"))
        self.assertEqual(p.saved, OTHER + [r.ADD])
        self.assertEqual(p.clicks, [r.ADD, r.DROP])
        self.assertEqual(self.cat(), ("fyi", "applied"))
        events = [x["event"] for x in self.jr.rows()]
        self.assertEqual(events.count("write_open"), 2)
        self.assertEqual(events[-2:], ["verified", "db_updated"])

    def test_a_dry_run_writes_nothing(self):
        p = Fake(OTHER + [r.DROP])
        self.assertEqual(self.run_repair(p, apply=False)[0], "ready")
        self.assertEqual((p.clicks, self.cat()), ([], ("actionable", "applied")))

    def test_another_account_in_the_browser_writes_nothing(self):
        p = Fake(OTHER + [r.DROP], account=1)
        self.assertEqual(self.run_repair(p), ("refused", "identity_refused"))
        self.assertEqual((p.clicks, self.cat()), ([], ("actionable", "applied")))

    def test_a_thread_that_shows_another_latest_message_is_never_written(self):
        for kw in ({"message_ok": False},):
            p = Fake(OTHER + [r.DROP], **kw)
            self.assertEqual(self.run_repair(p)[0], "refused")
            self.assertEqual((p.clicks, self.cat()), ([], ("actionable", "applied")))

    def test_a_message_that_asks_something_is_reported_and_its_label_stays(self):
        p = Fake(OTHER + [r.DROP], text="Rs 500 debited. Please verify now.")
        self.assertEqual(self.run_repair(p), ("refused", "evidence"))
        self.assertEqual((p.clicks, self.cat()), ([], ("actionable", "applied")))

    def test_an_older_ask_in_the_thread_refuses_the_repair_despite_a_later_done_payment(self):
        for parts in (["Your EMI payment is due tomorrow.", DONE_TEXT], ["", DONE_TEXT], [DONE_TEXT]):
            p = Fake(OTHER + [r.DROP], parts=parts)  # the last case: one part for two messages
            self.assertEqual(self.run_repair(p), ("refused", "evidence"))
            self.assertEqual((p.clicks, self.cat()), ([], ("actionable", "applied")))
        p = Fake(OTHER + [r.DROP], parts=["Rs 200 debited via UPI. No action required.", DONE_TEXT])
        self.assertEqual(self.run_repair(p)[0], "repaired")

    def test_a_row_that_already_shows_fyi_and_no_action_only_updates_the_store(self):
        p = Fake(OTHER + [r.ADD])
        self.assertEqual(self.run_repair(p), ("repaired", "updated"))
        self.assertEqual((p.clicks, self.cat()), ([], ("fyi", "applied")))

    def test_a_row_with_neither_label_is_refused_and_not_written(self):
        p = Fake(OTHER)
        self.assertEqual(self.run_repair(p), ("refused", "no_action_label"))
        self.assertEqual((p.clicks, self.cat()), ([], ("actionable", "applied")))

    def test_a_rerun_after_a_full_repair_clicks_nothing_and_the_store_stays_consistent(self):
        p = Fake(OTHER + [r.DROP])
        self.run_repair(p)
        p.clicks.clear()
        self.assertEqual(self.run_repair(p), ("repaired", "already"))
        self.assertEqual((p.clicks, self.cat()), ([], ("fyi", "applied")))

    def test_a_checkbox_that_is_not_in_the_expected_state_is_never_clicked(self):
        p = Fake(OTHER + [r.DROP])
        orig = p.eval
        p.eval = lambda js: {**orig(js), "deck_items": [{"role": "menuitemcheckbox", "checked": None, "name": r.ADD}]} if js == gla.js_menu() else orig(js)
        self.assertEqual(self.run_repair(p), ("refused", "menu_state_refused"))
        self.assertEqual(p.clicks, [])

    def test_a_lost_receipt_that_saved_is_confirmed_by_the_reload_and_never_clicked_again(self):
        p = Fake(OTHER + [r.DROP], lose_receipt=True)
        self.assertEqual(self.run_repair(p), ("repaired", "updated"))
        self.assertEqual(p.clicks, [r.ADD, r.DROP])

    def test_a_write_that_does_not_save_is_uncertain_and_the_store_stays(self):
        p = Fake(OTHER + [r.DROP], save=False)
        self.assertEqual(self.run_repair(p), ("uncertain", "not_visible"))
        self.assertEqual((p.clicks, self.cat()), ([r.ADD], ("actionable", "applied")))

    def test_a_restart_after_an_interrupted_click_reads_first_and_resumes_without_a_repeat(self):
        p = Fake(OTHER + [r.DROP], crash_on_click=1)
        with self.assertRaises(KeyboardInterrupt):
            self.run_repair(p)
        self.assertEqual(len(self.jr.open_writes(r.sel_key(SEL))), 1)
        p.saved = p.view = OTHER + [r.DROP, r.ADD]  # the click did save before the crash
        p.clicks.clear()
        p.crash_on_click = None
        self.assertEqual(self.run_repair(p), ("repaired", "updated"))
        self.assertEqual(p.clicks, [r.DROP])
        self.assertEqual(self.jr.open_writes(r.sel_key(SEL)), [])

    def test_a_restart_where_the_click_did_not_happen_writes_it_once_more_within_the_cap(self):
        p = Fake(OTHER + [r.DROP], crash_on_click=1)
        with self.assertRaises(KeyboardInterrupt):
            self.run_repair(p)
        p.clicks.clear()
        p.crash_on_click = None
        self.assertEqual(self.run_repair(p)[0], "repaired")
        self.assertEqual(p.clicks, [r.ADD, r.DROP])

    def test_the_attempt_cap_stops_a_third_write(self):
        key = r.sel_key(SEL)
        for i in (1, 2):
            self.jr.add(key, "write_open", id=i, op="add_fyi", attempt=i, before=3)
            self.jr.add(key, "write_close", id=i, outcome="not_visible")
        p = Fake(OTHER + [r.DROP])
        self.assertEqual(self.run_repair(p), ("refused", "attempt_cap"))
        self.assertEqual(p.clicks, [])

    def test_an_unrelated_chip_that_the_reload_drops_refuses_success(self):
        p = Fake(OTHER + [r.DROP], drop_other_on_remove=True)
        verdict, why = self.run_repair(p)
        self.assertEqual((verdict, why), ("lost", "lost"))
        self.assertEqual(self.cat(), ("actionable", "applied"))

    def test_the_store_refuses_a_row_that_changed_and_accepts_a_repeat(self):
        self.assertEqual(r.store_update(self.db, SEL), "updated")
        self.assertEqual(r.store_update(self.db, SEL), "already")
        other = tempfile.mkdtemp()
        db2 = str(Path(other) / "d.db")
        make_db(db2, category="reply_needed")
        self.assertEqual(r.store_update(db2, SEL), "refused")

    def test_the_fixed_texts_hold_no_banned_word(self):
        for t in (*r.OPEN_MENU, r.click_item(r.ADD), r.click_item(r.DROP)):
            self.assertTrue(gla.safe_step(t), t)
        self.assertNotIn("click css", r.click_item(r.DROP))


class Ownership(Base):
    def api(self, paused, running=False):
        calls = []

        def call(cfg, method, path, body=None):
            calls.append((method, body))
            if method == "POST":
                paused[0] = body["paused"]
                return 204, b""
            return 200, json.dumps({"automation_paused": paused[0], "gmail": {"running": running}}).encode()
        return call, calls

    def test_it_pauses_and_releases_only_its_own_pause(self):
        paused = [False]
        call, calls = self.api(paused)
        with r.Owned({}, self.db, call=call, sleep=lambda _: None):
            self.assertTrue(paused[0])
        self.assertFalse(paused[0])

    def test_it_never_releases_a_pause_of_the_owner(self):
        paused = [True]
        call, calls = self.api(paused)
        with r.Owned({}, self.db, call=call, sleep=lambda _: None):
            pass
        self.assertTrue(paused[0])
        self.assertFalse([c for c in calls if c[0] == "POST"])

    def test_an_open_journal_entry_or_a_running_pass_is_not_a_settled_boundary(self):
        sqlite3.connect(self.db).execute("INSERT INTO gmail_org_journal(bot_id, outcome) VALUES('x', NULL)").connection.commit()
        paused = [False]
        call, _ = self.api(paused)
        with self.assertRaises(RuntimeError):
            with r.Owned({}, self.db, call=call, sleep=lambda _: None, wait_s=0):
                pass
        self.assertFalse(paused[0])  # the pause that it made is released


class Select(Base):
    def test_one_candidate_is_picked_and_two_are_refused(self):
        self.assertEqual(len(r.candidates(self.db, "account_a")), 1)
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO gmail_triage(bot_id,account,thread_id,message_id,state,category,summary,label_state) VALUES('shared-profile','account_a','1a1e0299fe4ccd66','1a1e02c107532ace','triaged','actionable','UPI 500 again','applied')")
        c.commit()
        self.assertEqual(len(r.candidates(self.db, "account_a")), 2)


if __name__ == "__main__":
    unittest.main()
