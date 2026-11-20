//! Durable state of the Gmail browser poller: per-account baseline and success metadata,
//! observed conversation revisions and the triage events they queue.
//!
//! The caller supplies only completed, identity-verified window scans. The store cannot check the page.
//! The watermark is the START time of the last successful scan. A completion time is never a watermark,
//! so mail that arrives while a scan runs stays inside the next window.
//! A changed latest-message ID signals a changed conversation. It does not prove a new
//! incoming message. A seen revision is not a processed one: events stay durable and unbatched
//! until the batcher turns them into a task.

use std::collections::BTreeMap;

use anyhow::bail;
use rusqlite::{OptionalExtension, params};

use super::db::{TASK_COLS, Task, task_row};
use crate::store::{Store, now};

pub const GMAIL_SOURCE: &str = "gmail_poll";
pub const GMAIL_KIND: &str = "inbox_changed";
/// Background priority. Equals `db::LOW`, which exists only in test builds.
pub(super) const GMAIL_PRIORITY: i64 = 0;

/// Why a scan did not complete. Each value maps to one fixed result code.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GmailScanFailure {
    Incomplete,
    Error,
    Auth,
    IdentityMismatch,
}

impl GmailScanFailure {
    pub(crate) fn code(self) -> &'static str {
        match self {
            Self::Incomplete => "incomplete",
            Self::Error => "error",
            Self::Auth => "auth",
            Self::IdentityMismatch => "identity_mismatch",
        }
    }
}

/// One conversation of a complete scan: its thread ID and its latest non-draft message ID.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GmailThreadRev {
    pub thread_id: String,
    pub message_id: String,
}

/// What a scan queues for each unseen revision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScanMode {
    /// A `bot_events` row after the silent baseline. The default worker.
    Events,
    /// A pending triage row, also in the baseline, and no event. The summary owns the revision.
    Triage,
}

