# VM optimization implementation plan

Date: 2026-11-12.

The owner approved these five optimization areas. Implement them in dependency order. Measure each change against the verified Stage 1 baseline. Do not claim a gain from a unit test alone.

## 1. Idle sleep and event batching

Stage: executor and persistent task queue, followed by Deck integration.

- Track bot activity on the host. Save only after commands settle and no task needs the computer.
- Make idle duration and event batching windows configurable. Keep a maximum batching delay so steady traffic cannot postpone work forever.
- Group compatible events for the same bot and subscription. Preserve event identifiers and pending work across restarts.
- Reuse an awake computer for pending work. Wake a sleeping computer once for a batch.
- Respect automation pause, budget holds and user-selected message priority. Never hide unresolved external actions by retrying them.

Checks: use a fake clock for idle and burst tests. Test continuous events, duplicate events, restart, direct messages and both holds. On a real VM, verify one wake per eligible batch and no wake while held. Compare wake counts and total awake time for the same fixture event sequence.

## 2. Shared clean base image and private overlays

Stage: VM harness first, then provisioning.

- Provision an account-free Arch desktop base. Freeze and identify it before making private writable bot overlays.
- Keep a base while any overlay depends on it. Never modify or delete a backing image through normal bot cleanup.
- Keep each bot's files, sessions and saved-state pairing private. Pin the base identity in runtime metadata.
- Refuse base replacement and unsafe restore combinations. Preserve a recovery path for existing standalone prototype disks.

Checks: boot two overlays in sequence. Write different markers and verify separation. Check the base digest before and after both runs. Repeat saved-state cycles. Report total allocated storage including the shared base, both overlays and saved state.

## 3. Demand-driven screen capture

Stage: harness measurements first; view subscription control during Deck integration.

- Keep capture off without a viewer or an agent request. A closed view must not keep a capture worker alive.
- Bound capture frequency. Allow one capture operation at a time and coalesce slow requests.
- Reduce delivery when frames do not change. Distinguish reduced delivery from reduced capture cost.
- Stop capture for saved computers and remove subscriptions after disconnect.

Checks: no captures while unsubscribed; only one producer for multiple viewers; slow capture does not grow a queue; unsubscribe stops work. Measure host CPU, captured frames, transmitted frames and bytes separately. The current one-shot capture already avoids a permanent idle loop; do not present that behavior as a new gain.

## 4. Trim measured guest background work

Stage: VM harness.

- Inventory guest services, processes and CPU/memory use.
- Disable only work not needed by the guest. Preserve desktop login, networking, time recovery and control channels.
- Apply changes to the image provisioning script so a fresh build reproduces them.

Checks: report the exact service changes and before/after measurements under the same workflow. Repeat boot, bare interactions, artifact checks and saved-state resume. Keep the baseline if no justified service change improves it.

## 5. Benchmark saved-state compression

Stage: VM harness.

- Check which compression options the installed QEMU supports for file save and restore.
- Keep an uncompressed reference. Compare supported compression without modifying paired disks or replaying external actions.
- Record save/restore wall time, CPU time, peak resident memory and saved bytes. Keep format and compression metadata with the saved state.
- Enable a default only if measurements justify it. A smaller file alone is not sufficient if latency or CPU cost is unacceptable.

Checks: at least five paired cycles per candidate. Verify desktop, browser and file markers after every restore. Report unsupported options and failed gates. Do not add a dependency or swap a backend merely to force compression.

## Delivery

Implement overlay, service and compression work on the existing harness first. Keep capture subscription and idle/batching integration tied to their real executor and queue callers. Do not build unused production schedulers in the prototype.

The parent reviews each implementation diff and reproduces critical checks. Preserve raw local measurements outside Git. Commit only sanitized evidence. Keep all VM correctness gates and the bare-only policy intact.

Status: the harness pass is implemented and reviewed. Shared overlays, service trimming and optional codecs have recorded experiments. Capture behavior is audited and benchmarked. The parent ran fast checks and inspected raw results but did not repeat the live optimization matrix. Idle sleep, generic event batching and UI capture subscriptions are now integrated in Stage 3. Real service event sources remain deferred. See the implementation branch report `docs/vm-optimization-results.md`.

