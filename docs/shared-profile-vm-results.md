# Shared profile real-guest check: blocked

Date: 2026-11-14.
Source: integration branch `shared-browser-state` at `408ec3d`, imported as three cherry-picks on baseline `44f0bff`.
The imported diff touches only `guest/bare-call`, `test_multi_account.py`, `docs/decisions.md` and `docs/shared-browser-state-results.md`.

## Result

The guest run did not start. No VM, overlay, server or lock was created. No gate has evidence.

## Blockers

- Another end-to-end run held the VM lock (`setup_e2e.take_lock`). Its owner was a `setup_ui_e2e.py serve` process that this task did not start. The check ran twice, about 15 s apart. Both times the lock was held.
- The host load average was 16.0 to 17.5 on 16 CPUs. The unchanged guard allows at most 8.0. Memory was sufficient (about 15 GiB available against a 6 GiB minimum).
- The task allows one VM at a time and forbids killing unknown processes. The task also forbids changing the guards. So the run stopped.

A frozen base exists in the local base directory. The base was not opened, hashed or booted.

## Check that ran

```sh
python3 -m unittest discover -s scripts/vm-prototype -p 'test_multi_account.py' -v
```

Exit 0. 15 tests ran. 4 live tests were skipped because `DECK_LIVE_BARE` was not set. This repeats the parent's host-only result. It is not guest evidence.

## Gate status

| Gate | Status |
| --- | --- |
| Base digest unchanged | not tested |
| Guest adapter hash equals candidate | not tested |
| One bare session `main` | not tested in a guest |
| Two identities in one cookie jar | not tested in a guest |
| Switches show the intended identity | not tested in a guest |
| State file mode 0600, private directory | not tested in a guest |
| Explicit save, then restore after a fresh MCP process | not tested in a guest |
| Both switches after restore | not tested in a guest |
| VM save and wake, same boot, no duplicate fixture action | not tested |
| Full host and API route | not tested |

## Preparation at the first blocked checkpoint

At this checkpoint, the harness was not written yet. The later attempt below uses the completed harness. Run when the lock is free and the load is under 8.0. Reuse `Stack`, `take_lock`, `create_overlay`, `install_executor` and `save_vm` / `restore_vm`. Serve the `Service` fixture on the host loopback. The guest reaches it at the forwarded fixture address. Keep the browser restart and the VM save and wake as separate steps. Do not label the fixture as Google evidence.

## Live attempt 1 (harness at `100d3f2`): lock held, nothing started

Date: 2026-11-14.
Source: the six commits `bc76fb8` to `100d3f2` imported in order on baseline `44f0bff`. The final tree equals `100d3f2`.

### Checks

- Offline: `python3 -m unittest discover -s scripts/vm-prototype -p 'test_shared_profile_e2e.py' -v`. Exit 0. 31 tests ran, 31 passed.
- Live: `python3 scripts/vm-prototype/shared_profile_e2e.py --base <frozen base .qcow2 with sidecar> --out <new short private dir>`. Exit 4 (lock held). I ran it twice, about 10 s apart. Both runs gave exit 4.
- Output: `STOPPED: another setup end-to-end run holds the shared VM lock. Nothing was started.`

### Guards at the time

- The lock holder was a `setup_ui_e2e.py serve` process and its VM. This task did not start them. The process had run for about 12 minutes.
- Host load average was 2.8 (15 minutes: 9.4). The limit is 8.0. Memory was sufficient (about 14 GiB available against 6 GiB).
- The frozen base exists with its sidecar, mode `0444`. Its size and mode were the same before and after. The harness never opened it, because the lock check comes first.
- The guards were not bypassed. No other run was stopped. The harness was not changed.

### Cleanup

No VM, overlay, fixture, output directory or saved state was created. This task owns no process. The lock is still held by the other run, not by this task.

### Gate status

All gates are `not run`. This includes the source and adapter hashes, the single `main` session, both identities, switching, file privacy, restore across a `bare-mcp` restart, the VM wake, duplicate login effects, and the base and cleanup gates. The four live seams in the plan stay untested: the `as-deck` environment after restart, the guest `bare-mcp` path, the RPC reply shape and the fixture forwarding.

### Limits

No guest evidence exists yet. This is a synthetic fixture only. It makes no claim about Gmail, memory use or the full host route. Run again when the lock is free.

## Live attempt 2 (source `d98fde4`): first real guest run, not accepted

Date: 2026-11-14.
Source: parent `shared-browser-state` at `d98fde4375dc882e7d9e78418645dd2a995c3ad6`, fast-forwarded from `44f0bff`. `git diff d98fde4 HEAD` was empty before this section.

