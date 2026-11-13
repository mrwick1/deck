# Idle attribution results

Date: 2026-11-12. Harness revision: `d49dd08` plus `scripts/vm-prototype/idlebench.py`. Release binary: production build `1085df9`.

## Scope

This run attributes awake-idle work of one bot. It uses the real Stage 3 path: the Deck server, a scripted model, a synthetic site and the guest executor with a persistent connection. The path is labelled "connected executor". Every window verified the connection (executor process holds the virtio port, no reconnect).

The run changed no production code, VM flag, device, RAM, codec or cache. The full desktop stayed. Bare was the only browser. No real account, real model or external network was used.

## Method

- `idlebench.py` runs `stage3.main` unchanged, three fresh overlays on one frozen base, one boot after another. A hook replaces only the short awake-idle window of Stage 3. Every other Stage 3 gate ran before and after.
- Each boot waits 10 s after the task ends. It then reads counters at three edges, 60 s apart. This gives two consecutive 60 s windows per boot. The server idle-save delay was 170 s.
- Host counters per window: QEMU thread CPU, voluntary and nonvoluntary context switches (kept separate), QEMU helper CPU, `/proc/<pid>/io`, server CPU and the observer CPU. Threads are matched by TID, start time and name.
- Guest counters per window (guest agent): `/proc/stat` total and per CPU, interrupts, softirqs, context switches, `/proc/interrupts` by name, `/sys/block/vda/stat`, clocksource, and per-process ticks and switches for bare, sway and the executor. The record keeps the role only, never a command line or environment.
- A window is valid only if the computer was awake, no command or work was active, model calls and captures were 0, the viewer worker was off, the QEMU, guest boot, bare, sway and executor identities were the same at both edges, and both edges were complete. A reused PID or TID, or a counter that goes backwards, is rejected. It never becomes a negative delta. A counter that is missing is `null`, not zero.
- Host guard (D54): a 10 s host sampler evaluated rolling 60 s windows with `memorybench.guardrails`. CPU pressure above 25%, memory pressure above 5%, available memory under 2 GiB, VM swap, cgroup OOM and cgroup limit stop the run. Host swap-out is context and flagged as contended. A 60 s preflight ran first. The run had one attempt and no retry.

## Result

The run completed: 3 boots, 6 windows, 6 valid. Wall time was 780 s of the 1,500 s budget. No guard fired. The base digest was the same before and after. No QEMU process remained. Raw evidence: private `~/.cache/deck/idlebench-20261112-220837` (outside Git).

### Gates

| Gate | Boot 1 | Boot 2 | Boot 3 |
|---|---|---|---|
| Stage 3 gates (task, file, shell, idle save, restore, chat) | pass | pass | pass |
| Executor connected in both windows | yes | yes | yes |
| Model calls in both windows | 0 | 0 | 0 |
| Captures in both windows | 0 | 0 | 0 |
| Idle timer still running at the last edge (ms left) | 38,795 | 39,150 | 39,419 |
| Rejected thread or PID | none | none | none |

### Per window (one core = 100%; 60 s windows resolve 0.0167%)

| Boot, window | QEMU CPU | Main thread CPU | Main thread vol switches/s | vCPU0 / vCPU1 CPU | Guest interrupts/s | Local timer/s | Executor vol switches |
|---|---|---|---|---|---|---|---|
| 1, 1 | 1.83% | 1.31% | 1,002 | 0.37% / 0.15% | 86.4 | 70.9 | 60 |
| 1, 2 | 1.96% | 1.43% | 1,002 | 0.22% / 0.32% | 88.0 | 74.6 | 61 |
| 2, 1 | 1.23% | 0.82% | 918 | 0.17% / 0.25% | 85.6 | 71.8 | 60 |
| 2, 2 | 1.81% | 1.35% | 1,001 | 0.30% / 0.17% | 84.2 | 72.2 | 60 |
| 3, 1 | 1.38% | 0.97% | 1,004 | 0.32% / 0.10% | 81.5 | 68.3 | 60 |
| 3, 2 | 1.37% | 0.90% | 1,002 | 0.15% / 0.32% | 87.2 | 73.1 | 60 |

