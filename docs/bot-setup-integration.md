# Bot setup module: integration seam

Status: implementation slice. Nothing calls `src/bot/setup.rs` yet. It does not create a bot end to end.

## What exists

`Setup` is a serializable value. It owns no database. The caller loads it, calls one method and saves it.

| Method | Caller | Effect |
| --- | --- | --- |
| `Setup::new(id, draft)` | owner chat | Starts a draft at revision 1. |
| `edit(expected, f)` | owner chat | Changes a draft. Fails on a stale revision or after approval. |
| `approve(rev, by, limits)` | authenticated owner route only | Approves one revision. Checks `Limits` from reviewed config. Builds the steps. A repeat does nothing. |
| `advance(provisioner, persist, now)` | background driver | Runs steps in order. Calls `persist` before and after each side effect. Fails before approval. |
| `complete_wait(request_id, now)` | owner route, after the protected store accepted the value | Closes one owner request. An expired request opens a new one. A stale or repeated identity fails. |
| `resolve_unknown(step, done)` | owner route | Resolves an unknown outcome (D34). |
| `set_max_attempts(n)` | owner route | Changes the three-attempt default. |
| `claim_start()` | driver | Returns the supplied task or "announce ready", once. |

Step order: allocate disk, boot, install executor, then one credential wait per label. Owner waits use no attempt. A crash during a step costs one attempt on resume.

`HarnessProvisioner` maps the steps to `vmproto.py` (`status`, `overlay`, `start`, `restore`, `kill`, `install-executor`). It checks `status` first, because `overlay` and `install-executor` refuse to run twice.

## What Stage 1 must provide

- A table or column that stores the `Setup` JSON per setup id, with compare-and-swap on a version, so two writers cannot both advance one setup.
- A `persist` closure that writes that row durably before it returns.
- Owner-only API routes for the methods above. The bot tool set must not reach them. Page and event content must not call them.
- Save the setup before acting on `claim_start`. Then create the task. A crash between the two loses the start. It never repeats it.
- Reload non-final setups at startup and call `advance`.

## Gaps

- Protected credential entry has no store, lifetime or UI. `Wait` carries a request identity and a label only. Nothing in this module reads a value.
- No CAPTCHA or phone-approval path exists. No provisioner step returns those waits yet. The type and persistence are ready.
- The harness fixes 2 CPUs and 2048 MiB. `HarnessProvisioner` fails a draft that asks for other values. Per-bot resources need a harness change.
- `config.rs` has no frozen-base path, runtime root or setup `Limits`. The caller must read them from reviewed configuration.
- The spending ceiling is not part of the draft. Stage 2 owns its type. Approval must record it when that type exists.
- No React component. The chat needs the request identity, label, expiry and checklist from `Setup`.
- No retry delay between attempts. The driver can add one.
- A disk that exists without an overlay record gives an unknown outcome. The owner must clean it.
- Only serial setup is supported. Independent setup beside a blocked session is not built.
