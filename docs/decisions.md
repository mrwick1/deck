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

**Order:** Issues that block a `deck` task come first, then the oldest. The task skips an issue that has an open pull request or the label `in-progress`. It adds `in-progress` when it starts an issue. D93 replaces the pinned `bare` of this decision.

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

### 2026-11-14: keep interactive acceptance open until explicit stop

The generated setup UI acceptance command omits --wait. That flag ends the controller when its first task completes, before the required final restart and late image evidence. The deadline, loss checks and explicit stop cleanup remain. The regression failed before the change; all 43 controller tests pass afterward. No product runtime or acceptance gate changes.

## UI experience prototype (2026-11-14)

**Decision:** Build the new experience in a separate `prototype.html` entry point. Preserve the existing app as a reference. Use the installed React and Motion dependencies. Start with a bot-centered home, a main composer and bot workspaces with chat, activity and computer views.

**Reason:** The owner approved a new interface with fluid motion and stable layouts. A separate sample-data journey permits review before runtime integration.

**Boundary:** The prototype makes no API requests. Account connection, owner approval, work and results are simulations. State resets on reload. Browser motion, accessibility and layout-shift acceptance remain unverified.

## Prototype interaction flow (2026-11-14)

**Decision:** Keep prototype state per bot. Give Home its own composer draft. Show the destination before sending work. Keep setup review separate from bot creation. Use explicit controls to advance simulated account and task events. Require an owner-entered demo ceiling without a monetary default.

**Reason:** The owner approved the visual prototype and requested the design flow. Independent state permits review of navigation, owner waits and queued work without affecting the live runtime.

**Boundary:** The prototype uses a sample mail workflow. It does not implement arbitrary job planning, real accounts, spending enforcement or cross-bot delegation. State lasts only for the current page session.

## Connect the compact experience to the backend (2026-11-15)

**Decision:** Add the authenticated redesign at `/experience.html`. Retain the original app at `/` and the sample preview at `/prototype.html`. Reuse setup review, chat streaming, protected input and bot control APIs. Keep API calls on the same origin. Exclude the separate entries from the original app's service-worker navigation fallback.

**Layout:** Use compact bot rows and one workspace header. Show the composer only in chat. Put preview controls in a disclosure. Keep touch targets at least 44px. Honor reduced motion.

**Reason:** The owner approved the journey and requested real backend connections after the design pass. The owner also identified wasted space in the prototype.

**Boundary:** Publish only sample assets to the public preview. The authenticated interface requires the existing server and authentication. This change does not restart the server or run real-account work. Live phone motion and backend acceptance remain separate checks.

## Review the complete mobile flow (2026-11-15)

**Decision:** Use the existing shadcn/Radix controls, Vaul sheets, Motion transitions and assistant-ui chat renderer for the redesign. Start with mobile layouts. Cover every screen, input, selection, loading state and message-send transition. Retain reduced-motion support and real backend behavior.

**Review gate:** The owner requested two rounds for each screen. GPT Sol Medium and Opus 5.5 both review each round. Exchange their first-round feedback before the second round. Complete Home before mobile navigation, mobile use and the remaining screens. Record findings and fixes per screen. Do not claim native-device performance from viewport simulation.

**Reason:** The owner made a fluid mobile experience the main priority and expanded the work from Home to the complete flow.

## Track depth for in-app Back and animate keyed route bodies (2026-11-15)

**Decision:** Store a depth number in each history entry. Header Back calls `history.back()` when the depth is above zero. At depth zero it replaces the entry with Home and never leaves the app. Navigating to the current route replaces the entry. A newly created chat replaces `chat/new`. Render each route (header, notices and body) as one keyed Motion unit in `AnimatePresence` with `mode="wait"`. The outgoing unit is `inert`. Reduced motion skips entrance and exit. Focus moves to the heading when the new unit mounts. A cold load shows a route-shaped shell with the correct title, Back button and skeleton. Only chat routes and New bot show a composer skeleton. Headers are 64px. The bot controls use the Radix `DropdownMenu`. Tab scroll restores in a layout effect keyed to the tab. Both entry pages use `viewport-fit=cover`, and `.app` applies the safe-area insets once.

**Reason:** Two reviewers found that Back pushed Home and looped, cold deep links showed the Home skeleton, Home and other headers differed by 4px, and routes had inconsistent or missing exits. The plain `details` menu had no outside-click, Escape or focus handling. The tab scroll ref wrote on every poll render.

**Boundary:** Unit tests cover the navigation outcomes against a fake history. They do not cover React rendering. No frame smoothness, safe-area, physical keyboard or touch result is measured. See [ui-navigation-review-checks.md](ui-navigation-review-checks.md).

## Keep the setup review editor stable and exact (2026-11-15)

**Decision:** Enter in the review form saves the draft and never approves. Save draft is the only submit button. Approve is a separate `type="button"` that runs the same validation as Save. Unsaved edits live in App-owned state keyed by setup id. Each editor stores its base row and its typed fields. The form no longer remounts when the server version changes. A newer row replaces a clean editor. A newer row leaves a dirty editor untouched and shows a conflict. Save and Approve stay blocked until the owner discards their edits or keeps them on the newer row. A reload warning appears while edits or a write are unsaved. Nothing is written to browser storage. While a write runs, the editable fieldset is locked. The actions use `aria-disabled` so focus stays, and a synchronous guard stops a second submit. Only a confirmed reply announces success. A lost reply triggers a read-only comparison against the submitted and base snapshots. That check never replays a write. If the read also fails, Save and Approve stay blocked until a read settles the outcome. Budget amounts are written and read with integer arithmetic. Resource fields need whole numbers from 1 to 4294967295, the server `u32` domain. A zero allowance or ceiling and an empty restricted list stay valid, as `src/bot/setup.rs` allows. The form explains both. A missing server budget still blocks approval.

**Reason:** Two reviewers measured that Enter approved, a version bump discarded typing, Save skipped resource validation and fields stayed editable during a write. Floating point turned `9007199254740.991` into an off-by-one amount. Server code permits zero spending and no granted actions, so the form explains them instead of rejecting them.

**Boundary:** Activity, Requests and the original app keep their own float formatting. The checks in [ui-setup-review-checks.md](ui-setup-review-checks.md) state which results are tests, which are fixture runs in `bare` and which are unverified. No phone keyboard, IME, touch, screen reader or frame timing was measured.

## Keep typing that arrives after a write, and keep work when a setup leaves Draft (2026-11-15)

**Decision:** The editor stores the fields typed at send time with each write. A confirmed own row replaces the editor only if the fields are unchanged. Otherwise the editor rebases on that row and keeps the new typing. Save and Approve stay blocked while a sent write awaits a read. A row that leaves Draft keeps an unsaved or unconfirmed editor. The setup screen then shows read-only recovery with Copy and a confirmed Discard. One comparator checks every draft field except the revision.

**Reason:** Round two showed a poll wiping text typed after a lost read. It also showed a remote phase change deleting unsaved text without a message. The old comparison ignored budget currency and period.

**Boundary:** Recovery has no Save, Approve or Keep mine. A clean editor still drops when the row leaves Draft.

