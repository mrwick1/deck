# Architecture

This is the first design. Parts marked "open" are listed in [open-questions.md](open-questions.md).

```
user (web app) ──HTTPS + live stream──> deck service
                                          │
                                          ├── agent loop ⇄ model endpoint (Claude, OpenAI-compatible or Anthropic API)
                                          │
                                          ├── browser tools ──> bare-mcp ──> bare (headless)
                                          ├── file tools ──> workspace folder
                                          ├── shell tool ──> sandbox (open)
                                          ├── memory store
                                          └── routines (scheduler)
```

## Parts

### Web app

- Chat with streaming replies.
- A live view of the bot's work: the steps it runs and the latest page capture.
- File and image messages in both directions.
- Approval cards for actions that need a yes.
- It must work well on a phone browser.

### deck service

One process. It holds the agent loop, the tools, the store and the scheduler. It serves the web app and its API.

### Agent loop

1. Build the prompt: system instructions, tool list, memory and recent chat.
2. Call the model with tools.
3. Run the tool calls. Stream each step to the web app.
4. Repeat until the model gives a final answer.

The system instructions must name every tool and when to use it. Example: "When the user asks for a screenshot, take a `shot` in `bare` and send it with `send_file`." Rakazo lacked this line. See [rakazo-lessons.md](rakazo-lessons.md).

### Model access

The service calls a model endpoint with an API key from its configuration. The owner's setup uses a local proxy with an OpenAI-compatible API (`/v1/chat/completions`) in front of a Claude subscription. That proxy handles tool calls and image input. The endpoint and the key are configuration. They are never in the code.

### Browser: bare

- `deck` starts `bare-mcp` and talks to it over MCP (stdio).
- It uses the `bare-mcp` tools: `open`, `step`, `run_steps`, `save_state`, `ensure_session` and `close`.
- Steps use the `bare` step language: `click`, `type`, `press`, `scroll`, `expect`, `shot` and others. Locators are role, label, text, placeholder or CSS.
- `shot NAME` writes a PNG. `deck` sends that PNG to the model when the model must see the page, and to the chat when the user asks for it.
- Saved sessions keep logins between tasks.
- `bare-mcp` needs new tools for an agent. See [bare-gaps.md](bare-gaps.md).

### Workspace and shell

- Each bot has a workspace folder for its files.
- A shell tool, if added, runs in a sandbox (open: container or other).

### Memory

- A short notes file per bot, which the bot edits itself, one fact per line.
- Optional search over past chats later.

### Routines

- A routine is a stored prompt with a schedule.
- Each run is a normal agent run, with its result in the chat.

## Data

- One local database for chats, runs, memory and routines (open: SQLite or other).
- Files in the workspace folder.
- Secrets in the configuration or a vault, never in the database in plain text.
