#!/usr/bin/env python3
"""Bounded live acceptance of the ACTUAL Deck Gmail worker: server Bot loop -> Hub/GuestHost journal -> guest bare-call -> bare.

It is not a prototype scan. The real Deck binary runs the production scanner and parser against the retained
shared-profile VM of `gmail_accounts_e2e.py`. Only the model is a loopback stub. It counts requests and never
reads mail, so no model call can touch an account. The helper scripts read structure and opaque IDs only.
Nothing here opens a message, sends, labels, archives, marks read, changes a setting or signs in.

Order (every step needs the one before it; the first material failure stops the run):
  1 preflight   offline: private paths, binding, base, candidate digest, loopback ports, selectors. Starts nothing.
  2 resume      `gmail_accounts_e2e.py resume` starts the RETAINED overlay with the pinned candidate (own lock, memory guard).
  3 selectors   `switch` to each account in the one shared session, then one narrow `recent` probe for the search box.
  4 handover    the helpers have exited. They hold no lock and send no command. Deck then owns the browser.
  5 observe     Deck polls every interval. Gates come from production status and a read-only copy of the host database.
  6 pause       automation pause while idle: a due tick must start nothing. The server restarts while paused: the hold stays.
  7 resume      the hold is released: one poll runs, the window continues from the stored watermark, no new baseline.
  8 cleanup     stop Deck and the stub, settle, then `down --keep-disk`. Disk, host database and saved profile stay.

A gate that did not run is a failure. The manifest holds counts, times and fixed words. Private IDs, addresses, page
text and cookies never enter the output. Raw files stay under --out (mode 0700), outside Git.
"""
import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import gmail_accounts_e2e as g
import setup_e2e as e
import shared_profile_e2e as sp
import vmproto as v

HERE = Path(__file__).resolve().parent
PASS, FAIL = e.PASS, e.FAIL
BOT = sp.BOT
ACCOUNTS = (("account_a", 0, "GMAIL_A_USER", "A"), ("account_b", 1, "GMAIL_B_USER", "B"), ("account_c", 2, "GMAIL_C_USER", "C"))
INTERVAL, INITIAL_HOURS, OVERLAP, MAX_PAGES, MAX_SCAN = 120, 24, 3600, 3, 90
CADENCE_MIN, CADENCE_MAX = 119.0, 150.0  # an observed start-to-start gap of one scheduled interval, plus scheduling latency
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
GATES = ("preflight", "helper_resume", "selectors_three_accounts", "search_box_confirmed", "server_connected_gmail_configured",
         "baseline_three_healthy", "baseline_silent_zero_model", "journal_owned_by_gmail_poll", "cycles_observed", "later_windows_continue",
         "watermark_persisted", "cadence_scheduled_interval", "no_duplicate_events", "single_serial_owner", "pause_blocks_due_tick",
         "restart_keeps_pause_and_state", "resume_runs_poll_without_reseed", "journal_clean", "executor_identity_kept", "cleanup_clean")


class Stop(Exception):
    """A stable failure with no private text."""


# ---------- pure helpers (unit tested) ----------

def render_config(c):
    """The private deck.toml. Values come from `c`. The model key is never written: the environment holds it."""
    accts = "".join(f'[[gmail.accounts]]\nkey = "{k}"\nindex = {i}\nuser_secret = "{s}"\n' for k, i, s, _ in ACCOUNTS)
    return f'''listen = "127.0.0.1:{c["server_port"]}"
data_dir = "{c["data_dir"]}"
trusted_proxies = ["127.0.0.1"]
[model]
base_url = "http://127.0.0.1:{c["model_port"]}/v1"
api_key_env = "DECK_MODEL_KEY"
agent_model = "scripted"
small_model = "scripted"
[bare]
command = "unused"
[agent]
max_steps = 4
[vm]
version = 1
bot_id = "{BOT}"
runtime_dir = "{c["runtime_dir"]}"
harness = "{HERE / "vmproto.py"}"
fixture_port = {c["fixture_port"]}
internet = true
idle_secs = {c["idle_secs"]}
command_secs = 120
lifecycle_secs = 240
[gmail]
profile = "{g.PROFILE}"
account_control_selector = {json.dumps(c["account_selector"])}
search_box_selector = {json.dumps(c["search_box"])}
interval_secs = {INTERVAL}
initial_hours = {INITIAL_HOURS}
overlap_secs = {OVERLAP}
max_pages = {MAX_PAGES}
max_scan_secs = {MAX_SCAN}
{accts}'''


