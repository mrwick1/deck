# Setup UI baseline

Date: 2026-11-13.
Base: `6d5ca42`. This report adds no code. It records a browser check of the current bot setup screen.
Scope: one throwaway stack with a scripted local model and a fake VM harness. No real VM ran. No protected value was entered.

## Verdict

The owned slice is complete for this baseline. The review, approval, progress and control paths work in the real page at desktop and phone widths. This is not an end-to-end pass. Protected input, owner challenge responses, a live VM and a first task dispatch remain untested.

## Stack

- Deck built from `6d5ca42` with the locked web dependencies and a fresh frontend build.
- A fake harness stood in for the VM harness. It answers status, overlay, start, kill and executor install. It creates no guest and no executor socket.
- A scripted model returns one fixed reply. No paid model call ran.
- A local proxy added the owner header that a trusted proxy adds.
- Config, fixtures, the database and screenshots stayed outside Git.

## Results

| Check | Result |
| --- | --- |
| Overview loads with an existing draft | Pass |
| Review shows name, instructions, model, authority, resources and budget | Pass. Budget shows 20.00 allowance and 100.00 ceiling in TST, matching config. |
| Edit name and save draft | Pass. Version and draft revision advance. Reload keeps the edit. |
| Stale save from a second session | Pass. The stale page shows "Setup changed; reload it". The server keeps the other session's edit. |
| Stale approval through the API | Pass. Returns 409. The setup stays a draft. |
| Approve after reload | Pass. Setup reaches Ready. The bot runtime registers. |
| Ready bot chat opens | Pass. Shows the completion message. |
| Pause and resume automation on a Ready bot | Pass. The server flag toggles. The button label updates after refresh. |
| Chat message to a Ready bot | Accepted. The bot enters "waking" and stops there, because the fake harness made no executor socket. Model call not exercised. |
| Second draft with a credential label | Pass. Three system steps finish. The setup enters a credential wait with zero attempts. |
| Credential wait status in UI | Pass. Shows "Waiting for protected input" and the warning not to send credentials in chat. |
| Pause and resume a waiting setup | Pass for the flag. Pause sets `automation_paused` to 1. Resume clears it. The UI shows no paused state. |
| Ordinary completion of a credential wait | Pass as fail-closed. Returns 503. |
| Protected secret route | Absent. Returns 404. Expected at this baseline. |
| Challenge response route | Absent. Returns 404. Expected at this baseline. |
| Restart with a Ready bot | Pass. The bot stays Ready and registered. |
| Restart during a credential wait | Pass. Same request identity, same version, zero attempts per step. |
| Phone review at 390 by 844 | Pass. Authority, resources and budget fields render. Budget guard shows its toast. |
| Client budget guard (allowance above ceiling) | Pass. Save refused with a toast. Server unchanged. |
| Console errors | None on either session. |
| Repo gates on the unchanged head | `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test` (229 unit, 4 integration, 2 live ignored), `npm run check`, `npm test` (27), `npm run build` all exit 0. |

## Findings for the parent

1. **Server accepts an invalid budget on save.** A draft with an allowance above the ceiling returns 204 and persists. Approval rejects the same draft with 409. The rule exists only in the client for saves. Owner: deck core (`src/bot/setup_api.rs` edit handler). Not fixed here.
2. **Name edits do not reach the chat title.** After a rename, the Ready bot header and the sidebar keep the title from the original job text. Owner: setup API or web chat title.
3. **Ready does not mean the computer works.** The setup reaches Ready after the harness steps. The bot card then shows "Computer failed to start or save" and an executor alert. This happened because the fixture made no executor socket. It shows that Ready does not check executor reachability. Owner: deck core and VM harness.
4. **Executor alert shows a full host path.** The alert names the runtime socket path on the host. Owner: deck core error text. The owner UI shows it.
5. **Credential label spaces are removed.** Entered as "Fixture login", stored and shown as "Fixturelogin". Owner: setup API. Intent not verified.
6. **Setup pause has no UI state.** Pause sets a hold, but the setup JSON and the card do not show it. Owner: setup UI.
7. **Legacy bot route polls with 404.** The page calls `/api/bot` on every load when no legacy bot exists. It logs one 404 per call. Owner: web client.
8. **First Review click after reload did not open the form at phone width.** The second click did. Hypothesis: the list re-renders after load. To verify.
9. **Job placeholder looks clipped at phone width.** The textarea uses `pre-wrap`. The capture shows one clipped line. To verify with a real device or a longer capture.

None of these blocks the baseline. Items 1, 3 and 5 affect the protected-input work and need an owner decision.

## Bare

Every check used bare in headless mode, one session per viewport. No bare call failed. No bare issue was filed.

## Not tested

- A real VM, a real executor socket and a guest-side first task.
- Protected input, both its endpoint and its guest transfer.
- Phone-approval and CAPTCHA waits, and their owner responses.
- Model calls from a bot, including the ceiling request.
- A restart during first-task dispatch.
- The inbox and task list views.
- Keyboard-only and screen-reader behavior.
- Hyprcage screens. Bare headless covered the page.

## Reproduction

The stack files are not committed. They used the synthetic config, a fake harness and a model stub. Rebuild them from the repo's `deck.example.toml`, with a `[provisioning]` section. The harness path must point to a local fixture that answers the protocol in `src/bot/setup.rs`.

## Parent integration follow-up

At `e3ad16e`, the parent moved credential-label normalization to save time. The editor no longer removes spaces or newlines while the owner types. Type checking and the production build pass. A repeat browser check remains open.

The parent also reconciled challenge fields with the backend. A regression showed that an answered request appeared expired. The corrected UI reads `instructions`, `match_number` and `answered`. All 44 web tests pass. This is deterministic evidence, not a new browser run. Other baseline findings remain open unless later evidence closes them.

The invalid-budget save regression failed before the storage fix and passed after it. The combined rerun passes 249 Rust unit tests and four integration tests. Two live tests remain ignored. Draft saves now validate a supplied budget before committing. A repeat browser run remains open.
