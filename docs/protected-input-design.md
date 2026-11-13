# Protected input design

Date: 2026-11-13.
Status: design. No protected input code exists. Setup still advertises `protected_input: false`.
Scope: D29, D30, D57, D60 and D62. This document fixes no monetary default and claims no universal financial boundary.

Every statement carries one label.

- **Verified (T3):** read from the installed T3 Code source or from this repository.
- **Frozen:** an interface that the owner's integration plan froze. See [setup-e2e-plan.md](setup-e2e-plan.md).
- **Proposed:** a design choice for implementers. It has no evidence yet.
- **Not verified:** a gap. Do not assume it is true.

## 1. Source inventory

D29 requires inspection of T3 Code before the port is chosen.

| Item | Result |
| --- | --- |
| Version inspected | T3 Code 0.1.10 nightly, build 20261113.4616. |
| Client | Three minified bundles: `secretRequest`, `SecretSourceInput` and `_chat.settings.secrets`. They hold the card state and the input control. |
| Server | One compiled executable. It embeds readable JavaScript with source-map style `// ../../packages/...` markers. The inspection read that text only. |
| Not read | Authentication databases, sessions, saved secrets and the secret store directory. |
| License | **Verified (T3):** the upstream repository's license is MIT, from the public GitHub license API. The installed build ships no license file for T3 Code itself. Its third-party notice list names dependencies only. |
| Source map | **Not verified:** no unminified source ships with the build. Names below come from compiled text. |

Decision: Deck reimplements the verified mechanisms in Rust and React. It copies no T3 code. MIT would permit reuse with a notice. The reimplementation needs no notice, because it shares behavior and no expression. If a later change copies any code, add the MIT notice first.

## 2. Verified T3 mechanisms

### 2.1 Request identity

- **Verified (T3):** The agent calls a `request_secret` tool. The server records a card with status `pending` and an identity derived from the thread and a caller key. The same key gives the same card. A retried call does not create a second card.
- **Verified (T3):** The card holds `label`, `reason`, optional `url`, optional `placeholder` and optional `delivery`. The owner's answer names only the thread, the card identity and the answer.
- **Verified (T3):** The server copies `label` and `reason` from the stored card when it records the answer. The answer cannot change them. This matches Deck's frozen rule that the label comes from the current pending request.
- **Verified (T3):** A stored value gets a reference. The reference is `secret-ref:` plus 32 hex characters. It is an HMAC-SHA256 tag over the thread and card identity. The key is a random 32-byte server salt kept in the secret store. A reference therefore binds one card in one thread. It is not a guessable identifier.

### 2.2 Transport

- **Verified (T3):** The owner's answer travels over the authenticated WebSocket RPC `secrets.answerRequest`. The route needs the orchestration-operate scope. No value is in a URL.
- **Verified (T3):** The answer is one of `save` (a trimmed non-empty string), `decline`, or `reference` (a password-manager reference that the server resolves).
- **Verified (T3):** The tracing span records the thread identity and the answer type. It does not record the value. The metric counters use status only.
- **Not verified:** Whether transport or proxy logs ever contain the RPC frame. Deck must test its own path.

### 2.3 Answer order and checks

The server checks these conditions, in this order:

1. The card exists and belongs to a live request. Otherwise: `not_found`.
2. The card status is `pending`. Otherwise: `already_answered`.
3. The asking run is not terminal. Otherwise: `agent_stopped`.

Then it runs this sequence:

1. Write the value to the secret store. It uses create-if-absent, so a repeat is harmless.
2. Dispatch a record command with a deterministic command identity. The status becomes `saved` or `declined`.
3. If the record command fails, delete the stored value and report `record_failed`.
4. Read the card again. If it is not `saved`, delete the value and report `agent_stopped`.

**Verified (T3):** value first, record second, and compensating deletion on failure. This ordering never marks a request saved without a stored value. The cost is a window where a value exists and the card is still `pending`. A crash in that window leaves a stored value with no record.

### 2.4 Waiting, expiry and cancellation

