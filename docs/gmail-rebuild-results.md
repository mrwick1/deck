# Gmail computer rebuild (2026-11-15)

Issue: [#35](https://github.com/mrwick1/deck/issues/35). It follows [gmail-missing-base-results.md](gmail-missing-base-results.md). This record holds no private content.

## Result

The Gmail bot runs on a new computer with a new base. The old base `a90de2e085a7ad52` is still lost. The old overlay, its saved state and its database copy stay offline under `/data/tmp/gt2` and in the recovery directory.

## What changed

- New base `832a2dbd2dad4840`. Built by `vmproto.py setup` at the Deck commit of this branch, from the Arch cloud image, with the pinned browser (SHA-256 `d9435503…0df831b`). Frozen read-only. `verify-base` passes. Provenance is in a private file next to it. It lives under `~/.local/share/deck-gmail/bases`. It is not the old base under a new name.
- New overlay and runtime under `~/.local/share/deck-gmail/trial`. The host database moved to `~/.local/share/deck-gmail/data`. The move used the SQLite backup API with the service stopped. Integrity, the full dump hash and every table count matched. The original stays in place.
- The service config points at these paths and sets `persistent_vm`. The live QEMU scope has `RuntimeMaxUSec=infinity`. The first scope had 3 hours, because the helper started it without the opt-in. The service stop and start fixed that. This also exercised the stop and resume route.
- A rollback copy of the old config, unit and app is in `~/.local/share/deck-gmail/rollback/pre-rebuild-*`.

## Fencing and identity

The host database keeps its pin (guest id) and its epoch. `install_executor` now takes the pinned guest id and the host epoch (`--rebind-guest-id`, `--rebind-epoch` in `gmail_accounts_e2e.py up`). The fresh guest starts with that id and `max_epoch` 17. The host epoch only rises: the next connection used 18. No old guest could reply, because no VM of the old overlay existed (checked by process list and by the missing base). A guest with another id is still refused by `pin_guest`. No SQL edit and no journal edit ran. See D82.

## Defect found and fixed

`install_executor` enabled the executor service before it wrote the identity file. A udev trigger had already started the service, and the executor reads the identity only at start. Deck got `unprovisioned` and every poll ended `wake_failed`. The install now restarts the service after it writes the identity. A test failed first. The live guest got the same restart once.

## Sign-in

One shared browser session. A and B signed in through placeholders and protected secrets. A needed one phone approval, sent as an urgent notification at once. Google also offered a selfie enrollment. It was declined. B and C needed no challenge. Identity of A, B and C was verified in the page. Switches A, B, C, A, C passed after a save, an MCP restart and a restore. No mailbox content was read.

## Live gates

`gmail_service.py verify` passes every gate: unit active, three complete cycles, all accounts fresh, journal settled, mode and event gates, one executor identity, one VM, not paused, no holds. Triage, seen rows, the summary day, chat and messages kept their history. Some counts rose because new mail arrived. No event task is queued or running. No model call ran for a general task. The UI answers on the tailnet bind. The public route redirects to the login as before.

## Not done or open

- The next 08:00 scheduled summary has not run on this computer.
- A restart of the whole host and the reaper timer were not tested. `reap-data-tmp` is unchanged. Nothing durable lives under `/data/tmp` now, except the preserved old files.
- No off-volume backup of the new base exists. A rebuild would not match its digest.
- The guest-side `grid_count` result for Gmail is intermittent. It was not investigated here.
- A full browser login by the owner through Authelia was not run.
