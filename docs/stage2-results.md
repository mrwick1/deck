# VM Stage 2 results: guest executor and host command journal

Date: 2026-11-12.

## Verdict

Deck now has a real host-to-guest command path. A Rust host client and a SQLite journal drive a Python guest executor inside the Arch VM. The executor runs commands as the desktop user, never as root. One serial smoke test on a real VM passed. The deterministic tests pass.

This stage proves the control path and its recovery rules. It does not integrate the agent loop, bare, the UI or the task queue. Those belong to Stage 3. The Stage 1 results (`vm-stage1-results.md`) still describe the VM, bare window mode and saved-state resume.

Decision references use the titles in the parent decision log: "Each bot owns a persistent desktop computer", "Action permissions are trusted instructions" and "Start the executor stage and retain unfinished optimizations". The parent owns that log. This branch did not edit it.

## What was built

| Path | Purpose |
| --- | --- |
| `scripts/vm-prototype/guest/deck_executor.py` | Guest executor. Python standard library only. Runs as the desktop user. |
| `scripts/vm-prototype/guest/deck-executor.service`, `70-deck-executor.rules` | systemd unit (`User=deck`) and the udev rule that gives `deck` the serial port. |
| `src/executor/proto.rs` | Frames, limits, ID rules, the command digest, SHA-256 and base64 adapters that reuse the packages already in Cargo.lock. |
| `src/executor/journal.rs` | Journal methods on `Store`: epochs, command rows, events, artifact records. Migration V3 is in `src/store.rs`. |
| `src/executor/client.rs` | One connection: hello, epoch fence, exec, cancel, status, reconcile, artifact fetch. |
| `src/executor/cli.rs` | Developer entry point: `deck executor <command>`. |
| `scripts/vm-prototype/vmproto.py` | Adds the executor serial port to the run-mode QEMU command and the `install-executor` bootstrap command. Stage 1 controls are unchanged. |
| `scripts/vm-prototype/stage2.py` | The serial real-VM smoke scenario. |
| `scripts/vm-prototype/test_executor.py`, `src/executor/tests.rs` | Deterministic tests. |

## Protocol v1

A frame is `DK`, one version byte (`1`), a big-endian u32 length and one JSON object. The maximum frame is 256 KiB. A wrong magic, version, length or JSON closes the connection. Each request carries an `id`. Each reply echoes it. Every message after `hello` carries the connection `epoch`.

| Direction | Type | Meaning |
| --- | --- | --- |
| host → guest | `hello` | `proto`, `bot_id`, `epoch`, `nonce`. The host allocated the epoch. |
| guest → host | `hello_ack` | `bot_id`, `guest_id`, `epoch`, `nonce`, `max_epoch_before`, `boot_id`, limits. |
| host → guest | `exec` | `bot_id`, `task_id`, `command_id`, `argv`, `deadline_ms`, `digest`. |
| guest → host | `exec_ack` | Receipt state at once. `duplicate` is true when the ID was known. |
| host → guest | `status` | Look up one command receipt. Works while the command runs. |
| host → guest | `cancel` | Ask the guest to stop the command. Optional `grace_ms` (0 to 10000). |
| guest → host | `cancel_ack` | `accepted`, `already_terminal` or `unknown_command`. It never means terminated. |
| guest → host | `result` | Sent once when a command ends. Carries the receipt. |
| host → guest | `artifact_get` | `command_id`, `handle`, `offset`, `length` (at most 128 KiB). |
| guest → host | `artifact_chunk` | Base64 data and `eof`. |
| guest → host | `error` | `code` and `message`. Codes: `unprovisioned`, `wrong_bot`, `stale_epoch`, `payload_conflict`, `bad_digest`, `bad_request`, `busy`, `unknown_artifact`, `artifact_changed`, `no_hello`, `unknown_type`, `unsupported_version`. |

