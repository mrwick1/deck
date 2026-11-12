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

## D7. The web app is a Svelte SPA (2026-11-11, replaced by D19)

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

## D19. The web app is a React SPA on shadcn/ui and assistant-ui (2026-11-12)

**Decision:** The web app uses React, Vite, Tailwind CSS and shadcn/ui. The chat uses assistant-ui components. Motion, Vaul and Sonner give animations, a swipe drawer and toasts. The app is phone first and works on a desktop too. It is still a static SPA that the service serves. This replaces D7.

**Reason:** The owner wants the app to feel native and fluid. React has the largest set of ready-made components and animation libraries. assistant-ui already has streaming text, tool-call cards, reasoning blocks, a thread list and auto-scroll. Its external-store runtime takes the deck event stream with no change to the service.

**Look:** The colors, type, work-log rows, composer panels and image zoom follow T3 Code. T3 Code uses the MIT license. Ported files name it in their header, and `web/THIRD_PARTY.md` holds its license.

**Cost:** The script bundle grows from about 30 kB to about 370 kB gzip.

**Blocker:** `@assistant-ui/react` needs `TransformStream`, which `bare` does not have yet ([bare#389](https://github.com/mrwick1/bare/issues/389)). Until then, the app does not render in `bare`, and the screenshot end-to-end test fails.
