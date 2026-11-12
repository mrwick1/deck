# Sustained bare memory benchmark

Date: 2026-11-12.

## Result in short

The experiment is **incomplete**. The planned design is three independent boots of 30 minutes each. Only boot 1 ran, and it stopped after 1,551 s of its 1,800 s workload. A stop rule fired on host-wide swap-out. The VM reported no swapped memory; attribution of the host paging is unproven. The harness then ended the run, as designed. It did not retry.

The incomplete long run produced no save, restore, first-bare-action or post-restore marker data. A later parent smoke validated those paths; see the final validation section. No claim about variation between boots is possible. The numbers below describe one boot for 26 minutes. They are not a plateau claim and not a capacity claim.

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
3. **Hypothesis, to verify: the host growth follows guest memory that the guest touched, not a bare leak.** The guest page cache grew 2.1 MiB per minute and guest used memory grew 0.6 MiB per minute. Their sum, 2.7 MiB per minute, is close to the host anonymous growth of 2.9 MiB per minute. Without ballooning, freeing a guest page does not guarantee that its host memory is released. Host RSS can reflect previously touched guest pages. Guest available-memory estimates and cache counters are not disjoint components; their similar combined slope does not establish a causal accounting identity. If this is right, host RSS would level off near the guest RAM size plus QEMU overhead. This run is too short to check that.
4. **The bare output file grows with requests.** It grows about 850 bytes per request and lives in tmpfs. Over a long session it counts toward guest `Shmem` and runtime memory. At this rate one million requests add about 0.8 GiB. This is an extrapolation, not a measurement. This file belongs to Deck’s bare-session transport wrapper. Bounding it is a Deck transport question, not evidence of a bare-engine leak. Naive truncation can break reply offsets and must not be added without protocol tests.
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

Other work ran on the host, including builds and a model server that this experiment does not own. These observations do not establish which work caused the paging. The VM could not swap, because its cgroup sets `MemorySwapMax=0`. Earlier windows also showed background paging of up to 8,013 pages (31 MiB). The VM stayed healthy to the end.

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

- The existing 87 Python tests and 44 new tests pass (131 in total). One new test covers a sampler that dies before the harness reads its CPU clock. A repeat run of the suite found this race after the live run.
- The new tests cover the fixture sizes, hashes and bounds, the session cap, the download cap and owned-file rules, the deadline loop, the growth math, missing counters, the stop rules, the sampler-failure invalidation, failure retention and the scope wait.
- Mutating the cap, the prune rule, the deadline, the sampler error handler, the retention error handler, the stop rule and the owned-path check made the tests fail.
- Compilation, `--help` (no VM starts) and `git diff --check` pass.

## Smoke test (validation only)

This section checks the harness. It is not a memory, growth, plateau or performance result. Two single-boot smoke attempts used `--boots 1 --workload-s 60 --max-wall-s 1200` on the same frozen base. Each used a fresh private overlay, a cold boot and the normal pace and sample cadence. Both attempts **failed**. The raw directories are kept outside Git.

| Item | Attempt 1 | Attempt 2 |
|---|---|---|
| Host before the run, 60 s window | CPU pressure 11.0%, memory pressure 0.16%, swap-out 1,408 pages | CPU pressure 0.4%, memory pressure 3.25%, swap-out 0 pages |
| Stop reasons from the existing thresholds | none | none |
| Wall time | 119 s | 110 s |
| Launch to desktop ready | 20.8 s | 15.3 s |
| Workload | 9 operations in 62.3 s (navigate 4, snapshot, read, download, new tab, extra session 1 each) | 9 operations in 61.9 s, same mix |
| Bare errors, sampler errors, retention errors | none | none |
| Result | stopped before save: `terminal_pixels` | stopped before save: `terminal_pixels` |
| Base digest | unchanged | unchanged |
| Leftovers | no VM, no scope, no fixture | no VM, no scope, no fixture |

The host was quiet enough by the existing thresholds. The harness did not wait and did not relax any guard.

**What failed.** After the workload, the pixel hash of the terminal window differed from the hash taken after the unsaved note was typed. The harness stopped before the save. The save, restore, first-bare-action and post-restore marker steps therefore **did not run again**. All their code paths remain unvalidated against a VM.