- **Verified (T3):** The agent's tool polls the card every 500 ms until the status leaves `pending`, the run ends or a timeout passes. A timeout or a run end records `cancelled`.
- **Verified (T3):** A `declined`, `cancelled` or timed-out result returns a fixed note. The note tells the agent not to ask in chat and not to ask again unless the secret is still needed. A new ask needs a new caller key.

### 2.5 Storage, lifetime and consumption

- **Verified (T3):** The store holds JSON with the project identity, the value and the save time. The secrets directory is mode 0700. Each file is mode 0600. Writes go to a temporary file, then rename.
- **Not verified:** Encryption at rest. The inspected code writes the value as plain text in a protected file. Do not describe this as encrypted.
- **Verified (T3):** The lifetime is 24 hours. Reads check the project identity and the age. An expired reference is deleted and fails with `ref_expired`. A sweep runs at start and every hour. It removes expired values and files.
- **Verified (T3):** A reference is one use. Consumption runs under a lock. It reads the value, then deletes the file. If the delete fails, the use fails and the value does not leave. A second use fails with `ref_unavailable`.
- **Verified (T3):** `delivery: "file"` writes the value to a private file that the agent's shell commands can read. The agent does not see the value. A pasted value path writes the same kind of file at once. The directory is 0700, the file is 0600, creation uses exclusive create plus rename, and the file lives 24 hours by modification time.

### 2.6 Client behavior

- **Verified (T3):** The input is a password-type control with autocomplete and spell check off.
- **Verified (T3):** The client trims the value. An empty value sends nothing.
- **Verified (T3):** The card shows one of four states: pending, saved, declined or ended. The saved label states that the value is kept out of chat.
- **Verified (T3):** The client maps server error tags to fixed messages. It never shows server text that could hold the value.
- **Not verified:** Whether the client clears the field after submit, and whether browser history or a password manager can retain it. The bundles are minified. Test this in Deck's UI with bare.

### 2.7 What T3 does not cover

T3 keeps one secret store on the host. Deck's value must end inside a private guest. T3 has no guest delivery, no bot binding, no screenshot or desktop-capture protection and no sign-in challenge handling. These parts are new Deck design.

## 3. Deck adaptation map

**Verified (repository):** current Deck state, read at `6d5ca42`.

| Area | State |
| --- | --- |
| Guest secrets | `bare-call` in the guest passes `~/.config/deck/secrets.env` to `open` and `ensure_session` as `secrets_file`. Typed text `${NAME}` becomes the value in bare. Replies show `[secret]` (D21). |
| Host secret names | `Tools::secret_names` reads names only, for the prompt. |
| Setup wait | `Step::Credential(label)` opens a `Wait` with `request_id`, `kind`, optional `expires_at` and `ttl_secs`. `complete_wait` marks a credential step done. Its comment says the caller must first confirm that the protected store accepted the value. |
| Setup API | `CompleteWait` on a credential wait returns 503. The ordinary route never accepts a value. |
| Wait kinds | `Credential { label }`, `PhoneApproval`, `Captcha`. |
| Desktop views | `QmpCapture` takes a raw framebuffer dump for the owner's live view. `vmproto.py capture` takes QEMU and `grim` dumps. Task screenshots go through bare. |
| Guest command path | `Qga.exec` takes stdin and sends it as base64 `input-data` in the guest agent call. The executor runs commands with stdin set to the null device and journals argv. |

Smallest adaptation, **Proposed**:

1. Keep T3's request identity rule. Deck already has it: `Wait.request_id`, in a snapshot with a version check.
2. Keep T3's checks as a fail-closed sequence. Section 4 gives it.
3. Replace T3's host store with delivery to the guest. The host keeps no value.
4. Keep T3's 24-hour sweep idea only for guest files. Section 6 gives lifetimes.
5. Add what T3 lacks: bot binding, receipt reconciliation and capture protection.

## 4. Host protected endpoint

### 4.1 Frozen contract

`POST /api/setups/{id}/secret` with JSON `{version, request_id, value}`.

- **Frozen:** The owner identity comes from the authenticated session. The existing trusted-proxy owner authentication applies.
- **Frozen:** The server takes the label from the current pending wait. The body has no label.
- **Frozen:** No value appears in the URL, the ordinary message API, a response or a log.
- **Frozen:** The response is 204 only after verified guest delivery and a durable wait transition.
- **Frozen:** A stale `version`, a stale `request_id` or a mismatched identity fails. A missing capability fails closed. It never fakes delivery.

