# Unattended bot implementation plan

Date: 2026-11-13.
Status: implementation proposal. No implementation or deployment is authorized by this document alone.

## Goal and authority

Implement the approved requirements in D57–D61 of [decisions.md](decisions.md). Create bots through reviewed conversational setup. Give each bot its own computer. Let it complete its approved job without routine action approvals. Let it raise its working budget below an absolute ceiling that only the owner can change. Preserve progress during unattended operation and requests for help.

The five planning agents supplied recommendations. Their timing, retention, scheduling and interface defaults remain proposals. This plan does not approve those defaults. Gmail work belongs to another workstream. Do not change private Gmail tasks or account state.

## Verified starting point

This checkpoint comes from code inspection. No runtime tests ran for this plan.

| Area | Current code | Required change |
| --- | --- | --- |
| Configured bot | `src/config.rs` has one optional VM configuration. `src/bot/mod.rs` owns its runtime. | Add durable bot definitions and setup drafts. Keep existing configuration usable during migration. |
| Durable runtime | `src/bot/db.rs` stores task and hold state. Executor records live in `src/executor/journal.rs`. | Extend existing state rather than creating a second task or action journal. |
| Budget hold | `hold_budget` and `approve_budget` in `src/bot/mod.rs` set and clear a flag. `src/bot/api.rs` exposes approval without an amount. | Replace unqualified clearing with accounted allowance and owner-only ceiling changes. |
| Model calls | `src/model.rs` streams text, tool calls and finish events. Its stream interface has no usage event. `src/agent.rs` calls it. | Capture usage, bound requests and account for dispatch, settlement and uncertain charges. |
| Owner interface | `web/src/components/BotBar.tsx`, `web/src/lib/api.ts` and `web/src/lib/bot.ts` expose controls and budget state. | Show authority, working budget, ceiling, setup progress and durable owner requests. |
| VM lifecycle | `src/bot/vm.rs` calls the harness in `scripts/vm-prototype`. | Add repeatable private-computer provisioning without losing lifecycle fencing or saved-state behavior. |

Existing pause, desktop ownership and unknown-outcome behavior remain constraints. Full authority is a trusted instruction under D33. It does not create a hard action barrier around raw desktop, browser, shell or API access.

## Stage 0. Specify the accounting contract

Resolve these details before coding budget changes. Record accepted choices in decisions.md. These are proposed implementation choices, not additional approved requirements.

- Use a per-bot ceiling for a stated accounting period and currency. Store the period explicitly. Recommend a calendar month in the owner's saved timezone. Require an amount during setup rather than inventing a monetary default.
- Define whether the ceiling covers model charges alone or other metered services. Report unmetered purchases separately. Full job authority does not establish universal financial enforcement through raw tools.
- Keep prices in configuration. Use integer monetary units at a documented precision. Record price versions. Never embed model endpoints, credentials or prices in tests as production configuration.
- Define a bounded automatic increase rule from remaining work and recent usage. Specify an increment limit and the minimum useful allowance. Clamp every increase to the ceiling. A model recommendation is input to this rule, not permission to alter the ceiling.
- Define spending-spike and repeated-no-progress thresholds. Distinguish a task investigation hold from a ceiling hold. Avoid an approval request for every working-budget increase.
- Define reservation bounds for request input, output and provider-specific charges. Unknown pricing or missing usage must produce an explicit accounting state. Do not treat them as zero cost.
- Specify period rollover. Recommend retaining an existing ceiling hold until the owner responds. Do not implement this recommendation as approved behavior without review.

Gate: a reviewer can compute admission, increase, settlement and hold outcomes from concrete examples, including an in-flight request near the ceiling. No unresolved accounting semantics remain in the implementation slice.

## Stage 1. Persist authority and spending state

Work primarily in `src/store.rs`, `src/bot/db.rs`, `src/config.rs` and their existing tests. Keep shared schema migrations in the existing Store initialization path.

