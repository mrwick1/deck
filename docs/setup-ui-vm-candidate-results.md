# Setup UI plus VM run: candidate bare results

Date: 2026-11-13. Base: `195ceea`, branch `finish-bot-setup-e2e`.

## Result

The run reached the end of the flow. Every server-side gate passed. The run is not accepted. Two required worker gates are missing: `ui_privacy_viewer` and `ui_secret_input_check`. The controller set `acceptance_complete` to fail for that reason.

## Source of the browser

- Candidate: `bare-mcp` built from the `bare` branch `fetch-metadata-475`, commit `11fe92b7` (draft PR 479).
- The installed `bare` tools were not used.
- The installed MCP server was not changed.
- The candidate ran headless. No hyprcage screen was created.
- A small stdio bridge kept one candidate process alive. It forwarded only allowlisted tools. It removed file, secret and storage arguments. It wrote no argument values to any log.

## What ran

1. The UI showed the bot setup form. The job text and the first task were typed in the real form.
2. The label `FIXTURE_PASS` was typed in the credential field. The draft was saved.
3. The review screen showed the job, task and label before approval. Gate `ui_review_screen_seen` passed.
4. The real approve button was clicked. The setup moved to provisioning, then to `credential_wait`.
5. The protected fill script ran in the page. It returned `filled: true`, a length of 29, and a 12-character hash prefix. No value was printed or stored.
6. The real "Deliver securely" button was clicked. The setup moved to `phone_wait`.
7. One Deck restart ran during the unanswered phone wait. The wait kept its owner. Restart gates passed.
8. The phone owner step completed through the local service. The model hold was set. The real "I completed it" button was clicked.
9. The UI showed "Waiting for verification". The state reported the answer held and recorded. A screenshot showed no secret. The model was released.
10. The CAPTCHA owner step, model hold, UI answer, pending screenshot and release followed the same order.
11. The run reached `done`. One Deck restart ran after completion. The duplicate check passed.
12. The controller finish check ran. It returned `ok: true` for 41 server-side gates.
13. The controller stop ran. It reported no survivors.

## Gate counts

The manifest holds 44 gates.

- Pass: 43.
- Fail: 1 (`acceptance_complete`).
- Absent: `ui_privacy_viewer`, `ui_secret_input_check`.

Proxy-derived UI gates passed: `ui_approval_through_proxy`, `ui_secret_form_posted_204`, `ui_phone_answer_posted_204`, `ui_captcha_answer_posted_204`.

Worker gates:

| Gate | Result | Basis |
| --- | --- | --- |
| `ui_review_screen_seen` | pass | Visual read of the review screen before approval |
| `ui_screenshot_privacy` | pass | Visual inspection: masked field after fill, no secret in pending views |
| `ui_secret_input_check` | not recorded | The expected length is not visible to the worker. The fill result has length 29 and `filled: true`. |
| `ui_privacy_viewer` | not recorded | No DOM or viewer text search was run. The pending views were checked by eye only. |

## Real versus scripted

- Real: the UI form, the approve, deliver, answer and release clicks. The Deck restarts. The candidate browser. The protected fill ran in the page and the value stayed in the page.
- Controller-driven: the local service owner steps, the model hold and release. These use the control API, as the plan allows.
- No API bypass of an approval, delivery or owner response was used.

## Privacy checks

- Screenshots were taken of the filled form and of both pending views. Each file was inspected by eye. The field showed masked dots. The pending views held no secret, match number or check id.
- The screenshots were deleted after inspection. They lived in RAM only. None is in the repository.
- No snapshot was taken of the protected form. A snapshot prints field values, so the form was read only with a button-label query.
- Not checked: the managed viewer state while private (204 with capture stopped). Not checked: the pixel content of live guest frames.

## Cleanup

- The controller stop returned `ok: false` with an empty survivors list. The only failing gate was `acceptance_complete`.
- The control and UI listeners refuse connections now.
- The candidate session was closed. The candidate process exited with code 0. The bridge exited.
- The RAM directory with screenshots and temporary responses was deleted.
- No QEMU, Deck or controller process from this run remained at the check.
- Not checked: the VM disk file. The controller reports deletion; this report did not inspect the file system.

## Findings

1. Controller model flag. `release_wake` sets the release event but does not clear the held flag. Only `hold_wake` clears it. After a release, the state keeps `model.held: true` until the next hold. A second release call would then pass its `held` check with no new held wake. The fix belongs in the test harness (`setup_synthetic_login.py`, `release_wake`). Not changed here: this run is limited to the new report.
2. Missing worker gates. The run does not record `ui_secret_input_check` or `ui_privacy_viewer`. The controller needs the expected fixture length and a DOM text check. Neither was available to the worker. A later run needs the expected length passed to the worker, and a counts-only DOM check.
3. Load note. The candidate `open` report showed `request 404` for the bot list path on each UI load. The UI still worked. This is not a `bare` failure, so no `bare` issue was filed. To verify: whether the 404 comes from a Deck route that is not yet in place.

## Not tested

- Desktop 1280 and phone 390 layouts. Only the 1280 by 800 desktop viewport was used.
- The bare 464 (colors) and 467 (wrap) issues. No visual layout fallback was used.
- Managed viewer blank 204 during private capture.
- Live guest pixel content after delivery.
- Gates `ui_secret_input_check` and `ui_privacy_viewer`.
- No separate run of the other owner and restart checks in a second session.
