# Setup runtime acceptance

Date: 2026-11-13.
Base: `c9153e2`.
Tests: `src/bot/mod.rs`, module `tests`, names that end in the cases below.

## What these tests exercise

Each test builds a real `Bot` with the real Python executor, the real hub, the real `Store` and the production targeted sender `secret_requests_api::sender`. The tests call `resume_delivered`, `reconcile` and `resume_challenge` with that sender. They do not use the earlier fake wake closure.

## Limits

- The model is scripted. No paid model runs.
- The computer is the fake VM. No real guest, QGA or browser runs.
- The host receipt is a `Store` row from `record_secret`. No value exists in these tests. No guest delivery runs.
- No HTTP route runs. The route code that calls the sender is not covered here.
- The phone test seeds an approved, ready setup row for the bot directly in SQLite. The approval and the steps are fixtures. The seed does not prove that setup provisioning works.
- A passing phone test does not prove a service login. The task "verifies" a fixture observation that the script supplies.

## Cases

| Test | Result checked |
| --- | --- |
| `the_real_sender_resumes_the_waiting_task_once_after_a_delivered_secret` | The task waits after `request_secret`. After the receipt and one wake, the same task finishes. Model calls: 3. Tasks: 1. Wake messages: 1. Shell effect: 1. Repeat wake, reconcile and a late repeat add nothing. |
| `an_automation_pause_holds_…`, `a_budget_hold_holds_…`, `a_ceiling_hold_holds_…` | With the hold set, the wait stays. The wake is stored once. The second wake reports `Already`. No model call runs (1 call, 0 effects). After release the task runs once (3 calls, 1 effect, 1 task, 1 message). |
| `a_durable_answer_survives_a_restart_and_runs_the_same_task_once` | The answer is durable, the host restarts while paused, and a reconcile repeats. After release the same task finishes once (3 calls, 1 effect, 1 message). |
| `an_answer_after_the_task_was_replaced_creates_and_steers_nothing` | A cancelled task is replaced by a waiting task. `resume_delivered` returns `NoTask`. A direct sender call with the old id fails. No message reaches the new task. Model calls stay at 2. Tasks: 2. |
| `a_phone_answer_wakes_the_task_which_checks_status_and_reports_verification` | `request_owner_help` waits with `main:1`. The owner answer is saved. Two wakes give `Sent`, then `Already`. After the pause lifts, the model calls `owner_help_status` and `report_help_verification`. Model calls: 4. Tasks: 1. The setup wait is closed. |

## Mutation evidence

I changed production temporarily and reverted it.

- Removing the exact-task check in the sender fails the replaced-task test at `send(old_task, "late")`. A first version of this test passed under the same change. The `wake` pre-check hid the sender, so the test now keeps the replacement task waiting.
- Removing the `resume` mark in the sender fails five tests: the three holds, the secret test and the phone test. The restart test passes, because `Bot::start` restores the mark from the chat.

## Open seams

1. `resume_delivered` trusts the host receipt. Its signature has no guest channel. `ensure_delivered` rechecks the guest receipt. A repeated post for a `delivered` request therefore wakes the task even when the guest value is gone or changed. No normal test can express this without a production signature change, so none is added. Expected result after the fix: with a host receipt and no guest receipt, a repeated post must refuse and send no wake. The route test in `setup_api.rs` needs a registered runtime and a fake guest. The parent owns that fix.
2. The screenshot guard (`Tools::screen_refused`) covers `browser_screenshot` and the `shot` or `screenshot` lines of `browser_step`. It does not cover other tools that can read the page: DOM reads, `eval` and `shell`. Those tools can return typed text. Do not claim universal screenshot or value protection.
3. The sender's transactional dedup is not tested apart from `wake`'s marker check. A race of two simultaneous wakes is not covered.
