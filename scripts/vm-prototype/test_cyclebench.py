import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cyclebench as cb

HERE = Path(__file__).resolve().parent


def ev(name, t0, t1, **extra):
    return {'name': name, 't0': t0, 't1': t1, **extra}


def save_events():
    return [ev('qga.sh', 100.0, 100.4, arg='sync'), ev('qmp.migrate', 100.5, 100.51),
            ev('qmp.query-migrate', 101.0, 101.001, status='active'),
            ev('qmp.query-migrate', 103.0, 103.001, status='completed'),
            ev('qmp.quit', 103.1, 103.12)]


def restore_events():
    return [ev('start_qemu', 200.0, 200.3), ev('qmp.migrate-incoming', 200.35, 200.36),
            ev('qmp.query-status', 201.0, 201.001, status='paused'),
            ev('qmp.query-status', 202.4, 202.5, status='running'),
            ev('qga.ping_ready', 202.5, 204.0)]


class TimingLabels(unittest.TestCase):
    def test_save_phases_come_from_event_boundaries_and_leave_a_residual(self):
        p = cb.save_phases(save_events(), call_t0=99.9, call_t1=104.0, result={'total_s': 4.1})
        self.assertTrue(p['complete'])
        self.assertAlmostEqual(p['sync_s'], 0.4, 3)
        self.assertAlmostEqual(p['migrate_s'], 2.501, 3)
        self.assertAlmostEqual(p['quit_to_return_s'], 0.9, 3)
        self.assertAlmostEqual(p['call_s'], 4.1, 3)
        parts = p['sync_s'] + p['migrate_s'] + p['quit_to_return_s']
        self.assertAlmostEqual(p['unattributed_s'], p['call_s'] - parts, 3)
        self.assertGreaterEqual(p['unattributed_s'], 0)

    def test_restore_never_labels_task_time_as_pure_vm_restore(self):
        p = cb.restore_phases(restore_events(), call_t0=200.0, call_t1=204.2, result={'to_usable_s': 4.2, 'load_s': 2.5})
        self.assertTrue(p['complete'])
        self.assertAlmostEqual(p['spawn_s'], 0.3, 3)
        self.assertAlmostEqual(p['incoming_load_s'], 2.15, 3)
        self.assertAlmostEqual(p['to_running_s'], 2.5, 3)
        self.assertAlmostEqual(p['agent_wait_s'], 1.5, 3)
        self.assertAlmostEqual(p['to_first_agent_shell_s'], 4.2, 3)
        self.assertFalse([k for k in p if 'pure' in k or k == 'vm_restore_s'])

    def test_missing_boundary_is_incomplete_not_zero(self):
        events = [e for e in restore_events() if e['name'] != 'qga.ping_ready']
        p = cb.restore_phases(events, call_t0=200.0, call_t1=204.2, result={'to_usable_s': 4.2})
        self.assertFalse(p['complete'])
        self.assertIsNone(p['agent_wait_s'])
        p = cb.save_phases([e for e in save_events() if e['name'] != 'qmp.quit'], 99.9, 104.0, {'total_s': 4.1})
        self.assertFalse(p['complete'])
        self.assertIsNone(p['quit_to_return_s'])

    def test_harness_total_that_disagrees_with_the_observed_call_is_flagged(self):
        p = cb.restore_phases(restore_events(), call_t0=200.0, call_t1=204.2, result={'to_usable_s': 9.0})
        self.assertFalse(p['harness_agrees'])


