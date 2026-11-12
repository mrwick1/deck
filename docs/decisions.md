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

## D21. Each bot owns a persistent desktop computer (2026-11-12)

**Decision:** The owner can create independent bots for distinct purposes. Examples include email, job hunting and social media. Each bot owns a persistent virtual machine with a full desktop. Each bot has its own accounts, files, memory, instructions and model configuration. Deck manages the computers and their lifecycle.

**UI:** Each bot has a dedicated view with chat, desktop display, screenshots, activity and controls. Human control of the desktop can wait. The architecture must support it later.

**Reason:** Each bot needs its own working environment and account boundary. Bots are independent workers. Delegation between bots is not the basis of this design.

**Scope change:** Full desktops replace the desktop exclusion in the original vision. `bare` remains the only browser. The VM backend and desktop transport remain open. This decision does not select Xvfb or VNC.

## D22. Bots can use desktop tools, shell commands and APIs (2026-11-12)

**Decision:** A bot can operate its desktop, use `bare`, run shell commands and call service APIs. Its permissions govern each route.

**Reason:** A bot must use the tools that fit its work. Desktop interaction alone is not required.

**Scope change:** D13 described a host workspace shell sandbox. The new design places the bot's computer behind a VM boundary. The shell sandbox policy inside the VM remains open.

## D23. Permissions are dynamic and belong to each bot (2026-11-12)

**Decision:** The owner can change each bot's permissions at any time. Permissions describe actions, such as reading messages, sending messages or submitting job applications automatically. A bot can request a missing permission. The owner can approve one action or grant ongoing permission with limits. Revocation blocks future actions during an active run too.

**Enforcement:** Deck must enforce permissions across desktop, browser, shell and API tools. A denied action must not become available through another tool.

**Reason:** Different bots need different levels of autonomy. Permissions must change as the owner changes the bot's duties.

**Scope change:** This extends D14. The enforcement mechanism remains open. A model flag alone does not establish enforcement across all tools.

## D24. Bots can react to configured events (2026-11-12)

**Decision:** Bots can start work from user messages, schedules and configured event subscriptions. Monitoring depends on the bot's duties and the owner's instructions. Examples include incoming email and job alerts.

**Reason:** Bots must do ongoing work without a new user message for each task.

**Open:** Verify the event sources available for each service. APIs, notifications and browser polling are possible approaches. No specific service integration is selected yet.

## D25. Pause automation and sleep computer are separate controls (2026-11-12)

**Decision:** Pause automation stops new work and automatic wake-ups. Incoming events remain queued. Sleep computer saves the VM state to disk and stops the VM to release CPU and RAM. Its persistent disk remains stored. Resume restores the saved computer state.

**Wake policy:** Sleeping leaves automation enabled. An event or schedule can wake the computer. Pausing automation overrides automatic wake-ups.

**Reason:** The owner needs to save resources without losing the bot's environment. The owner also needs a separate control that prevents automatic work.

**Proposed mechanism:** A lightweight event listener outside the VM queues events and requests wake-ups. A source that needs a running desktop may require periodic wakes or an awake computer. This mechanism needs validation.

**Limits:** Saved state consumes disk space. External connections and expired logins may need recovery. Save, restore and crash recovery need a prototype. Stop behavior for an action already in progress remains open.

## Discussion tracking

Append agreed decisions here as the architecture discussion continues. Keep proposals and open implementation questions separate from agreed behavior. D21–D25 describe the target architecture. They do not claim that these features exist in the current code.

## D26. Create bots through a conversation (2026-11-12)

**Decision:** The owner describes a bot's purpose in plain language. Deck prepares a setup for review. The setup includes the name, instructions, accounts to connect, permissions, approval rules, event subscriptions, schedules, model and resource limits.

**Flow:** The owner approves the setup. Deck then creates the bot's computer and helps connect the accounts. The owner can change the configuration later.

**Reason:** A conversation is enough for the first setup flow. Templates can come later.

## D27. The bot signs in through secure credential prompts (2026-11-12)

**Decision:** Port the secure credential input mechanism from T3 Code. The bot requests the email address and password through that mechanism. It then fills the sign-in fields securely and completes the sign-in flow. The owner does not take over the desktop.

**Security boundary:** Credential values must bypass model context, chat history and ordinary tool logs. The model can refer to protected values without reading them. Field entry must not return the values to the model. Screenshots and page reads during sign-in must not expose protected values.

**Reason:** The bot must complete account setup without general desktop takeover or passwords in chat.

**Open:** Inspect the T3 Code implementation before selecting the port. Define credential storage, secret lifetime and recovery for failed sign-ins. Define handling for MFA, passkeys and other verification challenges. These details are not settled by this decision.

