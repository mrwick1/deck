# VM Stage 1 results

Date: 2026-11-12.

## Verdict

One full Arch Linux desktop guest runs `bare` in window mode under rootless QEMU/KVM. Five save/restore cycles keep every tested marker. All measured performance targets pass with a wide margin. The guest executor, Deck integration, the host journal and the fencing gates are not part of Stage 1. They are NOT TESTED.

The guest OS is minimal Arch Linux (owner clarification, D44 in the parent decision log). The only browser is `bare`. No Chrome, Xvfb or VNC exists.

## What was built

All files are in `scripts/vm-prototype/`.

| File | Purpose |
| --- | --- |
| `vmproto.py` | `setup`, `start`, `stop` (save), `restore`, `kill`, `status`, `exec`, `capture`, `clean`. Standard library only. |
| `stage1.py` | The scenario: boot, bare, captures, five cycles, kill, cold boot. Writes `results.json` in the runtime directory. |
| `fixture.py` | Synthetic fixture service on loopback. It also serves a host-only marker on a second port. |
| `guest/` | First-boot provisioning (`setup-guest.sh`, `user-data`), the sway config and three small guest helpers. |
| `test_vmproto.py` | Unit tests: process identity, state/disk pairing, QEMU argument limits, zombie handling. |

The runtime directory is outside Git. Set `DECK_VM_DIR` or pass `--dir`. It holds the base image, the disk, saved state, sockets and artifacts. `.gitignore` also ignores `scripts/vm-prototype/run/`.

## Design

- Backend: QEMU/KVM, 2 vCPUs, 2 GiB RAM, one qcow2 disk (16 GiB virtual), `virtio-vga` 1280x800 with `-display none`.
- Guest: Arch cloud image, then `pacman -Syu` and these packages: sway, foot, grim, wtype, nano, qemu-guest-agent, curl, ttf-dejavu. Sway renders in software (`WLR_RENDERER=pixman`). `bare-mcp` is copied in from the host through the seed disk.
- Host control uses two unix sockets only: QMP and the QEMU guest agent over virtio-serial. The guest has no host mount, no 9p or virtiofs share and no `hostfwd`. The host opens no listener for the guest.
- Run-mode network: `restrict=on`. One `guestfwd` points `10.0.2.100:80` at the loopback fixture. QEMU starts one `socat` helper for each guest connection. The `tcp:` form of `guestfwd` shares one backend connection, so only the first request worked. The `cmd:` form fixed it.
- Setup mode uses an open user network and a read-only seed disk. This mode is used only for provisioning.
- Limits: the VM runs inside a user systemd scope with `MemoryMax=3500M`, `MemorySwapMax=0` and `RuntimeMaxSec=10800`. The limits were applied. They were not stress-tested.
- Save: `sync` in the guest, then QMP `migrate` to a `file:` URI, then `quit`. Restore: start QEMU with `-incoming defer`, then `migrate-incoming`.
- Pairing: each save records the size, mtime and inode of the disk. Restore refuses a state file when the disk changed. Restore deletes the state file after a successful load, so old memory cannot load against a newer disk. The refusal path has a unit test. It was not triggered in the live run.
- Bare in the guest: one `bare-mcp` process per desktop session, fed through a FIFO. This is a test shim, not the Stage 2 executor.

## Reproduce

```
export DECK_VM_DIR=/path/outside/git
python3 -I scripts/vm-prototype/vmproto.py setup        # download, verify, provision (minutes)
cd scripts/vm-prototype && python3 stage1.py --stop-fixture
python3 -m unittest test_vmproto
```

`stage1.py` starts the fixture itself. The fixture must run before QEMU starts, because `guestfwd` checks the helper at launch. Single steps: `vmproto.py start|stop|restore|kill|capture|status`. Read `$DECK_VM_DIR/results.json` for all samples.

## Versions and host

- QEMU 11.1.0. Host kernel `7.1.9-zen1-2-zen`, 16 logical CPUs, about 21 GiB available, load average 0.7.
- Guest: kernel 7.2.15-arch1-1, sway 1:1.12-4, foot 1.28.0-2, grim 1.5.0-2, wtype 0.4-2, nano 9.3-1, qemu-guest-agent 11.1.3-1.
- Base image: Arch Linux cloud image (build of 2026-11-01), SHA-256 `794eb4889b39e1f0…` (checked against the published checksum).
- `bare-mcp` 0.1.0, SHA-256 prefix `4071d82672854339`.

## Measurements

One host, one image, one run of five cycles. Host load was low.

