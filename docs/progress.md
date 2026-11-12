# Deck progress and plan

Update this file when a stage or verification step ends.

## One-bot runtime: 2026-11-12

Stages 1–3 are complete for one configured bot with synthetic tasks. PR #22 merged the code and evidence. The merged revision is `1085df9`. No deployment is required for local use.

| Stage | Verified result | Evidence |
| --- | --- | --- |
| 1 | Full Arch desktop, bare, artifacts and five saved-state cycles | [Stage 1 results](vm-stage1-results.md) |
| 2 | Guest executor, durable command records, cancellation, artifacts and restore fencing | [Stage 2 results](stage2-results.md) |
| 3 | Server integration, queue, controls, idle save, wake, event batching and subscribed screen capture | [Stage 3 results](stage3-results.md) |

CI exposed a pause race during task selection. Dispatch now rechecks holds when it claims work. A regression failed before the fix. Both CI runs passed on the corrected branch before merge.

The parent checked the UI in bare. The strict real-VM run used the real server and a scripted model. It completed browser, shell and file work, saved the idle computer and restored it for the next task. Deterministic tests cover stop, steer, holds and uncertain outcomes.

### Current resource baseline

These are single-run observations, not capacity estimates or before/after performance claims. CPU is a percentage of one core. The server memory is shared Deck overhead.

| State | VM memory | VM CPU | Server memory | Server CPU |
| --- | --- | --- | --- | --- |
| Working | 531 MiB | 5.57% | 20.9 MiB | 0.60% |
| Awake, idle | 534 MiB | 4.39% | 21.3 MiB | 0.07% |
| Saved | 0 | No process | 21.4 MiB | 0.00% |

No model calls or captures occurred in the idle and saved windows. Capture stopped after the viewer disconnected. A restored task completed in about 3 seconds. The workload was small and synthetic.

### In progress: benchmark one integrated bot

A release-binary lifecycle series completed four runs. Three form the baseline; one overlapped local tests and remains diagnostic evidence. Each round used a new private overlay. Timestamped API traces, one-second process counters and scenario results stay outside Git. See [the benchmark report](single-bot-benchmark.md). All lifecycle checks passed. No performance change or speedup is claimed. Sustained active-load tests remain pending.

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

## Evidence and attribution follow-up

The evidence-gap fixes have three passing regressions. All 65 Python tests pass. Repeated guest idle snapshots, a separate host-thread sample and a software CPU profile point toward VM event-loop overhead, but do not prove a specific timer or device cause. Closing the editor did not eliminate variation. Two marker-aligned synthetic tool tests passed with complete retained evidence. See [the investigation report](benchmark-investigation.md). No production tuning or capacity claim follows from these data.

## Saved-state cost pass

The runtime-only saved-state experiment completed three fresh boots and five cycles per boot. All 15 cycles passed desktop, browser, file and transfer checks. The parent reproduced the raw summary and all 87 Python tests. No sampler or retention error was recorded. The base digest stayed unchanged and no QEMU process remained. See [cycle-benchmark-results.md](cycle-benchmark-results.md).

No server-path speedup is claimed. Restores were warm in the host page cache. The sampler has material CPU overhead. Migration bandwidth and the post-agent restore gap are hypotheses for controlled follow-up. The next live experiment is sustained memory growth; idle attribution follows it.

## Memory pass: incomplete first run

The first memory experiment stopped on the existing host-wide swap guard after 1,551 of 1,800 planned workload seconds in boot 1. Boots 2 and 3 did not start. The raw summary correctly reports failure, with no sampler or retention error and an unchanged base. Parent checks reproduce all 126 Python tests. See [memory-benchmark-results.md](memory-benchmark-results.md).

This partial run shows stable bare process memory after warm-up, continued host VM/cache growth and reply-file growth. It does not establish a host-memory plateau or a leak. The Deck reply spool requires bounded-transport investigation, not an engine-leak claim. Save/restore after the memory workload remains untested. Run one short smoke before a fresh full attempt. Keep the original guard and incomplete evidence; do not relax the threshold to obtain a pass.

## Memory smoke: desktop comparison unresolved

Two fresh one-minute smoke attempts passed workload bounds but stopped at the pre-save terminal-pixel check. Refocusing did not resolve the mismatch. Save/restore after this workload is still unvalidated; the long experiment remains incomplete. All 128 Python tests pass in the implementation report. The next diagnostic must retain pre/post workload screenshots and compositor trees, determine whether layout, focus, cursor or content changed, and test a correction without dropping the volatile-state gate.

## Memory smoke: parent validation passed

Screenshots and compositor trees identified tiling geometry as the earlier pixel mismatch. The parent inspected those images, reproduced all 131 Python tests, then ran one fresh smoke with the fixed terminal. Workload bounds, save/restore, unsaved terminal pixels and process, browser session, file/download hashes, base integrity and cleanup all passed. No sampler, retention or stop error was recorded.

This closes the short smoke gap. It does not complete the three independent 30-minute memory runs. The fixed terminal changes browser geometry, so future memory curves need a separate baseline. The next action is one fresh full attempt under unchanged resource guards.

## Second full memory attempt and revised observation policy

The second full attempt stopped in boot 1 after 497.6 seconds on host-wide paging. It remains incomplete; boots 2 and 3 did not run. The parent verified its failed summary and retained evidence.

D52 versions future memory runs separately: host-wide paging is context, while VM, OOM, limit, pressure and low-memory guards remain safety stops. Paging windows are explicit and cannot support clean latency comparisons. All 132 Python tests pass. The next attempt uses this revised method, not a retroactive pass for earlier data.

## Memory v2 observation complete; planned repetition incomplete

The parent verified the failed raw summary: boots 1 and 2 completed 30-minute workloads and passed save/restore, desktop, session and file gates. Boot 3 stopped at 1,057.8 seconds on CPU pressure and did not reach save. The three-boot completion claim does not hold. No sampler or retention error was recorded; the base stayed unchanged and no QEMU remained.

Bare process memory showed no significant late trend in the two complete boots. Host VM memory and guest cache kept growing; no host plateau or leak cause was established. Paging context prevents clean latency comparisons. The report retains the failed run and layout-specific data. No further long memory rerun is scheduled on this host. The shorter idle attribution experiment is next; the missing third full repetition remains explicit.

## Idle attribution verified

The connected-executor Stage 3 experiment completed three boots and six valid 60-second idle windows. The parent reproduced all 168 Python tests and recalculated the window gates and host deltas from raw records. Model and capture counters stayed flat. No observer or retention error was recorded, the base stayed unchanged and no QEMU remained. See [idle-benchmark-results.md](idle-benchmark-results.md).

The executor timeout is a small switch-rate source, not an explanation for most host work. QEMU main-thread switches and guest local timers recur, but no causal timer/device change is justified. This closes the planned idle attribution pass with a negative tuning finding. Memory still lacks its third full repetition and a host plateau. The next bounded candidate is a controlled migration-bandwidth comparison, not generic idle tuning. No production performance gain is claimed.
