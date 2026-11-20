#!/usr/bin/env python3
"""Durable local service for the Deck Gmail poller. One shared browser, three accounts, poll-only.

It supervises the real Deck binary. Deck owns the retained VM and its host database. Nothing here signs in, opens a
message, sends, labels, archives or deletes. The model is never configured: `poll_only` keeps every new event
unprocessed in the host database until a later run enables a consumer.

  install   copy the code and the binary into the service directory, write the private config and the user unit
  run       the unit entry point: lock, preflight, resume the VM, start Deck, watch it, stop it safely
  status    print the private status file with ages (fixed words, account keys and counts only)
  verify    read-only gates over the live status and a copy of the host database
  adopt-pause  mark the current automation pause as the service's own, so the next start releases it once

The status file and the config stay private (mode 0600) outside Git. A page text, an address or a cookie never
enters the status, a log line or an alert.
"""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
INTERVAL = 120
STALE_S = 5 * INTERVAL  # no success for five intervals
ALERT_EVERY_S = 3600
IMMEDIATE = ("auth", "identity_mismatch")  # the worker never signs in: a person must act
ACCOUNTS = ("account_a", "account_b", "account_c")
BOT = "shared-profile"


# ---------- pure helpers (unit tested) ----------

SUMMARY_KEYS = ("cleanup", "time", "timezone", "labels", "label_halted", "label_states", "triage_backlog", "triage_errors", "next_summary", "last_delivery", "last_phase")


def render_config(c):
    """The private deck.toml. Mode `poll_only` (default): no model endpoint is reachable, so no event can reach a model.
    Mode `summary`: the model endpoint and name come from the private config, the key never. Deck then batches no
    general agent task: the constrained summary owns the events."""
    c = effective(c)
    mode = c.get("mode", "poll_only")
    if mode not in ("poll_only", "summary"):
        sys.exit("STOPPED: unknown service mode")
    if mode == "summary" and not (c.get("model_base_url") and c.get("model_name")):
        sys.exit("STOPPED: summary mode needs a model endpoint and a model name")
    model = (f'base_url = {json.dumps(c["model_base_url"])}\nagent_model = {json.dumps(c["model_name"])}\nsmall_model = {json.dumps(c["model_name"])}'
             if mode == "summary" else 'base_url = "http://127.0.0.1:9/v1"\nagent_model = "unused"\nsmall_model = "unused"')
    labels = c.get("labels")
    tail = ""
    if mode == "summary":
        tail = f'[gmail.summary]\ntime = {json.dumps(c.get("summary_time", "08:00"))}\ntimezone = "Asia/Kolkata"\n'
        for k in ("max_threads_per_run", "batch_size", "max_run_secs", "max_read_chars"):
            if c.get("summary_" + k):
                tail += f"{k} = {int(c['summary_' + k])}\n"
        if c.get("summary_label_catchup"):
            tail += "label_catchup = true\n"
        if labels:
            tail += f'[gmail.summary.labels]\nchips_selector = {json.dumps(labels["chips_selector"])}\n[gmail.summary.labels.steps]\n' + "".join(
                f"{k} = {json.dumps(labels['steps'][k])}\n" for k in ("needs_reply", "action", "waiting", "fyi", "receipts", "jobs", "tech", "review") if k in labels["steps"])
            # Dynamic topic steps (D106) hold `{label}` inside a quoted argument. They sit in the labels table, before its steps table.
            topic_keys = "".join(f"{k} = {json.dumps(labels[k])}\n" for k in ("topic_step", "topic_create_step") if labels.get(k))
            tail = tail.replace("\n[gmail.summary.labels.steps]\n", "\n" + topic_keys + "[gmail.summary.labels.steps]\n", 1)
        cleanup = c.get("cleanup")
        if cleanup and labels:
            # Host-owned text from the private config. Deck validates it again at start.
            tail += f'[gmail.summary.cleanup]\ntrash_step = {json.dumps(cleanup["trash_step"])}\n' + "".join(
                f"{k} = {int(cleanup[k])}\n" for k in ("max_run_secs", "triage_per_pass", "trash_per_pass", "topics_per_pass", "topics_new_per_pass", "topics_registry_cap", "backlog_pages_per_pass", "backlog_queue_cap") if k in cleanup)
            if "backlog" in cleanup:
                tail += f"backlog = {'true' if cleanup['backlog'] else 'false'}\n"
            if cleanup.get("backlog_exclude"):
                tail += f"backlog_exclude = {json.dumps(cleanup['backlog_exclude'])}\n"
    accts = "".join(f'[[gmail.accounts]]\nkey = "{k}"\nindex = {i}\nuser_secret = "GMAIL_{k[-1].upper()}_USER"\n' for i, k in enumerate(ACCOUNTS))
    host = c.get("bind", "127.0.0.1")
    peers = list(dict.fromkeys(["127.0.0.1", *([host] if host != "127.0.0.1" else []), *c.get("proxy_peers", [])]))
    return f'''listen = "{host}:{c["port"]}"
data_dir = "{c["data_dir"]}"
trusted_proxies = {json.dumps(peers)}
[model]
{model}
api_key_env = "DECK_MODEL_KEY"
[bare]
command = "unused"
[agent]
max_steps = 4
[vm]
version = 1
bot_id = "{BOT}"
runtime_dir = "{c["runtime_dir"]}"
harness = "{c["app"]}/scripts/vm-prototype/vmproto.py"
fixture_port = {c["fixture_port"]}
internet = true
idle_secs = 3600
command_secs = 120
lifecycle_secs = 240
[gmail]
profile = "main"
{"poll_only = true" if mode == "poll_only" else ""}
account_control_selector = {json.dumps(c["account_selector"])}
search_box_selector = {json.dumps(c["search_box"])}
interval_secs = {INTERVAL}
initial_hours = 24
overlap_secs = 3600
max_pages = 3
max_scan_secs = 90
{accts}{tail}{c.get('provisioning_toml', '')}'''


