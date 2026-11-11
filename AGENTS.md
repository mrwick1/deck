# Agent instructions for deck

Read [README.md](README.md) and [docs/decisions.md](docs/decisions.md) first.

## Hard rules

- The only browser is `bare` (`mrwick1/bare`, through `bare-mcp`). Never add Chrome, Chromium, Playwright, Puppeteer, Selenium, Xvfb, a VNC desktop or any other browser. Never add one as a fallback.
- When `bare` fails, file a `bare` issue as [docs/feedback-loop.md](docs/feedback-loop.md) describes. Do not work around the failure in `deck`.
- This repository is public. Never commit IP addresses, host names, account names, email addresses, tokens, keys or cookies.
- Model endpoints and keys are configuration. Never put them in code or in tests.

## Workflow

- Never commit on `main`. Each change goes through a branch and a pull request.
- Record each new decision in `docs/decisions.md`, with its date and reason.
- Write documents in short sentences, in the active voice, with one idea per sentence.
