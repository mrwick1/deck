# Decisions

Each decision has a date, the decision and the reason.

## D1. Build our own agent instead of using Rakazo (2026-11-11)

**Decision:** Build `deck`. Do not run Rakazo.

**Reason:** We installed Rakazo v0.9.0 and tested it. It worked and it was fast. But the experience was not smooth. The bot said that it could not send a screenshot, and that was false: it had the `attach_file` tool. Rakazo is also large: about ten services, and a full desktop container for each workspace. We want a small agent that we control and can fix. Lessons: [rakazo-lessons.md](rakazo-lessons.md).

## D2. bare is the only browser (2026-11-11)

**Decision:** `deck` uses only the `bare` browser. There is no Chrome, Chromium, Playwright, Xvfb desktop or other browser. There is no fallback.

**Reason:** `deck` must push `bare` to improve fast. Each real site that fails in `bare` becomes a `bare` issue, and agents fix it in the background. A fallback browser would hide these failures and stop the loop. See [feedback-loop.md](feedback-loop.md).

**Consequence:** Some sites will fail at first. `deck` must report each failure clearly and file the issue. It must not pretend that the task worked.

## D3. The client is a web app (2026-11-11)

**Decision:** The user talks to `deck` in a web app.

**Reason:** A web app can show a live view of the work, file previews and approval cards. A chat app such as Telegram cannot show these well. The web app must work on a phone, so a native app is not needed.

## D4. Screenshots always reach the chat (2026-11-11)

**Decision:** When the user asks for a screenshot, `deck` takes a `bare` capture and sends the PNG into the chat. The system instructions and a test cover this.

**Reason:** This was the main failure in Rakazo.

## D5. deck is a standalone app (2026-11-11)

**Decision:** `deck` is its own service with its own web app. It is not a toolkit inside a coding harness.

**Reason:** The vision needs routines, approval cards, a live view and files in the chat. A coding harness does not give that experience.

## D6. The service is in Rust (2026-11-11)

**Decision:** The service uses Rust with axum and tokio. It builds to one binary.

**Reason:** `bare` is a Rust workspace. One language makes fixes in both repositories easier. Later, `deck` can call `bare` crates in the same process instead of through MCP.

## D7. The web app is a Svelte SPA (2026-11-11, replaced by D20)

**Decision:** The web app uses Svelte and Vite. The service serves the built static files.

**Reason:** The bundle is small and fast on a phone. Svelte makes streaming chat simple. No Node server runs in production.

## D8. deck runs on the owner's main machine (2026-11-11)

**Decision:** `deck` runs as a systemd user service on the owner's always-on main machine.

**Reason:** `bare` and the model proxy already run there. `deck` is one small binary plus a headless `bare`, so it does not compete with other work. It can move to a server later.

## D9. deck is public, behind Caddy and Authelia (2026-11-11)

**Decision:** The owner's Caddy server publishes the web app. Caddy proxies to `deck` over the private network. Authelia, the owner's single sign-on, gates every request with a forward-auth check.

**Reason:** The owner wants to reach `deck` from any device and network. Caddy and Authelia already publish and protect the owner's other apps.

**Consequence:** `deck` holds site logins and runs a shell. Security is a first-class requirement. Page content is untrusted input to the model.

## D10. Authelia is the only login (2026-11-11)

**Decision:** `deck` has no login code. It uses the same Authelia login as the owner's other apps: a username and a password. `deck` trusts the `Remote-User` header only on requests from the Caddy server. It binds only to the private network address, never to a public one.

**Reason:** One login for all apps. No auth code in `deck` to build or get wrong.

**Risk:** A password is weaker than a passkey for an agent with a shell and site logins. Authelia can add a WebAuthn second factor for the `deck` host later, with no change in `deck`.

## D11. Models: Sonnet and Haiku (2026-11-11)

**Decision:** Agent runs use Sonnet. Routines, chat titles and summaries use Haiku. Both go through the owner's OpenAI-compatible proxy.

**Reason:** Sonnet is fast and good with tools. Haiku is enough for small jobs and costs less. The endpoint and the model names are configuration.

## D12. The database is SQLite (2026-11-11)

**Decision:** `deck` stores chats, runs, memory and routines in one SQLite file.

**Reason:** One owner and one process need no database server. A backup is a file copy.

## D13. The shell runs in bubblewrap from v1 (2026-11-11)

**Decision:** The bot has a shell tool in v1. Each command runs in a `bwrap` sandbox. The sandbox mounts `/usr`, `/etc` and `/opt` read-only and the bot's workspace folder read-write. It does not mount `/home`, `/data` or other user data, so keys, vaults and repositories stay hidden.

**Reason:** The owner wants a shell from the start. `bwrap` is lighter and faster than a container. Installed tools need the system folders. The mount layout follows Codex CLI (see [inspiration.md](inspiration.md)), without the user folders.

**Network:** The sandbox has internet access. The bot needs it to install tools and fetch data.

**Private network:** The sandbox can reach the private network too. The owner chose this on 2026-11-11 for simplicity.

**Risk:** Page content can carry a prompt injection. An injected command can then reach every private service that the host can reach. A network namespace that drops private ranges can close this gap later.

**Limits:** Each command runs in a `systemd-run` scope with `MemoryMax=2G`, `CPUQuota=200%` and `TasksMax=256`. A command stops after 10 minutes. The workspace has a 5 GB cap. All limits are configuration.

## D14. Approvals: model flag plus domain rules (2026-11-11)

**Decision:** The model marks a step as `needs_approval` when it sends, buys, posts, deletes or submits a form on a logged-in site. A list of domains in the configuration always needs approval. The run pauses and shows an approval card. A push notification reaches the owner's phone. Reading and navigation never ask.

**Reason:** No rule can tell a safe click from a risky one on every site. The model knows the intent. The domain list is a hard backstop.

## D15. The bare loop: a scheduled fixer and a pinned bare (2026-11-11)

**Decision:** `deck` files each `bare` failure as an issue on `mrwick1/bare` with the label `bare-mcp`. A scheduled agent task runs once a day on the owner's machine. It picks up to three open labelled issues and starts one agent for each, in its own worktree. Each agent opens a fix pull request. The owner reviews each pull request. Nothing merges on its own.

**Order:** Issues that block a `deck` task come first, then the oldest. The task skips an issue that has an open pull request or the label `in-progress`. It adds `in-progress` when it starts an issue. `deck` pins a `bare` release. The owner chooses when to update it.

**Reason:** Fixes arrive without manual work. A small daily batch keeps the cost and the review load low. A pinned `bare` keeps `deck` stable.

## D16. M1 starts in parallel with the bare gaps (2026-11-11)

**Decision:** Work on `deck` M1 starts now, against the current `bare-mcp`. The `bare` gaps (page snapshot and screenshot as a tool result) are built at the same time.