| Metric | Target | Samples | Median | Max | Result |
| --- | --- | --- | --- | --- | --- |
| Idle resident memory (QEMU and helpers), 60 s idle | ≤ 3 GiB | 599,628 KiB (586 MiB), one sample | – | – | PASS |
| Cold boot to first guest command | ≤ 45 s | 10.56 s, one sample | – | – | PASS (see note 1) |
| Cold boot to desktop ready (sway, nano, bare running) | – | 16.33 s, 16.77 s (after kill) | 16.55 s | 16.77 s | info |
| Save: quiesce plus migrate plus exit | ≤ 15 s | 3.82, 5.89, 3.82, 3.81, 3.92 s | 3.82 s | 5.89 s | PASS |
| Restore to a successful guest command | ≤ 15 s | 1.36, 0.50, 1.07, 1.18, 1.48 s | 1.18 s | 1.48 s | PASS |
| Saved state size | ≤ 3 GiB | 513.1, 513.5, 510.5, 510.1, 523.2 MB | 513.1 MB | 523.2 MB (0.49 GiB) | PASS |
| Disk allocation after the workflow | ≤ 10 GiB | 1,397,362,688 bytes (1.30 GiB); virtual 16 GiB | – | – | PASS |
| Resident memory after save | 0 | 0 in all five cycles (process gone, no helper) | 0 | 0 | PASS |
| Desktop capture, QEMU screendump | ≥ 1 per s | 10 samples, 23–31 ms | 24 ms | 31 ms | PASS (rate only) |
| Desktop capture, grim plus transfer to host | – | 0.31–0.54 s | 0.41 s | 0.54 s | info |
| Resident memory after each restore | – | 590,332; 590,024; 589,984; 591,964; 594,344 KiB | 590,332 KiB | 594,344 KiB | info |

Notes:

1. The target says "authenticated ready response". Stage 1 has no executor and no authentication. The measure is launch to a successful command through the guest agent.
2. Quiescence: the 0.2 s in each save is `sync` plus the agent round trip. No command was outstanding, so no wait for active work occurred. Waiting for active work is NOT TESTED.
3. The state size is 0.49 GiB for a 2 GiB guest. A busier guest will save more.
4. Idle memory was measured once, at the start, with the workflow state already built. The guest also reported about 344 MiB used inside.
5. Save sample 2 (5.89 s) was slower than the rest. The cause is unknown.
6. The first provisioning run took several minutes. It was not timed.

## Demonstration steps

| Step | Result |
| --- | --- |
| 1. Start from persistent disk | PASS for the VM. Connecting Deck to an executor: NOT TESTED. |
| 2. Shell command and file in the guest | PASS. A file made by a guest command survived all cycles and the kill. |
| 3. bare on a fixture page | PASS. bare opened the page in a Wayland window, read it, followed links, typed into a form, submitted it and downloaded a file. |
| 4. Screenshot and download into the host | PASS for byte transfer. The host pulled both files through the guest agent. Their SHA-256 matched the guest checksums and the known fixture bytes. Showing them in chat: NOT TESTED. |
| 5. Desktop capture and bare capture | PASS. QEMU `screendump` and `grim` both capture the whole desktop. bare's own screenshot shows only the page (636x796). Visual check: the desktop image shows nano with the unsaved text beside the signed-in bare window. |
| 6. Live activity in Deck | NOT TESTED |
| 7. Steering | NOT TESTED |
| 8. Stop a long command | NOT TESTED |
| 9. Save at a quiescent boundary | PASS for the save. After each save the VM process and `socat` helpers were gone and resident memory was zero. The fixture stayed alive. The disk and state were kept. |
| 10. Restore | PASS. See the gates. |
| 11. Kill, then cold boot | PASS. See below. |
| Desktop input by a controlled command | PASS. `wtype` typed the text into the focused terminal window. |

Kill and cold boot: the harness checked the process name and runtime directory, then sent SIGKILL to that one process. It exited. A cold boot of the same disk took 16.77 s to a ready desktop. The disk markers survived: the shell-created file, the disk marker, the downloaded file and a marker written after the last restore. The volatile state was lost: nano restarted with an empty buffer, the bare tab and its login were gone (`no live sessions`), and the terminal pixels differed. Reconciling the task from host records: NOT TESTED.

## Gates

