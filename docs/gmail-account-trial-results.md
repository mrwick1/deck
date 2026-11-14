# Gmail account trial results

## Status (2026-11-14): live attempt 2 is blocked. Account A password and phone approval worked. Identity proof needs a helper change

See "Live attempt 2" at the end. The sections below are the earlier history. They are kept unchanged.

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


## Parent review

The parent reproduced all 51 helper tests with exit 0. Review found that account switching used `open`, which replaces the live browser session. A regression failed before the correction. Switching now uses `step goto` within the existing session and requires a successful call plus the expected active-account identity. All 51 tests pass after the correction. This remains offline helper evidence. No real Gmail account has been accessed.


## Live attempt 2 (2026-11-14): load limit deactivated, real Google reached

**Authorization.** The owner said "We need to deactivate the load limit". The run used `--max-load inf`. That option disables only the load check. The memory guard (6144 MiB) stayed on. The owner also authorized a separate lock. The run used its own lock file. No performance claim is made. The host was under load during the run.

**Source.** Parent `gmail-account-trial` `c16e119`, imported with `git merge --ff-only`. Browser candidate SHA-256 `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`. Frozen base `a90de2e085a7ad52`, mode 0444. Its digest was equal before and after.

**Command.** `gmail_accounts_e2e.py --out <private> --base <base> --lock <own lock> --max-load inf up --bare-candidate <candidate> --expect-bare-sha256 <hash> --item-a <id> --item-b <id> --account-control-selector <hypothesis>`. Item IDs and addresses are not recorded here.

| Step | Result |
| --- | --- |
| `up`, first run | Exit 1. A `RuntimeError` in the VM layer hid its message. Cleanup passed. No secret was delivered. The cause is not known. A second run did not repeat it. |
| `up`, second run | Exit 0. Four gates passed: base frozen, internet in VM state, candidate installed, four protected secrets delivered. |
| Sign-in page | Reached through `open`. Protected fill of A's address, then A's password, one submission each. |
| First phone challenge | Google offered 2-Step Verification. The owner was asked and agreed. The challenge session had expired by the time the click ran (`sessionexpired`). This was an expiry, not a password error. |
| Second sign-in of A | One more password submission, because of the expiry. The page then left the sign-in flow and reached `mail.google.com` over https. The helper withheld the page content. |
| `controls` (new) | The mailbox rendered (`ready` complete). The header has one `[aria-label*="Google Account"]` control, one `[aria-label*="@"]` control and one `a[href*="SignOutOptions"]` control. Counts only. |
| `identity` | `unknown` for every selector tried. The control exists once, but `bare` replaces the delivered address with `[secret]` in its own output. The helper cannot read the address from the label. |
| `save_state` | Exit 0 (result withheld). The guest file `main.state` exists, 12,440 bytes, mode 0600. |
| `down --keep-disk` | Exit 0. No survivors. Base unchanged. Disk kept. |

**Result.** Account A: password accepted, Google 2-Step Verification reached, mailbox reached over https with a header account control present once. Account A identity is **not proven** by the helper. Account B was not tried. Add-account, switching, restart and restore were not run. This is not a Gmail success.

**Helper corrections (2 of 2 used, each with a failing test first).**
1. `up` now shows the sanitized message of a `RuntimeError` that happens before any secret is delivered. Test: `test_pre_delivery_runtime_error_shows_a_sanitized_reason`.
2. New command `controls`. It runs a fixed probe and prints host class, ready state, a path flag and a whitelist of integer counts. It never prints text. Tests: two in `test_gmail_accounts_e2e.py`. 54 tests pass.

**Blocker and proposed third correction.** `bare` hides any delivered secret in its output, so a label read gives `[secret]`. The fix must be a boolean oracle inside the page check. For example, a fixed `step` command `expect js "document.querySelector(SELECTOR).getAttribute('aria-label').includes('${GMAIL_A_USER}')"` (and the same for B). `bare` expands `${NAME}` inside the quoted js text (`crates/drive/src/steps.rs`, `expand`). It returns only pass or fail. The helper blocks `step` on the mailbox, so this needs a new fixed command. It was beyond the two allowed corrections. It also needs a narrow `AddSession` navigation. Alternatively, add B with `open` and `state=main`, which is an explicit restore.

**Continuation.** The kept private disk holds the signed-in state of A (`main.state`). `up` has no resume. A parent decision is needed on resume versus a new `up`.

**Private artifacts.** Raw output and logs stay outside Git, in two short private directories and one log directory under the local temporary area. The first directory (failed `up`) holds a pinned candidate copy only. Owned processes: none left.

**Not tested.** Account B sign-in, add-account, active-identity proof for A or B, switching, saved-profile restart and restore, any performance. The redaction filter is best effort.