**M1 scope:** the Rust service, the Authelia header check, streaming chat, the agent loop with `bare`, the step log, and a screenshot sent to the chat with a test.

**Reason:** The service, the login and the web app do not depend on the new `bare` tools.

## D17. License: MIT or Apache 2.0 (2026-11-11)

**Decision:** `deck` uses the license "MIT OR Apache-2.0".

**Reason:** `bare` uses the same license.

## D18. The agent gets every bare-mcp tool (2026-11-12)

**Decision:** `deck` reads the tool list of `bare-mcp` at the start of a run. It offers each tool to the model as `browser_<name>`. It does not offer `run_steps`. It removes the arguments `window`, `secrets_file`, `file`, `path`, `downloads_dir` and `inline`. A screenshot always goes to `shots/` in the workspace.

**Reason:** A new `bare` tool reaches the model with no change in `deck`. The model reads pages as an outline with refs, so it needs fewer screenshots. The model cannot write files outside the workspace.

## D19. deck controls the files of the browser tools (2026-11-12)

**Decision:** `deck` sets the folder of `browser_open` downloads to `downloads/` in the workspace. An upload may send only files of the workspace. A `browser_step` line with `upload` is refused.

**Reason:** `send_file` can send a downloaded file. A page must never get a file from outside the workspace.

## D20. The web app is a React SPA on shadcn/ui and assistant-ui (2026-11-12)

**Decision:** The web app uses React, Vite, Tailwind CSS and shadcn/ui. The chat uses assistant-ui components. Motion, Vaul and Sonner give animations, a swipe drawer and toasts. The app is phone first and works on a desktop too. It is still a static SPA that the service serves. This replaces D7.

**Reason:** The owner wants the app to feel native and fluid. React has the largest set of ready-made components and animation libraries. assistant-ui already has streaming text, tool-call cards, reasoning blocks, a thread list and auto-scroll. Its external-store runtime takes the deck event stream with no change to the service.

**Look:** The colors, type, work-log rows, composer panels and image zoom follow T3 Code. T3 Code uses the MIT license. Ported files name it in their header, and `web/THIRD_PARTY.md` holds its license.

**Cost:** The script bundle grows from about 30 kB to about 370 kB gzip.

