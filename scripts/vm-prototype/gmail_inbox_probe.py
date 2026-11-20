#!/usr/bin/env python3
"""Read-only Gmail inbox extraction probe for the retained shared-profile trial. It proves identifiers and coverage. It is not a worker.

It adds no browser and no API. It runs ONE fixed, bounded `eval` per read through the guest bare-mcp of the trial that
`gmail_accounts_e2e.py` owns (resume it first, take it down with that helper). The eval reads the structure and the
opaque id attributes of inbox rows. It never reads a subject, a sender, a snippet or a body, opens no message and clicks nothing.
Navigation is only `goto` of the inbox, the Sent list and the mailbox root.

Safety:
  * Identity before and after every read must equal the wanted account (`verify_identity` of the trial helper, over all
    registered accounts). Unknown, ambiguous, blocked or another account refuses the read. A change during the read discards the data.
  * The scan contract (selectors and attribute names) is private configuration. It is validated, never guessed. The page
    check refuses a malformed reply, a foreign page, an unloaded page, a row that does not carry exactly one id, duplicate
    ids and zero rows without a positive empty marker. Each refusal has its own status. None means "empty".
  * Ids stay in process memory. Only counts, booleans and salted digests (private seen store) are written or printed.
  * Evidence goes to a NEW private directory (--evidence). Nothing is overwritten.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import gmail_accounts_e2e as g

MAX_ROWS = 100
READ_ATTEMPTS = 4
ID_RE = re.compile(r"[A-Za-z0-9:#_.-]{4,100}")
ATTR_RE = re.compile(r"[a-z][a-z0-9-]{0,60}")
INBOX_VIEW = "#inbox"  # the Primary tab only
SEARCH_VIEW = "#search/in%3Ainbox"  # the Gmail search `in:inbox`: every category in one list
VIEWS = (INBOX_VIEW, SEARCH_VIEW)
WINDOW_VIEW_RE = re.compile(r"#search/in%3Ainbox\+after%3A[0-9]{10}")  # the bounded query `in:inbox after:<epoch seconds>`, the only date window
EPOCH_MIN, EPOCH_MAX = 10**9, 10**10
INITIAL_HOURS = 24
OVERLAP_SECONDS = 3600
MAX_PAGES = 60  # hard ceilings for the options of `snapshot`
MAX_SECONDS = 900
ADVANCE_POLLS = 4
CONTRACT_KEYS = {"row", "thread", "message", "unread", "empty", "range", "older", "newer", "grid", "view", "qbox", "max_rows"}
RETRY = {"unloaded", "no_rows_unproven", "error"}  # may be a page that is still loading

STRUCT_COUNTS = {"main": '[role="main"]', "grid": '[role="grid"]', "table": "table", "tr": "tr", "role_row": '[role="row"]',
                 "role_listitem": '[role="listitem"]', "checkbox": '[role="checkbox"]', "tablist": '[role="tablist"]', "tab": '[role="tab"]',
                 "older": '[aria-label*="Older"]', "newer": '[aria-label*="Newer"]', "iframe": "iframe"}
CATEGORY_NAMES = ("Primary", "Promotions", "Social", "Updates", "Forums")  # fixed Gmail UI vocabulary: the only tab labels that leave the page
STRUCT_CANDIDATES = ("data-thread-id", "data-legacy-thread-id", "data-legacy-last-message-id", "data-message-id", "data-item-id", "id")


class ProbeError(Exception):
    """A stable failure with no private text."""


# ---------- contract and fixed page programs ----------

def _sel_ok(s):
    return g.valid_selector(s)


def _ref_ok(x):
    return isinstance(x, dict) and set(x) == {"sel", "attr"} and _sel_ok(x["sel"]) and isinstance(x["attr"], str) and ATTR_RE.fullmatch(x["attr"]) is not None


def view_ok(v):
    return v in VIEWS or (isinstance(v, str) and WINDOW_VIEW_RE.fullmatch(v) is not None)


def is_search(v):
    """The all-category search views: the whole inbox or one bounded date window."""
    return v == SEARCH_VIEW or (isinstance(v, str) and WINDOW_VIEW_RE.fullmatch(v) is not None)


def _epoch(n):
    if not isinstance(n, int) or isinstance(n, bool) or not EPOCH_MIN <= n < EPOCH_MAX:
        raise ProbeError("the time is not a valid epoch second")
    return n


def window_query(lower):
    return f"in:inbox after:{_epoch(lower)}"


def window_view(lower):
    """The Gmail search hash of `in:inbox after:<epoch seconds>`. The epoch is absolute, so no time zone applies."""
    return f"#search/in%3Ainbox+after%3A{_epoch(lower)}"


def plan_window(now, watermark, hours=INITIAL_HOURS, overlap=OVERLAP_SECONDS):
    """The fixed lower bound of the next scan. No success yet: now minus `hours`. Otherwise the last success start minus the overlap, however long
    the downtime was. A long outage widens the window. It is never cut to `hours`."""
    _epoch(now)
    if not (isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= 168 and isinstance(overlap, int) and not isinstance(overlap, bool) and 0 <= overlap <= 86400):
        raise ProbeError("the lookback or overlap is out of range")
    if watermark is None:
        return {"kind": "baseline", "lower": _epoch(now - hours * 3600)}
    if _epoch(watermark) > now:
        raise ProbeError("the last success is in the future")
    return {"kind": "catchup", "lower": _epoch(watermark - overlap)}


def valid_contract(c):
    if not isinstance(c, dict) or not set(c) <= CONTRACT_KEYS or not {"row", "thread", "max_rows"} <= set(c):
        return False
    n = c["max_rows"]
    return (_sel_ok(c["row"]) and _ref_ok(c["thread"]) and (c.get("message") is None or _ref_ok(c["message"])) and isinstance(n, int)
            and not isinstance(n, bool) and 0 < n <= MAX_ROWS and all(c.get(k) is None or _sel_ok(c[k]) for k in ("unread", "empty", "range", "older", "newer", "grid", "qbox"))
            and view_ok(c.get("view", INBOX_VIEW)) and all(c.get(k) is None or '"' not in c[k] for k in ("older", "newer")))  # a double quote breaks the bare click grammar


def scan_js(c):
    """The one fixed read of a results page. The contract is data, the program is constant. Output: ids, counts and flags as JSON.

    Only visible elements count. Gmail keeps the other views in the DOM as hidden duplicates. Text is read only to match the range
    pattern (numbers) and one fixed empty phrase. Neither leaves the page as text."""
    if not valid_contract(c):
        raise ProbeError("the scan contract is not valid")
    return ("(() => { const C = " + json.dumps({k: c.get(k) for k in CONTRACT_KEYS}) + ";"
            " const q = (s) => s ? Array.from(document.querySelectorAll(s)) : [];"
            " const vis = (e) => !!(e.offsetParent || (e.getClientRects && e.getClientRects().length));"
            " const ids = (r, x) => x ? Array.from(r.querySelectorAll(x.sel)).slice(0, 4).map(e => String(e.getAttribute(x.attr) || '').slice(0, 120)) : null;"
            " const gs = C.grid ? q(C.grid).filter(vis) : null;"
            " const rows = C.grid ? (gs.length === 1 ? Array.from(gs[0].querySelectorAll(C.row)) : []) : q(C.row);"
            " const RE = /^\\s*([0-9][0-9,]*)\\s*[\\u2013-]\\s*([0-9][0-9,]*)\\s+of\\s+(many|[0-9][0-9,]*)\\s*$/;"
            " const num = (x) => Number(x.replace(/,/g, ''));"
            " const rg = new Set(); q(C.range).forEach(e => { if (!vis(e) || e.closest('[role=grid]')) return; const m = RE.exec(String(e.textContent || '').slice(0, 40));"
            " if (m) rg.add(JSON.stringify([num(m[1]), num(m[2]), m[3] === 'many' ? null : num(m[3])])); });"
            " const range = rg.size === 0 ? null : rg.size === 1 ? JSON.parse(Array.from(rg)[0]) : [null];"
            " const ctl = (s) => { if (!s) return null; const l = q(s).filter(vis); return {n: l.length, disabled: l.length === 1 && (l[0].getAttribute('aria-disabled') === 'true' || l[0].hasAttribute('disabled'))}; };"
            " const EM = ['No messages matched your search'];"
            " const empty = C.empty ? q(C.empty).filter(e => vis(e) && !e.closest('[role=grid]') && EM.some(p => String(e.textContent || '').trim().startsWith(p))).length : 0;"
            " const h = location.hash; const V = C.view || '#inbox'; const sk = V.indexOf('#search/') === 0;"
            " const dec = (x) => { try { return decodeURIComponent(x.replace(/\\+/g, ' ')); } catch (e) { return null; } };"
            " const hm = /^(.*?)(?:\\/p([0-9]{1,3}))?$/.exec(h); const want = sk ? dec(V.slice(8)) : null;"
            " const got = sk && hm[1].indexOf('#search/') === 0 ? dec(hm[1].slice(8)) : null; const hit = sk && want !== null && got === want;"
            " const qs = C.qbox ? q(C.qbox).filter(vis) : null;"
            " return JSON.stringify({host: location.hostname, proto: location.protocol, ready: document.readyState,"
            " view: sk ? (hit ? V : 'other') : /^#[a-z]{1,30}$/.test(h) ? h : 'other', page: hit ? Number(hm[2] || 1) : null,"
            " qn: qs ? qs.length : null, qm: qs ? qs.filter(e => want !== null && String(e.value || '').trim() === want).length : null,"
            " path_ok: /^\\/mail\\/u\\/[0-9]\\/$/.test(location.pathname), grids: gs ? gs.length : null,"
            " rows: rows.length, empty: empty, range: range, older: ctl(C.older), newer: ctl(C.newer),"
            " items: rows.slice(0, C.max_rows).map(r => ({t: ids(r, C.thread), m: ids(r, C.message), u: C.unread ? r.matches(C.unread) : false}))}); })()")


def structure_js(row="tr"):
    """Structure discovery: counts, tag names, attribute NAMES and the shape (count, distinct, length, charset) of candidate id attributes. No attribute value."""
    cnt = ", ".join(f"{k}: document.querySelectorAll({json.dumps(s)}).length" for k, s in STRUCT_COUNTS.items())
    cand = json.dumps(list(STRUCT_CANDIDATES))
    return ("(() => { const names = {}, tags = {}, cand = {}; const scope = document.querySelector('[role=\"main\"]') || document.body;"
            " const rows = Array.from(scope.querySelectorAll(" + json.dumps(row) + ")).slice(0, 5);"
            " rows.forEach(r => [r].concat(Array.from(r.querySelectorAll('*'))).forEach(el => { const t = el.tagName.toLowerCase(); tags[t] = (tags[t] || 0) + 1;"
            " Array.from(el.attributes).forEach(a => { names[a.name] = (names[a.name] || 0) + 1; }); }));"
            " " + cand + ".forEach(n => { const els = Array.from(scope.querySelectorAll('[' + n + ']')); const vals = els.map(e => String(e.getAttribute(n) || ''));"
            " cand[n] = {n: els.length, distinct: new Set(vals).size, min: vals.length ? Math.min.apply(null, vals.map(v => v.length)) : 0,"
            " max: vals.length ? Math.max.apply(null, vals.map(v => v.length)) : 0, charset_ok: vals.every(v => /^[A-Za-z0-9:#_.-]*$/.test(v))}; });"
            " const cats = " + json.dumps(list(CATEGORY_NAMES)) + "; const tabs = {}; let other = 0;"
            " document.querySelectorAll('[role=\"tab\"]').forEach(t => { const l = String(t.getAttribute('aria-label') || ''); const c = cats.find(n => l === n || l.startsWith(n + ' ') || l.startsWith(n + ','));"
            " if (!c) { other++; return; } const o = tabs[c] || (tabs[c] = {n: 0, selected: false}); o.n++; o.selected = o.selected || t.getAttribute('aria-selected') === 'true'; });"
            " const h = location.hash; return JSON.stringify({host: location.hostname, proto: location.protocol, ready: document.readyState,"
            " view: /^#[a-z]{1,30}$/.test(h) ? h : 'other', row_sample: rows.length, counts: {" + cnt + "}, tags: tags, attrs: names, cand: cand, tabs: tabs, tabs_other: other}); })()")


# ---------- pure parsing (unit tested) ----------

def _json(raw):
    try:
        d = json.loads(raw)
        if isinstance(d, str):
            d = json.loads(d)
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def one_id(x):
    """A row may carry its id on up to three elements, but every one must hold the same valid id."""
    return (isinstance(x, list) and 1 <= len(x) <= 3 and all(isinstance(v, str) for v in x) and len(set(x)) == 1
            and ID_RE.fullmatch(x[0]) is not None)


def bad(why):
    return {"status": "unsupported_dom", "reason": why}


def parse_scan(raw, c):
    """{"status": ...} plus, only for ok, partial and empty, the in-memory ids. The status never says empty without a positive marker."""
    d = _json(raw)
    if d is None:
        return {"status": "malformed"}
    if d.get("proto") != "https:" or g.host_class(d.get("host")) != "mailbox":
        return {"status": "not_mailbox"}
    if d.get("ready") != "complete":
        return {"status": "unloaded"}
    view = c.get("view", INBOX_VIEW)
    if d.get("view") != view or d.get("path_ok") is not True:
        return {"status": "not_inbox"}
    if c.get("grid") and d.get("grids") != 1:
        return bad("grid_count")
    if c.get("qbox") and not (_int(d.get("qn")) and _int(d.get("qm")) and d["qn"] == 1 and d["qm"] == 1):
        return bad("query_box")
    rows, items, empty = d.get("rows"), d.get("items"), d.get("empty")
    if not (_int(rows) and _int(empty) and isinstance(items, list) and len(items) == min(rows, c["max_rows"])):
        return bad("row_count_mismatch")
    pg = d.get("page")
    if is_search(view) and not (_int(pg) and pg >= 1):
        return bad("page_shape")
    out = {"status": "ok", "view": view, "page": pg if is_search(view) else None, "range": None, "older": None, "newer": None}
    rg = d.get("range")
    if rg is not None:
        if not (isinstance(rg, list) and len(rg) == 3 and _int(rg[0]) and _int(rg[1]) and (rg[2] is None or _int(rg[2]))):
            return bad("range_shape")
        out["range"] = dict(zip(("start", "end", "total"), rg))
    for key in ("older", "newer"):
        ol = d.get(key)
        if ol is not None:
            if not (isinstance(ol, dict) and _int(ol.get("n")) and ol["n"] <= 1 and isinstance(ol.get("disabled"), bool)):
                return bad(key + "_shape")
            out[key] = {"present": ol["n"] == 1, "disabled": ol["disabled"]}
    if rows == 0:
        if not c.get("empty") or empty == 0:
            return {"status": "no_rows_unproven"}
        if empty > 1:
            return bad("marker_count")
        return {**out, "status": "empty", "threads": [], "messages": {} if c.get("message") else None, "rows": 0}
    if empty:
        return bad("marker_with_rows")
    if out["range"] and rows <= c["max_rows"] and out["range"]["end"] - out["range"]["start"] + 1 != rows:
        return bad("range_rows_mismatch")
    threads, msgs, unread = [], {}, 0
    for it in items:
        t = it.get("t") if isinstance(it, dict) else None
        m = it.get("m") if isinstance(it, dict) else None
        if not one_id(t):
            return bad("row_id_shape")
        if c.get("message"):
            if not one_id(m):
                return bad("message_id_shape")
            msgs[t[0]] = m[0]
        threads.append(t[0])
        unread += it.get("u") is True
    if len(set(threads)) != len(threads):
        return bad("duplicate_thread_id")
    return {**out, "status": "partial" if rows > c["max_rows"] else "ok", "threads": threads, "messages": msgs if c.get("message") else None,
            "rows": rows, "unread": unread}


def report(r):
    """Counts and booleans of one parsed scan. Never an id."""
    rep = {"status": r["status"]}
    if r.get("view"):
        rep["view"] = r["view"]
    if "reason" in r:
        rep["reason"] = r["reason"]
    if "threads" in r:
        rep.update(rows=r["rows"], message_ids=len(r["messages"]) if r["messages"] is not None else 0, unique_threads=len(set(r["threads"])),
                   range=r["range"], older=r["older"])
        if r.get("page") is not None:
            rep["page"] = r["page"]
        if r.get("newer") is not None:
            rep["newer"] = r["newer"]
    return rep


def compare(a, b):
    return {"same_rows": set(a["threads"]) == set(b["threads"]), "same_order": a["threads"] == b["threads"], "same_messages": a["messages"] == b["messages"]}


def continuity(prev, cur):
    """baseline, overlap (the oldest previous row is still on the page) or gap (a possible miss). Necessary, not sufficient, for no missed arrival."""
    if not prev:
        return "baseline"
    return "overlap" if prev[-1] in set(cur) else "gap"


def coverage(rep, cont, message_ids, observed_arrival=False):
    """What one page can claim. Each claim needs positive evidence: a visible, disabled Older control (or a verified empty marker) and the
    all-category view. A missing control proves nothing. `complete` also needs an observed arrival."""
    limits = []
    older, status, view = rep.get("older"), rep.get("status"), rep.get("view")
    ended = older is not None and older["present"] and older["disabled"]
    covers = is_search(view) and (status == "empty" or (status == "ok" and ended))
    if status not in ("ok", "empty"):
        limits.append("scan_not_ok")
    if status != "empty":
        if older is None or not older["present"]:
            limits.append("pagination_unknown")
        elif not older["disabled"]:
            limits.append("older_pages_unread")
    if not is_search(view):
        limits.append("categories_not_covered")
    if cont == "gap":
        limits.append("possible_gap_since_last_scan")
    if not message_ids:
        limits.append("replies_in_existing_threads_undetected")
    if not observed_arrival:
        limits.append("arrival_never_observed")
    return {"page_covers_inbox": covers, "complete": not limits, "limits": limits}


def collect(first, advance, max_pages, max_seconds, clock=time.monotonic):
    """Walk the pages of the search view from page 1 to a disabled Older control. `first` is the parsed page 1. `advance(prev)` clicks
    Older and returns the parsed next page, or None. Any cap, stall or doubt gives `incomplete` with a reason. Only a disabled control ends
    the walk as `complete`. Overlapping rows are deduped and counted."""
    t0 = clock()
    out = {"status": "incomplete", "reason": None, "view": first.get("view"), "pages": 0, "rows": 0, "unique": 0, "duplicates": 0, "threads": [], "messages": None,
           "end": None}

    def stop(why):
        out["reason"] = why
        return out
    if first.get("status") == "empty":
        return {**out, "status": "empty", "pages": 1, "messages": {} if first["messages"] is not None else None, "end": "empty_marker"}
    if first.get("status") != "ok":
        return stop("first_page_not_ok:" + str(first.get("status")))
    nw, page, n, prev_end = first.get("newer"), first, 0, None
    if first.get("page") != 1 or not (nw and nw["present"] and nw["disabled"]) or (first["range"] and first["range"]["start"] != 1):
        return stop("not_first_page")
    out["messages"] = {} if first["messages"] is not None else None
    seen = {}
    while True:
        n += 1
        out["pages"] = n
        if n > 1:
            if page.get("page") != n:
                return stop("page_number")
        new = 0
        for t in page["threads"]:
            out["rows"] += 1
            m = page["messages"][t] if page["messages"] is not None else None
            if t in seen:
                out["duplicates"] += 1
                if seen[t] != m:
                    return stop("changed_during_walk")
                continue
            seen[t] = m
            out["threads"].append(t)
            new += 1
        out["unique"] = len(seen)
        if page["messages"] is not None:
            out["messages"] = dict(seen)
        if n > 1:
            if new == 0:
                return stop("no_progress")
            if page["range"] and prev_end is not None and page["range"]["start"] != prev_end + 1:
                return stop("range_discontinuity")
        prev_end = page["range"]["end"] if page["range"] else None
        ol = page.get("older")
        if ol is None or not ol["present"]:
            return stop("pager_missing")
        if ol["disabled"]:
            total = page["range"]["total"] if page["range"] else None
            if total is not None and total != len(seen):
                return stop("total_mismatch")
            out.update(status="complete", end="older_disabled")
            return out
        if n >= max_pages:
            return stop("page_cap")
        if clock() - t0 > max_seconds:
            return stop("time_cap")
        nxt = advance(page)
        if nxt is None:
            return stop("advance_failed")
        if nxt.get("status") != "ok":
            return stop("page_not_ok:" + str(nxt.get("status")))
        page = nxt


def recheck(snap, again, first):
    """A walk is stable only when a fresh read of page 1 equals the first read (same ordered threads, same last-message ids). Any change refuses it.
    This detects arrivals and removals above the walk. It is not a point-in-time snapshot."""
    out = {**snap, "stable": False}
    if snap.get("status") not in ("complete", "empty"):
        return out
    if again.get("status") not in ("ok", "empty") or again.get("threads") != first.get("threads") or again.get("messages") != first.get("messages"):
        return {**out, "status": "unstable", "reason": "first_page_changed"}
    return {**out, "stable": True}


def snapshot_report(r):
    """Counts and fixed words of a walked snapshot. Never an id."""
    return {k: r.get(k) for k in ("status", "reason", "view", "pages", "rows", "unique", "duplicates", "end", "stable", "seconds", "page_seconds")}


def snapshot_coverage(snap, message_ids, observed_arrival=False):
    """`snapshot_complete`: the whole inbox was read and the first page stayed the same. `complete` adds an observed arrival, which the probe never claims."""
    limits = []
    done = snap.get("status") in ("complete", "empty") and snap.get("stable") is True and is_search(snap.get("view"))
    window = snap.get("view") != SEARCH_VIEW
    if snap.get("status") not in ("complete", "empty") or snap.get("stable") is not True:
        limits.append("snapshot_unstable" if snap.get("status") == "unstable" else "snapshot_incomplete")
    if not is_search(snap.get("view")):
        limits.append("categories_not_covered")
    if window:
        limits.append("date_window_only")
    if not message_ids:
        limits.append("replies_in_existing_threads_undetected")
    if not observed_arrival:
        limits.append("arrival_never_observed")
    return {"snapshot_complete": done, "complete": not limits, "scope": "date_window" if window else "inbox", "limits": limits}


def stability(snaps):
    """Agreement of the reads of one scan. Every flag needs every read to be ok. A filtered subset never counts."""
    good = [x for x in snaps if x["status"] in ("ok", "empty")]
    all_ok = bool(snaps) and len(good) == len(snaps)
    cmp = lambda k: all_ok and all(compare(good[0], x)[k] for x in good)  # noqa: E731
    return {"reads": len(snaps), "all_ok": all_ok, "all_same_rows": cmp("same_rows"), "all_same_order": cmp("same_order"),
            "all_same_messages": cmp("same_messages")}


def digest(salt, value):
    return hashlib.sha256((salt + "\0" + str(value)).encode()).hexdigest()[:24]


def diff_seen(seen, cur):
    """Counts of new, changed and unchanged threads against the seen map. With no message ids a reply is invisible."""
    new = sum(t not in seen for t in cur)
    changed = sum(t in seen and seen[t] != m for t, m in cur.items())
    return {"new_threads": new, "changed_threads": changed, "unchanged": len(cur) - new - changed}


def _read_store(path):
    """The seen store as a dict, or None when the file does not exist. A corrupt or foreign file is an error, never a fresh baseline."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    except OSError:
        raise ProbeError("the seen store cannot be read") from None
    try:
        d = json.loads(text)
    except ValueError:
        raise ProbeError("the seen store is corrupt. It was not changed") from None
    if not (isinstance(d, dict) and isinstance(d.get("salt"), str) and all(isinstance(v, dict) for k, v in d.items() if k != "salt")):
        raise ProbeError("the seen store has an unknown shape. It was not changed")
    return d


