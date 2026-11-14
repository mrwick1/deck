# Setup UI plus VM run: two-fixes results

Date: 2026-11-14. Base: `1f92e26`.

## Result

The run is BLOCKED. It is not accepted.

- All four UI gates pass.
- `POST /finish` returned `pending_ui_gates: []`.
- `POST /finish` still returned `ok: false`.
- The manifest holds 46 gates: 43 pass and 3 fail.
- Failed gates: `guest_file_protected`, `special_characters_preserved_in_guest_file`, and `acceptance_complete`.
- `POST /stop` returned `ok: false` with no survivors.

The setup ran the full flow. It reached `done` and the task report arrived.
The guest file check did not run against a live socket. See Finding 1.

## Browser source

- Candidate: `bare-mcp` built from private source `9cf10333`. That source combines base `e6` with the fetch metadata fix (PR 479) and the textarea fix (PR 500).
- Binary SHA-256: `b4c9d1615a95edaaf48ec6f1cd277b53468bf8ebb30975b695ba8cf40ce0d7f1`. The worker checked it before use.
- The candidate ran headless. No hyprcage screen was created.
- One candidate process ran behind a scratch stdio bridge. The bridge used one JSON object per line.
- The installed `mcp__bare__*` tools were not used. No other browser was used.
- The T3 preview status reported no preview tab (`available: false`).

## Gates

| Gate | Status | Basis |
| --- | --- | --- |
| `ui_review_screen_seen` | pass | The review screen showed the job, first task and label before approval. |
| `ui_secret_input_check` | pass | The fill returned `filled: true`. Length 29 matched the plan. The hash prefix matched the plan. `visible_text_contains_value` was false. |
| `ui_privacy_viewer` | pass | While private, the latest-frame route answered 204 with 0 bytes. The blank viewer was inspected. The page had no password field. |
| `ui_screenshot_privacy` | pass | The masked field after fill showed dots only. The phone and CAPTCHA pending views showed no secret. The post-done guest frame was too small to read (Limit 3). |

Controller gates that passed include `ui_approval_through_proxy`, `ui_secret_form_posted_204`, `ui_phone_answer_posted_204`, `ui_captcha_answer_posted_204`, `secret_absent_from_proxy_log`, `secret_absent_from_host_evidence`, both restart gates, and `restart_after_completion_no_duplicate`.

Controller gates that failed:

- `guest_file_protected`: `FileNotFoundError`. The check could not open the QGA socket.
- `special_characters_preserved_in_guest_file`: depends on the check above.
- `acceptance_complete`: lists the two failures above.

## Real and scripted

- Real UI: propose, edit the task and label, save draft, review, approve, Deliver securely, open bot chat, Screen viewer, "I completed it" for phone and CAPTCHA, restart clicks through the controller route.
- Real protected form: the Deliver click reached `phone_wait` (controller stage) with `runtime_ready: true`.
- Scripted: the fill script ran in the page through `eval`. It fetched the one-use value from the test-only UI route and set the real password node. It returned booleans, a length and a hash prefix only.
- Controller-side: owner fixture steps, model hold and release, the two post-completion and mid-wait restarts.
- No direct production challenge or respond POST was sent.

## Findings

1. Guest file check failed at `/finish`. The QGA socket was absent. Timeline:
   - The second Deck restart (after completion) started at 07:18:51.
   - The run directory changed at 07:19:32. The socket disappeared then.
   - `/finish` ran at 07:19:51.
   - Hypothesis: the post-completion restart removed or stopped the QGA socket. To verify: check the socket before and after each restart on a fresh run.
2. `oklch()` background colours compute as transparent in this candidate. The primary "Deliver securely" button is invisible. A click still works. Filed as evidence on `mrwick1/bare#464`.
3. `document.body.innerText` throws over a `select`, not only over a `textarea`. Added as evidence on `mrwick1/bare#482`.
4. A transient alert `The run failed` appeared in the bot chat during the phone wait. It was gone at completion. Not investigated. To verify.
5. `GET /api/bot` returned 404 on page load. The earlier run showed the same 404. Not a new failure.
6. Async `eval` results resolved in this candidate. The fill script returned its promise result. This is the case that #481 reports for earlier builds.

## Limits

1. The controller's post-completion checks are the only source for the two guest-file gates. The UI gates do not replace them.
2. The DOM text scan after delivery cannot re-test the value. The value is one-use. The check at fill time (`visible_text_contains_value: false`) is the only in-page value test.
3. The post-done guest frame is 1280x800 at the source. It showed a dark terminal-style screen with a small status bar. At the display size the text was not legible. The native-scale review is for the parent.
4. The candidate is headless. Its `oklch` gap hides primary buttons. The candidate `innerText` gap blocks text scans on pages with `select`. Both affect what the worker sees.
5. The candidate screenshot is not a real browser render.
6. The branch in the brief was `finish-bot-setup-e2e`. The worktree branch is `t3/finish-setup-ui-evidence-gates`. The commit `1f92e26` matched the brief.

## Not tested

- Guest file contents after the final restart.
- A run without the post-completion restart.
- Desktop 1280 and phone 390 layouts beyond the single 1280 by 800 capture.
- Live guest pixels at native scale.
