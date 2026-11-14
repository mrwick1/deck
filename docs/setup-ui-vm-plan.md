# Setup UI plus VM run: controller and plan

Date: 2026-11-13. Base: `7c2be62`.

## Goal

One unmocked run: Deck web UI, review and approve, a real private VM, the protected form, synthetic phone and CAPTCHA-style owner answers, and the first task.
Earlier runs did not do this. The `synthetic-login` run created and answered through the HTTP API. The UI checks used a fake guest.
This plan adds a controller. The controller does not run the flow. A `bare` worker drives the real UI.

## Status

The controller is `scripts/vm-prototype/setup_ui_e2e.py`. Its tests are `test_setup_ui_e2e.py`. The tests start no VM, no Deck binary and no browser.
No live run has happened. This document is a plan, not a result.

## What the controller starts

`serve` starts these in one process tree:

- The real Deck binary, on a free loopback port. It has the provisioning config of `setup_e2e.py` (`Stack.write_config`).
- The scripted model and the local synthetic service. They run in the controller process. The password stays in memory.
- A streaming proxy on a free loopback port. This is the UI origin. It sets `Remote-User` for Deck and drops any value from the client.
- A control API on another free loopback port. Each call needs the `X-Control-Token` header.

No VM exists at start. Deck starts the VM only after the UI approves the setup.
For an interactive evidence run, pass `--idle-secs 600`. This changes only the test configuration. Run the final guest-file check promptly after the post-completion restart, while QGA is available. The separate saved-wake harness covers the short-idle lifecycle. Keep the guest-file assertion unchanged; an unavailable guest is a failed check, not proof that its credential file is missing.

`Stack` from `setup_e2e.py` owns every process. Its `cleanup` stops Deck, runs `vmproto.py kill` for each VM directory and checks the registered identities.

## Start command

Check first. This starts nothing:

```sh
python3 scripts/vm-prototype/setup_ui_e2e.py prepare --base <frozen-base.qcow2> --deck <deck-binary> --out <short-output-dir>
```

Start:

```sh
python3 scripts/vm-prototype/setup_ui_e2e.py serve --base <frozen-base.qcow2> --deck <deck-binary> --out <short-output-dir>
```

The interactive run must omit `--wait`. That option stops at the first completed task and prevents the required final restart and late evidence submission. Keep serving until the worker calls `/finish` and `/stop`. The parent owns this call. Run it in the background and read the first stdout line. It is one JSON object: `ui_url`, `control_url`, `control_token`, `out`.
Use a fresh `--out`. Keep it short: QEMU sockets need a path under 108 bytes.
The controller takes the same VM lock as `setup_e2e.py` and applies the same host-pressure limits (`--min-avail-mib`, `--max-load`). It never overrides them.
Do not start another VM-based run while it runs.

Without `--wait`, the controller runs until `POST /stop`. With `--wait`, it finishes and stops by itself when the first task is done.
Every run ends at `--max-secs` (default 3600). It also ends when Deck dies or a setup reaches `Investigate` or `Exhausted`.
It never retries an unknown outcome. It stops, cleans up and writes the manifest.

## Control contract

All routes are JSON. Header: `X-Control-Token: <control_token>`. The `Host` header must name the control port.

| Route | Purpose |
| --- | --- |
| `GET /plan` | The job text, the task text, the credential label, expected length and hash prefix, the task id pattern, the closing text, the UI URL, the page script template and the worker's gate names. |
| `GET /state` | `stage`, `setup_id`, `task_id`, `phase`, the open `wait` (kind, `request_id`, answered), counts, model hold state, service step names, gate statuses. No password, no match number, no check id. |
| `POST /owner/phone` and `/owner/captcha` | Body `{"request_id": "<id>"}`. Completes the local service's step. It touches only the fixture. It needs the current unanswered wait of that kind. |
| `POST /model/hold` | Hold the model's next reply to an owner-answer wake. It needs an open phone or CAPTCHA wait. |
| `POST /model/release` | Release the held reply. It needs a held wake. It records two gates first. |
| `POST /deck/restart` | Stop and start only the owned Deck. It records ownership and work gates. |
| `POST /gate` | The worker reports its own UI checks. Body `{name, status, detail?}`. The name must be one of the four UI gates. Detail is at most 300 characters. |
| `POST /finish` | Run the server-side checks. Idempotent. |
| `POST /stop` | Finish if needed, clean up, write the manifest, close the listeners. Returns survivors. |