def _atomic_write(p, text):
    p = Path(p)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_seen(path, label, mapping, merge=False):
    """Persist salted digests only (mode 0600), by atomic replace. The salt lives in the same private file. Other labels stay.
    `merge` keeps the earlier threads of the label (a date window shows only recent threads)."""
    d = _read_store(path) or {"salt": secrets.token_hex(16)}
    new = {digest(d["salt"], t): None if m is None else digest(d["salt"], m) for t, m in mapping.items()}
    d[label] = {**d.get(label, {}), **new} if merge else new
    _atomic_write(path, json.dumps(d, sort_keys=True))


def maybe_save_seen(path, label, mapping, cov, merge=False):
    """The seen state advances only after a complete scan. A partial or failed scan leaves it as it was."""
    if cov.get("snapshot_complete") is not True:
        return False
    save_seen(path, label, mapping, merge)
    return True


def read_watermark(path, label):
    """The start epoch of the last stable complete scan of `label`, or None. A corrupt or foreign file is an error, never a fresh baseline."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    except OSError:
        raise ProbeError("the watermark file cannot be read") from None
    try:
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError
        for v in d.values():
            _epoch(v)
    except (ValueError, ProbeError):
        raise ProbeError("the watermark file is corrupt. It was not changed") from None
    return d.get(label)


def maybe_save_watermark(path, label, start, cov):
    """The watermark is the START of a stable complete scan, set only then and never moved back. Anything else leaves it."""
    if cov.get("snapshot_complete") is not True:
        return False
    cur = read_watermark(path, label)
    if cur is not None and _epoch(start) <= cur:
        return False
    try:
        d = json.loads(Path(path).read_text())
    except FileNotFoundError:
        d = {}
    _atomic_write(path, json.dumps({**d, label: _epoch(start)}, sort_keys=True))
    return True


def load_seen(path, label, cur):
    """The earlier state of the threads that are on the page now, as ids comparable with `cur` (a changed message id becomes a sentinel)."""
    d = _read_store(path)
    if d is None or label not in d:
        return {}
    old, salt = d[label], d["salt"]
    out = {}
    for t, m in cur.items():
        k = digest(salt, t)
        if k in old:
            out[t] = m if old[k] == (None if m is None else digest(salt, m)) else "\0changed"
    return out


def example_report():
    """The documented output shape, built from synthetic ids only."""
    c = {"row": "r", "thread": {"sel": "s", "attr": "a"}, "message": {"sel": "s", "attr": "b"}, "max_rows": 50}
    snap = parse_scan(json.dumps({"host": "mail.google.com", "proto": "https:", "ready": "complete", "view": INBOX_VIEW, "path_ok": True, "rows": 2,
                                  "empty": 0, "range": [1, 2, 2], "older": {"n": 1, "disabled": True},
                                  "items": [{"t": ["synthetic-thread-1"], "m": ["synthetic-msg-1"], "u": True}, {"t": ["synthetic-thread-2"], "m": ["synthetic-msg-2"], "u": False}]}), c)
    rep = report(snap)
    return {"contract_version": 1, "account": "A", "reads": [rep] * 4, "stability": {"reads": 4, **compare(snap, snap)},
            "continuity": "baseline", "seen": diff_seen({}, snap["messages"]), "coverage": coverage(rep, "baseline", True)}


# ---------- navigation ----------

def _index(i):
    if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i <= 9:
        raise ProbeError("the account index must be one digit")
    return i


def inbox_url(i, view=INBOX_VIEW):
    return f"https://{g.MAILBOX_HOST}/mail/u/{_index(i)}/{view}"


def root_url(i):
    return f"https://{g.MAILBOX_HOST}/mail/u/{_index(i)}/"


def away_url(i):
    return f"https://{g.MAILBOX_HOST}/mail/u/{_index(i)}/#sent"


# ---------- one verified read ----------

def read_once(env, selector, who, labels, contract, identity=None, sleep=time.sleep):
    """One scan wrapped by identity checks. Status is the scan status, identity_refused or identity_changed. Ids appear only on ok, partial and empty."""
    js = scan_js(contract)
    identity = identity or (lambda env_, sel, labs: g.verify_identity(env_, sel, labs, who))  # one expect: wanted must be the only registered match
    before = after = "unknown"
    res = {"status": "error"}
    for i in range(READ_ATTEMPTS):
        if i:
            sleep(3)
        before = identity(env, selector, labels)["identity"]
        if before != who:
            res = {"status": "identity_refused"}
            continue
        err, text = env.call("eval", js=js)
        res = {"status": "error"} if err else parse_scan(text, contract)
        if res["status"] not in RETRY:
            break
    res.update(identity_before=before)
    if res["status"] == "identity_refused":
        return res
    after = identity(env, selector, labels)["identity"]
    res["identity_after"] = after
    if after != who:
        return {"status": "identity_changed", "identity_before": before, "identity_after": after}
    return res


# ---------- live commands ----------

def new_evidence_dir(path):
    path = Path(path)
    if path.exists():
        raise ProbeError("the evidence directory exists. Earlier results are never overwritten")
    path.mkdir(mode=0o700, parents=True)
    return path


def write_evidence(a, name, data):
    n = len(list(Path(a.evidence).glob("[0-9][0-9]-*.json")))
    g.write_private(Path(a.evidence) / f"{n:02d}-{name}.json", data)


def read_contract(a):
    try:
        c = json.loads(Path(a.contract).read_text())
    except (OSError, ValueError):
        raise ProbeError("the contract file is missing or invalid") from None
    if not valid_contract(c):
        raise ProbeError("the scan contract is not valid")
    return c


def go(env, url):
    err, _ = env.call("step", steps=f"goto {url}", compact=True)
    return not err


def click(env, selector):
    """Click the one Older control. bare refuses a hidden target. The receipt must be the exact success text."""
    err, text = env.call("step", steps=f'click css="{selector}"', compact=True)
    return not err and isinstance(text, str) and re.fullmatch(r"ok \([0-9]+ ms\)(: .*)?", text.strip().splitlines()[0] if text.strip() else "") is not None  # bare may append a page note


def snapshot_walk(env, selector, who, labels, c, index, max_pages, max_seconds, identity=None, sleep=time.sleep, clock=time.monotonic):
    """The bounded full snapshot: page 1 of the search view, Older to the end, then a fresh navigation and a recheck of page 1.
    Every page read is wrapped by identity checks. A click is followed by a bounded wait for the next page number."""
    if not (isinstance(max_pages, int) and not isinstance(max_pages, bool) and 0 < max_pages <= MAX_PAGES
            and isinstance(max_seconds, (int, float)) and not isinstance(max_seconds, bool) and 0 < max_seconds <= MAX_SECONDS):
        raise ProbeError("the page or time cap is out of range")
    if not valid_contract(c) or not is_search(c.get("view")) or not all(c.get(k) for k in ("grid", "older", "newer", "range")):
        raise ProbeError("the contract needs the search view, a grid, a range, Older and Newer")
    url = inbox_url(index, c["view"])
    t0 = clock()
    times = []
    read = lambda lab=labels: read_once(env, selector, who, lab, c, identity=identity, sleep=sleep)  # noqa: E731  # every page: all registered accounts

    def advance(prev):
        t = clock()
        if not click(env, c["older"]):
            return None
        for _ in range(ADVANCE_POLLS):
            sleep(2)
            r = read()
            if r["status"] != "ok" or r.get("page") != prev.get("page"):
                break
        times.append(round(clock() - t, 1))
        return r
    t = clock()
    first = read(labels) if go(env, url) else {"status": "navigation_failed"}
    times.append(round(clock() - t, 1))
    snap = collect(first, advance, max_pages, max_seconds, clock)
    again = {}
    if snap["status"] in ("complete", "empty"):
        again = read(labels) if go(env, url) else {"status": "navigation_failed"}
    snap = recheck(snap, again, first)
    return {**snap, "seconds": round(clock() - t0, 1), "page_seconds": times}


def recent_report(plan, start, snap):
    """The plan and the walked snapshot as counts and fixed words. No id and no query text."""
    return {"contract_version": 3, "plan": {"kind": plan["kind"], "window_seconds": start - plan["lower"]}, "snapshot": {**snapshot_report(snap), "view": "date_window" if snap.get("view") != SEARCH_VIEW else SEARCH_VIEW}}


def classify_structure(raw):
    """host class, ready, view shape and whitelisted counts, tag names, attribute names and candidate shapes. Never a value."""
    d = _json(raw)
    if d is None or d.get("proto") != "https:" or g.host_class(d.get("host")) != "mailbox":
        return {"host_class": "unknown"}
    ok = lambda m: {k: v for k, v in m.items() if isinstance(k, str) and ATTR_RE.fullmatch(k) and _int(v)} if isinstance(m, dict) else {}  # noqa: E731
    cand = {}
    for k, s in (d.get("cand") or {}).items() if isinstance(d.get("cand"), dict) else ():
        if k in STRUCT_CANDIDATES and isinstance(s, dict) and all(_int(s.get(x)) for x in ("n", "distinct", "min", "max")):
            cand[k] = {x: s[x] for x in ("n", "distinct", "min", "max")} | {"charset_ok": s.get("charset_ok") is True}
    counts = d.get("counts") if isinstance(d.get("counts"), dict) else {}
    tabs = {k: {"n": t["n"], "selected": t.get("selected") is True} for k, t in d["tabs"].items() if k in CATEGORY_NAMES and isinstance(t, dict) and _int(t.get("n"))} \
        if isinstance(d.get("tabs"), dict) else {}
    return {"tabs": tabs, "tabs_other": d.get("tabs_other") if _int(d.get("tabs_other")) else 0, "host_class": "mailbox", "ready": d.get("ready") if d.get("ready") in ("loading", "interactive", "complete") else "unknown",
            "view": d.get("view") if isinstance(d.get("view"), str) and (d["view"] == "other" or re.fullmatch(r"#[a-z]{1,30}", d["view"])) else "other",
            "row_sample": d.get("row_sample") if _int(d.get("row_sample")) else 0, "counts": {k: counts[k] for k in STRUCT_COUNTS if _int(counts.get(k))},
            "tags": ok(d.get("tags")), "attrs": ok(d.get("attrs")), "cand": cand}


def verified_env(a):
    env = g.attach(a)
    items, selector = g.read_refs(a)
    if a.account not in items:
        raise ProbeError("that account is not registered")
    return env, items, selector


def cmd_structure(a):
    env, items, selector = verified_env(a)
    labels = "".join(sorted(items))
    go(env, inbox_url(a.index))
    who = g.verify_identity(env, selector, labels)["identity"]
    if who != a.account:
        out = {"identity": who, "refused": True}
    else:
        err, text = env.call("eval", js=structure_js(a.row))
        again = g.verify_identity(env, selector, labels)["identity"]
        out = {"identity": who, "identity_after": again, **({"host_class": "unknown"} if err else classify_structure(text))}
        if again != who:
            out = {"identity": who, "identity_after": again, "refused": True}
    write_evidence(a, "structure", out)
    print(json.dumps(out))
    return 0 if not out.get("refused") and out.get("host_class") == "mailbox" else 1


def cmd_scan(a):
    env, items, selector = verified_env(a)
    c = read_contract(a)
    labels = "".join(sorted(items))
    plan = [("first", inbox_url(a.index)), ("again", None), ("away_back", (away_url(a.index), inbox_url(a.index))), ("refresh", (root_url(a.index), None))]
    reads, snaps = [], []
    for name, nav in plan:
        for url in (nav if isinstance(nav, tuple) else (nav,)):
            if url:
                go(env, url)
        if name == "refresh":
            time.sleep(4)
        r = read_once(env, selector, a.account, labels, c)
        snaps.append(r)
        reads.append({"read": name, **{k: v for k, v in report(r).items()}, "identity_before": r.get("identity_before"), "identity_after": r.get("identity_after")})
    good = [s for s in snaps if s["status"] in ("ok", "empty")]
    base = good[0] if good else None
    stab = stability(snaps)
    out = {"contract_version": 1, "account": a.account, "reads": reads, "stability": stab}
    if base:
        store = Path(a.evidence) / "private" / "seen.json"
        cur = base["messages"] if base["messages"] is not None else {t: None for t in base["threads"]}
        prev = load_seen(store, a.account, cur)
        out["continuity"] = "baseline" if not prev else continuity(list(prev), base["threads"])
        out["seen"] = diff_seen(prev, cur)
        out["coverage"] = coverage(report(base), out["continuity"], base["messages"] is not None)
        out["seen_advanced"] = maybe_save_seen(store, a.account, cur, out["coverage"])
    write_evidence(a, f"scan-{a.account}", out)
    print(json.dumps(out))
    return 0 if stab["all_ok"] and stab["all_same_rows"] else 1


def cmd_snapshot(a):
    """The bounded full snapshot of one account. Output: counts, fixed words and timings. The seen store advances only for a stable complete snapshot."""
    env, items, selector = verified_env(a)
    c = read_contract(a)
    labels = "".join(sorted(items))
    snap = snapshot_walk(env, selector, a.account, labels, c, a.index, a.max_pages, a.max_seconds)
    out = {"contract_version": 2, "account": a.account, "snapshot": snapshot_report(snap), "caps": {"max_pages": a.max_pages, "max_seconds": a.max_seconds}}
    cov = snapshot_coverage(snap, snap.get("messages") is not None)
    out["coverage"] = cov
    if snap.get("stable") is True:
        store = Path(a.evidence) / "private" / "seen.json"
        cur = snap["messages"] if snap["messages"] is not None else {t: None for t in snap["threads"]}
        out["seen"] = diff_seen(load_seen(store, a.account, cur), cur)
        out["seen_advanced"] = maybe_save_seen(store, a.account, cur, cov)
    write_evidence(a, f"snapshot-{a.account}", out)
    print(json.dumps(out))
    return 0 if cov["snapshot_complete"] else 1


def cmd_recent(a):
    """One bounded date-window scan of `in:inbox after:<lower>`. The seen store and the watermark advance only for a stable complete snapshot.
    A cap, a refusal or an unstable page leaves both as they were and the scan is incomplete, never `no new mail`."""
    env, items, selector = verified_env(a)
    c = read_contract(a)
    labels = "".join(sorted(items))
    priv = Path(a.evidence) / "private"
    start = int(time.time())
    plan = plan_window(start, read_watermark(priv / "watermark.json", a.account), a.lookback_hours, a.overlap_seconds)
    snap = snapshot_walk(env, selector, a.account, labels, {**c, "view": window_view(plan["lower"])}, a.index, a.max_pages, a.max_seconds)
    out = recent_report(plan, start, snap)
    out.update(account=a.account, caps={"max_pages": a.max_pages, "max_seconds": a.max_seconds})
    cov = out["coverage"] = snapshot_coverage(snap, snap.get("messages") is not None)
    if snap.get("stable") is True:
        cur = snap["messages"] if snap["messages"] is not None else {t: None for t in snap["threads"]}
        out["seen"] = diff_seen(load_seen(priv / "seen.json", a.account, cur), cur)
        out["seen_advanced"] = maybe_save_seen(priv / "seen.json", a.account, cur, cov, merge=True)
        out["watermark_advanced"] = maybe_save_watermark(priv / "watermark.json", a.account, start, cov)
    write_evidence(a, f"recent-{a.account}", out)
    print(json.dumps(out))
    return 0 if cov["snapshot_complete"] else 1


def cmd_epoch(a):
    """The executor identity max_epoch integer of the guest. Metadata only: no id, no secret, no change."""
    env = g.attach(a)
    code, out, _ = env.run(["/usr/bin/python3", "-c", "import json;print(int(json.load(open('/home/deck/.local/state/deck-executor/identity.json'))['max_epoch']))"])
    val = int(out.strip()) if code == 0 and out.strip().isdigit() else None
    print(json.dumps({"max_epoch": val}))
    return 0 if val is not None else 1


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="the private trial directory of gmail_accounts_e2e.py (resumed, running)")
    ap.add_argument("--evidence", required=True, help="a NEW private directory for sanitized per-operation evidence")
    ap.add_argument("--base")
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    sub = ap.add_subparsers(dest="sub", required=True)
    s = sub.add_parser("structure")
    s.add_argument("--account", required=True, choices=("A", "B", "C"))
    s.add_argument("--index", required=True, type=int)
    s.add_argument("--row", default="tr", help="row selector to sample (default: tr)")
    c = sub.add_parser("scan")
    c.add_argument("--account", required=True, choices=("A", "B", "C"))
    c.add_argument("--index", required=True, type=int)
    c.add_argument("--contract", required=True, help="private JSON scan contract")
    n = sub.add_parser("snapshot")
    n.add_argument("--account", required=True, choices=("A", "B", "C"))
    n.add_argument("--index", required=True, type=int)
    n.add_argument("--contract", required=True, help="private JSON scan contract, version 2 (search view)")
    n.add_argument("--max-pages", type=int, default=40)
    n.add_argument("--max-seconds", type=float, default=300)
    r = sub.add_parser("recent")
    r.add_argument("--account", required=True, choices=("A", "B", "C"))
    r.add_argument("--index", required=True, type=int)
    r.add_argument("--contract", required=True, help="private JSON scan contract, version 2 with `qbox` (the view is set by the plan)")
    r.add_argument("--max-pages", type=int, default=3)
    r.add_argument("--max-seconds", type=float, default=90)
    r.add_argument("--lookback-hours", type=int, default=INITIAL_HOURS, help="first scan only")
    r.add_argument("--overlap-seconds", type=int, default=OVERLAP_SECONDS)
    sub.add_parser("epoch")
    a = ap.parse_args(argv)
    a.lock = None
    return a


def main(argv=None):
    a = parse(argv)
    try:
        if a.sub != "epoch" and not Path(a.evidence).exists():
            new_evidence_dir(a.evidence)
        lock = g.op_lock(a)
        try:
            return {"structure": cmd_structure, "scan": cmd_scan, "snapshot": cmd_snapshot, "recent": cmd_recent, "epoch": cmd_epoch}[a.sub](a)
        finally:
            lock.close()
    except (ProbeError, g.TrialError) as x:
        print(f"STOPPED: {str(x)[:200]}", file=sys.stderr)
        return 5
    except Exception as x:  # noqa: BLE001 - the message may hold private text
        print(f"FAILED: {type(x).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