### 4.2 Proposed request handling

1. Authenticate the owner. Reject any other caller.
2. Cap the body size before parsing. Reject an oversized body with 413.
3. Load the setup row. Compare `version`. A mismatch returns 409.
4. Require an open `Wait` of kind `Credential` whose `request_id` equals the body. A different identity returns 409. A request that expired follows `complete_wait`: it opens a replacement request and returns 409 with only the new identity.
5. Derive the guest label (section 5.2). Look up the runtime registered for this setup. A missing runtime, a missing capability or a bot mismatch returns 503 with a fixed message.
6. Deliver the value (section 5.1). Keep it in one buffer. Zero it after the call.
7. On success, reconcile the receipt (section 6). Save the transition with `save_setup_state`. A version conflict here means the receipt exists but the wait did not move. Retry through the receipt path. Do not deliver twice.
8. Return 204 with an empty body.

Failure rules, all **Proposed**:

- Every failure body is a fixed string and an error code. No failure echoes input, a path or the guest's raw error text.
- A failed delivery leaves the wait open with the same `request_id`. The owner may retry. A delivery failure costs no setup attempt, as D57 says for owner waits.
- An unknown delivery outcome (a timeout after the value left the host) is not a failure. The server calls the receipt check (section 6) before it answers. If the check cannot decide, it returns 502 and keeps the wait open. The owner enters the value again. The guest overwrites the same label.
- The ordinary `CompleteWait` route keeps refusing credential waits.
- A value shape check rejects NUL, CR and LF. It also rejects empty values and values over a fixed byte limit. Deck must not trim or alter the value. T3 trims. Deck must not, because a real password can start or end with a space. Reject; never truncate.
- The server never writes the body to a trace, an error, a panic message or a debug print. Use a wrapper type with a redacting `Debug` and no `Serialize`. Tests must assert this.

### 4.3 Owner-response endpoint for challenges

`POST /api/setups/{id}/challenge/respond` with `{version, request_id, decision}`, where `decision` is `completed` or `cancel`. **Frozen.**

- **Proposed:** It carries metadata only. A verification code is a secret. It goes through the endpoint in section 4.1 as a `Credential` wait. This applies to OTP and any code the owner reads from a device.
- **Proposed:** `completed` means the owner says they finished the action. It does not mean the service accepted it. The bot must verify service state before it marks the step done (D30). A verified failure reopens a new request.
- **Proposed:** `cancel` closes the request. The step stays blocked and the setup reports a clear blocked state.
- Version and identity checks match section 4.2.
- **Proposed DTO change:** `PhoneApproval` and `Captcha` stay unit variants. Add instruction and expiry as optional fields with defaults on `Wait`, so old snapshots still decode. Report any deviation to the parent.

## 5. Guest channel

### 5.1 Frozen command

```
vmproto.py --dir RUNTIME deliver-secret --bot BOT --request-id REQUEST
```

- **Frozen:** It reads JSON `{label, value}` on stdin. The value never goes in argv.
- **Frozen:** It returns metadata only: `{ok, bot_id, request_id, label}`.
- **Frozen:** `secret-receipt --bot BOT --request-id REQUEST` reconciles a delivered receipt without a new value.

Invariants, **Proposed**:

1. The command verifies that the executor identity in the runtime belongs to `BOT` before it sends anything. A mismatch fails with no write.
2. The host sends the value over the guest agent's stdin path. It never builds a shell string from the value. Check that the guest agent does not log `input-data`. **Not verified:** guest agent logging. The guest implementation owner must test it.
3. The host command journal (`executor/journal.rs`) must not record this call. It is a harness call, not an executor command. Tests must search the database and journal for the value.
4. The guest writer creates the directory `~/.config/deck` with mode 0700 and the file with mode 0600, owned by the bot's user. Writes use a temporary file in the same directory and an atomic rename. It sets the mode before it writes the value.
5. The writer takes a lock. Two deliveries cannot interleave.
6. The guest never prints the value, in stdout, stderr or logs.

