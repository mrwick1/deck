# deck

`deck` is a self-hosted AI agent. It lives in a web app. It does work on the web for you with the [bare](https://github.com/mrwick1/bare) browser. It shows you its work, and it sends you files and screenshots in the chat.

## Status

Design only. There is no code yet. Start with [docs/vision.md](docs/vision.md).

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
| [docs/bare-gaps.md](docs/bare-gaps.md) | What `bare-mcp` must add for `deck` |
| [docs/open-questions.md](docs/open-questions.md) | Design questions and their answers |
