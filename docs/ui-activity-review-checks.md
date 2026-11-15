# Activity and results review

## Both first-round reviews

Both reviewers found missing pending and confirmed action feedback. They found the reviewed ceiling used floating point. Completed tasks had no conversation entry point. The second reviewer also found lost action focus, a large connection banner above the bot header, stale task errors, missing priority changes for held tasks, and a result heading that implied chronology without timestamps.

## Corrections

- Retry and Cancel use the Requests store. The app retains that store across routes. Activity cannot bypass an unknown response. Both screens use one task identity.
- The panel and task rows show pending or uncertain outcomes. Request acceptance is distinct from a later state read. Priority changes name the task and announce acceptance.
- Action buttons remain focusable while guarded. When a task action disappears, focus returns to Tasks if the user did not leave the panel or focus another control.
- Held and queued tasks offer priority. Completed tasks offer Open conversation. This opens the bot chat; it does not claim to locate a particular message.
- The reviewed ceiling and allowance use the integer formatter. The accounting period is shown.
- A compact connection message appears below the bot tabs. Writes stay blocked while stale.
- Action failures appear only while the task remains actionable. Unknown response state remains available in Requests.
- Home says Completed results. The API has no completion timestamp, so the UI makes no chronological claim. The existing eight-result limit remains.

## Checks and limits

The server permits priority changes for queued and held tasks. Only held tasks can retry. Running tasks require Stop instead of Cancel. These rules come from `src/bot/mod.rs`.

The parent fixture driver is `/tmp/deck-workspace-final.py`; its output is `/tmp/deck-workspace-final.log`. It checks exact large amounts, held priority, held task actions, duplicate guarding, focus after Retry, result navigation and wake feedback at three CSS viewports. These are scripted bare interactions with in-memory endpoints. They do not prove physical input, screen-reader output or frame pacing. Real backend acceptance remains open. Chat rendering has its own review gate.

The parent run completed at all three sizes. It rendered `9007199254740.991 USD` exactly and showed priority for a held task. Two Retry activations sent one POST. Pending focus stayed on the button; after Retry changed the task to queued, focus moved to Tasks. Open conversation selected Chat. Two Wake activations produced one wake POST, retained focus and showed Requesting wake. All sampled panels had equal client and scroll widths. Full typecheck, 251 tests and build passed during integration; concurrent Chat work was present. The build retains its chunk-size warning.

The GPT Sol Medium second-round review passed with limits. Its independent three-size fixture run confirmed exact amounts, task response guarding, pending focus, focus after Retry and conversation navigation. Its additional run confirmed an unknown-response hold across Activity and Requests. Priority POST acceptance and stale-layout geometry remained source-only. The other peer's second-round verdict remains pending.

## Final round-two corrections

Both peer reviews completed. The parent corrected the required focus findings. At 412×820, 320×640 and 412×450, Open conversation focused the Chat tab. Computer Retry and Reconnect focused the persistent Computer screen region. No action left focus on BODY. Evidence: `/tmp/deck-workspace-focus-final.log`. The screen gate is closed with the existing browser and device limits. This does not establish physical motion, screen-reader output or real-backend acceptance.
