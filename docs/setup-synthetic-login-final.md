# Synthetic login final live run

Date: 2026-11-13.
Source head: `7118ba2`. Binary: debug build from `b48fcd9`. SHA-256: `3efcc735766a330ed2f52c269404ec876e656f98d3b15d360468f03ac0548268`.
`git diff b48fcd9..HEAD -- src web Cargo.toml Cargo.lock` was empty. The binary matches the source at `HEAD`. It does not carry `HEAD` metadata. The manifest records the same hash.
Frozen base: `a90de2e085a7ad52`, mode 0444. SHA-256 `a90de2e0…37d0`, the same before and after.
Fidelity: real server, one real private VM, scripted model, local login service. The harness creates and answers through the HTTP API. This is not a run of the web UI. Earlier UI checks used a fake guest. This run does not replace them. No claim of an unmocked UI plus VM run.
No Gmail, real account, real credential or paid model. Default host-pressure limits (max load 8). Start load 6.89, 16994 MiB available. No override.

## Command

```sh
python3 scripts/vm-prototype/setup_e2e.py synthetic-login \
  --base <frozen-base.qcow2> --deck <deck-binary> --out <short-output-dir>
```

One live run on a fresh output directory. Duration 74 s. Exit code 0. I polled only the command output. No other process named the output directory.

## Verdict

All required gates passed, including `no_owned_process_left` and `acceptance_complete`.
60 gates recorded: 57 pass, 3 `not_tested` (optional), 0 fail.

## Results

- Secret with special characters: guest file equals `NAME=value`. The service authenticated the exact value that bare typed.
- Restart during the wait: ownership and work unchanged (`changed: []`) for both gates.
- Phone round and CAPTCHA-style round: the answer alone left verification pending. `verified` came only after `owner_help_status`, a page refresh, `browser_read` and `report_help_verification`.
- Service counters: 1 login ok, 0 failed, 1 submit attempt, 1 submit success.
- 19 scripted tool calls, each once.
- Restart after completion: no change. 3 server logs for 3 starts.
- `secret_never_in_argv`: 331 samples. `secret_absent_from_host_evidence`: 29 files, 0 matches.
- Base unchanged. Lock free after the run.

## Cleanup evidence

The manifest lists four registered subprocess identities, each with a birth time, and the bound VM identities. `survivors` is empty. The gate detail reads "0 registered identities still running". I checked afterwards that none of the registered pids exists. No process names the output directory. The earlier false positive does not recur.

## Not tested

- Optional gates `restart_from_saved_state`, `second_task_after_wake`, `raster_secrecy`: `not_tested`. This is not whole-product validation.
- Restart during a held reply or between dispatch and response.
- The guest disk and sockets are outside the leak scan.
- Phone-visible UI failures stay with bare issues 464 and 467 (colors, wrap). They are open. This run does not show them fixed.
- No performance claim.

## Cleanup

Server, VM, model, service and fixture processes ended. The overlay is gone. The lock is free. Evidence stays outside Git in a private directory. The earlier failed run manifests are unchanged.
