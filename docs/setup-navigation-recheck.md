# Setup navigation and owner UI recheck

Date: 2026-11-13.
Base: `b48fcd9`. Tree was clean at start. No product code changed.
Scope: bare browser check of sidebar navigation, the protected task form at 390 px, long-text wrap, the review toggle and owner waits.
Labels: the fixture waits were seeded. The fake guest and scripted model ran locally. This is not service proof and not a VM pass.

## Verdict

Not an end-to-end pass. Checks 1 and 4 pass. Check 5 passes for the answer, cancel and restart paths. Check 2 passes for keyboard, delivery and leak checks. Check 3 is blocked by a bare engine gap. The 200% zoom, live viewer and visible primary button were not verified.

## Method and limits

- Browser: bare only, headless, two sessions (1280 px and 390 px wide, device pixel ratio 1). Preview tools reported unavailable. No hyprcage screen was created, because bare ran headless.
- Stack: the owned stack from the brief. Fixture metadata listed three seeded waits (PhoneApproval, Captcha, TaskSecret) with chat IDs 1 to 3.
- Driftcheck: the brief named parent PID 78719. That PID was absent. The running parent was a different process with the same script. The Deck PID changed after the one restart call, as expected.
- Synthetic secret values were generated inside page JavaScript. The page returned only the length and a SHA-256 prefix.
- Console errors were read at the end of the run. Snapshots were taken at each step.

## Results

| # | Check | Result | Evidence |
| --- | --- | --- | --- |
| 1 | PhoneApproval opened from overview, TaskSecret opened from sidebar | Pass | Snapshot shows TaskSecret prompt only. Its secret-requests call used its own bot ID. |
| 1 | TaskSecret to Captcha and back, by sidebar | Pass | Captcha shows its own panel. No TaskSecret form. |
| 1 | `/api/chats` maps each chat to its bot | Pass | Chats 1 to 3 match the fixture metadata. |
| 1 | Plain chat has no stale bot panel | Pass, limited | "New Thread" opens the overview. The chat area shows no bot panel. The overview still lists every bot's wait. This is the existing design. |
| 2 | 390 px form visible before use | Partial | Input shows dots after fill. The empty input border and "Deliver securely" button are invisible. Cause: bare#464. |
| 2 | Escape clears by keyboard | Pass | 28 to 0 characters. Same node. Focus kept. |
| 2 | Clear by keyboard | Pass | Tab, Tab, Enter. 28 to 0. No secret request. |
| 2 | Challenge action by keyboard | Pass | One 204 to the challenge endpoint. Status "Waiting for verification". Not verified. |
| 2 | Delivery by keyboard | Pass | One POST to the bot's secret endpoint, 204. Body keys: `request_id`, `value` only. Field detached. |
| 2 | Guest file permissions and content | Pass | Directory 0700. File 0600. One line, key `FIXTURE_PASS`. SHA-256 prefix matches the page value. |
| 2 | Storage and DOM leak scan | Pass | No pattern in local storage, session storage, cookies or DOM. |
| 2 | Process and evidence leak scan | Pass | One fixture file (the guest file, expected). Zero hits in Deck or parent argv and environ, in evidence or in the repo. |
| 2 | Viewer withheld after gate opens | Not run | No open gate in this fixture. |
| 3 | Long request ID wraps in thread and owner panel | Blocked | The fixture ID was not rendered. Synthetic probe: bare does not break unbroken text (see F2). |
| 3 | Page overflow at 390 px | Pass | Document width 390. No page overflow. Carousel scroll was not separated. |
| 4 | New draft opens with "Hide review", `aria-expanded` true | Pass | Draft created by Propose setup with the scripted model. Not approved. |
| 4 | One click hides, one click reopens | Pass | Click counter: one event per click. Labels and `aria-expanded` changed once each. |
| 4 | Reloaded draft opens with one click | Pass | Closed after reload. One click opened it. |
| 5 | PhoneApproval answer by keyboard | Pass | One 204. Answered state, not verified. Match number 17 is in the API. The UI text was not read before the answer. |
| 5 | Answered state after reload and restart | Pass | Same identity. Answered state kept. Pause was not re-read. |
| 5 | Captcha cancel by keyboard | Pass | One 204. Resolution `cancelled` with matching request ID and task key. One owner message in chat 2. Kept after restart. |
| 5 | Status of PhoneApproval match and instructions in UI | Not verified | Instructions shown in API. UI match text not read. |

Console: no error entries in either session at the end.

## Findings (not changed)

- F1. **bare#464** hides the primary button text and the protected input border at 390 px. Deck uses `oklch()` theme colors, so the button background computes as transparent. No Deck fallback was added, as the brief asked.
- F2. **bare#467** (filed): `overflow-wrap: anywhere` and `word-break: break-all` do not break an unbroken text run. Spaced text wraps. Check 3 cannot pass until this is fixed in bare.
- F3. The TaskSecret bot shows an alert: its executor socket in the fixture is missing. The status "Computer failed to start or save" stays visible. This is a fixture runtime state. It was not investigated.
- F4. `/api/bot` returns 404 on every load. This matches the earlier baseline finding F6.
- F5. A "Reconnecting…" banner appeared after stream read timeouts. Cause not verified.
- F6. Pressing Enter once too often hit the focused Clear button a second time. It changed nothing.

## Cleanup

- Bare sessions closed. Cookie list empty.
- No hyprcage screen was created, so none was destroyed.
- `POST /fixture/restart` returned 204. Bot and chat identities did not change.
- `POST /fixture/shutdown` returned 204. The listener refused connections 15 seconds later. The Deck process and the parent process were gone.
- The private fixture directory remains on disk. It holds synthetic files only, and the guest secret file is mode 0600. I did not delete it. The parent script owns that path.

## Not run

200% zoom (bare has no zoom control). Live viewer gate. Visible primary button at 390 px (bare#464). Long-ID wrap proof (bare#467). First task. Real VM. Real service sign-in.

## Evidence (outside Git)

Scratchpad folder `recheck/`: `01-taskSecret-after-sidebar-1280.png`, `02-phone-taskSecret-form-390.png`, `03-phone-taskSecret-filled-390.png`. Issue draft: `issues/bare-overflow-wrap.md`.