**What was tried.** In attempt 1 the workload windows might have taken focus away from the terminal. A regression test now requires the terminal to be focused before the hash, as the setup does. The harness also records the hash as it was, the hash after the refocus and the reference. In attempt 2 the three hashes were all different. So focus alone does not explain the difference. The cause is **unproven**. Hypotheses to verify: a layout change from the opened and closed bare windows, or a change in the terminal content. The guard was not weakened. The gate still compares with the setup hash.

**Other checks.** The bounded tab count (at most 3), the bounded downloads (one 2 MiB file, hash equal), the end-page marker and the session cap held in both attempts. The save-phase `scope_gone` check is immediate, while the final cleanup waits up to 5 s. No attempt reached the save phase, so the real behavior is **not verified** and the code is unchanged.

**Next step.** Superseded by the diagnosis below.

### Diagnosis of the terminal pixel difference

One diagnostic boot (workload 60 s, original gate unchanged) kept the full screenshot, three terminal crops 0.6 s apart, the Sway tree and the nano process at three points. The points were setup, after the workload and after the refocus. The raw files are local and outside Git. The images were viewed.

| Point | Windows | Terminal rect | Terminal focused | Crop hash (3 crops equal) | Nano |
|---|---|---|---|---|---|
| Setup | 2 | 640x800 | yes | 145f70c8b59f477d | PID 602, notes file absent |
| After workload | 3 | 427x800 | no | 6baa286197f5df6d | PID 602, notes file absent |
| After refocus | 3 | 427x800 | yes | ed7b087468d2fa23 | PID 602, notes file absent |

**Cause (observed).** The terminal tiles with the bare windows. The workload opens a third window, so Sway narrows the terminal from 640 to 427 px. The images show the same text, `UNSAVED-NOTE-7f3a`, the `Modified` flag and the cursor at the same place. The three crops at each point are identical, so the cursor did not blink between them. The nano process is the same and the notes file is still absent. The hash gate failed on geometry. It did not fail on lost text. The unsaved text was intact at the first check.

**Limit.** This is one boot. The cursor and focus effects at an equal layout were not isolated. The refocus changed the hash at the new layout, which shows focus alone matters to the pixels.

**Fix.** The setup now floats the terminal at a fixed size before the session is built. The reference hash and the later hashes see the same layout. The gate still compares the pixels with the setup hash after the refocus. It is not weakened. Lost text, a changed layout or a changed focus still fail it. The record now includes the terminal rect. Regression tests use a fake desktop that tiles windows. They check that extra bare windows do not change the pixels, that lost text still fails and that a terminal that cannot be pinned stops the setup. Mutating the pin command and the pin check made them fail. Bare windows now open at the full output width, not half. This differs from the Stage 1 layout and may change page-render memory slightly.

### Attempts after the diagnosis

| Attempt | Result |
|---|---|
| Diagnostic boot | Stopped at the original gate as expected. Artifacts kept. Nothing leaked. Base unchanged. |
| Validation boot with the fix | Stopped at setup. Sway reports a floating window as 640x797 for a requested 800, and the pin check expected 800. No gate ran. No VM, scope or fixture was left. Base unchanged. |

The constant was then set to the observed 640x797 and the 131 tests pass. **This was not run on a VM.** The two-attempt limit is used. The save, restore, first-bare-action and post-restore marker steps are **still unvalidated**. The server, executor, chat, journal and holds were not tested. The full three-boot run remains **pending**.

## Outlier: 147 s host-side bare close

No retained log under the benchmark directories records a 147 s bare close. The matching numbers found there belong to other measurements (sample times, cycles, screen capture totals). The cause of the outlier is **unknown**: launch, wait, network and tool timeout are not separable from the data kept. This report makes no claim about the bare engine.

## Parent validation after the geometry fix

A fresh parent-run smoke used one boot and a 60-second workload with the pinned terminal. The unchanged host-pressure preflight passed. The setup, pre-save and post-restore terminal pixel comparisons passed at 640x797. The same nano process, empty on-disk notes, disk and shell markers, browser URL/login/cookie and downloaded-file checks all passed. Retained workload downloads also passed their hashes. Save left no VM or scope; final cleanup left no owned helper. The base digest remained unchanged.

The raw summary reports `ok: true`, no failed gates, no stop reason, no sampler error and no retention error. The whole restore call to the guest-agent echo took 2.58 seconds. The separate first bare action after that call took 0.28 seconds. These are single smoke observations, not a performance result.

