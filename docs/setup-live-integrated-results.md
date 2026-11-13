# Live integrated setup results

Date: 2026-11-13.
Source head: `b48fcd9`. Release binary SHA-256: `550b41c268f849ab2fc8a656ab6d8e44ee7ca8d52c00fd049771c35fb8bae40c`.
Frozen base: `a90de2e085a7ad52`, mode 0444. Its hash was the same before and after both runs.
Harness: `scripts/vm-prototype/setup_e2e.py` at this head, unchanged. Default host-pressure limits (load 8, 6144 MiB free). No override.
Fidelity: real server, one real private VM, scripted model, local fixture. No Gmail, real account, real credential or paid model.
Raw manifests stay outside Git. Each run used a fresh private overlay and deleted it after the evidence was saved.

## Verdict

The two live blockers are fixed in a real VM. Baseline is not fully green because of one harness measurement defect. Protected mode is partial because of one harness crash. Neither is a product failure in the evidence. No performance claim follows.

## Baseline

Run order: web build, `cargo build --release` (2 jobs, private target dir), baseline. One VM. Load at start 4.1.

| Gate | Result |
| --- | --- |
| Setup Ready through the API | pass, 16 s |
| Bot folder created by the server (no `--diagnostic-precreate-bot-dir`) | pass |
| Executor bound; protocol-ready probe before `InstallExecutorDone` | pass (no readiness failure seen) |
| First task state `done` | pass |
| First task effects: fixture text in the page tool result, one file sent | pass |
| Fixture visited (harness counter) | **fail: 0 visits, harness defect** |
| Base and executor identity | pass |
| Artifact files on the host | pass, 3 |
| Restart after dispatch: same task, model, message counts | pass (1 task, 4 model calls, 11 messages, before and after) |
| Idle save, no extra model calls, no VM process while saved | pass |
| Secret and challenge routes refuse with no pending request | pass (409) |
| Base unchanged, no owned process left | pass |

### The failed gate

`fixture.py` prints its access log to stdout. `setup_e2e.py` sends that stdout to `/dev/null` and counts visits in the stderr log. The log was empty. The counter cannot see any visit.

Independent evidence from the retained scripted-model log: the page text "Deck fixture" appears once, in the `browser_open` tool result, and never in assistant output. The model made exactly one `browser_open`, one `shell` and one `send_file` call. This supports one real visit. It is not the harness gate, so the gate stays recorded as fail. Fix: log to stderr, or capture stdout.

Do not read task state `done` as objective proof. The effects above come from the tool result and file message.

### Readiness from runuser or HOME

Not reproduced. The probe passed on the first setup. No stable error code was produced.

## Protected mode

Delivery over HTTP passed. The run then raised `AttributeError: 'bytes' object has no attribute 'encode'` and stopped.

| Gate | Result |
| --- | --- |
| Credential wait label comes from the request | pass |
| Refuses stale version, wrong request, newline value, label in body | pass (409, 409, 400, 400), value not echoed |
| Ordinary completion refuses a credential wait | pass (status 503; it is a refusal, not 4xx) |
| Failed attempts keep the wait | pass |
| Delivery returns 204, value not echoed | pass |
| Wait cleared only after delivery | pass |
| Guest file 0600 and NAME=value check | **not run** |
| Replay of a cleared request refused | not run |
| Setup Ready after delivery | not run |
| Challenge owner response | not run (stays pending) |
| Argv scan, host leak scan before and after restart | not run |
| Raster secrecy | not_tested (PNG search proves nothing) |

### Cause

`Guest.check_secret_file` passes `value.encode()` to `Qga.exec`. `Qga.exec` calls `.encode()` itself and expects a string (`vmproto.py` line 442). The harness defect is one call site. The product delivery path (host to guest by stdin) was not at fault in this run.

The guest receipt that the host checks before it clears the wait is the only evidence for guest delivery here. The harness never read the guest file.

## Not run

- Restore or wake second task: no saved private artifact path was supported, so not run.
- Challenge flow with a service login: another task owns it.
- Protected mode retry: it would fail at the same call. No rerun, no patch to the frozen harness.

## Cleanup

No QEMU, server, model or fixture process remains. The lock is free. Both overlays were deleted. The base hash is unchanged. No other VM or disk was touched.

## Not tested

Raster secrecy, real QGA argv exposure for the guest-file check, host leak scans, restart during a wait, restore, and any performance.
