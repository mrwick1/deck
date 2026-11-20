#!/usr/bin/env python3
"""Narrow live acceptance helper for the Gmail thread view and the Deck labels.

It attaches to the retained VM after `gmail_accounts_e2e.py resume`. Deck must not run: this helper and Deck never own
the browser at once. Every program that reads the page is fixed here and returns booleans, counts and generic UI words
only. No message text, address or label name that the owner wrote leaves the guest. Writes are limited to the four
fixed Deck label names and to steps that pass `safe_step`.

  shape   open one READ thread of one account and report the structure of the thread view (counts and booleans)
  words   report which generic toolbar words exist on the open thread
  probe   run one named fixed read-only program on the open page
  step    run one step text that passes `safe_step`; print only the receipt
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gmail_accounts_e2e as ga  # noqa: E402

HOST = "mail.google.com"
LABELS = ("Deck/Needs reply", "Deck/Action", "Deck/Waiting", "Deck/FYI")
BANNED = re.compile(r"archive|delete|trash|spam|send|mute|unread|report|snooze|forward|reply|discard", re.I)
GENERIC = ["Labels", "Move to", "Archive", "Delete", "Mark as unread", "Back to Inbox", "Back to search results", "More", "Older", "Newer",
           "Apply", "Create new", "Manage labels", "Search for label", "Label as:", "Labels:", "Create", "Cancel", "Done"]


def safe_step(text):
    """A step text that holds no banned word and no unexpanded placeholder. This is no proof of safety: the caller reads it first."""
    return isinstance(text, str) and 0 < len(text) <= 600 and "${" not in text and not BANNED.search(text.replace(LABELS[0], ""))


def wait_complete():
    return 'timeout 15000\nexpect js "document.readyState === \'complete\'"'


# Fixed read-only programs. Each returns JSON of booleans, counts and generic words. Never element text beyond GENERIC.
def js_shape(thread, message):
    return ("(() => { const vis = (e) => !!(e.offsetParent || (e.getClientRects && e.getClientRects().length));"
            " const m = document.querySelector('[role=\"main\"]');"
            " const all = m ? Array.from(m.querySelectorAll('[data-legacy-message-id]')) : [];"
            " const top = all.filter(e => !all.some(o => o !== e && o.contains(e)));"
            " const tag = (e) => e.tagName.toLowerCase() + '.' + String(e.className || '').split(/\\s+/).filter(Boolean).slice(0, 3).join('.');"
            " return JSON.stringify({host: location.hostname, hash_tail: location.hash.split('/').pop() === " + json.dumps(thread) + ","
            " main: document.querySelectorAll('[role=\"main\"]').length, msg_all: all.length, msg_top: top.length, msg_visible: top.filter(vis).length,"
            " msg_has_expected: all.some(e => e.getAttribute('data-legacy-message-id') === " + json.dumps(message) + "),"
            " msg_top_has_expected: top.some(e => e.getAttribute('data-legacy-message-id') === " + json.dumps(message) + "),"
            " msg_top_tags: Array.from(new Set(top.map(tag))).slice(0, 5),"
            " msg_text_lens: top.slice(0, 5).map(e => String(e.innerText || '').length),"
            " main_text_len: m ? String(m.innerText || '').length : -1,"
            " thread_attr_in_main: m ? m.querySelectorAll('[data-legacy-thread-id]').length : -1,"
            " h2_in_main: m ? m.querySelectorAll('h2').length : -1}); })()")


def js_words():
    g = json.dumps(GENERIC)
    return ("(() => { const G = " + g + "; const out = {}; for (const e of document.querySelectorAll('[aria-label],[data-tooltip],[title]')) {"
            " for (const a of ['aria-label', 'data-tooltip', 'title']) { const v = e.getAttribute(a); if (v && G.includes(v)) {"
            " const k = v + '|' + a + '|' + e.tagName.toLowerCase() + '|' + (e.getAttribute('role') || ''); out[k] = (out[k] || 0) + 1; } } }"
            " return JSON.stringify(out); })()")


def js_menu():
    """The open label menu: roles and counts. Item text is reported only when it is one of the four Deck names."""
    names = json.dumps(list(LABELS))
    return ("(() => { const N = " + names + "; const q = (s) => Array.from(document.querySelectorAll(s));"
            " const tag = (e) => e.tagName.toLowerCase() + '.' + String(e.className || '').split(/\\s+/).filter(Boolean).slice(0, 3).join('.');"
            " const items = q('[role=\"menuitemcheckbox\"],[role=\"menuitem\"],[role=\"checkbox\"],[role=\"option\"]');"
            " const deck = items.filter(e => N.includes(String(e.textContent || '').trim()) || N.includes(e.getAttribute('title') || '') || N.includes(e.getAttribute('aria-label') || ''));"
            " return JSON.stringify({menus: q('[role=\"menu\"],[role=\"listbox\"],[role=\"dialog\"]').length, items: items.length,"
            " deck_items: deck.map(e => ({tag: tag(e), role: e.getAttribute('role'), checked: e.getAttribute('aria-checked'), "
            "name: N.find(n => String(e.textContent || '').trim() === n || e.getAttribute('title') === n || e.getAttribute('aria-label') === n)})),"
            " inputs: q('input[type=\"text\"],input:not([type])').filter(e => e.offsetParent).length}); })()")


def js_chips():
    """Where the four Deck names appear in the open thread: tag chains only."""
    names = json.dumps(list(LABELS))
    return ("(() => { const N = " + names + "; const m = document.querySelector('[role=\"main\"]'); if (!m) return JSON.stringify({main: false});"
            " const chain = (e) => { const c = []; for (let x = e; x && x !== m && c.length < 5; x = x.parentElement) c.push(x.tagName.toLowerCase() + '.' + String(x.className || '').split(/\\s+/).filter(Boolean).slice(0, 3).join('.') + (x.getAttribute('role') ? '[role=' + x.getAttribute('role') + ']' : '') + (x.getAttribute('aria-label') && N.includes(x.getAttribute('aria-label')) ? '[aria-label=name]' : '') + (x.getAttribute('title') && N.includes(x.getAttribute('title')) ? '[title=name]' : '')); return c; };"
            " const hits = []; for (const e of m.querySelectorAll('*')) { if (e.children.length === 0 && N.includes(String(e.textContent || '').trim())) hits.push({name: String(e.textContent).trim(), chain: chain(e)}); }"
            " return JSON.stringify({main: true, hits: hits.slice(0, 8)}); })()")


def js_rowindex():
    """The 1-based position of the first read listed thread among its siblings, and the row selector facts. Booleans and counts only."""
    return ("(() => { const rows = Array.from(document.querySelectorAll('tr.zA')); const i = rows.findIndex(r => !/\\bzE\\b/.test(r.className) && r.querySelector('[data-legacy-thread-id]'));"
            " const r = rows[i]; const sib = r ? Array.from(r.parentElement.children).indexOf(r) + 1 : -1; return JSON.stringify({index: i, nth_child: sib, tbody_count: document.querySelectorAll('tbody').length,"
            " grid: r ? !!r.closest('[role=\"grid\"]') : false, row_role: r ? r.getAttribute('role') : null, tabindex: r ? r.getAttribute('tabindex') : null}); })()")


def js_selected():
    return ("(() => { const rows = Array.from(document.querySelectorAll('tr.zA')); const cls = {}; rows.forEach(r => String(r.className).split(/\\s+/).forEach(c => { cls[c] = (cls[c] || 0) + 1; }));"
            " const checked = document.querySelectorAll('tr.zA [role=\"checkbox\"][aria-checked=\"true\"]').length; const chk = document.querySelectorAll('tr.zA [role=\"checkbox\"]').length;"
            " return JSON.stringify({classes: cls, checked, checkboxes: chk, hash_parts: location.hash.split('/').length}); })()")


def js_texts():
    g = json.dumps(GENERIC + ["Create new", "Create new label", "Please enter a new label name:", "New label", "Please enter a new label name"])
    return ("(() => { const G = " + g + "; const out = {}; for (const e of document.querySelectorAll('*')) { if (!e.offsetParent) continue; const t = String(e.textContent || '').trim();"
            " if (!G.includes(t)) continue; if (e.children.length > 1) continue; const k = t + '|' + e.tagName.toLowerCase() + '.' + String(e.className || '').split(/\\s+/).filter(Boolean).slice(0, 3).join('.') + '|role=' + (e.getAttribute('role') || '') + '|aria=' + (e.getAttribute('aria-label') || ''); out[k] = (out[k] || 0) + 1; }"
            " return JSON.stringify(out); })()")


def js_list():
    return ("(() => { const q = (s) => document.querySelectorAll(s).length; return JSON.stringify({host: location.hostname, hash: location.hash.slice(0, 12),"
            " thread_attr: q('[data-legacy-thread-id]'), last_attr: q('[data-legacy-last-non-draft-message-id]'), tr: q('tr'), zA: q('tr.zA'), zE: q('tr.zE'), main: q('[role=\"main\"]')}); })()")


def js_view():
    sels = ["[data-legacy-message-id]", "[data-message-id]", "[data-legacy-thread-id]", "[data-thread-perm-id]", "[role=listitem]", "[role=list]", "h2", "div.adn", "div.gs", "div.a3s", "div.ii", "tr.zA", "[data-legacy-last-non-draft-message-id]"]
    parts = ", ".join(f"{json.dumps(x)}: m ? m.querySelectorAll({json.dumps(x)}).length : -1" for x in sels)
    return ("(() => { const m = document.querySelector('[role=\"main\"]'); return JSON.stringify({hash_kind: location.hash.split('/')[0], hash_parts: location.hash.split('/').length, hash_tail_len: location.hash.split('/').pop().length, main: !!m, text_len: m ? String(m.innerText || '').length : -1, counts: {" + parts + "}}); })()")


def js_rowattr():
    return ("(() => { const o = {}; for (const e of document.querySelectorAll('[data-legacy-thread-id]')) { const k = e.tagName.toLowerCase() + '.' + String(e.className || '').split(/\\s+/).filter(Boolean).join('.') + '|vis=' + !!(e.offsetParent) + '|tr=' + !!e.closest('tr') + '|parent=' + e.parentElement.tagName.toLowerCase(); o[k] = (o[k] || 0) + 1; } return JSON.stringify(o); })()")


def js_print():
    sels = ["table", "table.message", "div.message", ".maincontent", "h2", "h3", "hr", "td", "[data-legacy-message-id]", "[data-message-id]", "a", "img", "div", "span"]
    parts = ", ".join(f"{json.dumps(x)}: document.querySelectorAll({json.dumps(x)}).length" for x in sels)
    return ("(() => { const b = document.body; const ids = Array.from(document.querySelectorAll('[id]')).map(e => e.id).filter(i => /^[a-z_]{1,12}$/.test(i)).slice(0, 10);"
            " return JSON.stringify({host: location.hostname, search_keys: Array.from(new URLSearchParams(location.search).keys()), title_len: document.title.length, text_len: String(b.innerText || '').length,"
            " counts: {" + parts + "}, ids, body_classes: String(b.className || '').split(/\\s+/).slice(0, 4)}); })()")


PROBES = {"texts": js_texts, "selected": js_selected, "rowindex": js_rowindex, "print": js_print, "rowattr": js_rowattr, "view": js_view, "words": js_words, "menu": js_menu, "chips": js_chips, "list": js_list}


def env_for(a):
    ns = SimpleNamespace(out=a.out, base=a.base, lock=a.lock, min_avail_mib=6144, max_load=float("inf"), fixture_port=0, keep_disk=True,
                         account_control_selector=None)
    return ga.attach(ns), ns


def identity_ok(env, ns, account, index):
    items, selector = ga.read_refs(ns)
    letter = "ABC"[index]
    r = ga.verify_identity(env, selector, "".join(sorted(items)), want=letter)
    return r["identity"] == letter, r


def cmd_shape(a):
    env, ns = env_for(a)
    ok, r = identity_ok(env, ns, None, a.index)
    # The account page must be the wanted one before any thread is opened.
    err, _ = env.call("step", steps=f"goto https://{HOST}/mail/u/{a.index}/#inbox", compact=True)
    ok, r = identity_ok(env, ns, None, a.index)
    if err or not ok:
        print(json.dumps({"refused": "identity", **r}))
        return 1
    if a.pick_read:
        # The ids of the first listed thread that is read already. Opening it marks nothing. The ids stay in a private file.
        env.call("step", steps='timeout 20000\nexpect js "document.querySelectorAll(\'[data-legacy-thread-id]\').length > 0"', compact=True)
        err, raw = env.call("eval", js=("(() => { for (const e of document.querySelectorAll('[data-legacy-thread-id]')) { const r = e.closest('tr');"
                                        " if (!r || /\\bzE\\b/.test(r.className || '')) continue; const m = r.querySelector('[data-legacy-last-non-draft-message-id]');"
                                        " if (m) return JSON.stringify({thread: e.getAttribute('data-legacy-thread-id'), message: m.getAttribute('data-legacy-last-non-draft-message-id')}); }"
                                        " return JSON.stringify({}); })()"))
        try:
            d = json.loads(json.loads(raw) if raw.startswith('"') else raw)
        except ValueError:
            d = {}
        if err or not re.fullmatch(r"[0-9a-f]{16}", str(d.get("thread"))) or not re.fullmatch(r"[0-9a-f]{16}", str(d.get("message"))):
            print(json.dumps({"refused": "no_read_thread_listed"}))
            return 1
        a.thread, a.message = d["thread"], d["message"]
        fd = os.open(a.pick_read, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
    err, _ = env.call("step", steps=f"goto https://{HOST}/mail/u/{a.index}/#all/{a.thread}", compact=True)
    env.call("step", steps=wait_complete(), compact=True)
    ok, r = identity_ok(env, ns, None, a.index)
    if err or not ok:
        print(json.dumps({"refused": "identity_after_open", **r}))
        return 1
    import time
    time.sleep(3)
    err, raw = env.call("eval", js=js_shape(a.thread, a.message))
    print(raw if not err else json.dumps({"error": "eval"}))
    return 0


def cmd_printshape(a):
    """Open the print view of a thread by its legacy ID and report booleans: does the page carry the message ID and the thread ID."""
    env, ns = env_for(a)
    ids = json.load(open(a.pick))
    ok, r = identity_ok(env, ns, None, a.index)
    if not ok:
        print(json.dumps({"refused": "identity", **r}))
        return 1
    env.call("step", steps=f"goto https://{HOST}/mail/u/{a.index}/?view=pt&search=all&th={ids['thread']}", compact=True)
    env.call("step", steps=wait_complete(), compact=True)
    js = ("(() => { const h = document.documentElement.innerHTML; const t = document.querySelectorAll('table.message'); return JSON.stringify({host: location.hostname,"
          " th_param: new URLSearchParams(location.search).get('th') === " + json.dumps(ids["thread"]) + ", html_has_message: h.includes(" + json.dumps(ids["message"]) + "),"
          " html_has_thread: h.includes(" + json.dumps(ids["thread"]) + "), messages: t.length, per_message_text_len: Array.from(t).slice(0, 6).map(e => String(e.innerText || '').length),"
          " per_message_has_id_attr: Array.from(t).slice(0, 6).map(e => Array.from(e.querySelectorAll('*')).concat([e]).some(x => Array.from(x.attributes).some(at => at.value.includes(" + json.dumps(ids["message"]) + ")))),"
          " attr_forms: Array.from(document.querySelectorAll('*')).flatMap(x => Array.from(x.attributes).filter(at => at.value.includes(" + json.dumps(ids["message"]) + ")).map(at => x.tagName.toLowerCase() + '[' + at.name + ']=' + at.value.split(" + json.dumps(ids["message"]) + ").join('<ID>').slice(0, 60))).slice(0, 6),"
          " title_kind: /^Gmail/.test(document.title)}); })()")
    err, raw = env.call("eval", js=js)
    print(raw if not err else json.dumps({"error": "eval"}))
    return 0


def cmd_probe(a):
    env, ns = env_for(a)
    ok, r = identity_ok(env, ns, None, a.index)
    if not ok and not a.skip_identity:
        print(json.dumps({"refused": "identity", **r}))
        return 1
    err, raw = env.call("eval", js=PROBES[a.name]())
    print(raw if not err else json.dumps({"error": "eval"}))
    return 0


def cmd_step(a):
    if not safe_step(a.text):
        print(json.dumps({"refused": "step_text"}))
        return 1
    env, ns = env_for(a)
    ok, r = identity_ok(env, ns, None, a.index)
    if not ok:
        print(json.dumps({"refused": "identity", **r}))
        return 1
    err, text = env.call("step", steps=a.text, compact=True)
    ok2, _ = identity_ok(env, ns, None, a.index)
    print(json.dumps({"receipt": text[:60], "error": err, "identity_after": ok2}))
    return 0 if not err else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--lock", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shape")
    s.add_argument("--index", type=int, required=True, choices=range(3))
    s.add_argument("--thread", default="0" * 16)
    s.add_argument("--message", default="0" * 16)
    s.add_argument("--pick-read", help="ignore --thread/--message: take the first read listed thread and write its ids to this private file")
    s.set_defaults(fn=cmd_shape)
    p = sub.add_parser("probe")
    p.add_argument("--index", type=int, required=True, choices=range(3))
    p.add_argument("--skip-identity", action="store_true", help="for a page that has no account control, such as a print view. The program returns counts only")
    p.add_argument("name", choices=sorted(PROBES))
    p.set_defaults(fn=cmd_probe)
    q = sub.add_parser("printshape")
    q.add_argument("--index", type=int, required=True, choices=range(3))
    q.add_argument("--pick", required=True, help="private file with the thread and message IDs")
    q.set_defaults(fn=cmd_printshape)
    t = sub.add_parser("step")
    t.add_argument("--index", type=int, required=True, choices=range(3))
    t.add_argument("text")
    t.set_defaults(fn=cmd_step)
    a = ap.parse_args(argv)
    if not (getattr(a, "pick_read", None) or all(re.fullmatch(r"[0-9a-f]{16}", getattr(a, k, "0" * 16)) for k in ("thread", "message"))):
        sys.exit("STOPPED: ids are 16 hex characters")
    try:
        return a.fn(a)
    except ga.TrialError as x:
        print(f"STOPPED: {x}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
