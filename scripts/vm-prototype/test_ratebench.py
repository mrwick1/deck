import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cyclebench as cb
import ratebench as rb

HERE = Path(__file__).resolve().parent
A, B = 128 << 20, 512 << 20


class FakeQmp:
    """Records commands. `keep=False` models a limit that does not stick."""
    def __init__(self, start=A, keep=True):
        self.value, self.keep, self.log, self.closed = start, keep, [], False

    def cmd(self, name, **args):
        self.log.append((name, args))
        if name == 'migrate-set-parameters' and self.keep:
            self.value = args['max-bandwidth']
        return {'max-bandwidth': self.value} if name == 'query-migrate-parameters' else {}

    def close(self):
        self.closed = True


def cycle(boot, n, arm, readback=None, **metrics):
    want = {'A': A, 'B': B}[arm]
    m = {'state_bytes': 400 << 20, 'save.migrate_s': 3.0 if arm == 'A' else 1.0}
    m.update(metrics)
    return {'boot': boot, 'cycle': n, 'arm': arm, 'failures': [], 'metrics': m,
            'save': {'harness': {'exit_s': 0.1}},
            'rate': {'target_bytes_per_s': want, 'after_bytes_per_s': want if readback is None else readback}}


def full_run():
    return [cycle(b, i + 1, arm) for b in (1, 2, 3) for i, arm in enumerate(rb.SCHEDULE[b - 1])]


class Sequence(unittest.TestCase):
    def test_schedule_is_abba_baab_abba_and_balanced(self):
        self.assertEqual(rb.SCHEDULE, ['ABBA', 'BAAB', 'ABBA'])
        self.assertEqual(sum(s.count('A') for s in rb.SCHEDULE), 6)
        self.assertEqual(sum(s.count('B') for s in rb.SCHEDULE), 6)

    def test_arms_are_exact_binary_rates(self):
        self.assertEqual(rb.ARMS, {'A': 134217728, 'B': 536870912})

    def test_apply_queries_baseline_sets_then_reads_back(self):
        q = FakeQmp()
        rec = rb.apply_rate(lambda: q, B)
        self.assertEqual([c[0] for c in q.log], ['query-migrate-parameters', 'migrate-set-parameters', 'query-migrate-parameters'])
        self.assertEqual(q.log[1][1], {'max-bandwidth': B})
        self.assertEqual((rec['before_bytes_per_s'], rec['after_bytes_per_s'], rec['target_bytes_per_s']), (A, B, B))
        self.assertIn('apply_wall_s', rec)
        self.assertTrue(q.closed)

    def test_readback_mismatch_raises_and_keeps_the_record(self):
        q = FakeQmp(keep=False)
        with self.assertRaises(rb.RateError) as caught:
            rb.apply_rate(lambda: q, B)
        self.assertEqual(caught.exception.rec['after_bytes_per_s'], A)
        self.assertTrue(q.closed)


class Step(unittest.TestCase):
    def test_apply_runs_before_the_timed_cycle_and_the_arm_follows_the_schedule(self):
        order, logged = [], []
        ctx = SimpleNamespace(log=lambda kind, **d: logged.append((kind, d)), extra={})
        p = SimpleNamespace(qmp='x')
        def fake_apply(open_qmp, want):
            order.append(('apply', want))
            return {'target_bytes_per_s': want, 'after_bytes_per_s': want}
        def inner(*a):
            order.append(('cycle', ctx.extra['arm']))
            return {'failures': []}
        with patch.object(rb, 'apply_rate', fake_apply), patch.object(rb, 'ORIG_STEP', inner):
            rb.rate_step(ctx, p, [None], {}, 2, 1, 1)
            rb.rate_step(ctx, p, [None], {}, 2, 2, 1)
        self.assertEqual(order, [('apply', B), ('cycle', 'B'), ('apply', A), ('cycle', 'A')])
        self.assertEqual([d['ok'] for k, d in logged if k == 'rate_apply'], [True, True])

    def test_failed_readback_stops_before_the_cycle_and_is_logged(self):
        logged = []
        ctx = SimpleNamespace(log=lambda kind, **d: logged.append((kind, d)), extra={})
        def bad(open_qmp, want):
            raise rb.RateError({'target_bytes_per_s': want, 'after_bytes_per_s': A})
        with patch.object(rb, 'apply_rate', bad), patch.object(rb, 'ORIG_STEP', side_effect=AssertionError('ran')):
            with self.assertRaises(rb.RateError):
                rb.rate_step(ctx, SimpleNamespace(qmp='x'), [None], {}, 1, 2, 1)
        kind, d = logged[0]
        self.assertEqual((kind, d['ok'], d['arm'], d['boot'], d['cycle']), ('rate_apply', False, 'B', 1, 2))
        self.assertEqual(d['after_bytes_per_s'], A)

    def test_cycle_records_carry_the_arm_and_the_applied_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = rb.RCtx(Path(tmp), SimpleNamespace(base='b'))
            ctx.extra = {'arm': 'B', 'rate': {'after_bytes_per_s': B}}
            ctx.log('cycle', boot=1, cycle=1, failures=[], metrics={})
            ctx.log('cycle_partial', boot=1, cycle=1)
            ctx.log('boot_begin', boot=1)
            lines = [json.loads(x) for x in (Path(tmp) / 'trace.jsonl').read_text().splitlines()]
            self.assertEqual([l.get('arm') for l in lines], ['B', 'B', None])
            self.assertEqual(ctx.cycles[0]['rate']['after_bytes_per_s'], B)