### 5.2 Labels

- **Frozen:** On the guest, a label is an environment-style name. The host keeps a valid uppercase `NAME`. Otherwise it derives `DECK_` plus the first 16 uppercase hex characters of the SHA-256 of the display label.
- **Proposed:** A valid name matches `[A-Z][A-Z0-9_]{0,63}`. The hash uses the exact UTF-8 display label. The same label always maps to the same name. Two display labels that collide are a setup validation error.
- **Proposed:** The receipt holds the guest name and the display label. It holds no hash of the value and no fingerprint of it. A hash of a short code is easy to reverse. The derived name hashes the label, never the value.

### 5.3 Storage format

The existing guest reader expects `NAME=value` lines (D21).

- **Proposed:** The writer rejects NUL, CR and LF in the value. It rejects values over a fixed byte limit. It never truncates.
- **Proposed:** The writer replaces an existing line for the same name. It leaves other lines alone. A redelivery for a refreshed code replaces the old value.
- **Not verified:** How bare parses a line that holds `=` or `#` in the value, or leading and trailing spaces. The Guest owner must test these cases with bare. A value that bare cannot read must fail delivery, not be stored in a form that silently differs.
- **Not verified:** Whether bare passes a secret to a page without a leak in its DOM read, accessibility snapshot or console. See section 8.

### 5.4 Who can read the guest file

**Verified (repository):** D29 review item 2 says secure entry does not protect from a shell in the same VM. T3 has the same limit for `delivery: "file"`: the agent's commands can read the file. The guest file is readable by the bot's own user, and the shell tool runs as that user.

**Proposed claim:** Protected input keeps the value out of model context, chat, host storage and ordinary logs. It does not keep a value from a bot that runs a shell in its own computer. State this in the UI and in D-records. This does not add or remove any approved authority.

## 6. Lifetimes and crash semantics

| Item | Lifetime | Status |
| --- | --- | --- |
| Value in the browser | From typing to the response. Clear the field after submit, on success and on failure. | Proposed |
| Value in host memory | One request. Drop and zero it after the guest call. | Proposed |
| Value in host storage | Never. The host stores metadata and receipts only. | Frozen |
| Value in the guest file | Until the owner or the bot deletes it, or until the bot is deleted. | Proposed |
| Pending request | Optional `expires_at`. Default is none, so an unattended wait does not expire by itself. The owner may set one per request. | Verified (T3) for `expires_at`; the default is Proposed |
| Receipt | Kept with the setup snapshot. Metadata only. | Proposed |

Rationale for the guest lifetime: T3 expires a stored value after 24 hours because it is a transit copy. Deck's guest file is a working credential that unattended work needs after the owner leaves (D59). An expiry would break long runs. A saved VM state also contains the value in memory, which D29's open item names. Do not claim that deleting the file clears saved state.

Crash windows, **Proposed**:

| Crash point | State left | Recovery |
| --- | --- | --- |
| Before the guest call | Wait open. No file. | The owner enters the value again. |
| During the guest write | A temporary file may remain, with the same mode. The real file is intact. | The writer deletes stale temporary files at its next run. No receipt exists. The wait stays open. |
| After the guest write, before the receipt returns | The file holds the value. The host does not know. | The next owner action calls `secret-receipt`. It finds the guest receipt for this `request_id` and moves the wait. The owner does not enter the value again. |
| After the receipt, before the snapshot save | Same as above. | Same recovery. |
| After the snapshot save | Done. | None. |

Rules:

- A guest receipt names the bot and the `request_id`. It carries the label, the guest name and a timestamp. It carries no value.
- **A receipt alone cannot clear a different or stale request.** The host clears a wait only when the open wait has the same `request_id`, the same bot and the same label as the receipt. A receipt for an older `request_id` is ignored. After a re-issued request, the host demands a fresh delivery for the new identity.
- Receipts are per request identity. A refresh with the same label (a new OTP) writes a new receipt with a new `request_id`.
- T3's order (store, then record, then compensate) maps to: deliver, then record, then reconcile. The difference is that T3 deletes the value on a record failure. Deck cannot delete a guest value on every host failure, because the value may serve the retried request. Deck keeps the file and reconciles through the receipt.
- A paused, held or deleted bot refuses delivery with 503. It does not wake a held computer to take a value.

