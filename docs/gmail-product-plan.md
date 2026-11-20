# Deck for Gmail: implementation plan

This plan works back from the product post in [gmail-product-post.md](gmail-product-post.md). The owner set the decisions on 2026-11-21 (D96).

## How to read this plan

The [product post](gmail-product-post.md) is the target. This plan works back from it. Each feature has six parts:

- **Promise:** the claim in the post.
- **Today:** what the code does now. "Prod line" means the unpushed branch `prod-next-home`. Decision numbers D68 to D106 in this plan refer to the prod line. F0.1 renumbers them on `main`.
- **Build:** the work, split into host, model, UI and bare parts.
- **Proof:** the check that shows the feature works. A private eval or a live check on a real account.
- **Needs:** the features it depends on.
- **Size:** S (1–2 days), M (3–5 days), L (1–2 weeks), XL (more than 2 weeks). These are estimates, not commitments.

Each milestone becomes one success criterion, as D56 requires. Work on a milestone ends when its proof passes.

## The current base

Facts that shape the plan:

- The Gmail code is about 12,500 lines in `src/bot/gmail*.rs`. It lives only on the prod line: 173 commits and about 31,000 changed lines ahead of `main`.
- Deck reads and writes Gmail through the `bare` browser, not the Gmail API (D72). The host runs fixed browser steps. A model with no tools sorts the mail (D75).
- The host can add `Deck/` labels and move one thread to Trash. Each write checks the account, writes a journal row, reloads and reads back (D77, D83, D92).
- New mail is seen in about 2 minutes (D73). A label write waits 60 s for the page to settle (D79, bare#568).
- The general agent, with full tools, passed send, search, reply, forward and delete in the SC1 evals on 2026-11-13. The host-owned Gmail path does not send.
- The live service is stopped since 2026-11-18. It stalled for 16 hours before the stop.
- The brief goes only to the Deck chat. The Gmail bot chat is read-only (D80).

## Decisions
The owner answered the open questions on 2026-11-21.

| # | Question | Decision |
|---|---|---|
| Q1 | How does Deck reach Gmail? | Through the `bare` browser only. No Gmail API, no IMAP. |
| Q2 | Where does Deck run in the closed beta? | Self-hosted. Hosting by us is a later plan. |
| Q3 | Where does the brief go? | The web for now: the Deck web app, with browser notifications from the web app. No email, Slack or WhatsApp yet. |
| Q4 | May Deck send without approval? | Yes, but only for people that the user names, after an explicit approval step. Off for everyone else. |
| Q5 | Which models? | Haiku for sorting, Sonnet for drafts and chat (D11). |
| Q6 | How does Deck use the model? | As little as possible. Code decides first. The model gets large batches, for example 50 mails in one call, and returns a map from each mail to its answer. Code then does the work. See "The batch pipeline". |
| Q7 | Labels | The current `Deck/` labels are not useful. Labeling is rebuilt (F3). |
| Q8 | Code structure | Organize the Gmail code so that a new feature plugs in without edits across the whole module (F0.6). |
| Q9 | Who makes the label set? | Each user gets their own set. The model proposes it from about 200 recent mails and the existing labels. The user approves it. |
| Q10 | Does Needs reply show in Gmail? | Yes, as one Gmail label. Deck removes it after the reply. |

## The batch pipeline
Every Gmail feature follows the same five steps. The model runs in one step only, and only on what code cannot decide.

1. **Collect (code, browser).** Read a whole list page at once: up to 50 rows with sender, address, subject, snippet, date, Gmail tab, existing labels, unread, starred and attachment flags. Today a scan keeps only the thread and message IDs, and each thread then needs its own page load. A row read gives most of what sorting needs, with no extra page load.
2. **Decide by code.** Apply the user's rules, the sender memory and fixed patterns. A sender that got the same label three times, or that the user corrected, is labeled by code with no model call. Gmail's own tab (Promotions, Social, Updates) is a free signal.
3. **Ask the model once per batch.** Send the rest in one call: up to 50 compact rows, each with an alias (`m1`…`m50`), never a real ID. The model returns one JSON map: alias → label, status, confidence and `need_more`. The host validates the whole shape, as D75 does today.
4. **Read more only when needed.** Only the rows with `need_more`, or a status that needs a summary or a draft, get a full read of the print view. They go back to the model as a second, smaller batch.
5. **Act by code, grouped.** Group the results by action and label. For each group, select all its rows on one list page and apply the label once. That is one settle wait per group, not one per row. Then reload once and read every row back. Each change goes through the action engine (F0.3).

The model never sees a tool and never chooses what to change. It returns data. Code maps the data to actions.

Expected effect, to verify with F0.5 evals: model calls fall from one per 5 threads today to one per 50 rows or fewer, page loads fall from one per thread to one per list page, and label writes fall from one per row to one per label group.

Search, cleanup, briefs and drafts use the same pipeline. Search collects the result rows of one Gmail query, then asks the model one question about the whole batch.
## Milestone order

| Milestone | Theme             | Features               | Customer can...                                            |
| --------- | ----------------- | ---------------------- | ---------------------------------------------------------- |
| M0        | Solid ground      | F0.1–F0.6, F3 | rely on Deck to run for a week without a person            |
| M1        | Know what matters | F1, F2, F4, F12 | get a useful brief in the web app, see and undo every action |
| M2        | Replies           | F5, F6                 | approve drafts in their voice; never miss a follow-up      |
| M3        | Trust             | F11, F13, F5 auto-send | let Deck do routine work alone                             |
| M4        | Ask your inbox    | F8                     | search and command the inbox in plain words                |
| M5        | Clean inbox       | F7                     | clean years of mail and manage subscriptions               |
| M6        | Life admin        | F9, F10                | track bills, trips, deliveries, meetings and files         |
| M7        | Ready to sell     | F14–F18                | sign up and be running in five minutes                     |

M1 is the first milestone that a beta user would pay for. M0 is the gate.

## M0. Solid ground

The product cannot promise "works all day" while the service stalls. M0 ships no new customer feature. It makes the base safe to build on.

### F0.1 Land the prod line on main

- **Today:** the Gmail code, the compact UI fixes and the bot setup changes live on local branches. Decision numbers differ between lines (D68–D106 on the prod line, D86–D95 on main).
- **Build:** rebase the prod line onto `main` in reviewable PRs, one per area: Gmail worker, summary, cleanup, topics, service helper, UI. Renumber the decisions once. Remove private values that the public repo must not hold. Keep the private config outside the repo.
- **Proof:** CI is green on `main`. A binary built from `main` replaces the prod binary and passes one full poll and one summary.
- **Needs:** none. **Size:** L.

### F0.2 Service health and self-repair

- **Today:** the wrapper alerts on some failures. On 2026-11-17/18 the service made no scan for 16 hours and sent repeated alerts.
- **Build:**
  - Find the cause of the 16-hour stall. Add a test that reproduces it before the fix.
  - Add a watchdog: a scan older than 3 poll periods restarts the browser session, then the guest, then alerts once.
  - Deduplicate alerts: one alert per incident, one "recovered" message.
  - A health record per account: last good scan, last error, sign-in state. The UI and the brief read it.
- **Proof:** a 7-day unattended run with no person. At least 99% of poll windows complete. Each induced fault (killed browser, killed guest, lost network) recovers without a person.
- **Needs:** F0.1. **Size:** M.

### F0.3 The mail action engine

All later features change mail. Today each write (label, Trash) has its own code. One engine replaces them.

- **Build:**
  - One `MailAction` type: `label_add`, `label_remove`, `archive`, `unarchive`, `trash`, `untrash`, `star`, `unstar`, `mark_read`, `mark_unread`, `snooze`, `move`, `draft_create`, `draft_update`, `send`, `unsubscribe`.
  - Each action has: a precondition read, a bare step from private config, a postcondition read after reload, and an inverse action where one exists.
  - One journal table for all actions: account, thread, latest message, action, reason, actor (rule, model, user), state (`planned`, `running`, `done`, `uncertain`, `failed`, `undone`), time.
  - Keep the existing guards: identity check before and after, one selected row, the reload readback, the open-row recovery after a restart (D83, D92).
  - Batch writes on one settled page (D92) for every action type, not only labels.
- **Bare:** each new step needs a live check on a real account, as D84 did for Trash. Failures become bare issues (docs/feedback-loop.md).
- **Proof:** a private eval runs every action and its inverse on a test thread, with a reload readback for each. A restart in the middle of an action resumes or marks it `uncertain`, never repeats a send.
- **Needs:** F0.1. **Size:** L.

### F0.4 Speed

- **Promise:** "New mail is sorted within seconds."
- **Today:** a poll every 120 s. A label waits 60 s for the page to settle.
- **Build:**
  - Fix bare#568 in bare, so that a label write does not need the 60 s wait. This is the largest speed gain.
  - Watch the open inbox for new rows between polls, instead of a full scan each time. Gmail updates the open page by itself. Hypothesis: bare already receives these updates. To verify.
  - Keep the full scan as the safety net, every 10 minutes.
- **Proof:** median time from arrival to label under 60 s, 95th percentile under 3 minutes, over one day of live mail.
- **Needs:** F0.2. **Size:** L (most of it in bare).

### F0.5 Product evals

- **Build:** a private eval set per milestone. Each case has a seeded mail and the expected result. Sources: synthetic mail sent from a test account, and anonymized real cases with the owner's yes. Score triage accuracy, draft quality (rubric), action success and time.
- **Proof:** the eval runs in one command and prints a score per feature.
- **Needs:** none. **Size:** M, then grows with each feature.

### F0.6 Organize the Gmail code
- **Today:** the Gmail code is in a few very large files: `gmail_summary.rs` (5,186 lines), `gmail.rs` (3,990), `gmail_cleanup.rs` (1,600), plus stores and topics. The summary file holds the schedule, the prompt, the reader, the label writer and the Trash writer together. A new feature touches most of them.
- **Build:** a `src/gmail/` module that follows the batch pipeline:

| Part | Holds |
|---|---|
| `browser/` | page programs, bare steps, identity checks, settle waits |
| `collect/` | list scan, row facts, backlog walk, print-view read |
| `decide/` | user rules, sender memory, fixed patterns (no model) |
| `classify/` | batch prompt, aliasing, JSON validation (one model call per batch) |
| `taxonomy/` | the user's label set (F3) |
| `actions/` | the action engine, journal, readback, inverse actions (F0.3) |
| `policy/` | trust levels (F11) and the fixed guards (F13) |
| `features/` | one folder per feature: labels, brief, cleanup, follow-ups, drafts |
| `store/` | tables and migrations, one file per area |

- Each feature implements one small interface: it reads the collected rows and the decisions, and it returns planned `MailAction`s and brief items. The runtime runs collect, decide and classify once per cycle and shares the result with every feature. A new feature adds one folder and one line of registration.
- Order: land the prod line first (F0.1), then move code in steps that change no behavior, with all tests passing after each step. Then build the engine (F0.3) inside the new structure.
- **Proof:** the full test suite passes after each move. A new sample feature (for example "star mail from VIPs") needs changes only in its own folder and the registration line.
- **Needs:** F0.1. **Size:** L.
### F3 Labeling v2
- **Promise:** labels that a person would make: few, clear, and about what the mail is.
- **Today:** two sets of `Deck/` labels: four status labels (`Deck/Needs reply`, `Deck/Action`, `Deck/Waiting`, `Deck/FYI`) and topic labels (`Deck/Receipts`, model-made topics). Labels are only ever added. The model sorts 5 threads per call after a full read of each. Each label is one write with a 60 s wait.
- **Problems:** status is not a good label: it changes, and an add-only label goes stale. The `Deck/` prefix names the tool, not the mail. Free model topics drift. The cost per mail is high.
- **Build:**
  - **A label set per user.** At setup, collect about 200 recent rows (4 pages) and the user's existing labels. One model call proposes 8 to 15 labels, nested where useful (for example `Money/Bills`, `Money/Receipts`, `Travel`, `Work/Clients`, `Shopping`, `Newsletters`, `Security`). It reuses an existing user label when one fits. The user edits and approves the set. A new label later needs the user's yes in the brief.
  - **No `Deck/` prefix by default.** The host records every label it applied in the journal, so undo and cleanup do not need a prefix. A prefix stays as an option.
  - **Status leaves the labels, except Needs reply.** To do and Waiting live in Deck: in the brief, the To-do list and the activity log. Needs reply shows in Gmail too (owner decision, 2026-11-21) as one label, `Needs reply`. A label, not the star, so that the user's own stars keep their meaning. Deck removes the label when the user replies or marks the thread done, through `label_remove` (F0.3).
  - **Code first.** Sender memory, user rules and Gmail's tab decide most mail with no model call (pipeline step 2).
  - **One batch call.** The rest goes to the model 50 rows at a time, with the label set in the prompt. The answer must name a label from the set, or `none`.
  - **Grouped writes.** One selection and one apply per label on a list page, then one reload and readback (pipeline step 5).
  - **Labels follow the mail.** With `label_remove` from F0.3, a wrong label is removed when the user or a rule corrects it. A user's own move in Gmail is read as a correction and updates the sender memory.
  - **Migration.** Offer to rename or remove the old `Deck/` labels in one approved step.
- **Proof:**
  - Label accuracy at least 90% on the eval set, against labels the owner chose by hand.
  - At least 60% of mails after the first week are labeled by code with no model call. Hypothesis, to measure.
  - 50 new mails are labeled with at most 2 model calls and one write per label group.
- **Needs:** F0.3, F0.6. **Size:** L.
## M1. Know what matters

### F1 Smart triage and priority people

- **Promise:** five groups as mail arrives. Deck learns who matters. The user can pin VIPs.
- **Today:** four groups (Needs reply, Action, Waiting, FYI), plus a `kind` and a topic (D75, D104). No notion of people.
- **Build:**
  - Host: add the group `Noise`. Rename `Action` to `To do` in the UI only.
  - Host: a `contacts` table per account, built from the scans and from Sent: address, name, mails in and out, reply speed, last contact. A score from these facts, computed by the host, not the model.
  - Host: a `vip` flag set by the user, which overrides the score.
  - Model: the triage is part of the same batch call as the labels (F3). The sender score and the VIP flag go into each row as data. No extra model call.
  - UI: a People page with the top contacts, the VIP toggle and the reason for each score.
- **Proof:** triage accuracy at least 90% on the eval set, with no `Needs reply` mail from a VIP placed in `Noise`.
- **Needs:** F0.5. **Size:** M.

### F2 Urgent alerts

- **Promise:** security alerts, failed payments, flight changes and VIP mail reach the phone at once.
- **Today:** no per-mail notification. The service alerts the owner about faults only.
- **Build:**
  - Host: a fixed list of urgent kinds: `security`, `payment_failed`, `travel_change`, `vip_needs_reply`. The model marks a candidate. The host confirms it with word rules, as D95 does for payments.
  - Host: a notification outbox with one row per alert, retry and dedupe. Quiet hours per user.
  - Delivery: browser notifications from the Deck web app (Q3). Other channels later.
- **Proof:** each urgent eval case alerts once within 3 minutes of arrival (after F0.4). No alert for the non-urgent cases.
- **Needs:** F1, F4 delivery. **Size:** M.

### F4 Daily brief v2

- **Promise:** one brief for all accounts, at chosen times, with one-tap actions and a weekly review. Beta channel: the web app (Q3).
- **Today:** one brief a day, in the Deck chat, with thread links (D75, D78).
- **Build:**
  - Host: several brief times per user. Time zones from the IANA database with daylight saving (adds a time zone crate; today a fixed offset is used).
  - Host: a channel layer with one interface. Beta adapters: the Deck chat, a Brief page and browser notifications. Email, Slack, WhatsApp and Telegram come later as new adapters.
  - Host: one-tap actions. Each item carries signed, single-use action tokens: Approve draft, Snooze, Archive, Open in Gmail. A tap queues a `MailAction` through F0.3.
  - Host: a weekly review: counts from the journal, open threads, noise removed.
  - UI: a Brief page that shows today's brief with the same buttons.
- **Proof:** a brief arrives at each set time in the web app, with a browser notification. Each button performs its action and the journal shows it. The weekly review counts match the journal.
- **Needs:** F0.3, F12. **Size:** L.

### F12 Activity log, undo, pause, watch

- **Promise:** every action with its reason, one-tap undo, one pause switch, live view.
- **Today:** journals exist per write type. Pause writes is a config edit and a restart (D96). The live view exists.
- **Build:**
  - UI: an Activity page from the F0.3 journal: action, reason, mail subject, time, actor, state.
  - Host: undo runs the inverse action through the engine. Undo of a send is not possible after delivery, so sends use a delay window instead (F5).
  - Host: pause writes as a runtime switch in the API and UI, not a config edit.
- **Proof:** undo of each action type restores the mail state after a reload. The pause switch stops all writes within one poll and reads continue.
- **Needs:** F0.3. **Size:** M.

**M1 success criterion:** for 7 days on 3 real accounts, the owner gets a brief on the phone each morning, triage scores at least 90%, every action shows in the log and undo works for each type.

## M2. Replies

### F5 Replies in your voice

- **Promise:** a draft for every mail that needs a reply, in the user's tone, with context. The draft waits for approval.
- **Today:** nothing in the host path. The general agent can send through bare.
- **Build:**
  - Host: a style profile per account. Read a sample of Sent mail (for example the last 200 replies). Group by relation (colleague, client, family) from the contact data. Store a short style note and a few example replies per group. Refresh weekly.
  - Host: a facts store the user edits: rates, address, availability, signature. The user teaches facts in chat too ("my rate is ...").
  - Model: a draft call with the thread, the style note, the examples and the facts. The model returns text only. The host builds the draft.
  - Host: `draft_create` through F0.3 writes the draft into Gmail, so the user sees it in Gmail too.
  - Host: approve sends with `send` after a 30 s undo window. Edit opens the draft in Deck.
  - Guards: never send to an address that is not in the thread. Never include a password, a code or a card number. The model never chooses recipients.
  - UI: a Drafts page and draft cards in the brief: Approve, Edit, Discard.
- **Proof:** on the eval set, at least 70% of drafts are approved without edit by the owner (rubric score). Every approved send is found in Sent with the correct recipients and thread.
- **Needs:** F0.3, F1, F4. **Size:** XL.

### F6 Follow-ups, snooze and promises

- **Promise:** reminders and nudges when nobody answers; snooze to a time or an event; promises the user made become to-dos.
- **Build:**
  - Host: on each sent mail that asks a question, a `follow_up` row with a due date (default 3 working days, set by the user).
  - Host: at the due date with no reply, either a reminder in the brief or a nudge draft through F5.
  - Host: `snooze` as an engine action: archive now, a timer row, unarchive and mark unread at the time. "When the payment arrives" is a rule that watches for a matching mail.
  - Model: find commitments in the user's sent mail ("I'll send it by Friday"). The host stores a to-do with a date and reminds one day before.
  - UI: a Waiting and To-do list.
- **Proof:** seeded sent mail with no reply produces a reminder at the due date. A reply before the date cancels it. A snoozed thread returns at its time.
- **Needs:** F0.3, F5. **Size:** L.

**M2 success criterion:** for 7 days, every mail that needs a reply has a draft before the morning brief, at least 70% are approved unedited, and no follow-up due date is missed.

## M3. Trust

### F11 Trust levels and learning

- **Promise:** Suggest, Ask once, Autopilot per action and account. Deck starts on Suggest and learns from corrections.
- **Build:**
  - Host: a policy table: (account, action type, condition) → level. The engine reads it before each action. Suggest makes a planned row and waits.
  - Host: "Ask once" counts approvals per (action, condition). After N approvals with no reject, it offers Autopilot in the brief.
  - Host: corrections ("not spam", "this person is important", a moved label in Gmail) become rows that later triage prompts read as examples. A label that the user changes in Gmail is detected on the next scan.
  - Model: few-shot examples from the corrections, selected by sender and kind.
  - UI: a Trust page with the table and the history.
- **Proof:** an action on Suggest never runs without approval. A correction changes the result of the same eval case next time.
- **Needs:** F12. **Size:** L.

### F13 Fixed guard rails

- **Promise:** never delete for good, never empty Trash, never pay or buy, never send to a new contact, never share a password or code, never forward a security mail without approval each time.
- **Today:** the code has most of these as fixed rules (D83 config validation).
- **Build:** move the rules into one module that the engine calls last, after policy. No config or trust level can change it. Add tests for each rule.
- **Proof:** a test per rule. A red-team eval with mails that try to make Deck break each rule.
- **Needs:** F0.3. **Size:** S.

### F5b Auto-send for approved people
- **Decision:** Q4. Auto-send is off by default. It turns on only for people the user names.
- **Build:**
  - Host: an `auto_send` list per account: contact, allowed reply classes (for example acknowledgement, scheduling, routine answer) and an optional daily cap.
  - Host: an explicit approval step. Deck proposes a person after the user approved several drafts to them unedited. The user confirms in a dedicated dialog that shows the person, the classes and examples. Adding a person by hand needs the same dialog. Every change is in the activity log.
  - Host: an auto-sent reply still waits 30 s in an undo window and shows in the brief.
  - Guards (F13) still apply: no new recipients, no secrets, no money words, no security mail.
  - UI: the list on the Trust page, with remove at one tap.
- **Proof:** no reply is sent alone to a person outside the list. Removing a person stops auto-send at once.
- **Needs:** F5, F11, F13. **Size:** M.
## M4. Ask your inbox

### F8 Chat, search and plain-language rules

- **Promise:** search the whole history in plain words, give commands, make rules that replace Gmail filters.
- **Today:** the Gmail bot chat is read-only (D80). The general agent can search Gmail in the browser.
- **Build:**
  - Host: open the Gmail bot chat. Chat turns run the agent with Gmail tools that map to the engine, not raw browser tools: `search`, `read_thread`, `summarize_thread`, `draft_reply`, plus the actions of F0.3. Every write passes policy and guards.
  - Host: search uses Gmail's own search in the browser. The model writes the Gmail query. The host collects all result rows, then the model answers about the whole batch in one call (the batch pipeline). It opens a thread only when the rows do not answer the question. Optional later: a local index of read threads for faster answers.
  - Host: rules as data: a condition (sender, words, kind, topic) and actions. The model turns a sentence into a rule. The user confirms the rule card. The host applies rules before the model triage.
  - UI: chat in the bot pane, rule cards, a Rules page.
- **Proof:** an eval of 30 questions with known answers in a seeded account, at least 85% correct. A rule made in chat applies to the next matching mail.
- **Needs:** F0.3, F11. **Size:** L.

## M5. Clean inbox

### F7 Cleanup, subscriptions and digest

- **Promise:** sort years of mail with a plan first, a subscription manager with unsubscribe, a newsletter digest, noise removal.
- **Today:** backlog walk, guarded Trash of single-message promotions, topic labels (D83–D85, D94).
- **Build:**
  - Host: read the whole mailbox, not only the inbox (`in:anywhere` with exclusions), on the backlog path.
  - Host: a `senders` table: list id, unsubscribe header or link, mails per month, opened or not (read state in the list).
  - Host: `unsubscribe` as an engine action. Prefer the one-click header (RFC 8058) through Gmail's own Unsubscribe button. Never open an unknown link without approval.
  - Host: `archive` for noise on Autopilot, in place of Trash when the user prefers.
  - Host: a cleanup plan: counts per sender and per proposed action. The user approves the plan before the first large run.
  - Model: a digest of kept newsletters, in the weekly or daily brief.
  - UI: a Subscriptions page and a Cleanup plan page.
- **Proof:** on a seeded account with 2,000 mails, the plan matches the expected counts within 5%. Unsubscribe works on the Gmail one-click path. No protected mail moves.
- **Needs:** F0.3, F0.4, F11. **Size:** L.

## M6. Life admin

### F9 Bills, receipts, travel, deliveries, meetings

- **Promise:** due dates and reminders, receipts filed, trip summaries, delivery status, meeting invites.
- **Build:**
  - Model: per kind, extract a fixed JSON shape (bill: payee, amount, due date; trip: legs, times, booking ref; delivery: carrier, status; invite: time, organizer). The host validates each field.
  - Host: one table per kind and a timeline. Reminders through F2 and F4.
  - Host: "a payment that already went through is filed" reuses the D95 rules.
  - Host: meeting invites use Gmail's own Yes, No and Maybe buttons as engine actions. Clash detection needs a calendar view. Gmail-only scope: read clashes from invites in the mailbox. A calendar connection is out of scope for now.
  - UI: Money, Trips and Deliveries pages.
- **Proof:** extraction at least 95% field accuracy on the eval set. A bill reminder arrives 2 days before the due date.
- **Needs:** F1, F4. **Size:** XL.

### F10 Attachments

- **Promise:** read attachments, save chosen files, attach the right file to a draft.
- **Build:**
  - Bare: download from Gmail into the guest (SC1 `gmail-download` passed). Read PDFs with a text extractor in the guest.
  - Host: a files table with hash, thread, kind. Rules to save files to a folder. "A drive you choose" means a local folder or a WebDAV target first. Google Drive needs OAuth (see Q1).
  - Host: `draft_create` with attachments, through bare upload.
- **Proof:** an invoice PDF is read, its amount appears in the bill record, and the file is saved with a clear name. A draft carries the right attachment.
- **Needs:** F5, F9. **Size:** L.

## M7. Ready to sell

### F14 Sign-in that lasts

- **Today:** protected input and saved sessions work (D29, D63, D68, D71).
- **Build:** detect a sign-in wall on every scan. Tell the user once, with a protected-input card. Support Google Workspace accounts, including the SSO redirect of common identity providers.
- **Proof:** a forced sign-out produces one notice and a working re-sign-in with no other step.
- **Size:** M.

### F15 Data, hosting and privacy

- **Build:** export of all Deck data per user. Delete all data with one action, verified. A written data policy: no training on user mail. Then the hosted service (Q2): one isolated guest per user, encrypted disks, billing, sign-up, support. The hosted service is a separate plan.
- **Size:** M for export and delete; XL for hosting.

### F16 Spending

- **Today:** a spending ceiling and accounting exist (D36, D61). The UI has no spend data.
- **Build:** cost per day and per feature in the API and UI. A monthly limit with a warning at 80%.
- **Size:** S.

### F17 Basics

- **Any language:** triage and drafts in the mail's language. The guard word lists (D83, D95) need a per-language list or a model check confirmed by the host. Size M.
- **Desktop and phone:** finish the paused desktop redesign (control room), then the PWA with web push. A native app later. Size L.

### F18 Five-minute onboarding

- **Build:** a guided setup: one sentence, connect accounts, then a discovery report from the first scan (VIPs, subscriptions, open threads, cleanup plan), then trust levels, brief times and limit. First brief within the hour.
- **Proof:** a new test user reaches the first brief in under 10 minutes of their own time.
- **Needs:** F1, F4, F7, F11. **Size:** L.

## Cross-cutting rules

- **Bare first.** A Gmail failure in the browser becomes a bare issue, ported from Ladybird, Boa or Servo where possible. No Chrome fallback.
- **Code first, batch the model.** Code decides what it can. The model gets batches, never one mail per call. Count model calls per mail in every eval.
- **The model never acts.** The model returns data. The host decides and acts through the engine, policy and guards.
- **Mail is untrusted.** No mail text can grant authority. Only the user's chat and settings can.
- **Every write is checked.** Identity before and after, reload, readback, journal.
- **Every feature has an eval** before it ships.

## Suggested first steps
1. Land the prod line on `main` (F0.1). Start the product evals (F0.5) in parallel, with a hand-labeled set for F3.
2. Organize the code (F0.6) in moves that change no behavior.
3. Build the action engine (F0.3) and labeling v2 (F3) on the new structure, with the batch pipeline.
4. Service health (F0.2) and speed (F0.4) next. M1 starts when M0 passes.

## Risks

- **Gmail changes its page.** Browser steps break without notice. Mitigation: steps live in config, daily live checks, fast fixes.
- **Google blocks automated sign-in.** Mitigation: one long-lived session per account, human-like pace, phone approval on the first sign-in. To verify at scale.
- **Bare speed.** Speed promises depend on bare fixes (bare#568 and others).
- **Model errors.** A wrong send hurts trust most. Mitigation: Suggest by default, the undo window, fixed guards.
- **Cost per user.** Drafts and chat use the larger model. Measure cost per user per day in M1 and M2 before pricing.
