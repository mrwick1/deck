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

Status: planned. Stage 1 baseline exists. No optimization gain is established yet.
