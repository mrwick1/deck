# Gmail polling worker

This document describes the opt-in Gmail worker. See D72 and D73 in [decisions.md](decisions.md).

## Status

The code is tested with fakes and in one bounded live acceptance. See [gmail-worker-results.md](gmail-worker-results.md). Activation is a poll-only user service. See D76 and the section below. No incoming mail was observed. Issue 550 is unresolved.

## What it does

- A `[gmail]` section turns the worker on. Without it, the bot makes no poll and no wake-up.
- The section needs `[vm]`. A provisioned bot never inherits it.
- The worker uses the one shared browser. It checks the accounts one after the other.
- A scan searches `in:inbox after:<epoch>` in the Gmail page. It reads only opaque thread and message IDs.
- It checks the account identity before and after every read. It never signs in.
- It opens the configured profile only when no browser session exists.

## Scope

A scan covers a date window, not the whole inbox. The first scan of an account looks back `initial_hours` (default 24). A later scan starts at the start of the last successful scan minus `overlap_secs` (default 3600). Mail older than that window is not seen. A scan that hits a page cap, a time cap or any doubt fails and moves nothing.

## Scheduling

- The worker runs inside `Bot::step`. There is no second timer.
- The interval is 120 seconds from start to start. A slow poll leaves one poll due, never a burst.
- A pause, a hold, a waiting task, an owner task or an unsettled command blocks a poll. A blocked poll waits for a change.
- An owner message waits for the poll and runs next. The poll yields between accounts.
- A stop or a time cap settles the journal first. An unsettled command blocks every later poll.

## Data

- Schema 11 stores the watermark for each account, and the task `execution_kind`.
- One transaction writes the seen rows, the events, the watermark and the success time.
- The first scan after a start is silent when the watermark is empty. A legacy schema 10 account seeds a silent baseline again.
- A failed scan updates the attempt time and a fixed result code only.
- A clock behind the watermark is refused.

## Configuration

`search_box_selector` is required. The observed contract does not record it. Set it from a live check of the page. The example is in `deck.example.toml`, with synthetic values.

## Status output

`Bot::status` has a `gmail` section. It lists the interval, whether a poll runs, the last cycle and each account. It never holds page text.

## Morning summary

An opt-in `[gmail.summary]` section adds a daily summary after the poll. It changes what a scan queues. See [gmail-morning-summary.md](gmail-morning-summary.md) and D75.

## What remains for activation

- `search_box_selector` and the account control selector passed a live check. They are in the results document.
- A longer run with real arrivals, more than one result page and the idle save.
- A decision on issue 550.
- A guest-side recovery for a stale results page. The worker retries once through the mailbox root.

## Integration regressions

The parent added three regression checks. A deadline that expires between scheduler checks must still wake the loop. A database read failure must not become a new baseline. A failed page probe must not replace an existing browser session. Each check failed before its correction. The live acceptance ran the corrected code. It did not exercise each of these three cases.

## Poll-only service

- `[gmail] poll_only = true` stores new events and queues none. A start with `poll_only` does not replay stored events into triage.
- `scripts/vm-prototype/gmail_service.py` runs the real Deck binary as a user service. `install` copies the code and the binary into `~/.local/share/deck-gmail`. It writes a private config and the unit.
- The wrapper takes its own lock, checks the host database binding and memory, resumes the VM with its own boot lock, starts Deck, and releases only a pause that it set itself.
- On stop it pauses, waits for the running poll, stops Deck, then stops the VM and keeps the disk and the database.
- `status.json` in the private state directory has per-account result and ages, the stage and account of a running poll, the unprocessed event count and a cycle counter. `gmail_service.py status` prints it.
- An alert goes out through `tg-notify` for `auth` or `identity_mismatch` at once, for an account without a good scan for 10 minutes, for a hold and for a failed start or exit. Each alert repeats at most hourly.
- To enable triage later, remove `poll_only`, set a model endpoint and add the label-only guard. The stored events then batch at start.
