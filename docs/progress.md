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
| 2026-11-12 | `deck` saves downloads in the workspace and sends uploads only from it | this branch |

## Checks with a real model

- Hacker News: top 3 stories with points, then the first comment author. The model used snapshot, read, act and find. It took no screenshot.
- Wikipedia: a search, two facts, and a screenshot of the infobox, sent into the chat in 14 s.

## In progress

- A screenshot of a tall element is cut at the viewport (bare#384).
- The RegExp `v` flag: crates.io renders nothing without it.
- `adoptedStyleSheets` and `CSSStyleSheet.replaceSync`: archive.org needs them.

## Next

1. Merge each branch above when its tests pass, then rebuild and repin `bare-mcp`.
2. Run a new sweep of real sites with the new tools. File a `bare` issue for each new gap.
3. Run more real-model tasks in `deck`: a login with `${NAME}` secrets, a form with select and upload, a download sent into the chat.
4. Look at the open gaps below.

## Open gaps

- `archive.org`: the snapshot is empty, although the shadow root has content.
- Dialogs and tabs that a timer opens after `act` returns are not seen.
- Window mode is not tested with the new tools.
- A link click that the site covers with a loader fails as `covered`.

## How the work runs

- Each task gets its own `bare` worktree and a child agent.
- Each change gets a failing test first.
- An integration branch joins the branches, and one pull request merges them.
- After each merge, `bare-mcp` is rebuilt and repinned. The old binary stays as a backup.
