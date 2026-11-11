# deck

`deck` is a self-hosted AI agent. It lives in a web app. It does work on the web for you with the [bare](https://github.com/mrwick1/bare) browser. It shows you its work, and it sends you files and screenshots in the chat.

## Status

M1 in progress. Start with [docs/vision.md](docs/vision.md).

## Development

Copy `deck.example.toml` to `deck.toml`. Edit it for your setup.
Set `DECK_MODEL_KEY` in the environment. Never write the key in a file.
Run `cargo run` to start the server. It reads `deck.toml`, or the file in `DECK_CONFIG`.
The server trusts the `Remote-User` header only from the `trusted_proxies` addresses.
Run `cargo test` to run the tests.
Run `cargo fmt` and `cargo clippy --all-targets -- -D warnings` before you commit.

## Rules

- The only browser is `bare`. There is no Chrome, Chromium, Playwright or other browser, and none as a fallback.
- When a site fails in `bare`, `deck` files a `bare` issue and tells the user. Each failure makes `bare` better. See [docs/feedback-loop.md](docs/feedback-loop.md).

## Documents

| File | Contents |
| --- | --- |
| [docs/vision.md](docs/vision.md) | Goal, user, scope and quality bar |
| [docs/architecture.md](docs/architecture.md) | Parts of the system and how they talk |
| [docs/decisions.md](docs/decisions.md) | Decisions and their reasons |
| [docs/feedback-loop.md](docs/feedback-loop.md) | How `deck` makes `bare` better |
| [docs/rakazo-lessons.md](docs/rakazo-lessons.md) | What we learned from Rakazo |
| [docs/inspiration.md](docs/inspiration.md) | What we take from other open-source agents |
| [docs/bare-gaps.md](docs/bare-gaps.md) | What `bare-mcp` must add for `deck` |
| [docs/open-questions.md](docs/open-questions.md) | Design questions and their answers |
