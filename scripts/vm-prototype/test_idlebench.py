import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import idlebench as ib

HERE = Path(__file__).resolve().parent
HZ = 100


def thread(tid, start=5, comm="CPU 0/KVM", utime=0, stime=0, vol=0, nonvol=0):
    return {"tid": tid, "comm": comm, "start": start, "utime": utime, "stime": stime, "vol": vol, "nonvol": nonvol}


def host(mono, threads, pid=10, start=1, helpers=(), io=None, observer=0.0):
    return {"mono": mono, "qemu": {"pid": pid, "start": start, "comm": "qemu-system-x86"}, "threads": list(threads),
            "helpers": list(helpers), "io": io, "observer_cpu_s": observer}


class HostDelta(unittest.TestCase):
    def test_cpu_and_switches_come_from_tid_identity_and_stay_separate(self):
        a = host(0, [thread(10, comm="qemu-main", utime=10, stime=5, vol=100, nonvol=7), thread(11, utime=0, vol=3, nonvol=1)],
                 helpers=[{"pid": 20, "start": 2, "comm": "virtiofsd", "utime": 1, "stime": 1}], io={"read_bytes": 5, "write_bytes": 9}, observer=0.5)
        b = host(60, [thread(10, comm="qemu-main", utime=40, stime=15, vol=160, nonvol=9), thread(11, utime=6, vol=3, nonvol=2)],
                 helpers=[{"pid": 20, "start": 2, "comm": "virtiofsd", "utime": 4, "stime": 1}], io={"read_bytes": 5, "write_bytes": 19}, observer=0.75)
        d = ib.host_delta(a, b, HZ)
        self.assertTrue(d["valid"], d)
        main = next(t for t in d["threads"] if t["tid"] == 10)
        self.assertEqual((main["cpu_s"], main["vol"], main["nonvol"]), (0.4, 60, 2))
        self.assertAlmostEqual(main["cpu_percent"], 100 * 0.4 / 60, places=3)
        self.assertEqual(next(t for t in d["threads"] if t["tid"] == 11)["vol"], 0)
        self.assertEqual(d["helpers_cpu_s"], 0.03)  # helpers are not folded into the QEMU threads
        self.assertEqual(d["qemu_cpu_s"], 0.46)
        self.assertEqual(d["io"], {"read_bytes": 0, "write_bytes": 10})
        self.assertEqual(d["observer_cpu_s"], 0.25)
        self.assertEqual(d["rejected"], [])

    def test_a_reused_tid_is_rejected_and_never_gives_a_negative_delta(self):
        a = host(0, [thread(11, start=5, utime=500, vol=900)])
        b = host(60, [thread(11, start=77, utime=2, vol=1)])  # same TID, new start time: another thread
        d = ib.host_delta(a, b, HZ)
        self.assertEqual([r["tid"] for r in d["rejected"]], [11])
        self.assertEqual(d["rejected"][0]["reason"], "tid_reused")
        self.assertEqual(d["threads"], [])
        self.assertTrue(all(t["cpu_s"] >= 0 for t in d["threads"]))

    def test_a_counter_that_went_backwards_is_rejected_not_negative(self):
        d = ib.host_delta(host(0, [thread(11, utime=50)]), host(60, [thread(11, utime=10)]), HZ)
        self.assertEqual(d["rejected"][0]["reason"], "counter_decreased")
        self.assertEqual(d["threads"], [])

    def test_a_restarted_qemu_invalidates_the_whole_delta(self):
        for b in (host(60, [thread(10, utime=1)], pid=99), host(60, [thread(10, utime=1)], start=2)):
            d = ib.host_delta(host(0, [thread(10, utime=900)]), b, HZ)
            self.assertFalse(d["valid"])
            self.assertIn("qemu_identity_changed", d["reasons"])
            self.assertEqual(d["threads"], [])

    def test_a_missing_switch_counter_is_unavailable_not_zero(self):
        a = host(0, [thread(10, utime=1, vol=None, nonvol=4)])
        b = host(60, [thread(10, utime=2, vol=None, nonvol=9)])
        t = ib.host_delta(a, b, HZ)["threads"][0]
        self.assertIsNone(t["vol"])
        self.assertEqual(t["nonvol"], 5)
        self.assertEqual(t["cpu_s"], 0.01)

    def test_threads_that_appear_or_vanish_are_listed_not_summed(self):
        d = ib.host_delta(host(0, [thread(10, utime=1), thread(12, utime=1)]), host(60, [thread(10, utime=2), thread(13, utime=50)]), HZ)
        self.assertEqual((d["vanished"], d["appeared"]), ([12], [13]))
        self.assertEqual([t["tid"] for t in d["threads"]], [10])

    def test_reading_a_live_process_gives_identity_and_both_switch_counters(self):
        e = ib.host_edge(os.getpid(), None, observer=lambda: 0.0)
        self.assertEqual(e["qemu"]["pid"], os.getpid())
        main = next(t for t in e["threads"] if t["tid"] == os.getpid())
        for key in ("comm", "start", "utime", "stime", "vol", "nonvol"):
            self.assertIsNotNone(main[key], key)