| Gate | Result |
| --- | --- |
| Boundary | PASS as a smoke check. A host-only marker on a second loopback port was unreachable from the guest through the gateway and through the forward address. The loopback fixture port was unreachable through the gateway address. The only `fuse`-type mount in the guest is the kernel `fusectl`. No share exists. This does not prove VM security. |
| Guest identity | NOT TESTED |
| Artifacts | PASS for checksums (screenshot and download). The host stores them without any guest path. The artifact store belongs to Stage 2. |
| Save consistency | PARTIAL. Save happened after `sync` with no command outstanding. The pairing check exists and has a unit test, but the live run did not try a mismatched restore. The wait for outstanding commands is NOT TESTED. |
| Resume | PASS. Five cycles. After each restore the checks found: the same nano process and an unsaved buffer (same process, notes file still empty, same terminal pixels), the bare tab on `/account`, the fixture login (page text and session cookie), the stored files and the disk marker. The fixture ran across all cycles. |
| Restore epoch | NOT TESTED |
| Stop | NOT TESTED |
| Host recovery | NOT TESTED |
| External action uncertainty | NOT TESTED |
| Saved-state recovery | NOT TESTED |
| Late replies | NOT TESTED |
| Policy | PASS. The display path uses sway, QEMU `virtio-vga` and `bare` only. No other browser, no Xvfb, no VNC. |

## Findings and gaps

- Window mode needs a Wayland compositor in the guest. Deck today runs `bare` headless (see `docs/architecture.md`). That text describes the old scope. Stage 2 must decide which mode the executor uses. Window mode is what makes a real desktop capture show the browser.
- The guest agent runs commands as root and carries the executor traffic in this prototype. The Stage 2 design must decide whether to keep it or replace it with a narrow guest protocol. Its commands need a user switch, as the test does with a small wrapper.
- The agent answers before the desktop session is ready. A client must wait for the desktop (the harness polls for it).
- `bare-mcp` must be restarted after a cold boot. Only a restore keeps its process. The prototype re-initializes it after a cold boot.
- A QMP socket serves one client at a time. Close each client fully.
- The `sandbox` option of QEMU (seccomp) was not tried. The `cmd:` forward needs `spawn`, so the option needs a check.
- `D13` (bubblewrap shell) is replaced for this design by the VM boundary. `docs/decisions.md` in this branch does not record that. The parent owns that change.
- Only a mild load was present. A busier guest will change the save time and the state size.
- A second download of the same file got the name `report (2).csv`. The test hashed `report.csv`.
- No bare issue was needed. No bare tool failed.

## Next step

Start Stage 2: the guest executor and the host record. Start with command identifiers, deadlines, cancellation acknowledgement, artifact retrieval and a restore epoch. Keep the QEMU lifecycle in `scripts/vm-prototype/` as the test base. Run the stale-reply, cancel and recovery gates with fakes first, then repeat them on this VM.

## Parent verification

The parent reproduced the scenario on a separate copy of the provisioned disk. The parent inspected the desktop capture and confirmed the unsaved editor buffer beside the signed-in bare page. All five save/restore cycles preserved the required markers. Strict result validation passed.

| Metric | Independent reproduction |
| --- | --- |
| Idle host resident memory | 583,052 KiB after 60 seconds; one sample |
| Boot to guest command | 10.72 seconds; one sample |
| Boot to desktop | 16.68 seconds |
| Save samples | 3.716, 3.740, 3.721, 5.773, 3.705 seconds |
| Restore samples | 0.943, 1.055, 1.471, 0.719, 1.002 seconds |
| Maximum saved state | 500,975,601 bytes |
| Cleanup | No prototype VM or fixture remained running. |

After SIGKILL and cold boot, files survived. The unsaved buffer, browser tab and fixture login did not survive. This confirms the distinction between saved-state resume and crash recovery. It does not establish account session durability after a crash.

The parent fixed review findings in the harness:

- Reject a duplicate start before replacing a running VM's control sockets.
- Fail the scenario when required correctness checks fail or are missing.
- Clean up the owned VM and fixture when the scenario raises an exception.
- Refuse to reuse an occupied fixture port.
- Use the user's cache directory as the portable default runtime location.

Nine unit tests pass. Regression tests first failed for duplicate-start handling and missing result validation. Python compilation, shell syntax checks and diff checks pass.

Verdict: Stage 1 feasibility is reproduced. The full prototype checklist is not complete. Authenticated readiness, guest cancellation, host journals and epochs, Deck delivery and activity, and two-bot isolation remain untested. Idle memory and initial boot still have only one sample per run. End-to-end display latency is untested. The Stage 2 control path must replace or constrain the prototype's root guest-agent command interface.
