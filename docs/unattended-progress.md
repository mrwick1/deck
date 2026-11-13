# Unattended implementation handoff

Updated: 2026-11-13.
Status: core setup and local API/guest synthetic sign-in pass. Phone UI rendering remains blocked. No merge or deployment ran. Review: [draft PR #29](https://github.com/mrwick1/deck/pull/29). Integration runs on `finish-bot-setup-e2e`; `document-bot-setup` is the publisher branch. The local `THREAD.md` records its current review link and ownership.

## Latest accepted evidence

The [final live report](setup-synthetic-login-final.md) records exit 0. All 55 required API/guest gates pass. The manifest has 57 pass statuses, three optional checks unrun and zero failures. The service records one login, zero failed logins, one submit attempt and one success. Both owner rounds verify service evidence separately from owner answers. Restarts add no work. The cleanup registry has no survivors.

The parent inspected that manifest, checked all registered PIDs are absent and verified the released VM lock. No VM disk or memory-state file remains. Metadata and earlier failed evidence remain private. All delegates are terminal.

Local checks: 303 Rust unit tests, four integration tests, 49 web tests, type checking, build, formatting and clippy pass. The Python suite passes 366 tests with one screenshot gate skipped. No whole-product, real-account, performance or single unmocked UI plus VM claim follows. Bare issues [464](https://github.com/mrwick1/bare/issues/464) and [467](https://github.com/mrwick1/bare/issues/467) retain the phone rendering limits. Raster secrecy and saved-state second-task checks remain unrun.

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

The host lifecycle returned a partial slice, integrated as `67b0d14`. The failure harness is integrated as `da249a4`. Combined acceptance is pending. Parent inspection found an absent-receipt exit-status mismatch and missing active-task presentation/resumption seams. Three isolated follow-ups now own runtime integration, task API/UI and the failure harness. The original real VM delegate has not reported completion. See the completion plan for details. Their exact task identities and ownership are in the local handoff. Do not start replacement writers or competing live VM experiments while they run.

## Current work and remaining validation

Core setup and API/guest synthetic acceptance pass. All assigned implementation and acceptance workers have returned. Keep the draft review open with its explicit limits. Do not rerun the old implementation steps in the historical checkpoints below.

1. Resolve bare rendering issues 464 and 467 in bare. Deck adds no fallback.
2. Validate raster secrecy, saved-state wake with a second task and a single unmocked owner UI plus VM journey separately.
3. Keep real accounts and Gmail outside this synthetic workstream. No production action is authorized here.
4. Review broader deferred work separately: conversational recommendations, memory, capacity scheduling, resource tuning, period rollover and no-progress policy.

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
- Setup is connected to Store, worker, API and UI. Fresh API/guest provisioning and synthetic sign-in pass. UI checks used a fake guest; combined UI plus VM acceptance remains unverified.
- Protected input and guest transfer are implemented. Real guest delivery and local synthetic sign-in pass. Real-account and universal raster acceptance are not claimed. Do not collect values in ordinary chat or journal them in command arguments.
- Phone and CAPTCHA-style responses pass the local service flow through a real VM. Real provider challenges remain outside this run.
- Setup includes the reviewed ceiling, a serial driver and runtime registration. Its start claim and initial task commit together. Credential transfer and required synthetic API/guest acceptance pass; optional visual and lifecycle checks remain open.
- The harness uses fixed CPU and memory settings. Resource recommendations need actual support before the UI claims to apply them.
- Resource changes beyond the current image and selection of an unpriced model are rejected. The configured managed-bot count is not awake-capacity scheduling.
- Budget period rollover, spike thresholds and no-progress policy remain unimplemented. Do not invent a monetary default.
- Request reservation depends on configured provider bounds and pricing. It is not a universal invoice or external-purchase cap.
- Multi-connection races, production database migration, bare UI behavior and live recovery need targeted validation.

## Next delivery gate

Publish the final accepted evidence to the existing draft. Retain rendering and optional validation limits. Do not merge or deploy. Use the latest evidence above; the dated checkpoints below preserve how the implementation reached this state.

## Active-task owner response checkpoint

Commit `733c5ce` adds active-task protected prompts and response reconciliation. Parent review replaces generic steering with an exact-task transactional answer. It also preserves cancelled task bindings for recovery. These changes keep pause, budget and unknown-outcome gates effective.

The combined Rust run passes 271 unit tests and four integration tests. The later cancellation reconciliation test passes separately. Formatting and clippy pass. Web type checking, 49 tests and the build pass. These are deterministic checks. Live task resumption, protected browser rendering and the final HTTP failure suite remain open. The runtime/tool delegate and original live VM delegate have not reported completion.

## Current integrated acceptance checkpoint

The runtime and owner UI delegates are terminal. Their evidence is integrated. The parent fixed sidebar bot selection and long request wrapping. Type checking, 49 web tests and the build pass. A browser recheck remains open. The phone button-color failure is tracked in bare issue 464; Deck adds no fallback.

Private folder preparation (`8d25d6a`) and protocol readiness (`aa06d84`) are integrated. The combined Rust run passes 303 unit tests and four integration tests. Formatting and clippy pass. The HTTP/fixture suite passes 31 tests with one screenshot gate skipped after correcting an asynchronous next-wait assumption. No live guest sign-in pass is claimed.

The synthetic-login harness extension is the only active implementation delegate at this checkpoint. All previous live VM processes have been cleaned up. The next live run must use one fresh overlay and the shared VM lock. Keep performance claims separate. Live protected delivery, whole synthetic sign-in, raster secrecy and final browser rechecks remain open.

## Synthetic-login harness integration

Commit `ed6bd71` is integrated as `ab53a1e`. The parent reproduced all 86 focused harness tests. The mode now requires the service login, both owner-wait rounds, one final submission and restart persistence. It refuses missing required gates. No VM ran for these unit checks. The full prototype suite must not be rerun during the active VM check; its preflight tests require an idle host.

Three acceptance tracks remain distinct. The sidebar/phone browser recheck owns its fake-guest stack. The serial live worker owns the only VM and runs the prior baseline and protected modes. The new whole synthetic-login mode will run after that VM is cleaned up. The draft review remains open. No merge or deployment ran.

## Latest live and browser evidence

The serial live worker is terminal and cleaned up. Report `7558e42` confirms private folders, protocol readiness, one browser/shell/file task, restart counts and idle quiet. Its protected HTTP delivery passes, but two inherited harness defects prevented complete acceptance. Both now have failing regressions and fixes. The full Python suite passes 362 tests with one screenshot gate skipped. No real synthetic-login pass is claimed yet.

Report `9d091f1` confirms sidebar routing and keyboard controls. Bare issues 464 and 467 retain the phone rendering and unbroken-text failures. No Deck fallback is added. Owned UI fixture directories were removed after their workers stopped. The next VM run must use the complete synthetic-login mode serially.

## Whole-service restart repair

The first real synthetic-login run reached successful login and preserved its phone wait across restart. It then failed on a harness name error. Report `4ccd4c2` keeps that failed run explicit. The parent fixed the name, corrected cleanup's launcher false positive and added whole-mode regressions. A faithful rendered-page emulator also required an explicit read-only refresh before checking owner actions.

All 366 deterministic prototype tests pass; one screenshot gate remains skipped. Rust and frontend code are unchanged from the 303-unit/four-integration and 49-web-test checkpoint. The complete live owner rounds and final submission remain unverified until the rerun. No real account, merge or deployment was used.

## Behavior-complete live checkpoint

Report `2d3a463` records a live run with all behavior gates passed. Service counters are one login, zero failed logins, one submit attempt and one success. All 19 tool calls ran once. Owner answers remained pending until service evidence was refreshed and verified. Restart added no work. Host and argv scans found no value. The aggregate gate remains failed because cleanup reported one unidentified path match.

The parent now tracks owned process identities and descendants with birth-time checks and auditable survivors. Its regression passes. The complete Python suite passes 366 tests with one screenshot gate skipped. A fresh serial run is required for the repaired aggregate audit. No other worker or VM remains active. No merge or deployment ran.
