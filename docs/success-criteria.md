# Success criteria

Work in `deck` follows success criteria. The owner sets one criterion at a time. Work on a criterion ends only when its checks pass. Each criterion names its tasks, its proof and its pass rule.

## SC1. One morning-summary bot across multiple Gmail accounts

The owner selected this first delivery goal on 2026-11-14. One bot must cover multiple Gmail accounts. Use one browser session and Gmail account switching. A separate bot or isolated browser session per account does not meet the goal.

**Output:** One morning briefing identifies mail that needs a reply, urgent items, other actions and low-priority information. Each item names its account and links to its source thread. The briefing states coverage and lists blocked accounts.

**Proof:** Start with two synthetic accounts and an account switcher in one browser session on one bot computer. Then run private acceptance with at least two real Gmail accounts and the real model through bare. Verify account identity, login persistence, summary accuracy and recovery independently of the bot's claims.

**Pass rule:** Both accounts appear correctly in one briefing. A blocked login does not prevent the other account's briefing. Restart and duplicate trigger checks produce no duplicate briefing. Private mail and credentials never enter public issues. The daily trial must meet the acceptance gates in [the delivery plan](gmail-morning-plan.md). No acceptance pass is claimed yet.

**Approved action scope:** Apply triage labels and include recommendations in the briefing. Do not archive mail. Sending, deleting and other mailbox changes remain outside this milestone.

**Pending owner choices:** Morning time, timezone, mail coverage window and label names.

## Later Gmail capabilities

The prior full-mail workflow remains a later target. Sending, replying, forwarding, attachment handling and deletion are not prerequisites for the morning briefing.

### Gmail works end to end

**Goal:** The deck agent signs in to Gmail through `bare`. It then does normal mail work in that account.

**Proof:** Private eval tasks in `evals/run.py`. They run with the real model and the real `bare-mcp`. The task file, the secrets file and the browser state stay outside the repository (see `evals/README.md`, D52 and D53).

**Pass rule:** One full run passes every task below. No task needs a manual step, except one phone approval on the first sign-in.

### Tasks

Run the tasks in this order. Each task after the first uses the saved session.

| Id | Task | Check |
| --- | --- | --- |
| `gmail-login` | Sign in with the address and password secrets. Handle the second factor. Save the session. | Run ends `done`. The answer names the signed-in address. The password is not in the answer or a tool argument. |
| `gmail-reuse` | In a new deck process, restore the session and read the inbox. | No sign-in form is used. The answer lists inbox messages. |
| `gmail-send` | Send a mail to the test address with subject `deck-{{run_id}}`. Attach the three made files. | The send action shows in the trace. Later tasks find the message. |
| `gmail-search` | Search for `deck-{{run_id}}`. Open the message. Report the sender, the subject and the body. | The answer holds `{{run_id}}` and the body text. |
| `gmail-reply` | Reply to the message. Forward it to the test address. | Searches find the reply and the forward. |
| `gmail-download` | Download the three attachments. | `downloaded` holds: each file has the SHA-256 of a made file. |
| `gmail-organize` | Add a label. Star the message. Mark it unread, then read. Archive it. | A new chat reads the state back and finds each change. |
| `gmail-delete` | Delete the test messages of this run. | A search for `deck-{{run_id}}` finds no message outside the trash. |

### Known gaps

- `evals/run.py` runs all tasks in one deck process. `gmail-reuse` needs a second run with the same `--state-dir`.
- The checks of `gmail-organize` and `gmail-delete` read Gmail through the agent. No check reads Gmail state independent of the agent.
- Gmail failures in `bare` become `bare` issues, as `docs/feedback-loop.md` describes.
