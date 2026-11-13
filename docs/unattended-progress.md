# Unattended implementation handoff

Updated: 2026-11-13.
Status: implementation and integration in progress. No merge or deployment ran. Review: [draft PR #29](https://github.com/mrwick1/deck/pull/29). The integration branch is `document-bot-setup`. The local `THREAD.md` records its current review link and ownership.

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

## Ceiling and authority checkpoint (`439ab6e`)

Commit `439ab6e` connects the owner ceiling control to the ledger. Schema v6 stores durable requests and response receipts. Admission or a reported overrun creates an owner request. Authenticated responses apply once. Stale or changed responses fail. A denial keeps the ceiling and hold. Approval clears only the ceiling hold and makes accounting-held tasks eligible for the remaining gates.

Status and inbox now expose the ceiling request. The web form calls the authenticated handler. The parent has not exercised that form in bare or tested the complete HTTP authentication path for this new operation.

The agent refreshes the approved job and restrictions before each model call. A definition change invalidates pending tool calls and causes replanning. Revocation and holds are checked before tool dispatch, including direct chats using a configured bot.

Two parent regressions exposed integration defects:

- A late hold stopped the next model call but still allowed a tool returned by an in-flight request. The regression failed on the browser call, then passed after adding the dispatch check.
- A provider charge above the reserved bound created a ceiling hold without an owner request. The regression failed, then passed after making the request durable with the hold.

## Reviewed setup checkpoint (`ca17fa3`)

The Store, authenticated API, driver, private runtime registration and React review screen are connected. See [the setup guide](bot-setup-integration.md) for configuration and API details.

- Schema v7 marks lifecycle snapshots in the existing draft table. Snapshot writes use version checks. Approval records the reviewed job and budget atomically.
- Completion saves the ready marker, checklist and first task in one transaction. The real-database tests cover restart and duplicate completion.
- The serial setup driver checks host holds before side effects. Waits and deferred work consume no retry attempt. Startup reloads approved snapshots.
- Each ready bot gets its own runtime and control routes. Held ready bots still register so the owner can respond without a wake. Chat events and controls follow the selected bot.
- Draft review includes instructions, authority, configured model and resource recommendations, working allowance, absolute ceiling, credential labels and an optional first task.
- Recommendation generation currently uses the job text and configured defaults. It does not call a model or claim job-specific model selection.
- Protected input is unavailable. Credential waits remain durable and their ordinary completion endpoint refuses them. No password input is shown.

Parent tests exposed three provisioning defects before their fixes. The harness accepted an existing disk from a different base, accepted a reviewed disk size that the base could not provide, and accepted an executor binding for another bot. It now rejects each case without changing those files.

The first-task integration test also caught an unsupported task source value. The task now uses the existing owner-message source. The failure rolled back the start claim with the insert; no task was silently lost.

## End-to-end completion wave

The owner authorized eight parallel tasks and selected a local synthetic login first. Integration now runs on `finish-bot-setup-e2e`, based on `6d5ca42`. The publisher branch remains `document-bot-setup` and PR #29 remains a draft. No real account or Gmail work is authorized for this experiment.

The guest secret channel and its small parent review fixes are integrated at `1a1474c`. Its 31 focused tests and all 228 prototype Python tests pass. These use a fake QGA and a temporary guest home. Real guest delivery and bare redaction are not yet verified. See [the completion plan](setup-e2e-plan.md) for task scopes and evidence.

The reference report and protected-input UI are integrated as `34bc352` and `5995b3b`. Parent UI review adds fixed error messages and the guest byte limit. All 43 web tests pass. Browser validation remains open. The failure-test harness returned `a262434`; it remains unintegrated until the missing endpoints land. Its baseline has four passes, one skipped capture gate and eighteen expected endpoint failures.

Challenge handling is integrated as `cb38eab`. Bare baseline evidence is integrated as `0aa1e33`. The parent reconciled challenge DTO fields after a failing regression. All 44 web tests, type checking and the production build pass. These checks do not prove a live sign-in. See the completion plan for the baseline findings.

The parent reproduced the invalid-budget save in a failing Store regression. The save now validates the supplied budget before committing. The combined rerun passes 249 Rust unit tests and four integration tests. Two live tests remain ignored. Formatting and clippy pass.

Two delegates have not reported completion: host lifecycle and real VM acceptance. Their exact task identities and ownership are in the local handoff. Do not start replacement writers or competing live VM experiments while they run.

## Current work and next edits

The earlier reviewed-setup checkpoint is in draft review. Its original six children returned. The new end-to-end wave above has active delegates. The source passes deterministic checks. Keep live acceptance and the remaining product work separate.

1. Inspect the T3 Code protected-input mechanism required by D29. Specify credential storage, lifetime, redaction and guest transfer before collecting any values.
2. Connect protected credential input and supported phone/CAPTCHA interactions. Do not complete a credential wait through ordinary chat.
3. Verify fresh private-computer creation with one owned real VM. Retain image, executor, task, save/restore and cleanup evidence. Keep Gmail and real account state outside this experiment.
4. Verify the review and progress screen in bare at phone and desktop widths. Follow the bare feedback loop for failures.
5. Extend job-specific recommendations and conversational follow-up behavior. The current defaults are explicit in the setup guide.
6. Validate two-bot file, account and artifact isolation before making that claim. The API routing test does not prove VM isolation.
7. Define automatic resource adjustment, spike/no-progress thresholds and period rollover separately. Current harness and capacity limits remain explicit.

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
| Earlier parent checkpoint (`439ab6e`) | 221 Rust unit tests and four non-live integration tests pass. Two live tests remain ignored. Formatting and clippy pass. Web type checking, 26 web tests and the production build pass. |
| Reviewed setup (`ca17fa3`) | 229 Rust unit tests and four non-live integration tests pass. Two live tests remain ignored. Formatting and clippy pass. Web type checking, 27 web tests and the production build pass. |

The first integrated web check failed because dependencies were absent. `npm ci --ignore-scripts` restored the locked dependencies. The rerun passed. The existing bundle-size warning remains. The install reported ten dependency vulnerabilities; no lockfile or dependency version changed. Dependency remediation is outside this slice.

No paid model calls, real account actions, live VM experiments or browser checks ran in this implementation round. Child fake-harness tests do not prove real provisioning. No deployment, service restart or merge ran.

## Known gaps and limits

- The ceiling approval route is connected and covered by Store and handler checks. Complete HTTP authentication and browser acceptance for this operation remain untested. The legacy hold-release route remains separate from ceiling mutation.
- Current authority loads between model steps. The trusted-instruction boundary still applies. Broader behavioral tests of instruction changes and restrictions remain useful.
- Setup is connected to the real Store, worker, API and UI. Live fresh provisioning and browser acceptance remain unverified.
- Protected credential input, credential lifetime and guest transfer are not implemented. Do not collect values in ordinary chat or journal them in command arguments.
- CAPTCHA and phone wait types exist. No supported challenge-completion interaction has been verified.
- Setup includes the reviewed ceiling, a serial driver and runtime registration. Its start claim and initial task commit together. Credential transfer and live acceptance remain open.
- The harness uses fixed CPU and memory settings. Resource recommendations need actual support before the UI claims to apply them.
- Resource changes beyond the current image and selection of an unpriced model are rejected. The configured managed-bot count is not awake-capacity scheduling.
- Budget period rollover, spike thresholds and no-progress policy remain unimplemented. Do not invent a monetary default.
- Request reservation depends on configured provider bounds and pricing. It is not a universal invoice or external-purchase cap.
- Multi-connection races, production database migration, bare UI behavior and live recovery need targeted validation.

## Next delivery gate

Review this integrated checkpoint. Implement protected input next, then validate one fresh VM and the bare UI. Setup authentication now has HTTP tests. Full live ceiling-form acceptance remains unverified. Run the combined Rust and web gates. Use synthetic fixtures and a scripted model. Serialize any later VM checks and retain evidence. Use only bare for browser acceptance.

Keep this handoff current after each integration milestone. Record test results, unresolved gaps and exact commits. Do not label disconnected modules as finished product behavior.