### Owner-authorized separate lock

The owner said to bypass the shared VM lock for now. This run used the existing `--lock` flag with a new lock file outside Git. The shared lock was held by another end-to-end run. This task did not touch that lock or that run. Its VM and process were unchanged after this run.
This permission did not waive the load guard, the memory guard, cleanup or base integrity.
Limit: two VMs ran at the same time. This run makes no performance or memory claim. It is a functional check only.

### Command

```sh
python3 scripts/vm-prototype/shared_profile_e2e.py --base <frozen base .qcow2 with sidecar> \
  --out <new short private dir> --lock <new task-owned lock file> --fixture-port <free loopback port>
```

Exit 1 (not accepted). The offline suite ran first: 31 tests, 31 passed.

### Collision checks

- The fixture port had no listener. QEMU guest forwarding is per VM (`guestfwd` in user networking, one `socat` to the fixture port). It binds no host port.
- The QEMU control sockets and the overlay sit in the new output directory. The other run uses a different directory.
- The base is mode `0444`. Both runs only read it.
- Guards at start: load average 4.3 (limit 8.0), about 16 GiB available (minimum 6 GiB).

### Hashes

- Guest adapter `bare-call` equals the candidate: `0a0a05d0a79722fa631dbd19291c74e31ce5c8f09c96ac568bbbb29cdefe64aa`.
- Guest `bare-mcp`: `bc8d33dc325b5b534e73042a81e605a3190827ce77ff33088e0f94e187da7c17`.
- Host `bare-mcp` (differs from the guest one): `18488a21c5ede585bd60980e2681ca46e72ad6acd340d468c4b019b9435d42bf`.
- Harness: `024b650aa7587eaea376066423d2dca03cce79750e9b5f6c8569d7c9b1d73e9f`.
- Base id `a90de2e085a7ad52`.

### Gate results

| Gate | Result |
| --- | --- |
| `base_frozen_and_verified` | pass |
| `guest_adapter_hash_equals_candidate` | pass |
| `both_accounts_signed_in_together` | pass (A, then A and B in one session) |
| `alternating_identity_before_save` | pass (A, B, A, B) |
| `one_bare_main_session_one_cookie_record` | pass |
| `state_private_dir_and_file_0600` | pass |
| `state_saved_with_cookie_record` | pass |
| `mcp_restart_new_process` | pass (one process before, one after, none shared) |
| `restore_from_disk_and_switch_both` | not run: the flow stopped |
| `vm_wake_same_boot_and_profile` | not run |
| `no_duplicate_fixture_effects` | not run |
| `base_unchanged` | pass |
| `no_owned_process_left` | pass, no survivors |
| `acceptance_complete` | fail |

### Failure

After the restart, `open state=shared url=<fixture>` failed in the guest: `unknown argument "file": open takes downloads_dir, height, report, secrets_file, session, snapshot, state, timeout_ms, url, vars, width, window`.
The cause is not the harness. The adapter `prepare()` adds `file` to `open` when `state` is set. The guest `bare-mcp` in the frozen base has no `file` argument on `open`. The host `bare-mcp` has it. The host-only tests passed because they use the host binary.
No harness correction was made. A change to `ensure_session` or to the adapter would hide this mismatch. The gates stay unchanged.

### Cleanup

The harness stopped only its registered VM and fixture. The overlay was removed. The task-owned lock file was removed. The shared lock and the other run were not touched. The evidence directory (`manifest.json`) is kept outside Git.

### Next step

Provide a frozen base whose `bare-mcp` accepts `file` on `open`, or change the adapter and `bare` together. Then run again. Nothing here supports real Gmail.

## Live attempt 3 (source `f0e26fa`): pinned browser candidate, accepted

Date: 2026-11-14.
Attempt 2 stays as recorded above. Its manifest is kept unchanged in its private directory.

### Setup

The owner allowed a separate lock again. This run used a new lock file, a new output directory and a free fixture port. The shared lock and the other run were not touched. The load guard (8.0) and memory guard (6144 MiB) were unchanged. The run waited about 17 minutes (34 refusals, exit 3, nothing started) until the load average fell below 8.0. At start it was 7.06. At end it was 8.81. Another run was active during this time, so this run makes no performance or memory claim.

```sh
python3 scripts/vm-prototype/shared_profile_e2e.py --base <frozen base .qcow2 with sidecar> --out <new short private dir> \
  --lock <new task-owned lock file> --fixture-port <free loopback port> \
  --bare-candidate <host bare-mcp> --expect-bare-sha256 18488a21c5ede585bd60980e2681ca46e72ad6acd340d468c4b019b9435d42bf
```

