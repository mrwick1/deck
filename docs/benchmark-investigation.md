# Benchmark evidence and idle CPU investigation

Date: 2026-11-12.

## Scope

Fix the evidence gaps from review. Attribute awake-idle CPU before changing production settings. Replace loosely aligned active samples with explicit workload intervals. All scenarios use synthetic data. Run one VM at a time. Do not run test suites during measurement windows.

## Evidence fixes

Two regression tests failed before the fixes. A failed scenario skipped log and journal retention. The sampler had no failure channel. The wrapper now retains results, logs and data from a round in its `finally` path. It writes an explicit failed result when the scenario produces no result file. Retention errors make the run invalid.

The sampler catches its exception and reports it to the main run. The main run checks for observer failure after each scenario and again after joining the sampler. A failed sampler cannot produce a successful benchmark exit. Detection can wait until the current scenario ends; this is not immediate cancellation. `evidence-status.json` records sampler and retention errors.

A third regression injects a failed sampler into a successful scenario and verifies that the main run rejects it. Cleanup now waits for owned helper processes and terminates any that remain. Raw cleanup records no longer rely only on a request to terminate.

The failure-retention regression uses synthetic receipt bytes. It tests file retention, not database recovery. A filesystem failure can still prevent writing evidence; the benchmark must fail in that case. The trace is sampled evidence, not a complete kernel audit.

## Idle attribution

One fresh guest boot ran three 20-second intervals with the prototype editor open, then three with it closed. Each interval records guest process identity and cumulative CPU ticks at both boundaries. Reused PIDs are excluded through process start-time checks. The profiler retains both snapshots and calculated deltas.

| Interval | Editor open: host VM CPU | Editor closed: host VM CPU |
| --- | --- | --- |
| 1 | 2.95% | 1.65% |
| 2 | 3.05% | 3.70% |
| 3 | 2.30% | 1.45% |

CPU percentages refer to one host core. The closed intervals still vary. This is one ordered comparison, not randomized or repeated across boots. It cannot establish an editor-removal speedup. Guest editor, terminal, compositor and browser CPU deltas were negligible at this resolution. Most listed guest-process deltas were zero; nonzero leaders were small system or guest-agent activity. Kernel interrupt accounting and short-lived guest processes are not fully attributed by these snapshots.

A separate 15-second host-thread sample during the closed-editor phase measured about 1.40% for QEMU's main thread, 0.27% for one virtual CPU thread and 0.53% for the other. This observer overlapped the last closed-editor interval and adds measurement overhead. Keep it as attribution evidence, not a controlled performance comparison.

A separate cold boot then collected a 20-second software CPU-clock profile. It captured 177 samples with zero reported lost samples. Visible stacks include GLib main-context preparation/checks, `main_loop_wait`, clock reads, timer deadline calculation, polling and KVM paths. Many symbols remain unresolved. Individual symbol percentages are too noisy to rank exact causes.

**Finding:** The data point toward host VM event-loop and virtual CPU overhead. They do not support blaming the editor or a busy agent loop. The exact wakeup source remains unproven. Display/device timer attribution needs further tracing before changing QEMU settings. The profile was collected separately from the integrated server tests.

The first profiler attempt failed before collecting intervals because its wrapper expected two guest-command return values instead of three. The corrected attempt used a fresh directory. The failed log and cleanup record remain retained. Its VM stopped.

## Synchronized tool measurements

The scenario can now mark a selected shell interval. Sampling waits for the ready marker. A finish-marker assertion rejects a window that outlasted the workload. Two fresh-overlay scenarios used the same production release binary as the baseline.

| Measured five-second interval | Waiting shell | CPU-heavy shell |
| --- | --- | --- |
| Host VM CPU, one-core percentage | 2.99% | 101.98% |
| Model requests during interval | 0 | 0 |
| Screen captures during interval | 0 | 0 |

The waiting shell sleeps for 14 seconds. The CPU-heavy shell hashes a fixed buffer for 14 seconds. Each is a single calibration run, not a throughput estimate. The heavy task deliberately consumes a core; this is not evidence of excess background CPU. The full task includes boot, browser work, model exchanges and file delivery.

Both scenarios passed browser, shell, file, idle-save, restore and chat-continuity checks. Both recorded empty sampler and retention error lists. Both retained their server logs and host journals. Idle and saved windows still had zero model calls and captures. Cleanup records list no live owned helpers. A final process check found no investigation VM or benchmark process.

## Interpretation and next measurements

The sleep lifecycle remains effective. The active sample now has a known task boundary. The earlier active CPU figures remain descriptive and must not be used as paired comparisons.

Do not change production VM devices or desktop services from this evidence alone. Next, trace host wakeups and device/timer sources while recording guest interrupt and total CPU counters. Compare one candidate change against a fresh reference, reverse the order and repeat across boots. Preserve display, bare, artifact and saved-state gates.

Still untested: sustained browser navigation, artifact throughput, memory pressure, long soak, real-model latency and cost, service events and concurrent bots. These require separate workloads. No capacity or production performance improvement is claimed.

## Verification and provenance

All 65 Python tests pass. The three evidence regressions cover failure retention, sampler exception capture and rejection by the main run. Compilation and diff checks pass. Two real-VM synchronized scenarios exercise successful evidence retention and helper cleanup. The idle attribution and separate CPU profile completed. No Rust production code changed, and the Rust suite was not repeated for these Python harness changes.

Raw evidence stays outside Git in private runtime directories. Preserve the first failed attempt, corrected idle snapshots, host-thread sample, raw CPU profile, two synchronized traces, server logs, journals and scenario results. The profiler and benchmark scripts are in `scripts/vm-prototype/`. The CPU profile used `perf record -e cpu-clock -F 499 -g -p <owned-vm-pid> -- sleep 20` after a separate cold boot and stabilization period.