def preflight(f):
    """Fixed codes of every reason not to launch. `f` holds facts read from disk and the host. Empty list: go."""
    bad = []
    chk = lambda code, ok: ok or bad.append(code)  # noqa: E731
    chk("trial_bound_to_other_bot", f.get("bound_bot") == BOT)
    chk("runtime_not_the_trial_overlay", f.get("runtime_dir") == f.get("trial_runtime"))
    chk("vm_already_running", f.get("running_vms") == [])
    chk("base_not_frozen_or_changed", f.get("base_ok") is True and f.get("base_mode") == "0o444")
    chk("candidate_digest_differs", f.get("pinned_sha") == f.get("expect_sha") == f.get("recorded_sha"))
    chk("disk_missing", f.get("disk_ok") is True)
    chk("out_not_new_private", f.get("out_new") is True and f.get("out_private") is True)
    chk("host_db_not_fresh_or_private", f.get("db_dir_ok") is True)
    chk("host_db_bound_to_other_guest", f.get("db_bound") is True)
    chk("lock_is_not_owned_separate", bool(f.get("lock")) and f.get("lock") != e.LOCK)
    chk("ports_not_loopback_free_distinct", f.get("ports_ok") is True)
    chk("selector_invalid", all(g.valid_selector(f.get(k)) for k in ("account_selector", "search_box")))
    chk("accounts_not_three_distinct", f.get("accounts") == [a[0] for a in ACCOUNTS])
    return bad


def view(s):
    """The few production status fields the gates use. Never page text."""
    gm = s.get("gmail") or {}
    lc = gm.get("last_cycle") or {}
    keys = ("key", "result", "last_success_ms", "watermark_ms", "window_lower_s")
    return {"running": gm.get("running"), "started_ms": lc.get("started_ms"), "ended_ms": lc.get("ended_ms"), "result": lc.get("result"),
            "overdue_ms": gm.get("overdue_ms"), "interval": gm.get("interval_secs"), "scope": gm.get("scope"), "paused": s.get("automation_paused"),
            "holds": len(s.get("holds") or []), "unresolved": s.get("unresolved"), "computer": s.get("computer"),
            "model_calls": (s.get("counters") or {}).get("model_calls"), "connects": (s.get("counters") or {}).get("connects"),
            "tasks_running": sum(1 for t in s.get("tasks") or [] if t.get("state") == "running"),
            "accounts": [{k: a.get(k) for k in keys} for a in gm.get("accounts") or []]}


def cycles(views):
    """One record per observed poll start. `accounts` is the status after the poll ended, else None."""
    out = []
    for w in views:
        st = w.get("started_ms")
        if st is None:
            continue
        if not out or out[-1]["started_ms"] != st:
            out.append({"started_ms": st, "ended_ms": None, "result": None, "accounts": None})
        c = out[-1]
        if w.get("running") is False and c["ended_ms"] is None and isinstance(w.get("ended_ms"), int) and w["ended_ms"] >= st:
            c.update(ended_ms=w["ended_ms"], result=w.get("result"), accounts=[dict(a) for a in w.get("accounts") or []])
    return out


def healthy(c):
    """A finished poll whose cycle result is complete and whose three accounts all read `ok` with a fresh success."""
    a = c.get("accounts") or []
    return (c.get("result") == "complete" and [x.get("key") for x in a] == [k[0] for k in ACCOUNTS]
            and all(x.get("result") == "ok" and isinstance(x.get("last_success_ms"), int) and x["last_success_ms"] >= c["started_ms"] for x in a))


def first_window_ok(c, prior):
    """The first poll of a run. An account without a stored watermark looks back the initial window from the start of its own scan,
    which falls inside the poll. An account with one continues from it (a continued host database)."""
    def ok(x):
        lower, wm = x.get("window_lower_s"), (prior.get(x["key"]) or {}).get("watermark_ms")
        if wm is not None:
            return lower == wm // 1000 - OVERLAP
        return isinstance(lower, int) and c["started_ms"] // 1000 - INITIAL_HOURS * 3600 - 1 <= lower <= c["ended_ms"] // 1000 - INITIAL_HOURS * 3600 + 1
    return all(ok(x) for x in c["accounts"])


