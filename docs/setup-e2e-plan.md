# Setup end-to-end completion plan

Date: 2026-11-13.
Status: required API/guest synthetic-login gates pass. The owner selected a local synthetic login first. UI checks remain a separate evidence layer. Bare issues 464 and 467 block phone rendering. Do not use Gmail or real account credentials.

## Acceptance target

Create a new bot through the real Deck UI. Approve its setup. Provision its private computer from a clean frozen base. Supply synthetic credentials through protected input. Complete supported phone-approval and CAPTCHA-style owner waits against a local fixture. Run the supplied first task. Restart Deck during an owner wait and after task dispatch. Preserve progress and avoid duplicate external effects. Check the flow in bare at phone and desktop widths.

No protected value may enter model context, ordinary chat, tool arguments, command journals or retained screenshots. A secret delivery receipt may contain identifiers and status, but no value. A wait cannot complete before delivery succeeds. Expired or stale requests must fail safely. Unsupported real challenges must remain blocked; a fixture pass does not prove every service's CAPTCHA support.

## Parallel work

Eight isolated tasks started from `6d5ca42`. Their identities and initial states are recorded in the local handoff. Each task gets one writer and a narrow scope.

| Task | Ownership | First delivery |
| --- | --- | --- |
| Protected-input reference | Installed T3 Code mechanism and `docs/protected-input-design.md` | Inspect D29's required source. Specify transport, storage, lifetime, receipts and redaction. Do not copy unlicensed source. |
| Guest secret channel | Harness secret delivery and Python tests | Deliver through a private channel. Keep values out of argv and command journals. Apply guest file permissions and return metadata only. |
| Host secret lifecycle | Protected API, durable receipts and model-facing secret references | Validate owner, bot and request. Complete the wait only after delivery. Handle retry, restart, stale responses and active-task requests. |
| Login challenges | Durable phone/CAPTCHA request state and owner responses | Keep task/session ownership. Verify success before resuming. Handle expiry and unsupported challenges. |
| Protected-input UI | Chat prompt, setup progress and client API | Submit directly to the protected endpoint. Clear transient values. Never send them as messages or persist them in the browser. |
| Real VM acceptance | One private fresh VM, fixture and scripted model | Own the only live VM experiment. Prove baseline creation, initial task and restart. Prepare integrated protected-input checks. |
| Bare UI acceptance | Dedicated bare session and isolated screen | Check the real UI against a throwaway stack. Retain sanitized evidence. File bare issues for browser failures. |
| Failure and leakage tests | Synthetic API/restart and evidence checks | Exercise the agreed endpoints. Search retained state and traces without printing protected values. Cover stale delivery, duplicate response and crash windows. |

The first tasks may deliver independent pieces while dependencies run. That does not complete an integrated gate. The orchestrator reconciles interfaces, integrates commits and launches the final acceptance checks against that exact head. No competing live VM experiment may run.

## Shared contract to reconcile

The protected setup endpoint is planned as `POST /api/setups/{id}/secret`, with the current snapshot `version`, `request_id` and `value` in the request body. The owner session supplies identity. Values never go in URLs, normal chat endpoints or response bodies. The server derives the credential label from the pending request.

The guest channel must consume protected content on stdin or an equivalent private transport. It must validate the bot binding. Model-facing calls use a named reference such as `${FIXTURE_PASS}`, never the value. Exact receipt and active-task challenge interfaces must follow the inspected reference and existing Store/task semantics. No task may silently invent incompatible schemas or mark a test adapter as production enforcement.

The secret lifecycle and reference tasks must inspect the installed T3 Code implementation before selecting a port. If that source cannot establish a mechanism, report the gap before accepting real input. Local synthetic fixtures may continue independently.

## Execution boundaries

