#!/usr/bin/env python3
"""Instrumented saved-state sleep/wake cost experiment. Standard library only.

Fresh overlay boots on one frozen base. Each boot saves and restores the same desktop several
times (codec none, runtime path only). One VM runs at a time. All data is synthetic.
Every phase boundary is an observed timestamp. "to_first_agent_shell" is the whole restore call
(QEMU spawn, state load, guest-agent wait, clock set, first command). It is not pure VM restore.
The server journal, chat and hold gates are not exercised here. Raw output stays outside Git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import threading
import time
import traceback
from datetime import datetime

import stage1 as s1
import vmproto as v

REQUIRED_POST = ("nano_process_same", "notes_file_not_written", "disk_marker", "shell_file",
                 "downloaded_file_sha", "bare_tab_url", "bare_logged_in", "bare_cookie")
TICK = os.sysconf("SC_CLK_TCK")
SWAP_OUT_LIMIT_PAGES = 16384  # 64 MiB per cycle window
DEFAULT_BASE = "/data/tmp/deck-vm-opt/bases/a90de2e085a7ad52.qcow2"


class CycleError(Exception):
    def __init__(self, message, done):
        super().__init__(message)
        self.done = done


# ---------- pure helpers (unit tested) ----------

def check_failures(post, terminal_same, transfer_ok, fixture_ok):
    """Names of every failed correctness gate. A missing key fails; it is never skipped."""
    failed = [k for k in REQUIRED_POST if post.get(k) is not True]
    failed += [name for name, ok in (("terminal_pixels", terminal_same), ("transfer_sha", transfer_ok), ("fixture_read", fixture_ok)) if not ok]
    return failed


def run_cycles(n, step, deadline, now=time.monotonic):
    """Run step(1..n). Stop at the first failed gate or at the deadline. An exception keeps the finished cycles."""
    done = []
    for i in range(1, n + 1):
        if now() >= deadline:
            break
        try:
            c = step(i)
        except Exception as e:
            raise CycleError(f"cycle {i}: {e!r}", done) from e
        done.append(c)
        if c["failures"]:
            break
    return done


def _first(events, name, **match):
    return next((e for e in events if e["name"] == name and all(e.get(k) == x for k, x in match.items())), None)


def _last(events, name, **match):
    return next((e for e in reversed(events) if e["name"] == name and all(e.get(k) == x for k, x in match.items())), None)


def save_phases(events, call_t0, call_t1, result):
    sync = _first(events, "qga.sh", arg="sync")
    mig = _first(events, "qmp.migrate")
    done = _last(events, "qmp.query-migrate", status="completed")
    quit_ = _first(events, "qmp.quit")
    p = {"call_s": round(call_t1 - call_t0, 4),
         "sync_s": None if sync is None else round(sync["t1"] - sync["t0"], 4),
         "migrate_s": None if mig is None or done is None else round(done["t1"] - mig["t0"], 4),
         "quit_to_return_s": None if quit_ is None else round(call_t1 - quit_["t0"], 4)}
    p["complete"] = None not in (p["sync_s"], p["migrate_s"], p["quit_to_return_s"])
    p["unattributed_s"] = round(p["call_s"] - p["sync_s"] - p["migrate_s"] - p["quit_to_return_s"], 4) if p["complete"] else None
    p["harness_agrees"] = abs(result.get("total_s", p["call_s"]) - p["call_s"]) < 0.1
    return p


def restore_phases(events, call_t0, call_t1, result):
    spawn = _first(events, "start_qemu")
    inc = _first(events, "qmp.migrate-incoming")
    run = _last(events, "qmp.query-status", status="running")
    ping = _first(events, "qga.ping_ready")
    p = {"spawn_s": None if spawn is None else round(spawn["t1"] - spawn["t0"], 4),
         "incoming_load_s": None if inc is None or run is None else round(run["t1"] - inc["t0"], 4),
         "to_running_s": None if run is None else round(run["t1"] - call_t0, 4),
         "agent_wait_s": None if run is None or ping is None else round(ping["t1"] - run["t1"], 4),
         "post_agent_s": None if ping is None else round(call_t1 - ping["t1"], 4),
         "to_first_agent_shell_s": round(call_t1 - call_t0, 4)}
    p["complete"] = None not in (p["spawn_s"], p["incoming_load_s"], p["agent_wait_s"], p["post_agent_s"])
    p["harness_agrees"] = abs(result.get("to_usable_s", p["to_first_agent_shell_s"]) - p["to_first_agent_shell_s"]) < 0.1
    return p


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(xs), "min": min(xs), "median": round(statistics.median(xs), 4), "max": max(xs)}


def summarize(cycles):
    """Per metric: first cycle after boot, later cycles, and within- versus between-boot spread of the later ones."""
    out = {}
    for name in sorted({k for c in cycles for k in c["metrics"]}):
        rows = [(c["boot"], c["cycle"], c["metrics"].get(name)) for c in cycles]
        later = {}
        for b, n, x in rows:
            if n > 1 and x is not None:
                later.setdefault(b, []).append(x)
        out[name] = {"first_after_boot": stats([x for b, n, x in rows if n == 1]),
                     "later": stats([x for xs in later.values() for x in xs]),
                     "all": stats([x for _, _, x in rows]),
                     "missing": sum(x is None for _, _, x in rows),
                     "within_boot_range_median": round(statistics.median([max(xs) - min(xs) for xs in later.values()]), 4) if later else None,
                     "between_boot_median_range": round(max(statistics.median(xs) for xs in later.values()) - min(statistics.median(xs) for xs in later.values()), 4) if later else None}
    return out


def contention(m):
    """Reasons a window cannot be trusted. Thresholds are deliberately blunt."""
    wall = max(m["wall_s"], 1e-9) * 1e6
    out = []
    if m["swapout_pages"] > SWAP_OUT_LIMIT_PAGES:  # host-wide: background paging of other processes is expected
        out.append("swap_out")
    if m.get("vm_swap_kib", 0) > 0:  # attributable: the guest's own memory was swapped
        out.append("vm_swapped")
    if m["psi_memory_some_us"] > 0.05 * wall:
        out.append("memory_pressure")
    if m["psi_cpu_some_us"] > 0.25 * wall:
        out.append("cpu_pressure")
    if m["mem_available_kib"] < 2 << 20:
        out.append("low_memory")
    return out


# ---------- host readings ----------

def psi():
    out = {}
    for res in ("cpu", "memory", "io"):
        d = {}
        for line in Path(f"/proc/pressure/{res}").read_text().splitlines():
            kind, *kv = line.split()
            d[kind] = int(dict(x.split("=") for x in kv)["total"])
        out[res] = d
    return out


def vmstat_swap():
    d = dict(line.split() for line in Path("/proc/vmstat").read_text().splitlines())
    return {"in": int(d["pswpin"]), "out": int(d["pswpout"])}


def mem_available_kib():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])


def sys_busy_s():
    f = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    return (sum(f) - f[3] - f[4]) / TICK


def qemu_pids():
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if os.path.basename((d / "cmdline").read_bytes().split(b"\0")[0].decode()).startswith("qemu-system"):
                    out.append(int(d.name))
            except OSError:
                pass
    return out


def check_no_other_vm():
    if pids := qemu_pids():
        raise RuntimeError(f"another qemu process is running: {pids}")


def cg_stats(pid):
    cg = v.cgroup_dir(pid) if pid else None
    if cg is None:
        return None
    out = {"cpu_usage_usec": v.cpu_usec(cg)}
    for name in ("memory.current", "memory.peak", "io.stat"):
        try:
            out[name] = (cg / name).read_text().strip()
        except OSError:
            out[name] = None  # controller not delegated here: unavailable, not zero
    return out


def fincore(path):
    """Page-cache residency of one file, recorded only. Nothing is evicted."""
    try:
        r = subprocess.run(["fincore", "-b", "-n", "-o", "RES,SIZE", str(path)], capture_output=True, text=True, timeout=5)
        res, size = r.stdout.split()
        return {"resident_bytes": int(res), "size_bytes": int(size)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def vm_swap_kib(pid):
    total = 0
    for q in v.tree_pids(pid):
        try:
            total += int(next((l for l in Path(f"/proc/{q}/status").read_text().splitlines() if l.startswith("VmSwap:")), "VmSwap: 0 kB").split()[1])
        except OSError:
            pass
    return total


def vm_stats(p, with_pss=False):
    vm = v.read_vm(p) if p.vm.exists() else None
    if not vm or not v.proc_ok(p, vm["pid"]):
        return None
    pid = vm["pid"]
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    out = {"pid": pid, "rss_kib": v.tree_rss_kib(pid), "swap_kib": vm_swap_kib(pid), "cpu_ticks": int(stat[11]) + int(stat[12]), "cg": cg_stats(pid)}
    try:
        io = dict(x.split(": ") for x in Path(f"/proc/{pid}/io").read_text().splitlines())
        out["io"] = {k: int(io[k]) for k in ("read_bytes", "write_bytes")}
    except (OSError, KeyError, ValueError):
        out["io"] = None
    if with_pss:
        try:
            out["pss_kib"] = int(next(l for l in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines() if l.startswith("Pss:")).split()[1])
        except (OSError, StopIteration):
            out["pss_kib"] = None
    return out


def snap(ctx):
    return {"mono": time.monotonic(), "psi": psi(), "swap": vmstat_swap(), "mem_available_kib": mem_available_kib(), "sys_busy_s": sys_busy_s(),
            "proc_cpu_s": time.process_time(), "observer_cpu_s": ctx.observer_cpu(), "children_cpu_s": sum(os.times()[2:4])}


def host_delta(a, b):
    obs = b["observer_cpu_s"] - a["observer_cpu_s"]
    return {"wall_s": round(b["mono"] - a["mono"], 4), "swapout_pages": b["swap"]["out"] - a["swap"]["out"], "swapin_pages": b["swap"]["in"] - a["swap"]["in"],
            "psi_cpu_some_us": b["psi"]["cpu"]["some"] - a["psi"]["cpu"]["some"], "psi_memory_some_us": b["psi"]["memory"]["some"] - a["psi"]["memory"]["some"],
            "psi_io_some_us": b["psi"]["io"]["some"] - a["psi"]["io"]["some"], "mem_available_kib": min(a["mem_available_kib"], b["mem_available_kib"]),
            "sys_busy_s": round(b["sys_busy_s"] - a["sys_busy_s"], 3), "observer_cpu_s": round(obs, 4),
            "harness_cpu_s": round(b["proc_cpu_s"] - a["proc_cpu_s"] - obs, 4), "children_cpu_s": round(b["children_cpu_s"] - a["children_cpu_s"], 4)}


# ---------- run context, sampler, tracing ----------

class Ctx:
    def __init__(self, root, args):
        self.root, self.args, self.base = root, args, args.base
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.cycles, self.events, self.sampler_errors, self.retention_errors = [], [], [], []
        self.current = None  # Paths of the VM runtime the sampler should watch
        self.sampler_clock = None
        self.sample_n = 0
        self.pre_quit = None
        self.deadline = float('inf')

    def log(self, kind, **data):
        rec = {"wall_ns": time.time_ns(), "elapsed_s": round(time.monotonic() - self.started, 4), "kind": kind, **data}
        with self.lock, (self.root / "trace.jsonl").open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        if kind == "cycle":
            self.cycles.append(data)

    def observer_cpu(self):
        return time.clock_gettime(self.sampler_clock) if self.sampler_clock is not None else 0.0

    def check_sampler(self):
        if self.sampler_errors:
            raise RuntimeError(f"sampler failed: {self.sampler_errors}")


def sample_once(ctx):
    ctx.sample_n += 1
    rec = {"wall_ns": time.time_ns(), "mono": time.monotonic(), "psi": psi(), "swap": vmstat_swap(), "mem_available_kib": mem_available_kib(),
           "load": os.getloadavg(), "observer_cpu_s": time.thread_time(), "harness_cpu_s": time.process_time(), "vm": None}
    p = ctx.current
    if p is not None:
        try:
            rec["vm"] = vm_stats(p, with_pss=ctx.sample_n % 5 == 1)
            rec["overlay_allocated_bytes"] = v.allocated_bytes(p.disk) if p.disk.exists() else None
            rec["state_bytes"] = p.statefile.stat().st_size if p.statefile.exists() else None
        except (OSError, ValueError, IndexError) as e:  # the process or file vanished mid-read
            rec["vm"] = {"transient": repr(e)}
    return rec


def sampler(ctx, stop):
    try:
        with (ctx.root / "samples.jsonl").open("a") as f:
            while True:
                f.write(json.dumps(sample_once(ctx)) + "\n")
                f.flush()
                if stop.wait(ctx.args.sample_s):
                    f.write(json.dumps(sample_once(ctx)) + "\n")
                    return
    except BaseException as e:
        ctx.sampler_errors.append(repr(e))


class Tracing:
    """Time-stamp the harness calls that mark save and restore boundaries. Restores the originals on exit."""
    def __enter__(self):
        self.ctx = None
        self.orig = (v.Qmp.cmd, v.Qga.sh, v.Qga.ping_ready, v.start_qemu)
        cmd, sh, ping, start = self.orig
        tr = self

        def traced(name, fn, extra=lambda r: {}, **fixed):
            t0 = time.monotonic()
            r = None
            try:
                r = fn()
                return r
            finally:
                if tr.ctx is not None:
                    tr.ctx.events.append({"name": name, "t0": t0, "t1": time.monotonic(), **fixed, **(extra(r) if r is not None else {})})

        def qmp(self_, name, **args):
            if name == "quit" and tr.ctx is not None and tr.ctx.current is not None:
                vm = v.read_vm(tr.ctx.current)
                tr.ctx.pre_quit = cg_stats(vm["pid"]) if vm else None
            return traced("qmp." + name, lambda: cmd(self_, name, **args), lambda r: {"status": r.get("status")} if isinstance(r, dict) else {})
        v.Qmp.cmd = qmp
        v.Qga.sh = lambda self_, script, **kw: traced("qga.sh", lambda: sh(self_, script, **kw), arg=script[:40])
        v.Qga.ping_ready = lambda self_, timeout: traced("qga.ping_ready", lambda: ping(self_, timeout))
        v.start_qemu = lambda *a, **kw: traced("start_qemu", lambda: start(*a, **kw))
        return self

    def __exit__(self, *exc):
        v.Qmp.cmd, v.Qga.sh, v.Qga.ping_ready, v.start_qemu = self.orig


# ---------- live experiment ----------

def timed(fn):
    t = time.monotonic()
    r = fn()
    return r, round(time.monotonic() - t, 4)


def prepare_session(g, p, port):
    """Same synthetic session as Stage 1: disk and shell markers, a bare login on the fixture, an unsaved terminal note."""
    g.sh("printf created-in-guest > /home/deck/shell-created.txt; printf %s " + s1.DISK_MARK + " > /home/deck/disk-marker; mkdir -p /home/deck/Downloads /home/deck/shots; chown -R deck:deck /home/deck; sync")
    g.bare_init()
    g.tool("open", url=s1.FIX + "/", window=True, downloads_dir="/home/deck/Downloads")
    snap_ = g.tool("snapshot")
    g.tool("act", op="click", ref=g.ref(snap_, "link", "Download report"))
    time.sleep(1.5)
    g.tool("act", op="click", ref=g.ref(snap_, "link", "Sign in"))
    snap_ = g.tool("snapshot")
    for label, text in (("User", "demo"), ("Password", "demo-pass")):
        g.tool("act", op="type", ref=g.ref(snap_, "textbox", label), text=text)
    g.tool("act", op="click", ref=g.ref(snap_, "button", "Sign in"))
    g.tool("snapshot", mode="full")
    g.deck("swaymsg '[app_id=foot] focus'")
    time.sleep(0.5)
    g.deck(f"wtype '{s1.NOTE}'")
    time.sleep(0.5)
    state = {"ref_nano": s1.nano_identity(g), "foot_h": s1.foot_crop_hash(g)}
    post = s1.checks(g, ref_nano=state["ref_nano"])
    if bad := check_failures(post, True, True, True):
        raise RuntimeError(f"session setup failed gates: {bad}")
    return state


def cycle_step(ctx, p, g_box, state, boot, n, port):
    ctx.check_sampler()
    g = g_box[0]
    rec = {"boot": boot, "cycle": n, "label": "first_after_boot" if n == 1 else "later", "failures": [], "metrics": {}}
    ctx.pre_quit = None
    g.q.sh("true")
    pid = v.read_vm(p)["pid"]
    owned = v.tree_pids(pid)
    cycle_start = snap(ctx)
    rec["cg_before_save"] = cg_stats(pid)
    swap_before_save = vm_swap_kib(pid)
    i0 = len(ctx.events)
    a = snap(ctx)
    t0 = time.monotonic()
    saved = v.save_vm(p, qga=g.q)
    t1 = time.monotonic()
    b = snap(ctx)
    save_ev = ctx.events[i0:]
    rec["save"] = {"harness": saved, "phases": save_phases(save_ev, t0, t1, saved), "host": host_delta(a, b), "cg_pre_quit": ctx.pre_quit, "events": save_ev,
                   "wall_t0": t0, "wall_t1": t1}
    left = [x for x in owned if v.alive(x)]
    rec["vm_gone_after_save"] = not left
    rec["state_file"] = {"size_bytes": p.statefile.stat().st_size, "allocated_bytes": v.allocated_bytes(p.statefile), "page_cache": fincore(p.statefile),
                         "residency": "recorded after save, not controlled; disk-cold is not claimed"}
    rec["overlay_allocated_bytes_after_save"] = v.allocated_bytes(p.disk)
    ctx.log("cycle_partial", boot=boot, cycle=n, save=rec["save"]["phases"])
    i1 = len(ctx.events)
    a = snap(ctx)
    t0 = time.monotonic()
    restored = v.restore_vm(p, port)
    t1 = time.monotonic()
    b = snap(ctx)
    restore_ev = ctx.events[i1:]
    rec["restore"] = {"harness": restored, "phases": restore_phases(restore_ev, t0, t1, restored), "host": host_delta(a, b), "events": restore_ev,
                      "cg_after": cg_stats(restored["pid"]), "wall_t0": t0, "wall_t1": t1}
    g = s1.Guest(p)
    g.n = 1000 * n
    g_box[0] = g
    # latencies measured from restore return, in this fixed order
    _, shell2 = timed(lambda: g.sh("true"))
    ready = g.desktop_ready(30)
    bare, bare_s = timed(lambda: g.tool("eval", js="location.href"))
    rec["post_probes"] = {"second_agent_shell_s": shell2, "desktop_ready_s": round(ready, 4), "first_bare_eval_s": bare_s, "bare_url_ok": "/account" in bare}
    post = s1.checks(g, ref_nano=state["ref_nano"])
    same = s1.foot_crop_hash(g) == state["foot_h"]
    local = p.art / f"report-{n}.csv"
    g.q.pull("/home/deck/Downloads/report.csv", str(local))
    transfer = hashlib.sha256(local.read_bytes()).hexdigest() == s1.CSV_SHA
    fixture = g.sh(f"curl -s -m 5 {s1.FIX}/health")[1] == "ok"
    rec["post"], rec["terminal_pixels_same"], rec["transfer_ok"], rec["fixture_ok"] = post, same, transfer, fixture
    rec["failures"] = check_failures(post, same, transfer, fixture)
    if not rec["vm_gone_after_save"]:
        rec["failures"].append("vm_not_gone_after_save")
    if not rec["post_probes"]["bare_url_ok"]:
        rec["failures"].append("bare_probe_url")
    ph_s, ph_r = rec["save"]["phases"], rec["restore"]["phases"]
    if not (ph_s["complete"] and ph_r["complete"] and ph_s["harness_agrees"] and ph_r["harness_agrees"]):
        rec["failures"].append("timing_incomplete")
    end = snap(ctx)
    rec["vm_swap_kib"] = {"before_save": swap_before_save, "after_restore": vm_swap_kib(restored["pid"])}
    rec["cycle_host"] = host_delta(cycle_start, end)
    rec["failures"] += ["contention:" + x for x in contention({**rec["cycle_host"], "vm_swap_kib": max(rec["vm_swap_kib"].values())})]
    ctx.check_sampler()
    m = {}
    for side, ph in (("save", ph_s), ("restore", ph_r)):
        for k, x in ph.items():
            if isinstance(x, float):
                m[f"{side}.{k}"] = x
        for k, x in rec[side]["host"].items():
            if k in ("observer_cpu_s", "harness_cpu_s", "children_cpu_s", "psi_cpu_some_us", "psi_memory_some_us", "psi_io_some_us", "swapout_pages"):
                m[f"{side}.host.{k}"] = x
    m.update({"save.vm_cpu_s": saved.get("cpu_s"), "save.peak_rss_kib": saved.get("peak_rss_kib_sampled"), "state_bytes": saved["state_bytes"],
              "restore.vm_cpu_s_since_launch": restored.get("cpu_s_since_launch"), "restore.peak_rss_kib": restored.get("peak_rss_kib_sampled"),
              "state_allocated_bytes": rec["state_file"]["allocated_bytes"], "overlay_allocated_bytes": rec["overlay_allocated_bytes_after_save"],
              "post.second_agent_shell_s": shell2, "post.desktop_ready_s": rec["post_probes"]["desktop_ready_s"], "post.first_bare_eval_s": bare_s,
              "cycle.psi_cpu_some_us": rec["cycle_host"]["psi_cpu_some_us"]})
    rec["metrics"] = m
    ctx.log("cycle", **rec)
    return rec


def run_boot(ctx, n):
    a = ctx.args
    p = v.Paths(ctx.root / f"boot-{n}")
    port = getattr(a, "fixture_port", 29661) + 2 * n
    ctx.log("boot_begin", boot=n)
    v.create_overlay(p, ctx.base)
    ctx.current = p
    s1.fixture_start(p, port, port + 1)
    try:
        t0 = time.monotonic()
        v.start_qemu(p, "run", port)
        g = s1.Guest(p)
        g.q.ping_ready(120)
        g.sh("true")
        cmd_s = time.monotonic() - t0
        ready_s = cmd_s + g.desktop_ready()
        ctx.log("cold_boot", boot=n, launch_to_guest_command_s=round(cmd_s, 3), launch_to_desktop_ready_s=round(ready_s, 3))
        state = prepare_session(g, p, port)
        ctx.log("session_ready", boot=n)
        time.sleep(getattr(a, "settle_s", 15))
        want = min(a.cycles, a.max_cycles - len(ctx.cycles))
        done = run_cycles(want, lambda i: cycle_step(ctx, p, [g], state, n, i, port), ctx.deadline)
        if len(done) < want and not (done and done[-1]["failures"]):
            raise TimeoutError(f"wall budget reached after {len(done)} of {want} cycles")
        if done and done[-1]["failures"]:
            raise RuntimeError(f"boot {n} cycle {done[-1]['cycle']} failed: {done[-1]['failures']}")
    except CycleError as e:
        raise RuntimeError(str(e)) from e
    finally:
        if (vm := v.read_vm(p) if p.vm.exists() else None) and v.proc_ok(p, vm["pid"]):
            v.kill_vm(p)
        s1.fixture_stop(p)
        ctx.current = None
        ctx.log("boot_end", boot=n)


def cleanup_owned(ctx):
    """Stop only runtimes created by this run, after checking their identity, then confirm they are gone."""
    left = []
    for d in sorted(ctx.root.glob("boot-*")):
        p = v.Paths(d)
        vm = v.read_vm(p) if p.vm.exists() else None
        if vm and v.proc_ok(p, vm["pid"]):
            v.kill_vm(p)
            if v.alive(vm["pid"]):
                left.append(vm["pid"])
        s1.fixture_stop(p)
    ctx.log("cleanup", vm_left=left, qemu_pids_on_host=qemu_pids())


def digest_ok(path):
    side = Path(path).with_suffix(".json")
    return v.sha256_file(path) == json.loads(side.read_text())["sha256"]


def base_unchanged(a):
    return digest_ok(a.base)


def preflight(a):
    check_no_other_vm()
    free = shutil.disk_usage(Path(a.output).parent).free
    if free < 12 << 30:
        raise RuntimeError("less than 12 GiB free for overlays and state files")
    if not digest_ok(a.base):
        raise RuntimeError("base digest does not match its record")
    qv = subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    return {"base": Path(a.base).stem, "base_digest_ok": True, "deck_binary_sha256": s1_hash(a.deck), "qemu": qv, "kernel": os.uname().release, "cpus": os.cpu_count(),
            "loadavg": os.getloadavg(), "mem_available_kib": mem_available_kib(), "fincore": bool(shutil.which("fincore")), "psi_before": psi(), "swap_before": vmstat_swap(),
            "codec": "none", "tick_hz": TICK,
            "not_exercised": "server journal, chat continuity, budget and automation holds, real-model latency; the release binary is only hashed",
            "limits": "sampler 1 s plus boundary snapshots; page-cache residency is recorded, never controlled; cgroup io.stat appears only when delegated"}


def s1_hash(path):
    if not path:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def run(a):
    root = Path(a.output).resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("raw evidence must stay outside the repository")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(root, 0o700)
    ctx = Ctx(root, a)
    ctx.deadline = time.monotonic() + a.max_wall_s
    ctx.log("metadata", **preflight(a), revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=Path(__file__).parent).strip(),
            script_sha256=s1_hash(__file__), boots=a.boots, cycles_per_boot=a.cycles, max_cycles=a.max_cycles, max_wall_s=a.max_wall_s)
    stop = threading.Event()
    thread = threading.Thread(target=sampler, args=(ctx, stop), daemon=True)
    thread.start()
    try:
        ctx.sampler_clock = time.pthread_getcpuclockid(thread.ident)
    except ProcessLookupError:
        ctx.sampler_errors.append("sampler clock unavailable: thread exited during startup")
    error = None
    with Tracing() as tracing:
        tracing.ctx = ctx
        try:
            for n in range(1, a.boots + 1):
                ctx.check_sampler()
                run_boot(ctx, n)
        except BaseException as e:
            error = e
            ctx.log("run_error", error=repr(e), traceback=traceback.format_exc())
    stop.set()
    thread.join()
    base_ok = False
    try:
        cleanup_owned(ctx)
        base_ok = base_unchanged(a)
        ctx.log("base_check", unchanged=base_ok)
    except Exception as e:
        ctx.retention_errors.append(repr(e))
    ok = error is None and not ctx.sampler_errors and not ctx.retention_errors and base_ok and all(not c["failures"] for c in ctx.cycles)
    try:
        (root / "summary.json").write_text(json.dumps({"ok": ok, "error": repr(error) if error else None, "cycles": ctx.cycles, "summary": summarize(ctx.cycles)}, indent=1, default=str))
        (root / "evidence-status.json").write_text(json.dumps({"sampler_errors": ctx.sampler_errors, "retention_errors": ctx.retention_errors, "base_unchanged": base_ok}, indent=1))
    except OSError as e:
        ctx.retention_errors.append(repr(e))
    if error:
        raise error
    if ctx.sampler_errors:
        raise RuntimeError(f"invalid evidence: sampler failed: {ctx.sampler_errors}")
    if ctx.retention_errors or not base_ok:
        raise RuntimeError(f"invalid evidence: retention={ctx.retention_errors} base_unchanged={base_ok}")
    print(f"Evidence: {root}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE, help="frozen base qcow2")
    ap.add_argument("--deck", help="release binary; only hashed, the integrated path is not run")
    ap.add_argument("--output", default=str(Path.home() / ".cache/deck" / datetime.now().strftime("cyclebench-%Y%m%d-%H%M%S")), help="new private directory outside Git")
    ap.add_argument("--boots", type=int, default=3, choices=range(1, 6))
    ap.add_argument("--cycles", type=int, default=5, help="save/restore cycles per boot")
    ap.add_argument("--max-cycles", type=int, default=15)
    ap.add_argument("--max-wall-s", type=int, default=45 * 60)
    ap.add_argument("--settle-s", type=int, default=15, help="wait after session setup before cycle 1")
    ap.add_argument("--sample-s", type=float, default=1.0)
    ap.add_argument("--fixture-port", type=int, default=29661)
    run(ap.parse_args())
