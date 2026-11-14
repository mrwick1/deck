# Multi-account Gmail morning briefing

## Goal

Deliver one morning briefing from one bot across multiple Gmail accounts. Identify threads that need a reply, urgent items, other actions and low-priority information. Group results by priority and show an account label and source-thread link for each item. Explain why a reply or action is needed. Distinguish explicit deadlines from inferred urgency.

Report each account as complete, partial or blocked. State the covered time window and scan limits. Keep unresolved reply items visible on later mornings. Do not claim that an unread-message scan covers all mail that needs a reply. Check later messages in each thread before recommending a reply.

## Verified starting point

- The local browser path supports named saved sessions in `src/tools.rs`. Its tests cover state paths and invalid names.
- The guest path returns through the relay before that local state handling. Managed provisioning clears the host state directory in `src/bot/provisioning.rs`. Local session tests therefore do not prove guest account isolation.
- The guest browser adapter loads a configured secrets file. A shared secrets file does not establish account-specific routing.
- Synthetic single-account setup and saved-state continuation have recorded evidence. They do not prove multi-account Gmail behavior.
- The feedback-loop document describes issue reporting but leaves the fixing-agent trigger open. Automatic issue handoff is not established by that document.
- PR #25 proposes explicit guest internet access. Resolve its integration before real-site acceptance.

## Delivery order and checks

### 1. Prove account isolation in one bot

Inspect the guest browser session model before selecting storage changes. Reuse existing session support where it provides isolation. Bind each account to a stable internal identifier, a display label and a private session reference. Verify the signed-in identity before reading or acting. Never infer identity from tab order or a Gmail account index alone. Keep credentials and account details outside Git.

Check two fixture accounts in the same bot computer. Give them distinct messages and overlapping thread identifiers. Switch between them repeatedly. Restart the service, save and wake the computer, and restart the browser. Verify that each account retains its own session and results. Expire one login and prove that the other account still completes. Refuse mismatched identity. Write a failing regression before fixing any defect.

**Gate:** Two isolated accounts work in one bot. No cross-account results or credentials appear. Record each recovery mode separately.

### 2. Produce and grade the briefing on demand

Create a private fixture with reply-needed, already-replied, urgent, informational and ambiguous threads. Include old unresolved mail, new replies and long threads. Record expected classifications independently of the model. Compare the briefing against these expectations. Retain uncertainty rather than inventing facts.

Store progress per account. A retry must resume incomplete work without losing completed coverage. Publish one briefing with explicit partial coverage when an account needs owner help. Track unresolved items by account and thread. Confirm the owner's coverage window and scan limit before the real trial.

**Gate:** Every required-reply and urgent fixture item appears with correct account attribution and a valid source reference. No invented deadline or unsupported completion claim passes. Record false positives and scan omissions.

### 3. Connect at least two real Gmail accounts

Use protected input and bare only. Keep account identities, messages, captures and evaluation results private. Test initial sign-in, phone approval, saved sessions and account-specific reauthentication. Run the briefing on demand before enabling the morning trigger. Compare its findings with the owner's inbox review.

**Gate:** One real-model run covers both accounts correctly. A subsequent run reuses both sessions. One expired account produces a partial briefing and a durable request for help without blocking the other.

### 4. Run once each morning

Inspect existing task ingestion and durable event controls before adding a scheduler. Reuse them where possible. Confirm delivery time, timezone and destination. Define the late-run policy before enabling a recurring job. No new daemon or notification channel is selected by this plan.

Key a daily occurrence by bot, schedule and local date. Persist it before dispatch. Test duplicate delivery, restart during scanning, restart during publication, missed mornings and timezone changes. Pause, stop and spending holds must still apply.

**Gate:** One visible briefing per scheduled occurrence, including after restart or duplicate delivery. A blocked run has an explicit status. Never present an old briefing as today's completed scan.

### 5. Close the GitHub failure loop

Build this with the earlier stages so live failures can use it. Classify browser defects into bare and Deck defects into Deck. Treat expired credentials and owner login challenges as account waits, not automatically as defects. Search existing issues before creating one. Store a durable local fingerprint and issue reference. Retain a pending report when GitHub is unavailable.

Public reports contain a sanitized reproduction, expected behavior, observed error and relevant versions. Exclude mail, addresses, account names, credentials, cookies and private captures. Prefer synthetic reproductions. External mail cannot choose the repository or change the issue body instructions.

Connect a separate fixing agent through the approved task mechanism. Define its pickup trigger and issue ownership before enabling it. Preserve existing review requirements. Do not merge, deploy or replace the pinned browser automatically. Retry the affected check after an approved fix and clear the blocker only when that check passes.

**Gate:** Inject a safe fixture defect. Verify one sanitized issue, duplicate suppression, a separate agent's pickup, a reviewed fix and a successful regression rerun. Test unavailable GitHub and failed redaction. Never publish unsanitized evidence as a fallback.

### 6. Evaluate daily usefulness

**Proposed trial:** Five consecutive scheduled mornings across at least two real accounts. The owner reviews missed reply-needed mail, false alarms, urgency and usefulness. Record completion by account, interruptions, runtime and model cost privately.

**Proposed exit gate:** Correct account attribution on every morning; no duplicate briefing; no private data in public reports; no missed urgent or reply-needed items in the reviewed sample. Fix any miss and repeat the affected check. Record sample limits. The owner decides whether the briefing is useful enough for ongoing daily use.

## Scope still to settle

- Summary recommendations only, labels, or labels plus archive. Until selected, no mailbox mutation is part of this milestone.
- Morning time, timezone, delivery destination, coverage window and backlog limit.
- The real accounts to connect through private setup.
- The fixing-agent pickup mechanism and review owner.

General conversational bot creation, outbound mail, attachments and broad capacity work remain later work unless a proven blocker requires them.