/// The date window of one scan: when it started and the lower bound of its search, in epoch seconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GmailWindow {
    pub start_ms: i64,
    pub lower_s: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GmailScanOutcome {
    /// True when this scan seeded the silent baseline.
    pub baseline_seeded: bool,
    /// IDs of the `bot_events` rows this scan queued, in insertion order.
    pub new_event_ids: Vec<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GmailAccountStatus {
    pub baseline_done: bool,
    pub last_attempt_ms: Option<i64>,
    pub last_success_ms: Option<i64>,
    /// One of ok, incomplete, error, auth, identity_mismatch.
    pub last_result: Option<String>,
    /// Start time of the last successful window scan. `None` until a scan succeeds.
    pub watermark_ms: Option<i64>,
    /// Lower search bound of that scan, in epoch seconds.
    pub window_lower_s: Option<i64>,
    /// What the last successful scan covered. Always `date_window`: never the whole inbox.
    pub scope: Option<String>,
}

fn valid_account(a: &str) -> bool {
    (1..=64).contains(&a.len())
        && a.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// 16 lowercase ASCII hex digits. Uppercase is rejected, never normalized.
pub(super) fn valid_id(id: &str) -> bool {
    id.len() == 16 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn check_common(bot: &str, account: &str, at_ms: i64) -> anyhow::Result<()> {
    if bot.is_empty() {
        bail!("empty bot id");
    }
    if !valid_account(account) {
        bail!("invalid account key");
    }
    if at_ms <= 0 {
        bail!("invalid timestamp");
    }
    Ok(())
}

impl Store {
    /// Record a completed, identity-verified window scan. An empty list is a verified empty window.
    /// An account without a watermark seeds a silent baseline and queues nothing. Later scans
    /// queue one event per unseen (thread, message) pair. A scan that starts before the watermark
    /// is refused, so the watermark never moves back. All writes share one transaction.
    #[cfg(test)]
    pub async fn record_gmail_window_scan(
        &self,
        bot: &str,
        account: &str,
        threads: Vec<GmailThreadRev>,
        window: GmailWindow,
        done_ms: i64,
    ) -> anyhow::Result<GmailScanOutcome> {
        self.record_gmail_window_scan_as(bot, account, threads, window, done_ms, ScanMode::Events)
            .await
    }

    /// Like `record_gmail_window_scan`, with the queue that the mode names. The seen row and its
    /// triage row share one transaction, so a revision never counts as seen without a queued triage.
    pub async fn record_gmail_window_scan_as(
        &self,
        bot: &str,
        account: &str,
        threads: Vec<GmailThreadRev>,
        window: GmailWindow,
        done_ms: i64,
        mode: ScanMode,
    ) -> anyhow::Result<GmailScanOutcome> {
        check_common(bot, account, done_ms)?;
        if window.start_ms <= 0 || window.lower_s <= 0 {
            bail!("invalid window");
        }
        let mut unique: BTreeMap<String, String> = BTreeMap::new();
        for t in threads {
            if !valid_id(&t.thread_id) || !valid_id(&t.message_id) {
                bail!("invalid thread or message id");
            }
            if let Some(old) = unique.insert(t.thread_id.clone(), t.message_id.clone())
                && old != t.message_id
            {
                bail!("contradictory duplicate thread");
            }
        }
        let (bot, account) = (bot.to_owned(), account.to_owned());
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            let mark: Option<i64> = tx
                .query_row(
                    "SELECT watermark_ms FROM gmail_accounts WHERE bot_id = ?1 AND account = ?2",
                    params![bot, account],
                    |r| r.get(0),
                )
                .optional()?
                .flatten();
            if mark.is_some_and(|m| window.start_ms < m) {
                return Err(rusqlite::Error::ToSqlConversionFailure(
                    "scan starts before the watermark".into(),
                ));
            }
            let silent = mark.is_none();
            let at_ms = window.start_ms;
            let mut ids = Vec::new();
            for (thread, message) in &unique {
                let fresh = tx.execute(
                    "INSERT OR IGNORE INTO gmail_seen(bot_id, account, thread_id, message_id, first_seen_ms) VALUES (?1,?2,?3,?4,?5)",
                    params![bot, account, thread, message, at_ms],
                )? == 1;
                // Enabling summaries must also queue revisions already seen by polling.
                if mode == ScanMode::Triage {
                    tx.execute(
                        "INSERT OR IGNORE INTO gmail_triage(bot_id, account, thread_id, message_id, first_seen_ms) VALUES (?1,?2,?3,?4,?5)",
                        params![bot, account, thread, message, at_ms],
                    )?;
                }
                if fresh && !silent && mode == ScanMode::Events {
                    let payload = serde_json::json!({
                        "account": account,
                        "thread": thread,
                        "latest_message": message,
                    })
                    .to_string();
                    tx.execute(
                        "INSERT INTO bot_events(bot_id, source, event_id, kind, payload, priority, received_ms) VALUES (?1,?2,?3,?4,?5,?6,?7)",
                        params![
                            bot,
                            GMAIL_SOURCE,
                            format!("{account}:{thread}:{message}"),
                            GMAIL_KIND,
                            payload,
                            GMAIL_PRIORITY,
                            done_ms
                        ],
                    )?;
                    ids.push(tx.last_insert_rowid());
                }
            }
            tx.execute(
                "INSERT INTO gmail_accounts(bot_id, account, baseline_done, last_attempt_ms, last_success_ms, last_result, watermark_ms, window_lower_s, scope) VALUES (?1,?2,1,?3,?3,'ok',?4,?5,'date_window')
                 ON CONFLICT(bot_id, account) DO UPDATE SET baseline_done = 1, last_attempt_ms = ?3, last_success_ms = ?3, last_result = 'ok', watermark_ms = ?4, window_lower_s = ?5, scope = 'date_window'",
                params![bot, account, done_ms, window.start_ms, window.lower_s],
            )?;
            tx.commit()?;
            Ok(GmailScanOutcome {
                baseline_seeded: silent,
                new_event_ids: ids,
            })
        })
        .await
    }

    /// Record a scan that did not complete. Only the attempt time and the fixed result code change.
    pub async fn record_gmail_scan_failure(
        &self,
        bot: &str,
        account: &str,
        failure: GmailScanFailure,
        at_ms: i64,
    ) -> anyhow::Result<()> {
        check_common(bot, account, at_ms)?;
        let (bot, account) = (bot.to_owned(), account.to_owned());
        self.run(move |c| {
            c.execute(
                "INSERT INTO gmail_accounts(bot_id, account, last_attempt_ms, last_result) VALUES (?1,?2,?3,?4)
                 ON CONFLICT(bot_id, account) DO UPDATE SET last_attempt_ms = ?3, last_result = ?4",
                params![bot, account, at_ms, failure.code()],
            )?;
            Ok(())
        })
        .await
    }

    /// The poll state of one account, or `None` when it never ran.
    pub async fn gmail_account_status(
        &self,
        bot: &str,
        account: &str,
    ) -> anyhow::Result<Option<GmailAccountStatus>> {
        let (bot, account) = (bot.to_owned(), account.to_owned());
        self.run(move |c| {
            c.query_row(
                "SELECT baseline_done, last_attempt_ms, last_success_ms, last_result, watermark_ms, window_lower_s, scope FROM gmail_accounts WHERE bot_id = ?1 AND account = ?2",
                params![bot, account],
                |r| {
                    Ok(GmailAccountStatus {
                        baseline_done: r.get(0)?,
                        last_attempt_ms: r.get(1)?,
                        last_success_ms: r.get(2)?,
                        last_result: r.get(3)?,
                        watermark_ms: r.get(4)?,
                        window_lower_s: r.get(5)?,
                        scope: r.get(6)?,
                    })
                },
            )
            .optional()
        })
        .await
    }

    /// Queue one Gmail poll task, unless one is already queued or running. Only this method sets
    /// the `gmail_poll` kind. It also keeps the newest finished polls only.
    pub async fn enqueue_gmail_poll(
        &self,
        bot: &str,
        chat_id: i64,
    ) -> anyhow::Result<Option<Task>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            tx.execute(
                "DELETE FROM tasks WHERE bot_id = ?1 AND execution_kind = 'gmail_poll' AND state IN ('done','failed','cancelled')
                   AND seq NOT IN (SELECT seq FROM tasks WHERE bot_id = ?1 AND execution_kind = 'gmail_poll' AND state IN ('done','failed','cancelled') ORDER BY seq DESC LIMIT 20)",
                [&bot],
            )?;
            let id = uuid::Uuid::new_v4().simple().to_string();
            let n = tx.execute(
                "INSERT INTO tasks(id, bot_id, chat_id, source, title, body, priority, state, created_at, execution_kind)
                 SELECT ?1, ?2, ?3, 'event', 'Gmail poll', 'Scheduled check of the configured Gmail accounts.', 0, 'queued', ?4, 'gmail_poll'
                 WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE bot_id = ?2 AND execution_kind = 'gmail_poll' AND state IN ('queued','running'))",
                params![id, bot, chat_id, now()],
            )?;
            let task = if n == 1 {
                Some(tx.query_row(
                    &format!("SELECT {TASK_COLS} FROM tasks WHERE id = ?1"),
                    [&id],
                    task_row,
                )?)
            } else {
                None
            };
            tx.commit()?;
            Ok(task)
        })
        .await
    }

    /// The queued Gmail poll of a bot, if any.
    pub async fn next_queued_gmail_poll(&self, bot: &str) -> anyhow::Result<Option<Task>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {TASK_COLS} FROM tasks WHERE bot_id = ?1 AND state = 'queued' AND execution_kind = 'gmail_poll' ORDER BY seq LIMIT 1"),
                [&bot],
                task_row,
            )
            .optional()
        })
        .await
    }

    /// End every queued or running Gmail poll as failed. A poll never waits or holds: a new one runs on schedule.
    pub async fn end_open_gmail_polls(&self, bot: &str, why: &str) -> anyhow::Result<usize> {
        let (bot, why) = (bot.to_owned(), why.to_owned());
        self.run(move |c| {
            c.execute(
                "UPDATE tasks SET state = 'failed', detail = ?2, ended_at = ?3 WHERE bot_id = ?1 AND execution_kind = 'gmail_poll' AND state IN ('queued','running')",
                params![bot, why, now()],
            )
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::migrate_to;
    use rusqlite::Connection;

    const T1: &str = "0000000000000001";
    const T2: &str = "0000000000000002";
    const M_A: &str = "00000000000000a1";
    const M_B: &str = "00000000000000b1";
    const M_C: &str = "00000000000000c1";

    /// A window that started at `at` milliseconds and looked back to a fixed lower bound.
    fn win(at: i64) -> GmailWindow {
        GmailWindow {
            start_ms: at,
            lower_s: 1_000_000_000,
        }
    }

    fn rev(t: &str, m: &str) -> GmailThreadRev {
        GmailThreadRev {
            thread_id: t.into(),
            message_id: m.into(),
        }
    }

    #[tokio::test]
    async fn gmail_enabling_summary_queues_previously_seen_mail_once() {
        let s = Store::open_memory().unwrap();
        scan(&s, "bot", "one", &[(T1, M_A)], 1000).await;
        for at in [2000, 3000] {
            s.record_gmail_window_scan_as(
                "bot",
                "one",
                vec![rev(T1, M_A)],
                win(at),
                at,
                ScanMode::Triage,
            )
            .await
            .unwrap();
        }
        let pending = s.gmail_pending_triage("bot", 10, None).await.unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].key.message_id, M_A);
    }

    async fn scan(
        s: &Store,
        bot: &str,
        acct: &str,
        v: &[(&str, &str)],
        at: i64,
    ) -> GmailScanOutcome {
        s.record_gmail_window_scan(
            bot,
            acct,
            v.iter().map(|(t, m)| rev(t, m)).collect(),
            win(at),
            at,
        )
        .await
        .unwrap()
    }

    async fn counts(s: &Store) -> (i64, i64, i64) {
        s.run(|c| {
            let n = |t: &str| c.query_row(&format!("SELECT COUNT(*) FROM {t}"), [], |r| r.get(0));
            Ok((n("gmail_seen")?, n("bot_events")?, n("gmail_accounts")?))
        })
        .await
        .unwrap()
    }

    #[tokio::test]
    async fn gmail_baseline_queues_nothing() {
        let s = Store::open_memory().unwrap();
        let o = scan(&s, "b", "acct1", &[(T1, M_A), (T2, M_A)], 1000).await;
        assert!(o.baseline_seeded && o.new_event_ids.is_empty());
        assert_eq!(counts(&s).await, (2, 0, 1));
        let st = s.gmail_account_status("b", "acct1").await.unwrap().unwrap();
        assert_eq!(
            st,
            GmailAccountStatus {
                baseline_done: true,
                last_attempt_ms: Some(1000),
                last_success_ms: Some(1000),
                last_result: Some("ok".into()),
                watermark_ms: Some(1000),
                window_lower_s: Some(1_000_000_000),
                scope: Some("date_window".into()),
            }
        );
        assert!(
            s.gmail_account_status("b", "other")
                .await
                .unwrap()
                .is_none()
        );
    }

    #[tokio::test]
    async fn gmail_identical_scan_queues_nothing() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "acct1", &[(T1, M_A)], 1000).await;
        let o = scan(&s, "b", "acct1", &[(T1, M_A)], 2000).await;
        assert!(!o.baseline_seeded && o.new_event_ids.is_empty());
        assert_eq!(counts(&s).await.1, 0);
    }

    #[tokio::test]
    async fn gmail_new_and_changed_threads_queue_once() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "acct1", &[(T1, M_A)], 1000).await;
        let o = scan(&s, "b", "acct1", &[(T1, M_B), (T2, M_A)], 2000).await;
        assert_eq!(o.new_event_ids.len(), 2);
        scan(&s, "b", "acct1", &[(T1, M_B), (T2, M_A)], 3000).await;
        let ev = s.unbatched_events("b").await.unwrap();
        assert_eq!(ev.len(), 2);
        assert!(ev.iter().all(|e| e.source == GMAIL_SOURCE
            && e.kind == GMAIL_KIND
            && e.priority == 0
            && e.received_ms == 2000));
        assert_eq!(ev[0].event_id, format!("acct1:{T1}:{M_B}"));
        assert!(ev[0].event_id.len() <= 128);
        let p: serde_json::Value = serde_json::from_str(&ev[0].payload).unwrap();
        assert_eq!(
            p,
            serde_json::json!({"account":"acct1","thread":T1,"latest_message":M_B})
        );
    }

    #[tokio::test]
    async fn gmail_reverted_pair_does_not_requeue() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "a", &[(T1, M_A)], 1).await;
        assert_eq!(
            scan(&s, "b", "a", &[(T1, M_B)], 2)
                .await
                .new_event_ids
                .len(),
            1
        );
        assert!(
            scan(&s, "b", "a", &[(T1, M_A)], 3)
                .await
                .new_event_ids
                .is_empty()
        );
        assert!(
            scan(&s, "b", "a", &[(T1, M_B)], 4)
                .await
                .new_event_ids
                .is_empty()
        );
        assert_eq!(
            scan(&s, "b", "a", &[(T1, M_C)], 5)
                .await
                .new_event_ids
                .len(),
            1
        );
        assert_eq!(counts(&s).await.1, 2);
    }

    #[tokio::test]
    async fn gmail_accounts_and_bots_are_isolated() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b1", "a", &[(T1, M_A)], 1).await;
        // Another account and another bot have their own baseline: silent.
        assert!(scan(&s, "b1", "c", &[(T1, M_A)], 2).await.baseline_seeded);
        assert!(scan(&s, "b2", "a", &[(T1, M_A)], 3).await.baseline_seeded);
        assert_eq!(counts(&s).await, (3, 0, 3));
        // The same change queues once for each account and bot.
        for (b, a) in [("b1", "a"), ("b1", "c"), ("b2", "a")] {
            assert_eq!(scan(&s, b, a, &[(T1, M_B)], 4).await.new_event_ids.len(), 1);
        }
        assert_eq!(counts(&s).await.1, 3);
    }

    #[tokio::test]
    async fn gmail_failures_never_advance_cursor() {
        let s = Store::open_memory().unwrap();
        // Before any baseline.
        s.record_gmail_scan_failure("b", "a", GmailScanFailure::Auth, 10)
            .await
            .unwrap();
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!(
            st,
            GmailAccountStatus {
                baseline_done: false,
                last_attempt_ms: Some(10),
                last_success_ms: None,
                last_result: Some("auth".into()),
                watermark_ms: None,
                window_lower_s: None,
                scope: None,
            }
        );
        assert_eq!(counts(&s).await, (0, 0, 1));
        // The first complete scan after a failure still seeds silently.
        let o = scan(&s, "b", "a", &[(T1, M_A)], 20).await;
        assert!(o.baseline_seeded && o.new_event_ids.is_empty());
        // After the baseline.
        for (i, f) in [
            GmailScanFailure::Incomplete,
            GmailScanFailure::Error,
            GmailScanFailure::IdentityMismatch,
        ]
        .into_iter()
        .enumerate()
        {
            let at = 30 + i as i64;
            s.record_gmail_scan_failure("b", "a", f, at).await.unwrap();
            let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
            assert!(st.baseline_done);
            assert_eq!(st.last_attempt_ms, Some(at));
            assert_eq!(st.last_success_ms, Some(20));
            assert_eq!(st.last_result.as_deref(), Some(f.code()));
            assert_eq!(counts(&s).await, (1, 0, 1));
        }
        // A later success recovers and queues only real changes.
        assert_eq!(
            scan(&s, "b", "a", &[(T1, M_B)], 40)
                .await
                .new_event_ids
                .len(),
            1
        );
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!(
            (st.last_success_ms, st.last_result.as_deref()),
            (Some(40), Some("ok"))
        );
    }

    #[tokio::test]
    async fn gmail_empty_complete_scan_is_valid() {
        let s = Store::open_memory().unwrap();
        let o = scan(&s, "b", "a", &[], 5).await;
        assert!(o.baseline_seeded && o.new_event_ids.is_empty());
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert!(st.baseline_done && st.last_success_ms == Some(5));
        // A thread after an empty baseline is a real change.
        assert_eq!(
            scan(&s, "b", "a", &[(T1, M_A)], 6)
                .await
                .new_event_ids
                .len(),
            1
        );
        // A later empty scan forgets nothing.
        assert!(scan(&s, "b", "a", &[], 7).await.new_event_ids.is_empty());
        assert!(
            scan(&s, "b", "a", &[(T1, M_A)], 8)
                .await
                .new_event_ids
                .is_empty()
        );
    }

    #[tokio::test]
    async fn gmail_invalid_input_mutates_nothing() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "a", &[(T1, M_A)], 1).await;
        let before = counts(&s).await;
        let long = "x".repeat(65);
        let bad = |v: Vec<GmailThreadRev>, acct: &str, at: i64| {
            let s = s.clone();
            let acct = acct.to_owned();
            async move {
                s.record_gmail_window_scan("b", &acct, v, win(at.max(1)), at)
                    .await
                    .is_err()
            }
        };
        let ok = vec![rev(T2, M_A)];
        assert!(bad(vec![rev("short", M_A)], "a", 2).await);
        assert!(bad(vec![rev(T2, "00000000000000A1")], "a", 2).await);
        assert!(bad(vec![rev("000000000000000g", M_A)], "a", 2).await);
        assert!(bad(vec![rev(T2, M_A), rev(T2, M_B)], "a", 2).await);
        for acct in ["", "has space", "a@b", "é", long.as_str()] {
            assert!(bad(ok.clone(), acct, 2).await, "{acct}");
        }
        assert!(bad(ok.clone(), "a", 0).await);
        assert!(bad(ok.clone(), "a", -1).await);
        assert!(
            s.record_gmail_scan_failure("b", "a b", GmailScanFailure::Error, 2)
                .await
                .is_err()
        );
        assert!(
            s.record_gmail_scan_failure("b", "a", GmailScanFailure::Error, 0)
                .await
                .is_err()
        );
        assert!(
            s.record_gmail_window_scan("", "a", ok.clone(), win(2), 2)
                .await
                .is_err()
        );
        assert_eq!(counts(&s).await, before);
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!(
            (st.last_attempt_ms, st.last_result.as_deref()),
            (Some(1), Some("ok"))
        );
        // An exact duplicate pair is not contradictory.
        assert!(
            scan(&s, "b", "a", &[(T2, M_A), (T2, M_A)], 3)
                .await
                .new_event_ids
                .len()
                == 1
        );
        // A 64-character key is valid.
        scan(&s, "b", &"x".repeat(64), &[], 4).await;
    }

    #[tokio::test]
    async fn gmail_event_failure_rolls_back_everything() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "a", &[(T1, M_A)], 1).await;
        s.run(|c| {
            c.execute_batch(
                "CREATE TRIGGER fail_event BEFORE INSERT ON bot_events WHEN NEW.event_id LIKE '%0bad0%' BEGIN SELECT RAISE(ABORT, 'injected'); END;",
            )
        })
        .await
        .unwrap();
        let before = counts(&s).await;
        // The first change inserts a seen row and an event; the second fails.
        let err = s
            .record_gmail_window_scan(
                "b",
                "a",
                vec![rev(T1, M_B), rev("00000000000bad00", M_A)],
                win(2),
                2,
            )
            .await;
        assert!(err.is_err());
        assert_eq!(counts(&s).await, before);
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!((st.last_success_ms, st.last_attempt_ms), (Some(1), Some(1)));
        // Without the fault the same scan queues both.
        s.run(|c| c.execute_batch("DROP TRIGGER fail_event;"))
            .await
            .unwrap();
        let o = scan(&s, "b", "a", &[(T1, M_B), ("00000000000bad00", M_A)], 3).await;
        assert_eq!(o.new_event_ids.len(), 2);
    }

    #[tokio::test]
    async fn gmail_success_metadata_failure_rolls_back_seen_and_events() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "a", &[(T1, M_A)], 1).await;
        s.run(|c| {
            c.execute_batch(
                "CREATE TRIGGER fail_meta BEFORE UPDATE ON gmail_accounts BEGIN SELECT RAISE(ABORT, 'injected'); END;",
            )
        })
        .await
        .unwrap();
        let before = counts(&s).await;
        assert!(
            s.record_gmail_window_scan("b", "a", vec![rev(T1, M_B)], win(2), 2)
                .await
                .is_err()
        );
        assert_eq!(counts(&s).await, before);
    }

    #[tokio::test]
    async fn gmail_state_survives_reopen_and_migration() {
        let dir = std::env::temp_dir().join(format!("deck-gmail-{}", uuid::Uuid::new_v4()));
        {
            let s = Store::open(&dir).unwrap();
            scan(&s, "b", "a", &[(T1, M_A)], 1).await;
            scan(&s, "b", "a", &[(T1, M_B)], 2).await;
        }
        let s = Store::open(&dir).unwrap();
        assert!(
            s.gmail_account_status("b", "a")
                .await
                .unwrap()
                .unwrap()
                .baseline_done
        );
        assert_eq!(s.unbatched_events("b").await.unwrap().len(), 1);
        assert!(
            scan(&s, "b", "a", &[(T1, M_B)], 3)
                .await
                .new_event_ids
                .is_empty()
        );
        drop(s);
        std::fs::remove_dir_all(&dir).unwrap();

        // A version 9 database gains the tables and keeps its rows.
        let conn = Connection::open_in_memory().unwrap();
        migrate_to(&conn, 9).unwrap();
        conn.execute(
            "INSERT INTO bot_events(bot_id, source, event_id, kind, payload, priority, received_ms) VALUES ('b','x','e','k','{}',0,1)",
            [],
        )
        .unwrap();
        migrate_to(&conn, i64::MAX).unwrap();
        migrate_to(&conn, i64::MAX).unwrap();
        let n: i64 = conn
            .query_row("SELECT COUNT(*) FROM bot_events", [], |r| r.get(0))
            .unwrap();
        let v: i64 = conn
            .query_row("PRAGMA user_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!((n, v), (1, 14));
        conn.prepare("SELECT 1 FROM gmail_triage").unwrap();
        conn.prepare("SELECT 1 FROM gmail_summary_days").unwrap();
        conn.execute("SELECT * FROM gmail_accounts", [])
            .unwrap_or(0);
        conn.prepare("SELECT 1 FROM gmail_seen").unwrap();
    }

    #[tokio::test]
    async fn gmail_watermark_is_the_scan_start_and_never_the_completion() {
        let s = Store::open_memory().unwrap();
        // The scan started at 1000 and ended at 9000. Mail that arrived in between must stay inside the next window.
        s.record_gmail_window_scan("b", "a", vec![rev(T1, M_A)], win(1000), 9000)
            .await
            .unwrap();
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!(st.watermark_ms, Some(1000));
        assert_eq!(
            (st.last_success_ms, st.last_attempt_ms),
            (Some(9000), Some(9000))
        );
        assert_eq!(st.scope.as_deref(), Some("date_window"));
        // Failures change neither the watermark nor the window.
        s.record_gmail_scan_failure("b", "a", GmailScanFailure::Incomplete, 9500)
            .await
            .unwrap();
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!(
            (st.watermark_ms, st.window_lower_s),
            (Some(1000), Some(1_000_000_000))
        );
    }

    #[tokio::test]
    async fn gmail_a_scan_that_starts_before_the_watermark_is_refused() {
        let s = Store::open_memory().unwrap();
        scan(&s, "b", "a", &[(T1, M_A)], 5000).await;
        let before = counts(&s).await;
        let err = s
            .record_gmail_window_scan("b", "a", vec![rev(T1, M_B)], win(4000), 6000)
            .await;
        assert!(err.is_err());
        assert_eq!(counts(&s).await, before);
        let st = s.gmail_account_status("b", "a").await.unwrap().unwrap();
        assert_eq!(
            (st.watermark_ms, st.last_success_ms),
            (Some(5000), Some(5000))
        );
        // The same start is accepted and moves nothing back.
        assert!(
            s.record_gmail_window_scan("b", "a", vec![rev(T1, M_A)], win(5000), 6000)
                .await
                .is_ok()
        );
    }

    #[tokio::test]
    async fn gmail_a_window_with_an_impossible_lower_bound_is_refused() {
        let s = Store::open_memory().unwrap();
        for lower in [0, -5] {
            let w = GmailWindow {
                start_ms: 1_000_000,
                lower_s: lower,
            };
            assert!(
                s.record_gmail_window_scan("b", "a", vec![], w, 1_000_001)
                    .await
                    .is_err(),
                "{lower}"
            );
        }
        assert_eq!(counts(&s).await, (0, 0, 0));
    }

    #[tokio::test]
    async fn gmail_schema_10_baseline_without_a_watermark_seeds_silently_again() {
        let conn = Connection::open_in_memory().unwrap();
        migrate_to(&conn, 10).unwrap();
        conn.execute_batch(
            "INSERT INTO gmail_accounts(bot_id, account, baseline_done, last_attempt_ms, last_success_ms, last_result) VALUES ('b','a',1,7,7,'ok');
             INSERT INTO gmail_seen(bot_id, account, thread_id, message_id, first_seen_ms) VALUES ('b','a','0000000000000001','00000000000000a1',7);",
        )
        .unwrap();
        migrate_to(&conn, i64::MAX).unwrap();
        // No watermark is invented from the old completion time.
        let (wm, lower, scope): (Option<i64>, Option<i64>, Option<String>) = conn
            .query_row(
                "SELECT watermark_ms, window_lower_s, scope FROM gmail_accounts",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!((wm, lower, scope), (None, None, None));
        let s = Store::from_conn(conn).unwrap();
        let o = scan(&s, "b", "a", &[(T1, M_B), (T2, M_A)], 100).await;
        assert!(o.baseline_seeded && o.new_event_ids.is_empty());
        // After that, changes queue.
        assert_eq!(
            scan(&s, "b", "a", &[(T1, M_C)], 200)
                .await
                .new_event_ids
                .len(),
            1
        );
    }

    #[tokio::test]
    async fn task_execution_kind_defaults_to_model_and_rejects_unknown_values() {
        let s = Store::open_memory().unwrap();
        let chat = s.bot_flags("b", "B").await.unwrap().chat_id;
        let t = s
            .add_task("b", chat, "message", "t".into(), "x".into(), 1)
            .await
            .unwrap();
        assert_eq!(t.execution_kind, "model");
        let e = s
            .batch_events("b", chat, vec![], "e".into(), "x".into(), 0)
            .await
            .unwrap();
        assert_eq!(e.execution_kind, "model");
        let bad = s
            .run(|c| {
                c.execute(
                    "UPDATE tasks SET execution_kind = 'shell' WHERE bot_id = 'b'",
                    [],
                )
            })
            .await;
        assert!(bad.is_err());
        let spoof = s
            .run(|c| {
                c.execute(
                    "INSERT INTO tasks(id, bot_id, chat_id, source, title, body, priority, state, created_at) VALUES ('p','b',1,'gmail_poll','t','b',0,'queued','x')",
                    [],
                )
            })
            .await;
        assert!(spoof.is_err(), "source stays message or event");
    }

    #[tokio::test]
    async fn gmail_poll_tasks_are_enqueued_once_and_never_claimed_as_model_work() {
        let s = Store::open_memory().unwrap();
        let chat = s.bot_flags("b", "B").await.unwrap().chat_id;
        let p = s.enqueue_gmail_poll("b", chat).await.unwrap().unwrap();
        assert_eq!(
            (p.execution_kind.as_str(), p.source.as_str(), p.priority),
            ("gmail_poll", "event", 0)
        );
        assert!(s.enqueue_gmail_poll("b", chat).await.unwrap().is_none());
        // A model claim never sees it. A poll claim sees only polls.
        assert!(s.next_queued("b").await.unwrap().is_none());
        assert_eq!(
            s.next_queued_gmail_poll("b").await.unwrap().unwrap().id,
            p.id
        );
        // A title or body that says "gmail" does not make model work a poll.
        let m = s
            .add_task(
                "b",
                chat,
                "event",
                "Gmail poll".into(),
                "gmail_poll".into(),
                2,
            )
            .await
            .unwrap();
        assert_eq!(s.next_queued("b").await.unwrap().unwrap().id, m.id);
        assert_eq!(
            s.next_queued_gmail_poll("b").await.unwrap().unwrap().id,
            p.id
        );
        // Still outstanding while running. Another bot is independent.
        s.move_task(&p.id, &["queued"], "running", None)
            .await
            .unwrap();
        assert!(s.enqueue_gmail_poll("b", chat).await.unwrap().is_none());
        let other = s.bot_flags("c", "C").await.unwrap().chat_id;
        assert!(s.enqueue_gmail_poll("c", other).await.unwrap().is_some());
        s.move_task(&p.id, &["running"], "done", None)
            .await
            .unwrap();
        assert!(s.enqueue_gmail_poll("b", chat).await.unwrap().is_some());
    }

    #[tokio::test]
    async fn finished_polls_do_not_crowd_the_task_list_and_are_pruned() {
        let s = Store::open_memory().unwrap();
        let chat = s.bot_flags("b", "B").await.unwrap().chat_id;
        let real = s
            .add_task("b", chat, "message", "real".into(), "x".into(), 1)
            .await
            .unwrap();
        s.move_task(&real.id, &["queued"], "done", None)
            .await
            .unwrap();
        for _ in 0..30 {
            let p = s.enqueue_gmail_poll("b", chat).await.unwrap().unwrap();
            s.move_task(&p.id, &["queued"], "done", None).await.unwrap();
        }
        let shown = s.tasks("b", 10).await.unwrap();
        assert_eq!(shown.len(), 1);
        assert_eq!(shown[0].id, real.id);
        let polls: i64 = s
            .run(|c| {
                c.query_row(
                    "SELECT COUNT(*) FROM tasks WHERE execution_kind='gmail_poll'",
                    [],
                    |r| r.get(0),
                )
            })
            .await
            .unwrap();
        assert!(polls <= 21, "{polls}");
    }

    #[tokio::test]
    async fn interrupted_polls_are_ended_not_held() {
        let s = Store::open_memory().unwrap();
        let chat = s.bot_flags("b", "B").await.unwrap().chat_id;
        let p = s.enqueue_gmail_poll("b", chat).await.unwrap().unwrap();
        s.move_task(&p.id, &["queued"], "running", None)
            .await
            .unwrap();
        assert_eq!(
            s.end_open_gmail_polls("b", "interrupted by a host restart")
                .await
                .unwrap(),
            1
        );
        let t = s.task(&p.id).await.unwrap().unwrap();
        assert_eq!(t.state, "failed");
        assert_eq!(t.detail.as_deref(), Some("interrupted by a host restart"));
        assert!(s.enqueue_gmail_poll("b", chat).await.unwrap().is_some());
    }

    #[tokio::test]
    async fn schema_10_tasks_become_model_tasks() {
        let conn = Connection::open_in_memory().unwrap();
        migrate_to(&conn, 10).unwrap();
        conn.execute_batch(
            "INSERT INTO chats(id, title, created_at) VALUES (1,'c','x');
             INSERT INTO tasks(id, bot_id, chat_id, source, title, body, priority, state, created_at) VALUES ('t','b',1,'message','t','b',1,'queued','x');",
        )
        .unwrap();
        migrate_to(&conn, i64::MAX).unwrap();
        let k: String = conn
            .query_row("SELECT execution_kind FROM tasks WHERE id='t'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(k, "model");
    }
}
