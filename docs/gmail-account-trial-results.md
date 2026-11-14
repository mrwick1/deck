# Gmail account trial results

## Status (2026-11-14): blocked before boot by host load

The real Gmail trial did not run. No VM started. No vault item was read. No Google page was opened.

## Candidate

- Parent branch import: `c5fe2a5`. `git merge --ff-only` was impossible because `main` has four later documentation commits. A normal merge had one documentation conflict in `docs/decisions.md`. Both sides were kept.
- Guest internet: PR 25 commit `2db261a` was cherry-picked into this local candidate only. Production files merged without conflict. Its decision is renumbered D70, because D56 was already used. Nothing was pushed or merged.
- Browser: source `6e2cbb73`, SHA-256 `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`.

## Offline checks

- The two PR 25 network and save-state regressions pass (`test_vmproto` unittest, exit 0).
- The 11 new helper tests pass. The whole prototype Python suite passes: 496 tests, 8 skipped, exit 0.
- The Rust `vm` tests were not confirmed in this run.

## Helper

`scripts/vm-prototype/gmail_accounts_e2e.py` has the stages `up`, `call`, `identity`, `restart-mcp` and `down`. It reads both vault accounts into process memory. It delivers them on guest stdin through `secretchan` as `GMAIL_A_USER`, `GMAIL_A_PASS`, `GMAIL_B_USER` and `GMAIL_B_PASS`. It prints no mailbox page text. It maps the two addresses to A and B.

## Blocker

`up` stopped with exit 3: load average 40.12, limit 8. Other projects' builds caused the load. The trial keeps the owner's limits (load at most 8, memory at least 6144 MiB), and it does not poll.

## Not tested

Guest internet in the live VM, secret delivery, Google sign-in, account switching, saved state and restore.
