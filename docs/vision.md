# Vision

## Goal

`deck` is a personal AI agent that feels as smooth as a hosted agent product such as Grok Bot. It is self-hosted, and its owner controls the model, the data and the code. The owner runs several independent bots. Each bot owns a persistent desktop computer (D21).

## User

One owner. Later, maybe a few trusted people. `deck` is not a multi-tenant service.

## What the user does

- The user talks to `deck` in a web app, on a laptop or a phone.
- The user asks for work on the web: read a page, fill a form, log in, collect data, check something every day.
- The user creates bots for distinct purposes, such as email or job hunting. Each bot has its own accounts, files, memory, instructions and permissions (D21, D23).
- The user watches the work, or leaves and comes back to the result.
- The user changes a bot's permissions at any time (D23).

## What `deck` does

- It answers fast. A plain reply starts in about 1 second.
- It knows its own tools. It never says "I cannot" for a tool that it has.
- It works in the `bare` browser. It reads pages as text first and uses screenshots only when it must.
- It sends results into the chat: text, files and screenshots. A screenshot that the user asks for always reaches the chat.
- It keeps each bot's memory between chats. The owner can inspect, correct and delete it (D33).
- It starts work from user messages, schedules and configured events (D24).
- It follows each bot's permissions. It asks the owner for approval when a permission requires it, such as a payment or a sent message. Permissions are trusted instructions, not hard technical barriers (D14, D31).
- It pauses a bot at its spending budget. It resumes only after the owner approves (D34).
- It puts requests, results and failures in one inbox inside Deck (D30).
- When `bare` fails on a site, it files a `bare` issue and tells the user. See [feedback-loop.md](feedback-loop.md).

## Quality bar

- Smooth: no dead waits, no silent failures, and progress on screen while it works.
- Honest: it reports what it did and what failed. It never claims a result that it did not check.
- Small: one host service, one small executor in each bot's computer, few dependencies, and a setup that one owner can run.

## Out of scope for the first version

- Other browsers. `bare` is the only browser, with no fallback.
- Other browsers inside desktop apps. A desktop app with its own embedded browser is not approved.
- Group chats between bots. Bot-to-bot delegation is not part of this design (D21).
- A plugin marketplace.
- Native phone apps. The web app must work well on a phone.
- Phone push notifications in the first implementation. Notifications stay inside Deck for now (D30).

A full desktop per bot is in scope (D21). The display path is not chosen. See [architecture.md](architecture.md).
