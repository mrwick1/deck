# Open questions

The first ten questions were answered on 2026-11-11. The answers are in [decisions.md](decisions.md). Later decisions changed some of them. The status column shows which answers still hold.

| Question | Decision | Status |
| --- | --- | --- |
| Language and stack | D6 (Rust service), D20 (React web app) | D6 holds. D20 replaces D7. |
| Where it runs | D8 (owner's main machine) | Holds for the Deck service. The VM host is open (see below). |
| Access | D9 (Caddy and Authelia), D10 (Authelia is the only login) | Holds. |
| Model | D11 (Sonnet and Haiku) | Holds. |
| Shell and files | D13 (bwrap shell from v1) | Superseded in location by D24. The shell now runs inside each bot's VM. Its sandbox policy is open. |
| Database | D12 (SQLite) | Holds. |
| Approvals | D14 (model flag plus domain rules) | Phone push is deferred by D32. Enforcement is trust-based under D33. |
| Feedback loop | D15 (scheduled fixer, pinned bare) | Holds. |
| First milestone | D16 (M1 in parallel with the bare gaps) | M1 is done (see the README). |
| License | D17 (MIT or Apache 2.0) | Holds. |

## Current implementation checkpoint

Updated 2026-11-13. Stages 1–3 are merged. The optional one-bot path implements the executor protocol, durable command records, host epochs, cancellation acknowledgement, queue/steer/stop, generic event intake, idle save/wake and subscribed desktop capture. PR #23 adds measurements and the D53 save cap and is pending merge. Keep these implemented choices separate from the remaining product choices below.

## Open choices

These choices remain open. Each one is inside an approved decision. None reopens a decision.

### Guest control

- **Executor transport hardening:** the versioned protocol, private transport, identity checks, host epochs and reconnect handling exist. Identity provisioning and other host placements remain open (D37).
- **Guest network access:** which networks the VM can reach, and the rules for them (review item 6).
- **Shell policy inside the VM:** the sandbox rules for shell commands (D24).

### VM backend and display

- **VM backend expansion:** QEMU/KVM is used by the one-bot implementation. Other backends remain deferred; compare them only for a concrete lifecycle or host requirement (D37).
- **VM host:** where the VM backend runs. D8 does not cover it (D37).
- **Display expansion:** the prototype uses QMP desktop frames and separate bare page captures. Future takeover and alternate transports remain open; the bare-only policy still applies (D23, D38).
- **Display polish:** demand subscriptions and disconnect cleanup exist. Phone behavior, accessibility and future takeover need broader validation (D38).

### Credentials and sign-in

- **Credential storage:** where protected values live, and how the VM protects session cookies and disk data (D29, review item 2).
- **Secret lifetime:** how long a protected value stays valid, and how a failed sign-in recovers (D29).
- **Verification challenges:** MFA, passkeys and other challenges that the bot cannot complete. Each service needs its own flow (D30).
- **Listener boundary:** what credentials an event listener may hold while its VM sleeps (review item 2).

### Queue and control

- **Queue defaults:** the default message priority, the priority controls and task dependencies (D31).
- **Interruption:** how a running task yields to a higher-priority message, if it does (D31).
- **Stop and steer refinement:** acknowledgements, uncertainty handling, model-boundary steering and queue controls exist. Broader service and UI validation remains (D39).

### Recovery

- **Retry limits:** the number of retries for each safe action (D34).
- **Outcome evidence:** what evidence resolves an uncertain external action (D34).
- **Action evidence policy:** command journal fields and outcomes exist. Service-specific evidence and automatic investigation remain open (D34).

### Budgets and resources

- **Budget accounting:** budget periods, usage accounting and handling of requests in flight (D36).
- **Continuation scope:** what one budget approval allows (D36).
- **Resource defaults:** default limits for CPU, RAM and disk, and idle sleep timing (D36, D27).
- **Product resource targets:** final resource and latency targets. The values in `vm-prototype.md` are approved experiment targets (D44, D45).

### Events and wake-ups

- **Event sources:** which sources each service provides, and how each one is monitored (D26).
- **Listener design:** how a listener outside the VM queues events and requests wake-ups. Validate the wake policy (D27).

### Memory

- **Memory storage and retrieval:** how memory is stored, searched and given to the model (D35).
- **Provenance and correction:** how the source of a fact is kept, and how a correction replaces it (D35).
- **Reusable procedures:** whether procedures need their own format (D35).

### Interface

- **Overview layout:** the layout details and status controls of the home screen (D40, D42, D43).

## Carryover work

These implemented lifecycle items have real callers: automatic idle sleep, generic event batching and wake coalescing, live-view subscriptions and unchanged-frame suppression. See [optimization-plan.md](optimization-plan.md) for limits.

Remaining work:

- Memory: the third full repetition and a host-memory plateau are unproven.
- Idle attribution: no causal timer/device setting was established.
- Save cap: concurrent saves, slow storage, disk-cold behavior and peak CPU remain untested.
- Codec: keep `none`; no default compression change is selected.
- Broader product validation: fresh provisioning, failure halfway through a real save, protected credentials, real service events and automatic spending accounting.
