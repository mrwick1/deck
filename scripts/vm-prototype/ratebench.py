#!/usr/bin/env python3
"""One controlled migration-bandwidth comparison on the saved-state cycle. Standard library only.

Arm A keeps QEMU's observed default (128 MiB/s). Arm B sets 512 MiB/s with QMP before each save.
Three fresh boots, four cycles each: ABBA, BAAB, ABBA. One VM at a time, codec none, synthetic data.
The harness is cyclebench: this file only sets and verifies the limit around each of its cycles.
A restored VM is a new QEMU process, so the limit is set and read back before every save, outside the timed call.
A failed set, a readback mismatch or a mislabeled arm invalidates the whole attempt. Raw output stays outside Git.
The server journal, chat and hold gates are not exercised. No production setting changes.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from datetime import datetime
from types import SimpleNamespace

import cyclebench as cb
import vmproto as v

KEY = "max-bandwidth"
ARMS = {"A": 128 << 20, "B": 512 << 20}
SCHEDULE = ["ABBA", "BAAB", "ABBA"]
TOTAL = sum(map(len, SCHEDULE))
ORIG_STEP = cb.cycle_step
METRICS = ("save.migrate_s", "save.call_s", "save.sync_s", "save.quit_to_return_s", "save.unattributed_s", "mib_s", "state_bytes",
           "state_allocated_bytes", "save.vm_cpu_s", "save.peak_rss_kib", "save.host.harness_cpu_s", "save.host.observer_cpu_s",
           "save.host.children_cpu_s", "save.host.psi_io_some_us", "restore.to_first_agent_shell_s", "post.first_bare_eval_s")


class RateError(RuntimeError):
    def __init__(self, rec):
        super().__init__(f"{KEY} readback {rec.get('after_bytes_per_s')} != target {rec.get('target_bytes_per_s')}")
        self.rec = rec


def apply_rate(open_qmp, want):
    """Query the effective limit, set it, read it back. The caller times nothing here; the wall time is only recorded."""
    t0 = time.monotonic()
    q = open_qmp()
    rec = {"target_bytes_per_s": want}
    try:
        rec["before_bytes_per_s"] = q.cmd("query-migrate-parameters").get(KEY)
        q.cmd("migrate-set-parameters", **{KEY: want})
        rec["after_bytes_per_s"] = q.cmd("query-migrate-parameters").get(KEY)
    finally:
        q.close()
    rec["apply_wall_s"] = round(time.monotonic() - t0, 4)
    if rec["after_bytes_per_s"] != want:
        raise RateError(rec)
    return rec


def rate_step(ctx, p, g_box, state, boot, n, port):
    arm = SCHEDULE[boot - 1][n - 1]
    ctx.extra = {"arm": arm, "rate": None}
    try:
        rec = apply_rate(lambda: v.Qmp(p.qmp), ARMS[arm])
    except Exception as e:
        ctx.log("rate_apply", boot=boot, cycle=n, arm=arm, ok=False, error=repr(e), **getattr(e, "rec", {}))
        raise
    ctx.log("rate_apply", boot=boot, cycle=n, arm=arm, ok=True, **rec)
    ctx.extra = {"arm": arm, "rate": rec}
    return ORIG_STEP(ctx, p, g_box, state, boot, n, port)


class RCtx(cb.Ctx):
    """cyclebench context that stamps the arm and applied rate onto each cycle record."""
    def __init__(self, root, args):
        super().__init__(root, args)
        self.extra = {}

    def log(self, kind, **data):
        super().log(kind, **({**data, **self.extra} if kind in ("cycle", "cycle_partial") else data))


def validate_cycles(cycles):
    """Every reason the arms cannot be trusted. Empty means each cycle ran at the rate its label and the schedule say."""
    problems = []
    if len(cycles) != TOTAL:
        problems.append(f"expected {TOTAL} cycles, got {len(cycles)}")
    for c in cycles:
        where = f"boot {c.get('boot')} cycle {c.get('cycle')}"
        try:
            want = SCHEDULE[c["boot"] - 1][c["cycle"] - 1]
        except (KeyError, IndexError, TypeError):
            problems.append(f"{where}: not in the schedule")
            continue
        rate = c.get("rate")
        if c.get("arm") != want:
            problems.append(f"{where}: arm {c.get('arm')!r} breaks the schedule ({want})")
        elif not rate:
            problems.append(f"{where}: no rate record")
        elif rate.get("after_bytes_per_s") != ARMS[want] or rate.get("target_bytes_per_s") != ARMS[want]:
            problems.append(f"{where}: readback {rate.get('after_bytes_per_s')} does not match arm {want}")
    return problems


def row(c):
    m, ms, sb = c["metrics"], c["metrics"].get("save.migrate_s"), c["metrics"].get("state_bytes")
    r = {"boot": c["boot"], "cycle": c["cycle"], "arm": c.get("arm"), "failures": c.get("failures"),
         "readback_bytes_per_s": (c.get("rate") or {}).get("after_bytes_per_s"), "apply_wall_s": (c.get("rate") or {}).get("apply_wall_s"),
         "mib_s": round(sb / 2**20 / ms, 2) if sb and ms else None, "save.exit_s": ((c.get("save") or {}).get("harness") or {}).get("exit_s")}
    r.update({k: m.get(k) for k in METRICS if k != "mib_s"})
    return r


def summarize_rates(cycles):
    """All cycles, nothing dropped. Per arm, per boot (B minus A medians) and first-after-boot versus later."""
    rows = [row(c) for c in cycles]
    out = {"rows": rows, "per_arm": {}, "per_boot": {}, "first_vs_later": {}}
    for arm in ARMS:
        sel = [r for r in rows if r["arm"] == arm]
        out["per_arm"][arm] = {k: cb.stats([r[k] for r in sel]) for k in METRICS}
        out["first_vs_later"][arm] = {k: {"first_after_boot": cb.stats([r[k] for r in sel if r["cycle"] == 1]),
                                          "later": cb.stats([r[k] for r in sel if r["cycle"] > 1])}
                                      for k in ("save.migrate_s", "state_bytes", "mib_s")}
    for boot in sorted({r["boot"] for r in rows}):
        out["per_boot"][boot] = {}
        for k in METRICS:
            med = {arm: cb.stats([r[k] for r in rows if r["boot"] == boot and r["arm"] == arm])["median"] for arm in ARMS}
            out["per_boot"][boot][k] = {"A": med["A"], "B": med["B"], "B_minus_A": None if None in med.values() else round(med["B"] - med["A"], 4)}
    return out


def quiet_preflight(seconds, snap=cb.snap, sleep=time.sleep, delta=cb.host_delta):
    """Watch the idle host with no QEMU running. Contention or a running QEMU stops the run; nothing waits or kills."""
    cb.check_no_other_vm()
    stub = SimpleNamespace(observer_cpu=lambda: 0.0)
    a = snap(stub)
    sleep(seconds)
    d = delta(a, snap(stub))
    cb.check_no_other_vm()
    bad = cb.contention(d)
    if bad:
        raise RuntimeError(f"host contention before the run: {bad}")
    return {"window_s": seconds, "host": d, "contention": bad}


def default_output():
    return str(Path.home() / ".cache/deck" / datetime.now().strftime("ratebench-%Y%m%d-%H%M%S"))


def read_cycles(root):
    path = Path(root) / "trace.jsonl"
    recs = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return [r for r in recs if r["kind"] == "cycle"]


def run(a):
    root, orig = Path(a.output).resolve(), cb.preflight
    saved = (cb.preflight, cb.cycle_step, cb.Ctx)

    def preflight(args):
        meta = orig(args)
        meta["quiet_preflight"] = quiet_preflight(args.preflight_s)
        meta["ratebench"] = {"arms": ARMS, "schedule": SCHEDULE, "key": KEY, "set": "before each save, outside the timed call, read back"}
        return meta
    cb.preflight, cb.cycle_step, cb.Ctx = preflight, rate_step, RCtx
    err = None
    try:
        cb.run(a)
    except BaseException as e:
        err = e
        if root.exists() and not (root / "trace.jsonl").exists():
            (root / "preflight-failure.json").write_text(json.dumps({"error": repr(e), "traceback": traceback.format_exc()}, indent=1))
    finally:
        cb.preflight, cb.cycle_step, cb.Ctx = saved
    if not root.exists():
        raise err
    cycles = read_cycles(root)
    problems = validate_cycles(cycles)
    try:
        base_ok = json.loads((root / "summary.json").read_text())["ok"]
    except (OSError, ValueError, KeyError):
        base_ok = False
    ok = err is None and base_ok and not problems and all(not c["failures"] for c in cycles)
    (root / "rate-summary.json").write_text(json.dumps({"ok": ok, "error": repr(err) if err else None, "problems": problems,
                                                        "boots_started": len({c["boot"] for c in cycles}), "cycles_done": len(cycles),
                                                        "summary": summarize_rates(cycles)}, indent=1, default=str))
    if err:
        raise err
    if problems:
        raise RuntimeError(f"invalid evidence: {problems}")
    print(f"Evidence: {root}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=cb.DEFAULT_BASE, help="frozen base qcow2")
    ap.add_argument("--output", default=default_output(), help="new private directory outside Git")
    ap.add_argument("--preflight-s", type=int, default=60, help="idle-host watch before any boot")
    ap.add_argument("--max-wall-s", type=int, default=20 * 60)
    ap.add_argument("--settle-s", type=int, default=15, help="wait after session setup before cycle 1")
    ap.add_argument("--sample-s", type=float, default=1.0)
    ap.add_argument("--fixture-port", type=int, default=29661)
    ns = ap.parse_args()
    run(SimpleNamespace(**vars(ns), deck=None, boots=len(SCHEDULE), cycles=len(SCHEDULE[0]), max_cycles=TOTAL))
