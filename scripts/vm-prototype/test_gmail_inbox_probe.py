import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import gmail_inbox_probe as p

CONTRACT = {"row": "tr.row", "thread": {"sel": "span.t", "attr": "data-thread"}, "message": {"sel": "span.t", "attr": "data-last"},
            "unread": "tr.unread", "empty": "div.empty", "range": "span.range", "older": "div.older", "max_rows": 3}
NOMSG = {**CONTRACT, "message": None}


def raw(rows=(("thr-1111", "msg-1111"), ("thr-2222", "msg-2222")), **over):
    d = {"host": "mail.google.com", "proto": "https:", "ready": "complete", "view": "#inbox", "path_ok": True, "rows": len(rows), "empty": 0,
         "range": None, "older": None, "items": [{"t": [t], "m": [m], "u": False} for t, m in rows]}
    d.update(over)
    return json.dumps(d)


class Contract(unittest.TestCase):
    def test_accepts_the_documented_shape(self):
        self.assertTrue(p.valid_contract(CONTRACT))
        self.assertTrue(p.valid_contract(NOMSG))

    def test_rejects_unsafe_or_incomplete_contracts(self):
        for bad in (None, {}, {**CONTRACT, "row": ""}, {**CONTRACT, "row": "tr${X}"}, {**CONTRACT, "thread": None},
                    {**CONTRACT, "thread": {"sel": "a", "attr": "Bad Attr"}}, {**CONTRACT, "max_rows": 0}, {**CONTRACT, "max_rows": 500},
                    {**CONTRACT, "unread": 5}, {**CONTRACT, "extra": 1}):
            self.assertFalse(p.valid_contract(bad), bad)

    def test_the_v2_contract_keys_and_click_safe_selectors(self):
        v2 = {**CONTRACT, "grid": 'main table[role=grid]', "newer": "div[aria-label^=Prev]", "older": "div[role=button][aria-label^=Next]", "view": p.SEARCH_VIEW}
        self.assertTrue(p.valid_contract(v2))
        for bad in ({**v2, "view": "#sent"}, {**v2, "older": 'div[aria-label="Next results"]'}, {**v2, "older": "div\n[x]"}, {**v2, "grid": 5}):
            self.assertFalse(p.valid_contract(bad), bad)

    def test_scan_js_scopes_to_visible_elements_and_a_fixed_empty_phrase(self):
        js = p.scan_js({**CONTRACT, "grid": "table.g"})
        self.assertIn("getClientRects", js)
        self.assertIn("No messages matched your search", js)
        self.assertIn("table.g", js)

    def test_scan_js_is_one_fixed_read_only_expression(self):
        js = p.scan_js(CONTRACT)
        for word in ("click", "submit", "fetch(", "XMLHttpRequest", "innerText", "innerHTML", ".remove(", "setAttribute", "location.href =", "assign("):
            self.assertNotIn(word, js)
        self.assertIn("querySelectorAll", js)

    def test_structure_js_returns_names_and_counts_only(self):
        js = p.structure_js()
        for word in ("textContent", "innerText", "innerHTML", ".value", "click"):
            self.assertNotIn(word, js)
        self.assertIn("attributes", js)


class Parse(unittest.TestCase):
    def test_ok_with_rows(self):
        r = p.parse_scan(raw(), CONTRACT)
        self.assertEqual((r["status"], r["threads"], r["messages"]), ("ok", ["thr-1111", "thr-2222"], {"thr-1111": "msg-1111", "thr-2222": "msg-2222"}))

    def test_double_encoded_json_is_read(self):
        self.assertEqual(p.parse_scan(json.dumps(raw()), CONTRACT)["status"], "ok")

    def test_malformed_and_foreign_pages_are_refused(self):
        for text, status in (("nope", "malformed"), ("[]", "malformed"), (None, "malformed"),
                             (raw(host="accounts.google.com"), "not_mailbox"), (raw(proto="http:"), "not_mailbox"),
                             (raw(ready="loading"), "unloaded"), (raw(view="#sent"), "not_inbox"), (raw(path_ok=False), "not_inbox")):
            r = p.parse_scan(text, CONTRACT)
            self.assertEqual(r["status"], status, text)
            self.assertNotIn("threads", r)

    def test_empty_needs_a_positive_marker_zero_rows_alone_is_not_proof(self):
        self.assertEqual(p.parse_scan(raw(rows=(), empty=1), CONTRACT)["status"], "empty")
        self.assertEqual(p.parse_scan(raw(rows=(), empty=0), CONTRACT)["status"], "no_rows_unproven")
        self.assertEqual(p.parse_scan(raw(rows=(), empty=2), CONTRACT)["status"], "unsupported_dom")
        self.assertEqual(p.parse_scan(raw(rows=(), empty=1), {**CONTRACT, "empty": None})["status"], "no_rows_unproven")

    def test_marker_with_rows_is_a_contradiction(self):
        self.assertEqual(p.parse_scan(raw(empty=1), CONTRACT)["status"], "unsupported_dom")

    def test_unsupported_row_shapes(self):
        two = json.loads(raw())
        two["items"][0]["t"] = ["thr-1111", "thr-9999"]
        none = json.loads(raw())
        none["items"][0]["t"] = []
        badid = json.loads(raw(rows=(("x y", "msg-1111"),)))
        nomsg = json.loads(raw())
        nomsg["items"][1]["m"] = []
        miscount = json.loads(raw(rows=(("thr-1111", "msg-1111"),)))
        miscount["rows"] = 2
        dup = raw(rows=(("thr-1111", "msg-1"), ("thr-1111", "msg-2")))
        for text in (json.dumps(two), json.dumps(none), json.dumps(badid), json.dumps(nomsg), json.dumps(miscount), dup):
            self.assertEqual(p.parse_scan(text, CONTRACT)["status"], "unsupported_dom", text)

    def test_the_same_id_on_up_to_three_elements_of_a_row_is_one_id(self):
        d = json.loads(raw())
        d["items"][0]["t"] = ["thr-1111", "thr-1111"]
        d["items"][0]["m"] = ["msg-1111", "msg-1111", "msg-1111"]
        self.assertEqual(p.parse_scan(json.dumps(d), CONTRACT)["threads"], ["thr-1111", "thr-2222"])
        d["items"][0]["t"] = ["thr-1111"] * 4
        self.assertEqual(p.parse_scan(json.dumps(d), CONTRACT)["status"], "unsupported_dom")
        d["items"][0]["t"] = ["thr-1111"]
        d["items"][0]["m"] = ["msg-1111", "msg-9999"]
        self.assertEqual(p.parse_scan(json.dumps(d), CONTRACT)["status"], "unsupported_dom")

    def test_message_ids_may_be_absent_by_contract(self):
        r = p.parse_scan(raw(), NOMSG)
        self.assertEqual((r["status"], r["messages"]), ("ok", None))

    def test_more_rows_than_the_bound_is_partial_not_ok(self):
        rows = tuple((f"thr-{i}000", f"msg-{i}000") for i in range(4))
        d = json.loads(raw(rows=rows[:3]))
        d["rows"] = 4
        self.assertEqual(p.parse_scan(json.dumps(d), CONTRACT)["status"], "partial")

    def test_range_and_older_are_numbers_and_booleans(self):
        r = p.parse_scan(raw(range=[1, 2, 2], older={"n": 1, "disabled": True}), CONTRACT)
        self.assertEqual((r["range"], r["older"]), ({"start": 1, "end": 2, "total": 2}, {"present": True, "disabled": True}))
        bad = p.parse_scan(raw(range=["x", 2, 2]), CONTRACT)
        self.assertEqual(bad["status"], "unsupported_dom")

    def test_report_has_counts_and_booleans_only(self):
        rep = p.report(p.parse_scan(raw(), CONTRACT))
        self.assertEqual(rep, {"status": "ok", "view": "#inbox", "rows": 2, "message_ids": 2, "unique_threads": 2, "range": None, "older": None})
        self.assertNotIn("thr-1111", json.dumps(rep))


