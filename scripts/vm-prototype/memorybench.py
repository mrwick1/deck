#!/usr/bin/env python3
"""Sustained synthetic bare memory experiment. Standard library only.

Each boot is an independent fresh overlay on one frozen base. A boot sets up the Stage 1 desktop
session, runs a paced bare workload for a fixed time, saves the state, restores it and times the
first bare action. One VM runs at a time. All data is synthetic. Bare is the only browser.
This drives the guest bare session directly. It does not test the server, executor, chat,
journal or holds. A 10 s sample cannot show short peaks. Host RSS is not guest use, and guest
cache or tmpfs growth is not a leak. Raw output stays outside Git.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path, PurePosixPath

import benchmark
import cyclebench as cb
import fixture
import stage1 as s1
import vmproto as v

HERE = Path(__file__).resolve().parent
SESSION_CAP = 3
DL_CAP_BYTES = 32 << 20
DL_SUB = "membench"
GUEST_USER = "deck"
ROUND = ["navigate", "snapshot", "read", "navigate", "download", "navigate", "new_tab", "extra_session"]
MAX_OPS = 2000
GUEST_PHASES = ("settle", "load", "post")
GUEST_MEM_KEYS = ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "AnonPages", "Mapped", "Shmem", "Slab", "SReclaimable",
                  "SUnreclaim", "PageTables", "KernelStack", "SwapTotal", "SwapFree")
qemu_pids = cb.qemu_pids


# ---------- pure helpers (unit tested) ----------

def count_sessions(text):
    """Live bare sessions, tabs a page opened included (they are listed as name~N)."""
    return len(re.findall(r"^\S+: ", text, re.M))


def check_session_cap(n, cap=SESSION_CAP):
    if n > cap:
        raise RuntimeError(f"session cap {cap} exceeded: {n} open")


def parse_downloads(text):
    return [(m[2], int(m[3])) for m in re.finditer(r"^(downloaded: )?(/.+?) \((\d+) bytes", text, re.M)]


def owned_download(directory, path):
    """(k, name) for a synthetic download that sits directly in our directory, else None."""
    p = PurePosixPath(path)
    m = re.fullmatch(r"mem-(\d)(?: \(\d{1,4}\))?\.bin", p.name)
    if str(p.parent) != str(directory) or not m or int(m[1]) >= fixture.MEM_DOWNLOADS:
        return None
    return int(m[1]), p.name


def prune_plan(files, incoming, cap):
    """Names to delete, oldest first, so that the files plus the incoming one fit under the cap."""
    total = sum(f["size"] for f in files) + incoming
    drop = []
    for f in files:
        if total <= cap:
            break
        drop.append(f["name"])
        total -= f["size"]
    return drop


def cleanup_commands(directory, names):
    return [f"rm -f -- {shlex.quote(f'{directory}/{n}')}" for n in names if owned_download(directory, f"{directory}/{n}")]


class WorkloadError(Exception):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def run_workload(ops, deadline, step, now, sleep, pace_s, max_ops, stop=lambda: None):
    """Run step(i, name) over the repeating op list. Stop at the deadline or the op cap. A failure keeps the finished counts."""
    res = {"ops": 0, "counts": {}, "stopped": None, "failed_op": None}
    while True:
        if now() >= deadline:
            res["stopped"] = "deadline"
            return res
        if res["ops"] >= max_ops:
            res["stopped"] = "max_ops"
            return res
        if why := stop():
            res["stopped"] = "stop_request"
            raise WorkloadError(f"stop requested: {why}", res)
        name = ops[res["ops"] % len(ops)]
        try:
            step(res["ops"] + 1, name)
        except Exception as e:
            res["failed_op"] = name
            raise WorkloadError(f"op {res['ops'] + 1} ({name}): {e!r}", res) from e
        res["ops"] += 1
        res["counts"][name] = res["counts"].get(name, 0) + 1
        sleep(pace_s)


def slope(points):
    """Least-squares slope per minute of (seconds, value) points. None values are skipped, never zeroed."""
    pts = [(float(t), float(y)) for t, y in points if y is not None]
    n = len(pts)
    out = {"n": n, "per_min": None, "stderr_per_min": None, "label": "insufficient"}
    if n < 2:
        return out
    tm, ym = sum(t for t, _ in pts) / n, sum(y for _, y in pts) / n
    sxx = sum((t - tm) ** 2 for t, _ in pts)
    if sxx == 0:
        return out
    b = sum((t - tm) * (y - ym) for t, y in pts) / sxx
    out["per_min"] = round(b * 60, 6)
    if n > 2:
        se = math.sqrt(sum((y - ym - b * (t - tm)) ** 2 for t, y in pts) / (n - 2) / sxx)
        out["stderr_per_min"] = round(se * 60, 6)
        out["label"] = "growing" if b > 2 * se and b > 0 else "shrinking" if b < -2 * se and b < 0 else "no_significant_trend"
    return out  # the standard error ignores autocorrelation: treat the label as indicative


def growth_summary(samples, get, early_s=300, steady_from_s=600):
    def val(s):
        try:
            return get(s)
        except (KeyError, TypeError, IndexError):
            return None
    pts = [(s["t"], val(s)) for s in samples]
    vals = [y for _, y in pts if y is not None]
    return {"n": len(vals), "first": vals[0] if vals else None, "last": vals[-1] if vals else None,
            "min": min(vals) if vals else None, "max": max(vals) if vals else None,
            "early": slope([p for p in pts if p[0] <= early_s]), "steady": slope([p for p in pts if p[0] >= steady_from_s]), "all": slope(pts)}


def per_request(delta, requests):
    return None if delta is None or not requests else round(delta / requests, 3)


def _sum(xs):
    return sum(xs) if xs and all(x is not None for x in xs) else None


def parse_guest_sample(text):
    d = json.loads(text)
    mi = d.get("meminfo") or {}
    g = {"meminfo": {k: mi.get(k) for k in GUEST_MEM_KEYS}}
    total, free = g["meminfo"]["SwapTotal"], g["meminfo"]["SwapFree"]
    g["swap_used_kib"] = None if total is None or free is None else total - free
    for k in ("psi_memory", "oom_kill", "pswpin", "pswpout", "files", "downloads", "bare_dir", "dirs", "runtime_tmpfs_used_bytes", "runtime", "self_cpu_ticks"):
        g[k] = d.get(k)
    g["procs"] = d.get("procs") or []
    bare = [p for p in g["procs"] if p.get("group", "bare") == "bare"]
    g["bare_rss_kib"] = _sum([p.get("rss_kib") for p in bare])
    g["bare_pss_kib"] = _sum([p.get("pss_kib") for p in bare])
    g["bare_pss_anon_kib"] = _sum([p.get("pss_anon_kib") for p in bare])
    return g


def identity_changes(prev, cur):
    """Pids present in both samples with a different start time: a reused pid, not growth."""
    start = {p["pid"]: p["start"] for p in prev}
    return [p["pid"] for p in cur if p["pid"] in start and start[p["pid"]] != p["start"]]


def guardrails(host, cg_prev, cg_cur, guest):
    """Stop reasons. host: window metrics with the cyclebench thresholds, unchanged. guest: (previous, current)."""
    out = cb.contention(host) if host else []
    if cg_cur is not None:
        base = cg_prev or {}
        if cg_cur.get("oom", 0) + cg_cur.get("oom_kill", 0) > base.get("oom", 0) + base.get("oom_kill", 0):
            out.append("cgroup_oom")
        if cg_cur.get("max", 0) > base.get("max", 0):
            out.append("cgroup_max")
    if guest:
        g0, g1 = guest
        if (g1.get("oom_kill") or 0) > (g0.get("oom_kill") or 0):
            out.append("guest_oom")
        if (g1.get("swap_used_kib") or 0) > 0:
            out.append("guest_swap")
    return out


def window_metrics(a, b):
    vm = b.get("vm") or {}
    return {"wall_s": b["mono"] - a["mono"], "swapout_pages": b["swap"]["out"] - a["swap"]["out"],
            "psi_memory_some_us": b["psi"]["memory"]["some"] - a["psi"]["memory"]["some"],
            "psi_cpu_some_us": b["psi"]["cpu"]["some"] - a["psi"]["cpu"]["some"],
            "mem_available_kib": min(a["mem_available_kib"], b["mem_available_kib"]), "vm_swap_kib": vm.get("swap_kib") or 0}


METRICS = {
    "host_qemu_rss_kib": lambda s: s["vm"]["rss_kib"], "host_qemu_pss_kib": lambda s: s["vm"]["pss_kib"],
    "host_cgroup_current_bytes": lambda s: s["vm"]["cg"]["current"], "host_cgroup_anon_bytes": lambda s: s["vm"]["cg"]["stat"]["anon"],
    "host_cgroup_file_bytes": lambda s: s["vm"]["cg"]["stat"]["file"], "host_cgroup_shmem_bytes": lambda s: s["vm"]["cg"]["stat"]["shmem"],
    "guest_mem_available_kib": lambda s: s["guest"]["meminfo"]["MemAvailable"],
    "guest_used_kib_total_minus_available": lambda s: s["guest"]["meminfo"]["MemTotal"] - s["guest"]["meminfo"]["MemAvailable"],
    "guest_anon_pages_kib": lambda s: s["guest"]["meminfo"]["AnonPages"], "guest_cached_kib": lambda s: s["guest"]["meminfo"]["Cached"],
    "guest_slab_kib": lambda s: s["guest"]["meminfo"]["Slab"], "guest_shmem_kib": lambda s: s["guest"]["meminfo"]["Shmem"],
    "guest_bare_rss_kib": lambda s: s["guest"]["bare_rss_kib"], "guest_bare_pss_kib": lambda s: s["guest"]["bare_pss_kib"],
    "guest_bare_pss_anon_kib": lambda s: s["guest"]["bare_pss_anon_kib"],
    "guest_bare_out_bytes": lambda s: s["guest"]["files"]["out"]["bytes"], "guest_bare_err_bytes": lambda s: s["guest"]["files"]["err"]["bytes"],
    "guest_runtime_tmpfs_used_bytes": lambda s: s["guest"]["runtime_tmpfs_used_bytes"],
    "guest_owned_download_bytes": lambda s: s["guest"]["downloads"]["bytes"],
}


def analyze(root, workloads):
    """Per boot growth of every metric over the load window, from the persisted samples."""
    samples = [json.loads(x) for x in (Path(root) / "samples.jsonl").read_text().splitlines() if x]
    out = {}
    for n in sorted({s["boot"] for s in samples if s["phase"] == "load"}):
        load = [s for s in samples if s["boot"] == n and s["phase"] == "load"]
        for s in load:
            s["t"] = s["mono"] - load[0]["mono"]
        out[n] = {"samples": len(load), "span_s": round(load[-1]["t"], 1), "metrics": {k: growth_summary(load, f) for k, f in METRICS.items()}}
        o = out[n]["metrics"]["guest_bare_out_bytes"]
        out[n]["bare_out_bytes_per_request"] = per_request(None if o["last"] is None else o["last"] - o["first"], workloads.get(n, {}).get("requests_total"))
        out[n]["pid_reuse_seen"] = sorted({q for a, b in zip(load, load[1:]) if a.get("guest") and b.get("guest") for q in identity_changes(a["guest"]["procs"], b["guest"]["procs"])})
        gaps = [b["mono"] - a["mono"] for a, b in zip(load, load[1:])]
        out[n]["sample_gap_s"] = {"median": round(statistics.median(gaps), 2), "max": round(max(gaps), 2)} if gaps else None
    return out


# ---------- guest sample (runs in the guest as root, python3 on stdin) ----------

GUEST_SCRIPT = r'''
import json, os, pwd, sys
dl, user = sys.argv[1], sys.argv[2]
def rd(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None
def nums(text, sep):
    out = {}
    for l in (text or "").splitlines():
        k, _, rest = l.partition(sep)
        w = rest.split()
        if w and w[0].lstrip("-").isdigit():
            out[k.strip()] = int(w[0])
    return out
def psi(text):
    if text is None:
        return None
    out = {}
    for l in text.splitlines():
        kind, *kv = l.split()
        d = dict(x.split("=") for x in kv)
        out[kind] = {"avg10": float(d["avg10"]), "total_us": int(d["total"])}
    return out
def tree_bytes(path, cap=20000):
    total = n = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            n += 1
            if n > cap:
                return None
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total if os.path.isdir(path) else None
def fsize(path):
    try:
        return {"path": os.path.realpath(path), "bytes": os.stat(path).st_size}
    except OSError:
        return None
page = os.sysconf("SC_PAGE_SIZE") // 1024
procs = {}
for d in os.listdir("/proc"):
    if not d.isdigit():
        continue
    s = rd("/proc/%s/stat" % d)
    if not s or ")" not in s:
        continue
    f = s[s.rindex(")") + 2:].split()
    procs[int(d)] = {"comm": s[s.index("(") + 1:s.rindex(")")], "ppid": int(f[1]), "start": int(f[19]), "ticks": int(f[11]) + int(f[12])}
bare = {p for p, x in procs.items() if x["comm"] == "bare-mcp"}
grown = True
while grown:
    grown = False
    for p, x in procs.items():
        if p not in bare and x["ppid"] in bare:
            bare.add(p)
            grown = True
desk = {p for p, x in procs.items() if x["comm"] in ("sway", "foot", "nano")}
def rss(p):
    st = rd("/proc/%d/statm" % p)
    return int(st.split()[1]) * page if st else None
out_procs = []
for p in sorted(bare | desk):
    sm = nums(rd("/proc/%d/smaps_rollup" % p), ":")
    out_procs.append({"group": "bare" if p in bare else "desktop", "pid": p, "start": procs[p]["start"], "comm": procs[p]["comm"], "ticks": procs[p]["ticks"],
                      "rss_kib": rss(p), "pss_kib": sm.get("Pss"), "pss_anon_kib": sm.get("Pss_Anon"), "pss_file_kib": sm.get("Pss_File"),
                      "pss_shmem_kib": sm.get("Pss_Shmem"), "swap_kib": sm.get("Swap")})
rest = sorted(((rss(p) or 0, p) for p in procs if p not in bare and p not in desk), reverse=True)[:5]
others = [{"pid": p, "start": procs[p]["start"], "comm": procs[p]["comm"], "rss_kib": r} for r, p in rest]
u = pwd.getpwnam(user)
runtime = None
for p in sorted(bare):
    for kv in (rd("/proc/%d/environ" % p) or "").split("\0"):
        if kv.startswith("XDG_RUNTIME_DIR="):
            runtime = kv.split("=", 1)[1]
    if runtime:
        break
runtime = runtime or "/run/user/%d" % u.pw_uid
bd = runtime + "/bare"
try:
    bare_dir = {n: os.lstat(bd + "/" + n).st_size for n in os.listdir(bd)}
except OSError:
    bare_dir = None
try:
    ents = [e for e in os.scandir(dl) if e.is_file()]
    downloads = {"files": len(ents), "bytes": sum(e.stat().st_size for e in ents)}
except OSError:
    downloads = None
try:
    sv = os.statvfs(runtime)
    tmpfs = (sv.f_blocks - sv.f_bfree) * sv.f_frsize
except OSError:
    tmpfs = None
vm = nums(rd("/proc/vmstat"), " ")
me = (rd("/proc/self/stat") or "")
mf = me[me.rindex(")") + 2:].split() if ")" in me else []
print(json.dumps({"meminfo": nums(rd("/proc/meminfo"), ":"), "psi_memory": psi(rd("/proc/pressure/memory")), "oom_kill": vm.get("oom_kill"),
                  "pswpin": vm.get("pswpin"), "pswpout": vm.get("pswpout"), "procs": out_procs, "others_top": others, "runtime": runtime,
                  "files": {"out": fsize(bd + "/out"), "err": fsize(bd + "/err")}, "bare_dir": bare_dir, "downloads": downloads,
                  "runtime_tmpfs_used_bytes": tmpfs,
                  "dirs": {"executor_state": tree_bytes(u.pw_dir + "/.local/state/deck-executor"), "home_cache": tree_bytes(u.pw_dir + "/.cache"), "tmp": tree_bytes("/tmp")},
                  "self_cpu_ticks": int(mf[11]) + int(mf[12]) if len(mf) > 12 else None}))
'''


# ---------- host sampling ----------

def tree_sample(pid):
    out = []
    for q in v.tree_pids(pid):
        s = benchmark.sample(q)
        if s is None:
            continue
        sm = s.get("smaps_rollup") or {}
        out.append({"pid": s["pid"], "name": s["name"], "start_ticks": s["start_ticks"], "cpu_ticks": s["cpu_ticks"], "rss_kib": s["rss_kib"], "threads": s["threads"],
                    "pss_kib": sm.get("Pss"), "pss_anon_kib": sm.get("Pss_Anon"), "pss_file_kib": sm.get("Pss_File"), "pss_shmem_kib": sm.get("Pss_Shmem"), "swap_kib": sm.get("Swap")})
    return out


def cg_memory(pid):
    cg = v.cgroup_dir(pid)
    if cg is None:
        return None
    def rd(name):
        try:
            return (cg / name).read_text()
        except OSError:
            return None  # not delegated or gone: unavailable, not zero
    def kv(text):
        return None if text is None else {k: int(x) for k, x in (line.split() for line in text.splitlines())}
    cur, stat, ev = rd("memory.current"), kv(rd("memory.stat")), kv(rd("memory.events"))
    return {"current": int(cur) if cur else None, "events": ev, "cpu_usage_usec": v.cpu_usec(cg),
            "stat": {k: stat.get(k) for k in ("anon", "file", "shmem", "file_mapped", "kernel", "slab", "pgfault", "pgmajfault")} if stat else None}


def sample_once(ctx):
    ctx.sample_n += 1
    rec = {"wall_ns": time.time_ns(), "mono": time.monotonic(), "boot": ctx.boot, "phase": ctx.phase, "psi": cb.psi(), "swap": cb.vmstat_swap(),
           "mem_available_kib": cb.mem_available_kib(), "load": os.getloadavg(), "observer_cpu_s": time.thread_time(), "harness_cpu_s": time.process_time(),
           "vm": None, "guest": None}
    p = ctx.current
    vm = v.read_vm(p) if p is not None and p.vm.exists() else None
    if vm and v.proc_ok(p, vm["pid"]):
        procs = tree_sample(vm["pid"])
        rec["vm"] = {"pid": vm["pid"], "procs": procs, "rss_kib": sum(x["rss_kib"] for x in procs), "pss_kib": _sum([x["pss_kib"] for x in procs]),
                     "swap_kib": cb.vm_swap_kib(vm["pid"]), "cg": cg_memory(vm["pid"])}
        rec["overlay_allocated_bytes"] = v.allocated_bytes(p.disk) if p.disk.exists() else None
    with ctx.guest_lock:
        if ctx.guest is not None and ctx.phase in GUEST_PHASES:
            t = time.monotonic()
            code, out, err = ctx.guest.q.exec(["/usr/bin/python3", "-", ctx.dl_dir, GUEST_USER], stdin=GUEST_SCRIPT, timeout=30)
            if code != 0:
                raise RuntimeError(f"guest sample failed: {err[:200]}")
            rec["guest"] = parse_guest_sample(out)
            rec["guest"]["qga_sample_s"] = round(time.monotonic() - t, 4)  # QGA sampling overhead, labelled: not workload time
    return rec


def evaluate(ctx, rec):
    """Stop on OOM or cgroup events, guest OOM or swap, or host pressure over a 60 s window (cyclebench thresholds)."""
    if rec["phase"] != "load":
        ctx.ref = ctx.prev = None
        return
    prev, ctx.prev = ctx.prev, rec
    if ctx.ref is None or ctx.ref["boot"] != rec["boot"]:
        ctx.ref = rec
    host = None
    if rec["mono"] - ctx.ref["mono"] >= 60:
        host, ctx.ref = window_metrics(ctx.ref, rec), rec
    ev = ((rec["vm"] or {}).get("cg") or {}).get("events")
    pev = ((prev["vm"] or {}).get("cg") or {}).get("events") if prev else None
    zero = {"oom_kill": 0, "swap_used_kib": 0}
    pair = ((prev["guest"] if prev and prev["guest"] else zero), rec["guest"]) if rec["guest"] else None
    if reasons := guardrails(host, pev, ev, pair):
        ctx.stop_reasons.extend(r for r in reasons if r not in ctx.stop_reasons)
        ctx.log("guardrail", reasons=reasons, host=host, events=ev, mono=rec["mono"])


def sampler(ctx, stop):
    try:
        with (ctx.root / "samples.jsonl").open("a") as f:
            while True:
                rec = sample_once(ctx)
                f.write(json.dumps(rec) + "\n")
                f.flush()
                evaluate(ctx, rec)
                if stop.wait(ctx.args.sample_s):
                    f.write(json.dumps(sample_once(ctx)) + "\n")
                    return
    except BaseException as e:
        ctx.sampler_errors.append(repr(e))


class Ctx(cb.Ctx):
    def __init__(self, root, args):
        super().__init__(root, args)
        self.stop_reasons, self.failures, self.boot, self.phase = [], [], 0, "idle"
        self.guest, self.guest_lock, self.dl_dir, self.ref, self.prev = None, threading.Lock(), None, None, None

    def stop_reason(self):
        return (self.sampler_errors or self.stop_reasons or [None])[0]

    def set_phase(self, phase, guest=False):
        with self.guest_lock:  # waits for a guest sample in flight, so none starts while the VM saves
            self.phase = phase
            if not guest:
                self.guest = None


class QgaSerialized:
    """The sampler and the workload share one guest agent socket. Serialize their calls."""
    def __enter__(self):
        self.orig, lock = v.Qga.call, threading.RLock()
        orig = self.orig
        def call(self_, *a, **kw):
            with lock:
                return orig(self_, *a, **kw)
        v.Qga.call = call
        return self

    def __exit__(self, *exc):
        v.Qga.call = self.orig


# ---------- workload ----------

class Load:
    def __init__(self, ctx, g, dl_dir):
        self.ctx, self.g, self.dir = ctx, g, dl_dir
        self.requests, self.seconds, self.owned = {}, {}, []
        self.page = self.downloaded = self.deleted = self.sessions_max = self.retained_peak = 0

    def tool(self, name, **kw):
        self.requests[name] = self.requests.get(name, 0) + 1
        return self.g.tool(name, **kw)

    def sessions(self, limit=SESSION_CAP):
        n = count_sessions(self.tool("sessions"))
        self.sessions_max = max(self.sessions_max, n)
        check_session_cap(n)
        if n > limit:
            raise RuntimeError(f"expected at most {limit} sessions, found {n}")
        return n

    def open(self, session, url):
        r = self.tool("open", url=url, session=session, window=True, downloads_dir=self.dir)
        if not r.startswith("ok"):
            raise RuntimeError(f"open {url}: {r[:200]}")

    def link(self, label):
        return self.g.ref(self.tool("snapshot", session="work"), "link", label)

    def step(self, i, name):
        t = time.monotonic()
        getattr(self, name)()
        dt = time.monotonic() - t
        self.seconds.setdefault(name, []).append(round(dt, 3))
        self.ctx.log("op", boot=self.ctx.boot, i=i, name=name, seconds=round(dt, 3), sessions_max=self.sessions_max)

    def navigate(self):
        n = self.page % fixture.MEM_PAGES
        self.page += 1
        self.open("work", f"{s1.FIX}/mem/page/{n}")
        self.sessions(2)
        self.expect = f"Mem page {n}"

    def snapshot(self):
        if self.expect not in self.tool("snapshot", session="work"):
            raise RuntimeError("snapshot lacks the page heading")

    def read(self):
        if self.expect not in self.tool("read", session="work"):
            raise RuntimeError("read lacks the page heading")

    def download(self):
        for name in prune_plan(self.owned, fixture.MEM_DOWNLOAD_BYTES, DL_CAP_BYTES):
            cmd, = cleanup_commands(self.dir, [name])
            self.g.sh(cmd)
            self.owned = [f for f in self.owned if f["name"] != name]
            self.deleted += 1
        ref = self.link("Download file")
        reply = self.tool("act", op="click", ref=ref, session="work")
        got = [d for d in parse_downloads(reply) if owned_download(self.dir, d[0])]
        for _ in range(0 if got else 10):
            time.sleep(1)
            known = {f["path"] for f in self.owned}
            got = [d for d in parse_downloads(self.tool("downloads", session="work")) if owned_download(self.dir, d[0]) and d[0] not in known]
            if got:
                break
        if len(got) != 1:
            raise RuntimeError(f"expected one download receipt, got {got}")
        path, size = got[0]
        k, name = owned_download(self.dir, path)
        sha = self.g.sh(f"sha256sum -- {shlex.quote(path)} | cut -d' ' -f1")[1].strip()
        if size != fixture.MEM_DOWNLOAD_BYTES or sha != fixture.mem_download_sha(k):
            raise RuntimeError(f"download {name} differs from the fixture: {size} bytes, sha {sha[:12]}")
        self.owned.append({"name": name, "path": path, "size": size, "k": k})
        self.downloaded += 1
        self.retained_peak = max(self.retained_peak, sum(f["size"] for f in self.owned))

    def new_tab(self):
        self.sessions(2)
        reply = self.tool("act", op="click", ref=self.link("Open end page"), session="work")
        if not (m := re.search(r"opened tab: (\S+)", reply)):
            raise RuntimeError(f"no tab opened: {reply[:200]}")
        self.sessions(3)
        if self.tool("eval", js="document.title", session=m[1]).strip() != fixture.MEM_END_MARKER:
            raise RuntimeError("tab is not the end page")
        self.tool("close", session=m[1])
        self.sessions(2)

    def extra_session(self):
        self.sessions(2)
        n = self.page % fixture.MEM_PAGES
        self.open("extra", f"{s1.FIX}/mem/page/{n}")
        self.sessions(3)
        if f"Mem page {n}" not in self.tool("read", session="extra"):
            raise RuntimeError("extra session read lacks the page heading")
        self.tool("close", session="extra")
        self.sessions(2)

    def finish(self):
        """Park the work session on the deterministic end page."""
        self.open("work", f"{s1.FIX}/mem/end")
        if self.tool("eval", js="document.title", session="work").strip() != fixture.MEM_END_MARKER:
            raise RuntimeError("end page marker missing")
        self.sessions(2)

    def stats(self):
        med = lambda xs: round(statistics.median(xs), 3)
        return {"requests": self.requests, "requests_total": sum(self.requests.values()), "downloads": self.downloaded, "download_bytes_total": self.downloaded * fixture.MEM_DOWNLOAD_BYTES,
                "deleted_downloads": self.deleted, "retained_files": len(self.owned), "retained_bytes": sum(f["size"] for f in self.owned), "retained_peak_bytes": self.retained_peak,
                "retained_cap_bytes": DL_CAP_BYTES, "sessions_max": self.sessions_max, "session_cap": SESSION_CAP, "pages_visited": min(self.page, fixture.MEM_PAGES),
                "op_seconds": {k: {"n": len(x), "median": med(x), "max": max(x)} for k, x in self.seconds.items()}}


# ---------- one boot ----------

def discover(g):
    """Desktop user id, runtime directory and home from the running guest."""
    _, out, _ = g.sh(f"u=$(id -u {GUEST_USER}) && h=$(getent passwd {GUEST_USER} | cut -d: -f6) && echo $u $h")
    uid, home = out.split()
    if home != "/home/deck":
        raise RuntimeError(f"guest home {home} differs from the path the Stage 1 checks use")
    return {"uid": int(uid), "runtime": f"/run/user/{uid}", "home": home}


def query_migrate_parameters(p):
    """Record the effective migration limits. Nothing is changed."""
    try:
        q = v.Qmp(p.qmp)
        try:
            return {"params": q.cmd("query-migrate-parameters")}
        finally:
            q.close()
    except (OSError, RuntimeError, ValueError) as e:
        return {"unavailable": repr(e)}


def run_boot(ctx, n):
    a = ctx.args
    if time.monotonic() + a.workload_s + 240 > ctx.deadline:
        raise TimeoutError("wall budget left is too small for another boot")
    p = v.Paths(ctx.root / f"boot-{n}")
    port = a.fixture_port + 2 * n
    ctx.boot = n
    ctx.set_phase("boot")
    ctx.log("boot_begin", boot=n)
    v.create_overlay(p, ctx.base)
    ctx.current = p
    s1.fixture_start(p, port, port + 1)
    rec = {"boot": n, "failures": []}
    try:
        t0 = time.monotonic()
        v.start_qemu(p, "run", port)
        g = s1.Guest(p)
        g.q.ping_ready(120)
        g.sh("true")
        cmd_s = time.monotonic() - t0
        env = discover(g)
        s1.UID_DIR = env["runtime"]
        ready_s = cmd_s + g.desktop_ready()
        ctx.log("cold_boot", boot=n, launch_to_guest_command_s=round(cmd_s, 3), launch_to_desktop_ready_s=round(ready_s, 3), guest_env=env)
        if n == 1:
            ctx.log("migrate_parameters", boot=n, **query_migrate_parameters(p))
        state = cb.prepare_session(g, p, port)
        ctx.dl_dir = f"{env['home']}/Downloads/{DL_SUB}"
        g.sh(f"install -d -o {GUEST_USER} -g {GUEST_USER} {shlex.quote(ctx.dl_dir)}")
        ctx.guest = g
        ctx.set_phase("settle", guest=True)
        ctx.log("session_ready", boot=n)
        time.sleep(a.settle_s)

        load = Load(ctx, g, ctx.dl_dir)
        begin = time.monotonic()
        ctx.set_phase("load", guest=True)
        ctx.log("window_begin", boot=n, label="workload", mono=begin, observer_cpu_s=ctx.observer_cpu(), harness_cpu_s=time.process_time(),
                workload_s=a.workload_s, pace_s=a.pace_s, round=ROUND)
        err = None
        try:
            result = run_workload(ROUND, begin + a.workload_s, load.step, time.monotonic, time.sleep, a.pace_s, MAX_OPS, stop=ctx.stop_reason)
        except WorkloadError as e:
            result, err = e.result, e
        end = time.monotonic()
        ctx.set_phase("settle", guest=True)
        ctx.log("window_end", boot=n, label="workload", mono=end, observer_cpu_s=ctx.observer_cpu(), harness_cpu_s=time.process_time())
        ctx.log("workload", boot=n, partial=err is not None, error=str(err) if err else None, window_s=round(end - begin, 2), **result, **load.stats())
        if err:
            raise err
        load.finish()
        pre = s1.checks(g, ref_nano=state["ref_nano"])
        rec["pre_save_failures"] = cb.check_failures(pre, s1.foot_crop_hash(g) == state["foot_h"], True, True)
        if rec["pre_save_failures"]:
            raise RuntimeError(f"markers failed before save: {rec['pre_save_failures']}")

        # ---- save ----
        ctx.set_phase("save")
        g.q.sh("true")
        pid = v.read_vm(p)["pid"]
        owned, cg_dir = v.tree_pids(pid), v.cgroup_dir(pid)
        ctx.pre_quit = None
        i0, x, t0 = len(ctx.events), cb.snap(ctx), time.monotonic()
        saved = v.save_vm(p, qga=g.q)
        t1, y = time.monotonic(), cb.snap(ctx)
        events = ctx.events[i0:]
        left = [q for q in owned if v.alive(q)]
        rec["save"] = {"harness": saved, "phases": cb.save_phases(events, t0, t1, saved), "host": cb.host_delta(x, y), "cg_pre_quit": ctx.pre_quit,
                       "vm_gone": not left, "helpers_left": left, "scope_gone": cg_dir is None or not cg_dir.exists(),
                       "state_file": {"size_bytes": p.statefile.stat().st_size, "allocated_bytes": v.allocated_bytes(p.statefile), "page_cache": cb.fincore(p.statefile)},
                       "overlay_allocated_bytes": v.allocated_bytes(p.disk)}
        ctx.log("save_done", boot=n, **rec["save"])

        # ---- restore, then the first bare action, timed apart from the first guest echo ----
        ctx.set_phase("restore")
        i1, x, t0 = len(ctx.events), cb.snap(ctx), time.monotonic()
        restored = v.restore_vm(p, port)
        t1, y = time.monotonic(), cb.snap(ctx)
        rec["restore"] = {"harness": restored, "phases": cb.restore_phases(ctx.events[i1:], t0, t1, restored), "host": cb.host_delta(x, y)}
        g2 = s1.Guest(p)
        g2.n = g.n + 10 ** 4
        url, first_bare_s = cb.timed(lambda: g2.tool("eval", js="location.href"))
        _, shell2_s = cb.timed(lambda: g2.sh("true"))
        ready_s = g2.desktop_ready(30)
        rec["restore"]["probes"] = {"restore_call_to_first_guest_echo_s": rec["restore"]["phases"]["to_first_agent_shell_s"], "first_bare_action_s": first_bare_s,
                                    "first_bare_action": "eval location.href, session main, after the restore call returned", "second_agent_shell_s": shell2_s,
                                    "desktop_ready_s": round(ready_s, 4), "order": "first bare action, second shell, desktop check"}
        ctx.guest = g2
        ctx.set_phase("post", guest=True)

        # ---- markers after restore ----
        post = s1.checks(g2, ref_nano=state["ref_nano"])
        same = s1.foot_crop_hash(g2) == state["foot_h"]
        local = p.art / "report.csv"
        g2.q.pull(f"{env['home']}/Downloads/report.csv", str(local))
        transfer = hashlib.sha256(local.read_bytes()).hexdigest() == s1.CSV_SHA
        fixture_ok = g2.sh(f"curl -s -m 5 {s1.FIX}/health")[1] == "ok"
        rec["post"], rec["terminal_pixels_same"] = post, same
        rec["failures"] = cb.check_failures(post, same, transfer, fixture_ok)
        bad = [f["name"] for f in load.owned
               if g2.sh(f"sha256sum -- {shlex.quote(f['path'])} | cut -d' ' -f1")[1].strip() != fixture.mem_download_sha(f["k"])]
        rec["retained_downloads_checked"], rec["retained_downloads_bad"] = len(load.owned), bad
        rec["failures"] += [f"download_sha:{x}" for x in bad]
        count = count_sessions(g2.tool("sessions"))
        rec["sessions_after_restore"] = count
        if count > SESSION_CAP:
            rec["failures"].append("session_cap")
        if g2.tool("eval", js="document.title", session="work").strip() != fixture.MEM_END_MARKER:
            rec["failures"].append("end_page_marker")
        if not rec["save"]["vm_gone"]:
            rec["failures"].append("vm_not_gone_after_save")
        if rec["save"]["helpers_left"] or not rec["save"]["scope_gone"]:
            rec["failures"].append("helpers_or_scope_left_after_save")
        ph_s, ph_r = rec["save"]["phases"], rec["restore"]["phases"]
        if not (ph_s["complete"] and ph_r["complete"] and ph_s["harness_agrees"] and ph_r["harness_agrees"]):
            rec["failures"].append("timing_incomplete")
        names = g2.sh(f"ls -1 {shlex.quote(ctx.dl_dir)}")[1].split("\n")
        for cmd in cleanup_commands(ctx.dl_dir, names):  # owned synthetic files only
            g2.sh(cmd)
        rec["downloads_left_after_cleanup"] = g2.sh(f"ls -1 {shlex.quote(ctx.dl_dir)} | wc -l")[1].strip()
        if rec["failures"]:
            raise RuntimeError(f"boot {n} failed gates: {rec['failures']}")
    except BaseException as e:
        rec["error"] = repr(e)
        rec["traceback"] = traceback.format_exc()
        raise
    finally:
        ctx.set_phase("idle")
        vm = v.read_vm(p) if p.vm.exists() else None
        final = {}
        if vm and v.proc_ok(p, vm["pid"]):
            tree, cg = v.tree_pids(vm["pid"]), v.cgroup_dir(vm["pid"])
            v.kill_vm(p)
            final = {"killed_running_vm": True, "vm_left": [q for q in tree if v.alive(q)], "scope_gone": cg is None or not cg.exists()}
        s1.fixture_stop(p)
        rec["final"] = final or {"killed_running_vm": False}
        rec["overlay_allocated_bytes_final"] = v.allocated_bytes(p.disk) if p.disk.exists() else None
        ctx.current = None
        ctx.failures += rec["failures"]
        ctx.log("boot_result", **rec)
        ctx.log("boot_end", boot=n)


def cleanup_owned(ctx):
    """Stop only runtimes created by this run, after the identity check, then confirm they are gone."""
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


# ---------- run ----------

def base_digest(a):
    side = json.loads(Path(a.base).with_suffix(".json").read_text())
    return {"recorded_sha256": side["sha256"], "computed_sha256": v.sha256_file(a.base), "size_bytes": Path(a.base).stat().st_size}


def preflight(a):
    cb.check_no_other_vm()
    if shutil.disk_usage(Path(a.output).parent).free < 8 << 30:
        raise RuntimeError("less than 8 GiB free for overlays and state files")
    qv = subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    return {"base": Path(a.base).stem, "qemu": qv, "kernel": os.uname().release, "cpus": os.cpu_count(), "loadavg": os.getloadavg(), "mem_available_kib": cb.mem_available_kib(),
            "psi_before": cb.psi(), "swap_before": cb.vmstat_swap(), "codec": "none", "guest": "2 vCPU, 2 GiB, frozen base, fresh overlay per boot",
            "not_exercised": "server, executor, chat, journal and holds; real model; concurrent bots; disk-cold restore",
            "limits": "10 s samples miss short peaks; the guest sample is one QGA call whose cost is recorded; cgroup io.stat appears only when delegated"}


def write_summary(ctx, ok, error, base):
    recs = [json.loads(x) for x in (ctx.root / "trace.jsonl").read_text().splitlines()]
    boots = {}
    for r in recs:
        if r["kind"] in ("workload", "boot_result"):
            boots.setdefault(r["boot"], {}).update({k: x for k, x in r.items() if k != "kind"})
    growth = analyze(ctx.root, boots)
    (ctx.root / "summary.json").write_text(json.dumps({"ok": ok, "error": repr(error) if error else None, "stop_reasons": ctx.stop_reasons, "base": base,
                                                         "boots": [boots[k] for k in sorted(boots)], "growth": growth}, indent=1, default=str))


def run(a):
    root = Path(a.output).resolve()
    if root.is_relative_to(HERE.parents[1]):
        raise ValueError("raw evidence must stay outside the repository")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(root, 0o700)
    ctx = Ctx(root, a)
    ctx.deadline = time.monotonic() + a.max_wall_s
    before = base_digest(a)
    ctx.log("metadata", **preflight(a), base_digest_before=before, revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=HERE).strip(),
            script_sha256=cb.s1_hash(__file__), fixture_sha256=cb.s1_hash(HERE / "fixture.py"), boots=a.boots, workload_s=a.workload_s, pace_s=a.pace_s, sample_s=a.sample_s,
            max_wall_s=a.max_wall_s, round=ROUND, session_cap=SESSION_CAP, download_cap_bytes=DL_CAP_BYTES, pages=fixture.MEM_PAGES, page_bytes=fixture.MEM_PAGE_BYTES,
            download_bytes=fixture.MEM_DOWNLOAD_BYTES, download_files=fixture.MEM_DOWNLOADS)
    stop = threading.Event()
    thread = threading.Thread(target=sampler, args=(ctx, stop), daemon=True)
    thread.start()
    ctx.sampler_clock = time.pthread_getcpuclockid(thread.ident)
    error = None
    with QgaSerialized(), cb.Tracing() as tracing:
        tracing.ctx = ctx
        try:
            for n in range(1, a.boots + 1):
                run_boot(ctx, n)
        except BaseException as e:
            error = e
            ctx.log("run_error", error=repr(e), traceback=traceback.format_exc())
    stop.set()
    thread.join()
    base_ok, after = False, None
    try:
        cleanup_owned(ctx)
        after = base_digest(a)
        base_ok = before == after
        ctx.log("base_check", before=before, after=after, unchanged=base_ok)
    except Exception as e:
        ctx.retention_errors.append(repr(e))
    ok = error is None and not ctx.sampler_errors and not ctx.retention_errors and base_ok and not ctx.stop_reasons and not ctx.failures
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
    if ctx.sampler_errors:
        raise RuntimeError(f"invalid evidence: sampler failed: {ctx.sampler_errors}")
    if ctx.stop_reasons:
        raise RuntimeError(f"guardrail stop: {ctx.stop_reasons}")
    if ctx.retention_errors or not base_ok:
        raise RuntimeError(f"invalid evidence: retention={ctx.retention_errors} base_unchanged={base_ok}")
    print(f"Evidence: {root}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=cb.DEFAULT_BASE, help="frozen base qcow2")
    ap.add_argument("--output", default=str(Path.home() / ".cache/deck" / datetime.now().strftime("memorybench-%Y%m%d-%H%M%S")), help="new private directory outside Git")
    ap.add_argument("--boots", type=int, default=3, choices=range(1, 4), help="independent fresh overlays and cold boots")
    ap.add_argument("--workload-s", type=int, default=30 * 60, help="timed workload per boot")
    ap.add_argument("--pace-s", type=float, default=6.0, help="pause after each operation")
    ap.add_argument("--settle-s", type=int, default=15, help="wait after session setup before the workload")
    ap.add_argument("--sample-s", type=float, default=10.0)
    ap.add_argument("--max-wall-s", type=int, default=2 * 3600)
    ap.add_argument("--fixture-port", type=int, default=29681)
    run(ap.parse_args())