def continues(prev, c):
    """Each account's window starts at the stored watermark minus the overlap, and its watermark moved forward."""
    before = {a["key"]: a for a in prev["accounts"]}
    return all(isinstance(x.get("window_lower_s"), int) and x["key"] in before
               and x["window_lower_s"] == before[x["key"]]["watermark_ms"] // 1000 - OVERLAP and x["watermark_ms"] > before[x["key"]]["watermark_ms"]
               for x in c["accounts"])


def cadence(cs):
    """Start-to-start gaps in seconds between consecutive observed polls."""
    return [round((b["started_ms"] - a["started_ms"]) / 1000, 1) for a, b in zip(cs, cs[1:])]


def serial_ok(views, db):
    """One running task at most in every sample, one QEMU, and each poll task ends before the next one is created."""
    if any((w.get("tasks_running") or 0) > 1 for w in views):
        return False
    polls = sorted(db["polls"], key=lambda t: t["seq"])
    return all(a["ended_at"] is not None and a["ended_at"] <= b["created_at"] for a, b in zip(polls, polls[1:]))


def journal_problems(db, status_unresolved):
    """Fixed codes. Every command belongs to a gmail_poll task and is terminal, and no command is unresolved."""
    ids = {t["id"] for t in db["polls"]}
    bad = []
    if not db["commands"]:
        bad.append("no_commands")
    if any(c["task_id"] not in ids for c in db["commands"]):
        bad.append("command_outside_gmail_poll")
    if any(c["state"] not in TERMINAL for c in db["commands"]):
        bad.append("command_not_terminal")
    if status_unresolved != 0:
        bad.append("unresolved")
    return bad


