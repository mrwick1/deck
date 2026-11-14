# Synthetic login live rerun

Date: 2026-11-13.
Source head: `5007996`. Binary: debug build from `b48fcd9`. SHA-256: `3efcc735766a330ed2f52c269404ec876e656f98d3b15d360468f03ac0548268`.
`git diff b48fcd9..HEAD -- src web Cargo.toml Cargo.lock` was empty. The binary matches the source at `HEAD`. It does not carry `HEAD` metadata. The manifest records the same hash.
Frozen base: `a90de2e085a7ad52`, mode 0444. SHA-256 `a90de2e0…37d0`, the same before and after.
Fidelity: real server, one real private VM, scripted model, local login service. The harness creates and answers through the HTTP API. This is not a run of the web UI. Earlier UI checks used a fake guest. This run does not replace them. No claim of an unmocked UI plus VM run.
No Gmail, real account, real credential or paid model. Default host-pressure limits (max load 8). Start load 0.50, 23502 MiB available. No override.

## Command

```sh
python3 scripts/vm-prototype/setup_e2e.py synthetic-login \
  --base <frozen-base.qcow2> --deck <deck-binary> --out <short-output-dir>
```

One live run. Duration 70 s. Exit code 1.

## Verdict

Every behavior gate passed, including the owner rounds the previous run never reached. The run is not a complete pass: `acceptance_complete` failed on one gate, `no_owned_process_left`.

60 gates recorded: 55 pass, 3 `not_tested` (optional), 2 fail.

## Passed

- Secret delivery with refusals, guest file mode 0600 with exact value, special characters authenticated by the service.
- Setup `Ready` after the protocol probe. Phone wait raised with the service's own id.
- Restart during the wait: the task, request, step, match number, executor and counters did not change. `restart_adds_no_work` passed.
- Phone round and CAPTCHA-style round: stale (409), wrong request (409), `value` field (422), wait kept, wrong match number or check id refused by the service, owner action completed in the service, answer 204, answer alone left verification pending, `verified` only after `owner_help_status`, page refresh, `browser_read` and `report_help_verification`, duplicate answer 204 with one wake.
- The earlier `NameError` did not recur. The service page was refreshed before it was read.
- Service counters: 1 login ok, 0 failed, 1 submit attempt, 1 submit success.
- 19 scripted tool calls, each once. Task done. Final report in chat.
- Restart after completion: no counter or task change. 3 server logs for 3 starts.
- `secret_never_in_argv` (321 samples). `secret_absent_from_host_evidence` (29 files, 0 matches). Model turns hold no value.
- Base unchanged. Lock free after the run.

## Failed

1. `no_owned_process_left`: "1 left".
2. `acceptance_complete`: failed only because of gate 1.

### Cause of failure 1

The check matches any process whose command line holds the output path, except the harness and its ancestors. The output path was `/data/claude-tmp/sl/r2`. During the run I polled with a separate shell whose command line named that path. That shell is not an ancestor of the harness. Hypothesis: it is the one match. I cannot prove the pid after the fact. After the run, no process matched the path. No QEMU, server, model, service or fixture process remained. The long-lived pid 3174633 is not mine and was not touched.

This is a harness limitation: it treats any command line that names the path as a leftover. A later run should poll with a command that does not name the output directory, or the check should match owned pids.

## Harness wart

`*_resume_reached_the_model` passed, but its recorded detail reads "the wake did not reach the model before the timeout". `need()` stores the failure text as the detail on pass as well. The wording is misleading. The gate status is the truth, and the following verification gates prove the wake arrived.

## Not tested

- Optional gates `restart_from_saved_state`, `second_task_after_wake`, `raster_secrecy`: `not_tested`. They are not whole-product completion.
- Restart during a held reply or between dispatch and response.
- The guest disk and sockets are outside the leak scan.
- Phone-visible UI failures stay with bare issues 464 and 467.
- No performance claim.

## Cleanup

Server, VM, model, service and fixture processes ended. The overlay was deleted. The base hash is unchanged. The lock is free. Evidence stays outside Git, in a private output directory: manifest, three server logs, model request and response logs, fixture log, data directory.
