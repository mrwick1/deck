# Setup owner UI recheck results

Date: 2026-11-13.
Base: `9fe5b80`. Code and bundle: `c9153e2`. This commit adds the report only.
Scope: browser recheck of owner waits and the protected task form. Local synthetic stack only.

## Verdict

**Not an end-to-end pass.** Owner phone-approval and CAPTCHA responses, protected task delivery and restart checks passed. Phone-width visual review found two failures. A bare gap explains one of them. The fixtures were seeded, and they cannot prove a real challenge.

## Limits

- The fixtures were seeded in SQLite by the parent. No real model or service created these waits.
- The guest was a fake guest. The model was scripted. No live VM ran.
- No real CAPTCHA or service sign-in was tested.
- The screenshot did not capture a real guest screen or raster secrecy.

## Stack and tools

- Browser: bare only, headless. Two sessions: 1280 desktop and 390x844 phone.
- Bare reported `devicePixelRatio` 1 at phone width. This does not match a 3x phone.
- One hyprcage screen with mirror was created and destroyed. Bare headless cannot attach to it.
- Preview tools reported unavailable. No other browser ran.

## Results

| Check | Width | Result | Evidence |
| --- | --- | --- | --- |
| PhoneApproval panel shows synthetic instructions and match 17 | 1280 | Pass | Overview text |
| "I completed it" sends one 204 to the challenge endpoint | 1280 | Pass | Network log |
| Answered state: `answered: true`, UI "Waiting for verification", not signed in | 1280 | Pass | API and snapshot |
| Task stays waiting under pause, one owner marker, no new task | 1280 | Pass | Task list, chat 1 |
| Reload and restart keep the answered state | 1280 | Pass | Reload, then POST `/fixture/restart` |
| Enter on "Open bot chat" opens the chat | 390 | Pass | Keyboard press and h1 |
| Captcha panel shows synthetic instructions | 1280 | Pass | Snapshot |
| Captcha cancel sends one 204 | 1280 | Pass | Network log |
| Captcha resolved as `cancelled` with matching task key | 1280 | Pass | Setup `resolved` field |
| Captcha: one owner message, pause kept, no retry | 1280 | Pass | Chat 2 |
| TaskSecret form lists `FIXTURE_PASS` as a password field, autocomplete off | 1280 | Pass | Snapshot and secret-requests |
| Escape clears the field (length 36 to 0), same node | 1280 | Pass | Page check |
| Clear button empties the field | 1280 | Pass | Page check |
| Delivery sends one POST to the task endpoint with keys `request_id`, `value` | 1280 | Pass | In-page fetch log, key names only |
| Delivery returns 204, field detached and empty | 1280 | Pass | Node check |
| Guest file: directory 0700, file 0600, one line, hash matches | 1280 | Pass | Hash prefix `b29a8aa10d575548` |
| Delivery: no secret request left, task waiting, one owner marker, pause kept | 1280 | Pass | API and chat 3 |
| Restart: no second delivery, one receipt row | 1280 | Pass | API and guest receipts |
| Leak scan of fixture files and Deck and parent argv and environ, before and after restart | 1280 | Pass, 0 hits | Counts only |
| Keyboard activation of challenge and Clear controls | 390 | Not run | Controls consumed |
| Protected form at 390 | 390 | Not run | Request already delivered |
| Browser storage holds no value | — | Not run | Not checked |
| Live viewer withheld while the gate is open | — | Not run | No open gate after delivery |
| 200% zoom | 390 | Unavailable | Bare has no zoom control |
| Phone layout, no page-level horizontal overflow | 390 | Pass | DOM metric |
| Phone thread region fits its box | 390 | **Fail** | Finding F4 |
| Primary button labels visible | 390 | **Fail** | Bare issue #464 |
| Review: one click on a fresh draft | 1280 | Diagnosed | Finding F5 |

## Review two-click diagnosis (finding F5)

- The earlier claim was "the first click only focuses". It does not hold for this build.
- A fresh draft opens its review by itself. `web/src/components/BotSetups.tsx:70` sets `editing` after create.
- "Review setup" toggles `editing` (line 78). The first click closes the open review. The second click opens it.
- A reloaded draft opens on the first click. One bare click gave one DOM click event on the button.
- One bare click on a neutral search box gave one event of each type. Bare did not double-dispatch.
- No Deck or bare patch was made.

## Bare issue filed

- **mrwick1/bare#464**, label `bare-mcp`: `oklch()` and `color-mix()` compute as transparent. Tailwind v4 uses `oklch()` for theme colors. Primary buttons therefore show white text on no background.
- Related open issue #430 covers CSSOM at-rules. This report does not repeat it.
- No other bare failure was proven. The stream read timeouts after delivery are unexplained. They are not filed.

## Findings (not changed)

- **F1. Sidebar route uses the wrong bot.** Opening a bot chat from the sidebar does not set `selectedBot` (`web/src/App.tsx:154`). The page then polled the secret requests of another bot. The TaskSecret form did not appear on that route.
- **F2. Overview order changes between loads.** The same three bots appeared in different order after a reload.
- **F3. Status conflict.** The overview shows "Ready". The chat shows "Computer failed to start or save", and the bot status has `computer: failed` with a last error. This matches baseline finding 3.
- **F4. Long request ID overflows at 390.** The thread content is 506 px in a 375 px box. The follow-up suggestion row is 737 px in a 328 px box.
- **F5. Auto-open review.** See the diagnosis above.
- **F6. Legacy poll.** `/api/bot` returns 404 on each load. This matches baseline finding 7.
- **F7. Reconnecting banner.** The status "Reconnecting…" appeared after delivery. The cause is not verified.
- **F8. Receipt mode.** The receipt file is mode 644 inside a 0700 directory. It holds identifiers, a salt and a digest, not the value. This is an observation only.
- **F9. Task stays waiting.** After a CAPTCHA cancel, the task remains `waiting` under pause, with no retry. This matches the pause hold.

## Test-method note

- An early counter listener was attached twice. It reported two events for one click. A clean counter showed one. The earlier value was my error.
- The first overview click opened the Captcha chat, not PhoneApproval. The overview order had changed (F2). PhoneApproval was opened later by its own button.

## Cleanup

- Bare sessions `desk` and `phone` closed.
- Hyprcage screen destroyed. Its mirror was on workspace 7.
- POST `/fixture/shutdown` returned 204. The listener closed. The Deck process and the parent process exited. A check 15 seconds later found no fixture-root process.
- No user service or other process was touched.
- Evidence (screenshots, scan script, hash output) is in the session scratchpad under `recheck/`, outside Git.
- The private fixture root and its values stay out of this report.
