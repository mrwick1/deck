# Architecture

This file separates three states. Keep them apart.

- **Shipped:** what the code on this branch does now.
- **Measured:** prototype results. They show feasibility. They are not product behavior.
- **Target:** the approved design in D21–D46. Most of it is not built.

The decisions are in [decisions.md](decisions.md). The text of D21–D46 is pending merge into that file. The latest decision wins. Where D13, D14 or D23 conflicts with a later decision, the later decision applies.

## Shipped today

```
user (web app) ──HTTPS + SSE──> deck service (Rust, one process)
                                  │
                                  ├── agent loop ⇄ model endpoint (OpenAI-compatible /chat/completions)
                                  ├── browser tools ──> bare-mcp (stdio) ──> bare
                                  ├── send_file ──> workspace (shots/, downloads/)
                                  └── SQLite store (chats, messages, runs, events, files)
```

- The service is one binary. It serves the React web app that `web/` builds. See the README.
- The agent gets each `bare-mcp` tool as `browser_<name>`, except `run_steps`. It hides the arguments `window`, `secrets_file`, `file`, `path`, `downloads_dir` and `inline`. This follows D18. Source: `src/tools.rs`.
- Screenshots go to `shots/`. Downloads go to `downloads/`. Both folders are in the workspace. Uploads accept workspace-relative paths only. `browser_step` refuses `upload` lines. This follows D19.
- `[bare] secrets_file` names a file of `NAME=value` lines. deck passes it to `bare-mcp`. The model never sets it. The prompt lists secret names, never values. See the secrets entry in [decisions.md](decisions.md).
- The agent loop builds the prompt, calls the model, runs tool calls and streams each step. It repeats until the model gives a final answer or reaches `max_steps`.
- The system instructions name each tool and when to use it. Rakazo lacked this line. See [rakazo-lessons.md](rakazo-lessons.md).
- Model access uses an OpenAI-compatible endpoint only. The endpoint, model names and key are configuration. The key comes from the environment, never from a file.
- The design expects Authelia behind Caddy. deck trusts `Remote-User` only from `trusted_proxies`. Source: `src/auth.rs`.
- Cancel works with `POST /api/runs/{id}/cancel`. It returns `204`.
- Not shipped: a shell tool, a memory store, routines, event subscriptions, an approval flow, a live page view, and a per-bot model setting.

### Live events

The web app reads a chat's events from `GET /api/chats/{id}/events`. Each SSE event type is the event kind. Each stored event has an SSE `id`. A client reconnects with `after` set to the last `id` to replay what it missed.

Each event carries `run_id` and `at`. `at` is the time in epoch milliseconds. The web app uses it to show how long steps take.

The service sends these events today:

| Event | Data |
| --- | --- |
| `run_started` | `run_id` |
| `text_delta` | `text`: a part of the reply |
| `text` | `text`: the full reply |
| `tool_call` | `id`, `name`, `args` |
| `tool_result` | `id`, `ok`, `summary` |
| `file` | `file_id`, `name`, `mime` |
| `run_finished` | `status`, `error` |

The web app also has readers for events that the service does not send. Treat them as frontend placeholders:

| Event | Data | Status |
| --- | --- | --- |
| `reasoning_delta` | `text` | Placeholder. Not a product requirement (D37). |
| `reasoning` | `text` | Placeholder. Not a product requirement (D37). |
| `approval_request` | `id` (the tool call), `approval_id`, `reason` | Placeholder. No sender. |
| `approval_result` | `id` (the tool call), `approved` | Placeholder. No sender. |
| `page` | `url`, `title`, `file_id` (optional) | Placeholder. No sender. |

The web app calls `POST /api/approvals/{approval_id}` with `{"approved": true}` or `{"approved": false}`. The service has no route for it. The call is a placeholder.

## Measured evidence

This section records prototype results. It does not show that the target exists.