Limits: `argv` has 1 to 64 entries of at most 16 KiB. IDs match `[A-Za-z0-9_-]{1,64}`. The deadline is 1 ms to 3,600,000 ms. Output tails are 16 KiB each. At most 8 commands run at once.

The digest is SHA-256 over length-prefixed fields: bot, task, command, deadline, argument count and each argument. The epoch is not part of it. A resend after a reconnect therefore has the same digest. A Rust test and a Python test assert the same digest constant.

## Trust guarantees

What the design provides:

- One endpoint serves one bot. The guest holds `bot_id` and a random `guest_id` in a file on its own disk. The host journal pins that `guest_id` on first contact. A different guest later is refused.
- The host connects to a unix socket that QEMU creates in the runtime directory. The host checks that the socket is a socket, is owned by the host user and is not writable by group or others. It checks the peer credentials: the same user, and the QEMU process ID when the caller gives it. The guest end is a virtio-serial port. It has no network path and no host mount.
- The udev rule gives the guest port to `deck` with mode `0600`. The executor opened it as `deck` in the smoke test. The executor refuses to start as root.
- Every command and reply carries the connection epoch. The guest rejects a command with another epoch. The host drops any frame with another epoch and logs `stale_rejected`.

What it does not provide:

- No cryptographic guest attestation. A compromised guest can lie about any reply. The `guest_id` and the receipts detect misrouting, a replaced disk and a rolled-back guest. They do not stop a hostile guest.
- No authentication on the serial port beyond the OS permissions above. Anything that runs as `deck` in the guest, or as the host user, can speak the protocol.
- Per the parent decision "Action permissions are trusted instructions", the executor does not enforce action permissions. It does not block a command because of what it does.
- Commands run as `deck`. `deck` owns the whole guest user session. Isolation between commands, and between bots, rests on the VM boundary.

Guest bootstrap stays separate. `install-executor` uses the root guest-agent channel from Stage 1. That channel is a test shim. It writes the executor files and the identity once. Commands never use it.

## Command states

Guest receipt states: `running`, `succeeded`, `failed`, `cancelled`, `timed_out`, `interrupted`, `termination_unconfirmed`. The guest writes the receipt to disk (file and directory fsync) before it starts the process. A receipt that says `running` after an executor restart or a reboot becomes `interrupted`.

Host states and their meaning:

| Host state | Meaning | Changes later? |
| --- | --- | --- |
| `intent` | Recorded before any send. Never dispatched. | Yes, by an explicit submission. |
| `sent` | The host committed `sent`, then wrote the frame. Delivery is unknown. | Yes, by a reply or by reconcile. |
| `running` | The guest acknowledged it. | Yes. |
| `succeeded`, `failed`, `cancelled`, `timed_out` | The guest reported the end. For `cancelled` and `timed_out` the guest confirmed that no process remained in the group. | Never. |
| `not_delivered` | The guest proved it never ran the command (see below). | Only by an explicit submission. |
| `unknown` | The outcome cannot be established. The detail says why: `guest_state_lost`, `guest_interrupted`, `termination_unconfirmed`. | Only by direct guest evidence. Never by a retry. |

Rules:

1. The host writes `intent` before it sends. It writes `sent` before the frame goes out. A crash between those two writes leaves `intent`, which means never dispatched.
2. A reply updates the row after it arrives. A late `result` for a row that is already final is stored as a `late_result` event. It does not change the row.
3. Reconcile runs on every CLI connection. It asks for the receipt of each `sent`, `running` or `unknown` row. It sends no command.
   - A receipt exists: apply it.
- A missing receipt after reconnect leaves `unknown`. A remembered epoch cannot exclude rollback after hello but before the command receipt. Only an explicit pre-execution rejection establishes `not_delivered`.
   - No receipt, and the guest had not seen that epoch, or a `running` row lost its receipt: the row becomes `unknown` with `guest_state_lost`. The guest was replaced or rolled back. Its earlier timeline may have run the command.
   - A `sent` row on the same connection that sent it is left alone. The absence of a receipt proves nothing there.
