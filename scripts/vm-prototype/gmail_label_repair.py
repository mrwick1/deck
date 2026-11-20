#!/usr/bin/env python3
"""Exact repair of one wrong Deck category label, with an audit journal and a store update.

Deck labels are additive. This tool is the only removal path, and it is narrow on purpose:

  * one account, one thread and one latest message, read from a private pick file;
  * the only labels it may write are the two fixed names `Deck/FYI` (add) and `Deck/Action` (remove);
  * the message is read through the print view first and must show a done payment and no ask (the D102 word lists);
  * every write is guarded by a read of the real checkbox state and is never repeated blindly;
  * a reload must show the wanted label, no `Deck/Action`, and every other chip that the row showed before;
  * the stored category changes only after that proof, in one transaction.

Deck must not own the browser while this tool writes. The caller pauses it and releases only its own pause.
Nothing here prints mail text, an address or a label name of the owner. Output is booleans, counts and fixed words.

  select  find the one stored candidate and write its ids to a private file
  check   read the thread and report the evidence as booleans
  menu    open the label menu of the row and report the checkbox states (no click)
  repair  check, write, verify after a reload, then update the store
  store   finish the store update when Gmail is proven repaired already
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gmail_live_acceptance as gla  # noqa: E402

HOST = gla.HOST
ADD, DROP = "Deck/FYI", "Deck/Action"
LISTS = ("inbox", "category/promotions", "category/updates", "category/social", "category/forums")
CHIPS = "div.au div.av"
ID = re.compile(r"[0-9a-f]{16}")
MAX_TEXT = 20000
ATTEMPTS = 2
BOT = "shared-profile"

# D102 word lists. A test compares them with the Rust constants, so the two rules cannot drift.
ASK_WORDS = ["verify", "confirm", "update", "submit", "provide", "click", "tap", "retry", "kyc", "outstanding", "suspicious", "insufficient", "overdraft", "kindly"]
DONE_WORDS = ["debited", "credited", "successful", "successfully", "paid", "completed", "transferred"]
DONE_PHRASES = ["is complete", "payment received"]
BOILERPLATE = ["if you did not", "if you didn't", "if this was not you", "if this wasn't you", "if you do not recognize", "if you don't recognize",
               "if you do not recognise", "if you don't recognise", "no action", "do not share"]
OWNER_ACTION = ["due", "overdue", "failed", "declined", "unsuccessful", "pending", "dispute", "action required", "expire", "pay now", "reminder",
                "not processed", "blocked", "locked", "unable to", "reversed", "chargeback", "not successful", "not completed", "not debited",
                "not credited", "not paid", "unpaid", "could not", "couldn't", "will be debited", "will be charged", "will be deducted", "try again", "unauthori"]


# ---------- pure helpers (unit tested) ----------

def _words(text):
    return re.split(r"[^\w-]+", text.lower())


def has_word(text, words):
    return any(t in words for t in _words(text))


def has_any(text, words):
    low = text.lower()
    return any(w in low for w in words)


def without_boilerplate(text):
    """The text without the boilerplate sentences of a bank notice. A sentence ends at a newline or at . ! ? before a space."""
    low, out, sent = text.lower(), [], ""
    for i, c in enumerate(low):
        sent += c
        nxt = low[i + 1] if i + 1 < len(low) else None
        if c == "\n" or (c in ".!?" and (nxt is None or nxt.isspace())) or nxt is None:
            if not has_any(sent, BOILERPLATE):
                out.append(sent)
            sent = ""
    return "".join(out)


def ask_hits(body):
    """Which fixed-list ask entries the text holds. Only entries of the lists leave this function, never mail text."""
    return sorted({w for w in OWNER_ACTION if w in body.lower()} | {w for w in ASK_WORDS if has_word(body, [w])})


def thread_verdict(per_message):
    """Conservative whole-thread verdict. Latest wins is not proof: an older ask may still be open.
    The thread passes only when the last message is a done payment (uncut, no ask after the boilerplate cut)
    and no earlier message is cut, empty or holds an ask. An older ask is never judged by its position in the text."""
    if not per_message or not per_message[-1]["done_payment"]:
        return False
    return all(not p["ask"] and not p["cut"] and not p["empty"] for p in per_message[:-1])


def evidence(text, cut):
    """Booleans about the text of one thread. `cut` is true when the page text was longer than the read."""
    body = without_boilerplate(text)
    done = has_word(body, DONE_WORDS) or has_any(body, DONE_PHRASES)
    ask = has_any(body, OWNER_ACTION) or has_word(body, ASK_WORDS)
    return {"empty": not text.strip(), "ask_hits": ask_hits(body), "upi": bool(re.search(r"\bupi\b", text, re.I)), "amount_500": bool(re.search(r"(?:₹|rs\.?|inr)\s*500(?:\.00?)?(?!\d)", text, re.I)),
            "done": done, "ask": ask, "cut": bool(cut), "done_payment": bool(done and not ask and not cut)}


def parse_json(raw):
    d = raw
    for _ in range(2):
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except ValueError:
                return None
    return d if isinstance(d, dict) else None


def list_url(index, name):
    return f"https://{HOST}/mail/u/{index}/#{name}"


def print_url(index, thread):
    return f"https://{HOST}/mail/u/{index}/?view=pt&search=all&th={thread}"


def js_row(thread, message):
    return ("(() => { const rows = Array.from(document.querySelectorAll('tr.zA')).filter(r => Array.from(r.querySelectorAll('[data-legacy-thread-id]')).some(e => e.getAttribute('data-legacy-thread-id') === " + json.dumps(thread) + "));"
            " const r = rows[0]; return JSON.stringify({host: location.hostname, proto: location.protocol, ready: document.readyState, rows: rows.length,"
            " pos: r ? Array.from(r.parentElement.children).indexOf(r) + 1 : 0,"
            " message_ok: !!r && Array.from(r.querySelectorAll('[data-legacy-last-non-draft-message-id]')).some(e => e.getAttribute('data-legacy-last-non-draft-message-id') === " + json.dumps(message) + "),"
            " selected: document.querySelectorAll('tr.zA.x7').length,"
            " chips: r ? Array.from(r.querySelectorAll(" + json.dumps(CHIPS) + ")).map(e => String(e.textContent || '').trim().slice(0, 100)).slice(0, 60) : []}); })()")


def js_read(thread, message):
    return ("(() => { const t = Array.from(document.querySelectorAll('table.message')); const q = new URLSearchParams(location.search);"
            " const full = t.map(e => String(e.innerText || '')).join('\\n\\n'); const text = full.slice(0, " + str(MAX_TEXT) + ");"
            " const h = document.documentElement.innerHTML;"
            " return JSON.stringify({host: location.hostname, proto: location.protocol, ready: document.readyState, path: location.pathname,"
            " view: q.get('view'), th: q.get('th'), messages: t.length, parts: t.map(e => String(e.innerText || '').slice(0, " + str(MAX_TEXT) + ")), len: full.length, sent: text.length,"
            " has_message: h.includes(" + json.dumps(message) + "), has_thread: h.includes(" + json.dumps(thread) + "), text: text}); })()")


def row_ok(d, want_message=True):
    """A parsed list read: the row exists once, shows the expected latest message and carries a chip list."""
    if not d or d.get("host") != HOST or d.get("proto") != "https:" or d.get("ready") != "complete":
        return "unloaded"
    if d.get("rows") == 0:
        return "not_listed"
    if d.get("rows") != 1 or not isinstance(d.get("chips"), list) or not all(isinstance(c, str) for c in d["chips"]):
        return "row_ambiguous"
    if want_message and d.get("message_ok") is not True:
        return "stale_revision"
    return None


def menu_state(d, name):
    """('true'|'false'|None, count) of the one checkbox that carries this Deck name in the open menu."""
    items = [i for i in (d or {}).get("deck_items", []) if i.get("name") == name and i.get("role") == "menuitemcheckbox"]
    state = items[0].get("checked") if len(items) == 1 else None
    return (state if state in ("true", "false") else None), len(items)


def verified(chips, base, op):
    """Whether the chips after a reload prove one write. `base` is the chip list that the row showed before any write."""
    keep = [c for c in base if c != DROP]
    if not all(c in chips for c in keep):
        return "lost"
    if op == "add_fyi":
        return None if ADD in chips and DROP in chips else "not_visible"
    return None if DROP not in chips and ADD in chips else "not_visible"


class Journal:
    """Append-only JSON lines in a private file. Every line is flushed to disk before the next action."""

    def __init__(self, path, clock=lambda: int(time.time() * 1000)):
        self.path, self.clock = Path(path), clock
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)

    def add(self, key, event, **kw):
        line = json.dumps({"ms": self.clock(), "key": key, "event": event, **kw}, sort_keys=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def rows(self, key=None):
        out = []
        for ln in self.path.read_text().splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if key is None or r.get("key") == key:
                out.append(r)
        return out

    def open_writes(self, key):
        """Write entries that never closed: a crash happened between the click and the verdict."""
        closed = {r["id"] for r in self.rows(key) if r["event"] == "write_close"}
        return [r for r in self.rows(key) if r["event"] == "write_open" and r["id"] not in closed]


def sel_key(sel):
    return f"{sel['account']}/{sel['thread']}/{sel['message']}"


# ---------- the store ----------

def candidates(db, account):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        return c.execute("SELECT bot_id, account, thread_id, message_id FROM gmail_triage WHERE bot_id=? AND account=? AND state='triaged'"
                         " AND category='actionable' AND label_state='applied' AND summary LIKE '%UPI%' AND summary LIKE '%500%'", (BOT, account)).fetchall()
    finally:
        c.close()


def settled(db):
    """No journal entry of Deck is open. A pause is settled only then."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        return c.execute("SELECT COUNT(*) FROM gmail_org_journal WHERE outcome IS NULL").fetchone()[0] == 0
    finally:
        c.close()


