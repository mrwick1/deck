# Gmail inbox probe results

Date: 2026-11-15. Source: `gmail-account-trial` `d18d747`, imported with `git merge --ff-only`.

## Verdict

A deterministic, no-model scan of the first inbox page works in `bare` for all three accounts. It gives stable thread IDs and a per-thread "last non-draft message" ID. Hypothesis, not observed: that ID changes when a new message joins a thread. The probe did not observe an arrival, so it is not yet a proven change indicator. It does not prove complete incoming-mail detection. The limits are listed below.

## Method

- `scripts/vm-prototype/gmail_inbox_probe.py` runs one fixed, bounded `eval` through the guest `bare-mcp` of the retained trial. No other browser, no API, no model.
- Identity (`verify_identity` of the trial helper, all registered accounts) must equal the wanted account before and after each read. Unknown, ambiguous, blocked and other accounts refuse the read. A change during a read discards its data.
- The `eval` reads structure and opaque ID attributes only. It reads no subject, sender, snippet or body, and opens no message. Navigation is `goto` of the inbox, the Sent list and the mailbox root.
- IDs stay in process memory. Output holds counts, booleans and salted digests. The evidence directory is new and private.
- Tests: `test_gmail_inbox_probe.py` (92 tests, synthetic data only) and the unchanged helper suite (100 tests).

## Live facts (all three accounts the same)

- `resume` restored the saved profile. `switch` then verified A at `/mail/u/0/`, B at `/mail/u/1/` and C at `/mail/u/2/`. Identity needed no sign-in.
- The inbox is a `table`. Message rows are `tr[role="row"]`. There are 50 rows per page (the Gmail page size).
- Each row carries `data-thread-id` (29 characters), `data-legacy-thread-id` (16 hex characters), `data-legacy-last-message-id` and `data-legacy-last-non-draft-message-id` (16 hex characters each). Each attribute sits on two elements of a row with the same value. 50 distinct values per 100 elements.
- There is no `data-message-id` or `data-item-id`.
- Category tabs exist: Primary (selected), Promotions, Social and Updates. `#inbox` lists the Primary tab only.
- One "Older" and one "Newer" control exist (`aria-label` contains the word). "Older" was enabled on every account, so the inbox holds more than one page.

## Scan contract (version 1)

Private configuration, validated before use:

```json
{"row": "tr[role=\"row\"]",
 "thread":  {"sel": "[data-legacy-thread-id]", "attr": "data-legacy-thread-id"},
 "message": {"sel": "[data-legacy-last-non-draft-message-id]", "attr": "data-legacy-last-non-draft-message-id"},
 "older": "[aria-label*=\"Older\"]", "max_rows": 100}
```

A read is `ok` only when all of these hold: https `mail.google.com`; `readyState` complete; hash exactly `#inbox`; path `/mail/u/<digit>/`; item count equals `min(rows, max_rows)`; each row has one to three elements for each ID and all hold the same valid ID; thread IDs are unique; no empty marker is present. Other outcomes have their own status: `malformed`, `not_mailbox`, `unloaded`, `not_inbox`, `partial`, `no_rows_unproven`, `unsupported_dom` (with a reason code), `identity_refused`, `identity_changed`, `error`. Zero rows is `empty` only with a verified empty marker. No marker was found, because no account had an empty inbox. So an empty inbox reads as `no_rows_unproven`.

Example synthetic output (`example_report()`):

```json
{"contract_version": 1, "account": "A",
 "reads": [{"status": "ok", "rows": 2, "message_ids": 2, "unique_threads": 2, "range": {"start": 1, "end": 2, "total": 2}, "older": {"present": true, "disabled": true}}],
 "stability": {"reads": 4, "same_rows": true, "same_order": true, "same_messages": true},
 "continuity": "baseline", "seen": {"new_threads": 2, "changed_threads": 0, "unchanged": 0},
 "coverage": {"page_covers_inbox": true, "complete": false, "limits": ["arrival_never_observed"]}}
```

## Stability results

| Account | first | again | away and back (hash) | full refresh |
| --- | --- | --- | --- | --- |
| A, B, C | ok, 50 rows, 50 message IDs | same rows, order and message IDs | refused: `unsupported_dom` | same as first |

- The away-and-back read goes inbox, `#sent`, `#inbox` by hash navigation. It was refused on all accounts. Reason codes: `older_shape` (more than one "Older" control) on C, and `duplicate_thread_id` on a rerun for A without the "Older" check. The single-page app keeps the other view in the DOM. A hash round trip is therefore not a safe scan point. This is the intended fail-closed result. A full navigation (`goto` the root, then read) is safe.
- A second `scan` of A, minutes later, matched the seen store: 50 of 50 threads unchanged, continuity `overlap`.

## Verified, unproven and limits

Verified: identity before and after for A, B and C; fixed read-only extraction; stable thread and message IDs over repeated reads, full refresh and a later run; fail-closed handling of unloaded, foreign, malformed and ambiguous pages (offline tests, and live for the SPA duplicate case).