- **Stage 1 VM feasibility:** Five saved-state cycles ran on a copy of the disk. The unsaved editor buffer, the open bare page, a fixture login and files survived every cycle. Save and restore times stayed within the initial targets. A kill and cold boot kept files but lost the volatile editor and browser state. Login persistence after a crash is not proven. The experiment used a root guest agent. Stage 2 defines the product executor. Source: `docs/vm-stage1-results.md` on the implementation branch.
- **Stage 1 scope not tested:** authenticated executor readiness, cancellation, host action records, restore fencing, Deck UI, two-bot isolation, boot and idle memory samples, and end-to-end display latency.
- **Optimization harness:** The implementation branch contains shared base images with private overlays, guest service trimming, optional saved-state codecs and capture benchmarks. The codec experiment ran 20 successful restore cycles across four codecs. Capture suppression reduces transmitted data in the fixture. It does not reduce capture work.
- **Timing caveat:** Some optimization runs overlapped. Timing comparisons are provisional. No uncontended speedup is established. Source: `docs/optimization-plan.md` and `docs/vm-optimization-results.md` on the implementation branch.
- **Not integrated:** idle sleep, event batching and live-view capture subscriptions wait for the executor, the task queue and the UI. The saved-state codec default stays `none`.

## Target architecture (D21–D46)

### Bots and computers

- Each bot owns one persistent full-desktop virtual machine (D21). The guest OS is Arch Linux (D44).
- Each bot has its own accounts, files, memory, instructions and model configuration (D21).
- Deck creates and manages each computer and its lifecycle (D21, D35).
- Bots are independent workers. Bot-to-bot delegation is not part of this design (D21).
- The VM backend is not selected. QEMU/KVM is the first candidate, to be measured (D35, D44).
- Creating a bot through conversation is a later stage (D26).

### Host and guest split

- **Host (Deck):** the agent loop, model calls, tasks, memory, permissions, action records and VM lifecycle. Host state stays outside the VM (D35).
- **Guest:** the desktop, bare, a shell, files, applications, signed-in sessions and a small executor (D35).
- The executor runs commands as the guest desktop user. Privileged lifecycle commands use a separate path (Stage 2 plan).
- Target executor rules (Stage 2 plan, not built):
  - The protocol is versioned. A connection binds to one configured bot.
  - Each command carries a task ID, a command ID, a host-owned epoch and a deadline.
  - A repeated command with the same ID returns the known state. Reuse of an ID with other arguments is rejected.
  - Cancel returns a request. Termination needs its own confirmation.
  - Artifacts move by handle, with size and integrity checks. Paths stay inside the guest workspace and the host bot store.
- The host advances restore epochs. It rejects stale replies. A restored guest identifier does not prove a fresh identity (review recommendations).
- Guest transport authentication and connection recovery are open.

### Browser

- `bare` is the only browser (D2, D21). Do not add Chrome, Chromium, Playwright, Xvfb or VNC. Do not add them as fallbacks.
- The prototype runs bare inside the guest. The target connects its tools to the executor in a later step.
- The display path for the desktop view is not selected (D21). Verify it against the browser and display rules before choosing it.
- The relation between bare's captures and the desktop view is open (D36, review item 4).

### Actions and permissions

- A bot can use its desktop, bare, a shell and service APIs. Its permissions govern each route (D22).
- Permissions are trusted instructions. Deck does not guarantee that a denied action is technically unreachable (D31). This replaces the cross-tool enforcement guarantee in D23.
- The owner can change permissions at any time. The owner can grant one action or an ongoing permission with limits. Revocation blocks later actions, including in an active run (D23, D31).
- A bot can request a missing permission. The request goes to the Deck inbox (D23, D30).
- The domain list in D14 remains the stated approval backstop. Under D31 it is a check, not a guarantee.
- Approval requests appear in the Deck inbox. The phone push in D14 is deferred (D30).

### Events and schedules

- A bot starts work from user messages, schedules and configured event subscriptions (D24).
- Event sources differ by service. Verify each source before use. No service integration is selected (D24).
- **Pause automation** stops new work and automatic wake-ups. Incoming events stay queued (D25).
- **Sleep computer** saves the VM state, stops the VM and keeps its disk. Resume restores the saved state (D25).
- Sleeping leaves automation on. An event or schedule can wake the computer. A pause overrides automatic wake-ups (D25).
- A listener outside the VM may queue events and request wake-ups. This mechanism needs validation (D25).

### Budget holds

- Each bot has limits for CPU, RAM, disk and model usage. Deck has overall limits. Work queues when capacity is full (D34).
- When a bot reaches its spending budget, its work pauses. Deck notifies the owner in the inbox (D34).
- The bot resumes only after explicit owner approval. No approval means no continuation (D34).
- Events, schedules and incoming work do not bypass a budget hold (D34).
- Budget periods, usage accounting and the scope of a continuation approval are open (D34).