Exit 0 (accepted). Offline suite before the run: 45 tests, 45 passed (the 31 earlier tests and 14 new ones).

### Candidate handling

- The harness checked the candidate (readable executable regular file, expected digest). It then copied it to a private `0500` artifact in the output directory and checked the copy. The host file was only read.
- In the guest overlay only, it stopped the one `bare-mcp` (its `/proc/<pid>/exe` was the installed path), staged the file under another name, checked its digest and `ldd`, renamed it over `/usr/local/bin/bare-mcp`, checked the installed digest, started `bare-session` and waited for readiness.
- The frozen base was not modified. Its digest check passed before and after. The standard image is not updated by this run.

### Identities

- Original guest `bare-mcp`: `bc8d33dc325b5b534e73042a81e605a3190827ce77ff33088e0f94e187da7c17`.
- Installed candidate (actual browser in the run): `18488a21c5ede585bd60980e2681ca46e72ad6acd340d468c4b019b9435d42bf`, 20362312 bytes, ELF x86-64, BuildID `43de293ac9301f0ef89b2c2341b36b4a85749a05`, rustc 1.96.0. Needs `libxkbcommon`, `libgcc_s`, `libm`, `libc`. The guest `ldd` found them.
- Guest adapter `bare-call`: `0a0a05d0a79722fa631dbd19291c74e31ce5c8f09c96ac568bbbb29cdefe64aa`, equal to the candidate adapter.
- Harness: `1d4747cfc471a3ff5d358096686744a0893ecf1f9d2c80e1187f2b1e652d419a`. Base id `a90de2e085a7ad52`.
- Provenance limit: the candidate is the host binary in use on this machine. This task did not rebuild it from a known source commit or release. The private pinned copy is the only record. Do not call it a release.

### Gate results

All 15 required gates passed: `base_frozen_and_verified`, `bare_candidate_installed_and_ready`, `guest_adapter_hash_equals_candidate`, `bare_mcp_sha256_equals_expected`, `both_accounts_signed_in_together`, `alternating_identity_before_save`, `one_bare_main_session_one_cookie_record`, `state_private_dir_and_file_0600`, `state_saved_with_cookie_record`, `mcp_restart_new_process`, `restore_from_disk_and_switch_both`, `vm_wake_same_boot_and_profile`, `no_duplicate_fixture_effects`, `base_unchanged`, `no_owned_process_left`. `acceptance_complete` passed.

- One `main` session held both accounts. The service held one record, one login for A and one for B.
- After a new `bare-mcp` process, `open state=` restored both accounts from the file, and A, B, A switched in one session.
- The VM save took 4.1 s and the wake 1.4 s. The boot id, `bare-mcp` pid and memory marker were the same, and the state file was unchanged. Both accounts still switched.
- No registered process survived. The overlay and the task-owned lock file were removed.

### Product and harness conclusion

No harness correction was needed beyond the candidate install. The failure in attempt 2 was a browser version mismatch: the guest `bare-mcp` lacked `open` argument `file`. With a `bare-mcp` that has it, the flow passes in a real guest.
Production support still needs a guest image whose `bare-mcp` matches the adapter. This run does not update that image.

### Not tested

Real Gmail, the deck server and host executor relay, a model, tool schemas, the provisioning route, memory use and performance with a single VM, and a reproducible build of the candidate.

## Live attempt 4 (candidate source `6e2cbb7`): refused by the load guard

Date: 2026-11-14.
Attempt 3 stays as recorded above. It used the older candidate. It does not count for this one.

### Setup

The merge `git merge --ff-only 9445660` left no diff against that commit. The candidate was `bare-mcp` built from source `6e2cbb73ca749b5e567cfca87ed65fba08754307`. I checked its SHA-256 before use: `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`. It matched the required digest. The run used the frozen base `a90de2e085a7ad52` (mode `0444`, 1493565440 bytes), a new lock file, a new output directory and a free fixture port.

```sh
python3 scripts/vm-prototype/shared_profile_e2e.py --base <frozen base .qcow2 with sidecar> --out <new short private dir> \
  --lock <new task-owned lock file> --fixture-port <free loopback port> \
  --bare-candidate <private candidate path> --expect-bare-sha256 d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b
```

### Result

Exit 3: `host pressure: load average 23.71 is above 8.0. Nothing was started.` The load guard (8.0) and memory guard (6144 MiB) were unchanged. I made one attempt and did not retry in a loop.

No output directory, manifest, overlay, VM, fixture or browser was created. No gate ran. All 15 required gates are untested for this candidate. The candidate readiness, candidate install and one-session switching through browser restart and VM wake are untested.

