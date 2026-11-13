#!/usr/bin/env python3
"""Awake-idle attribution on the real Stage 3 path: server, scripted model and a connected guest executor.

Each boot runs stage3.main unchanged. A hook replaces only its short awake-idle window with two
consecutive timed windows. Counters are read at the window edges, never continuously:
host QEMU threads (CPU and context switches), helpers, guest /proc counters through the guest agent.
Context switches are proxies, not wakeup counts. A thread or interrupt that leads is a candidate,
not a proven cause. Edge reads cost guest and host work: the cost is recorded, and the edge reads
fall outside the measured windows. All data is synthetic. Bare is the only browser. Raw output stays outside Git.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from unittest import mock

import cyclebench as cb
import memorybench as mb
import stage3
import vmproto as v

HERE = Path(__file__).resolve().parent
HZ = os.sysconf("SC_CLK_TCK")
WINDOW_TOLERANCE_S = 5.0
BOOT_TAIL_S = 180  # estimate: executor install, cold boot, task, save wait, saved window, second task
ROLES = ("bare", "sway", "executor")

GUEST_SCRIPT = r'''
import json, os
def rd(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None
def parse(text):
    if not text or ")" not in text:
        return None
    return text[text.index("(") + 1:text.rindex(")")], text[text.rindex(")") + 2:].split()
def sw(pid):
    out = {}
    for line in (rd("/proc/%s/status" % pid) or "").splitlines():
        k, _, rest = line.partition(":")
        if k in ("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"):
            out[k] = int(rest.split()[0])
    return out.get("voluntary_ctxt_switches"), out.get("nonvoluntary_ctxt_switches")
out = {"hz": os.sysconf("SC_CLK_TCK")}
up = rd("/proc/uptime")
out["uptime"] = float(up.split()[0]) if up else None
bid = rd("/proc/sys/kernel/random/boot_id")
out["boot_id"] = bid.strip() if bid else None
cs = rd("/sys/devices/system/clocksource/clocksource0/current_clocksource")
out["clocksource"] = cs.strip() if cs else None
st = {"cpus": {}}
for line in (rd("/proc/stat") or "").splitlines():
    w = line.split()
    if not w:
        continue
    if w[0] == "cpu":
        st["cpu"] = [int(x) for x in w[1:]]
    elif w[0].startswith("cpu"):
        st["cpus"][w[0]] = [int(x) for x in w[1:]]
    elif w[0] in ("intr", "ctxt", "softirq"):
        st[w[0]] = int(w[1])
out["stat"] = st
sq = None
text = rd("/proc/softirqs")
if text:
    sq = {}
    for line in text.splitlines()[1:]:
        k, _, rest = line.partition(":")
        sq[k.strip()] = sum(int(x) for x in rest.split() if x.isdigit())
out["softirqs"] = sq
text = rd("/proc/interrupts")
if text is None:
    out["interrupts"] = None
else:
    lines = text.splitlines()
    ncpu = len(lines[0].split())
    irq = {}
    for line in lines[1:]:
        w = line.split()
        if not w:
            continue
        counts = []
        for x in w[1:1 + ncpu]:
            if not x.isdigit():
                break
            counts.append(int(x))
        irq[w[0] + " " + " ".join(w[1 + len(counts):])] = counts
    out["interrupts"] = irq
blk = rd("/sys/block/vda/stat")
out["block"] = [int(x) for x in blk.split()] if blk else None
procs = {}
for d in os.listdir("/proc"):
    if d.isdigit() and int(d) != os.getpid():
        r = parse(rd("/proc/%s/stat" % d))
        if r:
            f = r[1]
            procs[int(d)] = {"comm": r[0], "ppid": int(f[1]), "start": int(f[19]), "ticks": int(f[11]) + int(f[12])}
roles = {}
for pid, x in procs.items():
    if x["comm"] == "bare-mcp":
        roles[pid] = "bare"
    elif x["comm"].startswith("sway"):
        roles[pid] = "sway"
    elif x["comm"].startswith("python"):
        cmd = open("/proc/%d/cmdline" % pid, "rb").read() if os.path.exists("/proc/%d/cmdline" % pid) else b""
        roles[pid] = "executor" if b"/usr/local/bin/deck-executor" in cmd else "python_other"
grown = True
while grown:
    grown = False
    for pid, x in procs.items():
        if pid not in roles and roles.get(x["ppid"]) in ("bare", "bare_child"):
            roles[pid] = "bare_child"
            grown = True
rows = []
for pid, role in sorted(roles.items()):
    x = procs[pid]
    vol, nonvol = sw(pid)
    port = None
    if role == "executor":
        try:
            port = any("vport" in os.readlink("/proc/%d/fd/%s" % (pid, n)) for n in os.listdir("/proc/%d/fd" % pid))
        except OSError:
            port = None
    rows.append({"pid": pid, "role": role, "comm": x["comm"], "start": x["start"], "ticks": x["ticks"], "vol": vol, "nonvol": nonvol, "executor_port": port})
out["procs"] = rows
me = parse(rd("/proc/self/stat"))
out["self_ticks"] = int(me[1][11]) + int(me[1][12]) if me else None
print(json.dumps(out))
'''


# ---------- host edge and delta ----------

def parse_stat(text):
    try:
        return {"comm": text[text.index("(") + 1:text.rindex(")")], "ppid": int(text[text.rindex(")") + 2:].split()[1]),
                "start": int(text[text.rindex(")") + 2:].split()[19]), "utime": int(text[text.rindex(")") + 2:].split()[11]),
                "stime": int(text[text.rindex(")") + 2:].split()[12])}
    except (ValueError, IndexError):
        return None


def switch_counters(text):
    got = {}
    for line in (text or "").splitlines():
        k, _, rest = line.partition(":")
        if k in ("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"):
            got[k] = int(rest.split()[0])
    return got.get("voluntary_ctxt_switches"), got.get("nonvoluntary_ctxt_switches")


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def host_edge(pid, server_pid, observer):
    """Counters of the QEMU threads, its helpers and the harness. Unreadable counters are None, not zero."""
    main = parse_stat(read(f"/proc/{pid}/stat") or "")
    if main is None:
        raise RuntimeError(f"QEMU process {pid} is unreadable")
    threads = []
    try:
        tids = sorted(int(t.name) for t in Path(f"/proc/{pid}/task").iterdir())
    except OSError as e:
        raise RuntimeError(f"QEMU threads are unreadable: {e}")
    for tid in tids:
        s = parse_stat(read(f"/proc/{pid}/task/{tid}/stat") or "")
        if s is None:  # the thread ended during the read
            continue
        vol, nonvol = switch_counters(read(f"/proc/{pid}/task/{tid}/status"))
        threads.append({"tid": tid, "comm": s["comm"], "start": s["start"], "utime": s["utime"], "stime": s["stime"], "vol": vol, "nonvol": nonvol})
    helpers = []
    for q in v.tree_pids(pid)[1:]:
        s = parse_stat(read(f"/proc/{q}/stat") or "")
        if s:
            helpers.append({"pid": q, "comm": s["comm"], "start": s["start"], "utime": s["utime"], "stime": s["stime"]})
    io = None
    if text := read(f"/proc/{pid}/io"):
        io = {k.strip(): int(x) for k, _, x in (line.partition(":") for line in text.splitlines()) if x.strip().isdigit()}
    server = None
    if server_pid:
        s = parse_stat(read(f"/proc/{server_pid}/stat") or "")
        server = {"pid": server_pid, "ticks": s["utime"] + s["stime"]} if s else None
    return {"mono": time.monotonic(), "wall_ns": time.time_ns(), "qemu": {"pid": pid, "start": main["start"], "comm": main["comm"]},
            "threads": threads, "helpers": helpers, "io": io, "server": server, "observer_cpu_s": observer(), "harness_cpu_s": time.process_time(),
            "psi": cb.psi(), "swap": cb.vmstat_swap(), "mem_available_kib": cb.mem_available_kib(), "vm": {"swap_kib": cb.vm_swap_kib(pid)}}


def _diff(a, b):
    """b minus a. None when either is missing. Raises ValueError when the counter went backwards."""
    if a is None or b is None:
        return None
    if b < a:
        raise ValueError
    return b - a


def host_delta(a, b, hz=HZ):
    wall = max(b["mono"] - a["mono"], 1e-9)
    if (a["qemu"]["pid"], a["qemu"]["start"], a["qemu"]["comm"]) != (b["qemu"]["pid"], b["qemu"]["start"], b["qemu"]["comm"]):
        return {"valid": False, "reasons": ["qemu_identity_changed"], "wall_s": round(wall, 4), "threads": [], "rejected": [], "appeared": [], "vanished": [],
                "helpers_cpu_s": None, "qemu_cpu_s": None, "io": None, "observer_cpu_s": None}
    before = {t["tid"]: t for t in a["threads"]}
    after = {t["tid"]: t for t in b["threads"]}
    out, rejected = [], []
    for tid in sorted(before.keys() & after.keys()):
        x, y = before[tid], after[tid]
        if (x["start"], x["comm"]) != (y["start"], y["comm"]):
            rejected.append({"tid": tid, "reason": "tid_reused"})
            continue
        try:
            ticks = _diff(x["utime"], y["utime"]) + _diff(x["stime"], y["stime"])
            vol, nonvol = _diff(x["vol"], y["vol"]), _diff(x["nonvol"], y["nonvol"])
        except ValueError:
            rejected.append({"tid": tid, "reason": "counter_decreased"})
            continue
        cpu = ticks / hz
        out.append({"tid": tid, "comm": y["comm"], "cpu_s": round(cpu, 4), "cpu_percent": round(100 * cpu / wall, 4), "vol": vol, "nonvol": nonvol})
    helpers_cpu, hb = 0, {h["pid"]: h for h in b["helpers"]}
    for h in a["helpers"]:
        g = hb.get(h["pid"])
        if g and (g["start"], g["comm"]) == (h["start"], h["comm"]) and g["utime"] + g["stime"] >= h["utime"] + h["stime"]:
            helpers_cpu += (g["utime"] + g["stime"]) - (h["utime"] + h["stime"])
    io = None
    if a.get("io") and b.get("io"):
        try:
            io = {k: _diff(a["io"].get(k), b["io"].get(k)) for k in ("read_bytes", "write_bytes")}
        except ValueError:
            io = None
    obs = round(b["observer_cpu_s"] - a["observer_cpu_s"], 4) if a.get("observer_cpu_s") is not None and b.get("observer_cpu_s") is not None else None
    return {"valid": True, "reasons": [], "wall_s": round(wall, 4), "threads": out, "rejected": rejected,
            "appeared": sorted(after.keys() - before.keys()), "vanished": sorted(before.keys() - after.keys()),
            "helpers_cpu_s": round(helpers_cpu / hz, 4), "qemu_cpu_s": round(sum(t["cpu_s"] for t in out), 4), "io": io, "observer_cpu_s": obs}


# ---------- guest delta ----------

CPU_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal", "guest", "guest_nice")


def guest_delta(a, b):
    reasons = []
    d = {"valid": True, "reasons": reasons, "seconds": None, "cpu_jiffies": None, "cpus": {}, "intr": None, "ctxt": None, "softirq": None,
         "softirqs": None, "interrupts": None, "interrupts_unavailable": [], "block": None, "procs": [], "procs_rejected": [], "procs_exited": [], "procs_started": []}
    if a.get("boot_id") != b.get("boot_id") or a.get("boot_id") is None:
        reasons.append("guest_boot_changed")
        d["valid"] = False
        return d
    d["seconds"] = round(b["uptime"] - a["uptime"], 3) if a.get("uptime") is not None and b.get("uptime") is not None else None

    def vec(x, y, name):
        try:
            if x is None or y is None:
                return None
            return [_diff(p, q) for p, q in zip(x, y)]
        except ValueError:
            reasons.append(f"counter_decreased:{name}")
            return None

    sa, sb = a["stat"], b["stat"]
    if (cpu := vec(sa.get("cpu"), sb.get("cpu"), "cpu")) is not None:
        d["cpu_jiffies"] = dict(zip(CPU_FIELDS, cpu))
    for name in sa.get("cpus", {}):
        if name in sb.get("cpus", {}) and (c := vec(sa["cpus"][name], sb["cpus"][name], name)) is not None:
            d["cpus"][name] = dict(zip(CPU_FIELDS, c))
    for k in ("intr", "ctxt", "softirq"):
        try:
            d[k] = _diff(sa.get(k), sb.get(k))
        except ValueError:
            reasons.append(f"counter_decreased:{k}")
    if a.get("softirqs") is not None and b.get("softirqs") is not None:
        d["softirqs"] = {}
        for k in a["softirqs"].keys() & b["softirqs"].keys():
            try:
                d["softirqs"][k] = _diff(a["softirqs"][k], b["softirqs"][k])
            except ValueError:
                reasons.append(f"counter_decreased:softirq {k}")
    if a.get("interrupts") is not None and b.get("interrupts") is not None:
        d["interrupts"] = {}
        for k in sorted(a["interrupts"].keys() | b["interrupts"].keys()):
            x, y = a["interrupts"].get(k), b["interrupts"].get(k)
            if x is None or y is None or len(x) != len(y):
                d["interrupts_unavailable"].append(k)  # absent at an edge: not zero
                continue
            try:
                d["interrupts"][k] = sum(_diff(p, q) for p, q in zip(x, y))
            except ValueError:
                d["interrupts_unavailable"].append(k)
    if a.get("block") is not None and b.get("block") is not None:
        d["block"] = vec(a["block"], b["block"], "block")
    before = {p["pid"]: p for p in a.get("procs", [])}
    after = {p["pid"]: p for p in b.get("procs", [])}
    seconds = d["seconds"] or 1e-9
    for pid in sorted(before.keys() & after.keys()):
        x, y = before[pid], after[pid]
        if (x["start"], x["comm"], x["role"]) != (y["start"], y["comm"], y["role"]):
            d["procs_rejected"].append({"pid": pid, "role": y["role"], "reason": "pid_reused"})
            continue
        try:
            ticks, vol, nonvol = _diff(x["ticks"], y["ticks"]), _diff(x["vol"], y["vol"]), _diff(x["nonvol"], y["nonvol"])
        except ValueError:
            d["procs_rejected"].append({"pid": pid, "role": y["role"], "reason": "counter_decreased"})
            continue
        d["procs"].append({"pid": pid, "role": y["role"], "comm": y["comm"], "ticks": ticks, "vol": vol, "nonvol": nonvol,
                           "cpu_percent": round(100 * ticks / (b.get("hz") or HZ) / seconds, 4) if ticks is not None else None})
    d["procs_exited"] = sorted(before.keys() - after.keys())
    d["procs_started"] = sorted(after.keys() - before.keys())
    d["valid"] = not reasons
    return d


# ---------- window assertions ----------

def _role(guest, role):
    rows = [p for p in guest.get("procs", []) if p["role"] == role]
    return rows[0] if rows else None


def window_failures(a, b, window_s=60):
    """Reasons a window cannot be used. An empty list means every gate held at both edges."""
    if any(e is None or e.get(k) is None for e in (a, b) for k in ("host", "guest", "api")):
        return ["edge_incomplete"]
    out = []
    sa, sb = a["api"], b["api"]
    for s in (sa, sb):
        for ok, reason in ((s["computer"] == "awake", "not_awake"), (not s.get("working") and not s.get("waiting"), "owner_command_active"),
                           (s.get("queued", 0) == 0, "work_queued"), (s.get("unresolved", 0) == 0, "unresolved_work"),
                           (s.get("pending_events", 0) == 0, "events_pending")):
            if not ok and reason not in out:
                out.append(reason)
    if sb.get("idle_left_ms") is None or sb["idle_left_ms"] <= 0:
        out.append("idle_timer_expired")
    ca, cb_ = sa["counters"], sb["counters"]
    for key, reason in (("model_calls", "model_calls"), ("connects", "executor_reconnected"), ("wakes", "wake_or_save"), ("saves", "wake_or_save")):
        if ca[key] != cb_[key] and reason not in out:
            out.append(reason)
    if sa["screen"]["captures"] != sb["screen"]["captures"]:
        out.append("capture")
    if sa["screen"]["worker"] or sb["screen"]["worker"]:
        out.append("capture_worker_alive")
    for role in ROLES:
        x, y = _role(a["guest"], role), _role(b["guest"], role)
        if x is None or y is None:
            out.append(f"{role}_missing")
        elif (x["pid"], x["start"], x["comm"]) != (y["pid"], y["start"], y["comm"]):
            out.append(f"{role}_changed")
        elif role == "executor" and not (x["executor_port"] and y["executor_port"]):
            out.append("executor_not_connected")
    start = a.get("host_after") or a["host"]
    hd, gd = host_delta(start, b["host"]), guest_delta(a["guest"], b["guest"])
    out.extend(hd["reasons"] + gd["reasons"])
    if abs((b["host"]["mono"] - start["mono"]) - window_s) > WINDOW_TOLERANCE_S:
        out.append("window_length")
    return out


# ---------- host guard (D52) ----------

class Guard:
    """Memory-run guardrails over rolling windows of at least 60 s. Host-wide swap is context, not a stop."""
    def __init__(self):
        self.ref, self.prev_events, self.context_windows = None, None, []

    def feed(self, rec, events):
        host = None
        if self.ref is None:
            self.ref = rec
        elif rec["mono"] - self.ref["mono"] >= 60:
            host, self.ref = mb.window_metrics(self.ref, rec), rec
        if host and host["swapout_pages"] > cb.SWAP_OUT_LIMIT_PAGES:
            self.context_windows.append({"mono": rec["mono"], "reason": "host_swap_out", "host": host})
        reasons = mb.guardrails(host, self.prev_events, events, None)
        if events is not None:
            self.prev_events = events
        return reasons


def guard_record(ctx):
    rec = {"wall_ns": time.time_ns(), "mono": time.monotonic(), "boot": ctx.boot, "psi": cb.psi(), "swap": cb.vmstat_swap(), "mem_available_kib": cb.mem_available_kib(),
           "load": os.getloadavg(), "observer_cpu_s": time.thread_time(), "vm": {"swap_kib": 0}}
    p = ctx.current
    vm = v.read_vm(p) if p is not None and p.vm.exists() else None
    events = None
    if vm and v.proc_ok(p, vm["pid"]):
        rec["vm"] = {"pid": vm["pid"], "swap_kib": cb.vm_swap_kib(vm["pid"])}
        events = (mb.cg_memory(vm["pid"]) or {}).get("events")
        rec["cg_events"] = events
    return rec, events


def guard_loop(ctx, stop):
    try:
        while True:
            rec, events = guard_record(ctx)
            reasons = ctx.guard.feed(rec, events)
            if time.monotonic() > ctx.deadline:
                reasons.append("wall_budget")
            ctx.log("guard", **rec, reasons=reasons)
            if reasons:
                ctx.stop_reasons.extend(r for r in reasons if r not in ctx.stop_reasons)
                ctx.stop.set()
                p = ctx.current
                if p is not None:
                    v.kill_vm(p)  # the harness checks the process identity before it kills
                return
            if stop.wait(ctx.args.sample_s):
                return
    except BaseException as e:
        ctx.sampler_errors.append(repr(e))
        ctx.stop.set()


class Ctx(cb.Ctx):
    def __init__(self, root, args):
        super().__init__(root, args)
        self.boots, self.retention_errors, self.stop_reasons = [], [], []
        self.guard_errors = self.sampler_errors
        self.stop, self.guard, self.boot = threading.Event(), Guard(), 0

    def wait(self, secs):
        if self.stop.wait(secs):
            raise RuntimeError(f"stopped: {self.stop_reasons or self.sampler_errors}")


# ---------- dominance ----------

def _leaders(windows, kind):
    out = []
    for w in windows:
        if not w.get("valid"):
            continue
        if kind == "interrupt":
            counts = (w["guest"] or {}).get("interrupts")
        else:
            counts = {}
            for t in (w["host"] or {}).get("threads", []):
                counts[t["comm"]] = counts.get(t["comm"], 0) + (t["vol"] or 0) + (t["nonvol"] or 0)
        if counts:
            name, n = max(counts.items(), key=lambda kv: kv[1])
            if n > 0:
                out.append((w["boot"], name))
    return out


def repeated_dominant(windows, kind):
    """The leading source, only when it leads every valid window in at least two boots."""
    lead = _leaders(windows, kind)
    boots = sorted({b for b, _ in lead})
    names = {n for _, n in lead}
    return {"source": next(iter(names)) if len(boots) >= 2 and len(names) == 1 else None, "leaders": lead, "boots": boots}


# ---------- one boot ----------

def take_edge(ctx, p, api, server_pid):
    """host before, API state, guest counters, host after. The window runs from `host_after` to the next `host`."""
    pid = v.read_vm(p)["pid"]
    host = host_edge(pid, server_pid, ctx.observer_cpu)
    state = api.call("GET", "/api/bot")
    t = time.monotonic()
    code, out, err = v.Qga(p.qga).exec(["/usr/bin/python3", "-I", "-"], stdin=GUEST_SCRIPT, timeout=30)
    if code != 0:
        raise RuntimeError(f"guest counters failed: {err[:200]}")
    guest = json.loads(out)
    guest["qga_s"] = round(time.monotonic() - t, 4)
    after = host_edge(pid, server_pid, ctx.observer_cpu)
    keep = ("computer", "working", "waiting", "queued", "unresolved", "pending_events", "idle_left_ms", "counters", "screen")
    return {"mono": host["mono"], "wall_ns": host["wall_ns"], "api": {k: state.get(k) for k in keep}, "host": host, "host_after": after, "guest": guest}


def edge_cost(e):
    d = host_delta(e["host"], e["host_after"])
    return {"host_gap_s": d["wall_s"], "qga_s": e["guest"].get("qga_s"), "guest_self_ticks": e["guest"].get("self_ticks"), "qemu_cpu_s": d["qemu_cpu_s"],
            "observer_cpu_s": d["observer_cpu_s"]}


def make_window(boot, k, a, b, window_s):
    failures = window_failures(a, b, window_s)
    start = a["host_after"]
    hd, gd = host_delta(start, b["host"]), guest_delta(a["guest"], b["guest"])
    ctxm = mb.window_metrics(start, b["host"])
    sa = (start.get("server") or {}).get("ticks")
    sb = (b["host"].get("server") or {}).get("ticks")
    return {"boot": boot, "window": k, "valid": not failures, "failures": failures, "host": hd, "guest": gd,
            "host_context": ctxm, "host_flags": cb.contention(ctxm), "server_cpu_s": round((sb - sa) / HZ, 4) if sa is not None and sb is not None else None,
            "tick_resolution_percent_of_one_core": round(100 / (HZ * max(hd["wall_s"], 1e-9)), 4),
            "edge_cost": {"start": edge_cost(a), "end": edge_cost(b)},
            "label": "stage3 real server, scripted model, connected guest executor"}


def idle_hook(ctx, original):
    def window(label, secs, server_pid, p, api):
        if label != "awake, idle":
            return original(label, secs, server_pid, p, api)
        a = ctx.args
        ctx.wait(a.stabilize_s)
        edges = []
        for i in range(3):
            if i:
                ctx.wait(a.window_s)
            e = take_edge(ctx, p, api, server_pid)
            edges.append(e)
            ctx.log("edge", boot=ctx.boot, edge=i, **{k: e[k] for k in ("mono", "wall_ns", "api", "host", "host_after", "guest")})
        recs = [make_window(ctx.boot, k + 1, edges[k], edges[k + 1], a.window_s) for k in range(2)]
        for r in recs:
            ctx.log("window", **r)
        ctx.current_windows.extend(recs)
        s0, s1 = edges[0]["api"], edges[-1]["api"]
        return {"label": label, "seconds": round(edges[-1]["mono"] - edges[0]["mono"], 1), "windows": [{"window": r["window"], "valid": r["valid"], "failures": r["failures"]} for r in recs],
                "model_calls_delta": s1["counters"]["model_calls"] - s0["counters"]["model_calls"], "captures_delta": s1["screen"]["captures"] - s0["screen"]["captures"]}
    return window


def run_boot(ctx, n):
    a = ctx.args
    boot_dir = ctx.root / f"boot-{n}"
    ctx.boot, ctx.current, ctx.current_windows = n, v.Paths(boot_dir), []
    stage3.Model.calls = 0
    argv = ["stage3.py", "--base", a.base, "--deck", a.deck, "--dir", str(boot_dir), "--fixture-port", str(a.fixture_port + 3 * n), "--server-port", str(a.fixture_port + 3 * n + 1),
            "--model-port", str(a.fixture_port + 3 * n + 2), "--idle-secs", str(a.idle_secs)]
    owned, server_dirs = [], []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        owned.append(child)
        if kwargs.get("cwd") and Path(kwargs["cwd"]).joinpath("deck.toml").exists():
            server_dirs.append(Path(kwargs["cwd"]))
        ctx.log("process_start", boot=n, pid=child.pid)
        return child

    ctx.log("boot_begin", boot=n)
    error = None
    try:
        with mock.patch.object(subprocess, "Popen", popen), mock.patch.object(stage3, "window", idle_hook(ctx, stage3.window)), mock.patch("sys.argv", argv):
            stage3.main()
    except BaseException as e:
        error = e
        ctx.log("boot_error", boot=n, error=repr(e), traceback=traceback.format_exc())
    finally:
        for child in owned:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        try:
            boot_dir.mkdir(parents=True, exist_ok=True)
            result_path = boot_dir / "results.json"
            result = json.loads(result_path.read_text()) if result_path.exists() else {"ok": False, "error": "scenario produced no result"}
            for src in server_dirs[-1:]:
                if (src / "server.log").exists():
                    (boot_dir / "server.log").write_bytes((src / "server.log").read_bytes())
        except Exception as e:
            result = {"ok": False, "error": "no result"}
            ctx.retention_errors.append(repr(e))
            ctx.log("evidence_error", boot=n, error=repr(e))
    windows = ctx.current_windows
    connected = bool(windows) and all("executor_not_connected" not in w["failures"] and "executor_missing" not in w["failures"] for w in windows)
    complete = error is None and result.get("ok") is True and len(windows) == 2 and all(w["valid"] for w in windows)
    rec = {"boot": n, "complete": complete, "stage3_ok": result.get("ok"), "executor_connected": connected, "steps": result.get("steps"), "windows": windows,
           "stage3_windows": result.get("windows")}
    ctx.boots.append(rec)
    ctx.log("boot_end", boot=n, complete=complete, stage3_ok=result.get("ok"), executor_connected=connected)
    if error is not None:
        raise error
    if not complete:
        raise RuntimeError(f"boot {n} is incomplete: stage3_ok={result.get('ok')} windows={[(w['window'], w['failures']) for w in windows]}")


# ---------- run ----------

def cleanup_owned(ctx):
    mb.cleanup_owned(ctx)


def base_digest(a):
    return mb.base_digest(a)


def git_revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=HERE).strip()


def sha(path):
    try:
        return v.sha256_file(path)
    except OSError:
        return "unavailable"


def window_row(w):
    h, g = w["host"], w["guest"]
    threads = sorted(h.get("threads", []), key=lambda t: -(t["vol"] or 0) - (t["nonvol"] or 0))[:4]
    ints = sorted((g.get("interrupts") or {}).items(), key=lambda kv: -kv[1])[:4]
    secs = h["wall_s"]
    return {"boot": w["boot"], "window": w["window"], "valid": w["valid"], "failures": w["failures"], "seconds": secs, "qemu_cpu_percent": None if h["qemu_cpu_s"] is None else round(100 * h["qemu_cpu_s"] / secs, 3),
            "helpers_cpu_s": h["helpers_cpu_s"], "observer_cpu_s": h["observer_cpu_s"],
            "top_threads": [{"comm": t["comm"], "tid": t["tid"], "cpu_percent": t["cpu_percent"], "vol_per_s": None if t["vol"] is None else round(t["vol"] / secs, 2),
                             "nonvol_per_s": None if t["nonvol"] is None else round(t["nonvol"] / secs, 2)} for t in threads],
            "guest_intr_per_s": None if g.get("intr") is None else round(g["intr"] / secs, 2), "guest_ctxt_per_s": None if g.get("ctxt") is None else round(g["ctxt"] / secs, 2),
            "top_interrupts_per_s": [(k, round(n / secs, 2)) for k, n in ints], "guest_interrupts_unavailable": g.get("interrupts_unavailable"),
            "guest_procs": [{k: p[k] for k in ("role", "cpu_percent", "vol", "nonvol")} for p in g.get("procs", []) if p["role"] in ROLES],
            "host_flags": w["host_flags"], "host_context": w["host_context"]}


def write_summary(ctx, ok, error, base):
    wins = [w for b in ctx.boots for w in b["windows"]]
    (ctx.root / "summary.json").write_text(json.dumps({
        "ok": ok, "error": repr(error) if error else None, "stop_reasons": ctx.stop_reasons, "base": base,
        "path": "stage3 real server, scripted model, connected guest executor" if ctx.boots and all(b.get("executor_connected") for b in ctx.boots) else "executor connection not shown in every boot",
        "complete_boots": sum(1 for b in ctx.boots if b["complete"]), "boots_started": len(ctx.boots),
        "windows": len(wins), "valid_windows": sum(1 for w in wins if w["valid"]),
        "rows": [window_row(w) for w in wins],
        "dominant_interrupt": repeated_dominant(wins, "interrupt"), "dominant_thread": repeated_dominant(wins, "thread"),
        "host_paging_windows": ctx.guard.context_windows, "host_paging_contended": bool(ctx.guard.context_windows),
        "boot_steps": [{"boot": b["boot"], "complete": b["complete"], "stage3_ok": b.get("stage3_ok"), "executor_connected": b.get("executor_connected"), "steps": b.get("steps")} for b in ctx.boots],
        "limits": "context switches are proxies, not wakeups; interrupt and thread leaders are candidates, not causes; 60 s windows resolve 0.0167% of one core; edge reads add guest and host work",
    }, indent=1, default=str))


def run(a, boot=run_boot):
    root = Path(a.output).resolve()
    if root.is_relative_to(HERE.parents[1]):
        raise ValueError("raw evidence must stay outside the repository")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(root, 0o700)
    ctx = Ctx(root, a)
    ctx.deadline = time.monotonic() + a.max_wall_s
    ctx.current = None
    stop, error, before, after, base_ok = threading.Event(), None, None, None, False
    thread = None
    try:
        cb.check_no_other_vm()
        before = base_digest(a)
        ctx.log("metadata", revision=git_revision(), base_digest_before=before, deck_sha256=sha(a.deck), script_sha256=sha(__file__), stage3_sha256=sha(stage3.__file__),
                vmproto_sha256=sha(v.__file__), python=sys.version, kernel=os.uname().release, cpus=os.cpu_count(), ticks_per_second=HZ,
                loadavg=os.getloadavg(), mem_available_kib=cb.mem_available_kib(), psi_before=cb.psi(), swap_before=cb.vmstat_swap(),
                mono_start=time.monotonic(), wall_ns_start=time.time_ns(), idle_secs=a.idle_secs, window_s=a.window_s, stabilize_s=a.stabilize_s, max_wall_s=a.max_wall_s,
                preflight_s=a.preflight_s, sample_s=a.sample_s, not_tested="controls, real model, concurrent capacity",
                limits="context switches are proxies, not wakeups; edge reads add guest and host work; the host guard samples every sample_s")
        thread = threading.Thread(target=guard_loop, args=(ctx, stop), daemon=True)
        thread.start()
        try:
            ctx.sampler_clock = time.pthread_getcpuclockid(thread.ident)
        except ProcessLookupError:
            pass
        if a.preflight_s:
            ctx.wait(a.preflight_s + 2)  # the guard evaluates a 60 s host window before any VM starts
            ctx.log("preflight", reasons=ctx.stop_reasons, host_context=ctx.guard.context_windows)
        need = a.idle_secs + BOOT_TAIL_S
        for n in range(1, a.boots + 1):
            left = ctx.deadline - time.monotonic()
            if left < need:
                raise RuntimeError(f"budget: {left:.0f} s left, boot {n} needs about {need} s")
            boot(ctx, n)
            if ctx.stop.is_set():
                raise RuntimeError(f"stopped: {ctx.stop_reasons or ctx.sampler_errors}")
    except BaseException as e:
        error = e
        ctx.log("run_error", error=repr(e), traceback=traceback.format_exc())
    stop.set()
    if thread:
        thread.join()
    try:
        cleanup_owned(ctx)
        after = base_digest(a)
        base_ok = before == after
        ctx.log("base_check", before=before, after=after, unchanged=base_ok)
    except Exception as e:
        ctx.retention_errors.append(repr(e))
    ok = error is None and not ctx.sampler_errors and not ctx.retention_errors and base_ok and not ctx.stop_reasons and bool(ctx.boots) and all(b["complete"] for b in ctx.boots)
    try:
        write_summary(ctx, ok, error, {"before": before, "after": after, "unchanged": base_ok})
    except Exception as e:
        ctx.retention_errors.append(repr(e))
        ok = False
    try:
        (root / "evidence-status.json").write_text(json.dumps({"sampler_errors": ctx.sampler_errors, "retention_errors": ctx.retention_errors, "base_unchanged": base_ok,
                                                               "stop_reasons": ctx.stop_reasons}, indent=1))
    except OSError as e:
        ctx.retention_errors.append(repr(e))
    if error:
        raise error
    if ctx.sampler_errors or ctx.retention_errors or not base_ok or ctx.stop_reasons or not ok:
        raise RuntimeError(f"invalid evidence: sampler={ctx.sampler_errors} retention={ctx.retention_errors} base_unchanged={base_ok} stop={ctx.stop_reasons}")
    print(f"Evidence: {root}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=cb.DEFAULT_BASE, help="frozen base qcow2")
    ap.add_argument("--deck", required=True, help="release binary of the benchmark revision")
    ap.add_argument("--output", default=str(Path.home() / ".cache/deck" / datetime.now().strftime("idlebench-%Y%m%d-%H%M%S")), help="new private directory outside Git")
    ap.add_argument("--boots", type=int, default=3, choices=range(1, 4))
    ap.add_argument("--idle-secs", type=int, default=170, help="server idle-save delay; must outlast the stabilization and both windows")
    ap.add_argument("--window-s", type=int, default=60)
    ap.add_argument("--stabilize-s", type=int, default=10)
    ap.add_argument("--max-wall-s", type=int, default=25 * 60)
    ap.add_argument("--preflight-s", type=int, default=60)
    ap.add_argument("--sample-s", type=float, default=10.0, help="host guard period")
    ap.add_argument("--fixture-port", type=int, default=29700)
    args = ap.parse_args()
    if args.idle_secs < args.stabilize_s + 2 * args.window_s + 20:
        ap.error("--idle-secs must exceed stabilization plus two windows by at least 20 s")
    run(args)