class CorruptedMarkers(unittest.TestCase):
    GOOD = {k: True for k in cb.REQUIRED_POST}

    def test_clean_cycle_has_no_failures(self):
        self.assertEqual(cb.check_failures(dict(self.GOOD), terminal_same=True, transfer_ok=True, fixture_ok=True), [])

    def test_each_corrupted_marker_is_named(self):
        for key in cb.REQUIRED_POST:
            post = dict(self.GOOD, **{key: False})
            self.assertIn(key, cb.check_failures(post, True, True, True))

    def test_missing_key_after_bare_error_is_a_failure_not_a_pass(self):
        post = {k: True for k in cb.REQUIRED_POST if k != 'bare_cookie'}
        post.update(bare_error='bare eval failed')
        self.assertIn('bare_cookie', cb.check_failures(post, True, True, True))

    def test_screen_transfer_and_fixture_failures(self):
        failed = cb.check_failures(dict(self.GOOD), terminal_same=False, transfer_ok=False, fixture_ok=False)
        self.assertEqual(sorted(failed), ['fixture_read', 'terminal_pixels', 'transfer_sha'])

    def test_loop_stops_at_the_first_corrupted_cycle(self):
        calls = []
        def step(n):
            calls.append(n)
            return {'cycle': n, 'failures': ['disk_marker'] if n == 2 else []}
        out = cb.run_cycles(5, step, deadline=1e9, now=lambda: 0)
        self.assertEqual(calls, [1, 2])
        self.assertEqual([c['cycle'] for c in out], [1, 2])

    def test_loop_keeps_the_cycle_that_raised(self):
        def step(n):
            if n == 2:
                raise RuntimeError('qemu died')
            return {'cycle': n, 'failures': []}
        with self.assertRaises(cb.CycleError) as caught:
            cb.run_cycles(5, step, deadline=1e9, now=lambda: 0)
        self.assertEqual([c['cycle'] for c in caught.exception.done], [1])
        self.assertIn('qemu died', str(caught.exception))

    def test_loop_stops_at_the_deadline(self):
        clock = iter([0, 10, 20, 30])
        out = cb.run_cycles(5, lambda n: {'cycle': n, 'failures': []}, deadline=15, now=lambda: next(clock))
        self.assertEqual(len(out), 2)


class Summaries(unittest.TestCase):
    def cycles(self):
        data = {1: [10, 2, 2, 2.5, 2], 2: [12, 3, 3, 3.5, 3], 3: [11, 2.5, 2.5, 3, 2.5]}
        return [{'boot': b, 'cycle': i + 1, 'metrics': {'save.migrate_s': x}} for b, xs in data.items() for i, x in enumerate(xs)]

    def test_first_after_boot_is_separate_from_later_cycles(self):
        m = cb.summarize(self.cycles())['save.migrate_s']
        self.assertEqual(m['first_after_boot']['median'], 11)
        self.assertEqual(m['first_after_boot']['n'], 3)
        self.assertEqual(m['later']['n'], 12)
        self.assertEqual(m['later']['median'], 2.5)
        self.assertEqual((m['later']['min'], m['later']['max']), (2, 3.5))

    def test_within_and_between_boot_variation_are_separate(self):
        m = cb.summarize(self.cycles())['save.migrate_s']
        self.assertEqual(m['within_boot_range_median'], 0.5)
        self.assertEqual(m['between_boot_median_range'], 1.0)

    def test_none_values_are_counted_not_averaged(self):
        c = self.cycles()
        c[1]['metrics']['save.migrate_s'] = None
        m = cb.summarize(c)['save.migrate_s']
        self.assertEqual(m['later']['n'], 11)
        self.assertEqual(m['missing'], 1)


