# Setup UI plus VM run: accepted-results attempt

Date: 2026-11-14. Base: `f8df0ee`.

## Result

The run is not accepted.

- The manifest holds 46 gates. 44 pass. 2 fail.
- Failed gates: `ui_privacy_viewer` and `acceptance_complete`.
- `acceptance_complete` fails because `ui_privacy_viewer` fails.
- `/finish` returned `ok: false`. Its `pending_ui_gates` list was empty.
- `/stop` returned `ok: false` with no survivors.
- The guest file checks passed on a live socket. See "Guest file".

The failed gate records a product finding. The run does not show a defect in the guest file path.

## Test settings

- Deck ran with the test-only `--idle-secs 600` setting.
- The guest file check ran after the final restart, while QGA was present.
- The parent set the load limit for this run (`max-load 100`). This worker did not change it.
- Only one stack and one VM ran.

## Browser

- Candidate: a private `bare-mcp` build. It combines the fetch metadata fix and the textarea fix.
- Binary SHA-256: `b4c9d1615a95edaaf48ec6f1cd277b53468bf8ebb30975b695ba8cf40ce0d7f1`. The worker checked it before use.
- One candidate process ran behind a scratch stdio bridge. Each request was one JSON line.
- The installed `bare` tools were not used. No other browser was used.
- The candidate ran headless. No graphical screen was created.

## Real and scripted scope

Real:

- The Deck web UI, driven through the candidate browser.
- Propose, edit, save draft, review, approve.
- The protected form click on the real Deliver control.
- Opening the bot chat and the Screen viewer.
- The "I completed it" buttons for the phone and CAPTCHA waits.
- The controller route restarts.

Scripted:

- The fill script ran in the page through `eval`. It returned only booleans, a length and a hash prefix.
- The fixture steps for the phone and CAPTCHA owner actions.
- The model hold and release.
- The scripted model and the local synthetic service.

No direct production approve, secret or challenge POST was sent by the worker.

## Gates the worker recorded

| Gate | Status | Basis |
| --- | --- | --- |
| `ui_review_screen_seen` | pass | The review showed the job, the task and the label before approval. |
| `ui_secret_input_check` | pass | The fill returned `filled: true`. The length and the hash prefix matched the plan. No visible text leak. |
| `ui_privacy_viewer` | fail | See Finding 1. |
| `ui_screenshot_privacy` | pass | The masked field after fill shows dots only. The pending and done views show no password value. |

## Controller gates

Pass:

- `ui_approval_through_proxy`, `ui_secret_form_posted_204`, `ui_phone_answer_posted_204`, `ui_captcha_answer_posted_204`.
- `secret_absent_from_proxy_log`, `logs_accounted_for_each_start`, `base_frozen_and_verified`.
- `restart_preserves_waiting_ownership`, `restart_adds_no_work`, `restart_after_completion_no_duplicate`.
- `guest_file_protected`, `special_characters_preserved_in_guest_file`, `special_characters_authenticated_in_service`.
- The phone and CAPTCHA owner-round gates, and `first_task_done`, `task_final_report`.

Fail: `acceptance_complete`, which follows from `ui_privacy_viewer`.

## Restarts

Deck started three times in total:

1. The first start.
2. A restart during the unanswered phone wait. The wait kept its owner and added no work.
3. A restart after the task reached `done`. No duplicate work appeared.

## Guest file

- The QGA socket was present before the final restart.
- The QGA socket was present after the final restart. The check ran right after the restart.
- `guest_file_protected` passed.
- `special_characters_preserved_in_guest_file` passed.

This does not repeat the socket absence in the two-fixes run (its Finding 1). That absence did not recur in this run. Its cause stays open.

## Findings

1. Internal wake notes show in the visible chat. During the pending-verification views, and after the done state, the chat shows text such as "The owner marked [challenge <request id> completed]". The text names the phone request id and the CAPTCHA request id. The worker recorded `ui_privacy_viewer` as fail on this basis. Whether a request id counts as a check id that must stay hidden is a judgment for the parent.
2. The owner prompts show the match number and the CAPTCHA check id in plain text. The plan does not say whether these must be hidden. The worker did not treat them as a failure. The gate detail text says "by design". That wording was an assumption. It was not verified.
3. Transient `502` responses. During the phone "I completed it" action, the candidate reported `502` for `/api/setups`, `/api/setup-options` and the secret-requests route. The same routes returned `200` a few seconds later. Cause not known. To verify.
4. The transient `The run failed` alert appears during the phone wait. It reproduces the two-fixes finding 4. In the blank viewer screenshot, the alert overlaps the suggestion chips.
5. The `Deliver securely` control is not visible in the masked-field screenshot. Its colour resolves to transparent. The click still worked. This matches the `oklch` finding on `mrwick1/bare#464`.
6. `document.body.innerText` throws over a `select`. This matches `mrwick1/bare#482`. The worker used `textContent` for its counts.

## Native viewer frame

- One native frame was saved after login, while the viewer was live. It was 1280 by 800 PNG.
- The frame shows an empty nano buffer for a notes file in the guest. No secret is visible.
- This is one frame. It does not show every guest pixel or frame.

## Cleanup

- The worker closed the candidate browser session and stopped the bridge.
- The recorded QEMU process for this run was not running after `/stop`.
- `disk.qcow2` and `vm.state` were absent after `/stop`.
- The leak scan covered 40 files with 0 matches.
- The manifest lists no survivors.

## Not tested

- Acceptance. The run did not pass the acceptance gate.
- Layouts at desktop and phone sizes beyond the 1280 by 800 capture.
- A run without the post-completion restart.
- The root cause of the transient `502` responses (Finding 3).
- Whether the guest frame ever shows a secret at native scale. Only one frame was checked.