**Blocker:** `@assistant-ui/react` needs `TransformStream`, which `bare` does not have yet ([bare#389](https://github.com/mrwick1/bare/issues/389)). Until then, the app does not render in `bare`, and the screenshot end-to-end test fails.

## D21. The owner's secrets come from a file in the config (2026-11-12)

**Decision:** `[bare] secrets_file` names a file of `NAME=value` lines. deck passes it to `bare-mcp` on `open` and `ensure_session`. The model never sets it. The system prompt lists the names of the secrets, never the values. deck reads the names at the start of each run, so an edit to the file needs no restart.

**Reason:** The prompt told the model to type `${NAME}`, but deck removed `secrets_file` from every call, so no value ever reached `bare`. `bare` puts the value in the page and shows `[secret]` in every reply. The value then never enters the model context, the chat store or the log.

## D22. A fixed task set measures each change (2026-11-12)

**Decision:** `evals/` holds a fixed set of tasks for the agent. Nine tasks run on a local fixture site, and eight tasks read public sites. `evals/run.py` starts deck with the real model and the real `bare-mcp`. It scores each task by a regex on the answer, by the tools that the agent used, and by what the fixture site received. It writes one result file per run and compares two runs. The scripts use only the Python standard library.

**Reason:** A change to `bare` or to the prompt can help one site and break another. One manual check does not show that. A fixed set with the real model shows the change in passes, time and tool calls. The fixture tasks give the same result each run. The public tasks catch the problems of real sites. Their checks match the shape of an answer, so they survive data drift.

## D23. Each bot owns a persistent desktop computer (2026-11-12)

**Decision:** The owner can create independent bots for distinct purposes. Examples include email, job hunting and social media. Each bot owns a persistent virtual machine with a full desktop. Each bot has its own accounts, files, memory, instructions and model configuration. Deck manages the computers and their lifecycle.

**UI:** Each bot has a dedicated view with chat, desktop display, screenshots, activity and controls. Human control of the desktop can wait. The architecture must support it later.

**Reason:** Each bot needs its own working environment and account boundary. Bots are independent workers. Delegation between bots is not the basis of this design.

**Scope change:** Full desktops replace the desktop exclusion in the original vision. `bare` remains the only browser. The VM backend and desktop transport remain open. This decision does not select Xvfb or VNC.

## D24. Bots can use desktop tools, shell commands and APIs (2026-11-12)

**Decision:** A bot can operate its desktop, use `bare`, run shell commands and call service APIs. Its permissions govern each route.

**Reason:** A bot must use the tools that fit its work. Desktop interaction alone is not required.

**Scope change:** D13 described a host workspace shell sandbox. The new design places the bot's computer behind a VM boundary. The shell sandbox policy inside the VM remains open.

## D25. Permissions are dynamic and belong to each bot (2026-11-12)

**Decision:** The owner can change each bot's permissions at any time. Permissions describe actions, such as reading messages, sending messages or submitting job applications automatically. A bot can request a missing permission. The owner can approve one action or grant ongoing permission with limits. Revocation blocks future actions during an active run too.

**Enforcement:** Deck must enforce permissions across desktop, browser, shell and API tools. A denied action must not become available through another tool.

**Reason:** Different bots need different levels of autonomy. Permissions must change as the owner changes the bot's duties.

**Scope change:** This extends D14. The enforcement mechanism remains open. A model flag alone does not establish enforcement across all tools.

## D26. Bots can react to configured events (2026-11-12)

**Decision:** Bots can start work from user messages, schedules and configured event subscriptions. Monitoring depends on the bot's duties and the owner's instructions. Examples include incoming email and job alerts.

**Reason:** Bots must do ongoing work without a new user message for each task.

**Open:** Verify the event sources available for each service. APIs, notifications and browser polling are possible approaches. No specific service integration is selected yet.

## D27. Pause automation and sleep computer are separate controls (2026-11-12)

**Decision:** Pause automation stops new work and automatic wake-ups. Incoming events remain queued. Sleep computer saves the VM state to disk and stops the VM to release CPU and RAM. Its persistent disk remains stored. Resume restores the saved computer state.

**Wake policy:** Sleeping leaves automation enabled. An event or schedule can wake the computer. Pausing automation overrides automatic wake-ups.

**Reason:** The owner needs to save resources without losing the bot's environment. The owner also needs a separate control that prevents automatic work.

**Proposed mechanism:** A lightweight event listener outside the VM queues events and requests wake-ups. A source that needs a running desktop may require periodic wakes or an awake computer. This mechanism needs validation.

**Limits:** Saved state consumes disk space. External connections and expired logins may need recovery. Save, restore and crash recovery need a prototype. Stop behavior for an action already in progress remains open.

## Discussion tracking

Append agreed decisions here as the architecture discussion continues. Keep proposals and open implementation questions separate from agreed behavior. D23–D27 describe the target architecture. They do not claim that these features exist in the current code.

## D28. Create bots through a conversation (2026-11-12)

**Decision:** The owner describes a bot's purpose in plain language. Deck prepares a setup for review. The setup includes the name, instructions, accounts to connect, permissions, approval rules, event subscriptions, schedules, model and resource limits.

**Flow:** The owner approves the setup. Deck then creates the bot's computer and helps connect the accounts. The owner can change the configuration later.

**Reason:** A conversation is enough for the first setup flow. Templates can come later.

## D29. The bot signs in through secure credential prompts (2026-11-12)

**Decision:** Port the secure credential input mechanism from T3 Code. The bot requests the email address and password through that mechanism. It then fills the sign-in fields securely and completes the sign-in flow. The owner does not take over the desktop.

**Security boundary:** Credential values must bypass model context, chat history and ordinary tool logs. The model can refer to protected values without reading them. Field entry must not return the values to the model. Screenshots and page reads during sign-in must not expose protected values.

**Reason:** The bot must complete account setup without general desktop takeover or passwords in chat.

**Open:** Inspect the T3 Code implementation before selecting the port. Define credential storage, secret lifetime and recovery for failed sign-ins. Define handling for MFA, passkeys and other verification challenges. These details are not settled by this decision.

## D30. The bot coordinates service-specific sign-in verification (2026-11-12)

**Decision:** The bot handles MFA, OTP, 2FA and similar verification flows for each service. It identifies the next step, asks the owner for the required input or action, and continues the sign-in flow. General desktop takeover is not required.

**UI:** Build service-specific verification UI as each integration is added. Use protected input for verification codes and other secrets. For phone approval or number matching, show the service's instructions and the required number. Ask the owner to complete the phone action. The bot handles the website steps and checks whether sign-in succeeded.

**Reason:** Verification differs between services. The bot must coordinate each flow while the owner supplies the required proof.

**Scope:** This settles the verification interaction left open in D29. It does not assume that every challenge can be automated. Unsupported challenges must report a clear blocked state. Credential storage and secret lifetime remain open.

## D31. Bots keep a persistent task queue (2026-11-12)

**Decision:** Each bot keeps a persistent task queue. One task controls its desktop at a time. Related events can join a pending task, such as several emails in one summary. A task waiting for owner input stays visible. Unrelated work can continue only if it does not disturb the waiting task's session.

**UI:** Show Working, Waiting for you, Queued and Completed states. Provide controls to cancel tasks and change their priority. Preserve failure details for tasks that fail.

**Message priority:** The owner chooses priority when sending a message. A message can take priority over queued background work. The default priority and exact controls remain open. Queue priority does not imply interruption of a task already running.

**Reason:** Bots must handle ongoing events and direct requests without competing for desktop control. The owner must control which queued work comes first.

**Open:** Define safe handling of concurrent API work, task dependencies and interruption of running work.

## D32. Start with an inbox inside Deck (2026-11-12)

**Decision:** Provide one inbox inside Deck across all bots. It contains approvals, sign-in requests, failures and results. Each item opens the relevant bot and task. Requests remain until resolved, even when the owner dismisses a notification.

**Settings:** Each bot has notification settings. Support immediate in-app alerts for requests that need attention, scheduled summaries for routine results and silent progress updates.

**Scope:** Start with notifications inside Deck. Defer phone notifications. This replaces the phone push requirement in D14 for the initial implementation.

**Reason:** The owner chose to keep notifications inside Deck for now.

## Architecture review: open questions (2026-11-12)

These findings are design concerns and recommendations. They are not new agreed decisions or verified implementation failures.

1. **Permission enforcement:** D24 permits raw desktop, browser and shell access. D25 requires action permissions across every route. Raw access to a signed-in account can bypass a typed action gate. Define which guarantees Deck can enforce and which actions require trust in the bot. Consider host-controlled typed actions with credentials outside the VM.
2. **Secret ownership:** Secure entry does not protect session cookies from a shell in the same VM. VM disks and saved memory can contain credentials. Define storage protection, credential access and token refresh. Event listeners outside sleeping VMs also need an explicit credential boundary.
3. **Recovery:** Keep external action records outside VM saved state. Record intent and outcome. A crash can leave an action's outcome unknown. Reconcile uncertain outcomes before retrying. A normal save and resume does not inherently replay an action; rollback or retry can create duplicates.
4. **Desktop display:** Define how bare's browser output appears in the bot's desktop view. Preserve the only-browser rule. Do not assume desktop applications with embedded browsers are permitted.
5. **Task ownership:** Define whether waiting tasks retain exclusive access to their browser sessions. Do not assume unrelated tasks can safely share a profile.
6. **VM feasibility:** Prototype one VM before selecting a backend. Measure memory, save and restore time, session recovery and desktop capture. Define resource limits and network access.
7. **Documentation:** Reconcile the original vision and architecture with D23–D32 before implementation. The older documents still describe the earlier scope.

**Recommended next discussion:** Set the trust boundary first. Then define external action recovery. Next select and prototype the VM runtime. Keep these recommendations separate from the agreed product requirements until the owner decides.

## D33. Action permissions are trusted instructions (2026-11-12)

**Decision:** Action permissions are instructions that the owner trusts the bot to follow. The bot must follow the current permissions, request approval when required and respect revocation during a run.

**Boundary:** Deck does not guarantee that a denied action is technically unreachable through desktop, browser, shell or API access. A bot with raw access to a signed-in account may have the technical ability to perform that action. The owner accepts this trust model.

**Scope change:** This replaces the cross-tool enforcement guarantee in D25. Dynamic permissions, one-time approvals and ongoing grants remain part of the design. VM isolation between bots and protected credential input remain separate requirements.

**Reason:** The owner chose trusted instructions instead of hard technical barriers for action permissions.

## D34. Investigate uncertain outcomes before asking the owner (2026-11-12)

**Decision:** The bot investigates failures and attempts recovery within its permissions before asking the owner. If it cannot establish an action's outcome, it asks through the Deck inbox.

**Recovery rules:** Retry actions that are safe to repeat, with a retry limit. If the service clearly rejects an action, correct the problem and retry within the bot's permissions. If a timeout or crash leaves a send or submission uncertain, check the service for evidence before retrying. Do not repeat an uncertain external action without resolving its outcome or obtaining the owner's direction.

**Persistence:** Deck stores task progress and action records outside the bot's VM. Restoring the computer must not erase Deck's record of attempted actions or known results.

**Reason:** The bot should resolve recoverable problems itself. It must avoid duplicate messages, applications and other external actions.

**Open:** Define retry limits, evidence required to resolve an uncertain outcome and the action record format.

## D35. Bots save useful facts and lessons automatically (2026-11-12)

**Decision:** Each bot keeps its own persistent instructions, preferences and learned facts between tasks. It saves useful facts automatically. It also records lessons from completed work and corrections to improve later work.

**Owner control:** The owner can inspect, correct and delete the bot's memory. Bots do not share memory automatically. Credentials remain in the protected secret store.

**Boundary:** Learned facts and lessons do not grant permissions or override explicit owner instructions. New permissions still follow the approval model.

**Reason:** The owner wants each bot to improve through use without requesting approval for each memory entry.

**Open:** Define memory storage, retrieval, provenance and correction handling. Decide whether reusable procedures need a separate skill format.

## D36. Limit resources and pause at the spending budget (2026-11-12)

**Decision:** Configure CPU, RAM, disk and model usage limits per bot. Set overall resource limits for Deck. Queue work when capacity is full. Idle computers can sleep automatically.

**Spending:** When a bot reaches its spending budget, pause its work and notify the owner inside Deck. Resume only after explicit owner approval. No approval means no continuation. Events, schedules and incoming work must not bypass the budget pause.

**Reason:** The owner must control resource use and spending. Reaching a budget must not lead to automatic extra spending.

**Open:** Define budget periods, usage accounting, in-flight request handling and the scope of a continuation approval. Define resource defaults and idle sleep timing.

## D37. Deck runs the agent loop outside the bot's VM (2026-11-12)

**Decision:** Deck runs the agent loop and calls the model. It stores tasks, memory, permissions and action records outside the bot's VM. A small executor inside each VM operates the desktop, bare, shell and local files. The VM keeps its applications and signed-in sessions.

**Reason:** Deck must retain task history and manage the computer while the VM sleeps or recovers from a failure.

**Open:** Define the executor protocol, authentication and connection recovery. Select a VM backend after a prototype checks desktop capture, sleep, restore and resource use. The owner has approved the runtime split. This decision does not start the prototype.

## D38. Show chat by default and the computer screen when needed (2026-11-12)

**Decision:** The bot's main view shows chat by default. Do not keep the desktop visible during ordinary conversation. Show the computer screen when the agent needs it for browsing or other desktop work.

**Reason:** The owner wants a focused chat experience. The computer view should appear when it supports the current work.

**Scope:** This changes presentation only. Each bot still owns a full desktop VM under D23. Human takeover remains deferred.

**Open:** Define when the screen closes and how it appears on a phone. Define how bare's browser output reaches this view.

## D39. Show work status and support stop, steering and queued messages (2026-11-12)

**Decision:** Show what the bot is doing, how it is doing the work and its current status. Show concrete activity such as searches, browser steps and tool results as work proceeds. Present observable actions and concise explanations, not private model reasoning.

**Controls:** The owner can stop the bot's action and steer its current work. The owner can also queue messages for the bot to pick up after it finishes its task. Make steering current work and queuing later work distinct choices. Queued work keeps the owner-selected priority from D31.

**Reason:** The owner needs visibility and control during execution, without forcing every message to interrupt the current task.

**Limits:** Stop cannot undo an external action already submitted. Resolve uncertain outcomes under D34.

**Open:** Define stop acknowledgements, steering delivery points and the exact activity and message controls.

## D40. The home screen shows activity across all bots (2026-11-12)

**Decision:** When the owner opens Deck, show what all bots are doing. The home screen is an activity overview across bots. Opening a bot leads to its chat-first view under D38.

**Reason:** The owner wants to see ongoing work across bots before entering a conversation.

**Open:** Choose the overview layout, activity detail and status controls through UI discussion.

## D41. Compare interactive UI prototypes before choosing a layout (2026-11-12)

**Decision:** Build interactive prototypes and evaluate how they feel before selecting the UI layout. Compare alternatives with the same sample bots and work.

**Reason:** The owner wants to assess ease of use through interaction.

**Prototype:** `prototypes/bot-overview.html` compares cards, an activity feed and a split overview. It uses sample state only. No layout is selected by this prototype.

## D42. Use the card layout with a more active bot presentation (2026-11-12)

**Decision:** Use the card layout for the bot overview for now. Redesign its presentation so it feels like bots at work rather than generic cards. Use subtle animation and activity details to convey state.

**Reason:** The owner prefers the card layout after comparing prototypes. The visual design still needs refinement.

**Prototype direction:** Reduce box styling. Give each bot a small animated presence. Keep status and current activity readable without motion. Respect reduced-motion preferences. These visual treatments remain experiments.

## D43. Keep the current overview prototype as the starting UI (2026-11-12)

**Decision:** Keep the current bot overview prototype as the starting UI. Defer further visual redesign.

**Reason:** The owner accepts the current direction for now. This does not finalize the visual design.

## Two-round implementation review: recommendations (2026-11-12)

These are review recommendations. They do not authorize implementation or change the agreed product requirements.

### Common ground

- Prove one configuration-defined full desktop VM before building conversational provisioning. Test bare output, desktop capture and input, artifact delivery, saved-state resume and crash recovery.
- Consider direct QEMU/KVM first. Compare libvirt if lifecycle problems justify it. Backend suitability and performance remain unverified. Do not build two interchangeable backends before a concrete need exists.
- Keep the host agent loop and guest executor split from D37. Keep bare MCP inside the guest. Transfer screenshots and downloads explicitly into host artifact storage. A remote MCP relay alone does not resolve guest file paths.
- Keep VM lifecycle, automation pause, task state and budget hold separate. Neither events nor schedules may bypass a hold.
- Store tasks, action intents, results and restore epochs outside the VM. Reconcile in-flight work after a host restart. Preserve late replies and known results.
- Let the host advance the restore epoch. Reject stale responses and connections. Do not trust a restored guest identifier as fresh execution identity.
- Distinguish confirmed success, confirmed failure and unknown outcomes. Normal restore does not inherently repeat actions. Re-dispatch and rollback can. Investigate unknown external effects under D34 before retrying.
- A cancellation request is not proof of termination. Obtain an acknowledgement or retain an unresolved outcome. Do not discard useful late results.
- Test two independent bots and disks before claiming multi-bot support. Serialized VM execution is sufficient initially. Waiting tasks retain desktop ownership at first.
- Keep the initial inbox focused on budget holds and unresolved recovery. Defer service integrations, full notification polish and automatic memory retrieval from the runtime prototype. These features remain product requirements.

### Proposed sequence

1. Agree resource and latency targets. Prototype one full desktop VM and measure the targets.
2. Add durable bot, task, action and lifecycle state. Define the guest protocol and host-owned epochs. Add artifact transfer, status lookup, cancellation and reconnect handling.
3. Move one configuration-defined bot onto the executor. Show activity and support stop and steering.
4. Demonstrate two-bot isolation, durable queues, minimal inbox, pause behavior and budget approval holds.
5. Add conversational creation. Add account setup and event integrations in later slices.

### Validation gates

- Measure idle RAM, disk growth, capture latency and save/restore times. Confirm resource release while saved.
- Show what bare sees during browser work. Define the relation between browser captures and the actual desktop view. Do not substitute a browser-only environment for the required full desktop.
- Inject failures before dispatch, during execution and after completion but before receipt. No unknown action may be silently retried.
- Reject stale-epoch replies. Retain completed action evidence after restore.
- Demonstrate isolated files, sockets and artifacts for two bots.
- Confirm pause and budget holds suppress automatic wakes and model calls. Resume a budget-held bot only after explicit owner approval.
- Verify the proposed display path against the existing bans on Xvfb and VNC. A full desktop does not inherently select either. No policy exception is approved here.

### Remaining choices and limits

The backend, guest transport authentication, display path, numerical targets and exact milestone boundaries remain open. Verify current main before implementation; this review inspected an older worktree. No VM, backend, display or recovery experiment ran during the review.

## D44. Define pass criteria before the VM prototype (2026-11-12)

**Decision:** Use `vm-prototype.md` as the acceptance checklist for the first desktop VM prototype. Prove one configuration-defined computer before building the full provisioning flow.

**Reason:** The owner requested concrete acceptance criteria after the two-round implementation review.

**Status:** The checklist separates correctness gates from provisional performance targets. The numerical targets are proposals for the experiment, not measured results or final product limits. No prototype has run under this decision.

## D45. Approve the first VM prototype checklist (2026-11-12)

**Decision:** The owner approves the checklist and initial experiment targets in `vm-prototype.md`. Use them to evaluate the first VM prototype. The targets remain experiment criteria, not final product limits.

**Reason:** The owner reviewed and accepted the proposed criteria.

**Status:** No prototype results exist yet. Checklist approval does not authorize deployment.

## D46. Use Arch Linux for bot computers (2026-11-12)

**Decision:** Use Arch Linux as the guest OS for bot VMs. Do not use the Omarchy distribution for the current prototype. Build a minimal full desktop image with only the packages required for the prototype. Keep bare as the only browser.

**Reason:** The owner chose to continue with Arch after discussing Omarchy. The image must remain reproducible and meet the approved prototype criteria.

**Status:** The compositor and exact guest package set remain implementation choices. No performance comparison with Omarchy has run.

## Stage 1 verification result (2026-11-12)

This is measured evidence, not a new product decision. A separate implementation worktree contains the desktop VM harness and `docs/vm-stage1-results.md`. The parent reproduced five saved-state cycles on a copy of the disk. The unsaved editor buffer, open bare page, fixture login and files survived every cycle. Measured save and restore times remained within the approved initial targets. No prototype VM or fixture remained running after verification.

A kill and cold boot preserved files but lost the tested volatile editor and browser state. The prototype does not prove account login persistence after a crash. The guest control path uses a root guest agent for this experiment. Stage 2 must define the product executor boundary.

The parent added failing regression tests, then fixed duplicate-start handling and result validation. The parent also added failure cleanup and a portable runtime default. Nine unit tests, compilation and syntax checks pass.

Only Stage 1 feasibility is accepted by this verification. The complete prototype checklist remains open. Authenticated executor readiness, cancellation, host action records, restore fencing, Deck UI integration and two-bot isolation have not been tested. Boot and idle memory need more samples. End-to-end display latency remains untested.

## D47. Implement and measure five resource optimizations (2026-11-12)

**Decision:** Implement idle sleep with event batching, shared clean base images with private overlays, demand-driven screen capture, measured guest service trimming and saved-state compression evaluation. Follow `optimization-plan.md` in dependency order.

**Reason:** The owner approved all five optimization areas after reviewing Stage 1 resource usage.

**Boundary:** Preserve VM isolation, saved-state correctness, pause and budget holds. Keep baseline behavior when a proposed tuning does not improve measured results. A benchmark is not evidence of production integration.

## Optimization harness review (2026-11-12)

The implementation branch contains shared base images and private overlays, guest service trimming, optional saved-state codecs and capture benchmarks. The parent reviewed the diff and recorded experiment evidence. The parent did not repeat the live optimization matrix.

The recorded compression experiment has twenty successful restore cycles across four codecs. Overlay evidence records an unchanged base digest and separate private artifacts. Capture suppression reduces transmitted data in the fixture; it does not eliminate capture work. Compression remains optional with the uncompressed default unchanged.

Parent review fixed multi-base storage accounting, missing-codec preflight, invalid-codec state preservation and unbounded state polling. Twenty-nine unit tests and syntax checks pass. The new failure paths use mocked tests, not live VM experiments.

Idle sleep and event batching still require the executor and durable queue. UI capture subscriptions still require Deck integration. These approved optimizations are not complete. The results report is `docs/vm-optimization-results.md` in the implementation branch.

## Optimization coordination correction (2026-11-12)

The parent started a replacement task before the original task's background work and continuation fully settled. Both used the implementation worktree. This caused overlapping measurements and conflicting ownership reports. Both tasks have now ended. The parent verified a clean worktree and no running prototype VM, fixture or benchmark driver before recording the correction.

Performance comparisons remain provisional because sessions overlapped or competed for a fixture port. Keep datasets separate. Functional marker and checksum evidence remains useful, but no uncontended speedup is established. Keep compression optional and its default unchanged. The implementation report records commit ownership and dataset provenance. Do not run another benchmark matrix merely to close this reporting gap.

## D48. Start the executor stage and retain unfinished optimizations (2026-11-12)

**Decision:** Start Stage 2 under `stage2-plan.md`. Keep unfinished optimization integrations on the explicit carryover checklist in `optimization-plan.md`.

**Reason:** The owner authorized Stage 2 and asked to retain the remaining work for later.

**Boundary:** Use one implementation writer. Complete and clean up owned work before parent review. Do not repeat the optimization matrix as part of the executor stage.


## Documentation reconciliation (2026-11-12)

Main already assigned D21 to secret-file configuration and D22 to the fixed evaluation task set. Preserve those published IDs. Renumber this discussion's draft bot decisions from 21–46 to 23–48. Update target-design references in the architecture, vision and open-questions documents. This changes identifiers only, not the agreed behavior.

Keep the proposed domain approval list open in the new per-bot flow. The approved permission model uses trusted instructions. Do not claim that domain rules establish hard cross-tool enforcement.


## Stage 2 verification result (2026-11-12)

The parent reviewed the executor implementation and reproduced one serial real-VM smoke on a fresh private overlay and host journal. Commands ran as the desktop user. The smoke passed artifact checksums, running status, confirmed process-group cancellation, deadlines, duplicate-ID suppression, payload conflict rejection, stale-epoch and wrong-bot rejection, saved-state resume and crash recovery. The tested external marker was not repeated. The owned VM stopped at handback.

Review found that a remembered epoch plus a missing receipt cannot exclude rollback after hello. A failing regression demonstrated the error. Reconciliation now keeps the outcome unknown instead of allowing replay. A second failing regression covered cross-bot command lookup. The parent also bounded transport writes and reused existing hashing and encoding packages instead of new hand-written implementations.

Eighty-three Rust unit tests, three non-live integration tests, fifty Python tests, formatting, clippy and syntax checks pass. Live browser tests remain ignored. No optimization benchmark matrix ran.

Stage 2 is accepted as a developer control path. It is not a production bot runtime. Fresh provisioning, multi-bot execution, real dropped-reply injection and escaped process sessions remain untested. Process-group cancellation does not provide cgroup containment. Stage 3 must connect the long-running server and agent tools, define queue ownership and surface unknown outcomes. The results report is `docs/stage2-results.md` on the implementation branch.

## D49. Optimize one bot before testing multi-bot capacity (2026-11-12)

**Decision:** Focus resource work on one bot. Keep it lean while working and minimize its footprint while idle. Do not make multi-bot load testing the next performance milestone.

**Reason:** The owner wants low cost per bot first. That foundation should allow more bots later.

**Checks:** Distinguish working, awake-idle and saved states. Measure CPU, peak and steady resident memory, background work, capture activity and storage separately. Include the guest executor and attribute host-side bot overhead separately from the shared Deck service. Do not substitute configured guest RAM for measured usage.

**Next priority:** Integrate automatic idle sleep with durable command and task ownership. Keep screen capture off unless requested. Bound active commands, output, artifact transfers and background workers. Preserve pause, budget and unknown-outcome rules. Do not save a computer while unresolved work owns it.

**Boundary:** Multiple-bot isolation remains a correctness requirement. Concurrent capacity testing and further compression matrices are deferred until one bot's integrated lifecycle is lean and verified.

## D50. Integrate one lean bot in Stage 3 (2026-11-12)

**Decision:** Proceed with Stage 3 under `stage3-plan.md`. Build lifecycle optimizations into the server, queue, tools and UI integration. Tune from measured behavior after the integrated path works.

**Reason:** The owner approved continuing after choosing integration-time optimization over another separate tuning phase.

**Boundary:** Start with one bot. Keep multi-bot capacity tests, further compression matrices and real account integrations outside this stage. Preserve pause, budget hold and unknown-outcome rules. Test with synthetic data and a scripted model.


## Stage 3 verification result (2026-11-12)

The parent reviewed the integration and operated its real-server UI in bare. A separate strict real-VM run passed browser, shell and file delivery, automatic idle save, wake and chat continuity. Model-call and capture counters stayed flat in the awake-idle and saved windows. Direct viewer disconnect stopped the capture worker. Guest receipts confirmed active cgroup containment. No owned VM or UI helper remained after verification.

Review added failing regressions and fixes for active commands mislabeled as unknown outcomes, later tasks starting while an outcome remained unresolved, waiting-task resume bypassing pause, and interrupted browser bridges reported as a known cancellation. A browser bridge can stop while its persistent browser completes an external action. Such outcomes now remain unknown.

The overview is the landing view. Chat opens when the owner opens the bot. Capture is requested during browser activity in an open bot chat or by explicit screen control. Navigation was checked in bare after the landing fix.

One hundred thirty Rust unit tests, three non-live integration tests, sixty-two Python tests, twenty-two web tests, type checking, build, formatting and clippy pass. Live browser integration tests remain ignored; dedicated parent UI checks ran separately. The strict smoke contains explicit idle and capture assertions. An earlier UI-proxy run is not a resource baseline because its proxy delayed viewer disconnects.

Stage 3 is accepted for one configured bot with synthetic events and a scripted model. This is not a declaration that the full product vision is complete. Automatic spending accounting, fresh provisioning, real accounts and event sources, memory integrations and real halfway-save failure remain unverified or deferred. No deployment or production merge occurred. Implementation report: `docs/stage3-results.md` on the implementation branch.

## D51. Optimize the integrated single bot before capacity testing (2026-11-12)

**Decision:** Complete the three-stage integration, then profile and optimize one bot. Measure working, awake-idle and saved states separately. Keep progress and performance evidence current. Defer concurrent capacity testing until this pass ends.

**Reason:** The integrated lifecycle gives performance work a useful workload. Lower single-bot overhead improves the basis for scaling. Distributed ownership and resource controls still need separate validation.

## D52. Keep browser sessions in files under `state_dir` (2026-11-12)

**Decision:** `[bare] state_dir` names a folder. deck gives each `save_state` and `ensure_session` call a file `<name>.state` in it. The session name must match `A-Za-z0-9_-`, 1 to 64 characters. The model never sets the path. The folder is created with mode 0700 on Unix.

**Reason:** Without a file, a login lives only in one `bare-mcp` process. A new deck process or an eval run must log in again. Some logins need a phone approval each time.

**Boundary:** The files hold live credentials. Keep the folder outside the repository. VM mode does not change: the guest sets its own paths, and the relay path is unchanged.

## D53. Private eval runs make their own files and check downloads (2026-11-12)

**Decision:** `evals/run.py` takes `--secrets-file` and `--state-dir`. A task with `make_files` gets a fresh `.txt`, `.pdf` and `.png` for the run in `workspace/`. A `downloaded` check passes when a file in `workspace/downloads/` has the SHA-256 of a made file. A secret line of the secrets file (a name with PASS, TOKEN, SECRET or KEY) fails the run when its value shows in the answer or a tool argument.

**Reason:** A real mail task must attach files and prove that the same bytes came back. The files must be new for each run, so an old message cannot pass the check. Private tasks and logins stay outside the repository.

**Boundary:** Other secret lines, such as an email address, are not leak-checked, because a correct answer can show them. Private task files live outside the repository.

## D54. Separate memory safety stops from host paging context (2026-11-12)

**Decision:** For future sustained memory experiments, record host-wide swap-out above the existing threshold as a contended window. Do not stop only for that signal. Keep VM swap, guest swap, OOM, cgroup limit, host CPU/memory pressure and low available-memory stops. Do not change the cycle benchmark policy.

**Reason:** Two incomplete attempts crossed the host-wide paging threshold while the VM reported zero swap, no limit event and substantial host memory remained available. That counter does not identify which process caused paging. Memory-workload completion and clean latency comparisons need separate labels.

**Evidence rule:** Preserve both failed attempts under their original method. Version the revised method and retain paging windows in the trace, summary and status. A completed workload under paging is observational evidence, not proof of uncontended performance or a leak. No production setting changes.

## D55. Bound the one-bot save stream at 512 MiB/s (2026-11-12)

**Decision:** The `vmproto.py stop` command used by the configured one-bot server sets and verifies a 512 MiB/s migration cap before starting a save. An explicit `--bandwidth-mib 128` preserves the original CLI cap. Direct Python benchmark callers preserve the current QEMU limit unless they request one. The codec remains unchanged.

**Reason:** A controlled 12-cycle comparison across three boots reduced median save call time from 3.391 to 1.078 seconds at comparable state sizes. All recovery gates passed. Shorter saves release the bot computer sooner.

**Limits:** This is a warm-cache, one-bot result. It concentrates work into a shorter burst. Physical disk bandwidth and peak CPU were not established. Concurrent saves, slow storage and cold-cache performance remain untested. Query/read-back failure stops the save instead of silently using another cap. No general concurrency or capacity claim follows.

## D56. Work follows owner-set success criteria (2026-11-13)

**Decision:** The owner sets one success criterion at a time. Each criterion has tasks, a proof and a pass rule in `docs/success-criteria.md`. The first criterion is SC1: Gmail works end to end. Private eval tasks prove it.

**Reason:** A fixed target with machine-scored checks shows when work is done. It also picks the next `bare` and `deck` fixes.

## D57. Review conversational bot setup before creation (2026-11-13)

**Decision:** The owner describes the bot's job in chat. Deck asks follow-up questions and proposes a name, instructions, model, permissions and computer configuration. Each new bot gets its own computer by default. Deck recommends the model, tools and resources from the job. The owner can edit every proposed setting and approve or reject each permission separately. Deck waits for setup approval before creating the bot and computer.

**Drafts:** Save unfinished setup so the owner can leave and continue later.

**Permissions:** Recommend full authority for the approved job under D60. The owner can narrow that authority during setup. If a restriction blocks required work, suggest a narrower alternative and explain which work remains blocked.

**Execution:** Set up the computer first. Then request required credentials through protected input in the bot's chat. Keep credential values out of chat history and model context. Trigger phone approval when needed and wait for the owner. Pause at a CAPTCHA and notify the owner to complete it. Preserve setup progress in both cases.

**Recovery:** Default to three total attempts for setup failures that are safe to retry. Let the owner change the limit. Notify the owner when attempts are exhausted. Waiting for a login approval or CAPTCHA does not consume retries. D34 still governs uncertain external actions. Continue independent setup and work only when it does not disturb the blocked task's session.

**Completion:** Show setup progress and requests for help in the bot's chat. Finish with a checklist of configured items and remaining blockers. Start specific work already supplied by the owner once its prerequisites are ready. If the owner supplied only a general role, finish setup, notify the owner that the bot is ready and ask for instructions. Shared inbox presentation remains for a later planning pass.

**Later changes:** Update ordinary job instructions directly. Recommend model changes. The bot can install tools and adjust computer resources automatically without another setup approval. Report significant changes in chat. Request approval only when authority must extend beyond the approved scope or an explicit restriction must change. D60 governs routine action authority. D61 governs automatic working-budget increases and the owner-set spending ceiling.

**Reason:** The owner approved conversational setup with review, automatic preparation and durable requests for help. This refines D28–D30. It records product requirements, not implemented behavior.

## D58. Keep explicit grants and restrictions persistent (2026-11-13)

**Decision:** Describe permissions as actions, such as reading mail, sending mail, deleting mail and submitting applications. A send-mail grant covers every recipient. Ongoing grants remain active until revoked.

**Requests:** Do not request approval for actions already covered by D60. When additional authority is required, offer allow once, allow until revoked and deny. Accept explicit grants from the owner's authenticated chat instructions and show the resulting grant in chat. Clarify vague instructions. Websites, messages and other external content cannot grant permissions.

**Revocation:** Block new actions immediately. Stop an active action where possible and report its outcome. Revocation cannot undo an action already submitted. Resolve uncertain outcomes under D34.

**Blocked work:** Pause dependent work. Continue unrelated work only when it does not disturb the waiting task's session. Show what remains blocked.

**Coverage:** Apply the same permission instructions to desktop, browser, shell and API actions. Preserve D33's trusted-instruction boundary. This decision does not claim that raw tool access provides hard technical enforcement.

**History and requests:** Record who approved each grant, its scope and when it changed or was revoked. Show requests in the bot's chat and notify the owner when attention is needed. Defer shared inbox design. Notification delivery remains a separate design choice.

**Verification:** Cover grants, denials, one-time use, revocation during work and attempts through another tool. Verify behavior rather than only the approval screen. Distinguish tested behavior from the technical guarantees that D33 excludes.

**Reason:** The owner approved these permission defaults. They refine D25 and D33 without changing the accepted trust model.

## D59. Support long-running unattended sessions (2026-11-13)

**Decision:** Design bots for long-running unattended work. Preserve task progress across restarts. Retry safe work within limits. Continue unblocked work without disturbing waiting tasks. Wait durably when approval or owner input is required.

**Owner return:** Notifications must lead back to the task and retain enough context for the owner to respond later. The owner's absence must not discard progress, grant permission or bypass a hold.

**Boundary:** Unattended sessions do not mean ten sessions or any other concurrency limit. Preserve task ownership, pause, budget and uncertain-outcome rules. This decision does not approve new event sources or notification channels.

**Reason:** The owner clarified that bots will run unattended for long periods. This is a product requirement, not a claim of verified recovery behavior.


## D60. Default to full authority for unattended work (2026-11-13)

**Decision:** Propose full authority for the bot's job during setup. One setup approval authorizes the bot to select actions and complete that job without repeated action approvals. This includes sending messages, posting, submitting forms, deleting items, installing tools and adjusting computer resources within the approved scope. A send-mail grant covers every recipient.

**Owner control:** The owner can restrict authority during setup, change it later, stop work or revoke it. Request approval to extend beyond the approved scope or change an explicit restriction. Existing authority does not expire because a session runs unattended for a long time.

**Requests for help:** Request credentials through protected input. Wait for phone approval or CAPTCHA completion when needed. Ask to raise the absolute spending ceiling under D61. Investigate uncertain outcomes under D34 before asking for direction. Preserve progress while waiting. Continue independent work only when session ownership and dependencies permit it.

**Visibility:** Record outcomes and significant changes in chat. Keep routine progress silent or in summaries. Focus notifications on actionable blockers. This decision selects no new notification channel.

**Boundary:** Current permissions apply across desktop, browser, shell and API tools under D33's trusted-instruction model. External content cannot expand authority. Full authority does not bypass owner stop, pause, revocation, uncertain-outcome rules or the absolute spending ceiling.

**Scope change:** This replaces repeated action approvals within the approved job under D14, D25 and D58. It preserves initial setup review under D57 and explicit restrictions. It is a product requirement, not a claim of implemented enforcement.

**Reason:** The owner wants unattended bots with full authority. Frequent permission requests defeat that purpose.

## D61. Adjust working budgets below an owner-set spending ceiling (2026-11-13)

**Decision:** Distinguish the bot's working budget from its absolute spending ceiling. The bot can raise its working budget automatically when the work reasonably requires it. It cannot exceed or change the ceiling set by the owner. If more allowance is required, preserve progress and request a ceiling increase from the owner.

**Increase basis:** Base an automatic increase on the remaining work and recent spending. Record its reason in chat and notify the owner after the increase. Repeated failures without progress or a spending spike must pause the affected task for investigation rather than justify repeated budget increases.

**Ceiling hold:** Reaching a working budget alone does not require owner approval when an increase fits below the ceiling. If the next expenditure cannot fit below the ceiling, hold the affected work. Events, schedules, restarts, automatic budget changes and owner absence cannot bypass that hold. Only explicit owner approval can raise the ceiling. Preserve pause and unknown-outcome holds independently.

**Open details:** Define currency, accounting periods, ceiling scope, in-flight cost reservations, increase sizing and spike thresholds before implementation. These details must preserve the owner-only ceiling change. Do not claim that accounting controls universally prevent spending through raw browser or shell access under D33.

**Scope change:** This replaces D36's approval requirement when a working budget is reached. Owner approval remains required to raise the absolute ceiling. It updates the spending boundary in D57 and D59. It does not approve the five planning agents' other proposed defaults.

**Verification requirements:** Show that routine authorized actions proceed without approval. Show that working-budget increases fit below the ceiling and retain their reasons. Show that insufficient ceiling allowance holds work and produces one durable request. Reject bot-originated ceiling increases. Confirm that restart, schedule delivery and duplicate owner responses cannot bypass or apply an increase twice. Preserve login waits, revocation and uncertain outcomes.

**Reason:** The owner permits reasonable automatic budget increases but reserves changes to the absolute spending ceiling.

**Implementation proposal:** See [unattended-implementation-plan.md](unattended-implementation-plan.md) for delivery stages, dependencies and verification gates. The proposal does not approve unresolved accounting defaults.

## D62. Commit reviewed setup progress and initial work durably (2026-11-13)

**Decision:** Store setup snapshots in the existing SQLite store. Use separate snapshot and reviewed-draft revisions. Approve instructions and spending configuration atomically. Save the ready marker and initial task in one transaction. Register ready bots with independent runtime paths and control routes.

**Host binding:** Pin the approved image, runtime, resource, model and pricing profile. Stop on a changed binding or a mismatched image or executor identity. Preserve existing files. Paused or budget-held ready bots must retain access to owner controls without an automatic wake.

**Boundary:** These are implementation choices for D57–D61. They do not approve numeric budget defaults, capacity claims or protected credential storage. Current resource and model support remains limited by the configured harness and priced profile. Protected input and live acceptance remain open.

**Reason:** Unattended setup must survive restarts without losing its initial work, repeating approval or attaching another bot's computer.

## D63. Deliver protected input by guest reference and reconcile receipts (2026-11-13)

**Decision:** Accept protected values through authenticated endpoints outside ordinary chat. Keep the host value in request memory. Send it to the bound guest through stdin. Store only request metadata and receipts on the host. Keep the guest credential file private. Complete a wait only after matching guest evidence. Recheck that evidence before recovering a delivered task request.

**Privacy boundary:** Mask the entry control and clear it on submission or dismissal. Gate Deck screenshot tools and the live viewer while sign-in is sensitive. Raw guest shell, DOM and evaluation remain inside D33's trusted-instruction boundary. This is not universal value redaction. Live guest and raster acceptance must be reported separately.

**Reason:** Implement D29, D57 and unattended waits without putting values into model context, chat, command arguments or durable host records. Preserve exact task ownership and independent holds when an owner responds.

## D64. Prepare private storage and prove desktop readiness before initial work (2026-11-13)

**Decision:** Create validated owner-only bot data and workspace folders before runtime registration. Refuse linked or foreign-owned path components. Keep configuration reads free of directory creation. Before the executor setup step completes, require the existing bare session to answer its protocol and match the bound guest identity. Use a bounded probe. A missing session cannot count as Ready.

**Reason:** Live acceptance found missing host directories and a first browser task that ran before the desktop answered. These checks refine D62. They add no routine owner approval and authorize no replacement computer or browser fallback. Live confirmation remains a separate gate.

## D65. Validate local synthetic sign-in before a real account (2026-11-13)

**Decision:** Run setup acceptance against a local synthetic login first. Use generated credentials, a scripted model and one private guest computer. Exercise phone approval and CAPTCHA-style owner waits without a real account. Verify the service state separately from the owner answer. Keep live VM checks serial.

**Reason:** The owner explicitly selected local synthetic login first. This permits delivery, restart and duplication checks without Gmail or private account state. A fixture pass does not establish real-service CAPTCHA support or universal screenshot secrecy.

## D66. Preserve privacy transitions during capture (2026-11-13)

**Decision:** Store a per-bot privacy generation with the gate-open transaction. Keep it after the gate closes. Sample the gate and generation together before and after capture. Discard a frame if the gate is open or the generation changed. Refuse a cached frame from an older generation.

**Reason:** A gate can open and close during one capture. A boolean check cannot detect that transition. This refines D63. It adds no routine approval and does not establish universal redaction or real-pixel secrecy.


## D67. Prove one morning-summary bot across multiple Gmail accounts (2026-11-14)

**Decision:** Make a morning briefing the first daily-use goal. One bot manages multiple Gmail accounts on its computer. It identifies mail that needs a reply and triages the remaining mail. Use one browser session with Gmail account switching. Keep account identity and task results distinct within the shared login state. Do not create a separate browser or isolated session per account. Show the account for every result. A blocked account must not stop independent work on other accounts.

**Delivery:** Validate two accounts first. Preserve the earlier full-mail tasks as later acceptance work. Record failures as sanitized, deduplicated GitHub issues for a separate fixing agent. Keep browser failures in bare. Verify the affected task after a fix before clearing its blocker. Existing review and browser-update rules still apply.

**Action scope:** The owner approved applying labels. Do not archive mail. Include recommended actions in the summary.

**Open choices:** Confirm morning time, timezone, coverage window and label names before configuring the daily job.

**Reason:** The owner wants to assess a useful daily bot before expanding the general product. One bot per mailbox does not meet that goal. This records requirements, not verified multi-account support.