def summarize(s, now_ms, prev=None):
    """The private status view from one /api/bot reply. `prev` carries the cycle counter between samples."""
    gm = s.get("gmail") or {}
    lc = gm.get("last_cycle") or {}
    prev = prev or {}
    done = prev.get("cycles_seen", [])
    st, en = lc.get("started_ms"), lc.get("ended_ms")
    if lc.get("result") == "complete" and isinstance(st, int) and isinstance(en, int) and en >= st and st not in done:
        done = (done + [st])[-1000:]
    age = lambda ms: None if not isinstance(ms, int) else max(0, (now_ms - ms) // 1000)  # noqa: E731
    accts = [{"key": a.get("key"), "result": a.get("result"), "last_attempt_age_s": age(a.get("last_attempt_ms")),
              "last_success_age_s": age(a.get("last_success_ms")), "watermark_age_s": age(a.get("watermark_ms"))} for a in gm.get("accounts") or []]
    return {"at_ms": now_ms, "mode": gm.get("mode"), "computer": s.get("computer"), "paused": s.get("automation_paused"),
            "holds": len(s.get("holds") or []), "unresolved": s.get("unresolved"),
            "running": gm.get("running"), "stage": gm.get("stage"), "stage_account": gm.get("stage_account"),
            "last_cycle": {"result": lc.get("result"), "started_age_s": age(st), "ended_age_s": age(en)},
            "next_due_in_ms": gm.get("next_due_in_ms"), "overdue_ms": gm.get("overdue_ms"),
            "unprocessed_events": gm.get("unprocessed_events"), "model_calls": (s.get("counters") or {}).get("model_calls"),
            "cycles_complete_observed": len(done), "cycles_seen": done, "accounts": accts,
            "summary": {k: gm["summary"].get(k) for k in SUMMARY_KEYS} if gm.get("summary") else None}


def alert_list(v):
    """(key, level, text) for each condition that needs a person. Fixed words, opaque account keys, counts. Never page text."""
    out = []
    if v.get("holds"):
        out.append(("hold", "alert", f"Gmail poller: {v['holds']} unknown-outcome hold(s). Automation is blocked until the owner decides."))
    for a in v.get("accounts") or []:
        if a.get("result") in IMMEDIATE:
            out.append((f"auth-{a['key']}", "urgent", f"Gmail poller: {a['key']} needs a person ({a['result']}). The worker never signs in."))
        elif not v.get("paused") and (a.get("last_success_age_s") or 0) > STALE_S and a.get("last_attempt_age_s") is not None:
            out.append((f"stale-{a['key']}", "alert", f"Gmail poller: {a['key']} has no good scan for {a['last_success_age_s'] // 60} min (last result {a.get('result')})."))
    return out


def accounts_fresh(v):
    """All three accounts, each with a good scan within the stale limit."""
    a = v.get("accounts") or []
    return [x.get("key") for x in a] == list(ACCOUNTS) and all((x.get("last_success_age_s") if x.get("last_success_age_s") is not None else s_inf()) <= STALE_S for x in a)


def s_inf():
    return float("inf")


def read_model_key(item, run=subprocess.run):
    """The model key from the vault item, read at start into memory. The value never goes into an argument, a file or a log."""
    try:
        r = run(["rbw", "get", item], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        sys.exit("STOPPED: the vault is not reachable")
    v = r.stdout.strip()
    if r.returncode != 0 or not v:
        sys.exit("STOPPED: the vault item for the model key is missing or empty")
    return v


def model_env(c):
    return read_model_key(c["model_key_item"]) if c.get("mode") == "summary" else "unused"


def mode_gates(mode, st_mode, v, db):
    """The gates that depend on the mode. Poll-only keeps every event unprocessed and calls no model.
    Summary mode hands every event to triage and starts no general agent task."""
    return {"mode_matches": st_mode == ("poll_only" if mode == "poll_only" else "triage"),
            "no_general_agent_task": db["model_tasks"] == 0,
            "model_calls_allowed": mode == "summary" or not v["model_calls"],
            "events_as_the_mode_requires": v["unprocessed_events"] == (len(db["events"]) if mode == "poll_only" else 0)}


def journal_settled(db, unresolved):
    """Every command ended `succeeded` and none is unresolved. Deck prunes finished poll tasks, so a command may name a task that is gone."""
    return bool(db["commands"]) and all(c["state"] == "succeeded" for c in db["commands"]) and not unresolved


def due_alerts(alerts, sent, now_s):
    """Drop an alert sent within ALERT_EVERY_S. An `urgent` alert repeats at the same pace, never faster."""
    return [a for a in alerts if a[0] not in sent or now_s - sent[a[0]] >= ALERT_EVERY_S]


# ---------- files ----------

def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load(path):
    p = Path(path)
    if p.stat().st_mode & 0o077:
        sys.exit(f"STOPPED: {p.name} is not private")
    return json.loads(p.read_text())


def vm_env(c, env):
    """A production VM must outlive the 3 hour prototype bound; the config opts in with `persistent_vm`."""
    out = {k: v for k, v in env.items() if k != "DECK_VM_LIFETIME_S"}
    out = dict(out, DECK_VM_LIFETIME_S="infinity") if c.get("persistent_vm") else out
    return dict(out, DECK_VM_BARE_MCP=c["bare_mcp"]) if c.get("bare_mcp") else out  # vmproto installs it in each computer it starts


# ---------- temporary backlog boost ----------
# A boost overlays higher caps on the saved normal ones. The normal values are never overwritten, so ending the boost
# only switches the overlay off. A restart while the overlay is on keeps it on, because the flag lives in the config.
BOOST_MAX_S = 12 * 3600  # a boost never outlives this, whatever the backlog says
BOOST_MIN_WRITES = 10  # judge the uncertain share only after this many label writes since the start
BOOST_MAX_UNCERTAIN_PCT = 25
BOOST_OPEN = ("triage", "topic", "trash")


def effective(c):
    """The config that Deck is rendered from: the normal caps, with the boost overlay on top while it is active."""
    b = c.get("boost") or {}
    if not b.get("active"):
        return c
    out = dict(c)
    if "cleanup" in c:
        out["cleanup"] = {**c["cleanup"], **(b.get("cleanup") or {})}
    for k, v in (b.get("summary") or {}).items():
        out["summary_" + k] = v
    return out


def boost_decision(b, v, now_s):
    """None while the boost goes on. ("done", why) when the backlog walk is complete and no work is open.
    ("back_down", why) when a hold, a halt, a stale scan, a rising uncertain share or the time limit asks for the normal caps."""
    if not (b or {}).get("active"):
        return None
    sm = v.get("summary") or {}
    if v.get("holds"):
        return ("back_down", "hold")
    if sm.get("label_halted"):
        return ("back_down", "label_halted")
    if now_s - b.get("started_s", now_s) > BOOST_MAX_S:
        return ("back_down", "time_limit")
    # A restart wipes the scan ages and the stop itself costs minutes. Judge scans only once the boost is older than one stale limit.
    age = now_s - b.get("started_s", now_s)
    if age > STALE_S and not v.get("paused") and any((a.get("last_success_age_s") if a.get("last_success_age_s") is not None else s_inf()) > STALE_S
                                                     for a in v.get("accounts") or []):
        return ("back_down", "stale_scan")
    lab, base = sm.get("label_states") or {}, b.get("base_labels") or {}
    grown = {k: max(0, lab.get(k, 0) - base.get(k, 0)) for k in ("applied", "uncertain")}
    writes = grown["applied"] + grown["uncertain"]
    if writes >= BOOST_MIN_WRITES and grown["uncertain"] * 100 > writes * BOOST_MAX_UNCERTAIN_PCT:
        return ("back_down", "uncertain_share")
    cl = sm.get("cleanup") or {}
    walk = cl.get("backlog") or {}
    if walk and all(w.get("state") == "complete" for w in walk.values()) and not any((cl.get("open") or {}).get(k) for k in BOOST_OPEN) \
            and not any((sm.get("triage_backlog") or {}).get(k, [0])[0] for k in ACCOUNTS):
        return ("done", "drained")
    return None


def boost_view(b, now_s):
    """Fixed words and counts for the private status file."""
    b = b or {}
    return {"active": bool(b.get("active")), "age_s": max(0, now_s - b["started_s"]) if b.get("started_s") else None,
            "ended_reason": b.get("ended_reason")}


def set_boost(path, active, now_s, reason=None, caps=None, base_labels=None):
    """Re-read the config, change only the `boost` key and write it back. Returns the new config."""
    cfg = load(path)
    b = cfg.get("boost") or {}
    if active:
        b = {"active": True, "started_s": now_s, "cleanup": caps["cleanup"], "summary": caps["summary"], "base_labels": base_labels or {}}
    else:
        b.update(active=False, ended_s=now_s, ended_reason=reason)
    cfg["boost"] = b
    private_write(path, json.dumps(cfg, indent=1, sort_keys=True) + "\n")
    return cfg


# ---------- the live boundary ----------

class Svc:
    def __init__(self, cfg):
        import setup_e2e as e  # noqa: PLC0415 - the helpers need the harness modules only at run time
        import vmproto as v  # noqa: PLC0415
        self.e, self.v = e, v
        self.c = cfg
        self.state = Path(cfg["state_dir"])
        self.app = Path(cfg["app"]) / "scripts" / "vm-prototype"
        self.base_url = f"http://{cfg.get('bind', '127.0.0.1')}:{cfg['port']}"
        self.proc = None
        self.sent = {}

    def api(self, method, path, body=None):
        return self.e.http(self.base_url, method, path, body, timeout=15)

    def status(self):
        code, raw = self.api("GET", "/api/bot")
        return json.loads(raw) if code == 200 else None

    def helper(self, sub, timeout):
        a = [sys.executable, str(self.app / "gmail_accounts_e2e.py"), "--out", self.c["trial"], "--base", self.c["base"],
             "--lock", str(self.state / "boot.lock"), "--max-load", "inf", "--min-avail-mib", str(self.c["min_avail_mib"]), *sub]
        r = subprocess.run(a, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, env=vm_env(self.c, os.environ))
        private_write(self.state / "helper-last.out", r.stdout[-4000:] + "\n" + r.stderr[-2000:])
        return r.returncode

    def resume_args(self):
        """The helper arguments of a resume: install the bare-mcp file of the deploy when the guest holds another one."""
        f = self.c["bare_mcp"]
        return ["resume", "--expect-bare-sha256", sha256(f), "--bare-candidate", f]

    def vm_running(self):
        import gmail_accounts_e2e as g  # noqa: PLC0415
        return g.running_vms(self.v.Paths(Path(self.c["runtime_dir"])))

    def notify(self, level, text):
        try:
            subprocess.run([str(Path.home() / ".local/bin/tg-notify"), level, text], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def preflight(self):
        import gmail_worker_e2e as w  # noqa: PLC0415
        c = self.c
        bad = []
        try:
            sha256(c["bare_mcp"])
        except (KeyError, OSError):
            bad.append("bare_mcp_missing")
        if sha256(c["deck"]) != c["deck_sha256"]:
            bad.append("deck_binary_changed")
        if b"The web app is not built" in Path(c["deck"]).read_bytes():
            bad.append("deck_binary_has_no_web_app")
        db = Path(c["data_dir"]) / "deck.db"
        try:
            guests = [b["guest_id"] for b in w.db_facts(db)["bot"]]
            ident = self.v.read_json(Path(c["runtime_dir"]) / "executor.json").get("guest_id")
            if guests != [ident]:
                bad.append("host_db_not_bound_to_this_guest")
        except Exception:  # noqa: BLE001
            bad.append("host_db_unreadable")
        if c.get("mode") == "summary":
            try:
                model_env(c)
            except (SystemExit, OSError):
                bad.append("model_key_unusable")
        why = self.e.pressure(Path("/proc/meminfo").read_text(), Path("/proc/loadavg").read_text(), c["min_avail_mib"], float("inf"))
        if why:
            bad.append("host_pressure")
        return bad

    def start_deck(self):
        cfg = self.state / "deck.toml"
        private_write(cfg, render_config(self.c))
        log = open(self.state / "deck.log", "ab")
        os.chmod(self.state / "deck.log", 0o600)
        env = dict(vm_env(self.c, os.environ), DECK_CONFIG=str(cfg), DECK_MODEL_KEY=model_env(self.c))
        self.proc = subprocess.Popen([self.c["deck"]], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=self.state)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                return False
            if self.api("GET", "/api/bot")[0] == 200:
                return True
            time.sleep(0.5)
        return False

    def pause(self, paused):
        return self.api("POST", "/api/bot/automation", {"paused": paused})[0] == 204

    def quiesce(self):
        """Pause only if the owner has not, remember it, and let a running poll end before Deck stops."""
        s = self.status()
        if s is None:
            return
        if not s.get("automation_paused") and self.pause(True):
            (self.state / "paused-by-service").write_text("1")
        end = time.monotonic() + 150
        while time.monotonic() < end and (self.status() or {}).get("gmail", {}).get("running"):
            time.sleep(2)

    def stop_deck(self):
        p = self.proc
        if p and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def end_boost(self, path, reason):
        """Switch the overlay off in the config, then restart Deck once at a quiet point so it reads the normal caps."""
        self.c = set_boost(path, False, int(time.time()), reason)
        self.quiesce()
        self.stop_deck()
        return self.start_deck()

    def sample(self, prev):
        s = self.status()
        if s is None:
            return None
        v = summarize(s, int(time.time() * 1000), prev)
        v["boost"] = boost_view(self.c.get("boost"), int(time.time()))
        private_write(self.state / "status.json", json.dumps(v, indent=1, sort_keys=True) + "\n")
        now_s = int(time.time())
        for key, level, text in due_alerts(alert_list(v), self.sent, now_s):
            self.sent[key] = now_s
            self.notify(level, text)
        for k in [k for k in self.sent if k not in {a[0] for a in alert_list(v)}]:
            self.sent.pop(k)  # the condition cleared: a new occurrence alerts at once
        return v


def cmd_run(a):
    cfg = load(a.config)
    state = Path(cfg["state_dir"])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(state / "service.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("STOPPED: another service run holds the lock", file=sys.stderr)
        return 4
    s = Svc(cfg)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
    bad = s.preflight()
    if bad:
        print("STOPPED:", ",".join(bad), file=sys.stderr)
        s.notify("failed", f"Gmail poller did not start: {','.join(bad)}")
        return 2
    if not s.vm_running():
        if s.helper(s.resume_args(), 420) != 0:
            print("STOPPED: the VM did not resume", file=sys.stderr)
            s.notify("failed", "Gmail poller: the VM did not resume. Disk and database are kept.")
            return 3
    rc = 0
    try:
        if not s.start_deck():
            print("STOPPED: Deck did not start", file=sys.stderr)
            s.notify("failed", "Gmail poller: Deck did not start.")
            return 5
        marker = state / "paused-by-service"
        if marker.exists() and s.pause(False):
            marker.unlink()
        prev = {}
        while not stopping and s.proc.poll() is None:
            v = s.sample(prev)
            prev = v or prev
            d = boost_decision(s.c.get("boost"), v, int(time.time())) if v else None
            if d:
                if not s.end_boost(a.config, d[1]):
                    print("STOPPED: Deck did not start after the boost ended", file=sys.stderr)
                    s.notify("failed", "Gmail poller: Deck did not restart after the boost ended.")
                    return 5
                if marker.exists() and s.pause(False):
                    marker.unlink()
                if d[0] == "back_down":
                    s.notify("alert", f"Gmail poller: the boost ended early ({d[1]}). The normal caps are back.")
            for _ in range(int(cfg.get("sample_secs", 20))):
                if stopping or s.proc.poll() is not None:
                    break
                time.sleep(1)
        if s.proc.poll() is not None and not stopping:
            rc = 6
            s.notify("failed", "Gmail poller: Deck exited. The service restarts it.")
    finally:
        if s.proc and s.proc.poll() is None:
            s.quiesce()
        s.stop_deck()
        if s.vm_running() and s.helper(["down", "--keep-disk"], 200) != 0:
            s.notify("failed", "Gmail poller: the VM did not stop cleanly. Disk and database are kept.")
    return rc


def cmd_configure(a):
    """Set the service mode in the private config. Stop the service first, then start it again. A switch to
    `summary` keeps a copy of the poll-only config. `--mode poll_only` is the rollback: it never touches the database."""
    path = Path(a.config)
    cfg = load(path)
    if a.mode == "summary":
        if not (a.model_base_url and a.model_name and a.model_key_item):
            sys.exit("STOPPED: summary mode needs --model-base-url, --model-name and --model-key-item")
        read_model_key(a.model_key_item)
        if cfg.get("mode", "poll_only") == "poll_only":
            private_write(str(path) + ".poll-only", json.dumps(cfg, indent=1, sort_keys=True) + "\n")
        cfg.update(mode="summary", model_base_url=a.model_base_url, model_name=a.model_name, model_key_item=a.model_key_item,
                   summary_time=a.summary_time, summary_max_threads_per_run=a.summary_max_threads, summary_batch_size=a.summary_batch_size, summary_label_catchup=a.summary_label_catchup)
        if a.labels_file:
            cfg["labels"] = json.loads(Path(a.labels_file).read_text())
        else:
            cfg.pop("labels", None)
        if a.cleanup_file:
            cfg["cleanup"] = json.loads(Path(a.cleanup_file).read_text())
        else:
            cfg.pop("cleanup", None)
    else:
        cfg["mode"] = "poll_only"
    private_write(path, json.dumps(cfg, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"mode": cfg["mode"], "labels": "configured" if cfg.get("labels") else "off"}))
    return 0


def cmd_boost(a):
    """`on` saves a boost overlay in the config and `off` ends it. The service applies it at its next start.
    Stop the service first for `on`, then start it again. The normal caps stay in the config."""
    path = Path(a.config)
    if a.action == "off":
        set_boost(path, False, int(time.time()), "owner")
        print(json.dumps({"boost": "off"}))
        return 0
    caps = json.loads(Path(a.caps_file).read_text())
    if not isinstance(caps.get("cleanup"), dict) or not isinstance(caps.get("summary"), dict):
        sys.exit("STOPPED: the caps file needs a cleanup table and a summary table")
    cfg = load(path)
    st = json.loads((Path(cfg["state_dir"]) / "status.json").read_text())
    lab = ((st.get("summary") or {}).get("label_states")) or {}
    set_boost(path, True, int(time.time()), caps=caps, base_labels={k: lab.get(k, 0) for k in ("applied", "uncertain")})
    print(json.dumps({"boost": "on"}))
    return 0


def cmd_status(a):
    cfg = load(a.config)
    p = Path(cfg["state_dir"]) / "status.json"
    v = json.loads(p.read_text())
    v["status_file_age_s"] = int(time.time() - p.stat().st_mtime)
    print(json.dumps(v, indent=1, sort_keys=True))
    return 0 if v["status_file_age_s"] < 120 else 1


def cmd_adopt_pause(a):
    private_write(Path(load(a.config)["state_dir"]) / "paused-by-service", "1")
    return 0


def cmd_verify(a):
    """Read-only gates. Prints fixed words and counts. Needs the service running."""
    import gmail_worker_e2e as w  # noqa: PLC0415
    cfg = load(a.config)
    s = Svc(cfg)
    st = s.status()
    db = w.db_facts(Path(cfg["data_dir"]) / "deck.db")
    v = json.loads((Path(cfg["state_dir"]) / "status.json").read_text())
    unit = subprocess.run(["systemctl", "--user", "is-active", a.unit], capture_output=True, text=True).stdout.strip()
    g = {"unit_active": unit == "active",
         "three_complete_cycles": v["cycles_complete_observed"] >= 3,
         "all_accounts_fresh": accounts_fresh(v),
         "journal_settled": journal_settled(db, v["unresolved"]),
         **mode_gates(cfg.get("mode", "poll_only"), (st or {}).get("gmail", {}).get("mode"), v, db),
         "one_executor_identity": len(db["bot"]) == 1 and db["bot"][0]["epoch"] >= 5,
         "one_vm": len(s.vm_running()) == 1, "not_paused": v["paused"] is False, "no_holds": v["holds"] == 0}
    print(json.dumps({"gates": g, "ok": all(g.values()), "events": len(db["events"]), "epoch": db["bot"][0]["epoch"] if db["bot"] else None,
                      "cycles_observed": v["cycles_complete_observed"]}, sort_keys=True))
    return 0 if all(g.values()) else 1


UNIT = """[Unit]
Description=Deck Gmail poller and morning summary (shared browser, three accounts)
StartLimitIntervalSec=1800
StartLimitBurst=5

[Service]
Type=simple
ExecStart={python} -I {app}/scripts/vm-prototype/gmail_service.py run --config {config}
Restart=on-failure
RestartSec=60
TimeoutStopSec=330
KillMode=mixed

[Install]
WantedBy=default.target
"""


def cmd_install(a):
    """Copy the code and the binary into the service directory and write the private config and the user unit."""
    home = Path(a.home or Path.home())
    root = home / ".local/share/deck-gmail"
    app = root / "app"
    shutil.rmtree(app / "scripts", ignore_errors=True)
    shutil.copytree(HERE, app / "scripts" / "vm-prototype", ignore=shutil.ignore_patterns("__pycache__"))  # the harness imports test_multi_account
    (root / "bin").mkdir(parents=True, exist_ok=True)
    deck, bare = root / "bin" / "deck", root / "bin" / "bare-mcp"
    for src, dst in ((a.deck, deck), (a.bare_mcp, bare)):
        shutil.copy2(src, dst.with_suffix(".new"))
        os.replace(dst.with_suffix(".new"), dst)
    cfg = {"app": str(app), "deck": str(deck), "bare_mcp": str(bare), "deck_sha256": sha256(deck), "state_dir": str(root / "state"), "data_dir": a.data_dir,
           "trial": a.trial, "runtime_dir": str(Path(a.trial) / "computers" / BOT), "base": a.base,
           "port": a.port, "fixture_port": a.fixture_port, "min_avail_mib": 6144, "account_selector": a.account_selector, "search_box": a.search_box}
    path = home / ".config/deck-gmail/service.json"
    private_write(path, json.dumps(cfg, indent=1, sort_keys=True) + "\n")
    unit = home / ".config/systemd/user/deck-gmail-poller.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT.format(python=sys.executable, app=app, config=path))
    print(json.dumps({"config": str(path), "unit": str(unit), "app": str(app), "deck_sha256": cfg["deck_sha256"]}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    cfgpath = str(Path.home() / ".config/deck-gmail/service.json")
    for name, fn in (("run", cmd_run), ("status", cmd_status), ("verify", cmd_verify), ("adopt-pause", cmd_adopt_pause)):
        p = sub.add_parser(name)
        p.add_argument("--config", default=cfgpath)
        if name == "verify":
            p.add_argument("--unit", default="deck-gmail-poller.service")
        p.set_defaults(fn=fn)
    c = sub.add_parser("configure")
    c.add_argument("--config", default=cfgpath)
    c.add_argument("--mode", choices=("poll_only", "summary"), required=True)
    for k in ("model-base-url", "model-name", "model-key-item", "labels-file", "cleanup-file"):
        c.add_argument(f"--{k}")
    c.add_argument("--summary-time", default="08:00")
    c.add_argument("--summary-max-threads", type=int)
    c.add_argument("--summary-batch-size", type=int)
    c.add_argument("--summary-label-catchup", action="store_true")
    c.set_defaults(fn=cmd_configure)
    b = sub.add_parser("boost")
    b.add_argument("action", choices=("on", "off"))
    b.add_argument("--config", default=cfgpath)
    b.add_argument("--caps-file")
    b.set_defaults(fn=cmd_boost)
    i = sub.add_parser("install")
    for k in ("deck", "data-dir", "trial", "base", "bare-mcp", "account-selector", "search-box"):
        i.add_argument(f"--{k}", required=True)
    i.add_argument("--port", type=int, required=True)
    i.add_argument("--fixture-port", type=int, required=True)
    i.add_argument("--home")
    i.set_defaults(fn=cmd_install)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