There is no route that approves a setup, delivers a secret or answers an owner wait. The controller's Deck client refuses any method but GET. A test checks both.

`stage` values: `no_setup`, `ambiguous_setups`, `draft`, `credential_wait`, `provisioning`, `phone_wait`, `phone_answered`, `captcha_wait`, `captcha_answered`, `running`, `done`, `task_failed`, `unknown_outcome`.

## Worker discovery

The worker reads `GET /plan` for the job, the task text and the label `FIXTURE_PASS`. These are not secret.
The worker types them in the real UI: it creates the setup, edits the draft and reviews it.
It finds the setup id, the task id (`setup-<setup_id>`) and each wait's `request_id` from `GET /state`.
The setup id and the request ids are random. The worker never needs the password.

## Protected form

1. Poll `GET /state` until `stage` is `credential_wait`. Read `wait.request_id`.
2. In the page, run the script from `fill_js(request_id)` (template in `GET /plan`) with the bare eval tool.
3. The script fetches `/__e2e/secret` on the UI origin. It sets the real password input of the Deck form. It returns `filled`, `length`, a 12-character hash prefix and `visible_text_contains_value`. Compare length and hash with `/plan`. Require the text-leak flag to be false. Return no value.
4. The worker clicks the form's real submit button. The form posts the existing `POST /api/setups/{id}/secret` through the proxy. The controller sees the 204 in its proxy log.

The route `/__e2e/secret` is test-only. It lives in the controller, not in Deck. Its rules:

- It needs the header `Sec-Fetch-Site: same-origin`. A browser sets it. Page code cannot forge it.
- It needs the pending credential `request_id` and the expected label. A stale id gets 409. A wrong label gets 409.
- It releases the value once for each request. A second call gets 410. A refused call does not use the value.
- It never logs the query. The proxy log holds only method, path and status.
- It is one fixed route. It reads no file and writes no file.

If the one release is lost (for example the page reloads between the fetch and the click), the run cannot retry. The worker reports BLOCKED. Do not add a re-arm route.

## Owner phases

For each of phone and CAPTCHA, the worker does this:

1. Poll `GET /state` until `stage` is `phone_wait` (or `captcha_wait`).
2. `POST /owner/<kind>` with the `request_id`. The fixture step completes. Deck does not know yet.
3. `POST /model/hold`. Now the model holds its next reply to a wake.
4. Click the real Deck answer button in the UI. Deck posts `challenge/respond`.
5. Poll `GET /state` until `model.held` is true and `stage` is `<kind>_answered`. The answer alone is not verification. Inspect the pending state in the UI and take a screenshot.
6. `POST /model/release`. The model then reads the status, reloads the service page and reports verification.
7. Poll `GET /state` until the next stage.

Between phone and CAPTCHA, nothing else is needed: the guest's bare moves the service page on by itself.

## Checks the controller asserts

From Deck rows, the service and the model, at `POST /finish`. Names match `setup_e2e.py` where one exists.

- Protected delivery: `wait_cleared_only_after_delivery`, `guest_file_protected`, `special_characters_preserved_in_guest_file` (QGA read of the real guest file), `special_characters_authenticated_in_service`, `service_login_succeeded_with_reference`.
- Owner rounds, for `phone` and `captcha`: `_wait_raised`, `_service_refuses_wrong_owner_id`, `_owner_action_completed_in_service`, `_resume_reached_the_model`, `_answer_alone_leaves_verification_pending`, `_single_wake`, `_verified_only_after_service_evidence`.
- Task: `setup_ready_through_api`, `bot_data_dir_created_by_server`, `first_task_done`, `task_final_report`, `single_success_action`, `scripted_calls_exactly_once`, `model_uses_the_reference_not_the_value`, `model_turns_hold_no_value`.
- Restart (optional, by `POST /deck/restart`): `restart_preserves_waiting_ownership`, `restart_adds_no_work` while a wait is open, and `restart_after_completion_no_duplicate` after the task. All three are required for acceptance. Restart at least twice: once during a wait, once after completion.
- Privacy: `secret_never_in_argv`, `secret_absent_from_host_evidence`, `secret_absent_from_proxy_log`, `logs_accounted_for_each_start`.
- End: `base_frozen_and_verified`, `base_unchanged`, `no_owned_process_left`, `acceptance_complete`.

