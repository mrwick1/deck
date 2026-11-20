# Gmail morning summary

This document describes the opt-in morning summary. See D75 in [decisions.md](decisions.md). It builds on the worker in [gmail-worker.md](gmail-worker.md).

## Status

The summary ran live on 2026-11-15 and delivered one combined message for three accounts. See D78 and [gmail-worker-results.md](gmail-worker-results.md). Labels are on since 2026-11-15, with a 60 s settle wait before each write (D79). Before that wait, a label that Gmail showed was not saved in the shared browser (bare issue 568). The live thread read uses the print view (D77).

Code map: `src/bot/gmail_summary.rs` (schedule, prompt, validation, renderer, print read, label write, phase), `src/bot/gmail_summary_store.rs` (schema 12 queries), `src/config.rs` (`[gmail.summary]`), `Agent::complete_without_tools` in `src/agent.rs`, and the call in `gmail_cycle` in `src/bot/gmail.rs`. The service helper is `scripts/vm-prototype/gmail_service.py`.

## What it does

- Once each local day, after a set time, the bot posts one summary to its own chat.
- The summary groups mail by account, then by category: reply needed, action, awaiting reply, FYI.
- Each item has a one-line model summary and a link to the thread.
- The summary shows when each account was last checked and whether that check failed.
- It lists threads that could not be triaged yet. They stay queued for the next run.
- It adds no notification channel. The deck has none. The chat is the only target.

## Rules that never change

- The code never archives, sends, deletes for good, marks read or removes a label. The one exception is the owner-approved move to Trash with the `[gmail.summary.cleanup]` section. See [gmail-cleanup.md](gmail-cleanup.md) and D83.
- Mail is untrusted data. A message cannot grant authority. Only the owner's chat messages can.
- The model has no tools. It reads text that the host passes and returns JSON. The host validates that JSON.
- The host reads the mail. The host applies labels. The model does neither.
- Every browser step passes the same gate as a poll: pause, hold, budget, owner task, unsettled command and stop.

## Design

### Schedule

- `[gmail.summary] time` and `timezone` give the local send time. The default is `08:00` in `Asia/Kolkata`.
- A timezone is `UTC`, `Asia/Kolkata` or a fixed offset such as `+05:30`. A zone with daylight saving is refused. The machine zone is never read.
- The local day is `YYYY-MM-DD` in that offset. A summary is due when the local time is at or after the set time and the day has no delivery row.
- A missed run is not lost. A host that starts at 11:00 delivers at once, because the day has no row. A restart after delivery delivers nothing, because the row is durable.
- One summary covers every triaged thread that no earlier summary included. Skipped days merge into the next summary.
- The summary phase runs at the end of a poll cycle, inside the same poll task. It uses the same computer claim, gate and cancel token. Two browser owners cannot exist.
- A hold, a pause, an owner task or a stop ends the phase with no delivery. Its progress stays stored. The next poll that may run continues.
- The time cap (`max_run_secs`) skips the remaining work and delivers what is stored. The rest shows as "not triaged yet".
- No browser session means no phase. The poll fails first, and the summary waits for a poll that has a session.

### Triage queue

- Schema 12 adds `gmail_triage`. One row per (account, thread, message revision).
- A scan inserts a row for each unseen revision, in the same transaction as the `gmail_seen` row. Seen means arrived. It does not mean triaged.
- With the summary on, the scan queues no `bot_events` row. The summary replaces the generic event task, which gives the model full tools.
- The silent baseline still queues no event. It does queue triage rows, so the first summary covers the first window. A per-run cap bounds the cost.
- A row is `pending`, `triaged` or `superseded`. A newer revision of a thread supersedes older pending rows.
- A failed model call, an invalid answer or a failed read increments `attempts`, stores a fixed code and leaves the row `pending`. The row never drops.
- One pass tries each row once. A pass that completes delivers, so a failing row cannot block the day. It is tried again on the next local day. A pass that a gate interrupts may try a failed row again when it continues the same day.
- Rows seen before the summary was enabled get no triage row. Only revisions seen after that are queued.
- A row becomes `triaged` only after the host validated the answer and stored it in one write.

### Reading and triage