def store_update(db, sel):
    """One transaction: the stored category follows the label that Gmail shows. Safe to repeat: a row that is `fyi` already counts as done."""
    c = sqlite3.connect(db, timeout=60)
    try:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute("UPDATE gmail_triage SET category='fyi' WHERE bot_id=? AND account=? AND thread_id=? AND message_id=? AND state='triaged'"
                        " AND category='actionable' AND label_state='applied'", (BOT, sel["account"], sel["thread"], sel["message"]))
        if cur.rowcount == 1:
            c.execute("COMMIT")
            return "updated"
        row = c.execute("SELECT category, label_state, state FROM gmail_triage WHERE bot_id=? AND account=? AND thread_id=? AND message_id=?",
                        (BOT, sel["account"], sel["thread"], sel["message"])).fetchone()
        c.execute("ROLLBACK")
        return "already" if row == ("fyi", "applied", "triaged") else "refused"
    finally:
        c.close()


# ---------- the live boundary ----------

class Live:
    """The bare browser of the retained guest, through the acceptance helper. Output of a page read stays in this process."""

    def __init__(self, a):
        self.env, self.ns = gla.env_for(a)

    def step(self, text):
        err, _ = self.env.call("step", steps=text, compact=True)
        return not err

    def eval(self, js):
        err, raw = self.env.call("eval", js=js)
        return None if err else parse_json(raw)

    def identity(self, index):
        for i in range(3):
            if i:
                time.sleep(3)
            if gla.identity_ok(self.env, self.ns, None, index)[0]:
                return True
        return False


