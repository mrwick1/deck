# Stage 3 results: one lean bot through the real server, tools and UI

Date: 2026-11-12. Branch `t3/desktop-vm-feasibility`. Status: implementation reviewed; parent verification and remaining limits are recorded below.

## What is real now

When `deck.toml` has a `[vm]` section, the server owns one bot with one guest computer. Without `[vm]`, nothing changes: the server uses a local `bare` as before. In VM mode the server never starts a host `bare`. The browser runs in the guest.

| Part | Where | State |
| --- | --- | --- |
| Config `[vm]`, `[events]` | `src/config.rs` | Versioned (`version = 1`), validated, unit tested. |
| Durable state | `src/store.rs` (V4), `src/bot/db.rs` | Flags, tasks, steering, events, owner decisions. All in the host database, so a saved or lost guest keeps them. |
| Coordinator | `src/bot/mod.rs` | Event-driven loop (a notify and the next deadline). One task owns the computer. Idle save, wake, stop, steer or queue, pause, budget hold, inbox, event intake. |
| Computer control | `src/bot/vm.rs` | Runs `vmproto.py` with an explicit argv, a timeout and an identity check before any kill. |
| Guest link | `src/bot/hub.rs`, `src/executor/*` | One epoch per connect, journal reconcile, artifacts fetched by handle with checksums. |
| Tools | `src/tools.rs`, `guest/bare-call`, `guest/deck-publish` | `shell` and `ask_owner` are new. Browser calls go through the guest relay to the session `bare-mcp`. Results and files map back to host artifact paths. |
| Containment | `guest/deck_executor.py` | Each command runs in its own cgroup (`Delegate=yes`, `cgroup.kill`). A `setsid` descendant is killed. Fallback: process group. |
| Desktop env | `guest/deck_executor.py` | `XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY` and the bus address come from `/run/user/UID`, not from a fixed name. |
| Screen | `src/bot/screen.rs` | QMP `screendump`. One worker per bot. It runs only while a viewer exists and the computer is awake. It sends the frame number. The client fetches the PNG. |
| Events | `src/bot/events.rs`, `POST /api/bot/events` | Bearer token from an env var. Dedupe on (source, id). Grouping by (source, kind). Quiet window plus a maximum delay. One task per batch. |
| API | `src/bot/api.rs` | See "API" below. |
| UI | `web/src/components/BotBar.tsx`, `web/src/lib/bot.ts` | Overview card, chat first, bot bar, inbox, send options, screen on demand. |

## Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Server-owned runtime and lifecycle | PASS | Real VM run (below). `bot::tests`. |
| Durable queue survives restart | PASS (tested with a second `Bot` on the same database) | `a_run_that_the_host_lost_becomes_held_and_does_not_replay` |
| Steer at the model-call boundary | PASS | `steering_reaches_the_next_model_call` |
| Stop awaits termination, reports unconfirmed commands | PASS | `stop_cancels_the_task_and_kills_a_setsid_descendant` |
| `UNKNOWN` blocks the idle save until an owner decision | PASS | `an_unknown_outcome_blocks_the_idle_save_until_the_owner_decides` |
| A waiting task keeps the desktop | PASS | `a_waiting_task_keeps_the_computer_and_resumes_on_the_owner_answer` |
| Pause vs sleep are separate | PASS | `pause_and_budget_hold_keep_the_computer_asleep_until_cleared` |
| Budget hold persists, only an explicit approval clears it | PASS | same test, and `a_budget_hold_stops_a_running_task_at_the_model_boundary` |
| Events: dedupe, burst, delay bound, one wake, no bypass of holds | PASS | `an_event_burst_*`, `continuous_events_*`, `the_event_endpoint_*` |
| Capture only on demand, stops on sleep and disconnect | PASS | `capture_runs_only_for_a_viewer_*`, and the real run: 0 captures without a viewer, none after the viewer left |
| Tools through the guest with host artifact paths | PASS | Real run: `browser_open`, `shell`, `send_file` all worked |
| One scripted E2E task (browser, file, shell) | PASS | Real run |
| Sleep and wake round trip with chat continuity | PASS | Real run: the second task woke the saved computer in 3.0 s and the chat kept all earlier messages |
| Resource observations | PASS | Table below |
| UI exercised in bare | PASS for reviewed controls and navigation | Parent real-server controls check; built-UI overview navigation check. See parent review for scope. |