### Tasks and queue

- Each bot keeps a persistent task queue. One task owns the desktop at a time (D29).
- Related events can join a pending task. A task that waits for the owner stays visible (D29).
- States: Working, Waiting for you, Queued and Completed. A failed task keeps its failure details (D29).
- The owner can cancel tasks and change their priority (D29).
- The owner sets the priority of each message. Priority does not interrupt a running task (D29).
- Stop, steer and queue are separate controls (D37).
  - Stop ends the current action. It cannot undo an external action that was already submitted (D37).
  - Steer changes the current work. The prototype checks steering at the next model-call boundary. Steering does not change a command that is already running (vm-prototype).
  - Queue holds a message for later work. It keeps the priority that the owner chose (D37, D29).

### Uncertain outcomes

- The bot investigates a failure before it asks the owner. It retries only within its permissions (D32).
- A timeout or crash can leave a send or submission uncertain. The bot checks the service for evidence before it retries (D32).
- Do not repeat an uncertain external action until its outcome is resolved, or the owner directs it (D32).
- Deck stores task progress and action records outside the VM. Restoring the computer does not erase them (D32, D35).
- Retry limits, the evidence that resolves an outcome, and the action record format are open (D32).

### Sign-in and credentials

- The bot requests credentials through a protected input (D27). The mechanism is to be ported from T3 Code. Inspect the T3 Code implementation before the port (D27).
- Protected values bypass the model context, the chat history and ordinary tool logs. The model refers to a value without reading it (D27).
- The bot fills sign-in fields and completes the sign-in flow. The owner does not take over the desktop (D27).
- Screenshots and page reads during sign-in must not show protected values (D27).
- For MFA, OTP and similar checks, the bot names the next step. It asks the owner for the code or the phone action. It then continues and checks the result (D28).
- An unsupported challenge reports a clear blocked state (D28).
- Open: credential storage, secret lifetime, recovery for a failed sign-in, and session cookies inside the VM disk (D27, review item 2).

### Memory

- Each bot keeps its own instructions, preferences, learned facts and lessons between tasks (D33).
- The bot saves useful items automatically (D33).
- The owner can inspect, correct and delete a bot's memory (D33).
- Bots do not share memory automatically (D33).
- Learned items do not grant permissions or override explicit owner instructions (D33).
- Credentials stay in the protected secret store, not in memory (D33).
- Storage, retrieval, provenance, correction and the format of reusable procedures are open (D33).

### Models

- Agent runs use Sonnet. Routines, chat titles and summaries use Haiku. Both go through the OpenAI-compatible proxy (D11).
- The endpoint, model names and keys stay configurable (D11).
- Each bot has its own model configuration (D21).
- The product does not require the model's private reasoning. Show observable actions and concise explanations instead (D37).

### User interface

- The web app is a React SPA on shadcn/ui and assistant-ui (D20). It replaces the Svelte plan in D7.
- The home screen shows activity across all bots (D38). The current direction is the card layout with subtle animation (D40, D41).
- Respect reduced-motion preferences (D40).
- The layout details and status controls remain open (D38, D40). The interactive prototype `prototypes/bot-overview.html` compares the options (D39).
- The bot page shows chat by default. It shows the computer screen when the work needs it (D36).
- When the screen closes, and how it appears on a phone, is open (D36).
- The Deck inbox shows approvals, sign-in requests, failures and results across bots. A request stays until it is resolved, even after the owner dismisses its notification (D30).
- Phone notifications are deferred (D30).

## Data

- SQLite holds chats, runs, memory and routines (D12). The shipped store holds chats, messages, runs, events and files. Memory and routines are not shipped.
- Target host state lives outside the VM: tasks, action records, restore epochs, permissions and budgets (D32, D35).
- Files live in the bot's workspace folder.
- Credentials live in the protected store. They never appear in the database in plain text (D27, D33).

## Stage status

- **Stage 1:** VM feasibility reproduced. Only feasibility is accepted (see Measured evidence).
- **Stage 2:** approved and started. The executor and durable host control are in progress. Not verified. Do not treat Stage 2 as complete.
- **Later stages:** UI integration, real accounts, MFA, event integrations, conversational creation, and the home screen redesign.
- **Carryover:** idle sleep, event batching, live-view subscriptions, unchanged-frame suppression in Deck, and the saved-state codec default (D45, D46). Each item stays unchecked until its real caller exists.