4. Submitting a known command ID never sends a second `exec` while the row is `sent`, `running`, `unknown` or final. Only `intent` and `not_delivered` rows dispatch. A changed payload under a known ID is rejected before any send and logged as `payload_conflict`.
5. The guest also keeps receipts by command ID. It returns the stored receipt for the same ID and digest. It rejects the same ID with another digest. The host and the guest therefore both refuse a second run.

This design does not give exactly-once execution of external effects. It gives at-most-once dispatch per command ID, durable evidence and an explicit `unknown` state. Retrying an `unknown` action needs a new command ID. A person or a later policy chooses that.

## Cancel and deadline semantics

- `cancel` is journaled as `requested` before it is sent. `cancel_ack: accepted` moves it to `accepted`. The command is still `running` at that point.
- The guest sends SIGTERM to the process group, waits for the grace period (2 s by default), then sends SIGKILL. It then checks `/proc` until no live process remains in the group. Only then does it write `cancelled`. The host then sets `cancel_state` to `confirmed`.
- If a process stays alive, the guest reports `termination_unconfirmed`. The host marks the row `unknown` and `cancel_state` `unconfirmed`. It never reports `cancelled`.
- A deadline works the same way and ends in `timed_out`. After the leader exits normally, any remaining process in its group is killed and the command records that.
- A process that calls `setsid` leaves the group. The executor does not track it. Stage 3 may add a cgroup per command.

## Artifacts

A command writes files into `$DECK_OUT`. When the command ends, the guest registers the regular files directly inside that directory. The guest uses `O_NOFOLLOW`, checks the owner and the file type, and rejects names outside `[A-Za-z0-9_][A-Za-z0-9._-]{0,127}`. It rejects symlinks, directories, files over 8 MiB and any file past the 16th. It computes SHA-256 and returns a handle, name, size and digest. The host never sends a path. A request carries a handle and a command ID. The guest reopens the file and refuses it if the inode, size or mtime changed since registration.

The host writes to `<data_dir>/bots/<bot>/artifacts/<command>/`. It checks each directory level: not a link, owned by the host user. It creates a `.part` file with `O_EXCL` and mode `0600`. It checks the exact byte count and the SHA-256, then renames the file. A failure removes the `.part` file and leaves nothing. A repeat fetch returns the stored file.

## Developer entry point

`deck executor --help` starts no VM and no model. It returns before it reads the configuration or opens the journal.

```
deck executor run --bot B --endpoint SOCK --id CMD --task T [--deadline-secs N] [--wait-secs N] -- ARGV...
deck executor status|cancel|fetch|reconcile --bot B --endpoint SOCK [--id CMD]
deck executor show --bot B [--id CMD]       # reads the journal only
```

Common options: `--data-dir` (or `DECK_DATA_DIR`, or `data_dir` in `deck.toml`), `--peer-pid`, `--guest-id`, `--timeout-secs`. Every CLI call opens one connection with a new epoch. `run --wait-secs 0` dispatches and returns, so a second call can query or cancel.

## Reproduce

```
cargo build
cargo test                                   # includes tests against the real guest executor
python3 -m unittest discover -s scripts/vm-prototype -p 'test_*.py'

# Real VM smoke. Needs QEMU/KVM, a frozen Stage 1 base and a runtime directory outside Git.
export DECK_VM_DIR=$HOME/.cache/deck/deck-stage2
cd scripts/vm-prototype
python3 stage2.py --base /path/to/frozen-base.qcow2 --fixture-port 28931
```

`stage2.py` creates a private overlay on the read-only base, boots it, runs `install-executor`, runs the scenario, saves, restores, kills the VM and cold boots. It always kills the VM it started. It writes `results.json` to the runtime directory. `vmproto.py clean --disk` removes the overlay. A fresh `setup` also installs the executor, because `setup-guest.sh` now carries it. That path was not run (see NOT TESTED).

## Test results