Within a boot, the two windows differ by up to 0.6 points of one core (boot 2). Between boots, the QEMU CPU ranges from 1.23% to 1.96%. Nonvoluntary switches of the main thread stayed under 2 per second. Guest context switches were 76 to 80 per second. Matched persistent QEMU helpers and the server had no observed CPU tick increase. Work below the counter resolution and short-lived workers is not excluded. Block writes by QEMU were 2.2 to 3.7 MB per window; the guest block counters are in the raw file. Bare and sway showed 0 to 1 voluntary switches per window. The executor showed 60 to 61 voluntary switches and at most 0.0166% CPU (one tick).

### Repeated sources

- **Guest interrupt:** `LOC: Local timer interrupts` (68 to 75 per second) led in all 6 windows of all 3 boots. `CAL: Function call interrupts` (about 10 per second) was second in all windows. Both are generic kernel counters. Virtio interrupts were about 1 to 2 per second each.
- **Host thread, by switches:** the QEMU main thread (`qemu-system-x86`) led in all 6 windows, at about 1,000 voluntary switches per second. The two vCPU threads each made 20 to 50 per second.
- **Executor:** its 60 voluntary switches per 60 s match the 1 Hz `select` timeout in the guest executor source. They cost 0 to 1 tick of guest CPU. This is a measured executor voluntary-switch rate consistent with the timeout on a live connection; it is not an exact wakeup count. It is too small to explain the host numbers above (guest interrupts are 85 per second, the host main thread makes 1,000 per second).

Both leaders are candidates. They are not proven causes. A switch is not a wakeup. The leading thread and the leading interrupt are the expected busiest sources in any idle guest. The data do not show why the main thread makes about 1,000 switches per second. Hypotheses, to verify: QEMU timers or the display and device event loop, or user-mode network polling. Exact attribution needs privileged wakeup tracing, which this task deferred.

## Host context

One host swap-out window occurred (19,920 pages in 60 s, in boot 1, ending 5 s before the first idle edge), so the run is flagged `host_paging_contended`. Swap-out in the idle windows was 0 to 8,885 pages. The 60 s CPU pressure stayed under 4% in every window. Four 10 s guard samples showed CPU pressure of 15% to 35% (boots 1, 2 and 3). They were brief and no 60 s window passed the limit. Minimum available memory was 12.7 GiB. VM swap was 0. The cause of the host paging is unproven.

## Observer cost

- The guard sampler used about 0.12 to 0.15 s of CPU per 60 s window (about 0.2% of one core). It is host-side work, and it runs in the harness, not in the guest.
- Each edge runs one guest-agent call that starts a Python process in the guest: 0.11 to 0.32 s long, 1 to 11 guest ticks, 0.02 to 0.18 s of QEMU CPU. The host window starts after the call ends and ends before the next call starts. The guest counters span the two calls, so they include the tail of one call and the head of the next.
- The first edge of boot 1 was the most costly (11 guest ticks). The first window of boot 1 is the highest of the six. Do not read that as a difference between boots.
- Counter files contain cumulative totals, read sequentially near each edge rather than atomically. They hide bursts inside a window. `/proc` tick counters have 10 ms resolution.
- QEMU threads that start and stop between edges (short worker threads) are listed in the raw file as appeared or vanished. Their CPU is not in the totals.

## What this does not show

- A cause. The run has no controlled arm. Stage 3 leaves the prototype editor open. The earlier editor comparison used one boot and a different path.
- Exact wakeups. No tracing ran. No `perf` and no `sched_wakeup` data.
- Any control: no changed device, timer, display or network setting, and no ABBA arm.
- A real model, real accounts, real sites or concurrent bots. No capacity claim. The 3 boots are one attempt on one host, with one flagged paging window.
- The saved state, the working state and the saved-state codecs. They were outside these windows.
- A production tuning. This run is a negative result: no source is dominant enough to justify a change, and no candidate has a test.

The next step needs a controlled arm and privileged tracing, with one change at a time. Keep the full desktop.

## Verification

`python3 -m unittest discover -s scripts/vm-prototype -p 'test_*.py'` ran 168 tests before the live run (132 baseline plus 36 new). The new tests cover TID and PID identity, reuse rejection, missing counters, separate switch counters, window gates, guard limits, dominance rules, and the retention of a failed or invalid run.

## Parent verification

The parent reproduced all 168 Python tests, compilation, help and diff checks. It recalculated the six window gates and host deltas from the raw edge records: all were valid, with no rejected thread. The summary records three completed boots, six valid windows, paging context and no sampler/retention error. The base stayed unchanged and no QEMU process remained. Stable-thread CPU totals exclude short-lived threads and are lower bounds on total process work. No idle production setting follows from this pass.