class Validation(unittest.TestCase):
    def test_a_correct_run_has_no_problems(self):
        self.assertEqual(rb.validate_cycles(full_run()), [])

    def test_wrong_arm_for_the_schedule_is_rejected(self):
        c = full_run()
        c[1] = cycle(1, 2, 'A')  # schedule says B
        self.assertTrue(any('schedule' in x for x in rb.validate_cycles(c)))

    def test_readback_that_differs_from_the_arm_is_rejected(self):
        c = full_run()
        c[0] = cycle(1, 1, 'A', readback=B)
        self.assertTrue(any('readback' in x for x in rb.validate_cycles(c)))

    def test_missing_rate_record_is_rejected(self):
        c = full_run()
        del c[3]['rate']
        self.assertTrue(any('rate' in x for x in rb.validate_cycles(c)))

    def test_incomplete_run_is_not_valid(self):
        self.assertTrue(any('12' in x for x in rb.validate_cycles(full_run()[:7])))

    def test_unlabeled_cycle_is_rejected(self):
        c = full_run()
        del c[0]['arm']
        self.assertTrue(rb.validate_cycles(c))


class Summary(unittest.TestCase):
    def test_per_arm_medians_normalized_rate_and_boot_differences(self):
        s = rb.summarize_rates(full_run())
        self.assertEqual(len(s['rows']), 12)
        self.assertEqual(s['per_arm']['A']['save.migrate_s']['median'], 3.0)
        self.assertEqual(s['per_arm']['B']['save.migrate_s']['median'], 1.0)
        self.assertEqual(s['per_arm']['A']['mib_s']['median'], round(400 / 3.0, 2))
        self.assertEqual(s['per_boot'][1]['save.migrate_s']['B_minus_A'], -2.0)
        self.assertEqual(s['per_boot'][2]['save.migrate_s']['B_minus_A'], -2.0)

    def test_first_after_boot_is_separate_from_later_footprint(self):
        c = full_run()
        c[0]['metrics']['state_bytes'] = 300 << 20
        s = rb.summarize_rates(c)
        self.assertEqual(s['first_vs_later']['A']['state_bytes']['first_after_boot']['min'], 300 << 20)
        self.assertEqual(s['first_vs_later']['A']['state_bytes']['later']['min'], 400 << 20)

    def test_missing_metric_is_counted_not_zero(self):
        c = full_run()
        c[0]['metrics']['save.migrate_s'] = None
        s = rb.summarize_rates(c)
        self.assertEqual(s['per_arm']['A']['save.migrate_s']['n'], 5)
        self.assertIsNone(s['rows'][0]['mib_s'])


class Preflight(unittest.TestCase):
    def quiet(self, **kw):
        base = {'wall_s': 60, 'swapout_pages': 0, 'psi_memory_some_us': 0, 'psi_cpu_some_us': 0, 'mem_available_kib': 20 << 20}
        return lambda a, b: dict(base, **kw)

    def test_quiet_host_passes(self):
        out = rb.quiet_preflight(1, snap=lambda c: {}, sleep=lambda s: None, delta=self.quiet())
        self.assertEqual(out['contention'], [])

    def test_pressure_stops_the_run(self):
        with self.assertRaisesRegex(RuntimeError, 'cpu_pressure'):
            rb.quiet_preflight(1, snap=lambda c: {}, sleep=lambda s: None, delta=self.quiet(psi_cpu_some_us=30_000_000))

    def test_running_qemu_stops_the_run(self):
        with patch.object(cb, 'qemu_pids', lambda: [7]):
            with self.assertRaisesRegex(RuntimeError, 'another qemu'):
                rb.quiet_preflight(1, snap=lambda c: {}, sleep=lambda s: None, delta=self.quiet())