- Add versioned bot definitions, approved job instructions and authority state. Default newly reviewed setup to full job authority. Preserve explicit restrictions, revocation and owner identity history.
- Add setup drafts separately from active definitions. Draft text and recommendations cannot authorize execution.
- Persist working allowance, absolute ceiling, period, currency and policy version. Keep holds typed by reason. An allowance change must not clear automation pause or uncertain-outcome holds.
- Add durable request and charge identities. Store reservations, reported usage, settlement and unresolved exposure outside VM state.
- Permit ceiling changes only through an authenticated owner operation. Expose no ceiling-change capability to the bot's tool set. Record old and new values with an idempotency identity.
- Migrate existing configured bots without silently granting broader authority or inventing a ceiling. Preserve existing holds and require owner review of newly required configuration.

Write failing tests first for migration, draft isolation, owner-only ceiling mutation, duplicate updates and independent hold reasons.

Gate: migrations preserve existing tasks and journals. Restart retains authority, restrictions, monetary state and pending requests. A bot-originated write cannot alter a ceiling through the supported control API. This gate does not claim to constrain all raw shell access.

## Stage 2. Account for model calls and increase budgets automatically

Work primarily in `src/model.rs`, `src/agent.rs`, `src/bot/mod.rs`, the shared store and tests.

- Parse provider usage separately from content choices. Preserve usage-only terminal stream chunks. Request usage only through supported configured provider behavior. Keep streaming chat and tool assembly intact.
- Set request output bounds. Compute a conservative charge reservation before paid dispatch. Account for setup, summaries, retries and other Deck-initiated model calls, not only visible task replies.
- Reserve allowance transactionally. If the reservation exceeds the working allowance, apply the accepted automatic increase rule. Proceed without an owner prompt when the reservation fits below the ceiling.
- When the reservation cannot fit below the ceiling, create or update one durable ceiling-increase request. Hold affected work before dispatch. The bot cannot clear that hold by changing its working allowance.
- Settle each request once. Preserve uncertain exposure after a dropped reply or restart. Do not free a reservation merely because a timeout occurred. Reconcile reported usage without double counting.
- Store the reason for each automatic increase and emit the approved notification. Do not introduce a new external notification channel.
- Pause the affected task for investigation on the accepted spike or no-progress rule. Continue unrelated eligible work only under existing ownership constraints.

Write failing tests first for usage-only chunks, concurrent admission, lost responses, duplicate settlement, automatic increase and insufficient ceiling allowance. Use a scripted local model endpoint and supplied test pricing. Do not call a paid model.

Gate: a synthetic task crosses its working allowance without asking for approval. It never dispatches a priced request whose reservation exceeds its remaining ceiling. Restart and event delivery cannot bypass the hold. Report limits of reservation estimates and external spending explicitly.

## Stage 3. Apply full job authority and owner-only controls

Work primarily in `src/prompt.rs`, `src/agent.rs`, `src/bot/mod.rs`, `src/bot/api.rs` and the web API types.

- Supply current approved instructions, full-authority scope and restrictions to each model step. Refresh changed authority before subsequent work. Do not rely on stale chat summaries for grants.
- Do not prompt for sending, posting, submitting or deleting when the approved job covers the action. Request additional authority only for a scope extension or an explicit restriction change.
- Keep authenticated owner routes separate from bot tools and machine event intake. A source event or page cannot approve setup, widen authority, revoke an owner restriction or raise a ceiling.
- Replace the amount-free budget approval route and generic Continue control. Show the current ceiling and requested change. Apply a versioned owner response once, then re-evaluate all remaining holds.
- Keep stop, revocation, pause and unknown outcomes independent from monetary continuation. Record execution-stop acknowledgement separately from evidence of external effects.

Write failing tests first for routine actions without approval, explicit restriction handling, revocation between steps, external-content grant attempts and stale ceiling responses.

Gate: a scripted fixture job sends, submits and deletes within its authority without owner prompts. Owner revocation blocks subsequent instructed work. Raising a ceiling does not resume paused or unresolved tasks.

## Stage 4. Build reviewed setup and private provisioning

Work in the existing bot store, authenticated API, VM lifecycle and React application. Add focused modules only where existing code has no suitable home.

