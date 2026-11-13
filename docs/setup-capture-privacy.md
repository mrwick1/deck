# Screen capture privacy

Date: 2026-11-13. Base: 7c2be62.

## What the code does

- `Screen::work` runs one capture at a time, only while a viewer watches and the computer is awake.
- The privacy gate is a closure over the store. Legacy `Bot::new` and the managed proxy install it. It answers `(open, generation)` from one SQL statement. A store read error counts as open.
- The generation is a per-bot counter in the table `privacy_generations` (schema 9). It grows by one at each secret delivery, in the same transaction that opens the gate. Closing the gate does not delete it. The table holds no protected value.
- The SSE route `/api/bot/screen` sends the sequence number only. The client fetches `/api/bot/screen/latest`.
- `latest` answers 204 with no body when the frame is empty.

## Gaps found and fixed

1. A capture could finish after the gate opened. The worker checked the gate only before the grab. It then published the frame. Now it checks again after the grab and drops the result. The `withheld` counter shows each drop.
2. The worker cleared the cached frame only when it saw the gate open. If the gate opened and shut between two worker looks, the old frame came back through `latest`. Now `latest` clears the cache when it sees the gate open. The clear moves the sequence, so a viewer fetches the empty answer.

3. A gate could open and shut again inside one uncancellable grab. The worker read the gate before and after the grab and saw it shut both times. Now it reads `(open, generation)` before the grab and after it. It drops the frame when the gate is open or the generation differs. `latest` also clears a cached frame whose generation is older than the current one.

Tests in `src/bot/screen.rs` use a capture that the test releases by hand. They first failed (four red), then passed after the fix. The test in `src/bot/api.rs` uses a real in-memory store and the real `latest` handler body. It checks the public frame, 204 while open, zero captures while open, and a new public frame after the gate shuts. It did not fail before the fix on its own, because the worker also clears the frame within one interval.

The generation tests in `src/bot/screen.rs` hold a grab by hand, move the generation and shut the gate before release, and assert that the frame is not published (`withheld` is 1, `delivered` stays 1). They failed before the worker change and passed after it. A fresh public capture then resumes. Other tests cover persistence across close and reopen of a file store, an unknown bot at 0, and a failed read that answers open. A bool-only gate (`set_privacy_gate`) keeps its old behavior with generation 0.

## Not covered

- A test with fixed bytes proves the pipeline gate. It does not prove that real guest pixels show no value.
- `browser_screenshot` and `browser_step` with `shot` or `screenshot` refuse while the gate is open. A failed lookup also refuses. `browser_read`, `eval` and shell are trusted (D33). This is not universal redaction.
- The managed proxy sets the gate through a `OnceLock`. The first setter wins. Both setters use the same store read.
- The test fixture in `src/bot/mod.rs` replaces the bot screen with one that has no gate. It does not test the wiring.

## Live checks for the combined UI and VM run

1. With a real signed-in-guest, deliver a synthetic value. Check that `/api/bot/screen/latest` answers 204 and the capture count stops.
2. Verify the sign-in. Check that the next frame is new and differs from the frame before delivery.
3. Check that the SSE sequence moves at the gate open and at the gate close.
4. Check on both the legacy bot and a managed setup bot.
5. Check that real guest pixels show no value in any frame after delivery.
