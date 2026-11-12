# Stage 3: one integrated lean bot

Date: 2026-11-12.

## Goal

Connect the verified VM and executor to Deck's server, agent tools and UI. Optimize one bot's working, awake-idle and saved states during integration. Do not start a multi-bot capacity exercise.

## Delivery order

1. Put the VM lifecycle and long-lived executor connection under the server. Reconcile durable command state before accepting work after reconnect or restore.
2. Add persistent task ownership and message delivery modes: steer current work or queue later work with selected priority. One task owns the desktop. Preserve unknown outcomes and expose them in the inbox.
3. Route shell, browser and artifact operations through the guest. Preserve bare-only browsing, protected values and file boundaries. Commands need stronger containment than the Stage 2 process-group prototype before reporting task-wide termination.
4. Connect the accepted overview and chat-first UI to real state. Show activity and stop, steer, queue, pause and sleep controls. Show the screen only when needed.
5. Add automatic idle save, durable generic event batching and wake coalescing. Enforce pause and budget holds. Real account event integrations remain deferred.
6. Measure one useful scripted workload and the three lifecycle states. Capture subscriptions must stop on disconnect. Sleep must leave no VM, command or capture worker behind.

## Acceptance

Use deterministic tests before a serial real-VM smoke. Prove task persistence, message priority and steering, cancellation, artifact integrity, unknown-outcome handling, saved-state wake and hold behavior. Use a scripted model and synthetic fixture data. Do not call a paid model or connect accounts for verification.

Check one integrated flow from the actual UI and API, with bare as the test browser. If bare cannot render or operate the UI, follow the feedback loop. Do not add another browser.

For resource checks, report host and guest CPU and memory separately. Count captures, polls, model calls and workers while idle. Report the idle interval and wake latency. A lightweight host connection is different from a running VM. Record remaining host bot overhead while saved.

Do not claim complete spending enforcement from a hold flag alone. The coordinator must respect persisted budget holds and explicit continuation approval. Pricing and token accounting remain separate work unless implemented and verified in this stage.

## Scope limits

Start with one configured bot and private disk. Conversational provisioning, real accounts and MFA, service-specific subscriptions, automatic memory retrieval and visual redesign remain separate product work. Keep the accepted overview direction. Do not make multi-bot load tests or more compression tuning completion criteria.

## Ownership and handback

One implementation agent owns the existing implementation worktree. The parent edits only this design worktree until the agent stops and all owned test processes end. Never start a replacement writer while a returned agent still has background work.

The parent reviews the full diff and reproduces key checks. Update `optimization-plan.md` carryover only when the actual caller and test exist. Record incomplete behavior explicitly. No deployment or production merge is authorized by the stage plan.

Status: the one-configured-bot integration is reviewed and verified with a scripted model, synthetic data and one real VM. Parent review included bare UI checks and a strict idle/capture smoke run. Automatic spending accounting, fresh provisioning and real service/account integrations remain outside the verified result. See `docs/stage3-results.md` on the implementation branch.
