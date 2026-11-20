# Gmail mailbox cleanup

This document describes the cleanup of the Gmail phase. See D83 in [decisions.md](decisions.md). It builds on [gmail-morning-summary.md](gmail-morning-summary.md).

## What it does

- The owner approved it: organize the mailbox, keep software engineering and AI newsletters, and move clearly unwanted promotions and irrelevant newsletters to Trash.
- The only new write is "move one thread to Trash". The code never deletes for good, never empties Trash, never archives, never unsubscribes, never marks spam and never sends.
- Trash keeps a thread for 30 days. The owner can restore it.

## How a thread is judged

1. The host reads the print view of the thread. It records the message count and whether the text was cut.
2. The model has no tools. It returns a category, a one-line summary, a `kind`: person, work, receipt, security, job, tech, promotion, newsletter or other, and a `topic` (D106, below). It names no action and no target.
3. The host decides from the kind and the facts it read:

| Kind | Result |
| --- | --- |
| person, work, security | Category label only. |
| receipt | The topic label, else `Deck/Receipts` |
| job | The topic label, else `Deck/Jobs` |
| tech | The topic label, else `Deck/Software and AI` |
| other | The topic label when the model named a valid topic, else `Deck/Review` |
| promotion, newsletter | Trash, only when every guard below holds. Otherwise `Deck/Review`. |

Guards of a move to Trash:
- The category is `fyi`.
- The thread holds exactly one message.
- The text was read in full.
- The text holds no protected word (invoice, receipt, payment, password, verification or security code, interview, job offer, your application, statement, refund and similar).
- The text holds no whole word of software engineering or AI content (ai, llm, api, developer, software, engineer, github, rust and similar). The owner keeps such mail even when it sells something. It goes to review.
- The text holds no phrase that tries to steer a reader ("ignore previous", "trash all" and similar).
- At write time, the list row shows the thread once, with the latest message the host read, and no label of the owner besides `Deck/` labels and the `Inbox` chip that Gmail adds in a category list (D98).

A thread with several messages is never trashed, even when the latest one is a promotion. Existing labels are never removed. A `Deck/` category label still goes on kept threads. A thread that waits for Trash gets no category label first.

## How a write is made

- The step text is `[gmail.summary.cleanup] trash_step` in the private config. It is host text. Config validation refuses a step that holds archive, spam, send, mute, unread, permanent, forever, empty, report or unsubscribe. It must name delete or trash. The label steps still refuse delete and trash.
- Before a write: identity, the row bound to the thread and its latest message, one selected row.
- The host writes a journal row first. A restart marks an open row `unknown`. The next pass reads the lists and Trash first.
- After the write: identity, a reload of Trash, a read of the row. Only a thread that Trash shows after the reload counts as moved. Two attempts end as review.
- A step that ran and left no receipt is not repeated when Trash shows the thread.

## The backlog

- The window scan stays. It covers the last scan window.
- The backlog walk reads `in:inbox before:<ceiling>`, newest first, from a stored page. The ceiling is the lower bound of the window scan at the start. It never moves.
- It reads `backlog_pages_per_pass` pages for each account in a pass. An account stops while `backlog_queue_cap` of its own backlog rows wait for triage. A full queue of one account never stops the walk of another (D85).
- A page with a new thread is read again. A page with no new thread is skipped on the next read. A sweep that reaches the end with a new thread starts another sweep. A sweep with no new thread completes the walk.
- New mail ranks before the backlog in triage, in labels and in moves to Trash. A thread that a pass could not handle moves behind the others.
- `backlog_exclude` adds search terms to the walk. Leave it empty. A live check on 2026-11-15 showed that `label:deck` matches no thread, because Gmail has no label of that name: the `Deck/` labels are flat names. So `-label:deck` excludes nothing. A term that names one `Deck/` label would need a `/`, which the validation refuses. The walk needs no exclusion: it skips pages of known threads, and a sweep with no new thread ends the walk.
- Threads that were triaged before this change keep their label. The walk does not queue them again.

## Pass caps

Each poll runs one pass, before the daily summary: the backlog read first, then triage (`triage_per_pass`), moves to Trash (`trash_per_pass`), and category labels and topic labels (`topics_per_pass` each). The writes share `max_run_secs`. The backlog read has its own share of one third of it, so slow writes cannot starve it (D85). A label write waits 60 s for the page to settle, so labels are the slow part. Rows of one account and one list are written on one settled page and read back after one reload (D97).

## Notifications