Not proven:
- A new arrival. No message was sent and none was observed. The `last non-draft message` ID is expected to change on a new message. This was not observed.
- A thread ID alone misses new replies in an existing conversation. The message ID covers them, but it also changes on a message that the owner sends. The probe cannot tell sender from owner without reading sender text, which is out of scope.
- Complete coverage. The first page holds 50 threads of the Primary tab. Older pages were not read. Promotions, Social and Updates are not in `#inbox`. They need their own category views, or a single list across categories. Neither was tried. The continuation rule is: scan each category view and follow "Older" until the oldest known row appears (`overlap`). A `gap` means a possible miss.
- The range text ("1-50 of N") was not located, so no total is known.
- The empty marker, the unread flag and the "Newer" control were not located.
- Read-only status of the page: the eval has no write call, but the page is third-party code.

## Executor identity

The guest identity file reports `max_epoch` 0 (an integer, read only, nothing changed). The host `executor.json` holds no epoch. The host-side absence of secrets or state does not prove that guest restore is impossible. The guest injects the paths.

## Cleanup

`down --keep-disk`: no survivors, base `a90de2e085a7ad52` unchanged, disk kept. The authenticated state stays in the retained private directory. Sanitized per-operation evidence is in a separate new private directory. Historical manifests were copied before the run and not overwritten.

## Not done

No worker is running. No production Rust file changed. Ordering by date was not verified, so `overlap` is necessary and not sufficient for no missed arrival.

## Update 2026-11-15: all-category view, paging, empty marker, bounded snapshot (contract version 2)

This update adds the `snapshot` command. It makes no worker claim.

Observed live (account A, read-only, no message opened, no mail sent):
- The Gmail search `in:inbox` (hash `#search/in%3Ainbox`) lists every category in one list. `#inbox` is the Primary tab only (range "1-50 of 523"). The search walk passed 700 unique rows, so it holds more than Primary. The search range reads "1-50 of many", so no total exists. Only a disabled pager ends a walk.
- The SPA keeps hidden duplicate grids and controls in the DOM. The scan counts visible elements only and requires exactly one visible results grid.
- Pager in the search view: `div[role=button]` with `aria-label` "Next results" and "Previous results". A disabled one has `aria-disabled="true"`. (`#inbox` uses "Older" and "Newer".) Page n has the hash `#search/in%3Ainbox/p<n>` and the range text "<start>-<end> of many". bare refuses a click on a hidden target.
- Empty: the search `in:inbox before:1990/01/01` shows a visible `td` outside the grid that starts with "No messages matched your search". That is the positive empty marker (confirmed once, account A). A nonsense term returned 36 visible rows with no pager and no range. That view is unexplained and counts as `unsupported` because no range is present.
- Pages are contiguous: 14 pages, 700 unique rows, 0 duplicate rows, each range start equals the previous end plus one.

Contract v2 (private JSON): `row` `tr[role="row"]` (inside the grid), `grid` `[role="main"] table[role="grid"]`, `view` `#search/in%3Ainbox`, `thread` and `message` as in version 1, `range` `span` (text must match "<n>-<n> of many|<n>"), `older` `div[role=button][aria-label^=Next]`, `newer` `div[role=button][aria-label^=Prev]`, `empty` `[role="main"] td`, `max_rows` 100. The `older` and `newer` selectors hold no double quote, because the bare click grammar rejects one.

Typed snapshot (counts and fixed words only; ids stay in memory): `status` (`complete`, `empty`, `incomplete`, `unstable`), `reason`, `view`, `pages`, `rows`, `unique`, `duplicates`, `end` (`older_disabled` or `empty_marker`), `stable`, `seconds`, `page_seconds`. `coverage.snapshot_complete` is true only for a `complete` or `empty` snapshot whose recheck was stable. `coverage.complete` also needs an observed arrival, so it stays false.

Walk rules: page 1 must have page number 1, a range start of 1 and a disabled Previous control. Each next page must be page n+1 and continue the range. A page with no new thread is `no_progress`. A repeated thread with another last-message ID is `changed_during_walk`. A missing pager is `pager_missing`. Hard caps: `--max-pages` (default 40, ceiling 60) and `--max-seconds` (default 300, ceiling 900). A cap, a click failure or an unreadable page gives `incomplete` and never "no new mail". After a complete walk the probe navigates again and reads page 1. A different ordered thread list or message ID gives `unstable` and refuses the snapshot.

Recommendation for the worker: take a full bounded snapshot, then recheck page 1, and refuse an unstable one. The worker semantics are inbox conversation revisions queued for triage. They are not every transient message. Overlap with the oldest known row is not used: thread order, reply reordering and date order are unproven, and the last-message ID cannot count every message or tell an own reply from an incoming one. The recheck detects changes above the walk. It is not a point-in-time snapshot: a change after the recheck, or a change that leaves page 1 unchanged and moves rows between later pages, can pass. That race is unavoidable in this UI.