The parent inspected the diagnostic screenshots and reproduced all 131 Python tests before the live run. This validates the previously untested 640x797 fixture handling and the save/restore path on this VM. Portability to another compositor configuration remains untested. The fixed terminal changes the browser layout; do not combine its future memory curves with the earlier tiled-layout baseline.

The full three-boot memory experiment remains pending. Server, executor, chat, journal, holds, real models and concurrent capacity remain untested here. Earlier failed attempts and their budgets remain recorded above.

## Full run 2 on the pinned terminal (stopped by the host guard)

Run 2 used revision `229cd9c`, the pinned 640x797 terminal and the unchanged harness. It ran `--boots 3 --workload-s 1800 --max-wall-s 7200` with the default pace (6 s) and sample cadence (10 s). Guest: 2 vCPUs, 2 GiB, codec `none`, the same frozen base. No code, guard or threshold changed. No retry followed.

**The run is incomplete.** The existing `swap_out` rule stopped boot 1 after **497.6 s** of its 1,800 s workload. Boots 2 and 3 did not start. No 1,800 s workload completed in this run. Save, restore, the first bare action after the workload and the post-restore markers did not run. Between-boot variation is unmeasured.

### Preflight and drift

- No QEMU process ran. The diff of `scripts/vm-prototype` and `docs` between `229cd9c` and HEAD was empty.
- One 60 s host window passed the existing limits: 1,009 swap-out pages, memory pressure 0.18% of the window, CPU pressure 5.2%, 14.1 GiB available, VM swap 0. No stop reason.
- The parent test pass of the same code (131 tests, plus the pinned-layout smoke) was reused. This run added only a compile check. It ran no new tests.

### Boot 1 before the stop

| Item | Value |
|---|---|
| Workload window | 497.6 s (elapsed 539 s from launch to boot end) |
| Stop | `swap_out`, window 61.4 s: 22,968 pages (89.7 MiB) against the 16,384-page limit |
| Other readings in that window | memory pressure 2.1%, CPU pressure 9.4%, 12.4 GiB available, VM swap 0 |
| Guest swap, guest OOM, cgroup `oom`, `oom_kill`, `max`, `high` | none |
| Operations | 73 (navigate 28, snapshot 9, read 9, download 9, new tab 9, extra session 9) |
| Bare requests | 209; bare errors 0; error file 0 bytes |
| Downloads | 9 (18 MiB), none deleted, 18 MiB retained (cap 32 MiB); sessions at most 3 |
| Samples | 49 in the load window; gap median 10.23 s, maximum 10.42 s; no sampler or retention error; no PID reuse |
| Observer CPU | 2.79 s over 497.6 s (0.56% of one core) |

The host available memory was 12.4 GiB at the stop. The VM could not swap, because its cgroup sets `MemorySwapMax=0`. Other host work ran, and its share of the paging is unproven.

### Memory readings (first five minutes only)

All slopes cover the early window. No steady window exists. Labels are indicative.

| Metric | First | Last | Early slope per minute |
|---|---|---|---|
| Host QEMU RSS (MiB) | 531.2 | 589.8 | 8.5 |
| Host QEMU PSS (MiB) | 524.4 | 580.1 | 7.8 |
| Host cgroup anonymous (MiB) | 497.3 | 555.8 | 8.7 |
| Guest used, total minus available (MiB) | 339.1 | 344.2 | 2.0 |
| Guest page cache (MiB) | 219.8 | 261.8 | 6.4 |
| Guest anonymous pages (MiB) | 37.6 | 42.4 | 0.8 |
| Guest slab (MiB) | 47.5 | 47.0 | no trend |
| Guest shared memory (MiB) | 24.1 | 26.2 | no trend |
| Bare RSS (MiB) | 26.1 | 31.2 | 0.6 |
| Bare PSS (MiB) | 21.6 | 26.7 | 0.7 |
| Bare output file | 3.0 KB | 175 KB | 21.2 KB |

The bare output file grew 843 bytes per request. The guest runtime tmpfs grew with it. This file belongs to the Deck wrapper, not to the bare engine. The bare RSS and PSS rose about 5 MiB early. The run ended before the point where the first run showed no further trend, so no plateau claim holds. Raw RSS is not a leak.

### Compared with run 1

