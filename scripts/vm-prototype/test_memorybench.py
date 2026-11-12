import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fixture
import memorybench as mb

HERE = Path(__file__).resolve().parent


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body, r


class FixtureBounds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_every_page_has_the_fixed_size_and_one_blank_target_link(self):
        for n in (0, 1, fixture.MEM_PAGES - 1):
            status, body, _ = get(self.port, f"/mem/page/{n}")
            self.assertEqual(status, 200)
            self.assertEqual(len(body), fixture.MEM_PAGE_BYTES)
            self.assertEqual(body.count(b'target="_blank"'), 1)
            self.assertIn(b"Download file", body)
        self.assertEqual(fixture.MEM_PAGES, 40)
        self.assertEqual(fixture.MEM_PAGE_BYTES, 32 * 1024)

    def test_downloads_are_two_mib_with_a_known_hash(self):
        for k in (0, fixture.MEM_DOWNLOADS - 1):
            status, body, r = get(self.port, f"/mem/dl/{k}")
            self.assertEqual(status, 200)
            self.assertEqual(len(body), 2 << 20)
            self.assertEqual(hashlib.sha256(body).hexdigest(), fixture.mem_download_sha(k))
            self.assertIn(f"mem-{k}.bin", r.getheader("Content-Disposition"))
        self.assertNotEqual(fixture.mem_download_sha(0), fixture.mem_download_sha(1))

    def test_routes_outside_the_bounds_are_404_and_allocate_nothing(self):
        before = fixture.mem_download.cache_info().currsize
        for path in ("/mem/page/40", "/mem/page/99", "/mem/page/100", "/mem/dl/8", "/mem/dl/99", "/mem/page/-1", "/mem/page/x", "/mem/page/1/../../etc"):
            self.assertEqual(get(self.port, path)[0], 404, path)
        self.assertLessEqual(fixture.mem_download.cache_info().currsize, max(before, fixture.MEM_DOWNLOADS))

    def test_end_page_is_deterministic_and_old_routes_still_work(self):
        self.assertIn(fixture.MEM_END_MARKER.encode(), get(self.port, "/mem/end")[1])
        self.assertEqual(get(self.port, "/files/report.csv")[1], fixture.CSV)
        self.assertEqual(get(self.port, "/health")[1], b"ok")
        self.assertIn(b"Sign in", get(self.port, "/login")[1])


class TabAndDownloadCaps(unittest.TestCase):
    def test_session_count_includes_tabs_a_page_opened(self):
        text = "main: http://x/account\nwork: http://x/mem/page/1\nwork~2: http://x/mem/end\n"
        self.assertEqual(mb.count_sessions(text), 3)
        self.assertEqual(mb.count_sessions(""), 0)

    def test_the_fourth_session_is_a_failure(self):
        mb.check_session_cap(3)
        with self.assertRaisesRegex(RuntimeError, 'session cap'):
            mb.check_session_cap(4)

    def test_receipts_come_from_the_click_reply_or_the_downloads_list(self):
        r = "downloaded: /home/deck/Downloads/membench/mem-2 (1).bin (2097152 bytes, application/octet-stream)\n*[e3] link"
        self.assertEqual(mb.parse_downloads(r), [("/home/deck/Downloads/membench/mem-2 (1).bin", 2097152)])
        self.assertEqual(mb.parse_downloads("no downloads"), [])

    def test_only_synthetic_names_in_the_owned_directory_are_owned(self):
        d = "/home/u/Downloads/membench"
        self.assertEqual(mb.owned_download(d, f"{d}/mem-3.bin"), (3, "mem-3.bin"))
        self.assertEqual(mb.owned_download(d, f"{d}/mem-3 (12).bin")[0], 3)
        for bad in (f"{d}/report.csv", f"{d}/../report.csv", "/home/u/Downloads/report.csv", f"{d}/mem-9.bin", f"{d}/sub/mem-1.bin", f"{d}/mem-1.bin; rm x"):
            self.assertIsNone(mb.owned_download(d, bad), bad)

    def test_prune_keeps_room_for_the_incoming_file_under_the_cap(self):
        files = [{"name": f"mem-{i}.bin", "size": 2 << 20} for i in range(16)]
        drop = mb.prune_plan(files, incoming=2 << 20, cap=32 << 20)
        kept = sum(f["size"] for f in files if f["name"] not in drop) + (2 << 20)
        self.assertLessEqual(kept, 32 << 20)
        self.assertEqual(drop, ["mem-0.bin"])  # oldest first, no more than needed
        self.assertEqual(mb.prune_plan(files[:3], 2 << 20, 32 << 20), [])