## D28. The bot coordinates service-specific sign-in verification (2026-11-12)

**Decision:** The bot handles MFA, OTP, 2FA and similar verification flows for each service. It identifies the next step, asks the owner for the required input or action, and continues the sign-in flow. General desktop takeover is not required.

**UI:** Build service-specific verification UI as each integration is added. Use protected input for verification codes and other secrets. For phone approval or number matching, show the service's instructions and the required number. Ask the owner to complete the phone action. The bot handles the website steps and checks whether sign-in succeeded.

**Reason:** Verification differs between services. The bot must coordinate each flow while the owner supplies the required proof.

**Scope:** This settles the verification interaction left open in D27. It does not assume that every challenge can be automated. Unsupported challenges must report a clear blocked state. Credential storage and secret lifetime remain open.

## D29. Bots keep a persistent task queue (2026-11-12)

**Decision:** Each bot keeps a persistent task queue. One task controls its desktop at a time. Related events can join a pending task, such as several emails in one summary. A task waiting for owner input stays visible. Unrelated work can continue only if it does not disturb the waiting task's session.

**UI:** Show Working, Waiting for you, Queued and Completed states. Provide controls to cancel tasks and change their priority. Preserve failure details for tasks that fail.

**Message priority:** The owner chooses priority when sending a message. A message can take priority over queued background work. The default priority and exact controls remain open. Queue priority does not imply interruption of a task already running.

**Reason:** Bots must handle ongoing events and direct requests without competing for desktop control. The owner must control which queued work comes first.

**Open:** Define safe handling of concurrent API work, task dependencies and interruption of running work.

## D30. Start with an inbox inside Deck (2026-11-12)

**Decision:** Provide one inbox inside Deck across all bots. It contains approvals, sign-in requests, failures and results. Each item opens the relevant bot and task. Requests remain until resolved, even when the owner dismisses a notification.

**Settings:** Each bot has notification settings. Support immediate in-app alerts for requests that need attention, scheduled summaries for routine results and silent progress updates.

**Scope:** Start with notifications inside Deck. Defer phone notifications. This replaces the phone push requirement in D14 for the initial implementation.

**Reason:** The owner chose to keep notifications inside Deck for now.

## Architecture review: open questions (2026-11-12)

These findings are design concerns and recommendations. They are not new agreed decisions or verified implementation failures.

1. **Permission enforcement:** D22 permits raw desktop, browser and shell access. D23 requires action permissions across every route. Raw access to a signed-in account can bypass a typed action gate. Define which guarantees Deck can enforce and which actions require trust in the bot. Consider host-controlled typed actions with credentials outside the VM.
2. **Secret ownership:** Secure entry does not protect session cookies from a shell in the same VM. VM disks and saved memory can contain credentials. Define storage protection, credential access and token refresh. Event listeners outside sleeping VMs also need an explicit credential boundary.
3. **Recovery:** Keep external action records outside VM saved state. Record intent and outcome. A crash can leave an action's outcome unknown. Reconcile uncertain outcomes before retrying. A normal save and resume does not inherently replay an action; rollback or retry can create duplicates.
4. **Desktop display:** Define how bare's browser output appears in the bot's desktop view. Preserve the only-browser rule. Do not assume desktop applications with embedded browsers are permitted.
5. **Task ownership:** Define whether waiting tasks retain exclusive access to their browser sessions. Do not assume unrelated tasks can safely share a profile.
6. **VM feasibility:** Prototype one VM before selecting a backend. Measure memory, save and restore time, session recovery and desktop capture. Define resource limits and network access.
7. **Documentation:** Reconcile the original vision and architecture with D21–D30 before implementation. The older documents still describe the earlier scope.

**Recommended next discussion:** Set the trust boundary first. Then define external action recovery. Next select and prototype the VM runtime. Keep these recommendations separate from the agreed product requirements until the owner decides.

## D31. Action permissions are trusted instructions (2026-11-12)

**Decision:** Action permissions are instructions that the owner trusts the bot to follow. The bot must follow the current permissions, request approval when required and respect revocation during a run.

**Boundary:** Deck does not guarantee that a denied action is technically unreachable through desktop, browser, shell or API access. A bot with raw access to a signed-in account may have the technical ability to perform that action. The owner accepts this trust model.

**Scope change:** This replaces the cross-tool enforcement guarantee in D23. Dynamic permissions, one-time approvals and ongoing grants remain part of the design. VM isolation between bots and protected credential input remain separate requirements.

**Reason:** The owner chose trusted instructions instead of hard technical barriers for action permissions.

## D32. Investigate uncertain outcomes before asking the owner (2026-11-12)