- Layout differs. Run 1 used tiled terminal and bare windows. Run 2 pins the terminal at 640x797 and opens bare windows at the full output width. Do not merge their curves.
- In the first minutes, host RSS slope (8.5 against 7.9 MiB per minute), guest cache slope (6.4 against 6.4) and bare RSS slope (0.6 against 0.7) are close. The windows are short and the layouts differ. This is no evidence about the layout effect.
- Hypothesis, to verify: host QEMU growth follows guest pages that the guest touched. Without a balloon, QEMU need not release freed guest pages. This does not mean host RSS must stay high forever. Guest used memory and cache are not disjoint, so the sum of their slopes proves no cause.

### Cleanup and integrity

- The harness reported no VM left and no QEMU on the host. A later check found no QEMU, fixture or harness process and no VM scope.
- The base digest was equal before and after (`a90de2e085a7ad52`).
- The trace, samples, summary, guest disk and logs are in a new private directory outside Git. Old failed and smoke directories were not touched.

### Not tested

- Any full 1,800 s workload, boots 2 and 3, and between-boot variation.
- State bytes, save and restore phase times, the first bare action after the workload and all pre and post markers, on a full-length boot. The 60 s smoke covers these only for a short workload.
- A host plateau, the server, executor, chat, journal, holds, real models and sites, concurrent capacity.
- The effect of the guest sample on the curves.

### Negative finding and next candidate

The unchanged host-wide `swap_out` rule stopped two full attempts (1,551 s and 497.6 s) with VM swap at 0 and over 12 GiB free. On this busy host the rule measures other work. The full experiment cannot finish under it. A stop rule on the VM's own swap and cgroup events, with host paging kept as context, is the next candidate. It needs a decision before any change. It is a proposal, not a user requirement. The other option is a run on a quiet host.

Run history addition: **Run 2** stopped at 497.6 s of boot 1 on `swap_out`. No retry followed.

## Prospective method revision: host paging context

The parent verified that the second full attempt remains failed in its raw summary, with one partial boot, no sampler/retention error and no remaining QEMU process. Neither failed attempt is relabelled.

Method `memory-v2-host-paging-context` changes only future memory experiments. A host-wide swap-out threshold crossing is retained in a `host_context` trace record, summary and evidence status. The summary marks such runs as host-paging-contended. The workload can continue unless another guard fires. VM/guest swap, OOM and cgroup limits, host CPU/memory pressure and low available memory still stop the run. The cycle benchmark policy is unchanged. See D52.

Two regressions failed before this revision and pass after it. One verifies that host paging alone does not stop the run while actual VM swap, pressure and low memory do. The other verifies that the paging window remains recorded. All 132 Python tests pass. This policy change is not a clean-performance claim. A new attempt must use a fresh output directory and retain all contention windows.

## Full run 3 under method `memory-v2-host-paging-context` (stopped by CPU pressure in boot 3)

Run 3 used revision `c848a57`, method version `memory-v2-host-paging-context` (D52) and no code change. It ran `--boots 3 --workload-s 1800 --max-wall-s 7200` with the default pace (6 s) and sample cadence (10 s). Guest: 2 vCPUs, 2 GiB, codec `none`, base `a90de2e085a7ad52`. Each boot is a fresh overlay and a cold boot. One VM ran at a time. No retry followed.

**The run is incomplete.** Boots 1 and 2 completed the full 1,800 s workload and all gates. Boot 3 stopped after 1,057.8 s of its workload on the `cpu_pressure` guard. Boot 3 ran no save, restore, first bare action or post-restore markers. This is not a completion claim for the 3-boot design. The earlier failed runs (1,551 s and 497.6 s) stay failed.

Raw evidence is in the private directory `~/.cache/deck/memorybench-v2-full-20261112-203712` (outside Git): `trace.jsonl`, `samples.jsonl`, `summary.json`, `evidence-status.json`, `boot-N/` with logs and disks.

### Preflight and drift

- No QEMU process ran. The diff of `scripts/vm-prototype` and `docs` between `c848a57` and HEAD was empty.
- One 60 s host window was evaluated with the memorybench guards (not the raw cycle rule): 1,040 swap-out pages (limit 16,384), memory pressure 0.15%, CPU pressure 0.39%, 14.5 GiB available, VM swap 0. No stop reason, no paging window.
- Reused the parent test pass (132 tests) and the pinned 60 s smoke. This run added only a compile check and `--help`. It ran no new tests and no build during the measurement.

