# Setup UI integrated results

Date: 2026-11-13.
Base: `c9153e2`. The worktree was clean at the start. This report adds no code.
Scope: browser acceptance of the bot setup UI on a local stack. A scripted model and a fake guest answered the runtime. The stack used synthetic values only. No real account, paid model or live VM took part.

## Verdict

**Not an end-to-end pass.** The setup UI passed the steps below at desktop and phone widths. Protected input reached the owner's guest file in the fake guest. The owner-challenge UI, the active-task secret path, the first task and live runtime completion did not run. Section 4 lists them.

## 1. Stack and tools

- A real Deck binary built from `c9153e2`. A local proxy added the owner header. A scripted model and a fake guest stood in for the runtime.
- Browser: `bare` only. Bare ran headless, one session per width. No other browser ran. Preview tools were not used.
- Screen: one hyprcage screen with its mirror. Bare headless cannot target that screen (section 4).
- The synthetic fixture root, its database and its logs stayed outside Git.

## 2. Results

| Check | Width | Result | Evidence |
| --- | --- | --- | --- |
| Create a draft from a job text | 1280 | Pass | Draft shows name, job and recommended model |
| Review shows authority and ceiling | 1280 | Pass | "Full authority" text. Absolute ceiling 1.00 TST, initial allowance 0.10 TST |
| Two credential labels, one per line | 1280 | Pass | Textarea holds 13 and 9 characters on two lines |
| Save, reload, labels kept | 1280 | Pass | Same two lines after reload |
| Approve creates the computer and reaches the wait | 1280 | Pass | Phase "Waiting". Steps run in order. First credential wait shows the warning not to use chat |
| Password field masking | 1280 | Pass | Input type is `password`, autocomplete off. Snapshot shows dots. Pixel check of the field image shows dots only |
| Generated value in page, reference retained | 1280 | Pass | Value made in page JS, set through the native setter. Node reference kept. Only length and hash returned |
| Deliver returns 204 and the wait moves on | 1280 | Pass | 204 from the secret endpoint. Next wait is the second label |
| Field clears on submit | 1280 | Pass | Retained node detached. Value length 0 |
| Guest file holds the delivered value | 1280 | Pass | Two lines, mode 0600, directory 0700. Each line matches its value's hash prefix |
| Value leak scan | 1280 | Pass, 0 hits | Database, WAL, shared memory, journal, executor state, receipts, Deck log, model log. Deck argv and environ (before the restart) |
| Stale request identity | 1280 | Pass | 409. Wait unchanged. Guest file unchanged |
| Stale version | 1280 | Pass | 409. Wait unchanged |
| Restart of the owned Deck process | 1280 | Pass | 204 from the restart route. Reload shows the same wait at version 13 |
| Second delivery after restart | 1280 | Pass | 204. Setup Ready at version 15. No wait remains |
| Label edit at phone width | 390x844 | Pass | New draft. Labels of 11 and 10 characters kept after reload |
| Phone layout | 390x844 | Pass, DOM metric only | No horizontal overflow. No image review |
| Console errors | both | None | `error` level, both sessions |

## 3. Safe hashes and counts

- Hash prefix of the first delivered value (SHA-256, first 16 hex): `fb1155ba3131d107`. Length 36.
- Hash prefix of the second delivered value: `62da2478550960af`. Length 36.
- Both prefixes match the guest file entries. The guest file holds 2 lines.
- Occurrences of either value outside the guest file: 0 on every surface.
- The Deck process check ran on the process before the restart. No check ran on the restarted process.

## 4. Not run, or not verified

- **Owner challenges (phone approval and CAPTCHA):** Not run. The stack produced no challenge wait. Seeding a wait would write to the live database behind a running server. No seeded snapshot was created.
- **Active-task secret requests:** Not run. The route `GET /api/bots/{id}/secret-requests` exists in source. No task waited on a secret.
- **First task and runtime completion:** Not run. The fake guest made no executor socket.
- **Escape clears the field:** Not run. The clear on submit passed.
- **Clear button:** Not run.
- **Keyboard-only navigation:** Not run. Labels and roles appear in the snapshot.
- **200% zoom:** Not run. The bare tool set has no zoom control.
- **Screen mirror through bare:** Not verified. Bare's `window` option opens a Wayland window. It takes no screen argument, so nothing shows that it can attach to the hyprcage screen. Bare ran headless. No bare window opened on any screen.
- **Backend API documentation at `/docs`:** Does not exist. The source has no Swagger or OpenAPI route. This report claims no documentation page.

## 5. Findings (not changed)

1. **Review opens on the second click.** The first click on "Review setup" only focuses it, at both widths. This matches baseline finding 8.
2. **`/api/bot` returns 404 on every page load.** The web client polls a legacy route. This matches baseline finding 7.
3. **Label entry with a line break needs a real newline.** Bare's `type` op clears the field first. A literal backslash and `n` went into the field and did not split the label. A real line break worked. Whether this is a bare bug or a caller error is not verified. No bare issue was filed.
4. **Ready does not prove a working computer.** The fake guest reached Ready with no executor socket. This matches baseline finding 3.

## 6. Cleanup

- Bare sessions closed (two: headless desktop and headless phone).
- The hyprcage screen was destroyed.
- The fixture stack shut down through its own route. The listener closed. The proxy and the restarted Deck process are gone. Nothing else was touched.
- Evidence (one element image and the scripts) stayed outside Git.
