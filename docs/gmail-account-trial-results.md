# Gmail account trial results

## Status (2026-11-14): live attempt 2 is blocked. Account A password and phone approval worked. Identity proof needs a helper change

See "Live attempt 3" at the end for the later result. Both accounts were proven there. See "Live attempt 2" for the blocker. The sections below are the earlier history. They are kept unchanged.

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

## Live attempt 3 (2026-11-14): both accounts proven, switched and restored in one session

**Authorization.** The owner kept the load limit off (`--max-load inf`) and used an own lock file. The memory guard (6144 MiB) stayed on. The owner is always ready for phone prompts. The run never asked first.

**Source.** Parent `gmail-account-trial` `d8b9c1d`, imported with `git merge --ff-only`. Browser candidate SHA-256 `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b` from source `6e2cbb73`. Frozen base `a90de2e085a7ad52`, digest equal before and after.

**Offline checks.** `python3 -m unittest discover -s scripts/vm-prototype -p 'test_gmail_accounts_e2e.py' -v` exits 0. 80 tests pass and none are skipped. Each change had a failing test first.

**Harness changes.**
1. Identity runs inside the page. `bare` shows a delivered address as `[secret]`, so a label cannot be read. The helper sends one `expect js` step per account. The step holds `${GMAIL_A_USER}` or `${GMAIL_B_USER}`, which `bare` expands in the guest. The page must be https `mail.google.com` with exactly one control of the selector. The label of that control must hold a complete email token equal to the address, ignoring case. The helper reads only the pass or fail receipt. It never prints a raw result or error, and it never reads the vault for this. Neither pass is `unknown`. Both pass is `ambiguous`. Tests run the check in `node` against a stub page for token boundaries, control count, host and scheme.
2. New command `resume`. It starts the retained overlay with explicit internet and its own lock. It refuses a disk that is not the recorded one before it starts anything. It never creates an overlay, installs the executor, delivers a secret or reads the vault. It registers the new PID, requires readiness, restores the saved main profile from its file and verifies A. A failure after the start cleans up the processes and keeps the disk. The original `manifest-up.json` stays unchanged. Evidence goes to `private/manifest-resume.json`.
3. New commands `add-account` (one fixed `goto` of Google's AddSession page in the existing session) and `restore` (one explicit `open` with `state=main`). Both hide the raw result. The generic `call` stays blocked on the mailbox.
4. Live correction 1 of 2. A failed `expect js` in a compact `step` returns no error flag. Only the text differs (`ok (N ms)` against `FAILED expect: ...`). The first live identity run therefore reported both accounts as a match (`ambiguous`). The helper now accepts only the exact success receipt. This was the only correction found during the live run.

**Live gates.**

| Gate | Result |
| --- | --- |
| `resume`: network, readiness, bound guest, candidate hash, private secret file and state file | Pass. No credential was read or delivered. |
| Restore of the saved main profile from its file | Pass. Account A needed no sign-in. |
| First `identity` after the receipt fix | `A`. The B check failed as it should. |
| `add-account` | The first navigation left the session with no document. The guest could resolve and fetch at once afterwards, so the cause was probably a transient resolver failure (hypothesis). The second navigation reached Google's sign-in page. No `bare` issue was filed. |
| Account B sign-in | One address submission and one password submission, both by placeholder. Google then sent a device prompt at once. The helper sent a phone alert without asking first. The owner approved it. The mailbox opened at `/mail/u/1/`. |
| `identity` for B | `B`, three runs. |
| Switch A, B, A in the same session | Three of three verified. |
| `save_state` | The state file grew from 12,440 to 15,894 bytes, mode 0600. `call` withholds the save result, so this size is the evidence. |
| Restart of the owned MCP | A new process (`new_process: true`). |
| Restore from the file, no fresh credentials | `restored: true`, identity `A`. |
| Switch B, A, B after the restore | Three of three verified. |
| `down --keep-disk` | No survivors. Base unchanged. Disk kept. |

**Recorded evidence.** `private/manifest-resume.json` holds the first run of `resume`. It still shows `ambiguous` and a false A gate. That run used the helper before correction 1. It is kept unchanged. The gates above came from separate commands after the fix. A later `resume` run has not repeated them. `private/manifest-down.json` was overwritten by the final `down`.

**Pending owner action.** None.

**Private state.** The retained disk, the pinned candidate, the manifests and raw logs stay under a private directory in the local temporary area. Neither is in Git. The authenticated disk holds both accounts in `main.state`.

**Not tested.** Message bodies, thread reads and every mailbox action were out of scope and were not touched. An expired session, a second restart, a VM wake after a save and a clean second `resume` were not run. The identity source is Google's own account control. It is not an absolute guard against a page that injects its own label. The guest was stopped with a kill, as in attempt 2. The disk survived it. A morning summary and labels are not implemented.



## Live attempt 4 (2026-11-14): account C added, three accounts verified, switched and restored

**Authorization.** The owner asked for a third account in the same profile. The load limit stayed off (`--max-load inf`), with an own lock file. The memory guard (6144 MiB) stayed on. No mailbox content was read and no mailbox action ran. No performance claim is made under load.

**Source.** Local commit `cb5a79b` imported with `git merge --ff-only`. Browser candidate SHA-256 `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`. Frozen base `a90de2e085a7ad52`, digest equal before and after.

**Offline checks.** The helper suite has 100 tests. It exits 0. Each change had a failing test first.

**Harness changes.**
1. `register-c` delivers only the two secrets of C (`GMAIL_C_USER`, `GMAIL_C_PASS`, request IDs `gmail-trial-c-*`). The guest script rewrites `secrets.env` with the A and B lines kept. A test runs the real guest script and checks this. `register-c` checks the receipts of A and B before any delivery and of all three after it. A receipt compares a digest in the guest. Nothing replays A or B, and no value leaves the guest. It records C only after every check passes, in a separate private file. The first trial record stays unchanged.
2. A running browser session reads `secrets.env` only when it opens. The flow is `save_state`, `register-c`, then `restore`. The restore is an explicit open of the saved profile.
3. A vault user without a domain is a Gmail user name. `load_creds` adds the domain. The requested address must equal the result, or `register-c` stops before delivery.
4. `identity` and `switch` take the registered accounts. Exactly one may pass. Two or more passes give `ambiguous`. `switch --to C` needs a registered C. A and B behave as before. `resume` still checks A and B only.

**Credential.** An existing vault item for the account held a password. No new secret was requested. Its address and ID are not recorded here.

**Live gates.**

| Gate | Result |
| --- | --- |
| `resume` of the retained overlay | Pass. A restored from its file with no sign-in. |
| A and B retained | `identity` A. `switch` to B then A passed. |
| `save_state`, then `register-c` | Registered. All three receipts intact. |
| `restore` | Account A verified. The new secrets loaded. |
| `add-account`, then protected sign-in of C | One address and one password submission, both by placeholder. Google showed no challenge. No phone approval was needed. |
| `identity` | `C`. |
| Switch A, B, C, A | First run: B gave `unknown`, then passed on a retry. The cause is not known (hypothesis: the header had not rendered yet). A second full run passed four of four. |
| `save_state` | The state file grew from 15,894 to 19,580 bytes, mode 0600. |
| Restart of the owned MCP | New process. |
| `restore` from the file | `restored: true`. |
| Switch A, B, C, A, C after the restore | Five of five. |
| `down --keep-disk` | No survivors. Base unchanged. Disk kept. |

**Evidence.** Per-operation outputs are in a separate private directory. The earlier `manifest-resume.json` was copied before the run. The final `manifest-down.json` of this run was copied to a separate file. Both stay unchanged otherwise.

**Not tested.** Message bodies and mailbox actions. A second restart after C. An expired session or a Google challenge for C. A clean second `resume` with three accounts. The index is not proof: index 3 fell back to A in one check. The identity source is Google's own account control.