## Real VM run

`scripts/vm-prototype/stage3.py` makes a private overlay on a frozen base, installs the executor, cold boots through the server, and runs the real server binary against a scripted model and the synthetic fixture. It stops everything it starts. The reported run is the fifth. Runs 1 to 4 failed or were weak for these reasons, all in the harness script: (1) it killed the VM before the install reached the disk; (2) it measured no working state; (3, 4) two checks were too loose or too strict. No product code changed for them.

One server, one VM (QEMU/KVM, 16 CPUs on the host), synthetic data, no real model. Single run, so these are samples, not distributions.

| State | Window | VM CPU | VM RSS | Server CPU | Server RSS | Model calls | Captures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Saved, before any task | 8 s | none (no process) | 0 | 0.0 % | 19.3 MiB | 0 | 0 |
| Working (a 14 s shell, no viewer) | 5 s | 5.8 % of one CPU | 538 MiB | 0.4 % | 20.5 MiB | 2 | 0 |
| Awake, idle | 15 s | 4.2 % of one CPU | 539 MiB | 0.07 % | 21.2 MiB | 0 | 0 |
| Saved, after the idle save | 10 s | none | 0 | 0.0 % | 21.2 MiB | 0 | 0 |

- Cold boot to a connected executor and ready desktop: 14.6 s. Task one in all: 29.2 s (it includes a 14 s sleep). Four model calls.
- Wake from the saved state with a task: 3.0 s. One model call.
- Screen: with one viewer for 4 s there were 5 captures, 1 delivered frame and 4 suppressed as unchanged (17 KiB sent, 158 ms of capture time in all). No worker after the viewer left. The capture count stayed flat.
- Workers: the server has no polling loop. The coordinator and the guest link wait on events and deadlines. The only worker is the screen worker, started once.
- The idle deadline in the run was 25 s so the test stayed short. The default is 120 s.

## API

`GET /api/bot`, `GET /api/bot/stream` (SSE status), `POST /api/bot/messages`, `/stop`, `/automation`, `/sleep`, `/wake`, `/budget-hold`, `/budget-hold/approve`, `GET /api/bot/tasks`, `POST /api/bot/tasks/{id}/{cancel,priority,retry}`, `GET /api/bot/inbox`, `POST /api/bot/inbox/commands/{id}`, `GET /api/bot/screen` (SSE of the frame number), `GET /api/bot/screen/latest`, `POST /api/bot/events` (bearer token). The existing `POST /api/chats/{id}/messages` goes through the queue for the bot's chat and accepts `mode` and `priority`.

## Rules the code keeps

- A task never replays by itself after a host restart. A task that ran becomes `held`.
- A held task with an unsettled command cannot retry until the owner decides.
- Event text is data. The task text says so, and the system prompt for the computer says so.
- Secrets in VM mode live in the guest (`~/.config/deck/secrets.env`). The host `secrets_file` is not read in VM mode.
- Upload through the relay is limited to the guest workspace.

## Not done

- **Spending accounting.** The budget hold is a real, persisted gate. Nothing sets it from cost yet. The owner sets it by hand (`POST /api/bot/budget-hold`). No model cost is counted.
- **Real accounts and memory.** No mail, calendar or memory integration. Events are synthetic.
- **Several bots.** One bot, one computer, by design.
- A steering message shows in the chat history as the model saw it ("The owner sent this while you work: ...").

## Not tested

