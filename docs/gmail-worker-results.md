# Gmail worker live acceptance results

Date: 2026-11-15. Base: `9a90791`. Harness: `scripts/vm-prototype/gmail_worker_e2e.py` (19 offline tests in `test_gmail_worker_e2e.py`).

## Verdict

The actual worker ran live and passed all 20 gates in its fifth run. The four earlier runs failed or stopped, and each failure is recorded below. The pass covers a bounded run of four polls (three, then one after a pause and a restart). It does not prove incoming-mail detection, because no mail arrived. The service activation below left the worker running (poll-only).

## What ran

- The real Deck binary, built from this tree, with a `[gmail]` section for three accounts and one shared profile.
- Path: Deck Bot loop, then the Hub/GuestHost journal, then the guest `bare-call`, then the pinned `bare-mcp` (digest `d9435503…f0831b`, the retained candidate).
- The production scanner script and page parser ran. Only the model was a loopback stub. It received 0 requests.
- Interval 120 s, first look-back 24 h, overlap 1 h, 3 pages, 90 s per account, idle save off.
- The VM was the retained shared-profile overlay. The helper resumed it and checked the account control (`a[href*="SignOutOptions"]`) for all three accounts in one session. One narrow `recent` probe confirmed the search box selector `input[name="q"]`: one complete, stable window scan. The helpers then exited. They held no lock and sent no command while Deck owned the browser.
- The host database was private and outside Git. The guest executor identity was never reset. The real server claimed the next epoch for the same guest each time (final epoch 5).

## Gates of the passing run

| Gate | Result |
| --- | --- |
| Preflight (binding, base, digest, ports, selectors) | pass |
| Resume, three account checks, search box probe | pass |
| Server connected, `gmail_poll` configured, 120 s | pass |
| First poll: three accounts `ok`, silent, 0 model requests | pass |
| Journal: every command belongs to a `gmail_poll` task and ended `succeeded` (182 commands in the database, from all runs) | pass |
| Three polls complete, each window starts at stored watermark minus 1 h | pass |
| Watermark in the database equals the status | pass |
| Start-to-start gaps 120.0 s and 120.0 s | pass |
| No duplicate events (0 events) | pass |
| One running task at a time, one QEMU, polls strictly ordered | pass |
| Pause while idle: a due tick started no poll and no command | pass |
| Restart while paused: pause kept, watermarks kept, VM kept, nothing ran | pass |
| Resume: one poll ran, window continued from the watermark, no new baseline | pass |
| Journal clean, executor guest identity kept | pass |
| Cleanup: no survivor, base unchanged, disk kept | pass |

Poll durations were 18 s to 70 s for three accounts. No page cap or time cap was hit.

## The earlier runs

1. Run 1: account B scanned `error` in the first poll. The code kept no reason. Run stopped by the gate.
2. Production seam fix 1: a failed scan now writes its fixed reason code and account key into the poll task detail. It never holds page text. A failing test came first.
3. Runs 2 and 3: stopped by the preflight and by a pause that the harness itself had left on the continued database. Nothing ran. The harness now releases only its own pause.
4. Run 4: the first poll was healthy. The next two polls failed on all accounts with `grid_count` and `query_box`.
5. A read-only numeric diagnostic showed the cause. On a repeat visit of a window URL, the search box value stayed wrong for 20 s. A wait did not help. One navigation through the mailbox root fixed it.
6. Production seam fix 2: `goto_read` reads again once after a root navigation when the reason is `query_box` or `grid_count`. A second failure is still a failure. Two tests came first.
7. Run 5: all gates passed.

The run did not show how often the retry fired.

## Not tested

- An incoming message. No mail was sent. The worker saw 0 events, so no arrival, no event queue and no triage path ran live.
- A scan with more than one page, a page or time cap hit, an outage catch-up and an old-dated message.
- A run longer than four polls. The idle save was off, so its interaction with polling is not tested.
- A guarantee of detection within two minutes. One gap pair of 120 s shows the schedule, not detection time.
- A phone verification challenge. None occurred, and the worker never signs in.
- Issue 550 (`computed-nodes`) did not occur in this run. It is not fixed. No budget or restart rule changed.

## State left behind

- The VM is stopped. The disk, the saved profile and the host database are kept.
- Automation is paused in the host database, because the harness holds it before stopping Deck. A later run must release it.
- Raw logs and the manifest are private.

## Activation (2026-11-15)

Mode: poll-only. No model is configured. New events stay unprocessed in the host database. See D76.

- Unit `deck-gmail-poller.service` (user, enabled, linger on). Config and status are private under `~/.config/deck-gmail` and `~/.local/share/deck-gmail/state`. `gmail_service.py status` and `verify` read them.
- It ran the retained VM and the retained host database (executor epoch 5, then 6 after the service start, then 7 after a clean restart). The epoch was never reset.
- The pause that the earlier harness left was released once. A clean restart paused and released only its own pause.
- First observed run: three complete cycles, all accounts `ok`, journal settled (every command `succeeded`), one VM, no model task, no model call, no hold. `verify` returned `ok`.
- The first cycle stored 7 events. These are thread revisions that arrived while nothing ran. Their arrival time is not known. They are retained and untriaged. This is not proof of live detection time.
- Failures: about one scan in six failed with `grid_count` after the root-navigation retry. Accounts A and B alternate. Each recovered in the next cycle. The cause is filed as bare issue 564. No workaround was added.
- Alert path: `tg-notify` was only dry-run checked. No alert fired.

Not tested: a naturally arriving message with a measured detection time, the stale and challenge alerts live, a reboot of the host, a service stop while a poll runs, and idle save.

## Summary activation (2026-11-15)

Mode: summary, labels off. Same isolated service, same VM, same host database (epoch kept, never reset). The retained seven events became triage rows when the summary was enabled. The old poll-only snapshot and config are kept in a private rollback directory.

- Model: `claude-haiku-5-5` through the existing local proxy. No tools. The key comes from the vault at start.
- First live run: three model calls, seven threads triaged, one combined message delivered. The next polls posted nothing.
- Labels: a write shows its chip and is not saved (bare issue 568). Deck now reloads before it counts a label. Every write ended `uncertain`, so labels are off. Three label states in the first run were wrongly `applied` and were reset to `none` in the database.
- Account A and B scans fail now and then with `grid_count` (issue 564). They recover on the next cycle.

Not tested: the 08:00 run of a second day, a label that persists, the budget ceiling in a summary run.