A read that finds another revision clears the own-write expectation and opens conflict resolution. A manual refresh can request the saved revision without another write. These paths prevent a stale base from trapping the owner.

## Keep the setup progress screen stable and honest (2026-11-15)

**Decision:** The setup screen uses one key per setup id in every phase. A version update never remounts it. Typed protected input, focus, scroll, notes and the action guards survive polls. Protected input stays keyed by request id and clears on submit, failure, unmount, expiry and replacement. The challenge answer has a synchronous guard that survives row revisions. Its pending, success and error text names the request, so a replacement request never shows an old result. A second protected submit returns before any state change while the first is held. The header reads Review setup for a Draft and Setup progress for every later phase. The cold route says Setup.

**Controls:** Pause and Resume are requests. The UI shows Pause requested or Resume requested only after a 204 reply. The record has no pause field and the server changes no setup revision, so the UI shows no paused state. Resume is off for an unknown outcome and for an unsupported challenge, with the reason beside it. Refresh progress shows a pending state and then a result read from the workspace error, because the refresh call never throws. An exhausted setup explains the limit. The new limit must be a whole number above the current limit and at most 4294967295, the server `u32` maximum. An invalid value sends nothing, shows an error beside the field and focuses it. An unknown outcome shows its step text and states that Deck cannot resolve it yet. It offers no retry and no owner guess. Wait and phase text come before the recovery editor. Buttons wrap and measure at least 44px.

**Reason:** Two reviewers measured that each revision remounted the screen and lost typing, focus and errors. They measured two answer POSTs from two fast clicks. They measured a second secret submit unlocking the field while the first was held. They measured 28px buttons and a Cancel button past the panel edge at 320px.

**Boundary:** Unit tests cover the key, the guard, the duplicate flag and the attempt check. Fixture runs in `bare` cover the rest. See [ui-setup-progress-review-checks.md](ui-setup-progress-review-checks.md).

## Preserve position in Conversations (2026-11-15)

**Decision:** Back restores the list scroll and the opened row's focus. Explicit navigation to the list starts at the top. Existing chat headers use the known title. Rows wrap long titles and identify Deck or the bot. Empty and stale lists explain their state. Stale rows remain readable; sending requires reconnection.

**Reason:** Both reviewers measured panel overflow and lost return position. The server sorts by newest creation, so the UI states that order without inventing last-activity data.

## Isolate late setup feedback (2026-11-15)

**Decision:** An unmounted protected-input form emits no success notification. A delivery notification names its label. Challenge controls stay visibly pending until their reconciliation read finishes. Refresh status does not erase control errors.

**Reason:** Second-round checks reproduced stale success notifications, disappearing action errors and controls that looked enabled while their guard was still locked. Exhausted-form spacing now uses its own class; a direct-child selector also matched draft review.

## Keep request responses across tab changes (2026-11-15)

**Decision:** The bot workspace owns inbox operation state and shared challenge response state. A forced reconciliation read starts after older reads settle. A request that may have applied stays locked until the owner checks and reviews it. Protected values remain in DOM fields and never enter the operation store. Shared request panels join the removal-focus sequence.

**Reason:** Reviews reproduced duplicate writes after stale reads and tab changes. A component-local challenge guard did not survive a tab change even after inbox guards moved into the workspace.

**Boundary:** Several server actions have no version or idempotency key. A read cannot prove that a returning hold is the same hold. The client does not automatically replay writes.

Requests state belongs above the route, not only above its tabs. Activity task actions use the same store and task identity. This prevents a route return or a second screen from bypassing an unknown response.

## Recover screen reads and explain workspace actions (2026-11-15)

**Decision:** Retry a failed frame read three times with increasing delays. Then offer Retry. Clear frames on privacy responses and stream errors. A stopped stream offers Reconnect. Preload each image and handle decode failure before showing it. Keep the frame area bounded.

**Reason:** An unchanged desktop emits no new frame event. The old viewer could claim reconnection without scheduling recovery. Invalid images also cleared all status.

**Decision:** Activity uses exact budget formatting and offers Open conversation on completed work. Queued and held tasks can change priority. Task and computer actions show pending and accepted feedback. A compact connection note sits below the bot tabs.

**Boundary:** Results open the conversation, not a task-specific message. Home says Completed results because tasks have no completion timestamp. Request acceptance does not prove the computer has reached a new state. Physical motion and real-backend acceptance remain unverified.

## 2026-11-15: Keep a pending question before its reply

**Decision:** Merge the pending user message into stored history before adding live run output. Use the same message order before and after send confirmation.

**Reason:** An early streamed reply previously displaced the pending question and changed its positional message ID. The regression test failed with assistant-before-user order, then passed after the merge changed.

## 2026-11-15: Keep chat sessions above route views

**Decision:** Keep Deck and visited bot chat controllers mounted above route views. Render their views through portal slots. Retain unconfirmed sends by conversation and keep unknown conversation creation separate. Never replay an ambiguous write automatically.

**Reason:** Route remounts discarded pending guards, live output and recovery text. A new conversation must keep later typing when its first send returns an ID. Acknowledging one conversation must not discard another held message.

**Boundary:** State lasts for the current application session. It does not survive a full page reload. Matching stored text is evidence, not proof of delivery. A lost create response can leave an empty conversation whose ID is unknown to the client.
## D68. Persist the shared browser profile in guest-owned storage (2026-11-14)

**Decision:** The guest browser adapter sets a private state file for `save_state`, `ensure_session` and `open state=`. A validated saved-state name selects the file. The live session label does not select it. One shared profile can retain multiple accounts through a site's account switcher. This adds no per-account browser or profile.

**Storage:** Keep the state under the guest user's `.local/state/deck/browser` directory. Reject unsafe names, linked or foreign directories, and unsafe existing files. Preserve valid files. Bare creates state files with private permissions.

**Reason:** The guest adapter previously passed no state file. A browser process restart lost saved state held only in memory. The host state directory does not apply to the guest relay.

**Evidence boundary:** The adapter and live synthetic tests pass. One browser session retains two synthetic accounts after a process restart. Actual Gmail switching, guest execution and VM recovery remain separate acceptance gates. No memory-saving claim follows.

## D69. Gate guest readiness on the browser capabilities the adapter uses (2026-11-14)

**Decision:** `bare-call --ready` reads the `tools/list` schemas. It requires the tool properties that the adapter sets: `open` with `state` and `file`, `save_state` with `name` and `file`, and `ensure_session` with `name` and `file`. A missing tool, a missing property or a malformed schema gives `ready: false`, reason `incompatible_browser` and exit 3. The reply lists only static names such as `open.file`. It never holds page content, credentials or schema text. The no-identity check and the RPC failure reasons stay as they were. The probe makes no tool call and starts no session.

**Reason:** The live guest accepted its old browser and then failed on `open` with an unknown `file` property. This is a capability contract. It does not compare version numbers. It does not require `fresh`, which the adapter does not use.

**Harness:** A run with `--bare-candidate` skips the readiness probe before the candidate is installed. The candidate install keeps its hash and manifest checks and must pass the same probe before any scenario tool. A run without a candidate probes at boot and fails before the scenario on an old browser.