def gsample(uptime=100.0, boot="b1", intr=None, procs=None, stat=None, block=None, **kw):
    return {"uptime": uptime, "boot_id": boot, "hz": HZ, "clocksource": "kvm-clock",
            "stat": stat or {"cpu": [10, 0, 5, 800, 1, 0, 2, 0, 0, 0], "cpus": {"cpu0": [5, 0, 2, 400, 0, 0, 1, 0, 0, 0]}, "intr": 1000, "ctxt": 5000, "softirq": 300},
            "softirqs": {"TIMER": 10, "RCU": 5}, "interrupts": intr if intr is not None else {"0: IO-APIC timer": [100, 20], "NMI: Non-maskable": [0, 0]},
            "block": block, "procs": procs or [], "self_ticks": 3, **kw}


def gproc(pid, role, start=7, ticks=0, vol=0, nonvol=0, comm=None, port=None):
    return {"pid": pid, "start": start, "comm": comm or role, "role": role, "ticks": ticks, "vol": vol, "nonvol": nonvol, "executor_port": port}


class GuestDelta(unittest.TestCase):
    def test_interrupt_and_cpu_counters_are_differenced(self):
        a = gsample(100.0)
        b = gsample(160.0, intr={"0: IO-APIC timer": [160, 80], "NMI: Non-maskable": [0, 0]},
                    stat={"cpu": [40, 0, 15, 830, 1, 0, 2, 0, 0, 0], "cpus": {"cpu0": [20, 0, 6, 430, 0, 0, 1, 0, 0, 0]}, "intr": 1500, "ctxt": 6000, "softirq": 360})
        d = ib.guest_delta(a, b)
        self.assertTrue(d["valid"], d)
        self.assertEqual(d["seconds"], 60.0)
        self.assertEqual(d["interrupts"]["0: IO-APIC timer"], 120)
        self.assertEqual(d["interrupts"]["NMI: Non-maskable"], 0)
        self.assertEqual((d["intr"], d["ctxt"], d["softirq"]), (500, 1000, 60))
        self.assertEqual(d["cpu_jiffies"]["user"], 30)
        self.assertEqual(d["cpus"]["cpu0"]["system"], 4)

    def test_a_missing_interrupt_row_is_unavailable_not_zero(self):
        a = gsample(100.0, intr={"0: timer": [1, 1], "24: virtio0-input": [5, 5]})
        b = gsample(160.0, intr={"0: timer": [3, 3]})  # the virtio row vanished
        d = ib.guest_delta(a, b)
        self.assertEqual(d["interrupts"], {"0: timer": 4})
        self.assertEqual(d["interrupts_unavailable"], ["24: virtio0-input"])

    def test_missing_interrupts_file_marks_all_unavailable(self):
        d = ib.guest_delta(gsample(interrupts=None, intr=None), gsample(160.0, intr=None))
        self.assertIsNone(d["interrupts"])

    def test_missing_block_stat_is_none(self):
        self.assertIsNone(ib.guest_delta(gsample(), gsample(160.0))["block"])
        d = ib.guest_delta(gsample(block=[1, 0, 8, 2, 0, 0, 0, 0]), gsample(160.0, block=[4, 0, 32, 5, 0, 0, 0, 0]))
        self.assertEqual(d["block"][0], 3)

    def test_another_boot_invalidates_the_delta(self):
        d = ib.guest_delta(gsample(boot="b1"), gsample(160.0, boot="b2"))
        self.assertFalse(d["valid"])
        self.assertIn("guest_boot_changed", d["reasons"])

    def test_counters_going_backwards_invalidate_not_negate(self):
        d = ib.guest_delta(gsample(stat={"cpu": [10] * 10, "cpus": {}, "intr": 9000, "ctxt": 5, "softirq": 1}), gsample(160.0))
        self.assertFalse(d["valid"])
        self.assertIn("counter_decreased:intr", d["reasons"])

    def test_pids_are_matched_by_start_and_comm_and_switches_stay_separate(self):
        a = gsample(procs=[gproc(5, "bare", ticks=10, vol=100, nonvol=3), gproc(6, "sway", start=8, ticks=500), gproc(7, "executor", ticks=1, port=True)])
        b = gsample(160.0, procs=[gproc(5, "bare", ticks=22, vol=130, nonvol=4), gproc(6, "sway", start=99, ticks=2), gproc(7, "executor", ticks=4, vol=60, port=True)])
        d = ib.guest_delta(a, b)
        bare = next(p for p in d["procs"] if p["role"] == "bare")
        self.assertEqual((bare["ticks"], bare["vol"], bare["nonvol"]), (12, 30, 1))
        self.assertEqual([r["pid"] for r in d["procs_rejected"]], [6])  # reused PID: start time differs
        self.assertEqual(d["procs_rejected"][0]["reason"], "pid_reused")
        self.assertEqual(next(p for p in d["procs"] if p["role"] == "executor")["vol"], 60)

    def test_the_guest_script_runs_and_reports_every_family(self):
        r = subprocess.run([sys.executable, "-I", "-"], input=ib.GUEST_SCRIPT, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        g = json.loads(r.stdout)
        for key in ("uptime", "boot_id", "stat", "softirqs", "interrupts", "procs", "self_ticks", "hz", "clocksource"):
            self.assertIn(key, g)
        self.assertGreater(g["stat"]["ctxt"], 0)
        self.assertTrue(g["stat"]["cpus"])
        self.assertTrue(all(isinstance(v, list) for v in (g["interrupts"] or {}).values()))
        # roles only, never a command line or environment
        self.assertFalse(any("cmdline" in p or "environ" in p or "argv" in p for p in g["procs"]))


def status(**kw):
    s = {"computer": "awake", "working": None, "waiting": None, "queued": 0, "unresolved": 0, "pending_events": 0, "idle_left_ms": 40000,
         "counters": {"wakes": 1, "saves": 0, "connects": 1, "model_calls": 2}, "screen": {"captures": 0, "worker": False}}
    s.update(kw)
    return s


def edge(mono, api=None, hsample=None, gs=None):
    gs = gs or gsample(100 + mono, procs=[gproc(5, "bare"), gproc(6, "sway"), gproc(7, "executor", port=True)])
    return {"mono": mono, "api": api or status(), "host": hsample or host(mono, [thread(10, utime=int(mono))]), "guest": gs}


class WindowChecks(unittest.TestCase):
    def test_a_clean_window_passes(self):
        self.assertEqual(ib.window_failures(edge(0), edge(60)), [])

    def test_every_activity_or_state_violation_is_reported(self):
        cases = {
            "not_awake": status(computer="saved"),
            "owner_command_active": status(working={"id": 1}),
            "work_queued": status(queued=1),
            "unresolved_work": status(unresolved=1),
            "events_pending": status(pending_events=2),
            "model_calls": status(counters={"wakes": 1, "saves": 0, "connects": 1, "model_calls": 3}),
            "executor_reconnected": status(counters={"wakes": 1, "saves": 0, "connects": 2, "model_calls": 2}),
            "capture": status(screen={"captures": 1, "worker": False}),
            "capture_worker_alive": status(screen={"captures": 0, "worker": True}),
            "idle_timer_expired": status(idle_left_ms=0),
        }
        for reason, end in cases.items():
            self.assertIn(reason, ib.window_failures(edge(0), edge(60, api=end)), reason)

    def test_a_different_epoch_or_executor_is_reported(self):
        procs = [gproc(5, "bare"), gproc(6, "sway"), gproc(7, "executor", start=77, port=True)]
        self.assertIn("executor_changed", ib.window_failures(edge(0), edge(60, gs=gsample(160.0, procs=procs))))
        self.assertIn("guest_boot_changed", ib.window_failures(edge(0), edge(60, gs=gsample(160.0, boot="other", procs=[gproc(5, "bare"), gproc(6, "sway"), gproc(7, "executor", port=True)]))))
        self.assertIn("qemu_identity_changed", ib.window_failures(edge(0), edge(60, hsample=host(60, [thread(10)], pid=44))))

    def test_an_unconnected_executor_is_labelled_not_assumed(self):
        gs = gsample(160.0, procs=[gproc(5, "bare"), gproc(6, "sway"), gproc(7, "executor", port=False)])
        self.assertIn("executor_not_connected", ib.window_failures(edge(0), edge(60, gs=gs)))
        gs = gsample(160.0, procs=[gproc(5, "bare"), gproc(6, "sway")])
        self.assertIn("executor_missing", ib.window_failures(edge(0), edge(60, gs=gs)))

    def test_a_missing_edge_component_makes_the_window_invalid(self):
        for part in ("host", "guest", "api"):
            bad = edge(60)
            bad[part] = None
            self.assertIn("edge_incomplete", ib.window_failures(edge(0), bad), part)

    def test_a_wrong_length_window_is_reported(self):
        self.assertIn("window_length", ib.window_failures(edge(0), edge(75)))


class Guard(unittest.TestCase):
    def rec(self, mono, out=0, cpu=0, mem=0, avail=8 << 20, swap=0):
        return {"mono": mono, "swap": {"out": out, "in": 0}, "psi": {"cpu": {"some": cpu}, "memory": {"some": mem}, "io": {"some": 0}},
                "mem_available_kib": avail, "vm": {"swap_kib": swap}}

    def test_host_swap_alone_is_context_and_does_not_stop(self):
        g = ib.Guard()
        self.assertEqual(g.feed(self.rec(0), None), [])
        self.assertEqual(g.feed(self.rec(61, out=50000), None), [])
        self.assertEqual(len(g.context_windows), 1)
        self.assertEqual(g.context_windows[0]["reason"], "host_swap_out")

    def test_actual_pressure_limits_stop(self):
        for kw, reason in (({"cpu": 30_000_000}, "cpu_pressure"), ({"mem": 4_000_000}, "memory_pressure"), ({"avail": 1 << 20}, "low_memory"), ({"swap": 4}, "vm_swapped")):
            g = ib.Guard()
            g.feed(self.rec(0), None)
            self.assertIn(reason, g.feed(self.rec(61, **kw), None), reason)

    def test_cgroup_oom_and_max_stop(self):
        g = ib.Guard()
        g.feed(self.rec(0), {"oom_kill": 0, "max": 0})
        self.assertEqual(sorted(g.feed(self.rec(10), {"oom_kill": 1, "max": 2})), ["cgroup_max", "cgroup_oom"])

    def test_pressure_below_the_limits_does_not_stop(self):
        g = ib.Guard()
        g.feed(self.rec(0), None)
        self.assertEqual(g.feed(self.rec(61, cpu=10_000_000, mem=1_000_000), None), [])


class Dominance(unittest.TestCase):
    def win(self, boot, intr, threads=None):
        return {"boot": boot, "valid": True, "guest": {"interrupts": intr}, "host": {"threads": threads or []}}

    def test_a_source_is_a_candidate_only_when_dominant_in_every_boot(self):
        wins = [self.win(1, {"timer": 90, "net": 5}), self.win(1, {"timer": 80, "net": 6}), self.win(2, {"timer": 70, "net": 9}), self.win(3, {"timer": 60, "net": 2})]
        self.assertEqual(ib.repeated_dominant(wins, "interrupt")["source"], "timer")

    def test_no_candidate_when_the_leader_changes_between_boots(self):
        wins = [self.win(1, {"timer": 90, "net": 5}), self.win(2, {"timer": 1, "net": 9}), self.win(3, {"timer": 60, "net": 2})]
        self.assertIsNone(ib.repeated_dominant(wins, "interrupt")["source"])

    def test_invalid_windows_are_ignored_and_one_boot_is_not_enough(self):
        bad = self.win(2, {"net": 99})
        bad["valid"] = False
        self.assertIsNone(ib.repeated_dominant([self.win(1, {"timer": 9}), bad], "interrupt")["source"])

    def test_thread_dominance_uses_switches_and_names_the_thread_comm(self):
        t = lambda comm, vol: [{"comm": comm, "tid": 1, "vol": vol, "nonvol": 0, "cpu_s": 0.1}]
        wins = [self.win(b, {}, t("qemu-main", 100)) for b in (1, 2, 3)]
        self.assertEqual(ib.repeated_dominant(wins, "thread")["source"], "qemu-main")


def args(out, **kw):
    d = dict(base="/nonexistent", deck="/nonexistent", output=str(out), boots=3, idle_secs=170, window_s=60, stabilize_s=10, max_wall_s=1500, preflight_s=0, sample_s=10.0)
    d.update(kw)
    return SimpleNamespace(**d)


class Evidence(unittest.TestCase):
    def run_with(self, boot, **kw):
        out = Path(tempfile.mkdtemp()) / "idlebench-test"
        with patch.object(ib.cb, "check_no_other_vm"), patch.object(ib, "base_digest", return_value={"sha": "x"}), patch.object(ib, "git_revision", return_value="abc"):
            try:
                ib.run(args(out, **kw), boot=boot)
                err = None
            except BaseException as e:
                err = e
        return out, err

    def test_a_failed_boot_keeps_its_result_stops_the_run_and_is_never_ok(self):
        calls = []
        def boot(ctx, n):
            calls.append(n)
            raise RuntimeError("stage3 gate failed")
        out, err = self.run_with(boot)
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(calls, [1])  # one attempt, no retry, no later boot
        summary = json.loads((out / "summary.json").read_text())
        self.assertFalse(summary["ok"])
        self.assertIn("stage3 gate failed", summary["error"])
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o700")
        kinds = [json.loads(l)["kind"] for l in (out / "trace.jsonl").read_text().splitlines()]
        self.assertIn("metadata", kinds)
        self.assertIn("run_error", kinds)
        self.assertIn("cleanup", kinds)

    def test_a_forced_sampler_failure_invalidates_a_run_whose_boots_succeeded(self):
        def boot(ctx, n):
            ctx.guard_errors.append("RuntimeError('forced')")
            ctx.boots.append({"boot": n, "complete": True, "windows": []})
        out, err = self.run_with(boot, boots=1)
        self.assertIsNotNone(err)
        self.assertFalse(json.loads((out / "summary.json").read_text())["ok"])
        status = json.loads((out / "evidence-status.json").read_text())
        self.assertTrue(status["sampler_errors"])

    def test_a_retention_failure_invalidates_the_run(self):
        def boot(ctx, n):
            ctx.boots.append({"boot": n, "complete": True, "windows": []})
            ctx.retention_errors.append("OSError('disk full')")
        out, err = self.run_with(boot, boots=1)
        self.assertIsNotNone(err)
        self.assertFalse(json.loads((out / "summary.json").read_text())["ok"])
        self.assertTrue(json.loads((out / "evidence-status.json").read_text())["retention_errors"])

    def test_the_deadline_stops_before_a_boot_that_cannot_finish(self):
        calls = []
        def boot(ctx, n):
            calls.append(n)
            ctx.boots.append({"boot": n, "complete": True, "windows": []})
        out, err = self.run_with(boot, boots=3, max_wall_s=100)  # a boot needs more than 100 s
        self.assertEqual(calls, [])
        self.assertIsNotNone(err)
        self.assertIn("budget", json.loads((out / "summary.json").read_text())["error"])

    def test_a_clean_run_is_ok_and_cleanup_ran(self):
        def boot(ctx, n):
            ctx.boots.append({"boot": n, "complete": True, "windows": []})
        with patch.object(ib, "cleanup_owned") as cleanup:
            out, err = self.run_with(boot, boots=2)
        self.assertIsNone(err)
        cleanup.assert_called_once()
        summary = json.loads((out / "summary.json").read_text())
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["complete_boots"], 2)

    def test_output_inside_the_repository_is_refused(self):
        with self.assertRaises(ValueError):
            ib.run(args(HERE / "inside"), boot=lambda c, n: None)


class Cli(unittest.TestCase):
    def test_help_needs_no_vm(self):
        r = subprocess.run([sys.executable, str(HERE / "idlebench.py"), "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--idle-secs", r.stdout)


if __name__ == "__main__":
    unittest.main()