## Carryover checklist

The owner asked to keep these items explicit so work can resume later. Do not mark them complete from harness measurements alone.

- [x] Automatic idle sleep. Implemented and verified in the one-bot Stage 3 flow. Depends on executor status, cancellation and durable task ownership in Stage 2. Integrate the idle policy after the queue exists. Verify no save while work or an unresolved action owns the computer.
- [x] Event batching and wake coalescing. Implemented for generic authenticated events; real service sources remain deferred. Depends on durable event ingestion and the task queue. Test duplicate events, bounded delay, restart, priority, automation pause and budget holds before service integrations.
- [x] Live-view capture subscriptions. Integrated; direct viewer disconnect stopped capture in the real-VM smoke. Depends on Stage 3 UI and executor integration. Test zero capture while unsubscribed, one producer per bot, bounded work under slow viewers and disconnect cleanup.
- [x] Unchanged-frame suppression in Deck. Integrated and observed on the real display path. The harness measured transmission savings only. Wire it into the actual display path and measure capture CPU and transmitted bytes separately.
- [ ] Revisit the default saved-state codec. Optional codecs exist. Keep `none` until a controlled measurement and deployment prerequisites justify a change. Do not require another benchmark matrix before executor work.
- [ ] Controlled performance follow-up. Existing optimization runs had overlapping sessions. Before asserting a product speedup or capacity, measure one owned workload at a time. Preserve separate datasets and report CPU, host memory, guest memory and total storage.

Implemented harness work: shared base/private overlays, service trimming and optional state codecs. Remaining robustness checks include safe base updates with dependent overlays, concurrent bot loads and missing-codec recovery on a real VM. Keep these out of Stage 2 unless its implementation needs them.

## Priority correction: one lean bot first

The owner clarified that the next resource milestone is one optimized bot. Multi-bot capacity testing is deferred.

Measure the actual integrated bot in three states:

| State | Required behavior |
| --- | --- |
| Working | Bound commands, output buffers, artifacts and workers. Capture only when needed. Measure peak memory and CPU for a fixed useful task. |
| Awake but idle | No repeated model calls, status polling or screen capture without a caller. Use a configurable idle interval before saving. Measure residual host and guest work. |
| Saved | No VM or dedicated capture process. Keep only necessary host records and wake subscriptions. Measure remaining per-bot host overhead separately from shared Deck. |

The one-bot lifecycle is now integrated through the server, executor and durable task ownership. The next priority is controlled profiling of that path. Automatic idle sleep must respect running and unknown work, automation pause and budget holds. Event batching follows durable event ingestion. Keep the full desktop requirement.

The current measurements are baselines. They do not establish an optimized integrated bot. Keep multi-bot correctness tests, but postpone concurrent load tests. Do not run another broad benchmark matrix before the lifecycle integration exists.


## Stage 3 completion scope

One configured bot now uses the guest tools through the server. The persistent queue and hold gates feed automatic idle save and wake. Generic event batching and capture subscriptions have real callers. Parent verification covered a browser/shell/file task, idle save, restore with chat continuity, zero idle and saved captures/model calls, and no capture worker after direct viewer disconnect. UI controls were operated in bare.

The parent fixed active-command inbox labeling, unresolved-work queue gating, pause gating on waiting-task resume and conservative browser-relay cancellation. Full test and build checks pass. These results close the four integration checkboxes above for the synthetic single-bot scope.

Automatic spending accounting, real accounts and event sources, memory integrations, fresh provisioning and real halfway-save failure remain open. Optional codec selection stays unchanged. Final observations are single-run data, not a new performance matrix or a concurrent-capacity claim.

## Post-integration performance pass

The owner approved this pass after Stage 3. Update [progress.md](progress.md) with each measured result. Use the strict Stage 3 resource windows as an initial observation, not proof of a speedup.

Profile residual awake-idle guest CPU first. Then inspect active command polling, artifact transfer, viewer capture cost and the web bundle. Change one cause at a time. Record the baseline revision, workload, run count, variation and correctness checks. Keep saved-state codecs unchanged until measurements justify a default change.

This pass optimizes one useful bot before concurrent load tests. It does not replace future work on task ownership and resource control across machines.