**Evidence boundary:** Tests use fakes only. No guest image changed and nothing deployed. The guest image must contain a compatible browser. Live VM and real-account behavior remain untested.

## D70. Make guest internet access explicit (2026-11-13)

**Decision:** VM mode keeps fixture-only networking by default. Set `[vm] internet = true` to permit outbound access for real sites. The CLI uses `--internet`. This also permits access to reachable private services. It adds no inbound port or host mount.

**Reason:** The first Gmail milestone needs a real site. Synthetic tests must keep their existing restricted network.

**Recovery:** Save metadata records the network policy. Restore refuses a different policy before starting QEMU. Older saved states use the restricted default. Restore with the original setting before changing the policy for a cold boot.

## D71. Add a registered account by delivering only its secrets (2026-11-14)

**Decision:** Deliver the secrets of a new account alone, under new request IDs. Check the receipts of the earlier accounts before and after. Record the account in its own private file only after every check passes. Reopen the saved profile to load the new secrets.

**Reason:** The guest keeps earlier secret lines, but a replay or a replaced file could lose a login. A running session reads the secret file only when it opens. Identity needs exactly one passing account among all registered accounts.

## D72. Poll Gmail in the browser and queue only observed conversation revisions (2026-11-15)

**Decision:** The owner chose browser polling every 120 seconds, without the Gmail API. One shared desktop owner runs all accounts in turn. A poll never disturbs a task that waits on the same desktop. A poll respects pause, budget holds and unknown-outcome holds. An account counts as healthy only after a full, completed, identity-verified scan. The first complete scan of an account stores a silent baseline and queues nothing. Each later complete scan queues one low-priority triage event for every unique (thread, latest message) pair not seen before. The store keeps every observed pair, so a change from A to B and back to A queues A once. A changed latest message marks a changed conversation. It does not prove an incoming message. An incomplete, failed, logged-out or mismatched scan records only the attempt time and a fixed result code. It never moves the baseline, the seen pairs or the queue. No poller is deployed by this decision.

**Reason:** A partial page must not look like an empty inbox, and a first scan must not flood triage. The store cannot check the page, so the adapter supplies only verified complete scans. Seen is not processed: events stay durable until the batcher turns them into a task.

**Storage:** Schema version 10 adds `gmail_accounts` and `gmail_seen`, keyed by bot and an opaque account key. Events use source `gmail_poll` and kind `inbox_changed`. Payloads hold only account key, thread ID and latest message ID. Seen pairs are never pruned in this change.

## D73. Run one serialized Gmail window worker inside the bot loop (2026-11-15)

**Decision:** D72 "healthy" means a completed, identity-verified scan of a monitored date window. It does not mean a full inbox history or real-time arrival. The first scan of an account looks back 24 hours. Each later scan starts at the start time of the last successful scan minus one hour. Limits apply: three pages, 100 rows per page and 90 seconds per account. One worker serves all accounts in turn, and a poll starts no earlier than 120 seconds after the previous start. The worker runs inside `Bot::step`, so pause, budget, unknown-outcome and waiting-task rules apply. A poll uses no model. A poll yields to an owner message between accounts.

**Reason:** The owner chose one shared browser and a 120 second check. Only a window bound keeps each scan complete and short. The overlap covers mail that arrives during a scan. A watermark that moves only on success never hides a failed scan.

**Storage:** Schema version 11 adds a task `execution_kind` (`model` or `gmail_poll`) and a window watermark for each account. The legacy watermark is empty, so an upgraded account seeds a silent baseline again. A start time before the watermark is refused.

**Status:** Issue 550 is unresolved. This decision claims no deployment, no arrival time and no performance. The worker is off unless `[gmail]` is configured.

## D74. Record scan failure reasons and re-navigate once on a stale results page (2026-11-15)

**Decision:** A poll task keeps the fixed reason code and the account key of each failed scan in its detail. A scan that reads `query_box` or `grid_count` navigates once through the mailbox root and reads again. A second failure stays a failure.

**Reason:** Live, Gmail kept a stale search box value after a repeat visit of a window URL, for at least 20 seconds. Waiting did not help. A root navigation did. The reason code made the failure visible. See [gmail-worker-results.md](gmail-worker-results.md).

**Status:** Fix one and two have failing tests first. The live run passed four polls. It did not show how often the retry fires.

## D75. Build the morning summary on a tool-less model and host-owned browser steps (2026-11-15)

**Decision:** A daily summary runs inside the Gmail poll task after the accounts are scanned. The host reads each thread in the shared browser. A model call with no tools classifies batches of threads. The host validates a fixed JSON shape and stores each result. The host renders one summary grouped by account and posts it to the bot chat. A local-day row makes delivery once a day and survives a restart. The schedule uses a fixed UTC offset (`Asia/Kolkata` by default), because the repository has no timezone dependency and the machine zone is not trusted.

**Triage state:** Schema 12 adds `gmail_triage`. A scan inserts a row for each new revision. A row leaves `pending` only after a validated model answer. A failure keeps the row and counts the attempt. With the summary on, the scan queues no `bot_events` row. D72 and D73 stay the same otherwise, except that the baseline now queues triage rows.

**Labels:** Labels use fixed `Deck/` names. The write steps come from configuration, not from code. A label counts only after an identity-checked readback. An uncertain write is recovered by a readback before any second write. Labels stay off until the owner gives a live-validated contract.

**Reason:** The agent prompt grants full authority to send and delete. A mail-driven agent task must not carry it. A tool-less model removes the path from mail text to an action. Reusing the poll task keeps one browser owner and needs no task-kind migration.

**Status:** Fakes only. The thread view contract and the label contract are unverified live. See [gmail-morning-summary.md](gmail-morning-summary.md).

## D76. Run the Gmail poller as a poll-only user service that keeps events unprocessed (2026-11-15)

**Decision:** An isolated local user service runs the real Deck binary against the retained shared-profile VM and its retained host database. It polls three accounts every 120 seconds in one shared browser. `[gmail] poll_only = true` makes the worker store each new event and queue none for triage. Events stay unprocessed in the host database. A later run without `poll_only` batches them at start. No model endpoint is configured for the service. A wrapper script starts the VM with its own boot lock, starts Deck, writes a private status file and sends an alert on a failure that needs a person. The service never signs in, opens a message, sends, labels, archives or deletes.

**Reason:** A local model proxy exists. The label-only guard does not exist yet, so a triage task could act on real mail without a limit. Poll-only collection keeps every event and makes no false completion. The status file shows each account, the stage, the last error code and the age.

**Pause:** The pause that the earlier harness left in the host database is released once, by the owner's explicit authorization. A pause that the owner sets later is kept: the service releases only a pause that it set itself when it stopped.

**Status:** See [gmail-worker-results.md](gmail-worker-results.md) for the activation record. Incoming-mail detection is not proven until a real arrival is observed.

## D77. Read threads from the print view and write labels from the inbox list (2026-11-15)

**Decision:** The summary reads a thread from the print view `?view=pt&search=all&th=<id>`. It checks the host, the thread id and the message table before it uses the text. It writes a label from the inbox list. It selects the row, proves that this row alone is selected, applies a fixed label step and reads the chip back. Each step checks the account identity before and after the write.