### Stop

The stop window was 61.7 s in boot 3: **CPU pressure 29.4%** against the 25% limit. In the same window: memory pressure 2.1%, swap-out 17,741 pages (above the 16,384-page paging threshold, so recorded as context), 12.2 GiB available, VM swap 0. Guest swap, guest OOM and cgroup `oom`, `oom_kill`, `max` were 0. The harness killed the VM and kept the failed summary. The process that caused the CPU pressure is unproven. The other host work is not attributed.

### Durations and operations

| Boot | Workload window | Elapsed (launch to end) | Operations | Requests | Downloads | Result |
|---|---|---|---|---|---|---|
| 1 | 1,804.8 s | 1,869.8 s | 264 | 759 | 33 | complete, deadline |
| 2 | 1,803.7 s | 3,733.8 s (cumulative) | 264 | 759 | 33 | complete, deadline |
| 3 | 1,057.8 s | 4,831.1 s (cumulative) | 155 | 441 | 19 | stopped, `cpu_pressure` |

Boots 1 and 2 each did 99 navigate and 33 each of snapshot, read, download, new tab and extra session. Bare errors: 0 in all boots. Tabs and sessions stayed at most 3. Downloads stayed at most 32 MiB (16 files retained, 32 MiB). Retained downloads matched their hashes (16 checked, 0 bad) in boots 1 and 2. Median operation times were equal across boots (navigate 0.52 s, snapshot 0.21 s, download 0.73 to 0.75 s, new tab 2.04 s, extra session 1.73 s). These are observational: paging and CPU pressure windows occurred.

### Gates (boots 1 and 2)

| Item | Boot 1 | Boot 2 |
|---|---|---|
| Terminal pixels same before and after (pinned 640x797) | yes | yes |
| Save call total | 5.09 s (migrate 4.83 s) | 4.73 s (migrate 4.49 s) |
| State file bytes | 685,873,661 | 553,989,464 |
| Restore to first guest echo | 1.37 s | 1.22 s |
| First bare action after restore (`eval location.href`) | 0.364 s | 0.222 s |
| Sessions after restore | 2 | 2 |
| Post-restore markers (nano process, disk marker, shell file, download SHA, bare tab URL, login, cookie; notes file not written) | all pass | all pass |
| VM, scope and helpers left | none | none |

Save and restore times and state bytes are observational only. Host paging was flagged in this run, so they are not clean-performance claims. Boot 1 and boot 2 state sizes differ by 132 MiB. The cause is unmeasured.

### Sampling and observer cost

| Boot | Samples | Span | Gap median / max | PID reuse |
|---|---|---|---|---|
| 1 | 176 | 1,793 s | 10.23 s / 10.44 s | none |
| 2 | 176 | 1,792.7 s | 10.23 s / 10.63 s | none |
| 3 | 103 | 1,043.8 s | 10.23 s / 10.41 s | none |

Observer (sampler thread) CPU, from cumulative readings at each workload end: about 9.6 s in boot 1, 8.2 s in boot 2 and 5.3 s in boot 3, which is about 0.5% of one core in each. The boot 1 figure may include setup. No sampler error and no retention error.

### Memory readings

The late window is the samples after 600 s. The slope is per minute. The standard error assumes independent samples. The samples are autocorrelated, so the error is not a confidence interval. "Growing" is the harness label.

