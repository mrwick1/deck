#!/usr/bin/env python3
"""Saved-state wake acceptance: first task, idle save, server restart while saved, wake, second task.

One real Deck server, one fresh private VM, a scripted model, no service and no credential. The model
is a dedicated adapter. It plays exactly two shell scripts and nothing else. The host does not trust
the model text. It reads the guest through QGA and the executor journal and checks:

- the first task leaves a disk marker, a memory-only marker, the boot id and one effect line;
- the idle save leaves a saved-state artifact and no VM process;
- a server restart while saved, and again while a second task is held, leaves the artifact unchanged
  and starts no model call, capture or wake;
- the second task wakes the saved computer (same boot id, same memory marker, a later uptime, a new
  QEMU process, the state file consumed) and appends exactly one effect line;
- task ids, executor identity, accounting limits and holds stay as they were.

It reuses the Stack, Manifest and cleanup registry of setup_e2e.py. It is a functional check, not a
benchmark. Raw evidence stays in --out, outside Git. The manifest holds identifiers, counts and
statuses.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import setup_e2e as e
import setup_synthetic_login as sl
import vmproto as v

ONE_MARK, TWO_MARK = "saved-wake task one", "saved-wake task two"
TASK_ONE = f"{ONE_MARK}: leave the marker files, then report"
TASK_TWO = f"{TWO_MARK}: read the marker files and add one effect line"
DONE_ONE, DONE_TWO = "saved-wake task one done", "saved-wake task two done"
MEM, BOOT = "/dev/shm/saved-wake-mem", "/proc/sys/kernel/random/boot_id"
PASS, FAIL, NOT_TESTED = e.PASS, e.FAIL, e.NOT_TESTED
OK_STATES = {"queued", "running", "waiting", "done"}


# ---------- the scripted model: two shell scripts ----------

def script_one(nonce, mem=MEM, boot=BOOT):
    """Disk marker, memory-only marker, boot id, uptime and the first effect line."""
    return (f'W="$DECK_WORKSPACE"; cat {boot} > "$W/boot_id.first"; printf "%s\\n" {nonce} > "$W/marker.txt"; printf "%s\\n" {nonce} > {mem}; '
            f'cut -d" " -f1 /proc/uptime > "$W/uptime.first"; echo "first-task-effect {nonce}" >> "$W/effects.log"; '
            f'echo "FIRST-OK WS=$W boot=$(cat {boot})"')


def script_two(nonce, mem=MEM, boot=BOOT):
    """Read the state of task one. Append the second effect line only when the disk marker is intact."""
    return (f'W="$DECK_WORKSPACE"; M=0; [ "$(cat "$W/marker.txt" 2>/dev/null)" = {nonce} ] && M=1; '
            f'R=0; [ "$(cat {mem} 2>/dev/null)" = {nonce} ] && R=1; B=0; [ "$(cat {boot})" = "$(cat "$W/boot_id.first" 2>/dev/null)" ] && B=1; '
            f'[ $M = 1 ] && echo "second-task-effect {nonce}" >> "$W/effects.log"; '
            f'echo "SECOND-OK marker_ok=$M mem_ok=$R boot_same=$B uptime=$(cut -d" " -f1 /proc/uptime) first_uptime=$(cat "$W/uptime.first" 2>/dev/null)"')


def _content(m):
    c = m.get("content")
    return c if isinstance(c, str) else json.dumps(c)


def _tool(name, args):
    return {"kind": "tool", "name": name, "args": args}


def _text(t):
    return {"kind": "text", "text": t}


def stdout_of(out):
    try:
        return str(json.loads(out).get("stdout", ""))
    except (ValueError, AttributeError):
        return ""


def decide(history, nonce):
    """The next model turn, from the last message alone. It plays two tasks and refuses anything else."""
    last = history[-1]
    if last["role"] == "user":
        t = _content(last)
        if TWO_MARK in t:
            return _tool("shell", {"script": script_two(nonce)})
        if ONE_MARK in t:
            return _tool("shell", {"script": script_one(nonce)})
        return _text("blocked: no scripted step for this message")
    out = stdout_of(_content(last))
    if "FIRST-OK" in out:
        return _text(DONE_ONE)
    if "SECOND-OK" in out:
        return _text(f"{DONE_TWO}: {out.strip().splitlines()[-1]}")
    return _text("blocked: the shell reply did not confirm the step")


class Model:
    """An OpenAI-style chat server that plays `decide`. It keeps a ledger of the tool calls it made."""

    def __init__(self, nonce, port=0, request_log=None):
        self.nonce, self.calls, self.ledger, self.texts, self.outputs = nonce, 0, [], [], []
        self.workspace = None
        self._lock = threading.Lock()
        m = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                history = json.loads(body)["messages"]
                turn = decide(history, m.nonce)
                with m._lock:
                    if request_log:
                        with open(request_log, "ab") as f:
                            f.write(body + b"\n")
                    if history[-1]["role"] != "user":
                        out = stdout_of(_content(history[-1]))
                        m.outputs.append(out)
                        if w := re.search(r"WS=(\S+)", out):
                            m.workspace = w[1]
                    m.calls += 1
                    n = m.calls
                    (m.ledger.append({"seq": n, "name": turn["name"], "args": turn["args"]}) if turn["kind"] == "tool" else m.texts.append(turn["text"]))
                delta = ({"tool_calls": [{"index": 0, "id": f"k{n}", "function": {"name": turn["name"], "arguments": json.dumps(turn["args"])}}]}, "tool_calls") if turn["kind"] == "tool" else ({"content": turn["text"]}, "stop")
                chunk = {"choices": [{"delta": delta[0], "finish_reason": delta[1]}]}
                data = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(sl.USAGE)}\n\ndata: [DONE]\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_port

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------- pure evidence helpers ----------

def parse_guest(text):
    """The guest facts script prints key=value lines, then the effect lines after `effects_begin`."""
    head, _, tail = text.partition("effects_begin\n")
    facts = dict(l.split("=", 1) for l in head.splitlines() if "=" in l)
    facts["effects"] = [l for l in tail.splitlines() if l.strip()]
    return facts


def effect_problems(facts, nonce, want):
    """Why the effect log is not exactly `want` (a list of 'first'/'second'). Empty when it is."""
    names = {"first": f"first-task-effect {nonce}", "second": f"second-task-effect {nonce}"}
    expected = [names[w] for w in want]
    return [] if facts.get("effects") == expected else [f"effect log {facts.get('effects')} expected {expected}"]


def quiet_view(status, calls):
    """What must not change while the computer is saved and held. `calls` is the model's own request count.
    The counters live in the server process: they restart at zero, so compare them only inside one server run."""
    s = status
    return {"model_calls": s["counters"]["model_calls"], "model_requests": calls, "wakes": s["counters"]["wakes"], "saves": s["counters"]["saves"],
            "connects": s["counters"]["connects"], "captures": s["screen"]["captures"], "tasks": [(t["id"], t["state"]) for t in s["tasks"]], "computer": s["computer"]}


def durable(view, computer=True):
    """The part of a view that must survive a server restart."""
    keys = ("model_requests", "tasks") + (("computer",) if computer else ())
    return {k: view[k] for k in keys}


def diff(a, b):
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def limits_view(status):
    """The accounting limits and holds. These stay equal across wake and restart."""
    acc = status.get("accounting") or {}
    return {k: acc.get(k) for k in ("currency", "exponent", "period", "policy_version", "ceiling", "allowance", "version")}


# ---------- the real collaborators ----------

class World:
    """What the flow may read from the real stack: VM files, the executor journal, the guest through QGA."""

    def __init__(self, stack, sid, model):
        self.stack, self.sid, self.model = stack, sid, model
        self.dir = stack.rt / sid
        self.p = v.Paths(self.dir)

    def vm(self):
        vm = v.read_vm(self.p)
        live = bool(vm) and v.proc_ok(self.p, vm["pid"])
        if live:
            self.stack.owned.update(e.capture_processes([vm["pid"]]))
        meta = self.p.statemeta.read_bytes() if self.p.statemeta.exists() else None
        return {"running": live, "pid": vm["pid"] if live else None, "state_saved": self.p.statefile.exists(),
                "state_bytes": self.p.statefile.stat().st_size if self.p.statefile.exists() else 0,
                "state_meta": json.loads(meta) if meta else None, "state_meta_sha256": hashlib.sha256(meta).hexdigest() if meta else None}

    def identity(self):
        info = json.loads((self.dir / "executor.json").read_text())
        return {"bot_id": info.get("bot_id"), "guest_id": info.get("guest_id")}

    def journal(self):
        """Read-only view of the executor journal in deck.db."""
        con = sqlite3.connect(f"file:{self.stack.out / 'data' / 'deck.db'}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("SELECT epoch, guest_id FROM executor_bots WHERE bot_id=?", (self.sid,)).fetchone()
            cmds = con.execute("SELECT task_id, state, dispatch_epoch FROM executor_commands WHERE bot_id=? ORDER BY rowid", (self.sid,)).fetchall()
        finally:
            con.close()
        return {"epoch": row[0] if row else None, "guest_id": row[1] if row else None, "commands": [list(c) for c in cmds]}

    def guest(self):
        """Read the guest through QGA as root. Fails when no VM runs: a saved computer has no guest to read."""
        ws = self.model.workspace
        if not ws or not re.fullmatch(r"[\w./-]+", ws):
            raise RuntimeError("the workspace path was not reported by the first task")
        script = (f'W={ws}; echo "marker=$(cat $W/marker.txt 2>/dev/null)"; echo "mem=$(cat {MEM} 2>/dev/null)"; echo "boot=$(cat {BOOT})"; '
                  f'echo "boot_first=$(cat $W/boot_id.first 2>/dev/null)"; echo "uptime=$(cut -d" " -f1 /proc/uptime)"; echo "first_uptime=$(cat $W/uptime.first 2>/dev/null)"; '
                  'echo effects_begin; cat $W/effects.log 2>/dev/null')
        code, out, _ = v.Qga(self.p.qga).sh(script)
        if code != 0:
            raise RuntimeError(f"guest read failed: exit {code}")
        return parse_guest(out)

    def restart(self):
        self.stack.stop("server")
        self.stack.start_server()
        c = self.stack.client
        c.until("the bot after a restart", lambda: c.raw("GET", f"/api/bots/{self.sid}")[0] == 200, 60)


def status_of(c, sid):
    return c.json("GET", f"/api/bots/{sid}")


def task_of(status, tid):
    return next((t for t in status["tasks"] if t["id"] == tid), None)


def watch_states(m, status):
    """An unknown outcome stops the flow. Nothing is retried."""
    bad = [(t["id"], t["state"]) for t in status["tasks"] if t["state"] not in OK_STATES]
    unresolved = (status.get("exposure") or {}).get("unresolved", 0)
    if bad or unresolved:
        m.gate("no_unknown_outcome", FAIL, f"task states {bad}, unresolved exposure {unresolved}: nothing was retried")
        raise e.Stop("unknown outcome")


def until_task(c, m, sid, tid, state, timeout, what):
    def cond():
        st = status_of(c, sid)
        watch_states(m, st)
        t = task_of(st, tid)
        return st if t and t["state"] == state and not st["working"] else None
    try:
        return c.until(what, cond, timeout)
    except SystemExit as x:
        m.gate(what.replace(" ", "_"), FAIL, str(x)[:200])
        raise e.Stop(what) from None


def settle_quiet(c, m, world, sid, a, name, baseline):
    """Wait, then check that nothing moved: no model call, capture, wake, save, connect or task change."""
    time.sleep(a.settle)
    now = quiet_view(status_of(c, sid), world.model.calls)
    e.need(m, name, not diff(baseline, now), f"changed: {diff(baseline, now)}" if diff(baseline, now) else f"unchanged: {baseline}")
    return now


# ---------- the flow ----------

REQUIRED = ["base_frozen_and_verified", "setup_ready_through_api", "first_task_done", "first_task_guest_state", "first_task_one_effect",
            "idle_save", "saved_state_artifact", "pause_held_while_saved", "restart_while_saved_keeps_artifact", "quiet_while_saved_after_restart",
            "second_task_held_not_dispatched", "held_second_task_starts_nothing", "held_restart_keeps_queued_task", "quiet_while_held_after_restart", "release_dispatches_second_task",
            "second_task_done", "wake_restored_not_cold_boot", "state_artifact_consumed", "guest_state_after_wake", "second_task_exactly_one_effect",
            "task_ids_distinct", "executor_identity_unchanged", "executor_epoch_advanced", "limits_and_holds_preserved", "model_ledger_two_shell_calls",
            "restart_after_second_task_no_duplicate", "second_idle_save", "quiet_after_second_save", "restart_while_saved_again_no_duplicate",
            "no_unknown_outcome", "base_unchanged", "no_owned_process_left"]


def flow(stack, m, a, world, sid, nonce):
    c, model = stack.client, world.model
    ev = m.data["evidence"]
    row = c.until("setup ready", lambda: (r := c.setup(sid)) and r["runtime_ready"] and r, a.ready_timeout)
    e.need(m, "setup_ready_through_api", row["phase"] == "Ready", f"phase {row['phase']}")
    first = f"setup-{sid}"
    st = until_task(c, m, sid, first, "done", a.task_timeout, "first task done")
    m.gate("first_task_done", PASS, f"task {first} done")
    g1, vm1, id1, j1 = world.guest(), world.vm(), world.identity(), world.journal()
    ok1 = g1.get("marker") == nonce and g1.get("mem") == nonce and g1.get("boot") == g1.get("boot_first") != "" and bool(model.workspace)
    e.need(m, "first_task_guest_state", ok1 and vm1["running"], f"disk marker {g1.get('marker') == nonce}, memory marker {g1.get('mem') == nonce}, boot id recorded {bool(g1.get('boot_first'))}")
    e.need(m, "first_task_one_effect", not effect_problems(g1, nonce, ["first"]), "one first-task effect line in the guest")
    ev["first"] = {"task_id": first, "guest": {k: g1[k] for k in ("boot", "uptime")}, "qemu_pid": vm1["pid"], "identity": id1, "journal": j1}
    limits0 = limits_view(st)
    # Idle save.
    st = c.until("idle save", lambda: (x := status_of(c, sid)) and x["computer"] == "saved" and x, a.idle_secs + 90, every=2)
    vm2 = world.vm()
    art = vm2["state_saved"] and vm2["state_bytes"] > 0 and not vm2["running"] and bool(vm2["state_meta"])
    e.need(m, "idle_save", st["counters"]["saves"] >= 1, f"computer {st['computer']}, saves {st['counters']['saves']}")
    e.need(m, "saved_state_artifact", art, f"state file {vm2['state_bytes']} bytes, codec {(vm2['state_meta'] or {}).get('codec')}, VM process running {vm2['running']}")
    ev["saved"] = {"state_bytes": vm2["state_bytes"], "state_meta_sha256": vm2["state_meta_sha256"], "codec": vm2["state_meta"].get("codec"), "saves": st["counters"]["saves"]}
    epoch_saved = world.journal()["epoch"]
    # An explicit pause, then a restart while saved.
    code, _ = c.raw("POST", f"/api/bots/{sid}/automation", {"paused": True})
    held = status_of(c, sid)
    e.need(m, "pause_held_while_saved", code == 204 and held["automation_paused"] and held["computer"] == "saved" and any(h.get("kind") == "automation_pause" for h in held["holds"]), f"status {code}, computer {held['computer']}, holds {held['holds']}")
    quiet0 = quiet_view(held, model.calls)
    world.restart()
    after = status_of(c, sid)
    survived = not diff(durable(quiet0), durable(quiet_view(after, model.calls)))
    vm3 = world.vm()
    same = vm3["state_meta_sha256"] == vm2["state_meta_sha256"] and vm3["state_bytes"] == vm2["state_bytes"] and vm3["state_saved"] and not vm3["running"]
    e.need(m, "restart_while_saved_keeps_artifact", same and survived and after["computer"] == "saved" and after["automation_paused"] and world.identity() == id1, f"computer {after['computer']}, paused {after['automation_paused']}, artifact unchanged {same}, tasks and model requests unchanged {survived}")
    settle_quiet(c, m, world, sid, a, "quiet_while_saved_after_restart", quiet_view(after, model.calls))
    # The second task, held.
    code, body = c.raw("POST", f"/api/bots/{sid}/messages", {"text": TASK_TWO, "mode": "queue"})
    sub = json.loads(body) if code == 202 else {}
    second = sub.get("task_id")
    st = status_of(c, sid)
    t2 = task_of(st, second) if second else None
    e.need(m, "second_task_held_not_dispatched", code == 202 and bool(second) and sub.get("held") == "automation paused" and bool(t2) and t2["state"] == "queued", f"status {code}, held {sub.get('held')}, task {t2 and t2['state']}")
    quiet2 = settle_quiet(c, m, world, sid, a, "held_second_task_starts_nothing", quiet_view(st, model.calls))
    world.restart()
    st = status_of(c, sid)
    ids = [t["id"] for t in st["tasks"]]
    vm4 = world.vm()
    e.need(m, "held_restart_keeps_queued_task", ids.count(second) == 1 and (task_of(st, second) or {}).get("state") == "queued" and st["computer"] == "saved" and not vm4["running"] and vm4["state_meta_sha256"] == vm2["state_meta_sha256"] and not diff(durable(quiet2), durable(quiet_view(st, model.calls))),
           f"{ids.count(second)} task(s) named {second}, state {(task_of(st, second) or {}).get('state')}, computer {st['computer']}")
    settle_quiet(c, m, world, sid, a, "quiet_while_held_after_restart", quiet_view(st, model.calls))
    # Release: the second task wakes the computer.
    wakes0, calls0 = st["counters"]["wakes"], model.calls
    code, _ = c.raw("POST", f"/api/bots/{sid}/automation", {"paused": False})
    e.need(m, "release_dispatches_second_task", code == 204, f"status {code}")
    st = until_task(c, m, sid, second, "done", a.task_timeout, "second task done")
    m.gate("second_task_done", PASS, f"task {second} done")
    vm5, g2, id2, j2 = world.vm(), world.guest(), world.identity(), world.journal()
    cold = g2.get("boot") != g1["boot"]
    restored = (vm5["running"] and vm5["pid"] not in (None, vm1["pid"]) and not cold and g2.get("mem") == nonce and float(g2["uptime"]) > float(g1["uptime"])
                and st["counters"]["wakes"] == wakes0 + 1)
    e.need(m, "wake_restored_not_cold_boot", restored, f"boot id same {not cold}, memory marker {g2.get('mem') == nonce}, uptime {g1['uptime']} -> {g2['uptime']}, new QEMU pid {vm5['pid'] != vm1['pid']}, wakes {wakes0} -> {st['counters']['wakes']}")
    e.need(m, "state_artifact_consumed", not vm5["state_saved"] and vm5["state_meta"] is None, "the saved state file and its metadata were consumed by the wake")
    e.need(m, "guest_state_after_wake", g2.get("marker") == nonce and g2.get("boot_first") == g1["boot_first"], "the disk marker and the first boot id file are unchanged")
    e.need(m, "second_task_exactly_one_effect", not effect_problems(g2, nonce, ["first", "second"]), "the guest log holds the first effect once and the second effect once")
    ev["second"] = {"task_id": second, "guest": {k: g2[k] for k in ("boot", "uptime", "first_uptime")}, "qemu_pid": vm5["pid"], "model_output": model.outputs[-1].strip(), "journal": j2}
    tasks = [t["id"] for t in st["tasks"]]
    e.need(m, "task_ids_distinct", first != second and tasks.count(first) == 1 and tasks.count(second) == 1 and task_of(st, first)["state"] == "done", f"tasks {tasks}")
    e.need(m, "executor_identity_unchanged", id2 == id1 and j2["guest_id"] == j1["guest_id"], f"bot {id2['bot_id'] == id1['bot_id']}, guest {id2['guest_id'] == id1['guest_id']}")
    e.need(m, "executor_epoch_advanced", j1["epoch"] <= epoch_saved < j2["epoch"], f"epoch at first task {j1['epoch']}, at save {epoch_saved}, after wake {j2['epoch']}")
    now_limits = limits_view(st)
    e.need(m, "limits_and_holds_preserved", now_limits == limits0 and not st["holds"] and not st["automation_paused"] and (st.get("exposure") or {}).get("unresolved", 0) == 0, f"limits equal {now_limits == limits0}, holds {st['holds']}, unresolved {(st.get('exposure') or {}).get('unresolved')}")
    names = [x["name"] for x in model.ledger]
    e.need(m, "model_ledger_two_shell_calls", names == ["shell", "shell"] and model.calls == calls0 + 2, f"tool calls {names}, model requests {model.calls} (before release {calls0})")
    m.gate("no_unknown_outcome", PASS, "no task in an unknown state, no unresolved exposure")
    # Repeated restart while awake.
    before = quiet_view(st, model.calls)
    world.restart()
    st = status_of(c, sid)
    after = quiet_view(st, model.calls)
    still = world.vm()["running"]
    g3 = world.guest() if still else None
    same = not diff(durable(before, computer=False), durable(after, computer=False))
    one = g3 is None or not effect_problems(g3, nonce, ["first", "second"])
    e.need(m, "restart_after_second_task_no_duplicate", same and one, f"counters unchanged {same}; guest effect log {'read: two lines' if g3 else 'not read: computer already saved'}")
    # Second idle save, then restart while saved again.
    st = c.until("second idle save", lambda: (x := status_of(c, sid)) and x["computer"] == "saved" and x, a.idle_secs + 90, every=2)
    vm6 = world.vm()
    e.need(m, "second_idle_save", st["counters"]["saves"] >= 1 and vm6["state_saved"] and not vm6["running"], f"saves {st['counters']['saves']}, VM process running {vm6['running']}")
    q = quiet_view(st, model.calls)
    q = settle_quiet(c, m, world, sid, a, "quiet_after_second_save", q)
    world.restart()
    st = status_of(c, sid)
    after = quiet_view(st, model.calls)
    e.need(m, "restart_while_saved_again_no_duplicate", st["computer"] == "saved" and not world.vm()["running"] and not diff(durable(q), durable(after)),
           f"computer {st['computer']}, changed: {diff(durable(q), durable(after))}")
    ev["final"] = {"counters": st["counters"], "tasks": [(t["id"], t["state"]) for t in st["tasks"]]}
    m.gate("raster_secrecy", NOT_TESTED, "optional: no screenshot or desktop capture evidence in this mode")


# ---------- entry ----------

def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="frozen base .qcow2 with its sidecar")
    ap.add_argument("--deck", required=True)
    ap.add_argument("--out", required=True, help="a short output directory that does not exist yet")
    ap.add_argument("--fixture-port", type=int, default=28951)
    ap.add_argument("--server-port", type=int, default=28952)
    ap.add_argument("--model-port", type=int, default=28953)
    ap.add_argument("--idle-secs", type=int, default=60)
    ap.add_argument("--disk-gib", type=int, default=16)
    ap.add_argument("--ready-timeout", type=int, default=300)
    ap.add_argument("--task-timeout", type=int, default=240)
    ap.add_argument("--settle", type=int, default=8)
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    ap.add_argument("--keep-disk", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    a.diagnostic_precreate_bot_dir = False
    lock = e.take_lock()
    why = e.pressure(Path("/proc/meminfo").read_text(), Path("/proc/loadavg").read_text(), a.min_avail_mib, a.max_load)
    if why:
        print(f"STOPPED: host pressure: {why}. Nothing was started.", file=sys.stderr)
        return 3
    out = Path(a.out)
    if not e.socket_path_ok(out):
        print(f"STOPPED: --out is too long for QEMU unix sockets: {out}", file=sys.stderr)
        return 2
    out.mkdir(mode=0o700, parents=True)
    (out / "private").mkdir(mode=0o700)
    m = e.Manifest("saved-wake")
    nonce = "SW" + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]
    stack = e.Stack(a)
    base = e.base_identity(a.base)
    head = subprocess.run(["git", "-C", str(e.HERE), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    m.data["identity"] = {"head": head, "base": {k: base[k] for k in ("id", "virtual_size", "mode")}, "deck_sha256": v.sha256_file(Path(a.deck)),
                          "fidelity": "real server, real VM, scripted model, no service, no credential", "nonce_is_a_marker_not_a_secret": True}
    m.data["host"] = {"loadavg_start": Path("/proc/loadavg").read_text().split()[:3], "mem_available_mib_start": int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) // 1024, "max_load_allowed": a.max_load}
    m.gate("base_frozen_and_verified", PASS if base["mode"] == "0o444" and e.verify_base(a.base) else FAIL, f"mode {base['mode']}")
    try:
        stack.write_config()
        stack.model = Model(nonce, port=a.model_port, request_log=out / "private" / "model-requests.jsonl")
        stack.model.start()
        stack.start_server()
        sid = e.create_and_approve(stack.client, "Leave a marker, save, wake and add one effect", task=TASK_ONE)
        m.data["evidence"]["setup_id"] = sid
        flow(stack, m, a, World(stack, sid, stack.model), sid, nonce)
    except e.Stop as x:
        m.gate("flow_stopped", FAIL, str(x)[:200])
    except BaseException as x:  # keep the evidence, then report
        m.gate("run_completed", FAIL, f"{type(x).__name__}: {m.scrub(str(x))[:300]}")
    finally:
        sizes = {d.name: v.allocated_bytes(d / "disk.qcow2") for d in stack.vm_dirs() if (d / "disk.qcow2").exists()}
        left = stack.cleanup()
        m.data["evidence"]["overlay_allocated_bytes"] = sizes
        if not a.keep_disk:
            for d in stack.vm_dirs():
                for f in (d / "disk.qcow2", d / "state" / "vm.state"):
                    f.unlink(missing_ok=True)
        m.data["host"]["loadavg_end"] = Path("/proc/loadavg").read_text().split()[:3]
        m.gate("base_unchanged", PASS if e.verify_base(a.base) else FAIL)
        m.data["evidence"]["cleanup"] = {"registered": {str(pid): birth for pid, birth in stack.owned.items()}, "survivors": left, "method": "registered subprocess and bound VM identities plus descendants"}
        m.gate("no_owned_process_left", PASS if not left else FAIL, f"{len(left)} registered identities still running; survivors: {left}")
        m.require(REQUIRED, ["raster_secrecy"])
        m.write(out / "manifest.json")
        print(f"manifest: {out / 'manifest.json'}")
    lock.close()
    return 0 if m.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
