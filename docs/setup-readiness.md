# Setup readiness probe

Date: 2026-11-13.
Base: `fa27294`.

## Problem

The `Boot` and `InstallExecutor` steps waited only for the guest agent. The live baseline showed that the first `browser_open` failed with "bare-mcp is not running in the desktop session". A later snapshot showed bare-mcp at 12 s of uptime in a guest at 24.9 s. The cause is a startup race. This is an inference. No engine bug is proven.

## Mechanism

`guest/bare-session` makes the FIFO and starts `bare-mcp`. `guest/bare-call` already sends JSON-RPC to that FIFO. The probe reuses that path.

1. `bare-call --ready [seconds]` runs as user `deck`. It checks that a reader holds the FIFO. It sends `initialize` (once per session, through the existing marker) and `tools/list`. It waits at most `seconds` in total.
2. It reads the guest identity file. It prints one JSON line.
3. `vmproto.py ready --bot ID [--timeout S]` runs the probe through the root QGA channel as `as-deck /usr/local/bin/bare-call --ready 8`. It polls every 0.5 s inside the deadline `S` (default 60). It also checks `systemctl is-active deck-executor.service`.
4. `HarnessProvisioner` runs `ready` as the last part of `InstallExecutor`, for a fresh install and for an existing `executor.json`. The deadline is `limit - 5 s`, at most 120 s.

A process name or a FIFO alone never counts. A real `tools/list` answer with at least one tool counts.

## Contract

Guest, stdout, one line:

| Result | Exit | Line |
| --- | --- | --- |
| Ready | 0 | `{"ready":true,"tools":N,"bot_id":"…","guest_id":"…"}` |
| Not ready | 3 | `{"ready":false,"reason":"no_session\|no_answer\|bad_reply\|no_identity\|error"}` |

Host, `vmproto.py ready`: stdout is one line. On success it is `{"ok":true,"bot_id","guest_id","tools"}` with exit 0. On failure it is `{"ok":false,"error":CODE}`, the code alone goes to stderr, and the exit is 1.

Codes: `no_binding`, `bot_mismatch` (host binding differs from `--bot`, no guest call), `identity_mismatch` (guest identity differs from the binding), `not_ready` (deadline passed).

Outcome in `setup.rs`:

- Success: `Done`.
- `identity_mismatch` or `bot_mismatch`: `Unknown`. The owner resolves it. Nothing retries it.
- Any other failure or a timeout: `Retry("the desktop is not ready: …")`. It costs one attempt. The probe reads only, so a retry is safe. After the attempts are used, the setup is `Exhausted`. It never becomes `Ready`.

An existing `executor.json` still passes the bot and guest ID shape check first. The step is not done until the probe passes.

## What the probe never does

It opens no page and takes no screenshot. It reads no credential. It sends no `tools/call`. It starts no second session or browser. It passes no value in argv. It prints only stable codes, never guest output. A repeated probe writes nothing except the one `init` marker that every relay call already writes.

One related fix: `bare-call` now opens the request FIFO without blocking. A FIFO with no reader now fails at once. It used to hang.

## Fake guests

A fake `vmproto.py` must answer `ready --bot ID --timeout S` and exit 0 when the desktop is ready. A fake that exits non-zero for unknown commands makes setup not ready. This is intended. Do not accept unknown commands as ready. `setup_secrets_guest.py` and other fixtures are owned elsewhere and need this command.

## Seams, not changed here

- A Ready bot that restarts does not rerun its Done steps. The runtime path (`provisioning.rs`, executor client) must call `vmproto.py ready` after a `restore` or cold start. That file is not owned by this change.
- A restored snapshot carries the old session in RAM. It answers the probe when the session lives. A stale session that does not answer is reported as `not_ready`. No cold boot happens silently.
- Hung guest probe commands are bounded by the QGA wait on the host. The guest process then ends on its own deadline.
- Not tested: a real VM, real QGA, real bare-mcp, and real timing of the race.