**Decision:** The bot investigates failures and attempts recovery within its permissions before asking the owner. If it cannot establish an action's outcome, it asks through the Deck inbox.

**Recovery rules:** Retry actions that are safe to repeat, with a retry limit. If the service clearly rejects an action, correct the problem and retry within the bot's permissions. If a timeout or crash leaves a send or submission uncertain, check the service for evidence before retrying. Do not repeat an uncertain external action without resolving its outcome or obtaining the owner's direction.

**Persistence:** Deck stores task progress and action records outside the bot's VM. Restoring the computer must not erase Deck's record of attempted actions or known results.

**Reason:** The bot should resolve recoverable problems itself. It must avoid duplicate messages, applications and other external actions.

**Open:** Define retry limits, evidence required to resolve an uncertain outcome and the action record format.

## D33. Bots save useful facts and lessons automatically (2026-11-12)

**Decision:** Each bot keeps its own persistent instructions, preferences and learned facts between tasks. It saves useful facts automatically. It also records lessons from completed work and corrections to improve later work.

**Owner control:** The owner can inspect, correct and delete the bot's memory. Bots do not share memory automatically. Credentials remain in the protected secret store.

**Boundary:** Learned facts and lessons do not grant permissions or override explicit owner instructions. New permissions still follow the approval model.

**Reason:** The owner wants each bot to improve through use without requesting approval for each memory entry.

**Open:** Define memory storage, retrieval, provenance and correction handling. Decide whether reusable procedures need a separate skill format.

## D34. Limit resources and pause at the spending budget (2026-11-12)

**Decision:** Configure CPU, RAM, disk and model usage limits per bot. Set overall resource limits for Deck. Queue work when capacity is full. Idle computers can sleep automatically.

**Spending:** When a bot reaches its spending budget, pause its work and notify the owner inside Deck. Resume only after explicit owner approval. No approval means no continuation. Events, schedules and incoming work must not bypass the budget pause.

**Reason:** The owner must control resource use and spending. Reaching a budget must not lead to automatic extra spending.

**Open:** Define budget periods, usage accounting, in-flight request handling and the scope of a continuation approval. Define resource defaults and idle sleep timing.

## D35. Deck runs the agent loop outside the bot's VM (2026-11-12)

**Decision:** Deck runs the agent loop and calls the model. It stores tasks, memory, permissions and action records outside the bot's VM. A small executor inside each VM operates the desktop, bare, shell and local files. The VM keeps its applications and signed-in sessions.

**Reason:** Deck must retain task history and manage the computer while the VM sleeps or recovers from a failure.

**Open:** Define the executor protocol, authentication and connection recovery. Select a VM backend after a prototype checks desktop capture, sleep, restore and resource use. The owner has approved the runtime split. This decision does not start the prototype.

## D36. Show chat by default and the computer screen when needed (2026-11-12)

**Decision:** The bot's main view shows chat by default. Do not keep the desktop visible during ordinary conversation. Show the computer screen when the agent needs it for browsing or other desktop work.

**Reason:** The owner wants a focused chat experience. The computer view should appear when it supports the current work.

**Scope:** This changes presentation only. Each bot still owns a full desktop VM under D21. Human takeover remains deferred.

**Open:** Define when the screen closes and how it appears on a phone. Define how bare's browser output reaches this view.

## D37. Show work status and support stop, steering and queued messages (2026-11-12)

**Decision:** Show what the bot is doing, how it is doing the work and its current status. Show concrete activity such as searches, browser steps and tool results as work proceeds. Present observable actions and concise explanations, not private model reasoning.

**Controls:** The owner can stop the bot's action and steer its current work. The owner can also queue messages for the bot to pick up after it finishes its task. Make steering current work and queuing later work distinct choices. Queued work keeps the owner-selected priority from D29.

**Reason:** The owner needs visibility and control during execution, without forcing every message to interrupt the current task.

**Limits:** Stop cannot undo an external action already submitted. Resolve uncertain outcomes under D32.

**Open:** Define stop acknowledgements, steering delivery points and the exact activity and message controls.

## D38. The home screen shows activity across all bots (2026-11-12)

**Decision:** When the owner opens Deck, show what all bots are doing. The home screen is an activity overview across bots. Opening a bot leads to its chat-first view under D36.

**Reason:** The owner wants to see ongoing work across bots before entering a conversation.

**Open:** Choose the overview layout, activity detail and status controls through UI discussion.

## D39. Compare interactive UI prototypes before choosing a layout (2026-11-12)

**Decision:** Build interactive prototypes and evaluate how they feel before selecting the UI layout. Compare alternatives with the same sample bots and work.

