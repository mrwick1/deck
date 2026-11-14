# Setup secret and challenge test results

Date: 2026-11-13.
Head under test: `da249a4`.
Suite: `scripts/vm-prototype/test_setup_secrets_e2e.py`.

## What this suite is

It is a synthetic acceptance test. It starts the real `deck` binary on a loopback port. It sends real HTTP requests. It uses a private SQLite file, a scripted local model and a fake guest harness. The fake guest is a fault injector. It is not the production guest channel. No VM, no browser and no real account is involved. This is not an end-to-end pass.

## Run

```
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=<private dir> cargo build
cd scripts/vm-prototype
DECK_BIN=<private dir>/debug/deck python3 -m unittest test_setup_secrets_e2e
```

## Result at `da249a4`

30 tests ran. 22 passed. 7 failed. 1 was skipped on purpose.

| Group | Pass | Fail | Skip |
| --- | --- | --- | --- |
| Harness (guest contract, legacy wait route) | 4 | 0 | 1 |
| Protected secret | 8 | 6 | 0 |
| Holds | 1 | 1 | 0 |
| Challenges | 9 | 0 | 0 |

The skip is the screenshot gate. Grepping PNG bytes proves nothing. It stays skipped until bare gives rendered or OCR evidence.

## The failures have one cause

The host treats a missing guest receipt as a failed delivery. It answers `502` and never calls `deliver-secret`. The guest helper `secretchan.py` answers a missing receipt with exit code 1 and `{"ok":false,"error":"not_delivered"}`. `HarnessChannel::run` in `src/bot/secrets.rs` turns any non-zero exit into `Failure::Failed`. Its `receipt` method only handles `ok:false` on exit 0.

Evidence:

- `test_a_missing_receipt_probe_falls_through_to_delivery` shows the guest journal holds `secret-receipt` and nothing else, and the status is 502.
- With a temporary fixture that answers a missing receipt with exit 0 and `{"ok":false}`, every test except the fixture's own contract test passed. That run was not kept.

Failing tests:

- `test_delivery_completes_the_wait_once_and_a_duplicate_is_refused`
- `test_failed_delivery_keeps_the_wait_and_allows_a_retry`
- `test_restart_while_waiting_keeps_the_same_request`
- `test_crash_after_delivery_before_the_receipt_reaches_the_host`
- `test_a_missing_receipt_probe_falls_through_to_delivery`
- `test_a_receipt_voided_by_an_overwrite_is_delivered_again`
- `test_pause_budget_and_unknown_outcome_holds_stay_in_force`

No safety assertion was removed to make a test pass.

## What passes

- Authentication: no user gives 401. A wrong user gives 403. Nothing is delivered.
- Cross-bot, cross-request and stale-version posts fail without delivery.
- The label comes from the pending request, not from the post.
- Failed and partial deliveries keep the wait open.
- An expired credential request gives 409 and delivers nothing.
- Unreadable bodies and a value in the URL are refused.
- Challenges: owner answers need the current version and request. A duplicate, a cross-bot answer and a wrong user fail. An expired request gives 409 and a fresh request that survives a restart. The old identity stays dead.
- A task sign-in wait (`Step::Login`) refuses the legacy `complete_wait` control. An owner answer sets `answered` and does not verify. The answer survives its original expiry and a restart.
- A setup-step phone wait completes as `owner_completed`, never `verified`.
- A credential wait refuses a plain completion.
- The task secret route needs authentication and an open request.
- Leak scan: 0 sentinel matches on every surface that ran. Surfaces: model requests, HTTP responses, deck log, deck process arguments and environment, database, WAL, shared memory file, every file under the data and runtime directories except the guest secret file. Encodings: plain, base64, URL-safe base64, hex and SHA-256 hex. The scan prints counts and surface labels only.

## Fixture changes

- A missing receipt is now exit 1 `{"ok":false,"error":"not_delivered"}`. A bot mismatch is `bot_mismatch`. A replay with another value is `request_mismatch`.
- Receipts keep a salted digest on the guest. A receipt is valid only while the stored value still matches. An overwrite by another request voids it.
- The env store keeps one line per name.

## Assertions corrected

- `bot_state` has `automation_paused` and `budget_hold`, not `paused`.
- The unknown-outcome test compared the `start` call count with zero. The driver legitimately starts the guest before the wait. It now compares before and after the post.
- Expiry now expects 409. Cancel now expects 204.

## Not tested

- Task secret delivery and resume after a crash. The route needs a ready bot and a model `request_secret` call. A setup stack has neither. Only the authentication and unknown-request guards run.
- Real QGA, `runuser`, file modes on a real guest and `secretchan.py` itself. The fake guest only copies its answer contract.
- The task side of sign-in verification (`verify_login_help`) and the Tools adapter.
- Concurrent posts of one request.
- Screenshots and the privacy gate in the browser. Skipped.
- The seeded phone, sign-in and expiry snapshots bypass the driver. Only the answer path uses the real endpoint.

## Parent fixture correction

The results above describe the child's run at `da249a4`. Parent review found that the fake guest restored an overwritten value on replay of a completed request. The production guest refuses that replay. A new fixture-only regression failed before the correction and passes after it. The HTTP overwrite case now requires rejection, an open wait and preservation of the newer value. This adds one test; the combined HTTP suite has not yet been rerun with the pending host fix.

## Combined host rerun

After integrating `7ced5ae` and aligning the API fixture with the real absent-receipt exit code, the parent ran the suite against the built Deck binary. All 31 tests ran: 30 pass and the screenshot gate remains skipped. The corrected overwrite case refuses a completed-identity replay and preserves the newer value. The Rust run passes 288 unit tests and four integration tests; two live tests remain ignored. This is HTTP/SQLite acceptance with a fake guest, not real VM or browser evidence.
