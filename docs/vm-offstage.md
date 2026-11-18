# The [vm] guests and offstage

Status: later. No work has started.

## Now

Without `[vm]`, deck reaches `bare` through `offstage mcp` (D95). With `[vm]`, each guest still runs `bare-mcp` directly:

- `scripts/vm-prototype/guest/bare-session` starts `bare-mcp` with no arguments in the desktop session of the guest.
- `scripts/vm-prototype/guest/bare-call` relays one request from the deck executor to that process.
- Each deploy builds `bare-mcp` from the newest `bare` main and installs it in every guest (D93).

## Goal

offstage is the only way an agent reaches `bare`. The guests must also use it.

## Open work

1. Install `offstage` in the guest image next to `bare-mcp` and `bare-drive`.
2. Start `offstage mcp` in `bare-session`, not `bare-mcp`.
3. In `bare-call`, prefix each tool name with `browser_`. Drop the other offstage tools from `tools/list`.
4. Keep a failed report as a report. offstage marks it as an error.
5. Decide where offstage screens fit. The guest already has its own desktop (sway).
6. Replace the `bare-mcp` hash check of D93 with a check of both binaries.
7. Measure guest memory and time to ready before and after. offstage adds one process.
