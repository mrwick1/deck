# Setup end-to-end completion plan

Date: 2026-11-13.
Status: authorized. The owner requested parallel implementation and orchestration. Use a local synthetic login first. Do not use Gmail or real account credentials.

## Acceptance target

Create a new bot through the real Deck UI. Approve its setup. Provision its private computer from a clean frozen base. Supply synthetic credentials through protected input. Complete supported phone-approval and CAPTCHA-style owner waits against a local fixture. Run the supplied first task. Restart Deck during an owner wait and after task dispatch. Preserve progress and avoid duplicate external effects. Check the flow in bare at phone and desktop widths.

No protected value may enter model context, ordinary chat, tool arguments, command journals or retained screenshots. A secret delivery receipt may contain identifiers and status, but no value. A wait cannot complete before delivery succeeds. Expired or stale requests must fail safely. Unsupported real challenges must remain blocked; a fixture pass does not prove every service's CAPTCHA support.

## Parallel work

Start eight isolated tasks from the current implementation commit. These assignments are planned until the orchestrator records their task identities in the local handoff. Each task gets one writer and a narrow scope.

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
