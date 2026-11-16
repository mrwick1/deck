# Requests review checks

## Round one feedback exchange

Two reviewers (Sol and Opus) reviewed daf5581. This table lists each finding and its correction.

| Finding | Source | Correction |
| --- | --- | --- |
| A poll that began before a write returned its old list. The answered card came back and enabled a second POST. | Opus, measured | A forced read waits for the read in flight, then starts a new one. A read that began before a write ended is dropped, never applied. |
| The guard and card key were lost on a tab switch. | Sol | One store lives in `useRequests`, which the bot workspace owns. Operations, drafts, answer keys and review marks live there. The guards are plain fields. |
| A new ceiling version remounted the card. The amount, error and focus were lost. | Opus, measured | The card key is the kind and the id. The draft keeps its amount. The card shows the reviewed and the current ceiling. The owner must review the change before an answer. |
| The ceiling used a float. It lost digits near 2^53. | Both | The text uses the existing `formatMinor`. A server amount that is unsafe or has a precision outside 0 to 9 shows "unavailable" and blocks approval. Keeping the ceiling stays possible. |
| An invalid amount had no `aria-invalid`, no description and no focus. | Sol | The field has `aria-invalid` and `aria-describedby`. A failed answer focuses it. |
| A bad number and a number not above the ceiling had one message. | Opus | There are six messages: empty, syntax, too many decimals, too large, not above, unsupported. |
| A lost reply said "Cannot reach the server" and re-enabled the buttons. | Opus | Status 0 and 5xx mean unknown. The card stays locked. A read starts at once. |
| A disabled fieldset dropped focus to BODY and showed no pending or success text. | Opus | The buttons use `aria-disabled` and handler guards. The input is read-only. A status region above the cards shows pending, accepted, confirmed and failed. Its text lives in the store. |
| A long title or detail overflowed the panel. | Opus | `requests.css` wraps anywhere. |
| The `h3` card heading had no style. | Opus | `requests.css` styles it. |

## Behavior

- Pending: the server has not answered. The card says it is sending.
- Accepted: a 2xx answer. The text says accepted. It does not say the state changed.
- Confirmed: accepted, and a later read started after the answer no longer lists the request (or lists a new ceiling version).
- Unknown: status 0 or 5xx. The write may have happened. The card stays locked and a read starts.
- A read that fails leaves the card locked. The owner uses Check status.
- A confirmed answer followed by a failed or stale read never offers a repeat on its own. The card offers Check status. After a read that still lists the request, it also offers "Review and respond again".
- 4xx is a final rejection. The draft stays. The text says the request changed or is not open.
- Focus stays on the pressed button while pending. After a card leaves, focus moves to the next card, the empty text or the panel heading. It moves only if focus was in the Requests tab. It never moves after a tab switch.
- The shared wait panel and the task secret panel reload with a forced read.

## Contract limits

These limits come from the backend. This change adds no API.

- Budget hold release, task retry, task cancel and outcome decisions send no version or key.
- The card for such a request cannot prove that a hold which left the list and came back is the same hold.
- The client therefore never resends on its own. A card that returns after a response asks the owner to review it first.
- After an unknown result, a read that still lists the request cannot prove the write failed. The owner chooses "Review and respond again".
- A ceiling answer has a key. The key is kept for one id, version, decision and exact amount. A resend after "Review and respond again" reuses it.

## Verification

- `requests-race.test.ts` runs the real `useRequests` in a server render with a mocked API. It failed on daf5581: the old hook joined the stale read and made one read. It passes now.
- `requests-store.test.ts` covers the read race, one operation per card, lost replies, a failing check read, a returning no-version request, 409, answer keys across versions, and exact amounts near 2^53.
- Type check, all tests and the build pass. The build keeps its known chunk-size warning.
- A scripted bare session (no window) used an in-memory fake of the inbox and POST routes at 412x820, 320x640 and 412x450. The panel and the document did not overflow at any size. A 180-character unbroken title wrapped. No button or input was under 44px. The amount input was 16px.
- The held-GET run held a poll, pressed Release hold, then released the poll. The card did not return. One POST went out.
- The lost-reply run sent one POST. A second press sent none.
- The 409 run kept the typed amount and focused the Raise ceiling button. The card showed the old and the new ceiling.
- A tab switch kept the lock and the draft. Focus stayed on BODY after the return, as it was left.

## Not tested

- Physical pointer and touch input. The driver used scripted clicks.
- Screen readers.
- Browsers other than bare.
- Focus after the removal of a focused node in browsers that fire no blur event. The code uses `isConnected` to tell removal from a real blur.
- A real backend. The fake serves a fixed three-card list.

## Second-round shared-panel correction

The second-round review found that challenge responses still used a panel-local guard. The workspace now retains the challenge guard and request-scoped outcome. The panel subscribes when it mounts again. Shared setup and task-secret panels also participate in the card-removal focus sequence.

The server-render regression failed before the change: a remounted panel ignored its pending response and rendered enabled controls. It passes after the change. All 193 tests, the type check and build pass. The build retains the existing chunk-size warning.

The parent used a held synthetic challenge response, switched to Activity and back, and activated the response again. The request count stayed at one and the controls remained visibly disabled. After the shared panel was removed, focus moved to the empty state. The local evidence is `/tmp/deck-requests-shared-final.log`. These are scripted bare interactions, not physical input or screen-reader tests. Protected-input delivery and expiry focus were not rechecked in this correction.

## Route-return and cross-screen correction

Both second-round reviews are complete. The remaining route-lifetime defect is corrected. `RequestsProvider` retains one store and challenge response state per bot above the route. Activity Retry and Cancel now use that store and the same task identity as Requests. A returned-request review also guards direct operation calls.

A fixture run at 412×820 kept the unknown-response lock and the typed amount `7.25` after Home and return. A second Retry sent no POST. Activity Retry had `aria-disabled=true`; a scripted click sent no second POST. Reviewing a changed ceiling focused its input. Reviewing a returned hold focused its response button. A returned hold replaced the old closed-request status with a review notice. Evidence: `/tmp/deck-requests-route-final.log`.

The shared-panel correction ran at 412×820, 320×640 and 412×450. Each run sent one challenge POST across a tab return and focused the empty state after panel removal. Neither the document nor the Requests panel overflowed.

Limits: this state lasts for the current app instance, not a full page reload. A failed connection still disables protected-input fields and can move focus away from them. Backend hold identity remains ambiguous. Parallel operations can conservatively require another read. Physical input, screen readers, real-backend behavior and frame pacing remain untested. The screen gate is closed with these limits; Activity and Chat retain separate review gates.
