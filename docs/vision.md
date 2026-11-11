# Vision

## Goal

`deck` is a personal AI agent that feels as smooth as a hosted agent product such as Grok Bot. It is self-hosted, and its owner controls the model, the data and the code.

## User

One owner. Later, maybe a few trusted people. `deck` is not a multi-tenant service.

## What the user does

- The user talks to `deck` in a web app, on a laptop or a phone.
- The user asks for work on the web: read a page, fill a form, log in, collect data, check something every day.
- The user watches the work, or leaves and comes back to the result.

## What `deck` does

- It answers fast. A plain reply starts in about 1 second.
- It knows its own tools. It never says "I cannot" for a tool that it has.
- It works in the `bare` browser. It reads pages as text first and uses screenshots only when it must.
- It sends results into the chat: text, files and screenshots. A screenshot that the user asks for always reaches the chat.
- It keeps memory between chats.
- It runs routines on a schedule.
- It asks before actions that it cannot undo, such as a payment or a sent message.
- When `bare` fails on a site, it files a `bare` issue and tells the user. See [feedback-loop.md](feedback-loop.md).

## Quality bar

- Smooth: no dead waits, no silent failures, and progress on screen while it works.
- Honest: it reports what it did and what failed. It never claims a result that it did not check.
- Small: one service, few dependencies, easy to run on one machine.

## Out of scope for the first version

- Other browsers. `bare` is the only browser, with no fallback.
- A full desktop with arbitrary apps (Xvfb and a window manager, as in Rakazo).
- Group chats between bots.
- A plugin marketplace.
- Native phone apps. The web app must work well on a phone.
