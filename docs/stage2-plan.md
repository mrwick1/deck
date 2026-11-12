# Stage 2: guest executor and durable host control

Date: 2026-11-12.

## Goal

Give Deck a real host-to-guest control path. Keep action evidence outside VM snapshots. Do not redesign the UI or add account integrations in this stage.

## Required behavior

- Version the protocol. Bind a connection to one configured bot. Give each command a task ID, command ID, host-owned epoch and deadline.
- Run bot commands as the guest desktop user. Keep privileged VM lifecycle management separate from ordinary commands.
- Support execution, status lookup and cancellation while a command runs. Confirm termination separately from accepting a cancel request.
- Preserve command receipts. Repeated requests with the same identity must return the known state, not execute again. Reject identity reuse with different arguments.
- Keep host intent and outcome records in SQLite before and after dispatch. Reconcile in-flight records after restart. A lost reply leaves an unknown outcome rather than an automatic retry.
- Advance epochs on the host after restore or reconnection fencing. Reject stale replies. Use guest receipts as evidence without assuming they prove an external action occurred exactly once.
- Transfer artifacts by handles with size and integrity checks. Keep artifact paths within the guest workspace and the host bot store.
- Keep bare as the only browser. A later integration step connects its tools to this executor.

## Checks

Start with deterministic protocol and journal tests. Cover duplicate delivery, changed arguments under the same ID, wrong bot, stale epoch, host restart, disconnect after dispatch, cancellation and a missing artifact. Use bounded waits in every test.

Then run a small serial real-VM smoke test with synthetic data. Show command execution, cancellation, artifact transfer and reconnect evidence. Do not rerun the optimization benchmark matrix.

The parent reviews the full diff and reproduces the critical checks. No second agent may use the implementation worktree while the first owns it. A returned turn is not proof that background work ended. All owned test processes must stop before handback.

## Deferred work

Idle sleep, event batching and UI capture subscriptions remain on the carryover checklist in `optimization-plan.md`. Stage 2 supplies dependencies; it does not complete those integrations. Conversational creation, real accounts, MFA, model spending enforcement, automatic memory and the home-screen redesign are outside this stage.

Status: approved to start. Implementation has not been verified.
