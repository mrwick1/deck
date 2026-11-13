# Unattended implementation handoff

Updated: 2026-11-13.
Status: implementation and integration in progress. Nothing is pushed or deployed.

## Read first

1. Read [decisions.md](decisions.md), especially D57–D61.
2. Read [unattended-implementation-plan.md](unattended-implementation-plan.md).
3. Read this handoff before continuing work.
4. Read the current diff and verify the checkpoint before continuing.

The owner authorized six implementation agents. Each worked in a separate prepared worktree. All six have returned. The parent owns integration on branch `document-bot-setup`. Do not edit that branch from another session while its writer is active. Use a separate worktree for independent work. Coordinate before taking over integration.

Gmail belongs to another workstream. Do not change its private tasks, account state or worktree.

## Approved product requirements

- Review conversational setup before creating a bot or its private computer.
- Recommend the name, model, instructions, tools, resources and full job authority. Keep the review editable and save drafts.
- Default to full authority within the approved job. Do not request repeated approval for covered sends, posts, submissions or deletes.
- Preserve explicit owner restrictions, revocation, stop, pause and uncertain-outcome rules. D33 remains a trusted-instruction model.
- Let the bot raise its working budget automatically. It cannot exceed or change the absolute ceiling set by the owner. Request an owner increase when needed.
- Request credentials through protected input after system setup. Phone approvals and CAPTCHAs wait for the owner without losing progress or consuming retries.
- Default safe setup failures to three total attempts. Continue unrelated work only when session ownership permits it.
- Support long-running unattended sessions. There is no ten-session requirement.

The five earlier planning reports remain proposals except where the owner expressly approved a requirement. Do not silently adopt their notification channels, calendar rollover, retention or timing defaults.

## Agent work and integration

All child commits started from `230d797`. Child completion means completion of its isolated slice, not the full product stage.

| Agent / stage | Child branch and commit | Integrated commit | Result and remaining dependency |
| --- | --- | --- | --- |
| 0. Accounting contract | `t3/accounting-contract`, `d61d7c4` | `62ea43e` | Pure checked monetary arithmetic, quotes, exposure and admission. Fourteen tests. No configured defaults, clock or database. Parent does not activate its extra step/period caps by default. |
| 1. Durable state | `t3/bot-durable-accounting`, `a0d48fc` | `24956c7` | Additive schema v5; drafts, definitions, typed holds, reservations and settlement. Thirteen child tests. Parent adds atomic allowance-plus-reservation admission. |
| 2. Model usage | `t3/streaming-accounting-seam`, `5edf6d1` | `603c0d7` | Usage-only chunks, bounded requests, admission and settlement interface. Child delivered a partial slice. Parent connects it to the durable ledger. |
| 3. Authority and controls | `t3/owner-control-contract`, `4fced56` | `ff28af4` | Full-authority prompt, ceiling response validation and web form. Child route returns unavailable. Parent must connect durable requests and owner responses. |
| 4. Setup | `t3/bot-setup-state-machine`, `8fb865d` | `afcf022` | Serializable setup state and a harness provisioner; 22 child tests. No end-to-end Store/API/UI driver or protected credential entry. |
| 5. Recovery | `t3/bot-runtime-recovery`, `9a9f361` | `26cd157` | Fixes an owner answer lost across host restart. Adds failed-restore preservation coverage. Live VM checks remain open. |

The parent resolved the setup cherry-pick conflict by retaining both `setup` and `state` module declarations. No unrelated code was removed in that conflict.

## Parent work completed

- `cb47ce4` records approved D57–D61 and the implementation plan.
- `fcf3315` connects configured VM bot model calls to durable admission and settlement.
- The Store raises allowance and reserves the next charge in one transaction. Concurrent calls cannot each spend the same remaining allowance.
- Automatic increases record a reason and a chat message. The absolute ceiling is the default monetary boundary. No extra step cap becomes an approval prompt.
- Lost usage retains reserved exposure. Duplicate dispatch identities are refused. Duplicate reported settlement applies once.
- Accounting configuration is explicit and opt-in for existing configured bots. It contains unit, period, prices, limits and increase increment. Startup does not overwrite stored owner ceiling changes.
- Runtime and tool-boundary hold checks read typed holds. Dispatch stays blocked by ceiling, investigation, pause and legacy budget holds.
- A failing parent regression showed malformed stored restrictions became an empty list. The read now fails instead of weakening the instructions.

## Latest integration checkpoint

Commit `439ab6e` connects the owner ceiling control to the ledger. Schema v6 stores durable requests and response receipts. Admission or a reported overrun creates an owner request. Authenticated responses apply once. Stale or changed responses fail. A denial keeps the ceiling and hold. Approval clears only the ceiling hold and makes accounting-held tasks eligible for the remaining gates.