OPEN_MENU = ('timeout 75000\nwait js "performance.now() > 60000"\nclick role=button name="Labels"',
             'timeout 75000\nwait js "performance.now() > 60000"\nclick role=button name="More email options"\nclick text="Label as"')


def click_item(name):
    return f'timeout 15000\nclick role=menuitemcheckbox name="{name}"'


def select_step(pos, thread):
    check = (f"expect js \"(() => {{ const s = document.querySelectorAll('tr.zA.x7'); return s.length === 1 && !!s[0].querySelector('[data-legacy-thread-id=\\\"{thread}\\\"]'); }})()\"")
    return f'timeout 15000\nclick css="tr.zA:nth-child({pos}) [role=checkbox]"\n{check}'


def read_row(page, sel, want_message=True):
    d = page.eval(js_row(sel["thread"], sel["message"]))
    why = row_ok(d, want_message)
    return (d, None) if why is None else (None, why)


def locate(page, sel):
    """Open each list of the account until one shows the row. Identity is proven after every open."""
    why = "not_listed"
    for name in LISTS:
        if not page.step(f"goto {list_url(sel['index'], name)}\nreload") or not page.identity(sel["index"]):
            return None, None, "identity_refused"
        d, why = read_row(page, sel)
        if d:
            return d, name, None
        if why != "not_listed":
            return None, None, why
    return None, None, why


