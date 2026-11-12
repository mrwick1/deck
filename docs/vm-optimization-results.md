# VM optimization results

Date: 2026-11-12.

This document covers the five areas of the optimization plan that the VM harness can measure. Idle sleep and event batching are deferred. They need the executor and the task queue.

All numbers come from one host with QEMU 11.1 and KVM, 2 vCPUs and 2 GiB of guest RAM. The fixture is synthetic. No real account and no model call took part. Raw results stay outside Git.

## Summary

| Area | Status | Result |
| --- | --- | --- |
| 1. Idle sleep and event batching | Deferred | Needs the executor and the queue. No prototype scheduler was written. |
| 2. Shared base and private overlays | Implemented and measured | Base stays unchanged. Two overlays stay separate. |
| 3. Demand-driven capture | Audited and measured | Capture is already one-shot. Hash suppression cuts transmitted bytes, not capture cost. |
| 4. Service trim | Implemented and measured | Six units masked. Guest memory use −24 MiB, cold boot −3.5 s. Default kept: trim on. |
| 5. Saved-state compression | Implemented and measured | `zstd-1` cuts state by 70% at equal latency. Default stays `none`. |

## 2. Base image and overlays

Commands (`DECK_VM_BASES` and the runtime directories are outside Git):

```
python3 -I vmproto.py --dir $RT_BASE setup
python3 -I vmproto.py --dir $RT_BASE freeze-base          # read-only base, mode 0444, digest record
python3 -I vmproto.py --dir $RT_A overlay --base $BASE    # private qcow2 per bot
python3 -I vmproto.py --dir $RT_B overlay --base $BASE
python3 optbench.py isolation --dir-a $RT_A --dir-b $RT_B --out isolation.json
```

Rules in code: the base is frozen and read-only. The overlay records the base identity. A saved state is paired with the disk and the base. Restore refuses a mismatch before QEMU starts. `clean` never removes a base.

Measured (overlay A had earlier stage 1 files, overlay B was fresh):

- Base digest identical before and after both runs.
- B first boot held none of A's files. A kept its files and never saw B's private marker. Artifacts stayed in separate stores.
- A state record copied from A onto B was refused.
- Both overlays saved and restored with their own markers.

Total storage, including the shared base:

| Part | Bytes |
| --- | --- |
| Shared base | 1,349,062,656 |
| Overlay A + overlay B | 100,540,416 |
| Total without saved state | 1,449,603,072 |
| Saved state A + B | 841,748,480 (uncompressed) |
| Total with saved state | 2,291,351,552 |

The overlay alone is not the total. Each added bot costs its overlay plus its saved state. The base is paid once.

## 4. Service trim

Change in `guest/setup-guest.sh`: mask `systemd-homed`, `systemd-homed-activate`, `systemd-nsresourced` (service and socket) and `systemd-userdbd` (service and socket). Login, networking, time sync, the guest agent and the control channels stay. A fresh `setup` reproduces the change.

Inventory before the trim showed homed, nsresourced and userdbd running with their helper processes. After the trim they do not run.

Two full stage 1 runs per variant, alternating A, T, A, T on fresh overlays (each run: boot, bare, artifacts, five save/restore cycles, kill, cold boot). Every gate passed in all four runs.

| Metric | Baseline (2 runs) | Trimmed (2 runs) |
| --- | --- | --- |
| Launch to desktop ready | 17.5 s, 18.2 s | 14.0 s, 13.8 s |
| Guest memory used (`free`) | 358.8, 363.1 MiB | 333.8, 340.4 MiB |
| VM resident memory, host, after 60 s idle | 589,964; 584,900 KiB | 553,456; 557,584 KiB |
| Saved state, median | 478, 483 MiB | 450, 449 MiB |
| Save median | 4.04, 3.82 s | 3.63, 3.53 s |
| Restore median | 2.18, 1.73 s | 1.76, 2.02 s |
| Idle VM CPU over 60 s | 0.95, 0.76 s | 0.85, 1.04 s |
| Markers after restore | all | all |

Decision: keep the trim. Memory, boot time and state size improved in both pairs. Save, restore and idle CPU are within run-to-run noise. No gain is claimed for them. Each base comes from its own `setup`, so base size differs too (1.52 GB vs 1.49 GB).

## 5. Saved-state compression