SEARCH = {**CONTRACT, "grid": "table.g", "view": p.SEARCH_VIEW, "older": "div.next", "newer": "div.prev"}
SRCH = p.SEARCH_VIEW


def page(start=1, n=2, total=None, older_disabled=False, newer_disabled=None, ids=None, **over):
    ids = [f"thr-{start + i:04d}" for i in range(n)] if ids is None else ids
    d = {"host": "mail.google.com", "proto": "https:", "ready": "complete", "view": SRCH, "page": (start - 1) // 2 + 1 if n == 2 else 1, "path_ok": True,
         "grids": 1, "rows": len(ids), "empty": 0, "range": [start, start + len(ids) - 1, total],
         "older": {"n": 1, "disabled": older_disabled}, "newer": {"n": 1, "disabled": start == 1 if newer_disabled is None else newer_disabled},
         "items": [{"t": [t], "m": ["m-" + t], "u": False} for t in ids]}
    d.update(over)
    return json.dumps(d)


class SearchView(unittest.TestCase):
    def test_the_search_view_is_scoped_and_reports_page_range_and_pager(self):
        r = p.parse_scan(page(), SEARCH)
        self.assertEqual((r["status"], r["view"], r["page"], r["range"], r["older"], r["newer"]),
                         ("ok", SRCH, 1, {"start": 1, "end": 2, "total": None}, {"present": True, "disabled": False}, {"present": True, "disabled": True}))

    def test_wrong_view_for_the_contract_is_not_inbox(self):
        self.assertEqual(p.parse_scan(page(view="#inbox"), SEARCH)["status"], "not_inbox")
        self.assertEqual(p.parse_scan(raw(), SEARCH)["status"], "not_inbox")
        self.assertEqual(p.parse_scan(page(), CONTRACT)["status"], "not_inbox")

    def test_not_exactly_one_visible_results_grid_is_unsupported(self):
        for n in (0, 2):
            r = p.parse_scan(page(grids=n), SEARCH)
            self.assertEqual((r["status"], r["reason"]), ("unsupported_dom", "grid_count"))

    def test_range_must_agree_with_the_row_count_and_total_may_be_many(self):
        self.assertEqual(p.parse_scan(page(range=[1, 5, None]), SEARCH)["reason"], "range_rows_mismatch")
        self.assertEqual(p.parse_scan(page(range=[3, 2, None]), SEARCH)["reason"], "range_rows_mismatch")
        self.assertEqual(p.parse_scan(page(range=[1, 2, 7]), SEARCH)["status"], "ok")
        self.assertEqual(p.parse_scan(page(range=[1, 2, "x"]), SEARCH)["reason"], "range_shape")

    def test_pager_with_more_than_one_visible_control_is_unsupported(self):
        self.assertEqual(p.parse_scan(page(newer={"n": 2, "disabled": True}), SEARCH)["reason"], "newer_shape")

    def test_a_marker_is_the_positive_empty_proof_in_the_search_view(self):
        r = p.parse_scan(page(ids=[], range=None, empty=1, older={"n": 0, "disabled": False}, newer={"n": 0, "disabled": False}), SEARCH)
        self.assertEqual((r["status"], r["threads"], r["view"]), ("empty", [], SRCH))


class Malformed(unittest.TestCase):
    def test_nested_or_unhashable_id_lists_are_unsupported_not_an_exception(self):
        for bad in ([["thr-1111"], ["thr-1111"]], [{"a": 1}], [None], [["x"]], [5], "thr-1111"):
            d = json.loads(raw())
            d["items"][0]["t"] = bad
            r = p.parse_scan(json.dumps(d), CONTRACT)
            self.assertEqual((r["status"], r["reason"]), ("unsupported_dom", "row_id_shape"), bad)
            d = json.loads(raw())
            d["items"][0]["m"] = bad
            self.assertEqual(p.parse_scan(json.dumps(d), CONTRACT)["reason"], "message_id_shape", bad)


class Reasons(unittest.TestCase):
    def test_each_unsupported_shape_has_a_stable_reason_code_and_no_id(self):
        d = json.loads(raw())
        d["items"][0]["t"] = ["thr-1111", "thr-9999"]
        short = json.loads(raw())
        short["items"].pop()
        dup = raw(rows=(("thr-1111", "msg-1"), ("thr-1111", "msg-2")))
        for text, why in ((json.dumps(d), "row_id_shape"), (json.dumps(short), "row_count_mismatch"), (dup, "duplicate_thread_id")):
            r = p.parse_scan(text, CONTRACT)
            self.assertEqual((r["status"], r["reason"]), ("unsupported_dom", why))
            self.assertEqual(p.report(r), {"status": "unsupported_dom", "reason": why})


class Tabs(unittest.TestCase):
    def test_only_whitelisted_category_names_and_counts_leave(self):
        raw_ = json.dumps({"host": "mail.google.com", "proto": "https:", "ready": "complete", "view": "#inbox", "counts": {},
                           "tabs": {"Primary": {"n": 1, "selected": True}, "Promotions": {"n": 1, "selected": False}, "Secret subject": {"n": 1, "selected": True}},
                           "tabs_other": 3})
        r = p.classify_structure(raw_)
        self.assertEqual(r["tabs"], {"Primary": {"n": 1, "selected": True}, "Promotions": {"n": 1, "selected": False}})
        self.assertEqual(r["tabs_other"], 3)
        self.assertNotIn("Secret", json.dumps(r))

    def test_structure_js_matches_tab_labels_only_against_a_fixed_list(self):
        js = p.structure_js()
        for name in p.CATEGORY_NAMES:
            self.assertIn(name, js)


class Compare(unittest.TestCase):
    def snap(self, rows):
        return p.parse_scan(raw(rows=rows), CONTRACT)

    def test_identical_snapshots(self):
        a = self.snap((("thr-1111", "m-1111"), ("thr-2222", "m-2222")))
        self.assertEqual(p.compare(a, a), {"same_rows": True, "same_order": True, "same_messages": True})

    def test_reordered_and_changed_message_are_distinct(self):
        a = self.snap((("thr-1111", "m-1111"), ("thr-2222", "m-2222")))
        b = self.snap((("thr-2222", "m-2222"), ("thr-1111", "m-1111")))
        c = self.snap((("thr-1111", "m-9999"), ("thr-2222", "m-2222")))
        self.assertEqual(p.compare(a, b), {"same_rows": True, "same_order": False, "same_messages": True})
        self.assertEqual(p.compare(a, c), {"same_rows": True, "same_order": True, "same_messages": False})

    def test_different_row_sets(self):
        a = self.snap((("thr-1111", "m-1111"),))
        b = self.snap((("thr-2222", "m-2222"),))
        self.assertFalse(p.compare(a, b)["same_rows"])