- Use only bare. Never launch another browser or add a fallback.
- Use hyprcage before host graphical work. Isolate screens and profiles. Destroy only owned screens at handback.
- Use generated synthetic accounts and credentials. Keep private configuration and raw evidence outside Git. Report hashes and sanitized results rather than values.
- Use a scripted local model. Do not call paid models for acceptance.
- Do not deploy, merge, restart user services or touch existing account sessions.
- Give each implementation task its own prepared worktree and build directory. Keep resource benchmarks separate from functional acceptance.
- End each task with exact commits, checks, untested behavior and remaining integration seams. Record a blocked browser or live gate explicitly.

## Review and documentation

The current draft is PR #29. CI passed at `490ae31`. That does not validate future commits. Update `unattended-progress.md`, `bot-setup-integration.md` and the local handoff after each integration milestone. Keep every task's ownership and terminal state. Review the full diff and reproduce the acceptance evidence before changing the draft's readiness.

## Guest-channel checkpoint: 2026-11-13

The guest channel returned first. Commits `7a8398b` and `8446c65` were integrated as `758f471` and `5677e33`. Parent fix `1a1474c` adds current-value validation for completed receipts, private-file mode repair and support for existing setup wait identities.

All three parent regressions failed before the fixes. The secret suite now has 31 passing tests. The complete prototype Python suite has 228 passing tests. Fake QGA tests run the real guest script in a temporary home. They do not prove real QGA, `runuser`, bare parsing or redaction.

The host integration must validate receipt bot, request and canonical label. The API must keep a wait pending after any failed delivery. T3 reference inspection, host delivery, challenge flow, UI and live acceptance remain outstanding. No protected-input or end-to-end completion claim follows from this checkpoint.

## Reference and UI checkpoint: 2026-11-13

The protected-input reference report is integrated as `34bc352`. It distinguishes inspected T3 behavior from proposed Deck behavior. The frontend slice is integrated as `5995b3b`. It submits protected values directly to the dedicated endpoint and shows challenge verification as pending. No browser acceptance has run on this integrated UI.

Parent review added regressions for value-bearing exception messages and the guest UTF-8 size limit. Both tests failed before the fixes. All 43 web tests now pass. Type checking and the production build also pass. The existing bundle-size warning remains.

The failure-test worker returned commit `a262434`. Its baseline has four passing tests, one skipped capture gate and eighteen failing endpoint checks. Integration waits for the host and challenge implementations. This is dependency evidence, not an end-to-end pass. The host lifecycle, login challenges, real VM and bare UI workers have not yet reported completion.

## Challenge and browser-baseline checkpoint: 2026-11-13

Challenge commit `01af1e4` is integrated as `cb38eab`. The state distinguishes an owner answer from verified service success. The old completion control refuses task login waits. The Tools adapter and real service verification remain open. The child reports 19 new deterministic tests; parent verification is in progress.

Bare baseline report `22ed7c5` is integrated as `0aa1e33`. It checked the real baseline UI with a fake guest at desktop and phone widths. Review, approval and wait persistence passed. It did not test protected input, a first task or a real VM. See [the full baseline](setup-ui-baseline.md).

Parent integration found different challenge field names in the UI and backend. A new regression failed before the fix. The UI now reads `instructions`, `match_number` and `answered`. Answered task challenges remain pending after their original expiry. All 44 web tests, type checking and the production build pass. Credential labels now retain spaces and line breaks during editing; normalization occurs only on save. Browser confirmation of this edit remains open.

The baseline also found invalid budget drafts accepted by the save path. The Store regression failed on the accepted invalid save: 248 other Rust tests passed. The parent now validates a supplied budget before committing a snapshot. The rerun passes: 249 Rust unit tests and four integration tests. Two live tests remain ignored. Formatting and clippy pass. The host lifecycle worker still owns `setup_api.rs`; the parent does not edit that file while the worker runs. Remaining findings include title synchronization, pause visibility and executor readiness. They require explicit verification before closure. Host lifecycle and real VM acceptance have not reported completion.

## Host integration checkpoint: 2026-11-13

