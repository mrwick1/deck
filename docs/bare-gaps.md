# What bare-mcp must add for deck

`bare-mcp` was built for end-to-end tests. It runs step journeys and checks results. An agent also needs to read a page and decide what to do. This list is the first set of `bare` issues that `deck` needs.

Checked against `bare-mcp` on 2026-11-11. It has these tools: `run_steps`, `open`, `step`, `save_state`, `ensure_session` and `close`.

## Needed

1. **Page snapshot tool.** Return the page as text: the interactive elements with a role, a name and a locator that `step` accepts, plus the main text. The model reads this instead of a screenshot. Today there is no tool to read a page, only `expect` steps.
2. **Screenshot as a tool result.** Return a PNG of the live page (or its path) from one call. Today `shot NAME` writes a PNG into the out folder inside a step. `deck` needs the path or the bytes in the result.
3. **Read text and values.** Get the text of one element, or of the page, as plain text. Today `expect` only checks a value.
4. **Links and URL.** Return the current URL, the title and the links of the page.
5. **Clear failure reports.** On a failure, say which part failed: network, HTML, CSS, JavaScript (with the error), layout or the locator. `deck` copies this into the `bare` issue.
6. **Many sessions.** One live session per bot, by name. Today a new `open` ends the old session.
7. **Downloads and uploads.** Save a downloaded file to the workspace, and set a file input from the workspace.

## Proposed design

This design comes from four browser agents. See [inspiration.md](inspiration.md).

Snapshot format: one line per element, indented by nesting.

```
[e1] heading "Settings"
[e2] textbox "Username" = "admin"
[e3] button "Save" (disabled)
*[e4] alert "Saved"
```

- `bare` mints each ref. A ref from an old page fails with a clear error.
- `*` marks an element that is new since the last snapshot.
- A long snapshot keeps 80 % from the top and 20 % from the end.

Tools:

| Tool | Purpose |
| --- | --- |
| `snapshot(scope?, mode?, budget?)` | The outline of the page or of one element. `mode` is `interactive` or `all`. |
| `find(role?, name?, text?, within?)` | Elements that match, without a full snapshot. |
| `read(ref?, offset?, max_chars?)` | Page or element text, in pages. |
| `screenshot(ref?)` | A PNG path of the page or of one element. |
| `step` | Accepts a ref as a locator. Returns a receipt: navigation, new elements, downloads. |

Each error has a code, the failed part and a hint for the next step.

## Later

- Tabs or windows.
- A stream of page captures for the live view in the web app.

## Status (2026-11-12)

`bare-mcp` now has the tools of the proposed design, and more. See [progress.md](progress.md) for the list and the open gaps.