### Cleanup

I removed the task-owned lock file and its directory. The shared lock, the other run and the frozen base were not touched. The base mode and size are unchanged. No owned process was left.

### Not tested

The live guest acceptance of this candidate, real Gmail, the deck server and relay, provisioning and performance. No process-level attribution of host load was performed.

## Live attempt 5 (candidate source `6e2cbb7`, harness `d98fcfe` plus one correction): accepted

Date: 2026-11-14.
Attempts 3 and 4 stay as recorded above. This run is the first real guest run of the source-tracked candidate. Attempt 3 used another binary and does not count for it.

### Setup

The merge `git merge --ff-only d98fcfe` left no diff against that commit. The candidate was `bare-mcp` built from source `6e2cbb73ca749b5e567cfca87ed65fba08754307`. Its SHA-256 matched before launch: `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b` (19528144 bytes). The owner allowed a separate lock again. Each run used a new lock file, a new output directory and a free loopback fixture port. The shared lock and other runs were not touched. The load guard (8.0) and the memory guard (6144 MiB) were unchanged. I made no load polling.
The offline suite for the harness ran after the correction: 51 tests, all passed. The parent's other offline gates were not repeated.

```sh
python3 scripts/vm-prototype/shared_profile_e2e.py --base <frozen base .qcow2 with sidecar> --out <new short private dir> \
  --lock <new task-owned lock file> --fixture-port <free loopback port> \
  --bare-candidate <private candidate path> --expect-bare-sha256 d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b
```

### Run 5a: harness race, exit 1

Host load at start was 0.84. The run booted the guest, then stopped with `expected one bare-mcp before the stop, found 0`. No scenario gate ran. The cause is in the harness. A run with a candidate skips the readiness probe at boot. The install started before the guest session had started its browser. The candidate was not at fault.
The manifest is kept unchanged in its private directory. It is not an acceptance.

### Correction (one, harness only)

A regression test came first: `test_waits_for_a_browser_that_starts_late_after_an_unprobed_boot`. It failed with the same error. `stop_mcp` now waits up to `MCP_WAIT` (60 s) for the browser to appear. It still refuses zero processes after the wait, two processes and a foreign executable. Only `shared_profile_e2e.py` and `test_shared_profile_e2e.py` changed. No gate, guard or production file changed.

### Run 5b: accepted, exit 0

Host load at start was 7.92 (limit 8.0) and 16.48 at the end. About 12.4 GiB of memory was available at start. Another load was on the host, so this run makes no performance or memory claim.

- Original guest `bare-mcp`: `bc8d33dc325b5b534e73042a81e605a3190827ce77ff33088e0f94e187da7c17`.
- Installed candidate, equal to the expected digest: `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`.
- Guest adapter `bare-call` equals the checkout copy: `19532bfe6a516758539ca0f6bc3268756f9f5b43d342c019ed907277e4cc4f1a`.
- Harness: `913374c5da4c918e22bea17168d9dabf15e504e9d68950601af38a2de7c02145`. Base id `a90de2e085a7ad52`.

All 15 required gates passed, and `acceptance_complete` passed: `base_frozen_and_verified`, `bare_candidate_installed_and_ready`, `guest_adapter_hash_equals_candidate`, `bare_mcp_sha256_equals_expected`, `both_accounts_signed_in_together`, `alternating_identity_before_save`, `one_bare_main_session_one_cookie_record`, `state_private_dir_and_file_0600`, `state_saved_with_cookie_record`, `mcp_restart_new_process`, `restore_from_disk_and_switch_both`, `vm_wake_same_boot_and_profile`, `no_duplicate_fixture_effects`, `base_unchanged`, `no_owned_process_left`.

- One `main` session held both synthetic identities. The service held one record, one login for A and one for B.
- After a new `bare-mcp` process (one before, one after, none shared), the state file restored both accounts and A, B, A switched.
- The VM save took 3.7 s and the wake 1.4 s (usable in 1.9 s). The boot id, the `bare-mcp` process and the memory marker were the same. Both accounts still switched.

### Cleanup

The harness stopped only its registered VM and fixture. No registered process survived and no QEMU process remained. The overlay was removed. I removed my lock files. The frozen base kept its mode `0444`, its size and its SHA-256 (`a90de2e0...`, digest checked before and after). The manifests, the pinned candidate copy and the candidate are kept outside Git.

### Not tested

Real Gmail, the deck server and host executor relay, a model, the provisioning route, memory use and performance with a single VM, a reproducible build of the candidate, and a guest image that ships this browser.