class Continuity(unittest.TestCase):
    def test_first_read_is_a_baseline(self):
        self.assertEqual(p.continuity([], ["a", "b"]), "baseline")

    def test_oldest_previous_row_still_present_means_overlap(self):
        self.assertEqual(p.continuity(["a", "b", "c"], ["x", "a", "b", "c"]), "overlap")

    def test_no_previous_row_present_means_a_possible_gap(self):
        self.assertEqual(p.continuity(["a", "b", "c"], ["x", "y", "z"]), "gap")

    def test_oldest_dropped_but_others_present_is_still_a_gap_unless_proven(self):
        self.assertEqual(p.continuity(["a", "b", "c"], ["x", "a", "b"]), "gap")


class Collect(unittest.TestCase):
    def pages(self, *raws):
        parsed = [p.parse_scan(x, SEARCH) for x in raws]
        it = iter(parsed[1:])
        calls = []

        def advance(prev):
            calls.append(prev["page"])
            return next(it, None)
        return parsed[0], advance, calls

    def run_(self, *raws, max_pages=5, max_seconds=100, clock=None):
        first, adv, calls = self.pages(*raws)
        return p.collect(first, adv, max_pages, max_seconds, clock or (lambda: 0)), calls

    def test_two_pages_to_a_disabled_pager_is_a_complete_snapshot(self):
        r, calls = self.run_(page(1, older_disabled=False), page(3, older_disabled=True, newer_disabled=False, page=2))
        self.assertEqual((r["status"], r["reason"], r["pages"], r["rows"], r["unique"], r["duplicates"], r["end"]), ("complete", None, 2, 4, 4, 0, "older_disabled"))
        self.assertEqual(r["threads"], ["thr-0001", "thr-0002", "thr-0003", "thr-0004"])
        self.assertEqual(len(r["messages"]), 4)
        self.assertEqual(calls, [1])

    def test_a_single_disabled_page_needs_no_click(self):
        r, calls = self.run_(page(1, older_disabled=True))
        self.assertEqual((r["status"], r["pages"], calls), ("complete", 1, []))

    def test_overlapping_rows_are_deduped_and_counted(self):
        r, _ = self.run_(page(1), page(3, ids=["thr-0002", "thr-0003"], older_disabled=True, newer_disabled=False, page=2))
        self.assertEqual((r["status"], r["rows"], r["unique"], r["duplicates"]), ("complete", 4, 3, 1))

    def test_the_page_cap_is_incomplete_never_complete(self):
        r, calls = self.run_(page(1), page(3, page=2, newer_disabled=False), page(5, page=3, newer_disabled=False), max_pages=2)
        self.assertEqual((r["status"], r["reason"], r["pages"]), ("incomplete", "page_cap", 2))

    def test_the_time_cap_is_incomplete(self):
        t = iter([0, 500, 500])
        r, _ = self.run_(page(1), page(3, older_disabled=True, newer_disabled=False, page=2), max_seconds=100, clock=lambda: next(t))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "time_cap"))

    def test_a_missing_older_control_is_incomplete_not_the_end(self):
        r, _ = self.run_(page(1, older={"n": 0, "disabled": False}))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "pager_missing"))

    def test_the_first_page_must_be_the_first(self):
        r, _ = self.run_(page(1, newer_disabled=False, older_disabled=True))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "not_first_page"))
        r, _ = self.run_(page(3, older_disabled=True, page=2))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "not_first_page"))

    def test_a_cycle_or_no_progress_stops_the_walk(self):
        r, _ = self.run_(page(1), page(1, newer_disabled=False, page=2))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "no_progress"))
        r, _ = self.run_(page(1), page(3, page=2, newer_disabled=False), page(1, page=3, newer_disabled=False), max_pages=9)
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "no_progress"))

    def test_a_page_that_does_not_continue_the_range_or_page_number_is_refused(self):
        r, _ = self.run_(page(1), page(7, page=2, newer_disabled=False))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "range_discontinuity"))
        r, _ = self.run_(page(1), page(3, page=5, newer_disabled=False))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "page_number"))

    def test_a_failed_click_or_unreadable_page_is_incomplete_with_the_status(self):
        r, _ = self.run_(page(1))
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "advance_failed"))
        first, _, _ = self.pages(page(1))
        r = p.collect(first, lambda prev: {"status": "unloaded"}, 5, 100, lambda: 0)
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "page_not_ok:unloaded"))

    def test_first_page_failure_or_empty(self):
        r = p.collect({"status": "error"}, None, 5, 100, lambda: 0)
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "first_page_not_ok:error"))
        e = p.parse_scan(page(ids=[], range=None, empty=1, older={"n": 0, "disabled": False}, newer={"n": 0, "disabled": False}), SEARCH)
        r = p.collect(e, None, 5, 100, lambda: 0)
        self.assertEqual((r["status"], r["rows"], r["threads"]), ("empty", 0, []))

    def test_the_recheck_of_the_first_page_decides_stability(self):
        first = p.parse_scan(page(1, older_disabled=True), SEARCH)
        snap = p.collect(first, None, 5, 100, lambda: 0)
        same = p.recheck(snap, p.parse_scan(page(1, older_disabled=True), SEARCH), first)
        self.assertEqual((same["status"], same["stable"]), ("complete", True))
        moved = p.recheck(snap, p.parse_scan(page(1, ids=["thr-0002", "thr-0001"], older_disabled=True), SEARCH), first)
        self.assertEqual((moved["status"], moved["reason"], moved["stable"]), ("unstable", "first_page_changed", False))
        newmsg = json.loads(page(1, older_disabled=True))
        newmsg["items"][0]["m"] = ["m-other"]
        self.assertEqual(p.recheck(snap, p.parse_scan(json.dumps(newmsg), SEARCH), first)["status"], "unstable")
        self.assertEqual(p.recheck(snap, {"status": "error"}, first)["status"], "unstable")
        self.assertFalse(p.recheck({**snap, "status": "incomplete"}, first, first)["stable"])

    def test_a_snapshot_never_holds_ids_in_its_report(self):
        r, _ = self.run_(page(1, older_disabled=True))
        self.assertNotIn("thr-0001", json.dumps(p.snapshot_report(r)))
        self.assertEqual(p.snapshot_report(r)["pages"], 1)


