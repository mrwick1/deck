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

## Open choices

These choices remain open. Each one is inside an approved decision. None reopens a decision.

### Guest control

- **Executor protocol and transport:** message format, connection authentication and reconnect handling (D37, Stage 2 plan).
- **Guest network access:** which networks the VM can reach, and the rules for them (review item 6).
- **Shell policy inside the VM:** the sandbox rules for shell commands (D24).

### VM backend and display

- **VM backend:** QEMU/KVM is the first candidate. Select the backend after measurement. Compare libvirt only if lifecycle problems justify it (D37, review recommendations).
- **VM host:** where the VM backend runs. D8 does not cover it (D37).
- **Display path:** how bare's output reaches the desktop view, and which display protocol the view uses. Do not add Xvfb or VNC without a policy decision (D23, D38, review item 4).
- **Display subscriptions:** when the screen view closes, how it appears on a phone, and when captures start and stop (D38, optimization carryover).

### Credentials and sign-in

- **Credential storage:** where protected values live, and how the VM protects session cookies and disk data (D29, review item 2).
- **Secret lifetime:** how long a protected value stays valid, and how a failed sign-in recovers (D29).
- **Verification challenges:** MFA, passkeys and other challenges that the bot cannot complete. Each service needs its own flow (D30).
- **Listener boundary:** what credentials an event listener may hold while its VM sleeps (review item 2).

### Queue and control

- **Queue defaults:** the default message priority, the priority controls and task dependencies (D31).
- **Interruption:** how a running task yields to a higher-priority message, if it does (D31).
- **Stop and steer:** stop acknowledgements, steering delivery points and the exact activity and message controls (D39).

### Recovery

- **Retry limits:** the number of retries for each safe action (D34).
- **Outcome evidence:** what evidence resolves an uncertain external action (D34).
- **Action record format:** the fields of an action record and its outcome (D34, review item 3).

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

These items are not open questions. Each stays unchecked until its real caller exists. See the carryover checklist in `optimization-plan.md` on the implementation branch.

- Automatic idle sleep.
- Event batching and wake coalescing.
- Live-view capture subscriptions.
- Unchanged-frame suppression in Deck.
- The saved-state codec default. It stays `none` until a controlled measurement justifies a change.
- A controlled performance follow-up, with one owned workload at a time.