class WorkloadLoop(unittest.TestCase):
    def clock(self):
        t = [0.0]
        return t, (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))

    def test_stops_at_the_monotonic_deadline_with_exact_counts(self):
        t, now, sleep = self.clock()
        def step(i, name):
            t[0] += 1.0
        r = mb.run_workload(["a", "b"], deadline=20.0, step=step, now=now, sleep=sleep, pace_s=1.0, max_ops=1000)
        self.assertEqual(r["ops"], 10)  # each op costs 1 s plus 1 s pace
        self.assertEqual(r["counts"], {"a": 5, "b": 5})
        self.assertGreaterEqual(t[0], 20.0 - 1e-9)
        self.assertEqual(r["stopped"], "deadline")

    def test_op_cap_bounds_a_fast_loop(self):
        t, now, sleep = self.clock()
        r = mb.run_workload(["a"], deadline=1e9, step=lambda i, n: None, now=now, sleep=sleep, pace_s=0.0, max_ops=7)
        self.assertEqual((r["ops"], r["stopped"]), (7, "max_ops"))

    def test_a_failed_op_keeps_the_finished_counts(self):
        t, now, sleep = self.clock()
        def step(i, name):
            if i == 3:
                raise RuntimeError("bare error")
        with self.assertRaises(mb.WorkloadError) as cm:
            mb.run_workload(["a", "b"], 1e9, step, now, sleep, 0.0, 100)
        self.assertEqual(cm.exception.result["counts"], {"a": 1, "b": 1})  # the failed op is not counted as finished
        self.assertEqual(cm.exception.result["failed_op"], "a")
        self.assertIn("bare error", str(cm.exception))

    def test_a_stop_request_ends_the_loop_before_the_next_op(self):
        t, now, sleep = self.clock()
        flag = []
        def step(i, name):
            if i == 2:
                flag.append("oom")
        with self.assertRaises(mb.WorkloadError) as cm:
            mb.run_workload(["a"], 1e9, step, now, sleep, 0.0, 100, stop=lambda: flag[0] if flag else None)
        self.assertEqual(cm.exception.result["ops"], 2)
        self.assertIn("oom", str(cm.exception))