Live limits (account A): one page costs about 15.6 s (identity checks, click, wait, read). The first page costs about 47 s (full identity check of all three accounts before and after). The first full run read 14 pages (700 rows) in 273 s, then bare stopped the page: every later eval threw `Limit(Host("computed-nodes"))` (bare issue 550). The full inbox did not finish, and no complete snapshot exists. The walk is `incomplete`, as designed. At that cost, a full walk of a large inbox cannot run every 120 s. A 120 s poll needs a cheaper change signal; that is open. Accounts B and C were not walked, because the page was stopped.

Probe fixes (each with a failing test first): nested malformed ID lists give `unsupported_dom` and no exception; an absent Older control and a Primary-only view are no evidence of coverage; stability flags need every read to be ok; a corrupt seen store is an error and stays on disk; the seen store is written atomically and advances only for a stable complete snapshot.

Cleanup: see the report of the run; the disk is kept, the base is unchanged.

## Parent integration correction

Every page now checks all registered account identities. A missing view cannot prove coverage. Both regression checks failed before the correction. The 93 probe tests pass after it. These corrections were not run live. The paging timings above describe the earlier code.

## Update 2026-11-15: bounded recent-mail window and one-expect identity (contract version 3)

This update adds the `recent` command. It makes no worker claim and no claim that a 120 s poll is cheap enough.

Why: the full-inbox walk (700 rows, 273 s) cannot meet a 120 s cadence, and `bare` stopped it (bare issue 550). The window is a smaller monitoring scope. It is not a fix. No budget changed, no error is hidden, and no restart is added.

Query semantics. The scan runs the Gmail UI search `in:inbox after:<epoch seconds>` (hash `#search/in%3Ainbox+after%3A<10 digits>`), all categories, no API. The epoch is absolute, so no time zone applies. Evidence is observed behaviour only. I found no source text in the page that defines the syntax. A 24 hour window gave 31, 6 and 3 rows for A, B and C. The unfiltered `in:inbox` search gave 700 or more rows. The page showed the exact query in the search box. A window that matched nothing showed the positive marker. The exact boundary (`after` is exclusive or inclusive, and the time of day it uses) is not proven. The overlap covers it.

Page contract. A page counts only when the decoded hash equals the intended query (with an optional `/p<n>`), exactly one visible grid and exactly one visible search box whose value equals the query. The unfiltered view, another bound or a different box value gives `not_inbox` or `query_box`. A known total (`of N`) must equal the unique rows (`total_mismatch`). Identity of all registered accounts is checked before and after every page. A walk needs a disabled Next control (or the empty marker) and a matching recheck of page 1.

Window plan. The first scan looks back 24 hours. Later scans start at the start time of the last stable complete scan minus a one hour overlap. After downtime the window grows. It is never cut to 24 hours. A cap, refusal or unstable page leaves the seen store and the watermark as they were. The watermark is the scan start and never moves back. The seen store merges, so threads that leave the window stay known. Defaults: 3 pages and 90 s.

Limits of a date window. It is not a guarantee for the whole inbox. A message with an old date (an import, or a delayed message that keeps its original date) can sit outside the window. A change to an older thread shows only if Gmail dates the change inside the window. That was not tested. The last-message ID changes on the owner's own reply, so a changed thread is not proof of incoming mail. No arrival was proven.

Identity cost. `verify_identity(..., want)` sends one `expect js` for all registered addresses (placeholders only). It passes only when exactly one registered address is in the single control label and it is the wanted one. Two accounts in one label, or one address registered twice, fail. A failure returns `unknown`, so the cause is not named. A pass returns at once. Before, a pass took one expect per account. Without `want`, the old per-account check stays.

Live results (account switching in one shared browser, retained VM, `--max-load inf`, host under other load, so the timings are observations only):

| Account | Initial (24 h) | Repeat (about 1 h window) |
| --- | --- | --- |
| A | complete, 31 rows, 1 page, 21.0 s (17 s page) | empty marker, 17.0 s |
| B | complete, 6 rows, 24.6 s | empty marker, 13.7 s |
| C | complete, 3 rows, 26.5 s | empty marker, 13.9 s |

Each scan was stable. None hit the 3 page or 90 s cap, and no `Limit(Host(...))` error occurred. No arrival was observed. The repeat scans found no change, and no mail was sent. An empty hour window on all three accounts was plausible at that hour but is not proof of no mail. One account scan costs 14 to 27 s here. Three accounts in turn cost about 40 to 80 s, and the identity cost was not measured separately. This might fit a 120 s cycle. That is a hypothesis. It needs a longer run with real arrivals, and it needs the busy case (a window with several pages).

Not tested live: more than one page in a window, a cap hit, an outage catch-up, an old-dated message, and own replies.

Cleanup: `down --keep-disk`: no survivors, base `a90de2e085a7ad52` unchanged, disk kept. Evidence is in a new private directory.
