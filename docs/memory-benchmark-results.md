# Sustained bare memory benchmark

Date: 2026-11-12.

## Result in short

The experiment is **incomplete**. The planned design is three independent boots of 30 minutes each. Only boot 1 ran, and it stopped after 1,551 s of its 1,800 s workload. A stop rule fired on host-wide swap-out that the VM did not cause. The harness then ended the run, as designed. It did not retry.

No save, restore, first-bare-action or post-restore marker data exist. No claim about variation between boots is possible. The numbers below describe one boot for 26 minutes. They are not a plateau claim and not a capacity claim.

## Purpose

Find out how memory grows when the bare session works for a long time. Separate four things: host QEMU memory, guest page cache, the bare process, and bare's runtime output file. Do not infer a leak from host RSS or cache alone.

## Scope

`scripts/vm-prototype/memorybench.py` is a new harness. It reuses `vmproto`, the Stage 1 guest and marker checks, `benchmark.sample`, and the `cyclebench` pressure, cgroup and tracing helpers.

- Each boot is a fresh private overlay on one frozen base, with a cold boot. The base is never modified. Its digest was equal before and after the run.
- Codec `none`. Guest: 2 vCPUs, 2 GiB RAM. No RAM reduction, cache dropping, balloon, codec or device change.
- One VM runs at a time. The harness refuses to start when another QEMU process runs.
- Bare is the only browser. The harness drives the guest bare session directly. It does **not** test the server, executor, chat, journal or holds.
- The fixture is synthetic. No account, external network or model call is involved.
- The guest user id, runtime directory and home come from the running guest. No id or runtime path is hard-coded.

## Workload

The fixture gained bounded routes in `fixture.py`: 40 pages of exactly 32 KiB, each with one `target="_blank"` link and one download link. It also serves 8 downloads of exactly 2 MiB with a known SHA-256, and one end page with a fixed marker. Routes outside these bounds return 404. Nothing grows with requests.

One round has eight operations: navigate, snapshot, read, navigate, download, navigate, new tab, extra session. The loop sleeps 6 s after each operation. It stops at a monotonic deadline or after 2,000 operations.

| Limit | Value |
|---|---|
| Open bare sessions, tabs included | at most 3, checked after every open and close |
| Owned download bytes | at most 32 MiB; the oldest owned file is deleted before the next download |
| Deleted files | only names `mem-<0-7>[ (n)].bin` in the owned directory |

Each download is hashed in the guest and compared with the fixture hash.

Boot 1 completed these counts before the stop:

| Item | Count |
|---|---|
| Operations | 225 (navigate 85, snapshot 28, read 28, download 28, new tab 28, extra session 28) |
| Bare requests | 646 (open 113, sessions 253, snapshot 84, read 56, act 56, eval 28, close 56) |
| Pages visited | all 40 |
| Downloads | 28 (56 MiB transferred), 12 deleted, 16 retained |
| Retained download peak | 32.0 MiB, equal to the cap and never above it |
| Largest session count | 3 |
| Bare errors | none |

Median operation time: navigate 0.52 s, snapshot 0.22 s, read 0.22 s, download 0.83 s (maximum 4.3 s), new tab 2.0 s, extra session 1.8 s.

## Sampling

The sampler ran every 10 s in the load window and wrote each sample to a flushed JSONL file. The window has 151 samples. The median gap is 10.2 s and the largest gap is 10.6 s.

- Host: QEMU process tree RSS and PSS, with process start times so a reused PID cannot fake growth; cgroup `memory.current`, `memory.events` and selected `memory.stat` fields; host PSI, swap counters, load and available memory.
- Guest, by one guest-agent call: `MemAvailable`, `AnonPages`, `Cached`, `Slab`, `Shmem`, swap, memory PSI, OOM kills, bare process tree RSS and PSS with start times, and the resolved sizes of the bare output and error files.
- A missing counter is recorded as unavailable, never as zero.

A sampler error invalidates the run. None occurred.

## Memory results