A routine success makes no alert and no notification. The daily summary message gets one block: the count of moves, topic labels and review threads since the last summary, the waiting work, and the state of the walk of each account. The service helper alerts on holds, sign-in problems and stale scans only. `Bot::status` holds counts and fixed words.

## Configuration

```toml
[gmail.summary.labels.steps]
receipts = "..."
jobs = "..."
tech = "..."
review = "..."

[gmail.summary.cleanup]
trash_step = "..."
trash_per_pass = 5
```

The cleanup needs `[gmail.summary.labels]`. A topic label without a step is never written.

## Read limit

`[gmail.summary] max_read_chars` sets how much of a thread the host reads. A thread that is longer is cut, and a cut thread never goes to Trash (reason `partial_read`). The service config key is `summary_max_read_chars`. A long promotion needs a high limit to reach Trash. The browser shows the account name as `[secret]`, so the host judges a cut from the page length and the sent length, not from the returned text (D86). An earlier `partial_read` review is read once more, one thread in each pass.

## One account at a time (D101)

The backlog job works on the first registered account that has backlog work left, and on no other account's backlog. New mail of every account keeps its own path. The status shows `focus` and, for each account, `pending` and `unresolved`. A walk that is `complete` with `unresolved` above zero means some rows ran out of attempts and need the owner. An account that needs a person (`auth`, `identity_mismatch`) never holds the focus.

## A done payment (D102)

A notice of a payment that already happened is `fyi` with kind `receipt`. The host lowers an `actionable` receipt to `fyi` only when its text shows that the payment is done and asks nothing of the owner. Boilerplate sentences such as "if you did not make this transaction" are ignored. A receipt with no such evidence stays `actionable`. Money words keep a promotion out of Trash. Deck labels are additive: an earlier wrong label stays until the owner clears it.

## Pausing the writes (D103)

Move `labels` and `cleanup` under `paused_writes` in the service config and restart at a quiet point. Polling and triage go on. Nothing is written to Gmail. Move the keys back to resume.

## Dynamic topics (D106)

- The model proposes a short topic for each thread, such as Travel, Deliveries or Learning. The user message lists the current topics. The model must reuse one when it fits.
- The host checks the topic: 2 to 24 characters, at most 3 words, letters, digits, spaces and hyphens, at most 2 digits, no reserved word. It matches the registry by lower case, single spaces and one final `s`. A match reuses the topic. The four fixed topics are seeded aliases: `Receipts`, `Jobs`, `Software and AI`. `Review` is a host label and never a proposed topic.
- A new topic becomes a registry row. The label is `Deck/<Name>`. The registry is per bot. For each account it records `creating` or `verified`. An account creates the label only when one of its threads needs it, so each account verifies the same topic on its own.
- Two private steps write it, both with `{label}` inside a quoted argument: `topic_step` applies an existing label, and `topic_create_step` creates the label from the label menu and applies it to the selected thread. The write uses the apply alternatives first. The create alternatives run only after a failed apply and a fresh read that shows no chip. A retry looks first, so it cannot create a second label. Gmail also refuses a duplicate name. The write keeps the identity checks, the journal, the reload and chip readback and the lost-label halt.
- The topic never changes the kind and never authorizes Trash. `decide` is unchanged. A thread that the host would trash, or keep for a guard, gets no topic.
- Limits: `topics_new_per_pass` (default 2) new registry topics and new Gmail labels in one pass, `topics_registry_cap` (default 30) topics. An `other` thread that hits a limit or has an invalid topic goes to `Deck/Review` with the reason `topic_limit` or `topic_invalid`. A receipt, job or tech thread then takes its fixed topic.
- Rows from before D106 keep their fixed topic and are not read again. Without `topic_step` the feature is off and nothing changes.
- The status shows `topics`: registry size, dynamic topics, accounts verified and creating, labels created, and rows sent to review by a limit or a bad name. The daily summary counts labels of dynamic topics.
- The owner's own labels are never read as topics, renamed or changed. The new labels live under `Deck/`.

```toml
[gmail.summary.labels]
topic_step = "..."         # holds {label} inside a quoted argument
topic_create_step = "..."  # creates the label and applies it
[gmail.summary.cleanup]
topics_new_per_pass = 2
topics_registry_cap = 30
```

## Not covered

- The model can misjudge a kind. The guards cut the risk. They do not remove it.
- The words in the guards are English only.
- A thread that no list shows is not labeled and not trashed.
- Mail that is not in the inbox is not read.
