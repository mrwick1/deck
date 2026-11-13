# Saved-state cycle benchmark

Date: 2026-11-12.

## Purpose

Measure what one sleep and one wake cost on the runtime path. Find avoidable latency before any change. Do not change production settings from this evidence. The codec stays `none`.

## Scope

`scripts/vm-prototype/cyclebench.py` boots three fresh private overlays on the frozen base. Each boot opens the synthetic session, then runs five save and restore cycles. One VM runs at a time. The run used no real account and no other browser. The session is the Stage 1 session: a disk marker, a shell file, a downloaded file, a bare login on the fixture, and unsaved text in the terminal editor.

The harness exercises the VM runtime only. It does not run the Deck server. These gates are **not tested** here:

- host journal receipts and recovery
- chat continuity
- budget and automation holds
- queue and idle-policy behavior
- real-model latency
- the release binary (the harness only records its hash)

Do not read these numbers as a server optimization.

## What each timestamp means

The harness wraps the existing save and restore calls. It records a monotonic timestamp at each QEMU and guest-agent call. Each phase is the difference of two observed boundaries. A remainder is reported as "unattributed". A cycle with a missing boundary fails the run.

- **Migrate** runs from the migrate command to the first poll that reports completion. The poll step is 20 ms.
- **Restore whole call** is QEMU spawn, state load, wait for the guest agent, clock set, and the first guest command. It is not pure VM restore. Use the "incoming command to running" row for the state load.
- **Clock set** is not timestamped on its own. The "agent ready to return" row contains it, together with one guest echo (about 0.2 s) and the state file unlink (about 0.03 s).
- **Post-restore probes** start when the restore call returns. They run in a fixed order: a second agent shell, the desktop check, one bare response.
- **Harness CPU** is the harness process CPU minus the sampler thread CPU. **Sampler CPU** is the sampler thread alone. **VM scope CPU** is the QEMU cgroup counter. These are three separate numbers.

## Method and controls

- Frozen base `a90de2e085a7ad52`, digest checked before and after the run. It was unchanged.
- Codec `none`. Three boots, five cycles each, 15 cycles, 255 s of wall time. The budget was 45 min and 15 cycles.
- One sampler thread read host pressure (PSI), swap counters, VM memory and I/O counters once per second. A failed sampler invalidates the run. Boundary snapshots added exact window deltas.
- Page-cache residency of the state file was recorded with `fincore` after each save. Nothing was evicted. `drop_caches` was not used.
- The cgroup `io.stat` file is not available in the user cgroup. The report records it as unavailable and uses per-process I/O counters instead.
- No build or test ran during the live window. The host was not idle: the one-minute load ranged 1.5–5.9 (16 CPUs) and other work ran. The run stops on host memory pressure, large swap-out, CPU pressure, or swapped guest memory.

After every cycle the harness checked: same editor process, unsaved text still on screen, file untouched on disk, shell file, disk marker, downloaded file checksum (pulled back to the host), bare tab URL, bare login and cookie, and a fixture read from inside the guest. All 15 cycles passed all gates. No VM, fixture or helper remained afterwards.

## Results

Cells show median (minimum–maximum). The first cycle after boot has three values. Later cycles have twelve. "Within-boot" is the median of the per-boot spread of later cycles. "Between-boot" is the spread of the per-boot medians of later cycles.

| Phase (s) | First after boot, n=3 | Later cycles, n=12 | Within-boot range (median) | Between-boot median range |
|---|---|---|---|---|
| Save: guest `sync` | 0.16 (0.16–0.16) | 0.16 (0.14–0.16) | 0.00 | 0.00 |
| Save: migrate command to completed | 4.98 (3.16–5.19) | 3.15 (3.12–5.10) | 1.81 | 0.31 |
| Save: quit to scope gone and metadata | 0.06 (0.06–0.06) | 0.06 (0.04–0.08) | 0.02 | 0.00 |
| Save: unattributed remainder | 0.02 (0.02–0.03) | 0.02 (0.02–0.06) | 0.01 | 0.01 |
| Save: whole call | 5.23 (3.40–5.43) | 3.40 (3.37–5.34) | 1.81 | 0.33 |
| Restore: QEMU spawn | 0.04 (0.04–0.04) | 0.04 (0.02–0.06) | 0.02 | 0.00 |
| Restore: incoming command to running | 0.91 (0.67–0.99) | 0.66 (0.43–1.33) | 0.57 | 0.20 |
| Restore: call start to running | 0.98 (0.74–1.08) | 0.72 (0.51–1.58) | 0.57 | 0.19 |
| Restore: running to agent ready | 0.10 (0.06–0.11) | 0.05 (0.03–0.10) | 0.04 | 0.02 |
| Restore: agent ready to return (clock set, echo, unlink) | 0.48 (0.38–0.70) | 0.70 (0.40–1.10) | 0.44 | 0.28 |
| Restore: whole call (to first agent shell) | 1.50 (1.47–1.66) | 1.43 (1.03–2.43) | 0.92 | 0.50 |
| After restore: second agent shell | 0.17 (0.17–0.17) | 0.18 (0.17–0.18) | 0.00 | 0.00 |
| After restore: desktop ready check | 0.20 (0.10–0.20) | 0.10 (0.10–0.20) | 0.10 | 0.05 |
| After restore: first bare response | 0.31 (0.20–0.40) | 0.30 (0.20–0.41) | 0.10 | 0.10 |