class GrowthMath(unittest.TestCase):
    def test_slope_of_a_line_is_exact(self):
        pts = [(t, 100 + 2 * t) for t in range(0, 600, 10)]
        s = mb.slope(pts)
        self.assertAlmostEqual(s["per_min"], 120.0, 6)
        self.assertEqual(s["n"], 60)

    def test_unavailable_values_are_skipped_not_zeroed(self):
        pts = [(0, 10), (10, None), (20, 30), (30, None), (40, 50)]
        self.assertEqual(mb.slope(pts)["n"], 3)
        self.assertIsNone(mb.slope([(0, None), (10, None)])["per_min"])
        self.assertEqual(mb.slope([(0, 1)])["label"], "insufficient")

    def test_label_separates_growth_plateau_and_noise(self):
        grow = [(t, 1000 + 5 * t) for t in range(0, 900, 10)]
        flat = [(t, 1000 + (3 if (t // 10) % 2 else -3)) for t in range(0, 900, 10)]
        self.assertEqual(mb.slope(grow)["label"], "growing")
        self.assertEqual(mb.slope(flat)["label"], "no_significant_trend")

    def test_summary_separates_early_and_steady_windows(self):
        samples = [{"t": t, "m": 1000 + (t if t < 300 else 300)} for t in range(0, 1800, 10)]
        out = mb.growth_summary(samples, lambda s: s["m"], early_s=300, steady_from_s=600)
        self.assertGreater(out["early"]["per_min"], 50)
        self.assertAlmostEqual(out["steady"]["per_min"], 0.0, 6)
        self.assertEqual(out["first"], 1000)
        self.assertEqual(out["last"], 1300)
        self.assertEqual(out["max"], 1300)

    def test_bytes_per_request_needs_requests(self):
        self.assertEqual(mb.per_request(1000, 10), 100.0)
        self.assertIsNone(mb.per_request(1000, 0))
        self.assertIsNone(mb.per_request(None, 5))


GUEST_TEXT = json.dumps({"meminfo": {"MemAvailable": 1000, "AnonPages": 500, "Cached": 200, "Slab": 50, "Shmem": 10, "SwapTotal": 0, "SwapFree": 0},
                         "psi_memory": None, "oom_kill": 0, "procs": [{"pid": 7, "start": 99, "comm": "bare-mcp", "rss_kib": 5000, "pss_kib": 4000}],
                         "files": {"out": {"path": "/run/user/5/bare/out", "bytes": 120}, "err": None}, "downloads": {"files": 2, "bytes": 4194304}})


class GuestSample(unittest.TestCase):
    def test_missing_counters_are_none_never_zero(self):
        g = mb.parse_guest_sample(GUEST_TEXT)
        self.assertIsNone(g["psi_memory"])
        self.assertIsNone(g["files"]["err"])
        self.assertEqual(g["bare_rss_kib"], 5000)
        self.assertEqual(g["bare_pss_kib"], 4000)
        none = mb.parse_guest_sample(json.dumps({"meminfo": {}, "procs": []}))
        self.assertIsNone(none["bare_rss_kib"])
        self.assertIsNone(none["meminfo"]["MemAvailable"])

    def test_process_identity_is_kept_so_reused_pids_are_visible(self):
        g = mb.parse_guest_sample(GUEST_TEXT)
        self.assertEqual(g["procs"][0]["start"], 99)
        self.assertEqual(mb.identity_changes([{"pid": 7, "start": 99}], [{"pid": 7, "start": 123}]), [7])
        self.assertEqual(mb.identity_changes([{"pid": 7, "start": 99}], [{"pid": 7, "start": 99}]), [])


class GuestScript(unittest.TestCase):
    def test_script_runs_and_its_output_parses(self):
        import os, pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["python3", "-", tmp, user], input=mb.GUEST_SCRIPT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        g = mb.parse_guest_sample(r.stdout)
        self.assertIsNotNone(g["meminfo"]["MemAvailable"])
        self.assertEqual(g["downloads"], {"files": 0, "bytes": 0})
        self.assertIsInstance(g["self_cpu_ticks"], int)


class ScopeGone(unittest.TestCase):
    def test_a_scope_that_disappears_late_is_reported_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "scope"
            d.mkdir()
            calls = []
            def sleep(s):
                calls.append(s)
                if len(calls) == 3:
                    d.rmdir()
            self.assertTrue(mb.wait_path_gone(d, timeout=5, sleep=sleep))
            self.assertEqual(len(calls), 3)

    def test_a_scope_that_stays_is_reported_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = [0.0]
            self.assertFalse(mb.wait_path_gone(Path(tmp), timeout=1, sleep=lambda s: t.__setitem__(0, t[0] + s), now=lambda: t[0]))
        self.assertTrue(mb.wait_path_gone(None))


class Guardrails(unittest.TestCase):
    QUIET = {"wall_s": 60, "swapout_pages": 0, "psi_memory_some_us": 1000, "psi_cpu_some_us": 100000, "mem_available_kib": 20 << 20}

    def test_quiet_window_has_no_reason(self):
        self.assertEqual(mb.guardrails(self.QUIET, None, None, None), [])

    def test_oom_and_cgroup_limit_events_stop_the_run(self):
        a = {"oom": 0, "oom_kill": 0, "max": 0, "high": 0}
        self.assertIn("cgroup_oom", mb.guardrails(self.QUIET, a, dict(a, oom_kill=1), None))
        self.assertIn("cgroup_max", mb.guardrails(self.QUIET, a, dict(a, max=1), None))
        self.assertEqual(mb.guardrails(self.QUIET, a, dict(a), None), [])

    def test_guest_oom_kill_or_swap_use_stops_the_run(self):
        g0 = {"oom_kill": 0, "swap_used_kib": 0}
        self.assertIn("guest_oom", mb.guardrails(self.QUIET, None, None, (g0, dict(g0, oom_kill=2))))
        self.assertIn("guest_swap", mb.guardrails(self.QUIET, None, None, (g0, dict(g0, swap_used_kib=4))))
        self.assertEqual(mb.guardrails(self.QUIET, None, None, (g0, dict(g0))), [])

    def test_existing_cyclebench_thresholds_apply_unchanged(self):
        self.assertIn("memory_pressure", mb.guardrails(dict(self.QUIET, psi_memory_some_us=5_000_000), None, None, None))
        self.assertEqual(mb.guardrails(dict(self.QUIET, swapout_pages=717), None, None, None), [])  # a few background pages
        self.assertIn("swap_out", mb.guardrails(dict(self.QUIET, swapout_pages=20000), None, None, None))


class EvidenceRetention(unittest.TestCase):
    def args(self, root):
        return SimpleNamespace(output=str(Path(root) / "mb"), base=__file__, deck=None, boots=1, workload_s=1, max_wall_s=600, sample_s=0.01,
                               pace_s=0.0, settle_s=0, fixture_port=29701)

    def run_with(self, boot, tmp):
        for p in (patch.object(mb, "preflight", lambda a: {"preflight": "stub"}), patch.object(mb, "base_digest", lambda a: "d" * 64),
                  patch.object(mb, "run_boot", boot), patch.object(mb, "cleanup_owned")):
            p.start()
            self.addCleanup(p.stop)
        return mb.run(self.args(tmp))

    def test_failed_boot_keeps_partial_counts_error_and_runs_cleanup(self):
        def boot(ctx, n):
            ctx.log("workload", boot=n, ops=12, counts={"navigate": 4})
            (ctx.root / f"boot-{n}").mkdir()
            (ctx.root / f"boot-{n}" / "qemu.log").write_text("evidence")
            raise RuntimeError("scripted oom stop")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "scripted oom stop"):
                self.run_with(boot, tmp)
            root = Path(tmp) / "mb"
            kinds = [json.loads(x)["kind"] for x in (root / "trace.jsonl").read_text().splitlines()]
            self.assertIn("workload", kinds)
            self.assertIn("run_error", kinds)
            self.assertEqual((root / "boot-1/qemu.log").read_text(), "evidence")
            summary = json.loads((root / "summary.json").read_text())
            self.assertFalse(summary["ok"])
            self.assertEqual(summary["boots"][0]["ops"], 12)
            mb.cleanup_owned.assert_called_once()

    def test_sampler_failure_invalidates_a_successful_run(self):
        def boot(ctx, n):
            import time
            time.sleep(0.2)
        def broken(ctx):
            raise OSError("sampler read failed")
        with tempfile.TemporaryDirectory() as tmp, patch.object(mb, "sample_once", broken):
            with self.assertRaisesRegex(RuntimeError, "sampler"):
                self.run_with(boot, tmp)
            root = Path(tmp) / "mb"
            self.assertIn("sampler read failed", json.loads((root / "evidence-status.json").read_text())["sampler_errors"][0])
            self.assertFalse(json.loads((root / "summary.json").read_text())["ok"])

    def test_a_sampler_that_died_before_its_clock_was_read_is_still_a_sampler_failure(self):
        def broken(ctx):
            raise OSError("sampler read failed")
        def boot(ctx, n):
            ctx.log("cold_boot", boot=n)
            ctx.observer_cpu()
        with tempfile.TemporaryDirectory() as tmp, patch.object(mb, "sample_once", broken), \
                patch.object(mb.time, "pthread_getcpuclockid", side_effect=ProcessLookupError):
            with self.assertRaisesRegex(RuntimeError, "sampler"):
                self.run_with(boot, tmp)

    def test_a_guardrail_stop_invalidates_the_run_and_is_recorded(self):
        def boot(ctx, n):
            ctx.stop_reasons.append("cgroup_oom")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "cgroup_oom"):
                self.run_with(boot, tmp)
            self.assertFalse(json.loads((Path(tmp) / "mb/summary.json").read_text())["ok"])

    def test_retention_failure_invalidates_the_run(self):
        def boot(ctx, n):
            pass
        with tempfile.TemporaryDirectory() as tmp, patch.object(mb, "write_summary", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "retention"):
                self.run_with(boot, tmp)

    def test_output_inside_the_repository_is_refused(self):
        a = self.args(tempfile.gettempdir())
        a.output = str(HERE / "inside-repo")
        with patch.object(mb, "preflight", lambda a: {}):
            with self.assertRaisesRegex(ValueError, "outside"):
                mb.run(a)
        self.assertFalse((HERE / "inside-repo").exists())

    def test_existing_output_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.args(tmp)
            Path(a.output).mkdir()
            with self.assertRaises(FileExistsError):
                mb.run(a)


class OwnedCleanup(unittest.TestCase):
    def test_cleanup_deletes_only_owned_synthetic_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Downloads" / "membench"
            d.mkdir(parents=True)
            keep = [d.parent / "report.csv", d / "notes.txt"]
            own = [d / "mem-1.bin", d / "mem-2 (1).bin"]
            for f in keep + own:
                f.write_text("x")
            cmds = mb.cleanup_commands(str(d), [f.name for f in d.iterdir()])
            self.assertEqual(sorted(c.split("/")[-1].strip("'\"") for c in cmds), sorted(f.name for f in own))
            self.assertTrue(all(str(d) in c for c in cmds))

    def test_cleanup_owned_stops_only_this_runs_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = mb.cb.Ctx(Path(tmp), SimpleNamespace(base=None))
            (Path(tmp) / "boot-1").mkdir()
            with patch.object(mb, "qemu_pids", lambda: [1]):
                mb.cleanup_owned(ctx)
            last = json.loads((Path(tmp) / "trace.jsonl").read_text().splitlines()[-1])
            self.assertEqual(last["kind"], "cleanup")
            self.assertEqual(last["vm_left"], [])


class Safety(unittest.TestCase):
    def test_guest_paths_are_discovered_not_hardcoded(self):
        src = (HERE / "memorybench.py").read_text()
        self.assertNotRegex(src, r"\b100[01]\b")
        self.assertNotRegex(src, r"/run/user/\d")

    def test_only_the_bare_browser_is_used(self):
        src = (HERE / "memorybench.py").read_text().lower()
        for bad in ("chromium", "playwright", "puppeteer", "selenium", "xvfb", "vnc"):
            self.assertNotIn(bad, src)

    def test_help_starts_no_vm(self):
        out = subprocess.run([sys.executable, str(HERE / "memorybench.py"), "--help"], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0)
        self.assertIn("--workload-s", out.stdout)


if __name__ == "__main__":
    unittest.main()