- Full browser coverage of every UI action remains open. Parent tested bare navigation, pause/resume, message submission, delivery options, inbox and a real screen frame. Stop, steer and event intake retain deterministic coverage rather than real-VM UI coverage.
- Steer, stop, `ask_owner` and events against a real VM. They ran against the real Python executor on a unix socket with a fake `bare` session and a fake computer. The real VM run covered one task, an idle save and a wake.
- Real-VM cancellation of a detached descendant remains untested. Parent checked live guest receipts and confirmed cgroup containment was active; detached-process cancellation tests use the real executor on the host kernel.
- A fresh `setup` that provisions the new files (`setup-guest.sh` has a `bash -n` check only). The real run used `install-executor` on an existing base.
- A real model, long tasks, and a save that fails halfway.
- Event intake over the network from outside the host.

## Reproduce

```
cargo build && cargo test
python3 -m unittest discover -s scripts/vm-prototype -p 'test_*.py'
(cd web && npm ci && npm run check && npm test && npm run build)
# Real VM run. Needs QEMU/KVM and a frozen base. The runtime directory stays outside Git.
python3 scripts/vm-prototype/stage3.py --base /path/to/frozen-base.qcow2 --dir "$HOME/.cache/deck/deck-stage3"
```


## Parent review

The parent tested the real server UI in bare through a loopback test proxy. The owner could open the overview and bot chat, pause and resume automation, submit a message, select queue mode and priority, open the inbox and view a real desktop frame. A message submitted while paused waited and then completed after resume. No other browser was used.

Review found two behavioral errors. A normally running command appeared as an unknown-outcome decision. A new regression failed first; the status and inbox now distinguish live work from outcomes that need attention. The owner cannot dismiss a live command through that inbox. A second failing regression showed later queued work starting while an earlier outcome remained unresolved. Such work now waits for resolution.

The initial UI opened the chat instead of the overview. The parent corrected the landing behavior and hid the chat composer on the overview. A separate bare navigation check on the built UI confirmed overview, open chat and return to overview. That navigation check used synthetic API responses. The earlier controls test used the real server. The screen is requested automatically during browser tool activity in an open bot chat, or manually by the owner.

Cancelling a bare relay command is not proof that the persistent browser stopped its operation. The parent added a failing guest regression and changed interrupted browser relay receipts to an unknown external outcome. This blocks silent retry, queued work and idle saving until the outcome is resolved. Shell cancellation still uses the command cgroup.

The parent inspected receipts inside the real guest and confirmed cgroup containment on executed commands. A new coordinator test covers a failed save: it reports failure, retains the completed task and does not replay work. It does not simulate a real half-written migration file.

The first parent run included interactive UI traffic through a test proxy. Its capture and resource windows were contaminated by those viewers and are not a performance baseline. The proxy delayed upstream disconnect detection. Do not use those figures to assert idle capture behavior. The parent added strict assertions to the end-to-end script and ran the final verification separately, without that UI proxy.

The implementation agent added numbered decision entries in this branch despite the parent owning the canonical log. The parent removed those duplicate entries. Their implementation behavior is described here and in the approved Stage 3 plan. No approved product decision was removed.


## Final parent verification

The strict end-to-end run passed on a fresh private overlay and host journal without the UI proxy. The browser/shell/file task completed, the file appeared in chat, idle save stopped the VM, and the next task restored it with earlier chat messages intact. There were zero model calls and captures during the awake-idle and saved observation windows. The direct viewer produced five captures and one changed frame; closing it stopped the worker and the capture counter stayed flat. These are single-run functional observations, not a capacity benchmark.

A final waiting-task regression failed first: answering a waiting task could resume it while automation was paused. Resume dispatch now obeys both pause and budget gates. The queued answer remains stored until the gate opens.

Final checks: 130 Rust unit tests and three non-live integration tests pass. The Python suite has 62 tests. Web type checking, 22 unit tests and the production build pass. Formatting, clippy, Python compilation, shell syntax and diff checks pass. The web build still reports a large-bundle warning; bundle splitting was not part of this stage.

The coordinator's failed-save behavior is covered by a fake failure. Real corruption or failure halfway through a migration remains untested. Fresh provisioning, real accounts/models, automatic spending accounting and memory integration remain open. No deployment or production merge occurred.