class EvidenceRetention(unittest.TestCase):
    def args(self, root, **kw):
        return SimpleNamespace(output=str(Path(root) / 'cb'), base=__file__, deck=None, boots=1, cycles=2,
                               max_wall_s=600, max_cycles=15, sample_s=0.01, **kw)

    def run_with(self, boot, tmp, **kw):
        patches = [patch.object(cb, 'preflight', lambda a: {'preflight': 'stub'}), patch.object(cb, 'base_unchanged', lambda a: True),
                   patch.object(cb, 'run_boot', boot)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return cb.run(self.args(tmp, **kw))

    def test_failed_boot_retains_partial_cycles_error_and_cleanup(self):
        def boot(ctx, n):
            ctx.log('cycle', boot=n, cycle=1, failures=[], metrics={})
            (ctx.root / f'boot-{n}').mkdir()
            (ctx.root / f'boot-{n}' / 'qemu.log').write_text('evidence')
            raise RuntimeError('scripted boot failure')
        with tempfile.TemporaryDirectory() as tmp, patch.object(cb, 'cleanup_owned') as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'scripted boot failure'):
                self.run_with(boot, tmp)
            root = Path(tmp) / 'cb'
            kinds = [json.loads(line)['kind'] for line in (root / 'trace.jsonl').read_text().splitlines()]
            self.assertIn('cycle', kinds)
            self.assertIn('run_error', kinds)
            self.assertEqual((root / 'boot-1/qemu.log').read_text(), 'evidence')
            summary = json.loads((root / 'summary.json').read_text())
            self.assertFalse(summary['ok'])
            self.assertIn('scripted boot failure', summary['error'])
            self.assertEqual(len(summary['cycles']), 1)
            cleanup.assert_called_once()

    def test_sampler_failure_invalidates_a_successful_run(self):
        def boot(ctx, n):
            import time
            ctx.log('cycle', boot=n, cycle=1, failures=[], metrics={'save.migrate_s': 1.0})
            time.sleep(0.2)
        def broken(ctx):
            raise OSError('sampler read failed')
        with tempfile.TemporaryDirectory() as tmp, patch.object(cb, 'cleanup_owned'), patch.object(cb, 'sample_once', broken):
            with self.assertRaisesRegex(RuntimeError, 'sampler'):
                self.run_with(boot, tmp)
            root = Path(tmp) / 'cb'
            status = json.loads((root / 'evidence-status.json').read_text())
            self.assertIn('sampler read failed', status['sampler_errors'][0])
            self.assertFalse(json.loads((root / 'summary.json').read_text())['ok'])

    def test_sampler_clock_lookup_race_is_reported_and_cleanup_runs(self):
        def boot(ctx, n):
            ctx.sampler_errors.append('sampler died during startup')
        with tempfile.TemporaryDirectory() as tmp, patch.object(cb, 'cleanup_owned') as cleanup, patch.object(cb.time, 'pthread_getcpuclockid', side_effect=ProcessLookupError):
            with self.assertRaisesRegex(RuntimeError, 'sampler'):
                self.run_with(boot, tmp)
            cleanup.assert_called_once()
            self.assertTrue((Path(tmp) / 'cb/evidence-status.json').exists())

    def test_successful_run_records_samples_and_a_valid_status(self):
        def boot(ctx, n):
            import time
            ctx.log('cycle', boot=n, cycle=1, failures=[], metrics={'save.migrate_s': 1.0})
            time.sleep(0.15)
        with tempfile.TemporaryDirectory() as tmp, patch.object(cb, 'cleanup_owned'):
            self.run_with(boot, tmp)
            root = Path(tmp) / 'cb'
            self.assertTrue(json.loads((root / 'summary.json').read_text())['ok'])
            samples = [json.loads(x) for x in (root / 'samples.jsonl').read_text().splitlines()]
            self.assertGreaterEqual(len(samples), 2)
            self.assertIn('psi', samples[0])
            self.assertIn('observer_cpu_s', samples[-1])
            self.assertEqual(json.loads((root / 'evidence-status.json').read_text())['sampler_errors'], [])

    def test_output_inside_the_repository_is_refused(self):
        a = self.args(tempfile.gettempdir())
        a.output = str(HERE / 'inside-repo')
        with patch.object(cb, 'preflight', lambda a: {}):
            with self.assertRaisesRegex(ValueError, 'outside'):
                cb.run(a)
        self.assertFalse((HERE / 'inside-repo').exists())

    def test_existing_output_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.args(tmp)
            Path(a.output).mkdir()
            with self.assertRaises(FileExistsError):
                cb.run(a)


class Safety(unittest.TestCase):
    def test_preflight_refuses_when_another_qemu_runs(self):
        with patch.object(cb, 'qemu_pids', lambda: [4242]):
            with self.assertRaisesRegex(RuntimeError, 'another qemu'):
                cb.check_no_other_vm()

    def test_contention_flags_swap_and_memory_pressure_but_not_quiet_hosts(self):
        quiet = {'wall_s': 30, 'swapout_pages': 0, 'psi_memory_some_us': 1000, 'psi_cpu_some_us': 100000, 'mem_available_kib': 20 << 20}
        self.assertEqual(cb.contention(quiet), [])
        self.assertEqual(cb.contention(dict(quiet, swapout_pages=717)), [])  # background host paging, 2.8 MiB, measured in run 1
        self.assertIn('swap_out', cb.contention(dict(quiet, swapout_pages=20000)))
        self.assertIn('vm_swapped', cb.contention(dict(quiet, vm_swap_kib=4)))
        self.assertIn('memory_pressure', cb.contention(dict(quiet, psi_memory_some_us=5_000_000)))
        self.assertIn('low_memory', cb.contention(dict(quiet, mem_available_kib=1 << 20)))

    def test_help_starts_no_vm(self):
        out = subprocess.run([sys.executable, str(HERE / 'cyclebench.py'), '--help'], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0)
        self.assertIn('--boots', out.stdout)


if __name__ == '__main__':
    unittest.main()