UI proof comes from the proxy log, not from the worker: `ui_approval_through_proxy`, `ui_secret_form_posted_204`, `ui_phone_answer_posted_204`, `ui_captcha_answer_posted_204`.
The controller counts 204 responses to the real UI's POSTs. The two challenge gates use order: the first answer is the phone, the second the CAPTCHA.

Acceptance requires 45 gates. 33 reuse a name from the earlier run. 12 are new: `phone_single_wake`, `captcha_single_wake`, `secret_absent_from_proxy_log`, `logs_accounted_for_each_start`, the 4 proxy-derived UI gates and the 4 worker gates. The earlier run required 55.
A required gate that is absent fails `acceptance_complete`.
Not repeated here: the API refusal gates (`refuses_stale_version`, `refuses_wrong_request`, `refuses_newline_value`, `refuses_label_in_body`, `ordinary_completion_refuses_credentials`, `failed_attempts_keep_the_wait`, `replay_of_a_cleared_request_refused`, the `*_refuses_*` challenge gates). A browser cannot send a stale id. The earlier API run proved them.

## Checks the worker reports

The worker calls `POST /gate` with `pass` or `fail`. All four are required for acceptance. `/finish` lists missing or failed UI gates in `pending_ui_gates` and returns `ok: false` until they pass.

- `ui_review_screen_seen`: the review screen showed the job, task and label before approval.
- `ui_secret_input_check`: the fill script returned `filled: true`, a length equal to the expected length and a hash prefix. Nothing else.
- `ui_privacy_viewer`: the rendered page text, live viewer and pending-verification views expose no credential value. Require HTTP 204 with zero frame bytes while private. Owner-facing match numbers and request identifiers are metadata, not passwords. Record raw internal wake prose as a separate usability finding. The protected input itself holds the value transiently; this check does not promise raw DOM or shell redaction. Use `bare` read and find. Record counts, not values.
- `ui_screenshot_privacy`: a `bare` screenshot of the form after fill, and of the pending view, show a masked field. Inspect the images. A byte search of a PNG proves nothing.

A gate detail that holds the password becomes a failure and is redacted.

Do not read `localStorage`, `sessionStorage`, cookies or the network log for the value. Return only counts and booleans from page scripts.

## Parent steps

1. Check the host: no other VM run, load and memory within limits.
2. Run `prepare`. Read the plan.
3. Start `serve` in the background. Read the first line.
4. Start one new `bare` delegate. Brief: the UI URL, the control URL and token, the sections above. The delegate uses `bare` only. No other browser.
5. If `bare` fails, file a `bare` issue as `docs/feedback-loop.md` says. Do not work around it in Deck.
6. When the stage is `done`, the worker calls `POST /finish`, then `POST /stop` (or lets `--wait` do it).
7. Read `manifest.json` in `--out`. It holds statuses and counts, never a value. Raw logs stay in `--out`, outside Git.

## Cleanup

- `POST /stop`, SIGTERM or the end of `--wait` runs `Stack.cleanup`.
- It stops Deck, kills each VM directory through `vmproto.py kill` (identity-checked), stops the service and the model, and checks the registered process identities and their descendants.
- It deletes `disk.qcow2` and `vm.state` unless `--keep-disk`.
- It closes the proxy and the control API. Open streams end.
- A survivor fails `no_owned_process_left`. The manifest lists it.
- Kill nothing that this run did not start.

## Limits

- The one-use value cannot be retried. A lost release ends the run as BLOCKED.
- `--wait` and the loss watch cover: Deck lost, an unknown or exhausted setup phase, the deadline. A VM that dies shows as `Investigate` in Deck. The controller does not watch the VM process itself.
- The two challenge-answer UI gates infer phone from captcha by order.
- No gate proves the visual layout. The worker's screenshots do, by inspection.
- Gates run with a fake backend only in unit tests. The unit tests inject the model ledger for the finish check.

## STOP and report

Stop and report, do not invent a fix, if:

- Deck needs a test-only production endpoint to continue. Deck must stay unchanged for this run.
- The real form cannot accept the page-set value (React state instead of the DOM node). Report it as a product finding.
- `bare` cannot run the eval, click or screenshot calls. File a `bare` issue.
- Host pressure forbids a VM. Do not override it.

## Needs a real UI plus VM run

Everything the controller reads from a live Deck, QGA and a real guest: the live stage names, the guest file check, the `Remote-User` trust through the proxy, the real form's reaction to the fill script, SSE through the proxy against real Deck, the privacy and screenshot checks, process cleanup with a real VM.
