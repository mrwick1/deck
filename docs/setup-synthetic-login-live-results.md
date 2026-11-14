# Synthetic login live results

Date: 2026-11-13.
Source head: `7ae4a72`. Binary: debug build from `b48fcd9`. SHA-256: `3efcc735766a330ed2f52c269404ec876e656f98d3b15d360468f03ac0548268`.
`git diff b48fcd9..HEAD -- src web Cargo.toml Cargo.lock` was empty. The binary matches the source at `HEAD`. It does not carry `HEAD` metadata.
Frozen base: `a90de2e085a7ad52`, mode 0444. Full SHA-256 `a90de2e0…37d0`, the same before and after.
Fidelity: real server, one real private VM, scripted model, local login service. The harness creates and answers through the HTTP API. This is not a run of the web UI. Earlier UI checks used a fake guest. This run does not replace them.
No Gmail, real account, real credential or paid model. Default host-pressure limits (load 8, 6144 MiB free). Start load 0.95. No override.

## Command

```sh
python3 scripts/vm-prototype/setup_e2e.py synthetic-login \
  --base <frozen-base.qcow2> --deck <deck-binary> --out <short-output-dir>
```

Default ports were free. One live run. Exit code 1.

## Verdict

Negative result. The run stopped at the first restart check. The cause is a harness typo. It is not a product failure in the evidence.

Gate count: 26 recorded. 22 pass. 4 fail. `acceptance_complete` fails. Most owner-round gates are absent because the flow stopped.

## Passed

- Delivery of the special password: refusals (409, 409, 400, 400), 204, wait cleared only after delivery, replay refused (409).
- Guest file: mode 0600, owner is the bot user, `NAME=value` match is a boolean `true`.
- Setup `Ready` through the API. Bot folder created by the server.
- Bare typed the exact `${FIXTURE_PASS}` value with symbols. The service counted 1 login ok, 0 failed (`special_characters_authenticated_in_service`).
- Phone wait raised (`phone_wait_raised`). The wait showed the service's own id. The task was waiting.
- `restart_preserves_waiting_ownership`: no change in waiting task, request, step, match number or executor.
- `secret_never_in_argv` (123 samples). `secret_absent_from_host_evidence` (17 files, 0 matches) at the time of the check.

## Failed

1. `run_completed`: `NameError: name 'counters_changed' is not defined`.
2. `logs_accounted_before_and_after_restart`: 2 server logs for 2 starts. The gate needs three starts. The run ended before the third. This is a result of failure 1.
3. `no_owned_process_left`: "1 left". The harness matches any process whose command line holds the output path. The only match was my own launcher shell. No QEMU, server, model, service or fixture process remained. This is a harness false positive for this launch style.
4. `acceptance_complete`: absent gates, listed in the manifest.

### Cause of failure 1

`setup_e2e.py` defines `counts_changed` (line 616). Lines 841 and 865 call `counters_changed`. The first call is after the restart during the wait. It fails every run of this mode at that point. The fix is a rename in the harness. This task owns no harness edit. The parent should fix it and add a regression that runs the mode with fakes through these lines.

The unit tests did not catch it. They start no VM and cover no code after the restart check.

## Not run because of the stop

Refused answers during the wait, owner action in the service, held reply and answered-not-verified check, `owner_help_status` / `browser_read` / `report_help_verification`, CAPTCHA-style round, `signin_verified`, the single report submission, restart after completion, and the final leak scan. Their state is unknown. Service counters for submit attempts and successes were not read.

## Model ledger

The scripted model made 4 calls: `browser_open`, `browser_step`, `browser_read`, `request_owner_help`. The request log lists the available tool schemas, which include the five tools the script uses and others. No credential text was read.

## Optional gates

`restart_from_saved_state`, `second_task_after_wake` and `raster_secrecy` did not run. They are `not_tested`. No separate restore flow was run.

## Not tested

Everything under "Not run". Phone-visible UI failures stay with bare issues 464 and 467. This run does not address them. No performance claim.

## Cleanup

Server, VM, model, service and fixture processes were stopped by the harness. The overlay was deleted. The base hash is unchanged. The lock is free. Evidence stays outside Git: manifest, server logs, model logs, fixture log, data directory.