def read_message(page, sel):
    """The print view of the thread, bound to the stored thread. It carries no message id: the list row binds the message, before and after this read."""
    if not page.step(f"goto {list_url(sel['index'], 'inbox')}") or not page.identity(sel["index"]):
        return None, "identity_refused"
    if not page.step(f"goto {print_url(sel['index'], sel['thread'])}"):
        return None, "navigation_failed"
    d = None
    for _ in range(3):
        d = page.eval(js_read(sel["thread"], sel["message"]))
        if d and d.get("ready") == "complete":
            break
        time.sleep(2)
    if (not d or d.get("host") != HOST or d.get("proto") != "https:" or d.get("path") != f"/mail/u/{sel['index']}/" or d.get("view") != "pt"
            or d.get("th") != sel["thread"] or not isinstance(d.get("messages"), int) or d["messages"] < 1 or not isinstance(d.get("text"), str)):
        return None, "thread_unverified"
    ev = evidence(d["text"], d.get("len", 0) > MAX_TEXT or d.get("len", 0) > d.get("sent", 0))
    ev["messages"] = d["messages"]
    if isinstance(d.get("parts"), list):
        parts = [evidence(t, ev["cut"]) for t in d["parts"] if isinstance(t, str)]
        ev["per_message"] = [{k: p[k] for k in ("done", "ask", "cut", "empty", "ask_hits")} for p in parts]
        ev["thread_done_payment"] = thread_verdict(parts) and len(parts) == d["messages"]
    if not page.step(f"goto {list_url(sel['index'], 'inbox')}") or not page.identity(sel["index"]):
        return None, "identity_changed"
    return ev, None


def open_menu(page, sel, row):
    """Select only this row and open its label menu. Neither step writes a label. Returns the probe or a reason."""
    if row["selected"] != 0:
        return None, "selection_dirty"
    if not page.step(select_step(row["pos"], sel["thread"])):
        return None, "select_failed"
    for alt in OPEN_MENU:
        if not page.step(alt):
            continue
        m = page.eval(gla.js_menu())
        if m and m.get("menus", 0) >= 1:
            return m, None
    return None, "menu_not_open"