Early means the first 5 minutes of the load window. Steady means minute 10 to the stop. Slopes are least-squares fits in MiB per minute unless the unit says otherwise. The label is "growing" when the slope exceeds twice its standard error. The error ignores autocorrelation, so the label is indicative only.

| Metric | First | Last | Early slope | Steady slope | Steady label |
|---|---|---|---|---|---|
| Host QEMU RSS (MiB) | 526.7 | 623.3 | 7.86 | 2.76 ± 0.09 | growing |
| Host QEMU PSS (MiB) | 519.6 | 616.1 | 7.87 | 2.76 ± 0.09 | growing |
| Host cgroup `memory.current` (MiB) | 541.3 | 661.6 | 18.6 | 2.46 ± 0.21 | growing |
| Host cgroup anonymous (MiB) | 492.1 | 589.8 | 7.86 | 2.86 ± 0.09 | growing |
| Host cgroup file cache (MiB) | 44.0 | 66.3 | 10.7 | −0.40 ± 0.26 | no significant trend |
| Host cgroup shared memory (MiB) | 11.7 | 11.7 | 0 | 0 | no significant trend |
| Guest used, total minus available (MiB) | 333.2 | 345.5 | 1.28 | 0.60 ± 0.07 | growing |
| Guest page cache (MiB) | 214.6 | 284.9 | 6.39 | 2.08 ± 0.08 | growing |
| Guest anonymous pages (MiB) | 37.1 | 42.5 | 0.71 | 0.005 ± 0.013 | no significant trend |
| Guest slab (MiB) | 47.2 | 48.2 | 0.07 | 0.13 ± 0.007 | growing, small |
| Guest shared memory (MiB) | 19.1 | 18.3 | −0.01 | 0.03 ± 0.03 | no significant trend |
| Bare process RSS (MiB) | 22.6 | 26.2 | 0.68 | −0.003 ± 0.03 | no significant trend |
| Bare process PSS (MiB) | 18.2 | 22.5 | 0.70 | −0.003 ± 0.015 | no significant trend |
| Bare process anonymous PSS (MiB) | 4.8 | 9.7 | 0.71 | 0.001 ± 0.004 | no significant trend |

Bare output file, the file that bare-session truncates at process start:

| Item | Value |
|---|---|
| Size at first and last sample | 3.1 KiB and 538.6 KiB |
| Growth | 20.9 KiB per minute, steady |
| Per request | 849 bytes (646 requests) |
| Error file | 0 bytes in every sample; no restart occurred |
| Runtime tmpfs used | 8 KiB to 544 KiB; this equals the output file growth |

Guest swap use stayed at zero. No guest OOM kill occurred. No cgroup event counter (`oom`, `oom_kill`, `max`, `high`) left zero. No PID reuse was seen.

## Findings

1. **The bare process stopped growing after the first minutes.** Its RSS and PSS rose about 4 MiB in the early window and then showed no significant trend. Its anonymous PSS is about 10 MiB. Bare is a single small process in this guest, so it is not the main memory consumer.
2. **Host QEMU memory kept growing at about 2.8 MiB per minute through the stop.** This run cannot show a plateau. The host RSS rose by 97 MiB in 26 minutes.
3. **Hypothesis, to verify: the host growth follows guest memory that the guest touched, not a bare leak.** The guest page cache grew 2.1 MiB per minute and guest used memory grew 0.6 MiB per minute. Their sum, 2.7 MiB per minute, is close to the host anonymous growth of 2.9 MiB per minute. Without a balloon, QEMU keeps a guest page resident after the guest touches it, even when the guest later frees it. Host RSS therefore tracks the high-water mark of touched guest pages. If this is right, host RSS would level off near the guest RAM size plus QEMU overhead. This run is too short to check that.
4. **The bare output file grows with requests.** It grows about 850 bytes per request and lives in tmpfs. Over a long session it counts toward guest `Shmem` and runtime memory. At this rate one million requests add about 0.8 GiB. This is an extrapolation, not a measurement. Whether bare should bound or rotate this file is an open question for the bare project.
5. **Retention worked.** The retained downloads reached the 32 MiB cap and never exceeded it. Deleting old owned files did not stop the page cache from growing.
6. **No memory counter shows a failure.** There was no swap, no OOM, no cgroup limit event and no memory pressure. The cgroup limit is 3,500 MiB and peaked near 663 MiB.

