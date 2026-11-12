# Deck progress and plan

Update this file when a stage or verification step ends.

## One-bot runtime: 2026-11-12

Stages 1–3 are complete for one configured bot with synthetic tasks. The integration branch contains the code and evidence. Upstream merge is pending. No deployment is required for local use.

| Stage | Verified result | Evidence |
| --- | --- | --- |
| 1 | Full Arch desktop, bare, artifacts and five saved-state cycles | [Stage 1 results](vm-stage1-results.md) |
| 2 | Guest executor, durable command records, cancellation, artifacts and restore fencing | [Stage 2 results](stage2-results.md) |
| 3 | Server integration, queue, controls, idle save, wake, event batching and subscribed screen capture | [Stage 3 results](stage3-results.md) |

The parent checked the UI in bare. The strict real-VM run used the real server and a scripted model. It completed browser, shell and file work, saved the idle computer and restored it for the next task. Deterministic tests cover stop, steer, holds and uncertain outcomes.

### Current resource baseline

These are single-run observations, not capacity estimates or before/after performance claims. CPU is a percentage of one core. The server memory is shared Deck overhead.

| State | VM memory | VM CPU | Server memory | Server CPU |
| --- | --- | --- | --- | --- |
| Working | 531 MiB | 5.57% | 20.9 MiB | 0.60% |
| Awake, idle | 534 MiB | 4.39% | 21.3 MiB | 0.07% |
| Saved | 0 | No process | 21.4 MiB | 0.00% |

No model calls or captures occurred in the idle and saved windows. Capture stopped after the viewer disconnected. A restored task completed in about 3 seconds. The workload was small and synthetic.

### Next: optimize one integrated bot

1. Measure one owned workload at a time. Keep working, awake-idle and saved results separate.
2. Profile residual guest CPU while awake and idle. Identify the process and timer before changing the desktop.
3. Measure active command polling, output handling and artifact transfer. Remove work only where evidence justifies it.
4. Measure screen capture cost with and without a viewer. Preserve zero capture without a caller.
5. Inspect the web bundle warning and split optional code only if it improves loading without changing behavior.

Keep the full desktop, durable recovery and control gates. Record each change and its before/after workload in [the optimization plan](optimization-plan.md). Multi-bot load tests follow this pass. Horizontal scaling will also need independent ownership, storage and capacity controls.

### Open product and verification work

Automatic spending accounting, real accounts and service events, memory integration and conversational provisioning remain open. Fresh guest provisioning and real failure halfway through a save remain untested. Some UI actions retain deterministic coverage rather than real-VM browser coverage. See the stage reports for exact limits.

## Browser tools workstream

The following history and plan describe the separate browser-tools workstream. They do not establish the integrated VM runtime state.

## Goal

Give the `deck` agent every browser tool that it needs, in `bare-mcp`. Read pages as text first, so that the agent needs few screenshots. Speed and low resource use come first. `bare` may do a task its own way, not the Chrome way.

## Scope

- In scope: `bare-mcp` tools, `bare` engine gaps that real sites show, and the `deck` service (agent loop, tools, prompt).
- Out of scope: the chat UI in `web/`. A separate session rebuilds it. It renders in bare since bare #410.

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
| 2026-11-12 | `id` globals and named forms; `eval` prints arrays and objects as JSON; `Intl.ListFormat` and `Intl.DisplayNames`; the Wikipedia search box keeps its text; `act` replies after an in-page navigation; `find` and locator fixes; an error for unknown arguments; `max_chars` limits | bare #403 |
| 2026-11-12 | `offsetTop` of an inline box with nothing to paint; storage bounds throw `QuotaExceededError`; `press` follows a replaced field | bare #407 |
| 2026-11-12 | The not-ready note names the pending requests; `type` follows a replaced field; a live page sends a request at once; pages with custom elements keep the style cache; `offsetTop` of a wrapped inline box; no CPU spin in `settle`. GitHub issues opens ready in about 10 s (was 20–27 s) | bare #408 |
| 2026-11-12 | A fixed eval set of 17 agent tasks with a fixture site and a runner (`evals/`) | deck #19 |
| 2026-11-12 | `links` filters (`text`, `root`, `external`); `WritableStream`, `TransformStream` and the pipe methods | bare #409 |
| 2026-11-12 | The prompt points the model to `browser_links` for URLs and lists `browser_eval`. Evals: 17 of 17 pass (16 before); `eval` calls drop from 9 to 5 | deck #20 |
| 2026-11-12 | Links inside headings show in `snapshot` and `links` (GitHub issue titles); `document.fonts` is an EventTarget, so the deck web app renders in bare; `new Response(stream)` and `Blob.stream()`; custom properties stored as numbers (about 15 % to 1.4 % of CPU on GitHub); `eval` of a microtask-only promise returns at once | bare #410 |
| 2026-11-12 | A script `click()` on a link follows it; a narrower `checkVisibility` gate; `eval` takes `return` with or without `await`, and a syntax error names its line | bare #411 |
| 2026-11-12 | `eval` follows a navigation that its code starts; a `Window` interface, so the GitHub issue page renders | bare #412 |
| 2026-11-12 | Standard members of common elements (missing members on an audit page drop from 159 to 38 of 338): table rows and cells, `form.elements`, `dialog`, attribute reflection; `meta.content`, so a click on a GitHub issue shows the issue | bare #413 |

## Checks with a real model

- Hacker News: top 3 stories with points, then the first comment author. The model used snapshot, read, act and find. It took no screenshot.
- Wikipedia: a search, two facts, and a screenshot of the infobox, sent into the chat in 14 s.
- A local CSV download: the model downloaded it, sent it into the chat and gave the correct total.
- crates.io: the version, the all-time downloads and the license of `serde`, and a full-page screenshot sent into the chat, in 13.7 s. All three facts were correct.
- A local order form: the model typed, selected, checked, uploaded `note.txt` and submitted, and gave the right order line.
- crates.io `tokio`: the version, the license and the repository URL, in 12.4 s.
- A local login with a `${NAME}` secret, in 9.4 s. The password is not in the run events, the log or the data folder.
- The eval set (`evals/`): 17 of 17 tasks pass with the deck prompt of deck #20.

## Browser tools checkpoint

The browser-tools checkpoint reported merged branches and bare main `052736a7`. This is historical state. It does not identify the browser binary in every VM image.

## Next

1. Run the eval set 3 times on the current `bare-mcp` to find flaky tasks.
2. Add harder tasks: the GitHub issue path (open the list, click an issue, read it), a search with filters, and a task that needs `find` and `read` together.
3. Fix what the new tasks show. Do speed work only where a task times out.

## Open gaps

- `find` on a link text gives the inner `generic` element, not the link.
- `history.length` reads 1 after each load.
- Not added: `iframe.contentDocument`, `input.valueAsNumber` and `stepUp`, `canvas.width` and `height`, `script` and `style` reflections. `li.value` is `undefined` until a page names an `HTML*Element` global.
- A `Request` with a stream body is refused.
- Selector matching is the largest style cost on GitHub (about 16 % of an `open`).
- GitHub issues `open` takes about 10 s, close to the step limit.
- `type` replaces the field text. There is no append mode.
- Window mode is not tested with the new tools.

## How the work runs

- Each task gets its own `bare` worktree and a child agent.
- Each change gets a failing test first.
- An integration branch joins the branches, and one pull request merges them.
- Each child uses its own build folder. A shared folder links crates from other branches.
- A test agent runs all suites of the integration branch in a new, empty build folder before the merge.
- After each merge, `bare-mcp` is repinned from that clean build. The old binary stays as a backup.
- The eval set measures each change with the real model.
