# Single-bot benchmark

Date: 2026-11-12.

## Purpose

Measure the merged runtime before changing its performance. Run one VM at a time. Use the release binary, a frozen base, a fresh private overlay for each round, a scripted model and the synthetic fixture. Run three rounds in sequence. Do not mix these data with the earlier debug-binary samples.

## Workload

The Stage 3 scenario cold boots a bot. It opens a fixture in bare, runs a shell command, sends a file and finishes. The shell includes a deliberate 14-second wait. The working sample therefore measures mostly waiting for a tool, not sustained browser or compute load. A viewer subscribes and disconnects. The computer saves after idle time. A second task restores it and replies.

This pass is a lifecycle benchmark. It is not a real-model, service integration, sustained-load or multi-bot capacity test.

## Evidence

`benchmark.py` records the binary and base hashes, revision, kernel, CPU count and timing units. Each JSONL record has wall and monotonic timestamps. API traces include requests and responses from this synthetic scenario. They do not include authentication headers. Process starts and window boundaries are explicit.

An observer samples owned host processes once per second. It records cumulative CPU ticks, RSS, memory accounting where accessible, faults, threads, file descriptors and I/O counters. It also records host load and memory pressure indicators. Guest process snapshots precede measured windows. The original scenario records VM and server CPU, RSS, model calls, captures, wake time, task time, screen delivery and correctness assertions.

The trace does not capture every kernel event. A short process or transient peak can fall between samples. Completed child CPU can escape process-tree snapshots. API traces and sampling add overhead. Guest process snapshots run before the measurement windows. RSS is not guest memory use or unique physical memory; compare PSS separately when available. No per-call cost is measured because the model is scripted.

Raw traces, server logs, journals, disks and captures stay outside Git. Use a new private output directory. Never run this API payload logger against real accounts. Never add credentials, tokens, cookies or model configuration values to logs. Commit only sanitized summaries.

## Reproduce

Build the web app and a release binary first. Set the paths for the frozen base and binary:

```sh
python3 scripts/vm-prototype/benchmark.py \
  --base /path/to/frozen-base.qcow2 \
  --deck /path/to/target/release/deck \
  --output "$HOME/.cache/deck/new-benchmark" \
  --rounds 3
```

`--help` starts no VM. The run stops its own processes through the Stage 3 cleanup path. An interrupted run needs an ownership check before cleanup. Keep failed rounds and their traces. Do not retry into the same output directory or omit failures from a report.

## Status

Four runs completed successfully. Use original rounds 2 and 3 plus the replacement round as the three-round baseline. Original round 1 overlapped a 7.5-second local test suite. Keep it as diagnostic evidence, but exclude it from baseline statistics. No other test suite or benchmark from this session ran during the accepted rounds. Other host activity was not excluded; the trace records host load.

The binary contains production code from merged revision `1085df9`. The benchmark wrapper adds observer overhead. It does not change production code. Each accepted round passed browser, shell, file delivery, idle save, restore and chat-continuity assertions. Each made five scripted model requests in total.

| Metric | Round 2 | Round 3 | Replacement |
| --- | --- | --- | --- |
| Cold wake to awake | 17.6 s | 18.6 s | 20.1 s |
| First task, including 14-second wait | 32.7 s | 33.4 s | 35.0 s |
| Restored reply task | 4.5 s | 3.0 s | 4.5 s |
| Working VM RSS | 529.7 MiB | 541.9 MiB | 540.0 MiB |
| Working VM CPU, one-core percentage | 8.32% | 8.72% | 9.04% |
| Awake-idle VM RSS | 524.0 MiB | 542.5 MiB | 540.7 MiB |
| Awake-idle VM CPU, one-core percentage | 2.20% | 6.54% | 3.46% |
| Awake-idle server RSS | 12.9 MiB | 12.6 MiB | 13.0 MiB |
| Saved server RSS | 13.0 MiB | 12.8 MiB | 13.1 MiB |

The CPU windows are about five seconds for working, fifteen seconds for awake-idle and ten seconds for saved. RSS in this table is the end-of-window reading. One-second raw samples retain additional memory observations. The working windows do not align with identical tool boundaries: model-call deltas were two, one and zero. Use them as descriptive observations, not a strictly paired active-work comparison. Future active-load measurements need explicit workload-boundary synchronization.

All accepted idle and saved windows had zero model calls and captures. Server CPU rounded to 0.00% in these short windows. Saved windows had no VM process and zero VM RSS. Each viewer interval captured five frames, delivered one and suppressed four unchanged frames. Closing the viewer stopped the worker and the capture counter stayed flat.

The awake-idle CPU range is too wide to claim a stable improvement. Guest snapshots show the desktop, terminal, editor and browser processes. They report lifetime CPU averages, not window-specific attribution. Profile their CPU deltas before choosing an optimization. The base still includes the prototype editor session.

Raw evidence remains in private runtime directories. Server logs and host databases were retained for all four runs. The trace includes full synthetic API responses; it must not be used for live accounts. No VM, benchmark or fixture process remained after the series.

## Verification and remaining work

The existing 62 Python tests pass. The new script compiles and its help starts no VM. Four real-VM scenarios exercised the wrapper. The release binary and web build succeeded. The web build retains its bundle warning. No production runtime behavior changed, so the Rust suite was not repeated for this instrumentation.

Not tested: sustained browser load, CPU-heavy shell work, large artifact throughput, real model latency/cost, real service traffic, memory pressure, long soak, crash recovery under load or concurrent bots. These remain separate workload tests. This baseline does not establish capacity or a performance gain.

## Review follow-up

Evidence gaps and workload alignment now have fixes and regression coverage. Further idle attribution and synchronized tool measurements are in [benchmark-investigation.md](benchmark-investigation.md). Keep the original measurements and exclusions above unchanged.
