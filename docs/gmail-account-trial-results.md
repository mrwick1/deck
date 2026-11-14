# Gmail account trial results

## Status (2026-11-14): not run. The helper is fixed after review and is not yet used live

The real Gmail trial did not run. No VM started. No vault item was read. No Google page was opened. The earlier run stopped before boot on host load. A parent review then rejected the first helper for live credential handling. The fixes are below.

## Candidate

- Parent branch import: `c5fe2a5`. `git merge --ff-only` was impossible because `main` has four later documentation commits. A normal merge had one documentation conflict in `docs/decisions.md`. Both sides were kept.
- Guest internet: PR 25 commit `2db261a` was cherry-picked into this local candidate only. Production files merged without conflict. Its decision is renumbered D70, because D56 was already used. Nothing was pushed or merged.
- Browser: source `6e2cbb73`, SHA-256 `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`.

## Offline checks

- The two PR 25 network and save-state regressions pass (`test_vmproto` unittest, exit 0).
- The helper has 51 offline tests (`test_gmail_accounts_e2e.py`). They use fakes for the VM, the guest and the vault. The 51 tests pass, exit 0.
- Rust: the parent ran `cargo test --offline -j1 internet_option_precedes_the_lifecycle_subcommand -- --nocapture`. It exited 0 and exactly that one Rust test passed. The other Rust `vm` tests were not run.

## Helper

`scripts/vm-prototype/gmail_accounts_e2e.py` has the stages `up`, `call`, `identity`, `switch`, `restart-mcp` and `down`.

**Credentials.** The two vault item IDs are options of `up` (`--item-a`, `--item-b`). The helper has no default IDs. It stores the IDs, which are not secrets, in the private output directory. `rbw` runs with a 15 second timeout. The helper checks type, length and line breaks of each field and requires two distinct accounts. It reads both before the VM boots. It delivers them on guest stdin through `secretchan` as `GMAIL_A_USER`, `GMAIL_A_PASS`, `GMAIL_B_USER` and `GMAIL_B_PASS`. Failures print stable text. They never print an exception message.

**Fail closed in `up`.** The base freeze check, the internet record, the candidate hash and every delivery receipt are gates. A false gate stops the run. Failures after the output directory exists, in pinning and in the VM layer, stop with a controlled report and clean up what the run started. The test for the unfrozen base asserts zero boot calls.

**Ownership.** `up` holds the shared host lock only while it boots. The guest then outlives it. The output directory owns the trial. Each later stage takes a per-trial lock (`private/op.lock`, exit 4 when busy). Each stage then proves ownership before any guest command. The recorded PID must have its recorded birth time (`setup_e2e.process_record`), must not be a zombie, and must be a QEMU of the trial runtime directory (`vmproto.proc_ok`). Each guest call has its own random output directory, so a reply of an earlier command cannot be read.

**Mailbox boundary.** `call` probes the page host before and after the tool. It prints a result only when both probes name another host. A mailbox host, a failed probe or a transition gives `WITHHELD` with no page text and no error text. While the page is in the mailbox or unknown, `call` runs only `open`, `save_state` and `close`.

**Identity and switch.** `identity` reads only the `aria-label` of the control that `--account-control-selector` names. It reads it only on `mail.google.com` over https. A title, a path and an index are never evidence. It prints one of A, B, other, ambiguous, blocked or unknown, and a host class. `switch --to X --index N` opens `/mail/u/N/`, hides the raw result, and succeeds only when the verified identity is X.

## Missing live step

The account-switcher selector is not known. `up` takes it as `--account-control-selector`, and without it `identity` and `switch` return blocked. To find it, a person must inspect the account control in a signed-in Gmail session on the owner's screen. Nothing may print mailbox text to discover it. The secret flow that fills the sign-in form is also untested. `call` withholds all content from an unknown page, so the sign-in page is not visible through it either.

## Blocker

`up` stopped with exit 3: load average 40.12, limit 8. The cause of the load was not established. The trial keeps the owner's limits (load at most 8, memory at least 6144 MiB), and it does not poll.

## Not tested

Guest internet in the live VM, secret delivery, Google sign-in, the account-switcher selector, account switching, saved state and restore. The redaction is best effort and is not proven.