Host commit `9378c28` is integrated as `67b0d14`. Failure harness `a262434` is integrated as `da249a4`. These changes have not passed combined acceptance. The host slice adds schema v8 metadata, protected setup and task endpoints, named secret requests and a capture gate. Its child tests use a fake guest.

Parent inspection found a guest receipt mismatch. The real guest returns exit 1 with `not_delivered` for an absent receipt. The host expects a successful false reply. Active-task requests also lack a visible protected form, and owner challenge answers do not yet wake the waiting task. Capture gates need runtime wiring. These are integration blockers.

Three isolated follow-up tasks started from `da249a4`: runtime/guest contract and challenge tools; active-task API/UI and owner resumption; and the HTTP failure/leakage harness. Each has separate file ownership. No new live VM experiment started. The original real VM worker remains the only live VM owner and has not reported completion. Final bare and live acceptance wait for the combined implementation.

## Failure-harness recheck checkpoint: 2026-11-13

Recheck `0e6141a` is integrated as `c9807c1`. The child reports 22 passes, seven receipt-probe failures and one skipped screenshot gate on `da249a4`. Parent review found and corrected an additional fixture mismatch: a completed identity must not restore an overwritten secret. Its fixture-only regression failed before the correction and passes after it. The seven-failure report remains historical evidence, not a result for the corrected combined head. Runtime and task UI follow-ups remain active. The original live VM worker has not reported completion.

## Active-task integration checkpoint: 2026-11-13

Task prompt commit `419c396` is integrated as `733c5ce`. Authenticated `GET /api/bots/{id}/secret-requests` returns pending task request metadata only. Chat shows those requests beside setup waits. Task input posts directly to the protected task endpoint. Delivered requests and expired requests have reconciliation paths. No live task-resumption or browser pass follows from this integration.

Parent review removed generic steering from owner-answer delivery. The targeted sender now checks the exact waiting task and inserts its answer in one database transaction. It never creates a task or steers another task. Duplicate answer insertion applies once. Tests cover a task replaced before the answer and independent pause/budget holds.

Cancellation now retains its task binding in the resolved snapshot. A failed cancellation wake can be reconciled after reload. The optional field preserves old snapshot decoding. A regression for the missing binding failed before implementation. The storage and recovery tests pass after the fixes. Cancellation metadata can be saved under spending holds; dispatch still obeys the holds.

Verification: the combined Rust run passes 271 unit tests and four integration tests. A subsequently added cancellation reconciliation test also passes. Two live tests remain ignored. Formatting, clippy, web type checking, all 49 web tests and the production build pass. The existing bundle-size warning remains. The HTTP failure harness must run again after the pending receipt-contract fix. Runtime/tool integration and the original live VM worker have not reported completion.

## Combined runtime checkpoint: 2026-11-13

Runtime commit `1e0e93f` is integrated as `7ced5ae`. It accepts only the real guest's exact absent-receipt response, rechecks existing guest receipts, installs capture privacy in `Bot::new` and adds task help/status/verification tools. Parent alignment changes the wake instruction to the real `owner_help_status` tool and corrects the remaining API test fixture's exit status. The fixture regression failed before that correction.

Parent verification now passes 288 Rust unit tests and four integration tests. Two live tests remain ignored. The real HTTP/SQLite synthetic failure suite passes 30 tests with one explicitly skipped screenshot gate. Formatting and clippy pass. These tests use a scripted model and fake guest. They do not prove real VM transport, bare rendering, active-task runtime resumption or raster secrecy. The final runtime and browser acceptance checks remain open. The original live VM worker has not reported completion.

## Final acceptance assignments

Two isolated checks started from `c9153e2`. The browser check uses the running throwaway Deck stack, scripted model and fake guest. It checks desktop and phone review, protected entry and challenge presentation in bare. It owns cleanup of that stack and its isolated screen. The runtime check uses the real Python executor and scripted model to exercise the production targeted sender, holds and restart. It owns tests only.