class Retention(unittest.TestCase):
    def args(self, root):
        return SimpleNamespace(output=str(Path(root) / 'rb'), base=__file__, deck=None, boots=3, cycles=4, max_cycles=12,
                               max_wall_s=600, sample_s=0.01, settle_s=0, fixture_port=1, preflight_s=0)

    def run_with(self, boot, tmp):
        for p in (patch.object(cb, 'preflight', lambda a: {'preflight': 'stub'}), patch.object(cb, 'base_unchanged', lambda a: True),
                  patch.object(cb, 'cleanup_owned'), patch.object(cb, 'run_boot', boot),
                  patch.object(rb, 'quiet_preflight', lambda s: {'contention': []})):
            p.start()
            self.addCleanup(p.stop)
        return rb.run(self.args(tmp))

    def test_failed_attempt_keeps_partial_cycles_and_is_marked_invalid(self):
        def boot(ctx, n):
            c = cycle(n, 1, 'A')
            ctx.log('cycle', **c)
            raise RuntimeError('scripted candidate failure')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'scripted candidate failure'):
                self.run_with(boot, tmp)
            root = Path(tmp) / 'rb'
            s = json.loads((root / 'rate-summary.json').read_text())
            self.assertFalse(s['ok'])
            self.assertEqual(len(s['summary']['rows']), 1)
            self.assertTrue(s['problems'])
            self.assertIn('scripted candidate failure', s['error'])
            self.assertTrue((root / 'trace.jsonl').exists())

    def test_complete_run_with_a_mislabeled_arm_is_invalid(self):
        def boot(ctx, n):
            for i, arm in enumerate(rb.SCHEDULE[n - 1]):
                c = cycle(n, i + 1, arm)
                if (n, i) == (2, 1):
                    c['rate']['after_bytes_per_s'] = B  # arm label says A
                ctx.log('cycle', **c)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'invalid'):
                self.run_with(boot, tmp)
            s = json.loads((Path(tmp) / 'rb/rate-summary.json').read_text())
            self.assertFalse(s['ok'])
            self.assertEqual(len(s['summary']['rows']), 12)

    def test_complete_valid_run_is_ok(self):
        def boot(ctx, n):
            for i, arm in enumerate(rb.SCHEDULE[n - 1]):
                ctx.log('cycle', **cycle(n, i + 1, arm))
        with tempfile.TemporaryDirectory() as tmp:
            self.run_with(boot, tmp)
            s = json.loads((Path(tmp) / 'rb/rate-summary.json').read_text())
            self.assertTrue(s['ok'])
            self.assertEqual(s['problems'], [])

    def test_preflight_failure_is_retained_and_starts_no_boot(self):
        booted = []
        def boot(ctx, n):
            booted.append(n)
        def bad(s):
            raise RuntimeError('host contention: cpu_pressure')
        with tempfile.TemporaryDirectory() as tmp:
            for p in (patch.object(cb, 'preflight', lambda a: {}), patch.object(cb, 'run_boot', boot),
                      patch.object(rb, 'quiet_preflight', bad)):
                p.start()
                self.addCleanup(p.stop)
            with self.assertRaisesRegex(RuntimeError, 'cpu_pressure'):
                rb.run(self.args(tmp))
            self.assertEqual(booted, [])
            self.assertIn('cpu_pressure', (Path(tmp) / 'rb/preflight-failure.json').read_text())


class Safety(unittest.TestCase):
    def test_help_starts_no_vm(self):
        out = subprocess.run([sys.executable, str(HERE / 'ratebench.py'), '--help'], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0)
        self.assertIn('--preflight-s', out.stdout)

    def test_default_output_is_a_private_ratebench_directory_outside_git(self):
        self.assertIn('ratebench-', rb.default_output())
        self.assertIn('.cache/deck', rb.default_output())


if __name__ == '__main__':
    unittest.main()