| Metric | First after boot | Later cycles | Within-boot range (median) | Between-boot median range |
|---|---|---|---|---|
| Save: VM scope CPU (s) | 0.42 (0.37–0.44) | 0.43 (0.34–0.63) | 0.07 | 0.04 |
| Restore: VM scope CPU since launch (s) | 1.06 (0.99–1.18) | 1.04 (0.59–1.76) | 0.95 | 0.53 |
| Save window: harness CPU, sampler excluded (s) | 0.63 (0.59–0.93) | 0.61 (0.54–0.94) | 0.36 | 0.16 |
| Save window: sampler thread CPU (s) | 0.22 (0.15–0.23) | 0.16 (0.13–0.28) | 0.10 | 0.07 |
| Restore window: harness CPU, sampler excluded (s) | 0.15 (0.14–0.19) | 0.15 (0.13–0.35) | 0.05 | 0.02 |
| Restore window: sampler thread CPU (s) | 0.06 (0.04–0.09) | 0.05 (0.04–0.12) | 0.05 | 0.04 |
| State file (MiB) | 429.2 (427.0–431.5) | 433.2 (429.2–437.2) | 7.7 | 1.3 |
| Overlay allocated after save (MiB) | 32.8 (26.4–33.6) | 42.1 (31.9–50.3) | 12.3 | 6.7 |
| Save: peak sampled VM RSS (MiB) | 526 (518–528) | 479 (466–491) | 9 | 17 |
| Restore: peak sampled VM RSS (MiB) | 461 (457–475) | 475 (461–486) | 9 | 18 |
| Save window: host I/O pressure (ms) | 375 (30–691) | 39 (21–251) | 161 | 11 |
| Restore window: host memory pressure (ms) | 125 (56–132) | 123 (58–272) | 161 | 38 |

Cold boot, for reference only: launch to first guest command 7.6–8.5 s, launch to desktop ready 13.6–14.4 s. A cold boot restores no session. This is not a wake-speedup claim.

Other recorded facts:

- The state file allocation equals its size. It was fully resident in the page cache after every save, so every restore here was warm. Disk-cold restore is **not tested**.
- No cycle swapped guest memory. Host swap-out stayed at zero in the save and restore windows of the accepted run.
- The state file is about 430 MiB. The sampled VM RSS is about 480–530 MiB.
- The scope memory counter read about 1 GiB just before QEMU quit. It counts page cache that the state write fills. Do not read it as guest memory.

## Findings

1. **Save is dominated by the migrate step.** It took 3.1–3.2 s in 10 of 15 cycles, 3.8 s in one and 4.9–5.2 s in four. The ten fast cycles wrote 135–139 MiB/s. This constant rate over a fully cached file looks like a rate limit, not a device limit. Hypothesis, to verify: QEMU's default migration bandwidth limit. The harness sets no limit. The installed QEMU was not queried for it.
2. **The slow saves do not follow "first after boot".** All four slow saves were cycle 1 or 2 of boots 1 and 2. Boot 3 had none, and its first cycle was fast. The slow saves had 190–690 ms of host I/O pressure in the window, against 21–72 ms for the fast ones. This fits host I/O contention. Hypothesis, to verify. One run cannot separate it from boot effects.
3. **Restore is the user-visible wake.** Its whole call took 1.0–2.4 s. State load is 0.4–1.3 s of it. The gap after the agent is ready is 0.4–1.1 s, and the largest piece is time before the first guest echo (0.14–0.87 s). That piece contains the clock-set call. The clock-set call is not timestamped on its own, so its share is unproven.
4. **Everything after restore is fast.** A second agent shell took 0.17 s. The first bare response took 0.2–0.4 s.
5. **Observer cost is not small.** The sampler thread used 0.13–0.28 s of CPU in a save window, against 0.34–0.63 s of VM scope CPU. The harness does not measure how much the sampler changes the latency. A run without the sampler was not made.
6. **Cycle count growth.** The overlay grew from 26–34 MiB to 32–50 MiB across a boot. Five cycles are too few to call this a trend.

## Candidate

No avoidable latency is proven. Two items justify one controlled follow-up each. Neither is a recommendation to change a default.

- **Migration bandwidth.** First read the effective limit with `query-migrate-parameters` on a booted VM. Then compare one raised limit with a fresh reference, reverse the order, and repeat across boots. Save runs when the bot is idle, so a faster save helps resource turnaround more than user wait.
- **Restore post-agent gap.** Timestamp the clock-set call on its own. Then decide whether it can leave the critical path. This needs the same ordering and repeat controls.

The default idle sleep policy and the codec need no change from this evidence.

## Run history

- **Run 1 stopped after one cycle.** The contention gate flagged any host swap-out. It fired on 717 pages (2.8 MiB) of background host paging with 17 GiB available. That was a defect in the gate. A failing test was written first. The gate now needs more than 64 MiB of host swap-out per cycle, or any swapped guest memory. Run 1 evidence is kept. Its cycle passed every correctness gate. It is not included in the numbers above.
- **Run 2** is the accepted run above. It was the only run after the fix.

## Verification

- The existing 65 Python tests and 22 new tests pass.
- New tests cover phase accounting from synthetic events, incomplete boundaries, a mismatch between harness and observed totals, each corrupted marker, a missing marker key after a bare error, retention of partial cycles after a failed boot, rejection of a run with a failed sampler, the output directory rules, a second running VM, and the contention gate.
- Compilation, `--help` (no VM starts) and `git diff --check` pass.
- Not tested: disk-cold restore, the Deck server path, cgroup `io.stat`, a sampler-off comparison, real-model latency, concurrent bots and any other codec.

Raw JSONL, samples, disks, state files and logs stay outside Git in a private directory per run.