- The host opens the inbox of the account and checks the identity. It opens `https://mail.google.com/mail/u/<index>/?view=pt&search=all&th=<thread id>`, reads it, and checks the identity again in the inbox. A print view has no account control.
- A fixed program reads the text of the message tables, truncated. It checks host, scheme, the exact path `/mail/u/<index>/`, `view=pt` and `th`. Any doubt fails that thread. The read does not mark mail as read (checked live).
- A batch holds at most `batch_size` threads. The prompt gives each thread an alias (`t1`, `t2`). The model never sees a real thread ID.
- Each thread text sits between lines that carry a random nonce. The prompt says that the text is untrusted data.
- The model must return `{"threads":[{"ref","category","summary"}]}` with exactly one entry for each alias. Any extra key, unknown alias, duplicate, unknown category or bad length rejects the whole batch.
- The summary text loses control characters, links and markup characters. The host builds every link from verified IDs.
- An accounting hold from the model call stops the phase. It is not a failure.

### Labels

- Labels are `Deck/Needs reply`, `Deck/Action`, `Deck/Waiting` and `Deck/FYI`.
- The label write is data-driven. `[gmail.summary.labels]` gives a chip selector and one bare step text for each label. Config validation refuses a step that names archive, delete, trash, spam, send, mute or unread.
- Without that section, labels are off and the summary says so. The code holds no Gmail selector for labels, because none was observed.
- A list shows only part of the inbox. Per thread, the host looks for the row in the main list and then in the social, promotions, updates and forums tabs. A thread in no list gets no label and is reported as not tried.
- A step may hold alternatives, split by a line of three dashes. The next one runs only after a failed one and a fresh chip readback that shows the label absent.
- After a write the host reloads the list and reads the chips again. A chip that a reload drops is `uncertain`, never `applied`.
- `label_catchup = true` runs the label stage on each poll after the delivery too. It is off by default, because a thread that no list shows is tried on every poll.
- Per thread: identity check, find the row, read the label chips. When the chip is present, record `applied` and write nothing. Otherwise check identity again, run the step once, check identity again, and read the chips.
- The label is `applied` only when the chip is visible and every chip from before is still visible. A lost chip stops all label writes for the run and shows in the summary.
- A step that fails or an unseen chip gives `uncertain`. The next run reads the chips first. It writes again only when that fresh readback shows the label absent. Three attempts end as `failed`.
- An identity mismatch before the write writes nothing and does not count as an attempt.
- A label belongs to the thread. When a thread changes category, the old `Deck/` label stays. The code never removes a label. Two `Deck/` labels can show on one thread.
- The readback checks only the chips that the selector matches. A label that the selector cannot see is not protected by the check.

### Summary and delivery

- The host renders the summary. The model writes only the one-line item text.
- Delivery inserts the chat message and the day row in one transaction. A crash leaves both or neither.
- Rows that the summary shows get `summarized_day`. A later summary never repeats them.

### Prompt injection

Mail text cannot reach a tool. The model has none. Its output is an enum and short text. The host maps the enum to a label step that the owner configured. A mail that says "archive all" or "send a reply" changes at most one category.
One residual risk remains. The model summary text enters the chat as an assistant message. A later agent run reads chat history. The host strips links and markup and limits the length. The agent prompt already tells the model to treat event text as data.

## Configuration

```toml
[gmail.summary]
time = "08:00"
timezone = "Asia/Kolkata"
max_threads_per_run = 30
batch_size = 5
max_read_chars = 6000
max_run_secs = 900
```

Labels need `[gmail.summary.labels]` in the private configuration. `deck.example.toml` shows synthetic values. A step must wait until the page has run 60 s since its load, or a chip shows and a reload drops it (bare issue 568, D79). It needs no `Apply` click. The `Labels` menu applies a label on the click and closes.

## Live acceptance (2026-11-15)

1. Thread read: the print view works on all three accounts. Gmail opens `#all/<legacy id>` too, but rewrites the hash tail. A row click opens the thread. No bare defect.
2. Summary: one message with seven threads from two accounts, three model calls, links bound to `/mail/u/<index>/`. A later poll of the same day posted nothing.
3. Labels: the four labels exist on all three accounts. The toolbar differs between accounts and pages. A write less than 31 s after the page load showed a chip and lost it on reload. A write 45 s after the load persisted (bare issue 568, D79). Persistence was read back after a reload on accounts A and B. Account C had no pending thread.
4. Not verified live: the budget ceiling mid-run, and a refused identity during a write.

## Not tested

A daylight-saving zone (refused). Large mailboxes beyond the per-run cap. A summary on a second day.