Neither check may launch a live VM. The original fresh-VM worker retains exclusive ownership of that gate. Browser privacy, task resumption and real guest transport remain unverified until evidence returns. No merge or deployment is authorized.

## Browser result and remaining owner-flow checks

Report `3b615d4` is integrated as `9fe5b80`. The browser run checked the real page with a fake guest. Setup protected entry, field clearing on submit, delivery, restart persistence and scanned surfaces passed. Password pixels showed dots. The phone check covered label persistence through DOM metrics; a full phone visual review remains open. Challenges, active-task input, keyboard controls and first-task completion did not run. Its stack and browser sessions are closed.

A new browser check now owns three explicitly seeded ready bots: phone approval, CAPTCHA-style help and active-task protected input. Automation is paused. This fixture tests owner presentation, response routing and durable answers. It cannot prove a genuine service login or a real guest first task. The recheck also covers Clear/Escape, phone rendering and the reported two-click review behavior. The worker owns fixture and screen cleanup. The actual-runtime and original live-VM checks remain active.

## Actual runtime and live baseline findings

Runtime acceptance `d2323ba` is integrated as `2151df7`. Seven new tests exercise the real targeted sender, Store, Python executor and scripted model with a fake VM. The child reports 295 unit tests pass. Tests cover one task, one durable answer and one side effect across holds and restart. The phone setup and delivery receipts are explicitly seeded. They do not prove HTTP delivery or a real service login.

Parent review adds a receipt-checking sender to the task HTTP and list-reconciliation paths. A recovered protected delivery now checks current guest evidence before it can append an answer. A new regression failed before the checker existed and passes after it. The checker refuses a missing receipt without a wake; a matching receipt permits one wake. Combined reruns remain pending.

Live harness `289c95b` is integrated as `2778965`. The original worker has shut down its VM. Its baseline proves private provisioning, identity, restart persistence and idle quiet. It does not pass the first browser task. Missing per-bot data directories prevent artifact creation. Desktop startup also appears to lag setup readiness; this timing diagnosis remains an inference until a readiness probe establishes it. Protected live delivery and challenge gates remain open. No diagnostic directory precreation may count as a pass. Further live checks must remain serial and use a fresh owned overlay.

## Live-blocker implementation assignments

Two isolated tasks started from `fa27294`. One creates validated private host directories before runtime registration. The other establishes a bounded protocol readiness check before setup can finish. Both must produce failing regressions and report exact fixture contracts. Neither may launch a VM. The live worker is terminal and has released its VM. A new serial live acceptance run will start only after these fixes integrate. The seeded owner-UI check remains active.

## Seeded owner UI evidence and review fixes

Report `0f90a62` is integrated as `7fa4198`. Seeded phone approval and CAPTCHA cancellation preserve task identity and pause. Active-task protected input clears on Escape, Clear and submission. Delivery and restart checks show one guest entry and no scanned leaks. The worker closed its sessions, screen and stack. The proxy's incomplete stream read appeared during shutdown; the report does not identify it as a failed product gate.