QEMU 11.1 offers no native compression for a file save here. The capability list of the running VM showed `xbzrle`, `multifd` and `mapped-ram` all off, and the plan does not enable them. The harness therefore pipes the migration stream through an installed tool (`exec:` URI): `zstd-N` (levels 1 to 19) and `lz4`. No dependency was added. The tool must be installed on the host.

The codec is stored in the state record. Restore reads it from there. An unknown codec is refused before QEMU starts.

Command: one VM, every candidate in each of five rounds, rotating the order (paired by round):

```
python3 stage1.py --dir $RT --fixture-port 18180 --stop-fixture --codec none,zstd-1,zstd-3,lz4
```

Medians of five cycles per candidate. CPU is the VM scope including the compressor and decompressor. Peak memory is the sampled resident size of QEMU and its children.

| Codec | State | Save | Restore | Save CPU | Restore CPU | Save peak RSS | Markers |
| --- | --- | --- | --- | --- | --- | --- | --- |
| none | 459.8 MiB | 3.65 s | 1.70 s | 0.45 s | 1.26 s | 538,292 KiB | all, 5/5 |
| zstd-1 | 138.9 MiB | 3.60 s | 1.41 s | 1.76 s | 1.47 s | 569,364 KiB | all, 5/5 |
| zstd-3 | 128.1 MiB | 3.61 s | 2.20 s | 2.51 s | 2.29 s | 621,580 KiB | all, 5/5 |
| lz4 | 184.6 MiB | 3.62 s | 1.28 s | 1.31 s | 1.47 s | 642,436 KiB | all, 5/5 |

Paired difference in save plus restore time against `none`, median of five: zstd-1 −0.10 s, zstd-3 +0.79 s, lz4 −0.04 s. The spread between rounds is about ±1 s, so only zstd-3 shows a trend (slower).

Decision: the default stays `none`. `zstd-1` is the recommended candidate. It cuts state by 70% with no measurable latency cost and about 1.3 s more CPU per save. It needs `zstd` on the host and the sample is five pairs. The owner or the Stage 2 integration should enable it. `zstd-3` is not worth its CPU and restore cost. `lz4` saves less than `zstd-1`.

Note: an earlier run of the same experiment (all markers intact) failed its own `helpers_gone` gate in every cycle. The gate matched command text with `pgrep -f`, and a waiting shell of mine contained the VM name. The gate now checks the process ids recorded before the save. The clean rerun is the one reported above.

## 3. Capture audit

```
python3 optbench.py capture --dir $RT --out capture.json
```

- With nobody asking, nothing captures: 0 QEMU child processes, 0 guest capture processes (`grim`, `wf-recorder`, `wayvnc`), 0.0 s host helper CPU over 30 s. The VM used 1.1 s of CPU in that window for its own work. Capture is one-shot, so this is not a new gain.
- After the measurements nothing stayed running.

Fixed workload: 24 frames, 0.5 s apart, three typed changes. Median of three rounds. "Minus control" subtracts a window with no capture.

| Mode | Captured | Transmitted | Bytes sent | VM CPU minus control | Host helper CPU |
| --- | --- | --- | --- | --- | --- |
| PNG, every frame | 24 | 24 | 430,548 | 0.68 s | 0.009 s |
| PNG, hash suppress | 24 | 4 | 71,758 | 0.94 s | 0.011 s |
| PPM, hash suppress (host encodes PNG) | 24 | 4 | 114,031 | 0.26 s | 0.094 s |

Hash suppression cuts transmission by 83% (bytes) but not capture cost. The PNG modes pay the PNG encode inside QEMU. Capturing raw PPM and encoding only changed frames on the host moves the cost from the VM to a host helper and lowers VM CPU. The VM CPU numbers vary between rounds by several tenths of a second; treat the PPM gain as a direction, not a size.

## Host and guest memory

Reported apart: the host sees the QEMU resident size (about 555 MiB trimmed, 587 MiB untrimmed after 60 s). The guest sees 334 to 340 MiB used of 2 GiB (trimmed).

## Limitations

- One host, one fixture, one guest build. Five cycles per candidate. Variance between runs is about ±1 s on save and restore.
- Not tested: a wake storm, many overlays at once, base replacement with live overlays, the legacy standalone disk recovery path on a live VM, a saved state restored after the host `zstd` binary disappears.
- The new owned-process `helpers_gone` check has a unit test but no live rerun.
- Capture subscription control, idle sleep and batching belong to later stages. No scheduler or subscription framework exists here.