## Why the run stopped

A rule from `cyclebench` stops the run when host-wide swap-out exceeds 16,384 pages (64 MiB) in a window. This harness evaluates it over 60 s windows. The unchanged threshold fired at 1,551 s:

| Reading in the last window | Value |
|---|---|
| Host swap-out | 34,632 pages (135 MiB) |
| Host available memory | at least 15.0 GiB |
| VM swapped memory | 0 KiB |
| Host memory pressure | 0.63% of the window |
| Host CPU pressure | 17.9% of the window |
| Host one-minute load | 13 to 17 on 16 CPUs |

Other work on the host caused the load and the paging: builds and a model server that this experiment does not own. The VM could not swap, because its cgroup sets `MemorySwapMax=0`. Earlier windows also showed background paging of up to 8,013 pages (31 MiB). The VM stayed healthy to the end.

The threshold was not changed after the fact. The run stopped, kept its evidence and ended without a retry.

## Observer cost

These numbers are separate from workload time.

| Item | Value |
|---|---|
| Sampler thread CPU in the load window | 10.4 s over 1,551 s (0.67% of one core) |
| Harness process CPU, sampler included | 11.7 s |
| One guest sample, guest-agent wall time | median 0.19 s, maximum 0.42 s |
| Guest sampler process CPU | median 2 ticks, maximum 11 ticks |

The guest sample adds guest CPU and a few page touches in the guest. The harness did not measure how much this changes the memory curves. A run without the guest sample was not made.

## Migration bandwidth

The harness read `query-migrate-parameters` on the first boot and changed nothing. The effective `max-bandwidth` is 134,217,728 bytes per second (128 MiB/s). The cycle benchmark saw saves at 135 to 139 MiB/s over a cached file. The two values are the same order, so the earlier rate-limit hypothesis is consistent with the data. A controlled comparison is still missing.

## Cleanup and evidence

- No QEMU, fixture or harness process remained. No cgroup scope remained.
- The base digest was equal before and after.
- The harness retained the trace, the samples, the guest disk, the QEMU log and the summary in a private directory outside Git. The run wrote no sampler or retention error.
- The final scope check read "not gone" because it ran right after SIGKILL. A later check found no scope. The check now waits up to 5 s. This change came after the run.

## Not tested

- Save bytes, save and restore phase times, the first bare action after restore, and every post-restore marker. The code for these steps exists and has unit tests for its pure parts. It has **not** run against a VM in this harness.
- Variation between boots. Only one boot ran.
- A plateau of host memory, and the last 249 s of the planned window.
- The server, executor, chat, journal and holds; real model and real sites; concurrent bots; disk-cold restore.
- The effect of the guest sample on the memory curves.

## Candidates, not recommendations

No production change follows from this evidence.

1. The host-wide swap-out rule is a poor stop condition for this VM, because its scope cannot swap. A stop rule on the VM's own swap and cgroup events would test what the experiment cares about. Host paging could stay in the record as context. These thresholds are proposals. They are not user requirements. Changing one needs a decision before the next run.
2. Run one short single-boot smoke test first, with a workload of about one minute. It would exercise the untested save, restore and marker path before the full run.
3. Rerun the three boots when the host is quiet.

## Run history

- **Run 1** is the only run. It started with a quiet-enough host (60 s windows of 9% and 12% CPU pressure). Boot 1 stopped at 1,551 s on the swap-out rule. Boots 2 and 3 did not start. No failed attempt was retried.

## Verification

- The existing 87 Python tests and 38 new tests pass.
- The new tests cover the fixture sizes, hashes and bounds, the session cap, the download cap and owned-file rules, the deadline loop, the growth math, missing counters, the stop rules, the sampler-failure invalidation, failure retention and the scope wait.
- Mutating the cap, the prune rule, the deadline, the sampler error handler, the retention error handler, the stop rule and the owned-path check made the tests fail.
- Compilation, `--help` (no VM starts) and `git diff --check` pass.
