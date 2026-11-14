# Shared profile real-guest acceptance plan

Date: 2026-11-14.
Status: the harness is written and unit tested. It has not run in a guest. This document claims no live result.

## Purpose

One browser session holds two synthetic accounts. The check proves this in a real guest, through the installed guest entrypoint. It covers switching, a browser restart that restores from disk, and a VM save and wake. See D68 and [shared-profile-vm-results.md](shared-profile-vm-results.md) for the blocked first attempt.

## Files

- `scripts/vm-prototype/shared_profile_e2e.py`: the harness.
- `scripts/vm-prototype/test_shared_profile_e2e.py`: fault-injection tests. They start no VM and no browser.

The harness reuses `setup_e2e` (`take_lock`, `pressure`, `socket_path_ok`, `Manifest`, `Stack` registry and cleanup, `base_identity`, `verify_base`) and `vmproto` (`create_overlay`, `start_qemu`, `install_executor`, `ready`, `save_vm`, `restore_vm`). It reuses the `Service` fixture of `test_multi_account.py` (one cookie record, several accounts, one selected identity). It adds a request counter and a fixed port. It changes no existing file.

## Fidelity

The harness runs `/usr/local/bin/bare-call` in the guest as the desktop user, through `as-deck` and the guest agent. The host sends the same `tools/call` request that the executor sends. The real `prepare()` in the guest fixes the state path. The guest bare-mcp is the real one.

Not covered: the deck server, the host executor relay, the model, tool schemas and the provisioning route. `Stack` could start the server, but a scripted model would have to call the browser tools. That is a larger harness. It is not built, so it is not claimed.

The identity service is synthetic. It is not Google. It does not show how Gmail behaves.

## Live command

Placeholders: `BASE` is a frozen base `.qcow2` with its `.json` sidecar. `OUT` is a new directory. `PORT` is a free loopback port. `SHA` is the expected `bare-mcp` digest.

```sh
python3 scripts/vm-prototype/shared_profile_e2e.py --base BASE --out OUT \
  [--fixture-port PORT] [--expect-bare-sha256 SHA] [--timeout 900] [--keep-disk]
```

Exit codes: 0 accepted, 1 not accepted, 2 bad setup (existing `OUT`, long path), 3 host pressure, 4 lock held. Codes 2 to 4 start nothing. Run only when the shared lock is free and the load is under 8.0. Run one VM at a time.

## Order of work

1. Take the lock. Check memory and load. Refuse an existing `OUT`. Only then create anything.
2. Check that the base is frozen (`0444`) and its digest verifies.
3. Start the fixture. Create a private overlay. Boot. Install the candidate `bare-call` into the overlay only. Wait for the readiness probe.
4. Run the gates below. The first failed gate stops the flow.
5. In `finally`: stop only registered processes, verify the base again and write `manifest.json`.

## Gates

Every gate is required. There is no optional gate. A gate that did not run is `absent` and fails `acceptance_complete`.

| Gate | Passes when |
| --- | --- |
| `base_frozen_and_verified` | the base is mode `0444` and its digest matches its record |
| `guest_adapter_hash_equals_candidate` | the SHA-256 of the guest `bare-call` equals that of `guest/bare-call` in the checkout |
| `bare_mcp_sha256_equals_expected` | only with `--expect-bare-sha256`: the guest `bare-mcp` digest equals it |
| `both_accounts_signed_in_together` | A signs in alone, then B signs in, and B's page lists both accounts |
| `alternating_identity_before_save` | switches A, B, A, B each show the intended identity |
| `one_bare_main_session_one_cookie_record` | `sessions` shows `main` once with one fixture address, and the service holds one record |
| `state_private_dir_and_file_0600` | `deck` and `browser` directories are `0700` and the file is one regular `0600` file of the desktop user |
| `state_saved_with_cookie_record` | the fixture cookie token is in that file |
| `mcp_restart_new_process` | after the requested restart, exactly one `bare-mcp` runs and it shares no pid with the old one |
| `restore_from_disk_and_switch_both` | `open state=` restores both accounts from the file, and switches A, B, A work in one session |
| `vm_wake_same_boot_and_profile` | after save and wake: no VM process during the save, same boot id, later uptime, same `bare-mcp` pid, memory marker kept, state file unchanged, both accounts still switch |
| `no_duplicate_fixture_effects` | the service saw exactly one login for A, one for B and one record |
| `base_unchanged` | the base digest still matches |
| `no_owned_process_left` | no registered process identity is still running |

The browser restart and the VM wake are separate gates. The restart kills the owned `bare-mcp` and its direct children. The restore reads only the state file. The wake keeps process memory. A cold boot fails on the boot id, the uptime and the `bare-mcp` pid.

## Evidence recorded

`manifest.json` holds the git head, the base identity, the candidate, guest adapter and `bare-mcp` digests, the harness digest, the fidelity text, host load at start and end, save and wake timings, and cleanup survivors. The raw directory stays outside Git.

## Tested offline

The tests drive the real `run` flow against a fake boundary. The fake guest talks HTTP to the real fixture with real cookies. Each fault below gives a nonzero exit and a failed `acceptance_complete`:

- occupied lock and high load (zero launches), an existing `OUT` (kept), adapter hash mismatch and expected `bare-mcp` mismatch;
- two sessions, a missing account, a wrong switched identity, a missing state file and a permissive one;
- an unchanged MCP process after a requested restart, a cold boot instead of a wake, a duplicate fixture effect, a changed base and a cleanup survivor;
- a crash midway: later gates stay not green and cleanup still runs.

Other tests run the real lock, the real `Stack` cleanup on owned and unowned child processes, the fixture's counting and the entrypoint command line.

## Remaining live requirements

These are untested until a live run:

- the guest agent calls: `as-deck env ... bare-call`, `stat`, `pgrep`, `sha256sum` and `grep` output formats;
- the `sessions` output. The one-session check counts the fixture address, like `test_multi_account.py`;
- the restart. `bare-session` runs `exec bare-mcp` and does not respawn. The harness restarts it with `as-deck setsid -f bare-session`. Its environment may differ from the sway launch. This is a hypothesis to verify;
- the kill of children. Only direct children of the old `bare-mcp` are killed;
- fixture reachability from the guest at the forwarded address, before and after the wake;
- `save_vm` and `restore_vm` timings, and that a wake keeps the browser alive;
- the full server and relay route, real Gmail and any per-site account switcher.