| Metric | Boot 1: first, last, late slope | Boot 2: first, last, late slope | Boot 3 (1,044 s only): first, last, late slope |
|---|---|---|---|
| Host QEMU RSS (MiB) | 661.6, 759.0, +1.93 (SE 0.05) | 535.1, 638.8, +2.30 (SE 0.06) | 524.1, 607.3, +1.31 (SE 0.06, 44 samples) |
| Host QEMU PSS (MiB) | 652.8, 750.3, +1.94 | 526.2, 630.0, +2.30 | 515.3, 597.5, +1.11 |
| Host cgroup anonymous (MiB) | 626.9, 725.8, +1.94 | 500.6, 604.4, +2.30 | 489.8, 574.3, +1.40 |
| Guest used, total minus available (MiB) | 335.4, 349.1, -0.05 (no trend) | 337.7, 333.7, +0.05 (no trend) | 332.5, 344.3, +0.39 (SE 0.16) |
| Guest page cache (MiB) | 343.6, 414.3, +1.52 | 214.3, 287.4, +1.49 | 215.0, 274.2, +1.73 |
| Guest anonymous pages (MiB) | 37.0, 42.5, +0.04 | 36.8, 42.2, +0.04 | 37.4, 43.0, +0.18 |
| Bare RSS (MiB) | 22.2, 27.4, +0.02 (no trend) | 22.6, 27.8, -0.01 (no trend) | 22.6, 29.9, +0.24 (SE 0.10) |
| Bare PSS (MiB) | 18.9, 24.0, +0.03 (no trend) | 19.1, 24.3, +0.01 (no trend) | 19.1, 25.5, +0.20 |
| Bare output file (KB) | 3.2, 649, +21.4 KB/min | 3.2, 649, +21.5 KB/min | 3.2, 383, +21.6 KB/min |

Host RSS, PSS and guest page cache first rise 8.5 to 9.0 MiB per minute in the first five minutes in all three boots. Then they slow to 1.3 to 2.3 MiB per minute. They do not stop growing within 30 minutes. **No host plateau is shown.** Guest used memory and bare RSS and PSS show no significant late trend in boots 1 and 2. Boot 3 has a shorter late window (44 samples) and shows a small positive slope. It ran under the highest host load and ended early. Do not read it as a difference between boots.

The bare output file grew 851 bytes per request in boots 1 and 2 (862 in boot 3), linearly with requests. The file lives in the guest runtime tmpfs and belongs to the Deck wrapper, not the bare engine. It is 0.65 MB at the end. That is small against the host growth, so it does not explain the host curve.

Host RSS and PSS are host-side numbers. Guest cache, available memory, anonymous pages, bare process memory and the runtime output file are guest-side numbers. The two sides are separate. Guest used and cache are not disjoint, so their sum is no accounting identity. Hypothesis, to verify: host RSS follows guest pages that the guest touched, and QEMU does not return freed pages without a balloon. Raw RSS growth is not a leak. This observational run cannot show a cause. Boot-to-boot start values differ (host RSS 661.6 MiB against 535.1 MiB at the first sample), so absolute levels do not compare across boots.

### Host paging context

The run recorded **3 paging windows** (swap-out above 16,384 pages in a 61 s window), all as `host_context` records, and `host_paging_contended` is true.

| Boot | Window | Swap-out pages | CPU pressure | Memory pressure | Available |
|---|---|---|---|---|---|
| 1 | one 61.7 s window | 22,075 | 15.2% | 0.33% | 10.1 GiB |
| 3 | 61.4 s | 29,191 | 0.29% | 2.9% | 11.7 GiB |
| 3 | 61.7 s (the stop window) | 17,741 | 29.4% | 2.1% | 12.2 GiB |

Range of the paging windows: 17,741 to 29,191 pages (69 to 114 MiB). VM swap was 0 in all windows. Other pressure was elevated in two: CPU pressure 15.2% (boot 1, under the limit) and 29.4% (boot 3, over the limit and the stop reason). Memory pressure stayed under 3%. Boot 2 had no paging window. The cause of the host paging is unproven. Because boots 1 and 3 were contended, no clean latency comparison and no causal leak claim holds from this run. The labels apply to all boots.

### Cleanup and integrity

- The harness reported no VM, no scope and no helper left in each boot. A later check found no QEMU, fixture or harness process.
- The base digest was equal before and after (`a90de2e085a7ad52`, size 1,493,565,440 bytes).
- `evidence-status.json`: no sampler error, no retention error, stop reason `cpu_pressure`. The failed summary was kept. No watcher, monitor or scheduled job was started.

### Not tested

- A complete 3-boot experiment (boot 3 stopped at 1,057.8 s), boot 3 save, restore, first bare action and markers, and an uncontended host.
- A host plateau, a leak cause, and clean latency or save and restore comparison.
- The server, executor, chat, journal, holds, real models and sites, concurrent capacity.
- Old tiled layout curves. They stay separate from the pinned layout curves.
- Bounded workload completion is not an optimization gain.

Run history addition: **Run 3** completed boots 1 and 2 under the D52 method. Boot 3 stopped at 1,057.8 s on `cpu_pressure`. No retry followed.
