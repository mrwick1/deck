# First desktop VM prototype

Date: 2026-11-12.

## Goal

Prove that Deck can operate one persistent full desktop computer through a guest executor. Prove that saved-state sleep releases resources and resumes the computer. Measure the result before selecting the final VM backend.

The owner approved this checklist and its initial experiment targets on 2026-11-12. This document does not report passing results or authorize a deployment.

## Scope

Use one configuration-defined bot and one VM. Keep the agent loop outside the guest. Put the desktop, bare, shell, files and executor inside the guest. Use only bare as the browser.

Use synthetic data and a controlled fixture service. Do not connect personal accounts. Do not test with real passwords or external submissions. Inject the model endpoint and key through configuration if model calls are used.

Start with QEMU/KVM as a candidate. Select the backend only after measuring the prototype. Do not build multiple backend implementations.

## Required demonstration

1. Start the VM from its persistent disk. Connect Deck to the correct guest executor.
2. Run a shell command and create a file inside the guest.
3. Use bare to open a fixture page, read it and interact with it.
4. Transfer a screenshot and a downloaded file into Deck. Verify file contents and display the screenshot in chat.
5. Show the actual desktop and identify its relation to bare's output. Show browser output when browsing starts. Do not label a browser capture as a full desktop capture.
6. Show live activity and task status in Deck.
7. Deliver steering at the next model-call boundary. Show that it affects the next step. Do not claim that it changes a command already executing.
8. Stop a deliberately long command. Distinguish the cancel request from confirmed termination.
9. Save at a quiescent boundary. Stop the VM process and release its resident memory. Keep its disk and saved state.
10. Restore the saved desktop. Verify an open application, an unsaved text marker, a browser tab, a fixture login and a stored file. Retain the same fixture service during this test.
11. Kill the VM without a saved-state shutdown. Cold boot its current disk. Report lost volatile state honestly and reconcile the task from host records.

Human takeover, real account setup, MFA, event integrations, conversational provisioning and memory retrieval are outside this prototype. Desktop input must still be demonstrated through a controlled command so future takeover has a viable path.

## Correctness gates

| Gate | Pass condition |
| --- | --- |
| Boundary | Guest commands cannot read a host-only marker. Mounts and channels expose only intended prototype resources. |
| Guest identity | Deck binds each command and artifact to its configured bot and VM. Wrong identity is rejected. |
| Artifacts | Screenshot and download bytes match guest checksums. Host storage does not depend on guest paths existing on the host. |
| Save consistency | Save occurs after command dispatch stops and outstanding commands settle. The saved memory and disk remain paired. |
| Resume | All markers in demonstration step 10 survive five consecutive save and restore cycles. |
| Restore epoch | The host advances its epoch on restore and rejects deliberately injected stale replies. |
| Stop | A cooperative test command terminates within the cancellation target. Missing acknowledgement produces an unresolved state, not a false success. |
| Host recovery | Restarting Deck reconciles in-flight tasks. It does not silently leave them running or re-dispatch them. |
| External action uncertainty | A fixture records a submission, then drops its reply. Recovery inspects fixture evidence before retrying. Exactly one submission exists. |
| Saved-state recovery | Resuming after the fixture submission does not silently dispatch it again. Known results remain in the host journal. |
| Late replies | A late result is recorded as evidence. It does not revive a cancelled task or trigger another action. |
| Policy | No alternative browser, Xvfb or VNC is introduced. If the display path requires a prohibited component, stop that approach and report the conflict. |

The boundary test is a smoke check. It does not prove VM security. The submission test proves the controlled case, not exactly-once behavior across arbitrary services.

## Provisional performance targets

These are initial experiment targets. They are not measured capabilities or final product limits. Report a miss rather than weakening a correctness gate.

| Metric | Initial target | Measurement |
| --- | --- | --- |
| Guest configuration | 2 vCPUs and 2 GiB RAM | Record launch configuration. |
| Host resident memory while idle | At most 3 GiB for the VM and dedicated helpers | Measure after 60 seconds idle. Exclude the shared Deck service and report it separately. |
| Cold boot to executor ready | At most 45 seconds | Measure launch to authenticated ready response. |
| Saved-state sleep | At most 15 seconds | Measure dispatch quiescence to saved-state completion. Report time waiting for active work separately. |
| Restore to usable executor | At most 15 seconds | Measure restore request to a successful guest command. |
| Desktop capture | At least one update per second while requested; capture-to-display latency at most 2 seconds | Use timestamps or a visible changing marker. No idle capture requirement. |
| Activity visibility | At most 1 second after Deck receives an event | Measure event receipt to UI update. |
| Cancellation | Termination acknowledgement within 3 seconds for the cooperative fixture command | Measure the stop request to confirmed command exit. |
| Persistent disk allocation | At most 10 GiB actual host allocation after setup and the scripted workflow | Report virtual capacity and allocated bytes separately. |
| Saved-state storage | At most 3 GiB per saved VM | Measure saved-state files separately from the persistent disk. |
| Sleeping resource use | No running VM process or dedicated guest helper; zero VM resident RAM | Check process ownership after save completes. Host metadata and shared Deck remain. |

Run five cycles on the same recorded host and image. Report every sample, the median and the maximum. Record tool versions, image version, relevant host capacity and background load in a local results file. Remove machine identifiers and private values before adding results to this public repository.

## Minimal implementation boundaries

Keep the host record for tasks, action intents, outcomes and restore epochs outside saved guest state. Use command identifiers and bounded deadlines. Support status lookup, cancellation acknowledgement and artifact retrieval. Fence stale connections after restore.

Use the narrowest guest protocol that can demonstrate these requirements. Do not build a general RPC framework or interchangeable VM backends for this prototype.

Use fakes for deterministic protocol and recovery tests. Use a real VM for desktop, isolation smoke checks, save/restore and performance measurements. Passing fake tests alone does not pass the prototype.

## Result and next step

Record each gate as pass, fail or not tested. Attach reproducible commands and evidence. Do not put credentials, machine identifiers or private network details in committed evidence.

A correctness failure blocks the relevant runtime approach. A performance miss requires explicit review of the target or implementation before backend selection. Do not hide a miss in an average.

After this prototype, demonstrate two independent bot disks and artifact stores. Then implement the persistent workstation slice: queues, pause and budget holds, minimal inbox, and conversational creation. This prototype alone does not establish multi-bot isolation or product readiness.