**Reason:** A live check on 2026-11-15 showed that a legacy thread id in the URL hash does not open the thread view. The print view is a server-rendered document, so its text does not depend on the page script. A label write from the list needs no thread view.

**Status:** Fakes and unit tests only. The live label write and the live summary are not proven. The service still runs poll-only.

## D78. Run the morning summary live, with labels off (2026-11-15)

**Decision:** The isolated service runs in summary mode. It reads each thread from the print view, calls a tool-less model through the existing local proxy and posts one combined summary a day at 08:00 Asia/Kolkata. Labels stay off in the service. The model key comes from the vault at start (`rbw get`), into the memory of the Deck process. No key file exists. Deck lists a thread in the inbox and in each category tab before it writes a label. A label step may hold alternatives, split by a `---` line, because the toolbar differs between pages. After a write, Deck reloads the list and reads the chips again. `[gmail.summary] label_catchup` (default false) runs the label stage on polls after the day's delivery.

**Reason:** The live run showed that a chip on the open page is not a saved label. Gmail shows the chip, sends no request and reports a script error, so a reload drops the label (bare issue 568). Only a reload check tells these cases apart. With the check on, every write ended `uncertain`. Labels are not proven, so they are off. Most threads sit in category tabs, which the main list does not show.

**Hash URL:** Gmail opens `#all/<legacy id>` and rewrites the hash tail to its own id. The old check "the hash ends with the thread ID" was a wrong assumption about the app, not a bare defect. The print view needs no such check.

**Status:** See [gmail-worker-results.md](gmail-worker-results.md) for the run.

## D79. A label step waits for the page to settle before it writes (2026-11-15)

**Decision:** Each label step starts with `wait js "performance.now() > 60000"`, under `timeout 75000`. The page has then run for 60 s since its last load. The step holds two alternatives. The first clicks the `Labels` toolbar button. The second opens `More email options`, then `Label as`, because some toolbars have no `Labels` button. The step names no `Apply` control. Deck code does not change. The chip selector and the steps stay in private configuration.

**Reason:** The cause of bare issue 568 was not a missing `Apply` step. The open `Labels` menu has a hidden `Apply` item. It never shows. A click on a label item applies the label and closes the menu. Live on 2026-11-15, the same clicks on six threads of one account gave this result. A write 31 s or less after the page load showed a chip and lost it on reload (seven attempts). A write 45 s or more after the load persisted on five of six tries. The sixth try (a production run) did not. A longer wait after the menu opened did not help. The row markup of a failing and a passing thread was the same. The test is consistent with a load time, not with a bare defect. The exact threshold between 31 s and 45 s is not measured. The step waits 60 s for margin.

**Cost:** Each label write waits up to 60 s. Each thread needs one reload, so a label run of ten threads takes about ten minutes. The label stage keeps its time cap.

**Status:** Persistence was proven by reload on two accounts. A step without the wait is a regression: a unit test shows a chip that is never saved. The toolbar alternative `More email options` was proven in parts, not as one step.


## D80. Serve the compact UI from the Gmail poller behind the existing login (2026-11-15)

**Decision:** The poller service binary embeds the compact UI. The route is a Deck host in Caddy with `import sso`. The service binds the tailnet address and trusts only loopback, itself and the Caddy server. The UI treats a bot with Gmail status as read-only: no composer, no destination in the home list.

**Reason:** This keeps D9 and D10. One process owns the VM and the database. A chat message must not start a task in the mailbox profile.

**Consequence:** The service no longer answers on loopback. Authelia uses `one_factor` for this host. A passkey factor is still open.

## D81. Production VMs opt out of the 3 hour scope bound (2026-11-15)

**Decision:** `vmproto.py` keeps the 3 hour `RuntimeMaxSec` by default. `DECK_VM_LIFETIME_S=infinity` removes it. The Gmail service sets that value only when its config holds `persistent_vm`. Production bases, overlays and state belong under `~/.local/share/deck-gmail`, not under `/data/tmp`.

**Reason:** The scope limit stopped the production QEMU while the idle reaper had already removed its base. Prototype runs still need the bound. The global reaper stays unchanged.

## D82. Replace a lost disk with a new base and keep the logical computer (2026-11-15)

**Decision:** A fresh disk on a new base takes the guest id that the host database pins and starts with `max_epoch` equal to the host epoch. The helper takes both values explicitly. The host database moves intact with the SQLite backup API. The old overlay stays offline and unchanged. The new base has its own id.

**Reason:** The old base is lost, so the old overlay cannot boot. The host pin and the epoch fence hold the history of the commands. Keeping them means the guest accepts only epochs above all earlier ones, and `pin_guest` still rejects any other identity. A new id would need a pin edit in the database, which is the riskier path. The owner approved the replacement and fresh sign-ins.

**Consequence:** The installer restarts the executor after it writes the identity, because the udev trigger starts it first.

## D83. Move clearly unwanted mail to Trash, host-enforced, with a resumable backlog (2026-11-15)

**Decision:** The Gmail phase gets one new destructive authority: move one thread to Trash. The owner approved it in writing: organize the mailbox, keep software engineering and AI newsletters, move clearly unwanted promotions and irrelevant newsletters to Trash. Nothing else changes. The code never deletes for good, never empties Trash, never archives, never unsubscribes, never marks spam and never sends.

**Rules:**
- The model returns a `kind` for each thread. It never returns an action, a step or a target. The host decides from `kind`, the category, and facts that it read itself.
- A thread goes to Trash only when all of these hold: `kind` is `promotion` or `newsletter`; the category is `fyi`; the print view shows exactly one message; the text was not cut; the text holds none of the protected words (invoice, receipt, password, code, security, interview, offer and similar); the text holds no instruction-like phrase; the list row shows no label of the owner besides the `Deck/` labels. Anything else with kind `promotion` or `newsletter` gets the `Deck/Review` label. A kind of `other` gets it too.
- A thread with several messages is never trashed. The check is by message count, not by the latest message.
- Receipts, jobs and software or AI content get their own label. Personal, work and security mail keep only the category label. Existing labels are never removed.
- The trash step is a fixed text in the private config, as the label steps are. Config validation refuses a trash step with archive, spam, send, mute, unread, permanent, forever, empty, report or unsubscribe. The label steps still refuse trash and delete words.
- Each write follows the label rule: identity before and after, a row bound to the thread and the latest message, one selected row, a journal entry before the write, a reload, and a readback. The write counts only when the reload shows the thread in Trash. An unsure write is never repeated without a fresh readback. Two attempts end as `failed`.
- The journal lives in the host database. At the start of each pass, an open entry becomes `unknown`. The next pass reads first, so a restart cannot write twice.
- The generic agent has no path to this code. Only the summary phase calls it, and only with the `[gmail.summary.cleanup]` section.

