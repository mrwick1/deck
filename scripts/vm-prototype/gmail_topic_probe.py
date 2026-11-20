#!/usr/bin/env python3
"""Read-only probe of the Gmail "create label" dialog, for the dynamic topic steps (D106).

It never creates a label. It selects one listed thread, opens its label menu, opens the "New label" dialog, reports the
fixed facts of the dialog, optionally types a synthetic name to see that the field takes it and the Create button
enables, then cancels, reloads the list and proves that the row kept every chip it had.

Deck must not own the browser while this tool runs. The tool pauses Deck at a settled boundary and releases only its own pause.
Output is booleans, counts and the interface words of Gmail. It prints no mail text, address or owner label name.

  select  write a private pick file for the latest triaged thread of one account (store read only)
  dialog  open the dialog, report its facts, cancel, and prove that nothing changed
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gmail_label_repair as glr  # noqa: E402
import gmail_live_acceptance as gla  # noqa: E402

BOT = glr.BOT
TARGET = re.compile(r'(role=textbox|role=textbox name="[A-Za-z0-9 :.\-]{1,60}"|css="[A-Za-z0-9 \[\]=_.#>:\-]{1,80}")')
PROBE_TEXT = "Deck/Zzprobe"

# Fixed program. No value is interpolated. It reports the visible dialog: its title, fields and buttons.
JS_DIALOG = (
    "(() => { const vis = e => e.offsetParent !== null; const q = s => Array.from(document.querySelectorAll(s)).filter(vis);"
    " const nm = e => String(e.getAttribute('aria-label') || e.getAttribute('title') || e.textContent || '').trim().slice(0, 60);"
    " const dialogs = q('[role=dialog],[role=alertdialog]'); const d = dialogs[dialogs.length - 1];"
    " if (!d) return JSON.stringify({dialogs: 0});"
    " const inputs = Array.from(d.querySelectorAll('input[type=text],input:not([type]),[role=textbox]')).filter(vis);"
    " const buttons = Array.from(d.querySelectorAll('button,[role=button]')).filter(vis);"
    " const h = d.querySelector('[role=heading],h1,h2');"
    " return JSON.stringify({dialogs: dialogs.length, title: h ? nm(h) : null,"
    " inputs: inputs.map(e => ({tag: e.tagName.toLowerCase(), role: e.getAttribute('role'), name: nm(e), value_len: String(e.value || '').length, value_ok: String(e.value || '') === " + json.dumps(PROBE_TEXT) + "})),"
    " buttons: buttons.map(e => ({name: nm(e), disabled: e.disabled === true || e.getAttribute('aria-disabled') === 'true'}))}); })()"
)


def facts_ok(d):
    """The dialog has one text field and a Create and a Cancel button."""
    if not d or d.get("dialogs", 0) < 1 or len(d.get("inputs", [])) < 1:
        return False
    names = [b.get("name") for b in d.get("buttons", [])]
    return "Create" in names and "Cancel" in names


def create_enabled(d):
    return any(b.get("name") == "Create" and not b.get("disabled") for b in (d or {}).get("buttons", []))


def pick_latest(db, account):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        return c.execute("SELECT account, thread_id, message_id FROM gmail_triage WHERE bot_id=? AND account=? AND state='triaged'"
                         " ORDER BY rowid DESC LIMIT 1", (BOT, account)).fetchone()
    finally:
        c.close()


def probe(page, sel, textbox=None):
    """Returns (report, reason). Every exit reloads the list so no dialog stays open."""
    d, _, why = glr.locate(page, sel)
    if not d:
        return None, why
    before = list(d["chips"])
    report = {}
    m, why = glr.open_menu(page, sel, d)
    if not m:
        return None, why
    try:
        report["menu_opened"] = True
        report["create_item"] = page.step('timeout 15000\nclick role=menuitem name="Create new"')
        if not report["create_item"]:
            return report, "no_create_item"
        facts = page.eval(JS_DIALOG)
        report["dialog"] = facts
        report["dialog_ok"] = facts_ok(facts)
        if textbox and report["dialog_ok"]:
            # The step text holds only the checked name of the field and the fixed synthetic text.
            report["typed"] = page.step(f'timeout 15000\ntype {textbox} "{PROBE_TEXT}"')
            typed = page.eval(JS_DIALOG)
            report["typed_value_ok"] = any(i.get("value_ok") for i in (typed or {}).get("inputs", []))
            report["create_enabled_after_typing"] = create_enabled(typed)
        report["cancelled"] = page.step('timeout 15000\nclick role=button name="Cancel"')
        return report, None
    finally:
        # A reload drops any open dialog or menu. Nothing was created: the Create button was never clicked.
        page.step(f"goto {glr.list_url(sel['index'], 'inbox')}\nreload")
        after, _, _ = glr.locate(page, sel)
        report["chips_unchanged"] = bool(after) and after["chips"] == before
        report["no_probe_chip"] = bool(after) and PROBE_TEXT not in after["chips"]


def cmd_select(a):
    row = pick_latest(a.db, a.account)
    if not row:
        print(json.dumps({"refused": "no_row"}))
        return 1
    glr.write_private(a.pick, {"account": row[0], "thread": row[1], "message": row[2]})
    print(json.dumps({"picked": True}))
    return 0


def cmd_dialog(a):
    if a.textbox and not TARGET.fullmatch(a.textbox):
        sys.exit("STOPPED: the field target is not accepted")
    page, sel = glr.Live(a), glr.load_sel(a)
    cfg = json.loads(Path(a.service_config).read_text())
    try:
        with glr.Owned(cfg, a.db):
            report, why = probe(page, sel, a.textbox)
    except RuntimeError as x:
        print(json.dumps({"refused": str(x)}))
        return 1
    print(json.dumps({"report": report, "reason": why}))
    return 0 if why is None else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--out", "--base"):
        ap.add_argument(flag)
    ap.add_argument("--lock", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--pick", required=True)
    s.add_argument("--db", required=True)
    s.add_argument("--account", default="account_a")
    s.set_defaults(fn=cmd_select)
    d = sub.add_parser("dialog")
    d.add_argument("--pick", required=True)
    d.add_argument("--db", required=True)
    d.add_argument("--service-config", required=True)
    d.add_argument("--textbox", default=None, help="locator of the field (role=textbox name=\"..\" or css=\"..\"), to type a synthetic name (never created)")
    d.set_defaults(fn=cmd_dialog)
    a = ap.parse_args(argv)
    if a.cmd == "dialog" and not (a.out and a.base):
        sys.exit("STOPPED: --out and --base are required")
    try:
        return a.fn(a)
    except gla.ga.TrialError as x:
        print(f"STOPPED: {x}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
