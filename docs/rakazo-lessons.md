# Lessons from Rakazo

[Rakazo](https://github.com/elie222/rakazo) is an open-source, self-hosted agent platform. We installed v0.9.0 on 2026-11-11, tested it, and removed it the same day. Its repository stays our reference.

## How Rakazo works

- **Services:** `api`, `worker`, `web`, `supervisor`, `postgres` (Docker Compose), and one computer container for each workspace.
- **Agent loop:** a library called Pi runs in the `worker`. It calls the model with a tool list. The model never runs inside the computer.
- **Computer:** the `supervisor` holds the Docker socket. On the first graphical tool call, it starts a container from the `rakazo/computer` image (Debian 12, 2 GB RAM and 2 CPUs by default). The container runs:
  - `Xvfb :1` (a virtual display, 1280x800),
  - `fluxbox` (a window manager),
  - `chromium` with one profile per bot,
  - `rakazo-computer-control` (a Python service for clicks, keys and screenshots),
  - `agent-browser` (drives Chromium through DevTools, for page text and element refs),
  - `x11vnc`, `websockify` and noVNC (the live view in the web app).
- **Tools:** `computer_observe` (a screenshot for the model), `computer_act` (clicks and keys), `browser_navigate`, `browser_snapshot` and `browser_act` (page text and refs), `shell`, file tools, and `attach_file` (sends a workspace file to the chat).
- **Safety:** approval modes (Ask, Auto with a checker model, Never), allow and block lists, and a spending limit. A host firewall rule stops computers from reaching private networks.
- **Memory:** a `MEMORY.md` per bot, up to 4 KB, that the bot edits itself, plus shared memory per space.
- **Routines:** schedules, email, webhooks and Slack messages can start a run.

## What we measured

On one laptop, through a local Claude proxy, with Sonnet:

| Task | Time |
| --- | --- |
| Page load of the web app | about 0.7 s |
| Plain reply ("pong") | about 1.5 s, of which the model call was 1.1 s |
| Open a page in the browser and report its heading | about 11 s, with a cold start of the computer container |

## What went wrong

- The user asked "can you send a screenshot". The bot took one, and then said that it could not attach it to the chat. That was false.
- The cause: screenshots from `computer_observe` go only to the model. No line in the system instructions tells the bot to save a screenshot as a file and send it with `attach_file`. The tool description of `attach_file` was clear, but the model did not connect the two.
- With an explicit request ("save it as a PNG and send it with attach_file"), it worked.

## What we take into deck

- Name every tool in the system instructions, with the user requests that need it.
- Make a screenshot a file the bot can send. Do not keep model-only images.
- Keep the agent loop outside the browser and the sandbox.
- Show progress steps in the chat while the bot works.
- Ask before actions that cannot be undone.
- Keep the system small. Rakazo's ten services and a desktop per workspace are too much for one owner.

## What we do not take

- Chromium, Xvfb, VNC and the full desktop. `deck` uses only `bare` (decision D2).
- Group chats between bots, the plugin store and billing.
