# Migration bandwidth comparison

Date: 2026-11-12.

## Purpose

Test one hypothesis. QEMU's default `max-bandwidth` of 128 MiB/s limits the saved-state write. Compare it with 512 MiB/s in a controlled run. The comparison itself changed no production setting. The parent adoption and its limits are recorded below.

## Method

`scripts/vm-prototype/ratebench.py` wraps the `cyclebench.py` cycle. It reuses the session markers, phase accounting, sampler, retention, base check and cleanup. It adds only the limit control.

- Arm A: 134,217,728 bytes/s (128 MiB/s, the value QEMU reported). Arm B: 536,870,912 bytes/s (512 MiB/s).
- Three fresh boots, four cycles each, on the frozen base with codec `none`. Boot order: ABBA, BAAB, ABBA. One VM at a time.
- Before each save the harness queries the limit, sets it with `migrate-set-parameters` and reads it back. The read-back must equal the target. This runs outside the timed save call. Each restore starts a new QEMU process, so the limit is set again every cycle.
- A set failure, a read-back mismatch or a mislabeled arm invalidates the attempt. The harness also validates the arm of every cycle against the schedule after the run.
- A 60-second idle preflight ran first, with no QEMU. It used `cyclebench.contention`. The old host-swap guard stayed in force (D52 not applied).
- Every cycle checked the desktop, browser, file and unsaved-buffer markers, terminal pixels, the download hash, helper absence and the first bare call after restore.

## Result

The attempt is valid. All 12 cycles ran, in 3 boots, in 279 s of wall time. No gate failed. The sampler and retention reported no error. The base digest was the same before and after. No QEMU process remained. Raw evidence: private `~/.cache/deck/ratebench-20261112-223613` (outside Git): `trace.jsonl`, `samples.jsonl`, `summary.json`, `rate-summary.json`, `evidence-status.json`, `boot-N/`.

The query before each set returned 134,217,728 in all 12 cycles. A restored QEMU process does carry the default, so setting every cycle was needed. Every read-back equalled its target. The set took 0.9 to 2.3 ms.

### All 12 cycles

Times are in seconds. "Migrate" runs from the migrate command to the first poll that reports completion (poll step 20 ms). "Normalized" is observed state bytes divided by migrate time.

| Boot | Cycle | Arm | Migrate | Normalized MiB/s | State bytes | Save call | VM CPU | Peak RSS KiB | Restore to first shell | First bare eval |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | A | 3.145 | 137.3 | 452,647,001 | 3.389 | 0.428 | 539,172 | 1.640 | 0.203 |
| 1 | 2 | B | 0.829 | 517.4 | 449,832,536 | 1.075 | 0.391 | 489,392 | 2.461 | 0.418 |
| 1 | 3 | B | 0.805 | 542.8 | 458,337,923 | 1.046 | 0.322 | 519,908 | 1.271 | 0.304 |
| 1 | 4 | A | 3.231 | 137.4 | 465,364,070 | 3.477 | 0.363 | 522,504 | 1.649 | 0.304 |
| 2 | 1 | B | 0.833 | 517.8 | 452,493,485 | 1.080 | 0.340 | 542,952 | 1.086 | 0.303 |
| 2 | 2 | A | 3.136 | 138.0 | 453,717,050 | 3.394 | 0.382 | 508,304 | 1.759 | 0.304 |
| 2 | 3 | A | 3.142 | 138.9 | 457,638,098 | 3.387 | 0.402 | 517,860 | 1.357 | 0.202 |
| 2 | 4 | B | 0.853 | 514.9 | 460,715,666 | 1.137 | 0.373 | 518,776 | 1.511 | 0.304 |
| 3 | 1 | A | 5.212 | 82.4 | 450,338,801 | 5.449 | 0.451 | 531,916 | 1.823 | 0.412 |
| 3 | 2 | B | 1.022 | 416.4 | 446,398,949 | 1.405 | 0.618 | 490,752 | 3.244 | 0.203 |
| 3 | 3 | B | 0.821 | 517.7 | 445,852,397 | 1.065 | 0.361 | 507,040 | 1.468 | 0.304 |
| 3 | 4 | A | 3.133 | 137.7 | 452,278,811 | 3.355 | 0.363 | 510,300 | 1.874 | 0.203 |

Nothing was removed. Boot 3 cycle 1 (A) is the slow outlier. The same cycle saw 0.89 s of CPU pressure, and boot 3 cycle 2 saw 0.77 s. Both stayed below the 25% gate. Whether the pressure caused the slow save is not proven.

### Summary by arm

| Metric | A, 128 MiB/s: median (range) | B, 512 MiB/s: median (range) |
| --- | --- | --- |
| Migrate | 3.143 (3.133–5.212) | 0.831 (0.805–1.022) |
| Save call total | 3.391 (3.355–5.449) | 1.078 (1.046–1.405) |
| Normalized MiB/s | 137.5 (82.4–138.9) | 517.6 (416.4–542.8) |
| State bytes | 453.2 MB (450.3–465.4) | 451.2 MB (445.9–460.7) |
| Guest sync (quiesce) | 0.159 (0.154–0.174) | 0.160 (0.158–0.201) |
| Quit to return | 0.062 (0.041–0.062) | 0.062 (0.061–0.105) |
| VM-scope CPU | 0.392 (0.363–0.451) | 0.367 (0.322–0.618) |
| Peak RSS sampled, KiB | 520,182 (508,304–539,172) | 512,908 (489,392–542,952) |
| Harness CPU in save | 0.636 (0.596–1.022) | 0.213 (0.162–0.327) |
| Observer CPU in save | 0.157 (0.135–0.250) | 0.060 (0.047–0.103) |
| Host IO pressure in save, µs | 25,597 (11,374–289,313) | 13,886 (5,895–28,737) |
| Restore to first shell | 1.704 (1.357–1.874) | 1.489 (1.086–3.244) |
| First bare eval after restore | 0.253 (0.202–0.412) | 0.304 (0.203–0.418) |