| Check | Result |
| --- | --- |
| `cargo test` | 81 unit tests and 3 integration tests passed, 0 failed. The live tests stay ignored, as before. 27 of the unit tests are new executor tests. |
| `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings` | pass |
| `python3 -m unittest discover -s scripts/vm-prototype -p 'test_*.py'` | 50 passed (29 from Stage 1, 3 new harness tests, 18 executor tests) |
| `python3 -m compileall -q scripts/vm-prototype`, `bash -n` on `setup-guest.sh`, `git diff --check` | pass |

Deterministic cases, each with bounded waits: duplicate delivery; same ID with changed task, arguments or deadline; wrong bot; a guest that lies about its epoch; a pinned and a mismatched guest ID; peer process check; socket ownership and permissions; stale reply epochs; epoch growth across a host restart and across a guest rollback; journal survival across a host restart; a dropped reply after execution (no second execution); lost guest state (unknown, never retried); disconnect before the guest reads the exec (proven not delivered, not retried); a silent guest (bounded wait, row stays `sent`); a guest refusal; cancel accepted versus confirmed; a late result after cancel; unconfirmed termination; artifact success, checksum mismatch, size mismatch, oversized declaration, bad names, over-serving, a missing artifact, a link planted at the file and at the directory; and, against the real Python executor, a process-tree stop, a deadline, output flooding without deadlock, artifact tampering after registration, an executor restart and a reconnect.

A test of the journal found one real bug: recording an intent failed when the bot row did not exist yet. The fix creates the bot row first.

## Real VM smoke evidence

One VM, one serial run, synthetic data. QEMU 11.1.0, the Stage 1 Arch image (frozen base, read-only), a private overlay. No model calls. No bare use. Source: `results.json` in the runtime directory.

| Step | Observed |
| --- | --- |
| Connect | The first connection got epoch 1. The peer check used the QEMU process ID. The guest ID was pinned. |
| Run and artifacts | A shell command ran as `deck`. Its exit state was `succeeded`. The host fetched three artifacts. The SHA-256 of a 200,000-byte file matched the value computed in the guest by the root shim. The files sit under the bot store. |
| Status and cancel | A detached `sleep` command was `running` when a new connection (new epoch) queried it. The command's child ran as `deck`. `cancel` was accepted. The row became `cancelled` with `cancel_state` `confirmed`. The child process was gone. One sample of cancel request to confirmed row: 0.14 s. The earlier, failed run of the same scenario measured 0.2 s. |
| Deadline | A 2 s deadline on a command with a background child ended as `timed_out`. No process remained. |
| Replay | The same ID and payload returned `succeeded`. The guest side-effect file kept one line. The same ID with other arguments was rejected. |
| Raw fencing | A direct hello with epoch 1 got `stale_epoch`. A hello for another bot got `wrong_bot`. |
| Save and restore | The journal had no in-flight row. The VM saved and restored. The next connection had epoch 11 and `max_epoch_before` 10. The old command returned `succeeded`. The side-effect file still had one line. A new command after the restore succeeded. Single samples, not a benchmark: save 6.28 s, restore to usable 1.94 s. |
| Kill and cold boot | A `sleep` command was in flight. The VM was killed with SIGKILL and cold booted. The guest executor started from its service. The receipt had survived. The host row became `unknown` with `guest_interrupted`. Submitting the same ID did not run it again. No process remained. |

The first full run failed at the last step because my test checked a file that the guest had not flushed to disk before the kill. That is a fault in the test. The command now runs `sync` first. The reported run is the second, complete run from a fresh overlay and a fresh host journal.

The journal events recorded on the real VM were `intent`, `dispatch`, `guest_report` and `payload_conflict`. The real run did not produce `stale_rejected` or `late_result`. Only the fake-guest tests cover those.

## Limitations

