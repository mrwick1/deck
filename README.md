# deck

`deck` is a self-hosted AI agent. It lives in a web app. It does work on the web for you with the [bare](https://github.com/mrwick1/bare) browser. It shows you its work, and it sends you files and screenshots in the chat.

## Status

M1 and the three-stage one-bot desktop prototype are merged. The measured save optimization and benchmark reports are in PR #23, pending merge. See [docs/progress.md](docs/progress.md) for verified scope and open work. Start with [docs/vision.md](docs/vision.md).

## Development

Copy `deck.example.toml` to `deck.toml`. Edit it for your setup.
Set `DECK_MODEL_KEY` in the environment. Never write the key in a file.
Run `cargo run` to start the server. It reads `deck.toml`, or the file in `DECK_CONFIG`.
The server trusts the `Remote-User` header only from the `trusted_proxies` addresses.
Run `cargo test` to run the tests.
For the web app, run `cargo run`, then `npm run dev` in `web/`. Open the address that Vite prints. The dev server proxies `/api` to `127.0.0.1:8790` and sets `Remote-User: dev`.
Run `npm ci` in `web/` once. Run `npm run check` and `npm test` there before you commit.
To build one binary, run `npm run build` in `web/`, then `cargo build --release`. The binary embeds `web/dist`.
Without `web/dist`, `cargo build` embeds a placeholder page.
`bare-mcp` must be on `PATH`, or set `bare.command` in `deck.toml`. The agent starts it on the first browser tool call.
Run `cargo test -- --ignored` to run the live tests. They start the real `bare-mcp`, so it must be on `PATH`.
`tests/screenshot_e2e.rs` is the end-to-end test of M1. A user sends a message in the web app, and the screenshot of the agent shows in the chat. A scripted model replaces the real one. Build `web/dist` first.
A `[vm]` section in `deck.toml` gives the bot its own guest computer. See [docs/stage3-results.md](docs/stage3-results.md).
The one-bot save command caps migration at 512 MiB/s. The CLI accepts `stop --bandwidth-mib 128` for the original cap. Concurrent saves and slow storage remain untested. See [docs/migration-bandwidth-results.md](docs/migration-bandwidth-results.md).
Run `cargo fmt` and `cargo clippy --all-targets -- -D warnings` before you commit.
Run `python3 evals/run.py` to score the agent on a fixed task set with the real model. See [evals/README.md](evals/README.md).

## Rules

- The only browser is `bare`. There is no Chrome, Chromium, Playwright or other browser, and none as a fallback.
- When a site fails in `bare`, `deck` files a `bare` issue and tells the user. Each failure makes `bare` better. See [docs/feedback-loop.md](docs/feedback-loop.md).

## Documents

| File | Contents |
| --- | --- |
| [docs/progress.md](docs/progress.md) | Current status, verification and historical checkpoints |
| [docs/vision.md](docs/vision.md) | Goal, user, scope and quality bar |
| [docs/architecture.md](docs/architecture.md) | Parts of the system and how they talk |
| [docs/decisions.md](docs/decisions.md) | Decisions and their reasons |
| [docs/success-criteria.md](docs/success-criteria.md) | Success criteria, their tasks and their pass rules |
| [docs/feedback-loop.md](docs/feedback-loop.md) | How `deck` makes `bare` better |
| [docs/rakazo-lessons.md](docs/rakazo-lessons.md) | What we learned from Rakazo |
| [docs/inspiration.md](docs/inspiration.md) | What we take from other open-source agents |
| [docs/bare-gaps.md](docs/bare-gaps.md) | What `bare-mcp` must add for `deck` |
| [docs/stage3-results.md](docs/stage3-results.md) | The lean bot on its own computer: gates, measures and gaps |
| [docs/optimization-plan.md](docs/optimization-plan.md) | Experiment order, current status and remaining work |
| [docs/migration-bandwidth-results.md](docs/migration-bandwidth-results.md) | Controlled save-rate comparison and integrated validation |
| [docs/memory-benchmark-results.md](docs/memory-benchmark-results.md) | Memory growth, incomplete repetitions and retained failures |
| [docs/idle-benchmark-results.md](docs/idle-benchmark-results.md) | Connected idle observations and attribution limits |
| [docs/open-questions.md](docs/open-questions.md) | Design questions and their answers |