**Reason:** The owner wants to assess ease of use through interaction.

**Prototype:** `prototypes/bot-overview.html` compares cards, an activity feed and a split overview. It uses sample state only. No layout is selected by this prototype.

## D40. Use the card layout with a more active bot presentation (2026-11-12)

**Decision:** Use the card layout for the bot overview for now. Redesign its presentation so it feels like bots at work rather than generic cards. Use subtle animation and activity details to convey state.

**Reason:** The owner prefers the card layout after comparing prototypes. The visual design still needs refinement.

**Prototype direction:** Reduce box styling. Give each bot a small animated presence. Keep status and current activity readable without motion. Respect reduced-motion preferences. These visual treatments remain experiments.

## D41. Keep the current overview prototype as the starting UI (2026-11-12)

**Decision:** Keep the current bot overview prototype as the starting UI. Defer further visual redesign.

**Reason:** The owner accepts the current direction for now. This does not finalize the visual design.

## Two-round implementation review: recommendations (2026-11-12)

These are review recommendations. They do not authorize implementation or change the agreed product requirements.

### Common ground

- Prove one configuration-defined full desktop VM before building conversational provisioning. Test bare output, desktop capture and input, artifact delivery, saved-state resume and crash recovery.
- Consider direct QEMU/KVM first. Compare libvirt if lifecycle problems justify it. Backend suitability and performance remain unverified. Do not build two interchangeable backends before a concrete need exists.
- Keep the host agent loop and guest executor split from D35. Keep bare MCP inside the guest. Transfer screenshots and downloads explicitly into host artifact storage. A remote MCP relay alone does not resolve guest file paths.
- Keep VM lifecycle, automation pause, task state and budget hold separate. Neither events nor schedules may bypass a hold.
- Store tasks, action intents, results and restore epochs outside the VM. Reconcile in-flight work after a host restart. Preserve late replies and known results.
- Let the host advance the restore epoch. Reject stale responses and connections. Do not trust a restored guest identifier as fresh execution identity.
- Distinguish confirmed success, confirmed failure and unknown outcomes. Normal restore does not inherently repeat actions. Re-dispatch and rollback can. Investigate unknown external effects under D32 before retrying.
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

## D42. Define pass criteria before the VM prototype (2026-11-12)

**Decision:** Use `vm-prototype.md` as the acceptance checklist for the first desktop VM prototype. Prove one configuration-defined computer before building the full provisioning flow.

**Reason:** The owner requested concrete acceptance criteria after the two-round implementation review.

**Status:** The checklist separates correctness gates from provisional performance targets. The numerical targets are proposals for the experiment, not measured results or final product limits. No prototype has run under this decision.

## D43. Approve the first VM prototype checklist (2026-11-12)

**Decision:** The owner approves the checklist and initial experiment targets in `vm-prototype.md`. Use them to evaluate the first VM prototype. The targets remain experiment criteria, not final product limits.

**Reason:** The owner reviewed and accepted the proposed criteria.

**Status:** No prototype results exist yet. Checklist approval does not authorize deployment.

## D44. Use Arch Linux for bot computers (2026-11-12)

**Decision:** Use Arch Linux as the guest OS for bot VMs. Do not use the Omarchy distribution for the current prototype. Build a minimal full desktop image with only the packages required for the prototype. Keep bare as the only browser.

**Reason:** The owner chose to continue with Arch after discussing Omarchy. The image must remain reproducible and meet the approved prototype criteria.

**Status:** The compositor and exact guest package set remain implementation choices. No performance comparison with Omarchy has run.

## Stage 1 verification result (2026-11-12)

This is measured evidence, not a new product decision. A separate implementation worktree contains the desktop VM harness and `docs/vm-stage1-results.md`. The parent reproduced five saved-state cycles on a copy of the disk. The unsaved editor buffer, open bare page, fixture login and files survived every cycle. Measured save and restore times remained within the approved initial targets. No prototype VM or fixture remained running after verification.

A kill and cold boot preserved files but lost the tested volatile editor and browser state. The prototype does not prove account login persistence after a crash. The guest control path uses a root guest agent for this experiment. Stage 2 must define the product executor boundary.

The parent added failing regression tests, then fixed duplicate-start handling and result validation. The parent also added failure cleanup and a portable runtime default. Nine unit tests, compilation and syntax checks pass.

Only Stage 1 feasibility is accepted by this verification. The complete prototype checklist remains open. Authenticated executor readiness, cancellation, host action records, restore fencing, Deck UI integration and two-bot isolation have not been tested. Boot and idle memory need more samples. End-to-end display latency remains untested.

## D45. Implement and measure five resource optimizations (2026-11-12)

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
