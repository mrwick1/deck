# Setup end-to-end completion plan

Date: 2026-11-13.
Status: authorized. The owner requested parallel implementation and orchestration. Use a local synthetic login first. Do not use Gmail or real account credentials.

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