## 7. Active-task secret requests

Setup waits are one case. A task in a ready bot may also need a code, for example when a service asks for a new OTP mid-task.

- **Verified (T3):** The agent's tool blocks on the card, gives a fixed note on a decline, and returns a reference, not a value. The model never sees the value.
- **Verified (repository):** Deck tasks have a `waiting` state with a `question` field. The setup route above is under `/api/setups/{id}`.
- **Proposed:** A model-facing `request_secret` tool with `label` and `reason` only. It returns `{status, name}` and never a value. The model then types `${NAME}` in bare. The text of the model's own reason must not contain a value, and Deck must not ask the model to write one.
- **Proposed:** The task keeps exclusive access to its browser session while it waits (D31). The owner sees the request in the bot's chat and in the inbox.
- **Not verified:** Which endpoint carries an active task's answer. The frozen route is `/api/setups/{id}/secret`. The host lifecycle owner must name the route for tasks, and report any deviation. Do not reuse the setup route for a task silently.
- **Proposed:** An expired code produces a new request with a new `request_id`. A late answer to the old identity fails with 409 and leaves the new request open. The tool result then says the request expired. The model asks again only if the task still needs the code.
- **Proposed:** A decline returns the same fixed note as T3. The model must not ask in chat.

## 8. Redaction and capture protection

### 8.1 What can hold a value

| Place | Rule |
| --- | --- |
| URL, query, ordinary message API, response body | Never. Frozen. |
| Host logs, traces, panic output | Never. Use the redacting wrapper. Proposed. |
| Command argv, host command journal, guest process list | Never. The value travels on stdin. Frozen for argv. |
| Setup snapshot, SQLite, chat history | Metadata only. |
| Model messages, tool arguments, tool results | Never. The model has the name only. |
| Guest file | Allowed, 0600. See section 5. |
| Browser field | Present while bare types it. See below. |

### 8.2 Screenshots and desktop capture

D29 says screenshots and page reads during sign-in must not expose protected values. Two capture paths exist: bare screenshots (model-facing) and the owner's live desktop view or `capture` command (raw framebuffer, pixels only).

**Claims that need bare evidence.** Do not claim raster secrecy from searching PNG bytes. A search proves nothing about pixels. Proposed evidence:

- Type a generated synthetic value through `${NAME}` on a local fixture page with a plain text field and a password field.
- **Proposed:** Read the page through bare's snapshot, read and eval tools. Check whether the value appears in DOM text, an input's `value` and the console. Report each result as a measured fact.
- **Proposed:** Take a bare screenshot while a typed value shows in a visible field. Inspect the image visually or with a pixel check of the field region. A password field masks it. A plain field such as an email address or an OTP may show the characters. These are different cases.
- **Not verified:** Whether bare's `[secret]` replacement covers screenshots, DOM reads and eval results. D21 says replies show `[secret]`. It does not say anything about pixels or about eval.

**Email addresses and other non-password values.** A sign-in email address in a secret line is displayed in the field. Bare's masking of replies does not hide pixels. D53's leak check skips non-secret names because a correct answer can show them. For protected input, treat an email as a value that can appear in captures. Choose one:

- **Proposed default:** Keep it in the guest file and in the page, and accept that it may appear in a capture of the field. Make sure it never enters host storage, chat or the model's own text. State this limit in the UI. It is lower sensitivity, but not zero.
- **Proposed stricter option:** Treat every protected value as capture-sensitive and apply the rules below to it too.

**Capture rules, Proposed.**

- Hold a per-bot capture-sensitive flag from the first typing of a protected value until the sign-in step ends. A successful receipt does not end it. The typing step does.
- While the flag is set: the model-facing screenshot tool returns a fixed refusal, and the live viewer and `capture` command return a placeholder frame, not a framebuffer. The flag also covers failed deliveries and expired requests.
- If bare cannot report when it types, a coarse flag holds while a credential step is active. This keeps the viewer blank longer, and hides more.
- Any retained screenshot or viewer frame taken before the flag must not contain a typed value. Tests use a fixture and a pixel check of the field region, not byte search.
- **Not verified:** Whether the guest compositor or the VM's saved state holds earlier frames. The VM owner must check before the claim is made.