- The executor serves one host connection at a time. Status and cancel work while a command runs, on the same connection or on a later one. Two simultaneous host connections are not supported.
- A dropped or restored connection can leave bytes in the serial buffer. The host skips frames until it sees its own `hello_ack`. The guest drops a half frame after 5 s of silence. Hypothesis: a very fast reconnect can still lose the first `hello`. The host would then time out, and the next call would retry with a new epoch. No test covers this path.
- The guest writes receipts to its own disk. A rollback to an older whole-VM image is detected only through `max_epoch_before`. If the rollback keeps a hello of the current epoch but loses the command, the host cannot tell. Stage 1 restore deletes a saved state after use, so the harness does not roll back.
- Output tails keep the last 16 KiB. The guest spools up to 1 MiB per stream in its state directory. The host does not fetch the spool.
- Arguments are stored in the journal. Do not pass secrets in `argv`. Credential entry is a separate feature.
- The executor does not log arguments or output. The CLI prints the stored output tails, which can hold anything the command printed.
- A command runs with a minimal environment. It has no desktop session variables. Driving the desktop, as `as-deck` does, is Stage 3 work.
- Process-group tracking misses processes that call `setsid`.
- The wrong-owner artifact check on the guest (a file not owned by the executor user) has no test, because the tests run as one user.

## NOT TESTED

- A fresh `setup` run with the new `setup-guest.sh` lines. The smoke used `install-executor` on an existing base. The setup edit has only a `bash -n` check.
- The kill-and-reboot case for a command that finished and was acknowledged but whose receipt was not on disk. The guest fsyncs before it replies, so the case should not occur. No test forces it.
- Host crash between the `sent` commit and the frame write on a real VM. The fake-guest tests cover the journal states.
- A dropped reply on a real VM. Fakes cover it.
- Multiple VMs and multiple bots at once. The journal keys rows by bot, but only one bot ran.
- Large artifacts at the 8 MiB limit, and 16 artifacts at once, over the serial port.
- Throughput and latency under load. No performance matrix ran.
- bare, the desktop, UI, the model loop, the task queue, budgets and permissions.

## Stage 3 integration work

- Route the agent's shell and file tools through the executor, and bare through a relay in the guest service.
- Put the epoch, the journal and reconcile inside the long-running Deck server instead of one CLI call per connection.
- Add a queue owner, so save-on-idle waits for `running` and `unknown` rows. Surface `unknown` rows in the inbox for the owner's decision, as the decision "Investigate uncertain outcomes before asking the owner" describes.
- Define evidence rules for resolving an `unknown` row, and the retry policy.
- Add a cgroup per command, a per-command resource limit and desktop session variables for commands that drive the screen.
- Add a way to bind the guest identity at bot creation, instead of `install-executor` on a running guest.

## Parent review and reproduction

The parent reproduced the serial real-VM smoke on a new private overlay and host journal. It passed command execution as the desktop user, status during execution, confirmed process-group cancellation, deadline termination, artifact integrity, duplicate-ID suppression, payload conflict rejection, stale-epoch and wrong-bot rejection, save/restore and kill/cold-boot recovery. The side-effect file retained one line after resubmission and restore. No owned VM remained running after the script exited.

Review found a missing-receipt recovery error. A snapshot can retain hello's epoch but predate the command receipt. A remembered epoch alone therefore cannot prove non-delivery. A new regression test failed first. Reconciliation now retains an unknown outcome and does not permit silent replay in that case.

The parent also added a failing cross-bot command-access regression, then bound session command lookup to the configured bot. Request writes and hello writes now have timeouts. The new hand-written SHA-256 and base64 routines were replaced with adapters to packages already in the dependency graph. No new package version was introduced. Guest output pipe descriptors close after draining.

The parent reproduction used the install-executor path. Fresh setup provisioning, detached sessions that escape a process group, multi-bot operation and real transport-loss injection remain untested. Process-group cancellation is not a cgroup containment guarantee. Treat this stage as a verified developer control path, not a production bot runtime.

Raw parent results are in a separate local stage2-parent-review runtime directory. Earlier experiment evidence is preserved. No performance benchmark was run.