class Walk(unittest.TestCase):
    def walk(self, scans, identities=None, max_pages=5, **kw):
        env = Fake(scans)
        ident_ = lambda *a: {"identity": "A", "host_class": "mailbox"}  # noqa: E731
        r = p.snapshot_walk(env, "sel", "A", "ABC", {**SEARCH, "older": "div.next"}, 1, max_pages, 100, identity=identity_ if (identity_ := identities) else ident_,
                            sleep=lambda s: None, clock=lambda: 0, **kw)
        return env, r

    def test_two_pages_and_a_matching_recheck_is_a_stable_complete_snapshot(self):
        env, r = self.walk([page(1), page(3, page=2, older_disabled=True, newer_disabled=False), page(1)])
        self.assertEqual((r["status"], r["stable"], r["pages"], r["unique"]), ("complete", True, 2, 4))
        steps = [kw["steps"] for t, kw in env.calls if t == "step"]
        self.assertEqual(steps, ["goto https://mail.google.com/mail/u/1/#search/in%3Ainbox", 'click css="div.next"', "goto https://mail.google.com/mail/u/1/#search/in%3Ainbox"])

    def test_a_stale_page_after_the_click_is_polled_a_bounded_number_of_times(self):
        env, r = self.walk([page(1), page(1), page(3, page=2, older_disabled=True, newer_disabled=False), page(1)])
        self.assertEqual((r["status"], r["stable"]), ("complete", True))
        env, r = self.walk([page(1)] * 20)
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "page_number"))
        self.assertLessEqual(len([1 for t, _ in env.calls if t == "eval"]), 1 + p.ADVANCE_POLLS * p.READ_ATTEMPTS)

    def test_a_changed_first_page_refuses_the_snapshot(self):
        env, r = self.walk([page(1), page(3, page=2, older_disabled=True, newer_disabled=False), page(1, ids=["thr-9999", "thr-0001"])])
        self.assertEqual((r["status"], r["reason"], r["stable"]), ("unstable", "first_page_changed", False))

    def test_an_incomplete_walk_does_not_recheck(self):
        env, r = self.walk([page(1), page(3, page=2, newer_disabled=False)], max_pages=2)
        self.assertEqual((r["status"], r["reason"], r["stable"]), ("incomplete", "page_cap", False))
        self.assertEqual(len([1 for t, kw in env.calls if t == "step" and "goto" in kw["steps"]]), 1)

    def test_the_click_must_succeed_and_a_wrong_identity_stops_it(self):
        env = Fake([page(1)])
        env.call = lambda tool, **kw: (True, "FAILED") if tool == "step" and "click" in kw["steps"] else Fake.call(env, tool, **kw)
        r = p.snapshot_walk(env, "sel", "A", "ABC", {**SEARCH, "older": "div.next"}, 1, 5, 100, identity=lambda *a: {"identity": "A"}, sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "advance_failed"))
        r = p.snapshot_walk(Fake([page(1)]), "sel", "A", "ABC", {**SEARCH, "older": "div.next"}, 1, 5, 100, identity=lambda *a: {"identity": "B"}, sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "first_page_not_ok:identity_refused"))

    def test_a_click_receipt_with_trailing_bare_noise_is_a_success_but_a_failure_is_not(self):
        for text, ok in (("ok (4639 ms)", True), ("ok (12 ms)\nscript error: (in promise) WarningError: World iframe is not loaded", True),
                         ("ok (9339 ms): not ready: still loading after 9338 ms, 1 request pending", True), ("FAILED hidden: x", False), ("ok (x ms)", False), ("not ok (1 ms)", False), ("", False)):
            env = Fake([])
            env.call = lambda tool, text=text, **kw: (False, text)
            self.assertEqual(p.click(env, "div.next"), ok, text)
        env = Fake([])
        env.call = lambda tool, **kw: (True, "ok (1 ms)")
        self.assertFalse(p.click(env, "div.next"))

    def test_missing_view_never_proves_snapshot_coverage(self):
        r = p.snapshot_coverage({"status": "complete", "stable": True}, True)
        self.assertFalse(r["snapshot_complete"])
        self.assertIn("categories_not_covered", r["limits"])

    def test_every_page_read_checks_all_registered_accounts(self):
        seen = []

        def ident_(env, sel, labels):
            seen.append(labels)
            return {"identity": "A"}
        env, r = self.walk([page(1), page(3, page=2, older_disabled=True, newer_disabled=False), page(1)], identities=ident_)
        self.assertEqual(r["status"], "complete")
        self.assertEqual(seen, ["ABC"] * 6)

    def test_caps_are_bounded(self):
        for bad in (0, p.MAX_PAGES + 1, True, "5"):
            with self.assertRaises(p.ProbeError):
                p.snapshot_walk(Fake([]), "sel", "A", "ABC", {**SEARCH, "older": "div.next"}, 1, bad, 100, identity=None)
        with self.assertRaises(p.ProbeError):
            p.snapshot_walk(Fake([]), "sel", "A", "ABC", {**SEARCH, "older": "div.next"}, 1, 5, p.MAX_SECONDS + 1, identity=None)
        with self.assertRaises(p.ProbeError):
            p.snapshot_walk(Fake([]), "sel", "A", "ABC", {**SEARCH, "view": "#inbox"}, 1, 5, 100, identity=None)


class Stability(unittest.TestCase):
    def snap(self, rows):
        return p.parse_scan(raw(rows=rows), CONTRACT)

    def test_filtered_successes_never_make_a_failed_set_stable(self):
        ok = self.snap((("thr-1111", "m-1111"),))
        st = p.stability([ok, ok, {"status": "error"}, ok])
        self.assertEqual((st["all_ok"], st["all_same_rows"], st["all_same_order"], st["all_same_messages"]), (False, False, False, False))
        self.assertEqual(st["reads"], 4)

    def test_all_ok_and_identical_is_stable(self):
        ok = self.snap((("thr-1111", "m-1111"),))
        st = p.stability([ok, ok])
        self.assertEqual((st["all_ok"], st["all_same_rows"], st["all_same_order"], st["all_same_messages"]), (True, True, True, True))

    def test_no_reads_is_not_stable(self):
        self.assertFalse(p.stability([])["all_ok"])


class Seen(unittest.TestCase):
    def test_new_changed_unchanged_counts_only(self):
        seen = {"t1": "m1", "t2": "m2"}
        cur = {"t1": "m1", "t2": "m9", "t3": "m3"}
        self.assertEqual(p.diff_seen(seen, cur), {"new_threads": 1, "changed_threads": 1, "unchanged": 1})

    def test_without_message_ids_a_reply_is_invisible(self):
        self.assertEqual(p.diff_seen({"t1": None}, {"t1": None}), {"new_threads": 0, "changed_threads": 0, "unchanged": 1})

    def test_hashing_is_salted_and_stable(self):
        a, b = p.digest("salt1", "thr-1111"), p.digest("salt2", "thr-1111")
        self.assertEqual(p.digest("salt1", "thr-1111"), a)
        self.assertNotEqual(a, b)
        self.assertNotIn("thr-1111", a)


class Fake:
    """Scripted guest. `scans` are the raw texts the fixed scan returns, in order."""

    def __init__(self, scans):
        self.scans, self.calls = list(scans), []

    def call(self, tool, **kw):
        self.calls.append((tool, kw))
        if tool == "eval":
            return (False, self.scans.pop(0)) if self.scans else (True, "boom")
        return (False, "ok (1 ms)")


def ident(*seq):
    it = iter(seq)
    return lambda env, sel, labels: {"identity": next(it), "host_class": "mailbox"}