Status and inbox now expose the ceiling request. The web form calls the authenticated handler. The parent has not exercised that form in bare or tested the complete HTTP authentication path for this new operation.

The agent refreshes the approved job and restrictions before each model call. A definition change invalidates pending tool calls and causes replanning. Revocation and holds are checked before tool dispatch, including direct chats using a configured bot.

Two parent regressions exposed integration defects:

- A late hold stopped the next model call but still allowed a tool returned by an in-flight request. The regression failed on the browser call, then passed after adding the dispatch check.
- A provider charge above the reserved bound created a ceiling hold without an owner request. The regression failed, then passed after making the request durable with the hold.

## Current work and next edits

Durable setup integration is next. There is no current red test or uncommitted code at this checkpoint. All six children have returned. No delegate remains active.

1. Persist setup state with compare-and-swap versions in the existing Store.
2. Include the working budget and absolute ceiling in the reviewed draft. Reconcile the setup and runtime authority representations.
3. Connect authenticated setup editing and approval. Keep drafts separate from active authority.
4. Add a durable provisioning driver and an atomic completion outbox. A ready task must neither disappear nor run twice after restart.
5. Register the ready bot with its private runtime. Keep bot files, accounts and journals isolated.
6. Specify and implement protected credential storage and guest transfer before accepting any real values. Connect phone and CAPTCHA waits only to a supported owner interaction path.
7. Add UI setup progress and review. Verify resource recommendations against real harness support.
8. Test the integrated flow before claiming end-to-end creation or unattended provisioning.

## Verification ledger

These results apply to the revisions stated. Do not transfer them to later edits without rerunning checks.

| Checkpoint | Verified result |
| --- | --- |
| Parent after accounting, storage, usage and recovery integration (`fcf3315`) | 185 Rust unit tests and four non-live integration tests pass. Two live tests remain ignored. Formatting, clippy and diff checks pass. |
| Parent accounting module (`62ea43e`) | All 14 pure accounting tests pass. |
| Parent restriction fix | Regression failed before the fix and passed after it. |
| Parent atomic admission | New test initially failed to compile without the method. It passes after implementation. Tests include competing reservations and the ceiling boundary. |
| Stage 3 child only (`4fced56`) | Child reports 147 Rust tests and web checks pass. The parent later reproduced the integrated Rust and web gates below. |
| Stage 4 child only (`8fb865d`) | Child reports 22 setup tests. The parent reproduced them as part of the combined suite below. This does not prove Store-backed setup. |
| Latest parent checkpoint (`439ab6e`) | 221 Rust unit tests and four non-live integration tests pass. Two live tests remain ignored. Formatting and clippy pass. Web type checking, 26 web tests and the production build pass. |

The first integrated web check failed because dependencies were absent. `npm ci --ignore-scripts` restored the locked dependencies. The rerun passed. The existing bundle-size warning remains. The install reported ten dependency vulnerabilities; no lockfile or dependency version changed. Dependency remediation is outside this slice.

No paid model calls, real account actions, live VM experiments or browser checks ran in this implementation round. Child fake-harness tests do not prove real provisioning. No deployment, service restart or merge ran.

## Known gaps and limits

- The ceiling approval route is connected and covered by Store and handler checks. Complete HTTP authentication and browser acceptance for this operation remain untested. The legacy hold-release route remains separate from ceiling mutation.
- Current authority loads between model steps. The trusted-instruction boundary still applies. Broader behavioral tests of instruction changes and restrictions remain useful.
- Setup is a state machine, not an integrated bot-creation flow. Its state is not yet stored in the real database.
- Protected credential input, credential lifetime and guest transfer are not implemented. Do not collect values in ordinary chat or journal them in command arguments.
- CAPTCHA and phone wait types exist. No supported challenge-completion interaction has been verified.
- Setup lacks the spending ceiling in its reviewed draft, a production worker, a completion outbox and runtime registration.
- The harness uses fixed CPU and memory settings. Resource recommendations need actual support before the UI claims to apply them.
- The setup start claim currently prevents duplicates but can lose work if a crash occurs between saving the claim and creating the task. Integration needs an atomic task/outbox operation.
- Budget period rollover, spike thresholds and no-progress policy remain unimplemented. Do not invent a monetary default.
- Request reservation depends on configured provider bounds and pricing. It is not a universal invoice or external-purchase cap.
- Multi-connection races, production database migration, bare UI behavior and live recovery need targeted validation.

## Next delivery gate

Integrate durable setup next. Ceiling control and authority refresh have deterministic integration coverage, with the HTTP and browser limits stated above. Run the combined Rust and web gates. Use synthetic fixtures and a scripted model. Serialize any later VM checks and retain evidence. Use only bare for browser acceptance.

Keep this handoff current after each integration milestone. Record test results, unresolved gaps and exact commits. Do not label disconnected modules as finished product behavior.
