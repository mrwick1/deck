# Computer viewer review checks

Date: 2026-11-15. Status: round-one corrections implemented. Not yet reviewed in round two.

## Files

- `web/src/experience/computer-controller.ts`: the stream, fetch, retry and URL logic. It uses no React.
- `web/src/experience/Computer.tsx`: draws the controller state. The signature is `Computer({ id })`.
- `web/src/experience/computer.css`: the bounded frame box and the 44 px Retry button.
- `web/src/experience/computer-controller.test.ts` and `computer-wiring.test.ts`: the checks.

The parent must import `computer.css` through `Computer.tsx` (already done) and may remove the old `.live-screen` rules from `live.css`.

## Behaviour

| Event | Result |
| --- | --- |
| Frame event | Cancel older work. Reset the retry budget. Fetch the frame. |
| Fetch fails, wrong type, empty body | Clear the frame. Retry the same sequence after 500, 1000 and 2000 ms. Then show Retry. |
| PNG bytes do not decode | Revoke the URL. Follow the same retry budget. |
| HTTP 204 | Clear the frame at once. No retry. |
| Image loads | Show the frame. Until then show "Loading the screen". |
| Stream error, state CONNECTING | Clear the frame. Say "Reconnecting". The browser reconnects. |
| Stream error, state CLOSED | Clear the frame. Say "stopped". Show Reconnect, which opens a new stream. |
| Unmount or `id` change | Close the stream. Abort the fetch. Cancel the timer and the decode. Revoke every URL once. |

The server sends the current sequence when a stream opens (`screen_stream` in `src/bot/api.rs`). A new stream therefore fetches a frame without a change on the desktop.

## Review checks

1. A failed fetch of an unchanged sequence retries. After three retries the viewer shows Retry.
2. The text "Retrying" shows only while a timer runs.
3. A 200 `image/png` response with bad bytes never shows a broken image. It ends in Retry, not in a loop.
4. A 204 response removes the visible frame before any other work.
5. A frame event that arrives during a fetch, a wait or a decode wins. The old callbacks do nothing.
6. A CLOSED stream never says "Reconnecting".
7. Unmount leaves no timer, fetch, stream or object URL.
8. Portrait and landscape frames fit the box and do not widen the page.

## Run

`npm --prefix web run check`, `npm --prefix web test`, `npm --prefix web run build`.

## Not measured

- No real browser run. The tests use fakes for `fetch`, the stream, the timers, the URLs and the image load.
- `Computer.tsx` has no DOM test. The wiring test reads its source.
- `bare` can report a valid PNG blob with `naturalWidth` 0. The component does not check `naturalWidth`, so it does not reject such an image. This is a `bare` limit, not a product check.
- The CSS box height (`clamp(240px, 60dvh, 640px)`) is not measured in a browser.

## Parent integration and toolbar

The parent copied the controller and component from `2096ac5`. Wake and sleep now show pending status and announce a request only after the server accepts it. The toolbar keeps focus while its synchronous handler rejects duplicates. Transitional waking and saving states reject new actions. State copy distinguishes asleep, waking, saving and failed. Privacy checks distinguish loading, failed checks and a known owner request; all remain fail-closed.

The parent fixture driver is `/tmp/deck-workspace-final.py`; output is `/tmp/deck-workspace-final.log`. It uses synthetic events and responses. It checks retry exhaustion on an unchanged frame, manual Retry followed by 204, stopped-stream Reconnect, tab cleanup and mobile panel geometry. Valid PNG decode, portrait/landscape fitting, physical touch and frame pacing need separate evidence. The controller tests mock image callbacks; they are not visual proof.

The parent run completed at 412×820, 320×640 and 412×450. Each failed frame made exactly four fetches before Retry appeared. Retry followed by 204 showed the privacy message. A CLOSED stream showed Reconnect. Switching tabs closed every owned screen stream. Panel and document widths matched at all sizes. At 412×450 the 270px frame extends below the initial viewport; its enclosing panel scrolls. No valid PNG or frame-rate claim follows from these checks.

The GPT Sol Medium second-round review passed with limits. Its independent three-size fixture run confirmed bounded retry exhaustion, Retry followed by 204, stopped-stream Reconnect, tab cleanup and scrolled access to Retry at reduced height. Actual invalid PNG decode and valid portrait/landscape rendering remain unmeasured. The other peer's second-round verdict remains pending.

## Final round-two corrections

Both peer reviews completed. The parent corrected the required focus findings. At 412×820, 320×640 and 412×450, Open conversation focused the Chat tab. Computer Retry and Reconnect focused the persistent Computer screen region. No action left focus on BODY. Evidence: `/tmp/deck-workspace-focus-final.log`. The screen gate is closed with the existing browser and device limits. This does not establish physical motion, screen-reader output or real-backend acceptance.