State size does not explain the gain. Median state bytes differ by 0.4%. Per-cycle footprints overlap (A 450–465 MB, B 446–461 MB).

### Per boot, B median minus A median

| Boot | Migrate | Normalized MiB/s | State bytes | Save call | VM CPU |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | −2.371 | +392.8 | −4.9 MB | −2.372 | −0.039 |
| 2 | −2.296 | +377.9 | +0.9 MB | −2.282 | −0.036 |
| 3 | −3.251 | +357.0 | −5.2 MB | −3.167 | +0.083 |

The sign is the same in all three boots. Boot 3 is larger because its A cycles include the 5.2 s outlier.

### First after boot versus later

| Arm | Metric | First after boot | Later |
| --- | --- | --- | --- |
| A | Migrate median | 4.178 (n=2; 3.145, 5.212) | 3.139 (n=4) |
| A | State bytes median | 451.5 MB | 455.7 MB |
| B | Migrate median | 0.833 (n=1) | 0.829 (n=5) |
| B | State bytes median | 452.5 MB | 449.8 MB |

The first-after-boot sample is too small to separate from the outlier.

### Control gates

- All 12 cycles passed every marker, pixel, process, login, download-hash and fixture gate.
- Save helpers were gone after every save. The first bare eval returned the fixture account URL in every cycle.
- VM swap was 0 KiB before save and after restore in every cycle. Host swap-out was 0 pages in every cycle window. The preflight window had 312 pages out and 115 in, below the 16,384-page limit. Minimum available memory in the samples was 11.5 GiB.
- Host CPU pressure and memory pressure stayed below the gates. D52 paging context did not apply: no window crossed the threshold.
- The state file was fully resident in the page cache after each save. These are warm-cache numbers. Disk-cold is not claimed.
- Cgroup `io.stat` was unavailable. Only host IO pressure is recorded for write cost.

## Conclusion

On a warm cache, the 128 MiB/s default limits the save. Raising it to 512 MiB/s cut the median migrate step by 2.31 s (74%) and the save call by 2.31 s, from 3.39 s to 1.08 s. The achieved rate moved from about 137 MiB/s to about 518 MiB/s. Normalized rates cluster near the configured cap and sometimes exceed it over a short window. Bursts or timing boundaries may explain this, but the mechanism was not measured. These data do not establish a physical-device or thread throughput ceiling. The measured cumulative costs are limited to this workload. Median cumulative VM CPU did not rise; the shorter save concentrates work into a smaller interval. Peak CPU was not measured. Harness and observer CPU fell, because they polled for a shorter time. Median sampled peak RSS fell; its ranges overlap, and the largest sampled value was slightly higher in B.

Restore latency did not change in a way the data can show. The first-shell ranges overlap. The first bare eval was slower by 0.05 s in the median, inside a range of 0.2 to 0.42 s.

## One-bot adoption and parent validation

D53 selects 512 MiB/s for the configured one-bot CLI/server save path, with a CLI override of 128 MiB/s. Direct benchmark calls preserve their QEMU setting. Median cumulative VM CPU and median sampled peak RSS did not increase. Peak CPU and physical-device I/O were not measured. It shortens the time a bot holds its resources before the VM exits. It allows a higher migration-stream rate for a shorter interval. Cached stream throughput is not measured physical-disk throughput. This may matter when several bots save at once. Concurrent adoption remains deferred pending its own measurements.

## Not tested

- The server journal, chat continuity, budget and automation holds, and the idle policy.
- Real models and real accounts.
- Concurrent saves and host capacity. A bandwidth increase may help or hurt there.
- A disk-cold state file and a slower disk.
- Rates above 512 MiB/s. Only two values were compared.
- Source-host contention remains unproven; one slow A cycle coincided with CPU pressure.

## Parent implementation and validation

The parent reproduced the raw arm medians and the cycle summary. The configured server invokes `vmproto.py stop`, which now requests and verifies 512 MiB/s before dispatching migration. `stop --bandwidth-mib 128` selects the original cap. Direct Python benchmark callers preserve the current QEMU setting by default, so reference and paired-arm experiments remain valid. Saved-state metadata records the requested cap. No codec or VM resource setting changed.

The first integrated smoke failed because the new QMP call nested `max-bandwidth` in an extra `parameters` object. The save reported failure and no successful save was recorded; the smoke did not pass. The parent corrected the flat QMP arguments and added a regression that rejects the wrong shape. The failed log and runtime remain retained.

A fresh integrated real-server/scripted-model smoke passed task, browser, shell, file, idle-save, restore and chat-continuity assertions. The saved-state metadata recorded 512 MiB/s. Capture stopped after the viewer disconnected, and idle/saved model and capture counters stayed flat. The frozen base digest remained correct and no QEMU process remained. This is functional validation of the selected save path, not a second speed benchmark.

Two parallel code and evidence reviews found a direct-test-discovery issue and unsupported cost wording. The parent moved the direct unittest entry point after the new tests and qualified stream throughput, cumulative CPU, sampled peak RSS and concurrency claims. The sampler-start clock race also has a failing-before regression and a fix that retains failure evidence and cleanup.

All 197 Python tests pass. The cycle suite passed six further consecutive runs after its startup-race fix. Direct execution of the VM harness suite ran all 35 tests. Compilation, help and diff checks pass. No Rust code changed, and its suite was not repeated for this Python-only implementation.