**Backlog:** The window scan stays. A second walk reads `in:inbox before:<ceiling>` one page at a time for each account. The ceiling is the first window scan lower bound at start. The checkpoint holds the next page, the sweep number and the count of new threads of the sweep. Pages of threads that are all known already are skipped, and the page number is kept. A sweep that reaches the end of the results with no new thread ends the backlog for that account. Any other end starts a new sweep. A walk reads at most `backlog_queue_cap` pending rows ahead. Rows of the backlog rank after new mail. A per-pass cap on triage, labels and trash keeps one pass short.

**Notifications:** A routine scan, label or trash success posts no alert and no notification. The summary reports counts once each day. The service helper alerts only on holds, sign-in problems and stale scans. Nothing in this change adds an emitter.

**Reason:** The owner asked for a cleaned and organized mailbox, not a list of suggestions. A category-only rule would trash ordinary mail. A mixed thread can hide a protected message under a promotion. So the host decides, from the thread facts, and keeps the rest.

**Boundary:** A model can misjudge `kind`. The host rules cut that risk but do not remove it. Trash is recoverable for 30 days.

## D84. Live checks of the cleanup writes, and a guard for engineering and AI mail (2026-11-15)

**Decision:** The first live Trash and the topic labels ran on the real accounts. The trash step `click role=button name="Delete"` matches one button after one row is selected in a list. After a full reload the thread showed in Trash and no longer in the inbox, so `Delete` moves to Trash in this view. The topic label steps use the same shape as the category label steps. A topic label shows after a reload only when the page ran for 60 s before the click. Eight `Deck/` labels now exist in each of the three accounts.

**Rules added:**
- A promotion or newsletter with a whole word of software engineering or AI content (ai, llm, api, developer, software, engineer, github, rust and similar) goes to review, not Trash. The prompt says that such mail is `tech` even when it sells something.
- `backlog_exclude` stays empty. The live search `label:deck` matched no thread, so `-label:deck` excludes nothing.

**Reason:** The owner keeps engineering and AI mail, whatever its commercial intent. A word match is cheap and errs toward review.

**Boundary:** The word list is English only. A match inside a promotion sends a legitimate promotion to review. That cost is accepted.

## D85. Read the backlog first and keep the last finished cycle visible (2026-11-16)

**Decision:** The cleanup pass reads the backlog pages first, with a time share of one third of `max_run_secs`. Triage, moves to Trash, category labels and topic labels follow, under the whole cap. The status keeps the last finished cycle while a new cycle runs. It adds `current_started_ms` for the cycle in progress.

**Reason:** The live walk never started with `backlog = true`. The backlog read came last. Each label write waits about 60 s and a pass holds up to six writes, so the cap ended the pass before the read. A cycle takes 4 to 6 minutes and the next one starts at once. The status reset `last_cycle` to empty at each start, so the helper almost never saw a complete cycle and counted none. Complete cycles were in the task list all along.

**Consequence:** A page read cannot wait behind slow writes. The writes have at least two thirds of the cap. A cycle still never reports complete unless it ended without a failed account or an interrupted phase. A cap that ends a pass early is not a failure, as before.

**Fairness:** The queue cap counts the pending backlog rows of one account, not of all accounts. The first live pass read 32 rows for one account. A shared cap would have kept the walk of the other two accounts closed for as long as the first one refilled the queue.

**Read limit:** The first live promotions and newsletters all ended in review with the reason `partial_read`. Their print text was longer than `max_read_chars`, which is 6000 by default. The guard held, so none went to Trash. The service config now takes `summary_max_read_chars`, and the live value is 12000. The guard stays: a thread that is still cut goes to review. The model prompt grows with the limit.

## D86. Tell the owner when a run reaches the step limit (2026-11-15)

**Decision:** When a run reaches `agent.max_steps`, store a fixed assistant message in the chat. The message says the step limit stopped the work and the task is not finished. Send it as a persisted text event too. The run still fails with the same task detail.

**Reason:** Issue #37. The chat ended on a raw tool result, so the owner did not learn why the bot stopped. Fixed text adds no model call and keeps the history valid for the model.

## D87. Let each bot set its own model-call limit (2026-11-15)

**Decision:** Add an optional `max_steps` to the setup draft. Accept 1 to 200 on save. An approved bot uses its draft value. A draft without it uses the global `[agent] max_steps`.

**Reason:** One global limit forced a narrow bot and a general research bot to share a value. A low limit stopped the general bot early. The field defaults to none, so older drafts still load unchanged. Issue #38.

## D88. Queue daily schedules for ready bots (2026-11-15)

**Decision:** A draft may carry one daily schedule: a `HH:MM` time and a zone, either `UTC` or a fixed offset such as `+05:30`. Invalid values fail when the draft loads or saves. A started bot with a schedule gets one queued task per local day, after the time has passed. The task holds the setup task text, or the instructions when there is no task. The last queued date commits with the task in one transaction. A restart queues nothing twice. A missed time queues one catch-up task, not one per missed day. A bot seen for the first time after its time has passed records today and queues nothing, because setup just ran the first task.

**Boundary:** Queueing bypasses no gate. Pause, budget and other holds still block dispatch. Deck adds no UI, no weekday rules, no named zones with daylight saving, and no model extraction of a schedule from job text.

**Reason:** A job that says "every day" ran once. The owner wants daily bots, such as a news digest at 08:00 Asia/Kolkata (`+05:30`). A fixed offset keeps the first version small and testable.

## D89. Show the schedule and step limit in the setup review form (2026-11-16)

**Decision:** The web review form has a daily time, a time zone offset and a step limit. Empty fields send no value. The form checks the same rules as the API and blocks save on an invalid value. The approved view shows both values.

**Reason:** Issue #48. Only a raw API call could set D87 and D88 values before approval.

## D90. Let the owner retire a bot (2026-11-16)

**Decision:** Add the control action `retire`. It needs `{"confirm": true}`. It kills the bot's computer through the harness `kill` call and drops the runtime. It then stores `retired` in the setup record. The setup driver skips a retired setup, so it never registers or wakes after a restart. A retired bot does not count toward `max_bots`. A repeat call changes nothing. Chat, files and database rows stay.

**Reason:** Issue #47. A broken bot kept its `max_bots` slot forever. The marker is written after the kill, so a failed kill can be retried. This slice has no UI and no undo.

## D91. Give the model the current date and time (2026-11-16)

**Decision:** Add one line to the end of each run's system message: `Current date and time: YYYY-MM-DD HH:MM UTC`. The line uses the UTC time when the run builds its messages. It goes last, so the rest of the prompt stays byte-identical.

**Reason:** Issue #49. The system prompt had no date. A digest made on 2026-11-16 was headed "15 Nov 2026".

## D92. Start the bare session once after the executor install (2026-11-16)

**Decision:** Write guest executor files under a temp name in the same folder, then rename them. After the install, ask the guest for a readiness probe. Start `bare-session` once, through `swaymsg`, only when the probe says `no_session`. A failure in this step does not stop the install.

**Reason:** Issue #46. The install rewrote `bare-session` about two seconds after sway started it at login. The fresh bot then had no session, and `ready` never passed. Atomic rename removes the half-written file. The one-time start restores the session. The probe starts nothing when a session already runs.

## D93. Production follows bare main (2026-11-16)

