# Progress and plan: bare tools for the deck agent

This file is the plan and the state of the work. Update it when a step ends.

## Goal

Give the `deck` agent every browser tool that it needs, in `bare-mcp`. Read pages as text first, so that the agent needs few screenshots. Speed and low resource use come first. `bare` may do a task its own way, not the Chrome way.

## Scope

- In scope: `bare-mcp` tools, `bare` engine gaps that real sites show, and the `deck` service (agent loop, tools, prompt).
- Out of scope: the chat UI in `web/`. A separate session rebuilds it.

## Done

| Date | Change | Where |
| --- | --- | --- |
| 2026-11-12 | Snapshot with refs, `act`, `find`, `read`, `links`, `screenshot`, `eval`, `sessions` | bare #380 |
| 2026-11-12 | `deck` offers every `bare-mcp` tool as `browser_<name>` | deck #11 |
| 2026-11-12 | A tool reply that is not an object no longer stops a run; a panic fails the run | deck #12 |
| 2026-11-12 | `act` without a snapshot reports a navigation | bare #383 |
| 2026-11-12 | `console`, `network`, `diagnose`, `downloads`, `storage`; select, check, hover, double click, upload, back, forward; dialogs; new tabs; iframes and shadow slots in the snapshot | bare #386 |
| 2026-11-12 | Fixes from a sweep of 20 real sites: cut marker and `offset`, a quick settle, `press` on a ref, focus checks, visible `find` matches, names of form fields, `open` waits for a ready page, full URLs | bare #386 |
| 2026-11-12 | Smaller replies: `act` gives only the changed lines (80–99 % smaller); full snapshots are 37–58 % smaller | bare #386 |
| 2026-11-12 | `deck` saves downloads in the workspace and sends uploads only from it | deck #13 |
| 2026-11-12 | Full-page screenshots (up to 8000 px); the RegExp `v` flag, so crates.io renders; `adoptedStyleSheets`, and `snapshot` and `read` walk shadow roots, so archive.org shows its content | bare #390 |
| 2026-11-12 | Relative upload paths resolve against the workspace | deck #15 |
| 2026-11-12 | `eval` awaits a Promise; late dialogs and tabs show in the next reply; `script error:` lines in `open` and `act`; a click waits up to 1 s for a short cover and names a lasting one; crates.io renders fully | bare #399 |

## Checks with a real model

- Hacker News: top 3 stories with points, then the first comment author. The model used snapshot, read, act and find. It took no screenshot.
- Wikipedia: a search, two facts, and a screenshot of the infobox, sent into the chat in 14 s.
- A local CSV download: the model downloaded it, sent it into the chat and gave the correct total.
- crates.io: the version, the all-time downloads and the license of `serde`, and a full-page screenshot sent into the chat, in 13.7 s. All three facts were correct.
- A local order form: the model typed, selected, checked, uploaded `note.txt` and submitted, and gave the right order line.
- crates.io `tokio`: the version, the license and the repository URL, in 12.4 s.

## In progress

- `TransformStream` and `WritableStream` (bare#389). Top priority: without them the new React web app does not render in bare, and `tests/screenshot_e2e.rs` fails.
- `id` globals and named forms (bare#392).
- `eval` prints arrays and objects as JSON (bare#393).
- `Intl.ListFormat` and `Intl.DisplayNames`: the GitHub issue list needs them (bare#394).
- The Wikipedia search box loses its text (bare#395).
- `act` replies show the old page after an in-page navigation (bare#396).
- `find` and locator faults, and an error for unknown arguments (bare#397).
- Reply size: text changes in the `act` diff, a short `open` reply, `max_chars` limits (bare#398).

## Next

1. Merge each branch above when its tests pass, then rebuild and repin `bare-mcp`.
2. Run a new sweep of real sites with the new tools. File a `bare` issue for each new gap.
3. Run more real-model tasks in `deck`: a login with `${NAME}` secrets, a form with select and upload, a download sent into the chat.
4. Look at the open gaps below.

## Open gaps

- Dialogs and tabs that a timer opens after `act` returns are not seen.
- Window mode is not tested with the new tools.
- A link click that the site covers with a loader fails as `covered`.

## How the work runs

- Each task gets its own `bare` worktree and a child agent.
- Each change gets a failing test first.
- An integration branch joins the branches, and one pull request merges them.
- After each merge, `bare-mcp` is rebuilt and repinned. The old binary stays as a backup.