def write_label(page, jr, sel, base, op):
    """One guarded write. The checkbox state is read before and after the click. Returns None on proof or a reason."""
    key, name = sel_key(sel), (ADD if op == "add_fyi" else DROP)
    if len([r for r in jr.rows(key) if r["event"] == "write_open" and r["op"] == op]) >= ATTEMPTS:
        return "attempt_cap"
    row, why = None, None
    d, name_list, why = locate(page, sel)
    if why:
        return why
    chips = d["chips"]
    keep = [c for c in base if c != DROP]
    if not all(c in chips for c in keep):
        return "lost"
    if (name in chips) == (op == "add_fyi"):
        return "state_changed"  # the write is done already or the row is not as planned: read again, decide again
    if not page.identity(sel["index"]):
        return "identity_before_write"
    m, why = open_menu(page, sel, d)
    if why:
        return why
    state, n = menu_state(m, name)
    want = "false" if op == "add_fyi" else "true"
    if n != 1 or state != want:
        return "menu_state_refused"
    if not page.identity(sel["index"]):
        return "identity_before_write"
    wid = len(jr.rows(key)) + 1
    jr.add(key, "write_open", id=wid, op=op, attempt=1 + len([r for r in jr.rows(key) if r["event"] == "write_open" and r["op"] == op]), before=len(chips))
    page.step(click_item(name))  # a lost receipt changes nothing: the reload below decides
    after = page.eval(gla.js_menu())
    flipped = menu_state(after, name)[0] == ("true" if op == "add_fyi" else "false")
    outcome = None
    if page.identity(sel["index"]):
        outcome = "ok"
    if outcome != "ok":
        jr.add(key, "write_close", id=wid, outcome="uncertain", reason="identity_after_write", flipped=flipped)
        return "identity_after_write"
    if not page.step(f"goto {list_url(sel['index'], name_list)}\nreload") or not page.identity(sel["index"]):
        jr.add(key, "write_close", id=wid, outcome="uncertain", reason="reload", flipped=flipped)
        return "reload_after_write"
    d, why = read_row(page, sel)
    if why:
        jr.add(key, "write_close", id=wid, outcome="uncertain", reason=why, flipped=flipped)
        return why
    bad = verified(d["chips"], base, op)
    jr.add(key, "write_close", id=wid, outcome="applied" if bad is None else bad, reason="readback", flipped=flipped, chips=len(d["chips"]))
    return bad


def repair(page, jr, db, sel, apply=True):
    """Check, repair, verify, update the store. Returns (verdict, detail). Never prints text of the mail."""
    key = sel_key(sel)
    for r in jr.open_writes(key):
        jr.add(key, "write_close", id=r["id"], outcome="unknown", reason="restart")
    ev, why = read_message(page, sel)
    if why:
        return "refused", why
    if not ev.get("thread_done_payment", ev["done_payment"]):
        jr.add(key, "refused", reason="evidence", ev={k: ev[k] for k in ("done", "ask", "cut", "messages")})
        return "refused", "evidence"
    d, _, why = locate(page, sel)
    if why:
        return "refused", why
    plans = [r for r in jr.rows(key) if r["event"] == "plan"]
    base = plans[0]["before"] if plans else d["chips"]
    if not plans:
        jr.add(key, "plan", before=base, evidence=True)
    has_drop, has_add = DROP in d["chips"], ADD in d["chips"]
    if not all(c in d["chips"] for c in base if c != DROP):
        jr.add(key, "refused", reason="lost")
        return "lost", "unrelated_label_missing"
    if not has_drop and not has_add:
        return "refused", "no_action_label"
    if not apply:
        return "ready", {"add_needed": not has_add, "drop_needed": has_drop}
    ops = (["add_fyi"] if not has_add else []) + (["remove_action"] if has_drop else [])
    for op in ops:
        why = write_label(page, jr, sel, base, op)
        if why:
            jr.add(key, "stopped", op=op, reason=why)
            return ("lost" if why == "lost" else "uncertain" if why not in ("menu_state_refused", "state_changed", "attempt_cap") else "refused"), why
    d, _, why = locate(page, sel)
    if why or verified(d["chips"], base, "remove_action"):
        jr.add(key, "stopped", op="final", reason=why or "final_check")
        return "uncertain", why or "final_check"
    jr.add(key, "verified", chips=len(d["chips"]))
    res = store_update(db, sel)
    jr.add(key, "db_updated", result=res)
    return ("repaired" if res in ("updated", "already") else "refused"), res


# ---------- ownership of the browser ----------