**If bare leaks or fails.** Follow [feedback-loop.md](feedback-loop.md). File an issue on the `bare` repository with the tool call, the result and the expected result. Use no private page content and no value. Do not add an engine workaround in Deck, and do not add another browser.

### 8.3 Failed delivery and expired codes

- A failed delivery returns a fixed error. It does not say which check failed in a way that helps guess a value.
- A one-time code can expire while it travels. Deliver it, let the bot type it, and let the service decide. A rejected code opens a new request with a new identity. A late receipt for the old identity does nothing.
- An expired OTP that sits in the guest file is stale. The next delivery for that name replaces it. The bot must not reuse a stale file. A step-level delete of one-time names after the sign-in ends is **Proposed**, and stays optional. It does not protect against a shell that already read the file.
- A value that reached the page and failed leaves no host trace. The bot's page may still hold it. Reload the page before the next attempt.

## 9. Acceptance cases

Use generated synthetic values, a local fixture, a scripted model and throwaway data. No Gmail, no real account, no paid model. The parent integrates and runs the final gate on one exact head. Mark each case as unit, fixture or live VM.

1. Happy path: a credential wait takes a value. The response is 204. The wait is done. The guest file holds `NAME=value` at mode 0600 in a 0700 directory. The receipt has no value.
2. Stale `version`: 409. No guest write.
3. Stale or unknown `request_id`: 409. No guest write. The open wait is unchanged.
4. A different bot's runtime: refused before any write.
5. A missing capability (`protected_input` false or no guest channel): fails closed with 503. The wait stays open. Nothing reports delivery.
6. A body with NUL, CR, LF, empty or oversized value: 400 or 413. No write. No truncation.
7. A value with leading or trailing spaces: stored unchanged.
8. Guest write fails: the wait stays open, with the same `request_id`. A retry works.
9. Crash after the guest write and before the snapshot save: restart, call the reconcile path. The wait completes with no second value.
10. A receipt for an older `request_id` does not clear a newer wait.
11. A reissued request after expiry: the old identity returns 409 and the new one works.
12. A duplicate submit of the same `request_id`: one effect. The second call fails or repeats with no second delivery.
13. Leak search over the database, journal, logs, chat history, traces, argv and model messages for the synthetic value finds nothing. Use the harness's own search. Never print the value.
14. Bare evidence: DOM read, snapshot, console and eval results for a typed plain field and a password field, recorded as measured facts.
15. Screenshot and desktop capture while the flag is set: model-facing refusal and viewer placeholder. A capture taken just before typing and one just after sign-in work.
16. A non-password value (a synthetic email) typed to a plain field: record what bare shows in the DOM and in pixels. Confirm host storage holds no value.
17. A phone-approval or CAPTCHA fixture wait: `completed` does not mark the step done until the bot verifies the fixture's state. `cancel` leaves the setup blocked.
18. An OTP fixture: a first code expires, a second is requested with a new identity, the first receipt does nothing, the second signs in.
19. Active task waiting for a code: the task keeps its session, other work continues without touching it, and a pause or revocation holds it.
20. Restart during an owner wait and after a task dispatch keeps the request identity and the wait.
21. Old snapshots without the new optional fields still load.

## 10. Open items

- Which endpoint carries an active task's secret answer.
- Guest agent logging of `input-data`, and bare's parsing of special characters in values.
- Whether bare masks pixels, DOM reads and eval results. File a `bare` issue for any leak.
- Saved VM state and the compositor can keep a value or an earlier frame.
- Encryption at rest. The T3 reference stores plain text in a 0600 file. A guest disk key is a separate decision.
- A lifetime for guest files beyond the bot's own life, and the owner's delete control.
- Default value size limit and default request expiry. Choose them in code review, with a reason.

This document adds no authority and removes none. D60's full job authority, the owner's absolute ceiling and pause, revocation and unknown-outcome rules stay as written.
