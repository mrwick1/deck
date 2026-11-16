# Chat screen checks

The code is in `web/src/experience/Chat.tsx`, `chat-flow.ts` and `chat.css`.
The fixture is `web/review/home.html?mode=chat`. It keeps all traffic in memory.

## Behavior

- The Deck chat session lives in `ChatHost`, above the routes. It draws into the slot of the chat route. The route change from `chat/new` to `chat/<id>` keeps the session. So does a visit to another screen.
- A send shows a pending bubble at once. The textarea clears at once. Later typing stays in the draft.
- Text typed during the first send moves to the draft of the created chat. The new-chat draft stays empty.
- A refused send puts the text back in front of the later draft. Nothing typed is lost.
- The bubble moves from the textarea to its place with Motion. Only transform and opacity change. With reduced motion it appears in place.
- An unconfirmed send shows a panel. It says the evidence is not proof. It offers a read-only check, "It was sent" and "It was not sent. Edit it". Deck never sends it again by itself.
- An unconfirmed create holds the message in a panel. The user checks Conversations, then restores the message.
- A failed history read shows the error with Retry. The welcome text stays hidden.
- Stop sends one request and shows "Stopping…".
- Latest shows only when the reader is away from the end. It sits inside the message pane.
- Ctrl or Cmd with Enter sends. Enter adds a line. Enter that confirms an IME composition does nothing.
- Each bot chat session now lives above the routes in BotChatHost. Its view follows the workspace slot; pending and unconfirmed sends survive route changes.

## Checks

- `npm --prefix web run check`, `test` and `build`.
- `chat-flow.test.ts` and `chatRuntime.test.ts` cover the pure helpers.
- Rendered checks use `bare` at 412x820, 320x640 and 412x450. The fixture modes are `create`, `send` and `history` on `window.__chatFake`.

## Limits

- `bare` does not compute transforms, sticky scroll or `field-sizing`. The motion is checked in source and by the inline start style.
- `bare` fires no scroll event for a script scroll. The checks dispatch it by hand.
- The hook wiring has no React unit test.

## Parent lifecycle checks

At 412×820, 320×640 and 412×450, a held bot send survived Home and return with one POST and one stream. After its reply was lost, the recovery panel also survived another Home round trip. A forced repeat sent nothing. The document had no horizontal overflow. Evidence: `/tmp/deck-bot-chat-persist.log`.

Unconfirmed messages are retained per conversation, not in one replaceable slot. A fixture send to chat 3 lost its reply. A second conversation then lost another reply. Returning to chat 3 retained its own message and blocked sending. Evidence: `/tmp/deck-chat-two-unknown.log`. These checks use scripted bare actions and synthetic data. They do not measure physical motion.

## Round-two early-stream correction

Sol found that live output preceded the pending question and replaced its DOM node. The parent extracted the existing merge into `sendItems`, added a regression, and observed its assertion fail with assistant-before-user order. The corrected merge adds the pending question before the run output. Typecheck, all 252 tests and the build pass. The build retains the existing chunk-size warning.

At 412×820, 320×640 and 412×450, the question precedes the early reply. Its original DOM node remains connected and identical both before and after POST confirmation. The driver waits for sending to finish. All sessions closed. Evidence: `/tmp/deck-chat-order-checked.log`. An earlier driver used a text locator for the icon-only Stop control and timed out; that assertion is not counted. Physical motion and real-backend behavior remain untested. The other peer review remains open.

## Round-two recovery isolation

The second peer found that acknowledging an existing conversation also cleared a separate unknown-create message. A parent fixture regression failed before the correction: after acknowledging chat 3, New conversation had no held text. Evidence: `/tmp/deck-create-hold-red.log`. One initial locator in that driver was malformed; the later lost-text assertion and state probe established the defect.

Acknowledgement now clears only the current conversation response. A separate action restores and clears the unknown-create message. That panel and its send block apply only to New conversation. The send handler also rejects a second create while its hold exists.

Remaining limits: navigating to another chat while the first create is held can briefly reopen the newly created chat controller before returning to the selected route. The message reaches the created conversation, but this can add a history read and stream restart. Actual bubble travel, frame pacing, physical input, screen readers and real-backend acceptance remain unverified. Bot chat-ID replacement remains a server-contract hypothesis.

The corrected fixture check passed at 412×820, 320×640 and 412×450. Acknowledging chat 3 kept its composer usable and showed no create panel there. Returning to New conversation retained `Keep me`; its own restore action returned that text to the draft. Each run made only the original message POST and create POST. All owned sessions closed. Evidence: `/tmp/deck-create-hold-green.log`. Both round-two reviews and required parent corrections are complete. The Chat review gate is closed with the limits above.

Final integration checks: TypeScript check, 252 web tests, production build, Rust formatting check and Clippy pass. `cargo test` passes 317 tests; two live-browser integration tests remain ignored. The build retains its existing bundle-size warning.