class ReadOnce(unittest.TestCase):
    def read(self, scans, identities, **kw):
        env = Fake(scans)
        r = p.read_once(env, "sel", "A", "ABC", CONTRACT, identity=ident(*identities), sleep=lambda s: None, **kw)
        return env, r

    def test_identity_before_and_after_wrap_the_scan(self):
        env, r = self.read([raw()], ["A", "A"])
        self.assertEqual((r["status"], r["identity_before"], r["identity_after"]), ("ok", "A", "A"))
        self.assertEqual([t for t, _ in env.calls], ["eval"])

    def test_wrong_identity_before_refuses_without_scanning(self):
        for who in ("B", "ambiguous", "unknown", "blocked"):
            env, r = self.read([raw()], [who] * 4)
            self.assertEqual(r["status"], "identity_refused", who)
            self.assertNotIn("threads", r)
            self.assertEqual(env.calls, [], who)

    def test_identity_change_during_the_scan_discards_the_data(self):
        env, r = self.read([raw()], ["A", "B"])
        self.assertEqual(r["status"], "identity_changed")
        self.assertNotIn("threads", r)

    def test_unknown_after_is_also_a_change(self):
        self.assertEqual(self.read([raw()], ["A", "unknown"])[1]["status"], "identity_changed")

    def test_an_unloaded_page_is_retried_a_bounded_number_of_times(self):
        env, r = self.read([raw(ready="loading"), raw(ready="loading"), raw()], ["A"] * 6)
        self.assertEqual(r["status"], "ok")
        env, r = self.read([raw(ready="loading")] * 10, ["A"] * 20)
        self.assertEqual(r["status"], "unloaded")
        self.assertLessEqual(len(env.calls), p.READ_ATTEMPTS)

    def test_an_eval_error_is_an_error_not_an_empty_inbox(self):
        env, r = self.read([], ["A"] * 8)
        self.assertEqual(r["status"], "error")
        self.assertNotIn("threads", r)

    def test_a_malformed_reply_is_not_an_empty_inbox(self):
        env, r = self.read(["garbage"], ["A", "A"])
        self.assertEqual(r["status"], "malformed")

    def test_zero_rows_without_a_marker_is_retried_then_refused(self):
        env, r = self.read([raw(rows=(), empty=0)] * 5, ["A"] * 10)
        self.assertEqual(r["status"], "no_rows_unproven")
        self.assertEqual(len(env.calls), p.READ_ATTEMPTS)

    def test_unsupported_dom_is_final_and_not_retried(self):
        d = json.loads(raw())
        d["items"][0]["t"] = []
        env, r = self.read([json.dumps(d)] * 5, ["A"] * 10)
        self.assertEqual((r["status"], len(env.calls)), ("unsupported_dom", 1))

    def test_invalid_contract_never_reaches_the_guest(self):
        env = Fake([raw()])
        with self.assertRaises(p.ProbeError):
            p.read_once(env, "sel", "A", "ABC", {"row": ""}, identity=ident("A", "A"), sleep=lambda s: None)
        self.assertEqual(env.calls, [])


class Navigation(unittest.TestCase):
    def test_urls_are_fixed_mailbox_https_with_a_single_digit_index(self):
        self.assertEqual(p.inbox_url(1), "https://mail.google.com/mail/u/1/#inbox")
        self.assertEqual(p.root_url(2), "https://mail.google.com/mail/u/2/")
        self.assertEqual(p.away_url(0), "https://mail.google.com/mail/u/0/#sent")
        for bad in (-1, 10, "1", True, None):
            with self.assertRaises(p.ProbeError):
                p.inbox_url(bad)