def http(cfg, method, path, body=None):
    r = urllib.request.Request(f"http://{cfg['bind']}:{cfg['port']}{path}", data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={"Remote-User": "e2e", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=15) as res:
            return res.status, res.read()
    except OSError:
        return 0, b""


class Owned:
    """Pause Deck at a settled boundary, keep the browser, and release only a pause that this object made."""

    def __init__(self, cfg, db, call=http, sleep=time.sleep, wait_s=300):
        self.cfg, self.db, self.call, self.sleep, self.wait_s, self.mine = cfg, db, call, sleep, wait_s, False

    def status(self):
        code, raw = self.call(self.cfg, "GET", "/api/bot")
        return json.loads(raw) if code == 200 else None

    def __enter__(self):
        s = self.status()
        if s is None:
            raise RuntimeError("deck_unreachable")
        if not s.get("automation_paused"):
            if self.call(self.cfg, "POST", "/api/bot/automation", {"paused": True})[0] != 204:
                raise RuntimeError("pause_failed")
            self.mine = True
        end = time.monotonic() + self.wait_s
        while time.monotonic() < end:
            s = self.status() or {}
            if s.get("automation_paused") and not s.get("gmail", {}).get("running") and settled(self.db):
                return self
            self.sleep(2)
        self.__exit__(None, None, None)
        raise RuntimeError("not_settled")

    def __exit__(self, *_):
        if self.mine:
            self.call(self.cfg, "POST", "/api/bot/automation", {"paused": False})
            self.mine = False


# ---------- commands ----------

def write_private(path, obj):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)


def load_sel(a):
    d = json.loads(Path(a.pick).read_text())
    if not all(ID.fullmatch(str(d.get(k, ""))) for k in ("thread", "message")) or d.get("account") not in ("account_a", "account_b", "account_c"):
        sys.exit("STOPPED: the pick file is not valid")
    return {**d, "index": "abc".index(d["account"][-1])}


def cmd_select(a):
    rows = candidates(a.db, a.account)
    if len(rows) != 1:
        print(json.dumps({"candidates": len(rows), "refused": "not_unique"}))
        return 1
    write_private(a.pick, {"account": rows[0][1], "thread": rows[0][2], "message": rows[0][3]})
    print(json.dumps({"candidates": 1}))
    return 0


def cmd_check(a):
    page, sel = Live(a), load_sel(a)
    ev, why = read_message(page, sel)
    print(json.dumps(ev if ev else {"refused": why}))
    return 0 if ev else 1


def cmd_menu(a):
    page, sel = Live(a), load_sel(a)
    d, _, why = locate(page, sel)
    if why:
        print(json.dumps({"refused": why}))
        return 1
    m, why = open_menu(page, sel, d)
    page.step(f"goto {list_url(sel['index'], 'inbox')}\nreload")
    print(json.dumps(m if m else {"refused": why}))
    return 0 if m else 1


def cmd_repair(a):
    page, sel, jr = Live(a), load_sel(a), Journal(a.journal)
    cfg = json.loads(Path(a.service_config).read_text())
    try:
        with Owned(cfg, a.db):
            verdict, detail = repair(page, jr, a.db, sel, apply=not a.dry_run)
    except RuntimeError as x:
        print(json.dumps({"verdict": "refused", "detail": str(x)}))
        return 1
    print(json.dumps({"verdict": verdict, "detail": detail}))
    return 0 if verdict in ("repaired", "ready") else 1


def cmd_store(a):
    sel, jr = load_sel(a), Journal(a.journal)
    key = sel_key(sel)
    if not any(r["event"] == "verified" for r in jr.rows(key)):
        print(json.dumps({"refused": "no_verified_entry"}))
        return 1
    res = store_update(a.db, sel)
    jr.add(key, "db_updated", result=res)
    print(json.dumps({"result": res}))
    return 0 if res in ("updated", "already") else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out"), ap.add_argument("--base"), ap.add_argument("--lock", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("select", cmd_select), ("check", cmd_check), ("menu", cmd_menu), ("repair", cmd_repair), ("store", cmd_store)):
        p = sub.add_parser(name)
        p.add_argument("--pick", required=True)
        if name in ("select", "repair", "store"):
            p.add_argument("--db", required=True)
        if name == "select":
            p.add_argument("--account", default="account_a")
        if name in ("repair", "store"):
            p.add_argument("--journal", required=True)
        if name == "repair":
            p.add_argument("--service-config", required=True)
            p.add_argument("--dry-run", action="store_true")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    if a.cmd != "select" and a.cmd != "store" and not (a.out and a.base):
        sys.exit("STOPPED: --out and --base are required")
    try:
        return a.fn(a)
    except gla.ga.TrialError as x:
        print(f"STOPPED: {x}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
