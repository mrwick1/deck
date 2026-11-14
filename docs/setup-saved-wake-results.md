# Saved-state wake acceptance

Date: 2026-11-13.
Source head: `7c2be62`. Binary: debug build from `b48fcd9`. SHA-256: `3efcc735766a330ed2f52c269404ec876e656f98d3b15d360468f03ac0548268`.
`git diff b48fcd9..HEAD -- src web Cargo.toml Cargo.lock` was empty. The binary matches the source at `HEAD`. It does not carry `HEAD` metadata.
Frozen base: `a90de2e085a7ad52`, mode 0444, SHA-256 unchanged after the run.
Fidelity: real server, real Store, real Python executor, one fresh private VM. The model is a scripted adapter that plays two shell scripts. There is no service, no credential, no browser and no paid model. The harness drives the HTTP API. It does not run the web UI.
Default host-pressure limits. Start load 2.91, 12259 MiB available.

## Command

```sh
python3 scripts/vm-prototype/setup_saved_wake_e2e.py \
  --base <frozen-base.qcow2> --deck <deck-binary> --out <short-output-dir>
```

New files: `setup_saved_wake_e2e.py` and its tests. The harness reuses `Stack`, `Manifest` and the cleanup registry of `setup_e2e.py`. It changes no existing file.

## Runs

- Run 1 failed on a harness defect. The wire form of the hold kind is `automation_pause`. The harness expected `AutomationPause`. The first task, the idle save and the saved-state artifact had passed. A fake that used the wire form failed 12 tests before the fix and passed after it. This is not a product result.
- Run 2, on a fresh directory: exit 0. 34 gates: 33 pass, 1 `not_tested` (`raster_secrecy`, optional), 0 fail. `acceptance_complete`: 32 required gates passed.

## Evidence from run 2

The host reads the guest through QGA and the executor journal. It does not trust the model text.

- First task: a disk marker, a memory-only marker in `/dev/shm`, the boot id and one effect line. Guest uptime 14 s.
- Idle save: the state file holds 466131914 bytes, codec `none`. No VM process runs.
- Pause while saved, then a server restart: the state metadata hash and size are unchanged. The computer is still saved. The server counters (model calls, captures, wakes) did not move over the settle window. The harness model count stayed at 2.
- The second task, submitted while paused, returned `held: automation paused` and stayed `queued`. A second restart kept exactly one queued task. Nothing woke the computer.
- Release: one wake. The QEMU pid changed. The state file and metadata were consumed. The boot id is the same. The memory marker survived. Uptime went from 14.17 s to 76.65 s. A cold boot would reset the first two.
- Effects: the guest log holds the first line once and the second line once. The second script reported `marker_ok=1 mem_ok=1 boot_same=1`.
- Identity: executor `bot_id` and `guest_id` are unchanged. The journal epoch went from 1 to 2. The second task's commands carry epoch 2.
- Task ids: the first is `setup-<id>`. The second is a different id. Each appears once.
- Limits: accounting ceiling, allowance, period and policy version are unchanged. No hold remains after release. Unresolved exposure is 0.
- The model made exactly two tool calls (two `shell`) and four requests.
- A restart with the computer awake, then a second idle save, then a restart while saved again: tasks, model requests and effects are unchanged. No second wake.

## Fake flow tests

29 tests, no VM. A simulation of Deck and the guest drives the whole flow through the restart and second-task branches. It is a mock. Each fault turns off one property and must fail its gate: cold boot, duplicate effect, changed guest, unchanged epoch, changed ceiling, unknown outcome (stops with no retry), activity while saved, a wake caused by a restart, a dropped or duplicated held task. One dropped-task case first raised `TypeError` in the harness. It now fails its gate.
Existing `setup_e2e` and `setup_synthetic_login` tests pass unchanged (121 tests with the new ones). The full prototype suite passes 366 tests with 4 skipped, run when no VM was live.

## Not tested

- Restart between dispatch and response, and restart during a held model reply.
- The web UI with a real VM.
- A second wake after the second save. The run ends saved.
- Real services, real models and real credentials. The `/dev/shm` marker proves memory restore. It is not a statement about every guest process.
- Numeric budget policy, raster secrecy and performance.
- The run did not check that the guest effect log is unreadable while saved. A saved computer has no QGA socket.

## Cleanup

Registered process identities: none survive. The overlay is deleted. The state file is deleted. The lock is free. Metadata stays in a private directory outside Git. The earlier run 1 evidence is unchanged.