class Synthetic(unittest.TestCase):
    def test_example_output_shape_is_stable(self):
        rep = p.example_report()
        self.assertEqual(set(rep), {"contract_version", "account", "reads", "stability", "continuity", "seen", "coverage"})
        self.assertFalse(rep["coverage"]["complete"])
        self.assertIn("arrival_never_observed", rep["coverage"]["limits"])

    def test_coverage_is_never_complete_without_a_continuation_guarantee(self):
        c = p.coverage({"status": "ok", "rows": 50, "view": SRCH, "older": {"present": True, "disabled": False}}, "overlap", message_ids=True)
        self.assertFalse(c["complete"])
        self.assertIn("older_pages_unread", c["limits"])

    def test_an_absent_older_control_is_not_evidence_of_the_end(self):
        for older in ({"present": False, "disabled": False}, None):
            c = p.coverage({"status": "ok", "rows": 5, "view": SRCH, "older": older}, "overlap", message_ids=True, observed_arrival=True)
            self.assertFalse(c["page_covers_inbox"])
            self.assertFalse(c["complete"])
            self.assertIn("pagination_unknown", c["limits"])

    def test_the_primary_tab_view_never_covers_the_inbox(self):
        c = p.coverage({"status": "ok", "rows": 5, "view": "#inbox", "older": {"present": True, "disabled": True}}, "overlap", message_ids=True, observed_arrival=True)
        self.assertFalse(c["page_covers_inbox"])
        self.assertIn("categories_not_covered", c["limits"])

    def test_a_whole_inbox_page_is_not_complete_until_an_arrival_was_observed(self):
        whole = {"present": True, "disabled": True}
        c = p.coverage({"status": "ok", "rows": 5, "view": SRCH, "older": whole}, "overlap", message_ids=True)
        self.assertEqual((c["page_covers_inbox"], c["complete"]), (True, False))
        self.assertIn("arrival_never_observed", c["limits"])
        c = p.coverage({"status": "ok", "rows": 5, "view": SRCH, "older": whole}, "overlap", message_ids=True, observed_arrival=True)
        self.assertEqual((c["page_covers_inbox"], c["complete"], c["limits"]), (True, True, []))

    def test_an_empty_scan_with_a_marker_covers_the_inbox_without_a_pager(self):
        c = p.coverage({"status": "empty", "rows": 0, "view": SRCH, "older": {"present": False, "disabled": False}}, "baseline", message_ids=True, observed_arrival=True)
        self.assertTrue(c["page_covers_inbox"])

    def test_without_message_ids_replies_are_not_detected(self):
        c = p.coverage({"status": "ok", "rows": 5, "view": SRCH, "older": {"present": True, "disabled": True}}, "overlap", message_ids=False)
        self.assertIn("replies_in_existing_threads_undetected", c["limits"])
        self.assertFalse(c["complete"])

    def test_gap_or_bad_status_is_incomplete(self):
        self.assertFalse(p.coverage({"status": "ok", "rows": 5, "view": SRCH, "older": None}, "gap", message_ids=True)["complete"])
        self.assertFalse(p.coverage({"status": "partial", "rows": 5, "view": SRCH, "older": None}, "overlap", message_ids=True)["complete"])

    def test_a_walked_snapshot_is_covering_only_when_complete_and_stable(self):
        good = {"status": "complete", "stable": True, "pages": 3, "rows": 150, "view": SRCH}
        c = p.snapshot_coverage(good, message_ids=True)
        self.assertEqual((c["snapshot_complete"], c["complete"], c["limits"]), (True, False, ["arrival_never_observed"]))
        for bad in ({**good, "status": "incomplete", "reason": "page_cap"}, {**good, "stable": False, "status": "unstable"}, {**good, "stable": None}):
            c = p.snapshot_coverage(bad, message_ids=True)
            self.assertFalse(c["snapshot_complete"], bad)
            self.assertFalse(c["complete"])
        self.assertIn("replies_in_existing_threads_undetected", p.snapshot_coverage(good, message_ids=False)["limits"])

    def test_the_page_check_runs_against_a_stub_dom(self):
        if not shutil.which("node"):
            self.skipTest("node needed")
        html_rows = [{"t": ["thr-1111"], "m": ["msg-1111"], "u": False}]
        stub = ("const rows = %s.map(it => ({matches: s => false, querySelectorAll: s => s === 'span.t' ? it.t.map(v => ({getAttribute: n => v})) : []}));"
                "globalThis.location = {hostname: 'mail.google.com', protocol: 'https:', pathname: '/mail/u/0/', hash: '#inbox'};"
                "globalThis.document = {readyState: 'complete', querySelectorAll: s => s === 'tr.row' ? rows : [], querySelector: s => null};"
                "process.stdout.write(String(eval(%s)));" % (json.dumps(html_rows), json.dumps(p.scan_js(CONTRACT))))
        r = subprocess.run(["node", "-e", stub], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        parsed = p.parse_scan(r.stdout, CONTRACT)
        self.assertEqual((parsed["status"], parsed["threads"]), ("ok", ["thr-1111"]))


class StubSearchPage(unittest.TestCase):
    """The v2 page program against a small stub DOM: hidden duplicates, range text, pager controls, the empty phrase and the hash."""

    def run_js(self, hash_, rows=2, empty_text=None, range_text="1\u201350 of many", hidden_grid=True):
        if not shutil.which("node"):
            self.skipTest("node needed")
        stub = r"""
        const el = (o) => Object.assign({offsetParent: {}, closest: () => null, getAttribute: () => null, hasAttribute: () => false, matches: () => false, querySelectorAll: () => []}, o);
        const row = (i) => el({querySelectorAll: s => s === 'span.t' ? [el({getAttribute: () => 'thr-' + i + '000'})] : []});
        const rows = Array.from({length: %(rows)d}, (_, i) => row(i + 1));
        const grid = el({querySelectorAll: s => s === 'tr.row' ? rows : []});
        const hidden = el({offsetParent: null, getClientRects: () => [], querySelectorAll: () => [row(9)]});
        const range = el({textContent: %(range)s});
        const hrange = el({textContent: '9\u201399 of 99', offsetParent: null, getClientRects: () => []});
        const next = el({getAttribute: n => n === 'aria-disabled' ? 'false' : null});
        const hnext = el({offsetParent: null, getClientRects: () => []});
        const prev = el({getAttribute: n => n === 'aria-disabled' ? 'true' : null});
        const marker = el({textContent: %(empty)s});
        const map = {'table.g': [grid].concat(%(hid)s ? [hidden] : []), 'span.r': [range, hrange], 'div.next': [next, hnext], 'div.prev': [prev], 'td.e': %(empty)s === null ? [] : [marker]};
        globalThis.location = {hostname: 'mail.google.com', protocol: 'https:', pathname: '/mail/u/0/', hash: %(hash)s};
        globalThis.document = {readyState: 'complete', querySelectorAll: s => map[s] || []};
        process.stdout.write(String(eval(%(js)s)));
        """ % {"rows": rows, "range": json.dumps(range_text), "empty": json.dumps(empty_text), "hid": "true" if hidden_grid else "false", "hash": json.dumps(hash_),
               "js": json.dumps(p.scan_js({**SEARCH, "row": "tr.row", "range": "span.r", "older": "div.next", "newer": "div.prev", "empty": "td.e"}))}
        r = subprocess.run(["node", "-e", stub], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_visible_grid_range_pager_and_page_number(self):
        out = self.run_js("#search/in%3Ainbox", range_text="1\u20132 of many")
        r = p.parse_scan(out, {**SEARCH, "row": "tr.row"})
        self.assertEqual((r["status"], r["threads"], r["page"], r["range"], r["older"], r["newer"]),
                         ("ok", ["thr-1000", "thr-2000"], 1, {"start": 1, "end": 2, "total": None}, {"present": True, "disabled": False}, {"present": True, "disabled": True}))
        self.assertEqual(json.loads(out)["grids"], 1)
        self.assertEqual(json.loads(self.run_js("#search/in%3Ainbox/p7", range_text="301\u2013302 of 1,234"))["range"], [301, 302, 1234])
        self.assertEqual(json.loads(self.run_js("#search/in%3Ainbox/p7", range_text="not a range"))["range"], None)
        self.assertEqual(json.loads(self.run_js("#search/in%3Ainbox/p7"))["page"], 7)

    def test_foreign_hashes_are_not_the_search_view(self):
        for h in ("#search/in%3Ainbox/x", "#search/in%3Asent", "#inbox"):
            self.assertNotEqual(json.loads(self.run_js(h))["view"], p.SEARCH_VIEW, h)

    def test_empty_phrase_counts_only_the_fixed_text(self):
        self.assertEqual(json.loads(self.run_js("#search/in%3Ainbox", rows=0, empty_text="No messages matched your search. Try using fewer words"))["empty"], 1)
        self.assertEqual(json.loads(self.run_js("#search/in%3Ainbox", rows=0, empty_text="Something else"))["empty"], 0)


WIN = p.window_view(1_700_000_000) if hasattr(p, "window_view") else None
WINDOW = {**SEARCH, "view": WIN, "qbox": "input.q"}


def wpage(*a, **kw):
    return page(*a, **{"view": WIN, "qn": 1, "qm": 1, **kw})


class WindowQuery(unittest.TestCase):
    def test_the_window_view_is_the_exact_fixed_query(self):
        self.assertEqual(p.window_view(1_700_000_000), "#search/in%3Ainbox+after%3A1700000000")
        self.assertEqual(p.window_query(1_700_000_000), "in:inbox after:1700000000")
        for bad in (0, -5, 1.5, True, "1700000000", 10**12, 99):
            with self.assertRaises(p.ProbeError):
                p.window_view(bad)

    def test_the_contract_accepts_only_known_views_and_a_click_safe_query_box(self):
        self.assertTrue(p.valid_contract(WINDOW))
        for bad in ({**WINDOW, "view": "#search/in%3Ainbox+after%3A12"}, {**WINDOW, "view": "#search/in%3Aall+after%3A1700000000"},
                    {**WINDOW, "view": WIN + "/p2"}, {**WINDOW, "qbox": 5}, {**WINDOW, "qbox": "input${X}"}):
            self.assertFalse(p.valid_contract(bad), bad)

    def test_the_returned_view_and_query_box_must_bind_to_the_intended_query(self):
        self.assertEqual(p.parse_scan(wpage(), WINDOW)["status"], "ok")
        self.assertEqual(p.parse_scan(wpage(view=SRCH), WINDOW)["status"], "not_inbox")  # the unfiltered inbox search
        self.assertEqual(p.parse_scan(wpage(view=p.window_view(1_700_000_001)), WINDOW)["status"], "not_inbox")  # another bound
        self.assertEqual(p.parse_scan(wpage(view="other"), WINDOW)["status"], "not_inbox")
        for over in ({"qm": 0}, {"qn": 2}, {"qn": 0, "qm": 0}, {"qn": 1, "qm": 2}, {"qm": "1"}):
            r = p.parse_scan(wpage(**over), WINDOW)
            self.assertEqual((r["status"], r["reason"]), ("unsupported_dom", "query_box"), over)

    def test_an_unbound_contract_does_not_need_the_box(self):
        self.assertEqual(p.parse_scan(wpage(), {**WINDOW, "qbox": None})["status"], "ok")

    def test_empty_window_needs_the_positive_marker_and_binds_the_view(self):
        e = p.parse_scan(wpage(ids=[], range=None, empty=1, older={"n": 0, "disabled": False}, newer={"n": 0, "disabled": False}), WINDOW)
        self.assertEqual((e["status"], e["threads"]), ("empty", []))
        r = p.parse_scan(wpage(ids=[], range=None, empty=0), WINDOW)
        self.assertEqual(r["status"], "no_rows_unproven")


class WindowJs(unittest.TestCase):
    def run_js(self, hash_, box_value="in:inbox after:1700000000", boxes=1):
        if not shutil.which("node"):
            self.skipTest("node needed")
        stub = r"""
        const el = (o) => Object.assign({offsetParent: {}, closest: () => null, getAttribute: () => null, hasAttribute: () => false, matches: () => false, querySelectorAll: () => []}, o);
        const grid = el({querySelectorAll: () => []});
        const box = el({value: %(val)s});
        const map = {'table.g': [grid], 'input.q': Array.from({length: %(n)d}, () => box)};
        globalThis.location = {hostname: 'mail.google.com', protocol: 'https:', pathname: '/mail/u/0/', hash: %(hash)s};
        globalThis.document = {readyState: 'complete', querySelectorAll: s => map[s] || []};
        process.stdout.write(String(eval(%(js)s)));
        """ % {"val": json.dumps(box_value), "n": boxes, "hash": json.dumps(hash_), "js": json.dumps(p.scan_js({**WINDOW, "row": "tr.row", "empty": None, "range": None, "older": None, "newer": None}))}
        r = subprocess.run(["node", "-e", stub], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_view_matches_the_decoded_exact_query_and_page(self):
        for h, pg in ((WIN, 1), (WIN + "/p3", 3), ("#search/in%3Ainbox%20after%3A1700000000", 1), ("#search/in%3Ainbox+after%3A1700000000/p12", 12)):
            d = self.run_js(h)
            self.assertEqual((d["view"], d["page"]), (WIN, pg), h)

    def test_other_queries_and_hashes_do_not_match(self):
        for h in ("#search/in%3Ainbox", "#search/in%3Ainbox+after%3A1700000001", WIN + "/x", WIN + "/p1234", "#search/in%3Ainbox+after%3A1700000000+x", "#inbox", "#search/%E0%A4%A"):
            self.assertEqual(self.run_js(h)["view"] == WIN, False, h)

    def test_the_search_box_value_is_compared_not_returned(self):
        d = self.run_js(WIN)
        self.assertEqual((d["qn"], d["qm"]), (1, 1))
        d = self.run_js(WIN, box_value="in:inbox after:1")
        self.assertEqual((d["qn"], d["qm"]), (1, 0))
        self.assertNotIn("after:1", json.dumps(d))
        self.assertEqual(self.run_js(WIN, boxes=2)["qn"], 2)


class WindowPlan(unittest.TestCase):
    DAY = 86400

    def test_the_first_scan_looks_back_24_hours(self):
        self.assertEqual(p.plan_window(1_700_100_000, None), {"kind": "baseline", "lower": 1_700_100_000 - self.DAY})

    def test_a_later_scan_starts_before_the_last_success_by_the_overlap(self):
        self.assertEqual(p.plan_window(1_700_100_120, 1_700_100_000, overlap=3600), {"kind": "catchup", "lower": 1_700_100_000 - 3600})

    def test_downtime_keeps_the_previous_bound_and_is_not_cut_to_24_hours(self):
        last = 1_700_000_000
        plan = p.plan_window(last + 5 * self.DAY, last, overlap=3600)
        self.assertEqual(plan["lower"], last - 3600)
        self.assertGreater(plan["kind"] == "catchup" and (last + 5 * self.DAY - plan["lower"]), self.DAY)

    def test_a_watermark_from_the_future_or_a_bad_clock_is_an_error(self):
        for now, wm in ((1_700_000_000, 1_700_000_001), (0, None), (-1, None), (True, None), (1.5, None), (1_700_000_000, "x"), (1_700_000_000, True), (1_700_000_000, 0)):
            with self.assertRaises(p.ProbeError):
                p.plan_window(now, wm)

    def test_the_lower_bound_must_stay_a_valid_window(self):
        with self.assertRaises(p.ProbeError):
            p.plan_window(1_700_000_000, 1000, overlap=3600)  # lower would be negative


class Watermark(unittest.TestCase):
    def test_missing_is_none_and_advances_only_for_a_stable_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "wm.json"
            self.assertIsNone(p.read_watermark(f, "A"))
            for cov in ({"snapshot_complete": False}, {"complete": True}, {}):
                self.assertFalse(p.maybe_save_watermark(f, "A", 1_700_000_000, cov))
                self.assertFalse(f.exists())
            self.assertTrue(p.maybe_save_watermark(f, "A", 1_700_000_000, {"snapshot_complete": True}))
            self.assertEqual(p.read_watermark(f, "A"), 1_700_000_000)
            self.assertIsNone(p.read_watermark(f, "B"))
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)

    def test_it_never_moves_back_and_failures_leave_it(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "wm.json"
            p.maybe_save_watermark(f, "A", 1_700_000_500, {"snapshot_complete": True})
            self.assertFalse(p.maybe_save_watermark(f, "A", 1_700_000_100, {"snapshot_complete": True}))
            self.assertFalse(p.maybe_save_watermark(f, "A", 1_700_001_000, {"snapshot_complete": False}))
            self.assertEqual(p.read_watermark(f, "A"), 1_700_000_500)

    def test_a_corrupt_file_is_an_error_and_stays(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "wm.json"
            for bad in ("{x", "[]", '{"A": "5"}', '{"A": true}', '{"A": -1}'):
                f.write_text(bad)
                with self.assertRaises(p.ProbeError):
                    p.read_watermark(f, "A")
                with self.assertRaises(p.ProbeError):
                    p.maybe_save_watermark(f, "A", 1_700_000_000, {"snapshot_complete": True})
                self.assertEqual(f.read_text(), bad)


class WindowWalk(unittest.TestCase):
    def walk(self, scans, max_pages=3, ident_=None):
        env = Fake(scans)
        r = p.snapshot_walk(env, "sel", "A", "ABC", {**WINDOW, "older": "div.next"}, 1, max_pages, 90, identity=ident_ or (lambda *a: {"identity": "A", "host_class": "mailbox"}),
                            sleep=lambda s: None, clock=lambda: 0)
        return env, r

    def test_the_walk_navigates_to_the_exact_window_and_is_stable_complete(self):
        env, r = self.walk([wpage(1), wpage(3, page=2, older_disabled=True, newer_disabled=False), wpage(1)])
        self.assertEqual((r["status"], r["stable"], r["pages"], r["unique"]), ("complete", True, 2, 4))
        steps = [kw["steps"] for t, kw in env.calls if t == "step"]
        self.assertEqual(steps[0], "goto https://mail.google.com/mail/u/1/" + WIN)
        self.assertEqual(steps[-1], steps[0])

    def test_a_page_that_ignores_the_filter_is_refused(self):
        env, r = self.walk([wpage(1, view=SRCH)])
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "first_page_not_ok:not_inbox"))

    def test_the_page_cap_is_incomplete_not_no_new_mail(self):
        pages = [wpage(1), wpage(3, page=2, newer_disabled=False), wpage(5, page=3, newer_disabled=False), wpage(7, page=4, older_disabled=True, newer_disabled=False)]
        env, r = self.walk(pages, max_pages=3)
        self.assertEqual((r["status"], r["reason"], r["pages"], r["stable"]), ("incomplete", "page_cap", 3, False))

    def test_cross_page_duplicates_are_counted_once_and_a_known_total_must_match(self):
        env, r = self.walk([wpage(1, total=3), wpage(3, page=2, total=3, ids=["thr-0002", "thr-0003"], older_disabled=True, newer_disabled=False), wpage(1, total=3)])
        self.assertEqual((r["status"], r["unique"], r["duplicates"]), ("complete", 3, 1))
        env, r = self.walk([wpage(1, total=9), wpage(3, page=2, total=9, older_disabled=True, newer_disabled=False)])
        self.assertEqual((r["status"], r["reason"]), ("incomplete", "total_mismatch"))

    def test_the_window_is_not_the_whole_inbox_in_the_coverage(self):
        snap = {"status": "complete", "stable": True, "view": WIN}
        cov = p.snapshot_coverage(snap, True)
        self.assertEqual((cov["snapshot_complete"], cov["complete"], cov["scope"]), (True, False, "date_window"))
        self.assertIn("date_window_only", cov["limits"])
        self.assertEqual(p.snapshot_coverage({**snap, "view": SRCH}, True)["scope"], "inbox")
        self.assertFalse(p.snapshot_coverage({**snap, "stable": False}, True)["snapshot_complete"])


class MergedSeen(unittest.TestCase):
    def test_a_window_scan_keeps_threads_that_left_the_window(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "seen.json"
            p.save_seen(f, "A", {"thr-1111": "m1", "thr-2222": "m2"})
            p.save_seen(f, "A", {"thr-2222": "m2", "thr-3333": "m3"}, merge=True)
            old = p.load_seen(f, "A", {"thr-1111": "m1", "thr-2222": "m2", "thr-3333": "m3"})
            self.assertEqual(old, {"thr-1111": "m1", "thr-2222": "m2", "thr-3333": "m3"})
            p.save_seen(f, "A", {"thr-3333": "m3"})
            self.assertEqual(p.load_seen(f, "A", {"thr-1111": "m1"}), {})

    def test_a_failed_recent_scan_changes_neither_store(self):
        with tempfile.TemporaryDirectory() as d:
            seen, wm = Path(d) / "seen.json", Path(d) / "wm.json"
            cov = p.snapshot_coverage({"status": "incomplete", "stable": False, "view": WIN}, True)
            self.assertFalse(p.maybe_save_seen(seen, "A", {"t-1111": "m"}, cov, merge=True))
            self.assertFalse(p.maybe_save_watermark(wm, "A", 1_700_000_000, cov))
            self.assertEqual(list(Path(d).iterdir()), [])


class RecentCli(unittest.TestCase):
    def test_defaults_are_the_initial_feasibility_caps(self):
        a = p.parse(["--out", "o", "--evidence", "e", "recent", "--account", "A", "--index", "0", "--contract", "c"])
        self.assertEqual((a.sub, a.max_pages, a.max_seconds, a.lookback_hours), ("recent", 3, 90, 24))

    def test_the_report_holds_no_id_and_no_raw_query(self):
        plan = p.plan_window(1_700_100_000, None)
        rep = p.recent_report(plan, 1_700_100_000, {"status": "complete", "stable": True, "pages": 1, "rows": 2, "unique": 2, "duplicates": 0, "view": WIN, "threads": ["thr-0001"]})
        text = json.dumps(rep)
        self.assertNotIn("thr-0001", text)
        self.assertNotIn("after", text)
        self.assertEqual((rep["plan"]["kind"], rep["plan"]["window_seconds"]), ("baseline", 86400))


class CliBoundary(unittest.TestCase):
    def test_evidence_directory_must_be_new_and_private(self):
        with tempfile.TemporaryDirectory() as d:
            ev = Path(d) / "ev"
            p.new_evidence_dir(ev)
            self.assertEqual(ev.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(p.ProbeError):
                p.new_evidence_dir(ev)

    def test_seen_store_holds_only_salted_digests(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "seen.json"
            p.save_seen(store, "A", {"thr-1111": "msg-1111"})
            text = store.read_text()
            self.assertNotIn("thr-1111", text)
            self.assertNotIn("msg-1111", text)
            self.assertEqual(store.stat().st_mode & 0o777, 0o600)
            self.assertEqual(p.diff_seen(p.load_seen(store, "A", {"thr-1111": "msg-1111"}), {"thr-1111": "msg-1111"})["unchanged"], 1)

    def test_a_corrupt_seen_store_is_an_error_not_a_fresh_baseline_and_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "seen.json"
            store.write_text("{not json")
            with self.assertRaises(p.ProbeError):
                p.save_seen(store, "A", {"thr-1111": "msg-1111"})
            with self.assertRaises(p.ProbeError):
                p.load_seen(store, "A", {"thr-1111": "msg-1111"})
            self.assertEqual(store.read_text(), "{not json")
            for bad in ("[]", '{"salt": 5}', '{"salt": "s", "A": []}'):
                store.write_text(bad)
                with self.assertRaises(p.ProbeError):
                    p.save_seen(store, "A", {"thr-1111": "m"})
                self.assertEqual(store.read_text(), bad)

    def test_a_missing_store_or_label_is_a_baseline_and_other_labels_survive(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "seen.json"
            self.assertEqual(p.load_seen(store, "A", {"t": "m"}), {})
            p.save_seen(store, "A", {"thr-1111": "msg-1111"})
            self.assertEqual(p.load_seen(store, "B", {"thr-1111": "msg-1111"}), {})
            p.save_seen(store, "B", {"thr-2222": "msg-2222"})
            self.assertEqual(p.load_seen(store, "A", {"thr-1111": "msg-1111"}), {"thr-1111": "msg-1111"})
            self.assertEqual(sorted(p.os.listdir(d)), ["seen.json"])

    def test_seen_advances_only_for_a_complete_scan(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "seen.json"
            for cov in ({"snapshot_complete": False}, {"complete": True}, {}):
                self.assertFalse(p.maybe_save_seen(store, "A", {"t-1111": "m"}, cov))
                self.assertFalse(store.exists())
            self.assertTrue(p.maybe_save_seen(store, "A", {"t-1111": "m"}, {"snapshot_complete": True}))
            self.assertTrue(store.exists())

    def test_snapshot_subcommand_has_bounded_defaults(self):
        a = p.parse(["--out", "o", "--evidence", "e", "snapshot", "--account", "A", "--index", "0", "--contract", "c"])
        self.assertEqual((a.sub, a.max_pages, a.max_seconds), ("snapshot", 40, 300))
        self.assertLessEqual(a.max_pages, p.MAX_PAGES)
        self.assertLessEqual(a.max_seconds, p.MAX_SECONDS)

    def test_help_prints_without_effects(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as r:
            p.parse(["--help"])
        self.assertEqual(r.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