def db_facts(path):
    """Read-only facts of the host database: counts, states and times. Never an ID value, a payload or an address."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        q = lambda sql, *p: c.execute(sql, p).fetchall()  # noqa: E731
        tasks = q("SELECT seq, id, state, created_at, ended_at, execution_kind FROM tasks WHERE bot_id = ?", BOT)
        return {"polls": [dict(zip(("seq", "id", "state", "created_at", "ended_at"), t[:5])) for t in tasks if t[5] == "gmail_poll"],
                "model_tasks": sum(1 for t in tasks if t[5] != "gmail_poll"),
                "commands": [dict(zip(("task_id", "state", "epoch"), r)) for r in q("SELECT task_id, state, dispatch_epoch FROM executor_commands WHERE bot_id = ?", BOT)],
                "events": [r[0] for r in q("SELECT event_id FROM bot_events WHERE bot_id = ? AND source = 'gmail_poll'", BOT)],
                "seen": q("SELECT COUNT(*) FROM gmail_seen WHERE bot_id = ?", BOT)[0][0],
                "accounts": {r[0]: {"baseline_done": r[1], "result": r[2], "watermark_ms": r[3], "window_lower_s": r[4]} for r in
                             q("SELECT account, baseline_done, last_result, watermark_ms, window_lower_s FROM gmail_accounts WHERE bot_id = ?", BOT)},
                "bot": [dict(zip(("epoch", "guest_id"), r)) for r in q("SELECT epoch, guest_id FROM executor_bots WHERE bot_id = ?", BOT)]}
    finally:
        c.close()


def probe_ok(out):
    """The narrow `recent` probe confirmed the search box: one complete, stable window scan, from the printed JSON only."""
    try:
        s = json.loads(out)["snapshot"]
    except (ValueError, KeyError, TypeError):
        return False
    return s.get("status") in ("complete", "empty") and s.get("stable") is True


class Stub(BaseHTTPRequestHandler):
    """A loopback model endpoint. It counts requests and answers a fixed no-op text. It stores no request body."""
    count = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with Stub.lock:
            Stub.count += 1
        body = (b'data: {"choices":[{"delta":{"content":"no-op"},"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\ndata: [DONE]\n\n')
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(body)


# ---------- the run, over a boundary ----------

class Run:
    """Decides every gate from what the boundary `env` reports. A failed gate stops the run at once."""

    def __init__(self, env, a):
        self.env, self.a = env, a
        self.m = {"gates": {k: None for k in GATES}, "facts": {}}
        self.views = []
        self.started = False  # the VM of the trial runs and needs `down`
        self.prior = {"accounts": {}, "events": []}

    def need(self, name, ok, detail=None):
        self.m["gates"][name] = PASS if ok else FAIL
        if detail is not None:
            self.m["facts"][name] = detail
        if not ok:
            raise Stop(f"gate failed: {name}")

    def sample(self):
        w = view(self.env.status())
        self.views.append(w)
        return w

    def wait(self, what, cond, limit, every=2.0):
        end = self.env.now() + limit
        while self.env.now() < end:
            w = self.sample()
            if cond(w):
                return w
            self.env.sleep(every)
        raise Stop(f"timed out: {what}")

    def quiet(self, seconds):
        """Watch `seconds` with the browser idle. Returns the poll and command counts before and after."""
        before = self.env.db()
        end = self.env.now() + seconds
        while self.env.now() < end:
            self.sample()
            self.env.sleep(2.0)
        after = self.env.db()
        return (len(before["polls"]), len(before["commands"])), (len(after["polls"]), len(after["commands"]))

    def flow(self):
        env, m = self.env, self.m
        bad = preflight(env.facts())
        self.need("preflight", not bad, bad)
        rc, out = env.resume()
        self.started = True  # the helper cleans up its own failed start, and `down` is safe to call again
        self.need("helper_resume", rc == 0, out)
        rcs = [env.switch(label, idx) for _, idx, _, label in ACCOUNTS]
        self.need("selectors_three_accounts", all(r == 0 for r in rcs), rcs)
        rc, out = env.probe()
        self.need("search_box_confirmed", rc == 0 and probe_ok(out))
        self.prior = env.db()  # a continued host database keeps its watermarks and events
        env.start_model()
        env.start_server()
        w = self.wait("server connects", lambda w: w["computer"] == "awake" and (w["connects"] or 0) >= 1, 90)
        if w["paused"] is True and getattr(self.a, "data_dir", None):  # the pause that the earlier run set when it stopped: release only that one
            env.pause(False)
            w = self.sample()
        self.need("server_connected_gmail_configured", w["interval"] == INTERVAL and w["scope"] == "date_window" and w["paused"] is False and w["holds"] == 0,
                  {"interval": w["interval"], "computer": w["computer"]})
        cap = env.now() + self.a.max_seconds
        first = self.wait("first poll finished", lambda w: any(c["ended_ms"] for c in cycles(self.views)), min(240, cap - env.now()))
        c1 = cycles(self.views)[0]
        db = env.db()
        self.need("baseline_three_healthy", healthy(c1) and first_window_ok(c1, self.prior["accounts"]), {"result": c1["result"], "accounts": [x["result"] for x in c1["accounts"]]})
        silent = len(db["events"]) == len(self.prior["events"]) and env.model_requests() == 0 and (first["model_calls"] or 0) == 0
        self.need("baseline_silent_zero_model", silent, {"new_events": len(db["events"]) - len(self.prior["events"]), "model_requests": env.model_requests()})
        self.need("journal_owned_by_gmail_poll", bool(db["polls"]) and not journal_problems(db, first["unresolved"]), journal_problems(db, first["unresolved"]))
        self.wait("three polls finished", lambda w: sum(1 for c in cycles(self.views) if c["ended_ms"]) >= 3, max(1, cap - env.now()))
        cs = [c for c in cycles(self.views) if c["ended_ms"]][:3]
        self.need("cycles_observed", len(cs) == 3 and all(healthy(c) for c in cs), [c["result"] for c in cs])
        self.need("later_windows_continue", continues(cs[0], cs[1]) and continues(cs[1], cs[2]))
        db = env.db()
        last = {x["key"]: x for x in cs[2]["accounts"]}
        self.need("watermark_persisted", all(db["accounts"].get(k, {}).get("watermark_ms") == last[k]["watermark_ms"] and db["accounts"][k]["baseline_done"] == 1 for k in last))
        gaps = cadence(cs)
        self.need("cadence_scheduled_interval", any(CADENCE_MIN <= x <= CADENCE_MAX for x in gaps), gaps)
        self.need("no_duplicate_events", len(db["events"]) == len(set(db["events"])), {"events": len(db["events"]), "seen": db["seen"]})
        m["facts"]["real_arrival_unverified"] = len(db["events"])  # an unknown change is recorded, never claimed as an incoming-mail proof
        self.need("single_serial_owner", serial_ok(self.views, db) and len(env.running_vms()) == 1)
        # pause while idle: the last poll has ended
        env.pause(True)
        due = self.wait("a due tick while paused", lambda w: w["paused"] is True and (w["running"] or (w["overdue_ms"] or 0) > 0), INTERVAL + 60)
        before, after = self.quiet(8)
        self.need("pause_blocks_due_tick", due["running"] is False and before == after and not any(w["running"] for w in self.views[-5:]), {"before": before, "after": after})
        wm = {k: dict(v_) for k, v_ in env.db()["accounts"].items()}
        epoch0 = env.db()["bot"]
        env.restart_server()
        w = self.wait("server reconnects", lambda w: w["computer"] == "awake" and (w["connects"] or 0) >= 1, 90)
        before, after = self.quiet(20)
        db = env.db()
        self.need("restart_keeps_pause_and_state", w["paused"] is True and before == after and env.vm_alive() and
                  {k: v_["watermark_ms"] for k, v_ in db["accounts"].items()} == {k: v_["watermark_ms"] for k, v_ in wm.items()} and
                  all(v_["baseline_done"] == 1 for v_ in db["accounts"].values()), {"paused": w["paused"], "before": before, "after": after})
        env.pause(False)
        tail_start = len(self.views)
        self.wait("resumed poll finished", lambda w: any(c["ended_ms"] for c in cycles(self.views[tail_start:])), min(240, max(1, cap + 400 - env.now())))
        post = [c for c in cycles(self.views[tail_start:]) if c["ended_ms"]][0]
        prev = {"accounts": [{"key": k, "watermark_ms": x["watermark_ms"]} for k, x in wm.items()]}
        prev["accounts"].sort(key=lambda x: x["key"])
        self.need("resume_runs_poll_without_reseed", healthy(post) and continues(prev, post), {"result": post["result"]})
        db = env.db()
        bad = journal_problems(db, self.views[-1]["unresolved"])
        self.need("journal_clean", not bad and self.views[-1]["holds"] == 0, bad)
        ident = env.guest_identity()
        self.need("executor_identity_kept", len(db["bot"]) == 1 and db["bot"][0]["guest_id"] == epoch0[0]["guest_id"] == ident and db["bot"][0]["epoch"] >= epoch0[0]["epoch"] >= 1,
                  {"epoch_before_restart": epoch0[0]["epoch"], "epoch_after": db["bot"][0]["epoch"]})
        m["facts"]["model_requests"] = env.model_requests()
        m["facts"]["events_total"] = len(db["events"])
        m["facts"]["cadence_gaps_s"] = gaps

    def quiesce(self):
        """Hold automation and let a running poll end, so the server never stops in the middle of a browser command."""
        try:
            self.env.pause(True)
            end = self.env.now() + 150
            while self.env.now() < end and self.sample()["running"]:
                self.env.sleep(2.0)
        except BaseException as x:  # noqa: BLE001 - the server may be down already
            self.m["facts"]["quiesce"] = type(x).__name__

    def finish(self):
        """Clean up what this run started, whatever happened. The disk, the host database and the saved profile stay."""
        env, m = self.env, self.m
        try:
            self.quiesce()
            env.stop_server()
            env.stop_model()
            if self.started:
                rc, down = env.down()
                m["facts"]["down"] = down
                ok = rc == 0 and down.get("survivors") == [] and down.get("base_unchanged") is True and down.get("disk_kept") is True
                m["gates"]["cleanup_clean"] = PASS if ok and not env.survivors() else FAIL
            else:
                m["gates"]["cleanup_clean"] = PASS if not env.survivors() else FAIL
        except BaseException as x:  # noqa: BLE001
            m["gates"]["cleanup_clean"] = FAIL
            m["facts"]["cleanup_error"] = type(x).__name__

    def run(self):
        try:
            self.flow()
        except Stop as x:
            self.m["stopped"] = str(x)[:200]
        except BaseException as x:  # noqa: BLE001 - the message may hold private text
            self.m["stopped"] = f"error: {type(x).__name__}"
        finally:
            self.finish()
        self.m["acceptance_complete"] = all(self.m["gates"][k] == PASS for k in GATES)
        self.m["views"] = len(self.views)
        return self.m


# ---------- the live boundary ----------

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Live:
    def __init__(self, a):
        self.a = a
        self.out = Path(a.out)
        self.priv = self.out / "private"
        self.data = Path(a.data_dir) if a.data_dir else self.priv / "data"
        self.t0 = time.monotonic()
        self.trial = Path(a.trial)
        self.rt = self.trial / "computers" / BOT
        self.p = v.Paths(self.rt)
        self.contract = json.loads(Path(a.contract).read_text())
        self.account_selector = json.loads((self.trial / g.REFS).read_text())["account_control_selector"]
        self.cfg = {"server_port": a.server_port or free_port(), "model_port": a.model_port or free_port(), "fixture_port": free_port(), "data_dir": str(self.data),
                    "runtime_dir": str(self.rt), "idle_secs": 3600, "account_selector": self.account_selector, "search_box": self.contract.get("qbox")}
        self.client = e.Client(f"http://127.0.0.1:{self.cfg['server_port']}")
        self.procs, self.owned, self.httpd, self.n = {}, {}, None, 0

    def now(self):
        return time.monotonic() - self.t0

    def sleep(self, s):
        time.sleep(s)

    def facts(self):
        a, out = self.a, self.out
        rec = json.loads((self.trial / g.MANIFEST_UP).read_text())
        try:
            bound = v.read_json(self.rt / "executor.json").get("bot_id")
            meta = v.read_json(self.p.diskmeta)
            ident = e.base_identity(a.base)
        except (OSError, ValueError, SystemExit):
            bound, meta, ident = None, {}, {}
        ports = [self.cfg["server_port"], self.cfg["model_port"], self.cfg["fixture_port"]]
        free = True
        for p in ports:
            with socket.socket() as s:
                free = free and s.connect_ex(("127.0.0.1", p)) != 0
        return {"bound_bot": bound, "runtime_dir": str(self.rt), "trial_runtime": str(self.trial / "computers" / BOT), "running_vms": self.running_vms(),
                "base_ok": e.verify_base(a.base), "base_mode": ident.get("mode"), "pinned_sha": v.sha256_file(self.trial / "private" / sp.PINNED_NAME),
                "expect_sha": a.expect_bare_sha256, "recorded_sha": rec["identity"]["candidate"]["sha256"],
                "disk_ok": g.private_ok(self.p.disk, bad=0o022) and meta.get("base", {}).get("id") == ident.get("id"),
                "out_new": not out.exists() or not any(out.iterdir()), "out_private": (not out.exists()) or g.private_ok(out, True),
                "db_dir_ok": self.db_dir_ok(), "db_bound": self.db_bound(), "lock": a.lock, "ports_ok": len(set(ports)) == 3 and free,
                "account_selector": self.account_selector, "search_box": self.cfg["search_box"], "accounts": [k[0] for k in ACCOUNTS]}

    def db_dir_ok(self):
        """A new run needs no database. A continued run needs a private directory that holds one."""
        if not self.a.data_dir:
            return not self.data.exists()
        return g.private_ok(self.data, True) and g.private_ok(self.data / "deck.db", bad=0o022)

    def db_bound(self):
        """A continued database must name the guest of this runtime. Its epoch must never be reset."""
        if not self.a.data_dir:
            return True
        try:
            return [b["guest_id"] for b in db_facts(self.data / "deck.db")["bot"]] == [self.guest_identity()]
        except (sqlite3.Error, OSError, KeyError):
            return False

    def running_vms(self):
        return g.running_vms(self.p)

    def vm_alive(self):
        return len(self.running_vms()) == 1

    def helper(self, script, pre, sub, timeout):
        """Run a helper CLI with output kept in a private file. Returns (exit code, stdout text)."""
        self.n += 1
        argv = [sys.executable, str(HERE / script), "--out", str(self.trial), *pre, *sub]
        try:
            r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, ""
        (self.priv / f"helper-{self.n}.out").write_text(r.stdout)
        (self.priv / f"helper-{self.n}.err").write_text(r.stderr)
        return r.returncode, r.stdout

    def common(self):
        return ["--base", self.a.base, "--lock", self.a.lock, "--max-load", "inf", "--min-avail-mib", str(self.a.min_avail_mib)]

    def resume(self):
        self.out.mkdir(mode=0o700, exist_ok=True)
        self.priv.mkdir(mode=0o700, exist_ok=True)
        rc, out = self.helper("gmail_accounts_e2e.py", self.common(), ["resume", "--expect-bare-sha256", self.a.expect_bare_sha256], 420)
        try:
            return rc, json.loads(out.strip().splitlines()[-1]).get("gates", {}) if rc == 0 else {}
        except (ValueError, IndexError):
            return 1, {}

    def switch(self, label, idx):
        return self.helper("gmail_accounts_e2e.py", ["--base", self.a.base, "--lock", self.a.lock, "--max-load", "inf"], ["switch", "--to", label, "--index", str(idx)], 150)[0]

    def probe(self):
        ev = self.out / "probe-evidence"
        return self.helper("gmail_inbox_probe.py", ["--evidence", str(ev), "--base", self.a.base, "--max-load", "inf"],
                           ["recent", "--account", "A", "--index", "0", "--contract", self.a.contract, "--max-pages", str(MAX_PAGES), "--max-seconds", str(MAX_SCAN)], 240)

    def start_model(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.cfg["model_port"]), Stub)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop_model(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def model_requests(self):
        return Stub.count

    def start_server(self):
        cfg = self.priv / "deck.toml"
        self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
        cfg.write_text(render_config(self.cfg))
        cfg.chmod(0o600)
        self.spawn(cfg)

    def spawn(self, cfg):
        env = dict(os.environ, DECK_CONFIG=str(cfg), DECK_MODEL_KEY="dummy-loopback-stub-key")
        self.n += 1
        log = open(self.priv / f"server-{self.n}.log", "wb")
        self.procs["server"] = subprocess.Popen([self.a.deck], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=self.out)
        self.owned.update(e.capture_processes([self.procs["server"].pid]))
        self.client.until("the server", lambda: self.client.raw("GET", "/api/bot")[0] == 200 if self.procs["server"].poll() is None else sys.exit("FAILED: the server exited"), 60)

    def stop_server(self):
        p = self.procs.get("server")
        if p and p.poll() is None:
            self.owned.update(e.capture_processes([p.pid]))
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def restart_server(self):
        self.stop_server()
        self.spawn(self.priv / "deck.toml")

    def status(self):
        code, raw = self.client.raw("GET", "/api/bot")
        return json.loads(raw) if code == 200 else {}

    def pause(self, paused):
        if self.client.raw("POST", "/api/bot/automation", {"paused": paused})[0] != 204:
            raise Stop("pause call failed")

    def db(self):
        if not (self.data / "deck.db").exists():
            return {"polls": [], "model_tasks": 0, "commands": [], "events": [], "seen": 0, "accounts": {}, "bot": []}
        return db_facts(self.data / "deck.db")

    def guest_identity(self):
        return v.read_json(self.rt / "executor.json").get("guest_id")

    def down(self):
        rc, out = self.helper("gmail_accounts_e2e.py", ["--base", self.a.base], ["down", "--keep-disk"], 200)
        try:
            return rc, json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 1, {}

    def survivors(self):
        return e.processes_left(self.owned) + [str(p) for p in self.running_vms()]


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="a NEW private directory for this run (host database, config, logs)")
    ap.add_argument("--trial", required=True, help="the retained trial directory of gmail_accounts_e2e.py")
    ap.add_argument("--base", required=True, help="the frozen base .qcow2 with its sidecar; never modified")
    ap.add_argument("--contract", required=True, help="private JSON probe contract that holds `qbox` (read only)")
    ap.add_argument("--deck", required=True, help="the Deck binary to run")
    ap.add_argument("--expect-bare-sha256", required=True)
    ap.add_argument("--lock", required=True, help="an owned lock file, not the shared default")
    ap.add_argument("--data-dir", help="continue an earlier run: its private data directory (host database). Never reset the executor epoch")
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-seconds", type=int, default=720, help="budget for the observation of the first three polls")
    ap.add_argument("--server-port", type=int, default=0)
    ap.add_argument("--model-port", type=int, default=0)
    return ap.parse_args(argv)


def main(argv=None, live=Live):
    a = parse(argv)
    env = live(a)
    m = Run(env, a).run()
    out = Path(a.out)
    if out.exists():
        (out / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True, default=str) + "\n")
        (out / "manifest.json").chmod(0o600)
    print(json.dumps({"gates": m["gates"], "acceptance_complete": m["acceptance_complete"], "stopped": m.get("stopped"), "facts": m["facts"]}, sort_keys=True, default=str))
    return 0 if m["acceptance_complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
