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