**Decision:** Production does not pin a `bare` release. Each `deck` deploy builds `bare-mcp` from the newest `bare` main and installs it in every guest. The guest check of the `bare-mcp` hash stays. The deploy sets the expected hash from that build. No person keeps the hash by hand. This replaces the pinned `bare` of D15.

**Mechanism:** The deploy keeps the built file next to `deck`. The service config names it in `bare_mcp`. At each start, the service hashes the file and calls `resume --bare-candidate` with that hash. After the boot, the helper installs the file in the shared guest when the guest digest differs. It uses the existing install: digest, `ldd`, atomic rename, session restart, readiness. It also replaces the private pinned copy and the first run's record. The service gives the file path to `deck` and to the harness as `DECK_VM_BARE_MCP`. `vmproto.py` then does the same install in each bot computer after `start`, `restore` and `install-executor`, when the guest digest differs. A file that is missing, unreadable or fails a check stops the start. The old `expect_bare_sha256` key is ignored.

**Reason:** The owner asked for it. A merged `bare` fix did not reach the guests: the Gmail service refused any `bare-mcp` except the pinned hash, and new bots took `bare` from the base image. Merged fixes must reach production with the next deploy.

**Risk:** A `bare` regression reaches production with the next deploy. The rollback copy of each deploy keeps the previous `bare-mcp`, so one restore undoes it.

## D94. Make the compact UI the root app (2026-11-16)

**Decision:** `index.html` loads the compact UI (`web/src/experience/main.tsx`). The server answers `/experience.html` with a permanent redirect to `/` and keeps the query. The compact UI registers the service worker. The build has no `experience` entry. The old app in `web/src/App.tsx` stays in the tree but no entry loads it.

**Reason:** The owner asked for the compact UI on `main`. Production already served it at the root. `main` still loaded the old app, so the two differed.

**Boundary:** The old UI files are not removed. Removing them needs the owner's confirmation.

## D95. Reach bare through offstage (2026-11-18)

**Decision:** Without `[vm]`, deck starts `offstage mcp`, not `bare-mcp`. It keeps only the `browser_*` tools and maps them to Bare names, so the rest of deck does not change. It hides the offstage `screen` argument. offstage marks a failed report (`"ok": false`) as an error; deck keeps it as a report. The `[vm]` guests still run `bare-mcp`; see [vm-offstage.md](vm-offstage.md). offstage auto-reports and self-update stay on.

**Reason:** The owner decided that offstage is the only way an agent reaches `bare`, and deck is an agent. See `docs/design-agent-interface.md` in `mrwick1/bare`.

**Risk:** offstage runs one tool call at a time. deck already did. offstage files a beta report after repeated failures, so busy deck traffic can file reports.

## D96. Build the Gmail product backwards from its product post (2026-11-21)

**Decision:** The first product is one bot with one job: Gmail. The target is the product post in [gmail-product-post.md](gmail-product-post.md). The plan in [gmail-product-plan.md](gmail-product-plan.md) works back from it in milestones M0 to M7. Each milestone becomes one success criterion (D56). The owner set these rules:

- Deck reaches Gmail through `bare` only. No Gmail API and no IMAP.
- The closed beta is self-hosted.
- The brief goes to the Deck web app, with browser notifications. Other channels come later.
- Deck sends without approval only to people that the user names in an explicit approval step.
- Haiku sorts mail. Sonnet writes drafts and answers chat (D11).
- Code decides first. The model gets batches of up to 50 mails in one call and returns a map from each mail to its answer. Code then makes the changes.
- Labeling is rebuilt. Each user gets their own label set: the model proposes it from recent mail and existing labels, and the user approves it. The labels have no `Deck/` prefix. To do and Waiting live in Deck. Needs reply also shows in Gmail as one label, which Deck removes after the reply.
- The Gmail code moves into a structure where a new feature adds its own folder.

**Reason:** The owner will sell Deck to customers who create and manage bots. One job done well comes before many jobs. A batch call costs less and runs faster than one call per mail. The current `Deck/` status labels go stale, and the prefix names the tool, not the mail.

**Risk:** The plan sizes are estimates. Speed promises depend on `bare` fixes. Browser steps break when Gmail changes its page.

## D97. A redacted read is not a cut read, and an old cut read is read once more (2026-11-16)

**Cause:** Every promotion and newsletter ended in review with `partial_read`, also at limits of 12000 and 20000. The browser tool shows each secret value as `[secret]` in all output. The account name is a secret. A promotion holds the account name in its print header. The text that came back was shorter than the page text. The host compared the page length with the length of the returned text. It read the difference as a cut. Bare is not at fault: the redaction is documented. A check with a 10 500 character string through the same `eval` path shows no cut in transport.

**Decision:** The page program returns `sent`, the length of its own slice, as a number. The host counts a read as cut only when the page text is longer than `max_read_chars` or longer than `sent`. A missing `len` or `sent` still counts as cut. The limit stays a hard bound, and a read that is really longer is still never trashed.

**Retry:** One pass sends one earlier `partial_read` review back to triage (`gmail_retry_partial_reads`). It takes only the latest revision of a thread, with no trash write and no topic write in doubt. The organize row stays and takes the reason `partial_read_retry`. The new answer replaces the decision in place. A topic label that was applied stays recorded. A changed topic restarts at `none`. A second cut read ends as `partial_read_final` and is never retried. The journal of earlier writes is not touched.

**Not covered:** The classifier still sees `[secret]` where the account name stood. The guards read the same text.

## D98. Gmail's own `Inbox` chip is not a label of the owner (2026-11-16)

**Cause:** Four threads were refused with `trash_refused`: three in account A and one in account B. An exclusive live read showed the chips of each row. Each row showed `Inbox` and `Deck/` chips only. The `Deck/` labels came from the host. `Inbox` is a chip that Gmail adds to a row in a category list. The main inbox list does not show it. The guard read every chip that is not a `Deck/` label as a label of the owner, so it refused every thread in a category list. The selector `div.au div.av` is right: it finds the chip text. Redaction and truncation were not the cause. The guard was not wrong about an owner label: no owner label was found.

**Decision:** The guard ignores a chip that is exactly `Inbox`. Any other chip still counts as a label of the owner. That includes `inbox`, `Starred` and any unknown chip. There is no list of safe names beyond this one. A label of the owner stays protected.

**Retry:** A new refusal gets the reason `trash_refused_final`. The four old `trash_refused` rows go back to triage once each, one in each pass (`gmail_retry_refused_trashes`). The guard decides again from a live read. The journal is not touched. A second refusal is final.

**Not covered:** The read covered four threads of two accounts. Another system chip in another list is not known, and the guard refuses it.


**Live evidence (2026-11-16):** The first read used an exclusive pause. I paused only my own hold, waited for the journal to settle, read the four rows through the guest browser, and released the pause. The service then ran the release binary with SHA-256 `67a30592081f8074094f5ba09337a17c0ba6af6caba2387ee4f2c9b115ff13d6`, with the previous binary and config kept in the private rollback directory. The tests number 498 (two new for the guard, one for the retry); format and lint pass. After the swap, the production journal shows two moves to Trash, each with a reload readback (journal rows 50 and 52). The manual proof move is not counted in them. Two of the four old refusals were queued for Trash again. One retry per pass is still running, and the three-round quiet check and the summary dedupe are not yet verified.

