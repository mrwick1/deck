# Setup UI plus VM run: final results (test sub-agent)

Date: 2026-11-13. Base: `39ef80a`. Branch: `t3/finish-ui-evidence-gates`.

## Result

The run is BLOCKED. It is not accepted.

The UI approve step passed. The setup reached `credential_wait`. The protected fill entered 29 characters into the real password field. The real "Deliver securely" button never submitted the value. The setup stayed at `credential_wait`. No phone or CAPTCHA step ran.

The controller finish check returned `ok: false`. Three worker gates are in `pending_ui_gates`. The controller stop reported no survivors.

## Browser source

- Candidate: `bare-mcp` built from the `bare` branch `fetch-metadata-475`, commit `11fe92b7` (draft PR 479).
- The installed `bare` tools were not used.
- The candidate ran headless. No hyprcage screen was created.
- A scratch stdio bridge kept one candidate process alive. It logged no argument values. It was stopped and removed at the end.
- No preview tab existed. No other browser was used.

## What ran

1. The real UI form received the job text. The proposal produced a draft.
2. The first task and the label `FIXTURE_PASS` were typed in the real form. Save draft succeeded.
3. The review screen showed the job, first task and label. Gate `ui_review_screen_seen` passed.
4. The real approve button was clicked. The setup moved to provisioning, then to `credential_wait`. Gate `ui_approval_through_proxy` passed.
5. The fill script from `setup_ui_e2e.fill_js` ran in the page. The page fetch released the value once. The field held 29 characters.
6. The fill script never returned its result. Its async steps did not settle (see findings 1 and 2). The result `filled: true` was not observed.
7. A masked-field screenshot was taken and inspected. It showed dots only, with no visible text.
8. The real "Deliver securely" button was clicked twice. Each click returned `ok`. The field kept its 29 characters. The setup stayed at `credential_wait`. The controller did not count a 204 for the form post (`ui_secret_form_posted_204` failed).
9. The controller was not restarted. No owner step, model hold or model release ran. No restart gate ran.
10. `POST /finish` returned `ok: false`. `POST /stop` returned `ok: false` with no survivors.

## Worker gates

| Gate | Status | Reason |
| --- | --- | --- |
| `ui_review_screen_seen` | pass | The review showed the job, the first task and the label before approve. |
| `ui_secret_input_check` | fail | The fill return was not observed. Length 29 matched the plan. The visible-text check was false. The hash prefix was not compared. |
| `ui_privacy_viewer` | fail | Not reached. The run did not leave `credential_wait`. The form's visible-text check was false. The viewer route returned 404 through the UI origin, not 204. |
| `ui_screenshot_privacy` | fail | The masked field after fill was inspected. It showed dots only. The pending views were not reached. |

Each fail carries a reason in the controller record. No gate was left absent.

## Controller result

- Finish: `ok: false`. `pending_ui_gates`: `ui_secret_input_check`, `ui_privacy_viewer`, `ui_screenshot_privacy`.
- Manifest: 33 gates. 10 pass. 23 fail. `open_gates` is empty.
- Gates that passed (10): `base_frozen_and_verified`, `base_unchanged`, `logs_accounted_for_each_start`, `model_turns_hold_no_value`, `no_owned_process_left`, `secret_absent_from_host_evidence`, `secret_absent_from_proxy_log`, `secret_never_in_argv`, `ui_approval_through_proxy`, `ui_review_screen_seen`.
- Gates that failed for the run, not for the worker: `ui_secret_form_posted_204`, `wait_cleared_only_after_delivery`, `guest_file_protected`, and the owner and task gates.
- Stop: `ok: false`, `survivors: []`. The control and UI listeners refuse connections.
- Serve exit code: not observed. The parent started `serve`.

## Findings

1. Async page JS does not settle in the candidate `eval`. A promise from `setTimeout`, `crypto.subtle` or `fetch` stays pending after 10000 ms. A timer set in `eval` never fires. Filed as `mrwick1/bare#481`. Issue `#387` (await a Promise result) is closed. This build may predate that fix. To verify.
2. `document.body.innerText` throws on a page that holds a `textarea`. The fill script reads that property. Filed as `mrwick1/bare#482`.
3. The filled "Deliver securely" button does not paint in the candidate screenshot. The click returns `ok`. The form does not submit. Added to `mrwick1/bare#464` as new evidence. Hypothesis: the same colour bug. To verify.
4. The viewer route `/api/bot/screen/latest` exists in `src/bot/api.rs`. Through the UI origin it returns 404, not 204. The bot list path `/api/bot` also returns 404 on load. This matches the earlier run. To verify: whether managed setups expose a screen route in this build.
5. The Deck form reads the password node directly (`web/src/components/SetupWait.tsx`). The source does not show a React-state fault. No product defect is established.

## Real versus scripted

- Real: the UI form (propose, edit, save, review, approve), the page fill of the value into the real field, the Deliver clicks, the screenshot.
- Controller-side: the fill release and the controller stage. No owner step ran.
- No API call approved, delivered or answered anything.

## Not tested

- The Deliver submission and its 204 count.
- Phone and CAPTCHA owner rounds, model holds and releases.
- Both required restarts (during a wait and after completion).
- The viewer's 204 and zero-byte check while private.
- The pending-verification views and their screenshots.
- The hash comparison of the value.
- Live guest pixels.
- Desktop 1280 and phone 390 layouts beyond the single 1280 by 800 screenshot.

## Cleanup

- Candidate session closed. Candidate and bridge processes stopped. The scratch socket directory was removed.
- Controller stopped. Listeners refuse connections.
- No hyprcage screen was created, so none was destroyed.
- Owned processes at the check: none. Other sessions' `bare-mcp` and deck processes were not touched.
- The masked-field screenshot is kept outside Git for parent review. Its path is in the run's hand-off, not in this report.
