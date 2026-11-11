# Inspiration from other projects

On 2026-11-11 we read thirteen open-source projects. This file lists what `deck` takes from them and what it skips. Paths are relative to each project's repository, at the commit of that date.

License note: we copy ideas, not code, unless the license allows it. Suna uses the Elastic License 2.0. Skyvern uses the AGPL 3.0. Open WebUI uses a BSD-based license with a branding clause. Do not copy code from these three.

## Agent products

Projects: OpenClaw (MIT), Hermes Agent (MIT), Suna (Elastic 2.0), Agent Zero (MIT).

### Take

- **Frozen memory.** Load `MEMORY.md` into the prompt once, at the start of a session. Memory edits go to disk only. The prompt cache stays valid. Source: Hermes `tools/memory_tool.py`.
- **Output spill.** Write long tool output to a workspace file. Give the model a short excerpt and the path. Source: Hermes `tools/hook_output_spill.py`.
- **No self-approval.** The agent's own credentials can never answer an approval. Only the owner's session can. Source: Suna `apps/api/src/projects/lib/approval-authority.ts`.
- **Quiet routines.** A routine can run a script first. It calls the model only when the script reports a change. Source: Hermes `cron/scheduler_script.py`, OpenClaw `src/cron/active-jobs.ts`.
- **One run per routine.** A routine does not start while its last run is still active. Source: OpenClaw `src/cron/active-jobs.ts`.
- **Pause detection.** Detect a shell command that waits for input. Report it instead of waiting for the timeout. Source: Agent Zero `plugins/_code_execution/prompts/fw.code.pause_dialog.md`.

### Skip

- Vector memory (FAISS, embeddings). Plain Markdown is enough for one owner.
- Full desktops with VNC.
- Gateways for many chat channels.
- Multi-tenant accounts, billing and cloud sandbox pools.

## Agent loops

Projects: Goose (Rust, Apache 2.0), OpenHands (MIT), Codex CLI (Rust, Apache 2.0).

### Take

- **MCP client.** Use the official `rmcp` crate with a child-process transport for `bare-mcp`. Goose and Codex both use it. Source: Codex `codex-rs/rmcp-client/`.
- **Parent-death signal.** Set `PR_SET_PDEATHSIG` on each child process, so `bare-mcp` stops when `deck` stops. Source: Goose `crates/goose/src/subprocess.rs`.
- **Events.** Store each step as an immutable action or observation event in SQLite. Stream the same events to the web app over SSE. Source: OpenHands `openhands-sdk/openhands/sdk/event/`, Goose `crates/goose-agent/src/events.rs`.
- **Clean cancel.** When the owner stops a run, write an error result for each open tool call. The chat history stays valid for the model. Source: Goose `crates/goose-agent/src/machine.rs` (`interrupted_tool_responses`).
- **Context compaction.** Drop large middle tool outputs first. Then summarize into fixed fields: intent, files, errors and next steps. Keep the system prompt first. Source: Goose `crates/goose-context-management/src/structured.rs`, OpenHands `openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py`.
- **Sandbox mounts.** Mount system folders read-only and the workspace read-write with `bwrap`. Source: Codex `codex-rs/linux-sandbox/src/bwrap.rs`.
- **Parallel tools.** Run independent tool calls at the same time, with a cancellation token. Source: Codex `codex-rs/core/src/tools/parallel.rs`.

### Skip

- Docker sandboxes per session.
- Telemetry, voice and desktop pairing.
- seccomp and Landlock layers in v1. `bwrap` and resource limits come first.

## Browser agents

Projects: browser-use (MIT), Stagehand (MIT), Skyvern (AGPL 3.0), Nanobrowser (Apache 2.0). All of them drive Chrome. We take only the design of their page model and tools. `bare` stays the only browser (D2).

### Take

- **Snapshot as an outline.** One line per element: a ref, the role, the name, states and the value. Example: `[e3] button "Save" (disabled)`. Source: Nanobrowser `src/browser-control/core/perception/`, browser-use `browser_use/ax.py`.
- **Short refs with an epoch.** The browser mints refs such as `e1`. A ref from an old page fails clearly. Source: Nanobrowser `src/browser-control/core/refs.ts`.
- **Mark new elements.** Mark elements that are new since the last snapshot with `*`. Source: browser-use `browser_use/ax.py`.
- **Token budget.** Cut a long snapshot to 80 % from the top and 20 % from the end. Remove empty wrappers and repeated text. Source: browser-use `browser_use/ax.py`.
- **Scoped snapshots.** Take a snapshot of one part of the page by ref. Source: Stagehand `packages/extension/understudy/a11y/snapshot/capture.ts`.
- **Small focused tools.** `find`, `read` with pages of text, and `screenshot` of one element. Source: Nanobrowser `src/browser-control/core/tools/observe.ts`.
- **Action receipts.** Each action returns what changed: navigation, new elements and downloads. Source: Nanobrowser `src/browser-control/core/controller.ts`.
- **Typed errors with hints.** Each error has a code and a next step for the model. This also feeds the `bare` issue reports.

### Skip

- Chrome DevTools Protocol details, such as backend node IDs and frame ordinals.
- Browser extensions.

The `bare-mcp` tool design that follows from this is in [bare-gaps.md](bare-gaps.md).

## Chat UIs

Projects: Open WebUI (SvelteKit, BSD-based license with a branding clause), LibreChat (React, MIT).

### Take

- **Streaming.** Use `fetch` with a `ReadableStream` and an event-stream parser. Break large chunks into small pieces, so text appears smoothly. Source: Open WebUI `src/lib/apis/streaming/index.ts`.
- **Step timeline.** Show the steps of a run as a vertical rail. The active step animates. The rail collapses when the run ends. Source: Open WebUI `src/lib/components/chat/Messages/ResponseMessage/StatusHistory.svelte`.
- **Images under tool calls.** Show a screenshot directly under the step that took it. Source: Open WebUI `src/lib/components/common/ToolCallDisplay.svelte`.
- **Approval card.** Approve, deny, or edit the arguments inline. Keep the approval state outside the message component. Source: LibreChat `client/src/components/Chat/Messages/Content/ToolApproval.tsx`.
- **PWA.** Use `vite-plugin-pwa` with `registerType: 'autoUpdate'`. The app installs on a phone. Source: LibreChat `client/vite.config.ts`.

### Gap

Neither project sends a push notification when the tab is closed. Both use in-tab notifications only. `deck` needs Web Push: a service worker, `PushManager.subscribe()` and VAPID keys in the Rust service.