- Turn the setup conversation into a persisted draft. Recommend a name, model, instructions, authority, tools and computer resources. Include the owner-set spending ceiling. Keep every field editable.
- Approve a specific draft revision. Create the bot and computer only after approval. Duplicate approval or a retry must not create a second computer.
- Provision one private disk and runtime directory from the supported image path. Track allocation and partial failures. Do not mount host user data or introduce another browser.
- Persist named setup steps and attempt counts. Default safe setup steps to three total attempts. Waiting for the owner consumes no attempt. Investigate unknown external outcomes under D34 rather than treating them as safe retries.
- Finish system setup before requesting credentials. Use protected input linked to a request identity. Keep values out of chat, model context, logs and artifacts. Specify credential storage and lifetime before implementing this substep.
- Preserve phone-approval and CAPTCHA waits across restart. Define a supported owner challenge-completion path before promising CAPTCHA completion. This plan does not assume general desktop takeover already exists.
- Show progress and blockers in the bot's chat. Continue independent setup only when session ownership permits it. Finish with the approved checklist. Start specific work already provided, or announce readiness and ask for instructions.
- Support automatic tool installation and resource changes at safe lifecycle boundaries. Record significant changes. Queue capacity that cannot fit rather than silently overcommitting the host.

Write failing tests first for draft persistence, revision conflicts, duplicate provisioning, partial allocation recovery, retry exhaustion and expired challenge response.

Gate: a fresh synthetic setup survives restart before approval, during provisioning and during an owner wait. No computer exists before approval. No secret value reaches stored chat or scripted model requests. A ready bot starts its supplied task once.

## Stage 5. Prove unattended continuity

Extend the existing runtime and executor suites. Use synthetic tasks before one serial real-VM smoke.

- Persist a concise checkpoint with completed prerequisites, next work, pending requests and references to unresolved actions. Reuse durable task and command state. Do not store private reasoning or credentials in checkpoints.
- Reconcile host and guest records before resumed dispatch. Preserve unknown outcomes, attempts, ownership and independent holds across restart and restore.
- Continue independent work only when it cannot disturb a waiting session. Keep conservative queueing where that independence is not established. Do not add broad concurrent execution merely to meet this requirement.
- Return the owner to the exact task, blocker and relevant evidence. Notification dismissal cannot resolve a request. Revalidate stale responses.
- Preserve disk and saved-state artifacts on ambiguous lifecycle failures. Do not replace a failed restore with an unannounced fresh computer.

Gate: interrupt a scripted task before dispatch, after command completion but before receipt, during budget reservation and during an owner wait. Recover without duplicate external effects or silent hold removal. Save and restore the computer between two tasks. Verify that idle model calls and capture counters remain flat without a caller.

## Verification and delivery

Each implementation slice starts with a failing regression for its behavior. Run relevant deterministic checks before broader validation. Use the existing commands:

- `cargo test`
- `cargo fmt --check`
- `cargo clippy --all-targets -- -D warnings`
- In `web/`: `npm run check`, `npm test` and `npm run build` when web code changes.

Discover the existing Python suite command before changing harness code. Do not guess a new runner. Use bare only for UI acceptance. If bare fails, follow `feedback-loop.md`. Never substitute another browser. Protected credential tests use synthetic secrets outside committed fixtures. Live VM tests use owned private overlays and clean up only their own processes and artifacts.

Deliver stages as small branches and PRs. Keep accounting and authority changes separate from provisioning where possible. Update progress and decisions with verified results and known limits. Implementation completion does not authorize deployment, production account actions or a merge.

## Deferred scope

Service-specific triggers, full shared inbox redesign, automatic memory retrieval, retention defaults, semantic search and multi-bot capacity benchmarks remain outside this implementation plan. Keep the existing event path working. Verify isolation before claiming multiple-bot support. Do not treat long unattended duration as a session-count target.

## First implementation slice

Start with Stage 0's accounting contract and Stage 1's durable state. Do not replace the current hold with automatic continuation until admission, settlement and owner-only ceiling mutation have passing tests. Full conversational provisioning follows those foundations.
