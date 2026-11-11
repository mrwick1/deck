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