The worker filed [bare issue 464](https://github.com/mrwick1/bare/issues/464) for missing CSS color support. Deck adds no color fallback. It also confirmed that a new draft already opens its review; the old button label made the first toggle look like an ignored click. The parent now labels that state Hide review and exposes its expanded state.

Sidebar navigation previously changed the chat without its bot scope. The parent adds optional bot identity to chat-list responses and selects that identity with the sidebar chat. A Store regression failed before the association existed and passes after it. Plain chats retain no bot identity. Long request text now wraps within the thread and owner panels. Web type checking, 49 tests and the build pass. A browser recheck is required for navigation and phone layout. The full Rust rerun is in progress.

## Private storage and protocol readiness integration

Readiness commit `461fed6` is integrated as `aa06d84`. Private-folder commit `9608750` is integrated as `8d25d6a`. Both live blockers now have deterministic regressions and fixes. The parent added an explicit `ready` command to the synthetic guest fixture; its contract test failed before the command existed. Unknown fixture commands remain errors.

The combined Rust run passes 303 unit tests and four integration tests. Formatting and clippy pass. The full Python run exposed one asynchronous test assumption: the response had completed before the driver opened the next credential wait. The corrected test waits for that durable transition. Its 32-test HTTP/fixture suite passes with one screenshot gate skipped. No live pass follows from these checks.

A separate harness task now extends the local fixture into an actual synthetic sign-in with phone and CAPTCHA-style waits. It owns only the live acceptance script and fixture tests, and may not launch a VM. The current protected mode proves delivery but does not yet exercise that whole service flow. Fresh serial live checks and the sidebar/phone recheck remain required.

## Post-blocker acceptance assignments

Two checks started from `b48fcd9`. The browser check owns a fresh seeded, paused fixture stack and verifies sidebar bot routing, long request layout and review-toggle wording. It owns stack and screen cleanup. The live check is the sole VM owner. It runs baseline and protected delivery serially on fresh overlays, with the shared VM lock and normal host-pressure limits. It may not use diagnostic directory precreation.

The synthetic-login harness implementation continues independently without a VM. These three tasks have separate file ownership. Their results must distinguish fixture checks, real guest delivery and whole-service sign-in. No merge or deployment is authorized.

## Whole synthetic-login harness integration

Commit `ed6bd71` is integrated as `ab53a1e`. The parent reproduced 51 acceptance-gate tests and 35 local-service/model-script tests. These 86 tests start no VM. The whole-service mode requires actual guest login, both owner-wait rounds, independent service evidence and one final submission. Its live run must wait for the current VM owner to finish and clean up. Final raster, saved-state wake and second-task gates remain explicit.

## Live confirmation and final harness corrections

Live report `ab05455` is integrated as `7558e42`. The fresh VM confirms normal directory creation, the readiness probe, browser/shell/file work, restart counts and idle quiet. Its fixture-visit gate failed because the harness discarded the fixture's stdout. Protected HTTP delivery passed but its guest-file check crashed because it supplied bytes to a text-only QGA API.

The parent added two regressions. Both failed before the fixes. The fixture output now reaches the visit log, and the guest value check passes text on stdin. The complete prototype Python suite now runs 363 tests: 362 pass and the screenshot gate is skipped. This is deterministic evidence. The final real synthetic-login run remains pending.

Navigation report `afc10e3` is integrated as `9d091f1`. Sidebar bot routing, review toggle, keyboard entry and seeded responses pass. Bare issue 464 blocks phone colors. [Bare issue 467](https://github.com/mrwick1/bare/issues/467) blocks unbroken-text wrapping. Deck adds no fallback. The owned UI fixtures and their synthetic values were removed after cleanup; sanitized reports and external screenshot evidence remain.

All previous delegates and VM processes are terminal at this checkpoint. The next real run will use the complete `synthetic-login` mode, one fresh overlay and the shared VM lock. No competing VM run may start. Raster secrecy, saved-state second-task and phone rendering remain separate limits.

## Whole-service live run assignment

The complete synthetic-login live run started from `7ae4a72`. It is the sole VM owner. It uses the verified frozen base, one fresh overlay and default host-pressure limits. The worker must retain every failed gate and clean up before returning. All previous VM and browser workers are terminal. The optional raster, saved-state wake and second-task gates remain explicit. The draft PR will receive the final evidence after integration.

## Whole-service restart regression and rerun preparation

The first whole-service live report `ffd65c9` is integrated as `4ccd4c2`. It proves exact special-character guest delivery, successful service login through bare and preserved phone-wait ownership after restart. It stopped on a misspelled counter helper before the owner rounds. The failed acceptance result remains recorded.

The parent added orchestration tests through both restarts and a changed-counter rejection test. Both failed on the typo. Cleanup now excludes launcher ancestors while still reporting child and reparented processes that retain the run path. Its new regression failed before the helper existed.

A rendered-page emulator exposed another harness assumption: reading a page does not refresh server state. The script now reloads each read-only challenge page before reading its evidence. The corrected emulator's integration test failed before this change and passes after it. The owner sequence now contains status, refresh, read and verification.

The full prototype suite runs 367 tests: 366 pass and one screenshot gate is skipped. These are deterministic checks. A new serial live rerun must use this repaired head. No other delegate or VM is active. The reported relay outside the live run has unverified ownership and was not touched.

## Repaired whole-service live rerun assignment

A new exclusive live rerun started from `5007996`. The worker may run the complete mode once on a fresh overlay with normal pressure limits. It must verify both owner rounds, refreshed service evidence and the final single submission. All previous workers are terminal. The failed earlier run remains retained. The new report must distinguish required gates from optional raster and saved-state checks.

## Behavior-complete live run and owned-process audit repair

Report `ff66d60` is integrated as `2d3a463`. Every live behavior gate passes: exact special-character delivery, both owner rounds with independent refreshed service evidence, one submission, restart persistence and zero scanned leaks. The aggregate result remains failed because cleanup counted one unidentified process naming the output path. The observer explanation is a hypothesis; the original manifest is not changed.

The parent replaced path matching with registered process identities. Subprocess roots and verified VM PID files supply ownership. Descendants are captured before their parents stop. The audit compares process birth times, retains reparented helpers and ignores reused PIDs and unrelated observers. The manifest now records registered identities and survivors. A new regression failed before this registry existed.

A fixture test also incorrectly required a repeated completion to fail. The endpoint permits an idempotent answer. The corrected test pauses the driver and requires 204 with the same snapshot, version, installer count and holds. The complete Python suite passes 366 tests with one screenshot gate skipped. A final fresh live run must verify the new audit and the aggregate gate.

## Tracked-cleanup live acceptance assignment

The next exclusive live run started from `7118ba2`. It must preserve the already-passing behavior gates and verify the registered process audit. Its manifest will contain the registered identities and any survivors. All prior workers are terminal. The worker may run once, retain failures and clean up before returning. No competing VM may start. The optional visual and saved-state limits remain unchanged.

## Final accepted API/guest checkpoint

Report `86d3cdf` is integrated as `5c7df33`. The fresh real-VM run exits 0. It records 60 gates: 57 pass, three optional checks not run and zero failures. The aggregate confirms all 55 required gates. Both owner rounds, exact special-character login, one submission, restart persistence and zero scanned leaks pass. The registered cleanup audit has no survivors.

The parent inspected the raw manifest, verified every registered PID is absent and acquired the released VM lock. No guest disk or saved-memory file remains. Runtime metadata is retained; the parent does not claim that the metadata directory is empty. Failed earlier manifests remain unchanged.

This closes core API/guest synthetic acceptance. It does not close bare rendering issues 464/467, raster secrecy, saved-state second-task checks or a single unmocked UI plus VM journey. All delegates are terminal. No live process, merge or deployment is owned by this workstream. The draft PR retains these limits.

## Continued remaining acceptance

The owner asked to continue after the published checkpoint. Three isolated tasks started from `7c2be62`. The saved-state task owns the only live VM in this wave. It must prove a saved-state restart, restore and a second task with preserved guest state and one effect. The combined UI/VM task prepares a test controller and runs no VM or browser yet. The capture task tests privacy during in-flight frames and fixes only confirmed races.

File ownership is separate. The first task owns its lifecycle harness. The second owns new UI controller files only. The third owns capture-related Rust code and tests. All must provide regression evidence, exact boundaries and cleanup. After integration, the parent will start the combined UI and real-VM run serially. No merge, deployment, real account or paid model is authorized. The previous accepted evidence remains valid for its stated head.

### Combined UI controller integrated

The controller and its 41 unit tests are integrated. The parent reproduced all tests. No live UI/VM run has occurred. The controller owns the test stack and cleanup. The browser owns all approval, protected-input and challenge form actions. The capture-privacy review must settle before the combined run starts.
