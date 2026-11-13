# Synthetic login acceptance

Date: 2026-11-13.
Base: `8d25d6a`.
Code: `scripts/vm-prototype/setup_e2e.py` (mode `synthetic-login`) and `scripts/vm-prototype/setup_synthetic_login.py`.
Tests: `test_setup_e2e.py` and `test_setup_synthetic_login.py`. They start no VM and no browser.

## Purpose

The owner chose a local synthetic login first. The earlier `protected` mode delivered a secret and then marked phone and CAPTCHA gates pending, because nothing raised them. This mode raises them with a real sign-in through the guest's bare.

## Flow

The harness runs a local login service and a scripted model in its own process. Both stay on loopback. The guest reaches the service through the existing fixture port. No external endpoint is used.

1. Create and approve a setup with the credential label `FIXTURE_PASS` and a supplied task.
2. Deliver a random synthetic password through `POST /api/setups/{id}/secret`. The harness holds it in memory only.
3. The task opens the local login with `browser_open`. It types the username and the reference `${FIXTURE_PASS}` with `browser_step`. The service compares the value that bare typed.
4. The service shows a match number. The model calls `request_owner_help` with kind `phone` and that number. The task parks.
5. The harness restarts Deck once. The waiting task, request, step, match number, task state and executor identity must be the same.
6. The harness first sends refused answers (stale version, wrong request, a `value` field). It then does the owner action in the service. The service refuses a wrong match number. The action uses the match number that Deck stored.
7. The harness holds the model's next reply. It posts the answer to `/api/setups/{id}/challenge/respond`. While the reply is held, Deck must show the request as answered and not verified.
8. The harness releases the reply. The model calls `owner_help_status`, then `browser_read`, then `report_help_verification`. It reports `verified` only if the page shows the step completed.
9. The same sequence runs for a CAPTCHA-style check. The owner action is a local call with the check id. It is not CAPTCHA solving.
10. The model calls `signin_verified` and submits the report once. The service counts logins, submit attempts and submit successes.
11. The harness restarts Deck again. No counter may change.
12. The harness scans every host sink for the password.

## Model script

The model sends only tools that exist in `src/tools.rs`: `browser_open`, `browser_step`, `browser_read`, `request_owner_help`, `owner_help_status`, `report_help_verification`, `signin_verified`. Step lines use the grammar in the bare `step` tool. A unit test checks each line. No screenshot line is used. The model never knows the password.

A tool reply that reports a failure stops the script with a text turn. A missing match number or check id also stops it.

## Gates

`acceptance_complete` is the closing gate. It passes only when every required gate passed. A missing, pending or unlisted not-tested gate fails it. The optional gates are `restart_from_saved_state`, `second_task_after_wake` and `raster_secrecy`. They report `not_tested` with a reason.

Each owner round (`phone_*` and `captcha_*`) has these gates:

| Gate suffix | Meaning |
| --- | --- |
| `wait_raised` | The wait belongs to this task. It is new, unanswered and shows the service's own id. |
| `refuses_stale`, `refuses_wrong_request`, `refuses_value_field` | Bad answers get a 4xx. |
| `failed_answers_keep_the_wait` | The wait is unchanged after those answers. |
| `service_refuses_wrong_owner_id` | The service rejects a wrong match number or check id. |
| `owner_action_completed_in_service` | The service shows the step completed. |
| `answer_accepted`, `resume_reached_the_model` | The answer returns 204. The wake reaches the model. |
| `answer_alone_leaves_verification_pending` | The service shows completed. Deck shows answered, not verified. |
| `verified_only_after_service_evidence` | Deck records `verified` after the exact status, read and report calls. |
| `duplicate_answer_adds_nothing` | A repeat answer returns 2xx. The model gets one wake for the request. |

Other gates: `special_characters_preserved_in_guest_file`, `special_characters_authenticated_in_service`, `restart_preserves_waiting_ownership`, `restart_adds_no_work`, `single_success_action`, `scripted_calls_exactly_once`, `restart_after_completion_no_duplicate`, `logs_accounted_before_and_after_restart`, `secret_never_in_argv`, `secret_absent_from_host_evidence`.

A failed gate that the flow depends on raises `Stop`. The harness keeps all evidence and runs the scans and cleanup. An unknown setup outcome (`Investigate` or `Exhausted`) also stops the run.

## Contract changes in `setup_e2e.py`

- `challenges()` no longer reads `setup.phase`. The phase is on the outer record. Success now means a `verified` entry in `setup.resolved`. A vanished wait is no longer success.
- A gate detail or exception text that holds the value in any encoded form is replaced. The gate fails.
- The value forms now include hex (both cases) and form encoding. The manifest writer refuses to write the value.
- Server logs are named by start count. Two starts in one second no longer collide.
- `--diagnostic-precreate-bot-dir` is refused in this mode. Exit code 2.
- `--credential-profile plain` keeps the special-character gates `not_tested`. That run cannot be a complete pass.
- The lock, the host pressure defaults, the fresh overlay, the disk cleanup and the process check are unchanged. The load thresholds are unchanged.

## Leak scan boundary

Scanned: the data directory with its database and WAL, the private directory (server logs, model request and response logs, service log, config), run metadata, sampled process argv and the model turns in memory. Forms: plain, JSON, URL, form, base64, URL-safe base64 and hex. The report holds counts and relative paths, never the value.

Not scanned: the guest disk, the guest saved state and sockets. The guest credential file lives on that disk by design. The guest check sends the value on QGA stdin only.

Base64 of a value at a shifted alignment inside a longer string is not searched.

## Live command

```sh
python3 scripts/vm-prototype/setup_e2e.py synthetic-login \
  --base <frozen-base.qcow2> --deck <path-to-deck-binary> --out <short-output-dir>
```

Run it alone. It takes `/tmp/deck-setup-e2e-vm.lock`. It stops with exit 3 when the host is under pressure. The output directory must stay short, because QEMU sockets have a length limit.

Expected result: every required gate passes, `acceptance_complete` passes, `open_gates` lists only the three optional gates, exit 0. A negative result is a valid result. Keep the manifest and the private directory of a failed run.

## Not proven by the unit tests

These need the live run:

- That bare substitutes `${FIXTURE_PASS}` with spaces and symbols exactly. The gate `special_characters_authenticated_in_service` decides it. A failure there is a bare or secrets-file finding. File a `bare` issue. Do not add a fallback.
- That the guest secrets file keeps the value byte for byte (`guest_file_protected`).
- The shape of real `browser_step` and `browser_read` replies and how a failed step is reported. The script treats `{"ok": false}`, an `error` key and a leading `error` or `failed` as failure. A missed failure ends at the next page read, with a stop text.
- That Deck calls the model with the wake as the last `user` message. The script depends on the text `owner_help_status for request <id>,`.
- That the setup phase is `Ready` while a task login wait is open (`setup_ready_through_api`).
- That Deck's model call survives a held reply for the harness check. The hold limit is 60 s.
- Raster secrecy. No screenshot is taken. The gate stays `not_tested`.
- Restart during the held reply, or between dispatch and response. Only restarts during the open wait and after completion run.
- Restart from saved state and a second task.

## Decision for the parent

This mode follows the owner's choice of a local synthetic login first. The decisions log is outside this task's ownership. The parent should record the choice in `docs/decisions.md`.