## D99. Several label writes share one settled page (2026-11-16)

**Cause:** A label write took about 85 s. It reloaded the list, waited until the page was 60 s old (D79), wrote, reloaded again and read the label back. Over a 3 h sample of the live journal, 102 category writes ran, and 31 of them ended `not_visible` and needed a second write. A cycle made about four writes. The wait was the largest part of each write.

**Decision:** The label stage writes the rows of one account and one list on the page that the first write settled. It does not reload between writes. It unselects the earlier row with a click on the selected checkbox, and it reads the next row again. A doubt about the open page makes it reload as before. After each write it reads the chips of the open page. A label that was there before and is now missing ends the batch at once. After the batch, one reload and one readback follow for each row. A failed reload makes the next readback reload again. The journal entry of a row stays open until its readback. A restart marks it `unknown`, and the next pass reads first (D83).

**Kept:** The 60 s wait in the label steps, the identity checks before and after each write, the one selected row, the journal before each write, the two-attempt limit and the halt on a lost label. Rows are only reordered inside one pass, by account.

**Boundary:** The fake mailbox of the tests cannot model page age. The claim that Gmail saves a second write on an aged page rests on D79 (a write 45 s or more after the load persisted). A live measurement must confirm it. If the share of `not_visible` rows rises, revert this change.

## D100. One build holds every known production feature, and each deploy records its source (2026-11-16)

**Cause:** The live binary `64674e90` was built from a clean `news-bot-prod` tree at `330b1d7`. That branch forked at `7db325c`, before the four commits that end at `75adc78`. The binary lacked their strings (`partial_read_retry`, `trash_refused_retry`) while the binary before it had them. The news-bot deploy therefore dropped the redacted-read fix, the Inbox chip fix and the retry of old refusals. The build output (`deck.d`) named the `news-bot-prod` worktree, which is how the source was found.

**Decision:** Merge `news-bot-prod`, then `main`, into the Gmail speed-up line. Keep both decision lines: the bot decisions keep the main numbers D86 to D88 and the Gmail decisions D86 to D97 become D97 to D99. Deploy one build from the merged tree. Record the source commit, the tree, the binary SHA-256 and the web bundle names for each deploy in [gmail-deploy-record.md](gmail-deploy-record.md).

**Reason:** Two branches grew from one base and each deploy came from one branch only. A recorded source per deploy makes the next drift visible at once. The proof of the source is inference from the build output and a clean tree. A rebuild at `330b1d7` was not compared byte for byte.

## D101. The backlog job works on one account at a time (2026-11-16)

**Decision:** The cleanup pass picks one focus account: the first registered account with backlog work left. Backlog work is the walk, a queued thread that waits for triage, and a queued label, topic or Trash write with an attempt left. The pass walks, triages, labels and moves to Trash the backlog rows of that account only. The focus comes from the database, so a restart resumes the same account. New mail of every account is still read and written in the same passes. Every account still gets its ceiling at once, so the cut between old and new mail is fixed early. Scans of all accounts run as before.

**Bounds:** An account whose last scan result is `auth` or `identity_mismatch` never holds the focus. A row that ran out of attempts leaves the focus too, and the status counts it as `unresolved` for its account. The status shows the `focus`, and for each account the `pending` and `unresolved` counts next to the walk state. A complete walk with `unresolved` above zero is not the same as a clean account.

**Kept:** The batch caps, the settled page reuse (D99), the journal, the identity checks, the guards for protected mail and the yield to an owner task. No archive, send or permanent delete.

**Reason:** The owner wants the existing mail finished account by account, in small batches, with the browser free for other tasks. The earlier rotation spent a page settle on each account in every pass.

## D102. A done payment is information, and a money notice is protected (2026-11-16)

**Cause:** One bank notice of a done UPI payment got the model answer `actionable` with kind `receipt` and the Action label. Of 20 stored receipts with that answer, 14 have the label (2 in account A, 12 in account B).

**Decision:** The triage prompt says that a notice of a payment, debit, credit or transfer that already happened is `fyi` with kind `receipt`. The host lowers `actionable` to `fyi` for a receipt only with positive evidence that the payment is done (debited, credited, successful, paid, completed, transferred, "is complete") and no ask of the owner. Asks are the words due, overdue, failed, declined, pending, dispute, action required, blocked, locked, "will be debited", "not successful", verify, confirm, update, click, KYC, outstanding and a few more. Bank boilerplate sentences ("if you did not ...", "no action required", "do not share") are removed before the check. A receipt with no such evidence keeps `actionable`, so the owner sees it. Money words (debited, credited, UPI, transaction, A/C, withdrawn, transfer, EMI) join the protected words, so a promotion with them goes to review and never to Trash.

**Revised 2026-11-16:** The first rule lowered a receipt when none of 17 words was present. Absence of a short word list is no proof that nothing is asked. The rule now needs positive evidence.

**Boundary:** The words are English only. A label that Gmail already holds is not removed: Deck labels are additive. A wrong earlier label stays until the owner clears it or a later decision adds a removal step with the same identity and read-back checks. The audit found no thread with applied labels of two categories.

**Reason:** The label said the owner must act, and nothing needed action. The fix changes new decisions only. The earlier rows stay as stored until reviewed.

## D103. Pause the label and Trash writes with the service config (2026-11-16)

**Decision:** To pause writes, the service config moves `labels` and `cleanup` to a `paused_writes` key. Deck then renders no label steps and no cleanup table. It polls and triages, and writes nothing to Gmail. To resume, move the two keys back and restart at a quiet point. A temporary `boost` overlay (higher caps, saved separately from the normal caps, ended by the service itself) exists in the service wrapper. The owner replaced the cap-only speed-up with D101. The boost stays off.

**Reason:** The owner reported a wrong label. A paused config needs no code and no write to stop the writes. The pause came at a settled boundary: automation pause, no running pass, no unresolved journal entry.

## D104. One exact removal path for a wrong Deck category label (2026-11-16)

**Cause:** One bank notice of a done UPI payment of 500 had the Action label (D102). Deck labels are additive, so no step could remove it.

**Decision:** `scripts/vm-prototype/gmail_label_repair.py` is the only removal path. It handles one account, one thread and one latest message from a private pick file. It writes only `Deck/FYI` (add) and `Deck/Action` (remove). It reads the thread through the print view first. The latest message must be a done payment by the D102 word lists. (Revised by D105: an earlier ask no longer counts as history.) The print view carries no message id, so the list row binds the latest message before and after the read and before each write. Each write reads the real checkbox state first and never repeats blindly. A reload must show the wanted label, no Action and every other chip of the row. The journal is a private append-only file. An open entry after a crash is marked `unknown`, and the next run reads first. Each operation has two attempts. The stored category changes in one transaction only after the proof.

**Result:** The live repair of the single account A candidate ended `applied` for both writes, and the store row is `fyi` with label `applied`. The 14 other stored receipts were not relabeled.

**Boundary:** The tool does not search for other wrong labels. The word lists are English only.

## D105. The label repair refuses when any message of the thread holds an ask (2026-11-16)

**Cause:** `gmail_label_repair.py` treated the latest message as the answer to every earlier one. It also treated an ask in the last 40% of a message as a bank footer. Neither is proven. The helper can remove a label again and again, so a rule that ignores an older open request is unsafe.

**Decision:** The thread passes only when three things hold. The last message is a done payment, uncut, with no ask after the D102 boilerplate cut. No earlier message holds an ask after the same cut. No earlier message is cut or empty. Otherwise the repair refuses with `evidence` and writes nothing. The position of an ask in the text never excuses it. The footer-position check is removed. A refused thread needs a human check of the meaning, or a richer bounded read, before any repair.

**Evidence:** The single live repair of one account A thread (D104) ended `applied` under the earlier rule. That is one result. It does not prove that the latest message decides a thread. This change did not re-read that mail or any other mail. The tests use synthetic text only.

**Boundary:** The production Rust classifier is unchanged. The word lists are English only. The earlier live repair is not undone.

## D106. Dynamic Gmail topic labels from a bounded registry (2026-11-16)

**Cause:** The owner asked for dynamic labels. The fixed topic labels (Receipts, Jobs, Software and AI, Review) cannot name a new type of mail such as travel.

**Decision:**
- The model returns a short topic for each thread, besides the category and the kind. It gets the list of current topics and must reuse one when it fits. The topic is data. The kind keeps deciding the safety rules.
- The host checks the topic: 2 to 24 characters, at most 3 words, letters, digits, spaces and hyphens, no run of 4 digits, no reserved word (category, control and write words). It folds case, spaces and a final `s`. A match with a registry topic reuses that topic. A new name is a new topic. The label is `Deck/` plus the name. There is no fuzzy merge beyond that fold and a small fixed alias list of the four old topics.
- A registry per bot stores the topics. A second table stores, for each account and topic, `creating` or `verified`. A topic exists on an account only where a thread needs it.
- The label is written with two private config steps: `topic_step` applies an existing label, `topic_create_step` creates it from the label menu and applies it to the selected thread. The host puts only the checked label name into a quoted argument. The name never reaches a selector or a program. Creation is the last alternative of one write. It runs only after the apply alternatives failed and a fresh read shows no chip. So a retry always looks first and never creates twice. The write keeps identity checks, the journal, the reload and chip readback, and the lost-label halt.
- A dynamic label never authorizes Trash. `decide` is unchanged. A promotion or newsletter is trashed or reviewed by the same guards. A topic applies to receipt, job, tech and other mail. A topic on `other` mail replaces the review label. The kind stays `other`.
- Limits: `topics_new_per_pass` (default 2) new registry topics and new Gmail labels in one pass, `topics_registry_cap` (default 30) topics in all. An `other` thread that hits a limit or has an invalid topic goes to Review with the reason `topic_limit` or `topic_invalid`. A receipt, job or tech thread then takes its old fixed topic.
- Existing rows stay as they are. Only new triage gets a topic. The registry seeds the four old topics when first used. The owner's own labels are never read, renamed or changed. The new labels live in the `Deck/` namespace.

**Reason:** The owner wants labels to follow the mail. A registry keeps one name for one concept across accounts. Bounds stop a label for each sender. Gmail rejects a duplicate name, so a wrong retry cannot make a second label.

**Boundary:** The registry has no delete or rename. The owner cleans the labels by hand. The word lists are English only.

## D107. Desktop-first redesign on one design system (2026-11-16)

**Decision:** Design for the laptop first. Home is a dashboard with a sidebar. A bot opens in a pane beside Home, so Home stays visible. The bot list changes form with the fleet size: cards up to 6 bots, rows up to 15, a grouped table with a filter above 15. All screens use one design system, recorded in [web/DESIGN.md](../web/DESIGN.md). Its look follows the owner's portfolio: dark first, near black, one red accent, Geist, Geist Mono and Redaction. Surfaces are square. Controls have a 4px corner. Red is the primary button color.

**Reason:** The owner found the mobile-first layout too sparse on a laptop. Three token sets and about 96 raw colors made the old styles inconsistent. The owner chose the portfolio as the reference, dark mode first and a crisp style with motion.

**Scope change:** This replaces the mobile-first direction of the 2026-11-15 mobile flow review. Touch screens keep 44px targets through `pointer: coarse`. The owner approved removal of the old app in `web/src/App.tsx`, which D101 kept. Screens move to the system one at a time, starting with Home. Each move deletes the old styles of that screen.

**Boundary:** This change adds the tokens, the shared parts and a development-only showcase at `web/design.html`. The build does not include the showcase. The current app stays light, but its shadcn controls take the new fonts, the red primary and the 4px corner. No screen layout changes.

**Trial at `/next`:** The new Home ships at `/next`, beside the current app at `/`, so the owner can test it with real bots and ask for changes. It reads and writes through the same API as the current app. The bot pane reuses the current chat, requests and computer views, with a dark style bridge in `web/src/next/legacy.css`. Setup review, Deck conversations and the history list open in the current app. The service worker does not serve `/next`. The project `cn` in `web/src/lib/cn.ts` knows the custom text sizes; the shared `cn` dropped `text-ui` next to a text color.

## D108. Fluid motion kit trial at `/next/kit` (2026-11-16)

**Decision:** Try a soft, animated style before the next rebuild. The page `/next/kit` shows it with real bots and three sample bots. It uses Animate UI (`@animate-ui/react`) for menus, dialogs, tabs, tooltips, switches, highlights and text effects, and Motion for springs and the card-to-pane morph. Corners are round. Controls are pills. The page also shows loading and working states for an AI product. The page is read-only. Nothing on it sends or changes anything.

**Reason:** The owner found the D107 style too sharp. The owner asked for a smooth, friendly and native feel, and for animated components from public libraries.

**License:** Animate UI uses MIT with the Commons Clause. An app may use it. Its components may not be sold or shared on their own. Deck installs it as a package and does not copy its source.

**Boundary:** The kit does not replace `/next` or `/`. The owner picks the parts to keep. The rebuild then follows that choice, and its decision replaces the D107 style.

**Home on the kit (2026-11-16):** The owner approved the kit look. `/next` now uses it with real data: a collapsible sidebar, soft cards, a card that grows into the bot pane, pill tabs, a pill composer with a destination menu, and AI loading states. The shared parts are in `web/src/fluid/`. The pane still reuses the current chat, requests and computer views. `web/src/next/legacy.css` maps their styles to the fluid tokens until they are rebuilt.

**Control room layout (2026-11-16):** The owner compared four layout prototypes and picked the Control room. The `/next` Home is now a grid of tiles: a live screen of a working bot, Needs you, Queued, Awake computers, Bots and Recently done. The live tile follows the pane's privacy rules. It hides the screen while a sign-in or protected input is open, and while the pane already shows that screen. The API has no spend data, so the prototype's spend tile became the Awake tile. The live tile does not rotate on its own, because each change opens a new screen stream. The prototype page `/next/layouts` is removed. The owner approved the result on 2026-11-16. Next: rebuild the pane views (Chat, Requests, Computer) and delete `web/src/next/legacy.css`.
