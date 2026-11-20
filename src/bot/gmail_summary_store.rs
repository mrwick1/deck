//! Durable state of the Gmail morning summary: triage rows, label progress and the delivery record of each local day.
//!
//! A scan queues a `pending` row for each unseen revision. The row becomes `triaged` only when the caller stores a
//! validated answer. A failure keeps the row `pending` and counts the attempt, so no mail drops.
//! Only the latest revision of a thread is read, labeled or shown.

use std::collections::BTreeMap;

use anyhow::bail;
use rusqlite::{OptionalExtension, params};

use super::gmail_cleanup::OrgRow;
use crate::store::Store;

/// What mail needs from the owner. The model chooses one. The host maps it to a label.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Category {
    ReplyNeeded,
    Actionable,
    AwaitingReply,
    Fyi,
}

impl Category {
    pub const ALL: [Category; 4] = [
        Self::ReplyNeeded,
        Self::Actionable,
        Self::AwaitingReply,
        Self::Fyi,
    ];

    pub fn code(self) -> &'static str {
        match self {
            Self::ReplyNeeded => "reply_needed",
            Self::Actionable => "actionable",
            Self::AwaitingReply => "awaiting_reply",
            Self::Fyi => "fyi",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|c| c.code() == s)
    }

    /// The heading of the summary.
    pub fn heading(self) -> &'static str {
        match self {
            Self::ReplyNeeded => "Needs reply",
            Self::Actionable => "Action",
            Self::AwaitingReply => "Waiting",
            Self::Fyi => "FYI",
        }
    }

    /// The Gmail label for this category. Fixed names, never taken from model text.
    pub fn label(self) -> &'static str {
        match self {
            Self::ReplyNeeded => "Deck/Needs reply",
            Self::Actionable => "Deck/Action",
            Self::AwaitingReply => "Deck/Waiting",
            Self::Fyi => "Deck/FYI",
        }
    }
}

/// One revision of one thread of one account.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct RevKey {
    pub account: String,
    pub thread_id: String,
    pub message_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingTriage {
    pub key: RevKey,
    pub attempts: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Triaged {
    pub key: RevKey,
    pub category: Category,
    pub summary: String,
    pub label_state: String,
    pub label_attempts: u32,
}

/// The label progress of a revision. `None` is not tried yet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LabelState {
    Applied,
    Uncertain,
    Failed,
}

impl LabelState {
    fn code(self) -> &'static str {
        match self {
            Self::Applied => "applied",
            Self::Uncertain => "uncertain",
            Self::Failed => "failed",
        }
    }
}

/// `bot_events.task_id` of an event that triage took over. It names no task.
pub const HANDOFF_MARK: &str = "gmail_triage";

/// Label attempts before a revision stays `failed`.
pub const LABEL_ATTEMPTS: u32 = 3;

const LATEST: &str = "NOT EXISTS (SELECT 1 FROM gmail_triage n WHERE n.bot_id = t.bot_id AND n.account = t.account AND n.thread_id = t.thread_id AND n.rowid > t.rowid)";

fn valid_key(k: &RevKey) -> bool {
    let id = |s: &str| s.len() == 16 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'));
    id(&k.thread_id)
        && id(&k.message_id)
        && (1..=64).contains(&k.account.len())
        && k.account
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn key_row(r: &rusqlite::Row, at: usize) -> rusqlite::Result<RevKey> {
    Ok(RevKey {
        account: r.get(at)?,
        thread_id: r.get(at + 1)?,
        message_id: r.get(at + 2)?,
    })
}

impl Store {
    /// The oldest pending revisions that are the latest of their thread, up to `limit`.
    /// An older pending revision of a thread that has a newer one becomes `superseded`.
    pub async fn gmail_pending_triage(
        &self,
        bot: &str,
        limit: u32,
        focus: Option<&str>,
    ) -> anyhow::Result<Vec<PendingTriage>> {
        let focus = focus.map(str::to_owned);
        let bot = bot.to_owned();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            tx.execute(
                &format!("UPDATE gmail_triage AS t SET state = 'superseded' WHERE bot_id = ?1 AND state = 'pending' AND NOT {LATEST}"),
                [&bot],
            )?;
            let rows = {
                let mut q = tx.prepare(
                    "SELECT account, thread_id, message_id, attempts FROM gmail_triage t WHERE bot_id = ?1 AND state = 'pending' AND (?3 IS NULL OR origin <> 'backlog' OR account = ?3) ORDER BY origin = 'backlog', rowid LIMIT ?2",
                )?;
                q.query_map(params![bot, limit, focus], |r| {
                    Ok(PendingTriage {
                        key: key_row(r, 0)?,
                        attempts: r.get(3)?,
                    })
                })?
                .collect::<Result<Vec<_>, _>>()?
            };
            tx.commit()?;
            Ok(rows)
        })
        .await
    }

    /// Store a validated answer. True once: a revision that is not `pending` any more stays as it is.
    pub async fn gmail_triage_done(
        &self,
        bot: &str,
        key: &RevKey,
        category: Category,
        summary: &str,
        org: OrgRow,
        at_ms: i64,
    ) -> anyhow::Result<bool> {
        if !valid_key(key) || summary.is_empty() || summary.len() > 400 || at_ms <= 0 {
            bail!("invalid triage result");
        }
        let (bot, key, summary) = (bot.to_owned(), key.clone(), summary.to_owned());
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            let once = tx.execute(
                "UPDATE gmail_triage SET state = 'triaged', category = ?5, summary = ?6, triaged_ms = ?7, last_failure = NULL WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND message_id = ?4 AND state = 'pending'",
                params![bot, key.account, key.thread_id, key.message_id, category.code(), summary, at_ms],
            )? == 1;
            if once {
                let d = org.decision;
                let topic = org.stored_topic();
                // A row that waits for its one read again takes the new decision. A second cut read is final.
                tx.execute(
                    "UPDATE gmail_organize SET kind = ?5, decision = ?6, reason = CASE WHEN ?7 = 'partial_read' THEN 'partial_read_final' ELSE ?7 END, topic_state = CASE WHEN topic IS ?8 THEN topic_state ELSE 'none' END, topic_attempts = CASE WHEN topic IS ?8 THEN topic_attempts ELSE 0 END, topic = ?8, decided_ms = ?9 WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND message_id = ?4 AND reason IN ('partial_read_retry','trash_refused_retry')",
                    params![bot, key.account, key.thread_id, key.message_id, d.kind.code(), d.action.code(), d.reason, topic, at_ms],
                )?;
                tx.execute(
                    "INSERT OR IGNORE INTO gmail_organize(bot_id, account, thread_id, message_id, kind, decision, reason, topic, decided_ms) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
                    params![bot, key.account, key.thread_id, key.message_id, d.kind.code(), d.action.code(), d.reason, topic, at_ms],
                )?;
            }
            tx.commit()?;
            Ok(once)
        })
        .await
    }

    /// Send the host's own earlier `partial_read` reviews back to triage, once each, up to `limit`.
    /// The organize row stays and gets the reason `partial_read_retry`, so a second cut read ends as `partial_read_final`.
    /// Only the latest revision of a thread, with no write of its own in doubt, is read again. Returns the count.
    pub async fn gmail_retry_partial_reads(&self, bot: &str, limit: u32) -> anyhow::Result<u32> {
        self.retry(
            bot,
            limit,
            "partial_read",
            "partial_read_retry",
            "o.trash_state = 'none' AND o.topic_state IN ('none','applied')",
        )
        .await
    }

    /// Send the guard's earlier `trash_refused` reviews back to triage once each (D98). Rows from before the fix took the
    /// gmail `Inbox` chip for a label of the owner. The guard still decides again. A new refusal is `trash_refused_final`.
    pub async fn gmail_retry_refused_trashes(&self, bot: &str, limit: u32) -> anyhow::Result<u32> {
        self.retry(
            bot,
            limit,
            "trash_refused",
            "trash_refused_retry",
            "o.trash_state = 'refused'",
        )
        .await
    }

    async fn retry(
        &self,
        bot: &str,
        limit: u32,
        from: &'static str,
        to: &'static str,
        only: &'static str,
    ) -> anyhow::Result<u32> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            let rows = {
                let mut q = tx.prepare(&format!(
                    "SELECT t.account, t.thread_id, t.message_id FROM gmail_triage t JOIN gmail_organize o ON o.bot_id = t.bot_id AND o.account = t.account AND o.thread_id = t.thread_id AND o.message_id = t.message_id WHERE t.bot_id = ?1 AND t.state = 'triaged' AND o.reason = '{from}' AND {only} AND {LATEST} ORDER BY t.rowid LIMIT ?2"
                ))?;
                q.query_map(params![bot, limit], |r| key_row(r, 0))?
                    .collect::<Result<Vec<_>, _>>()?
            };
            for k in &rows {
                let w = params![bot, k.account, k.thread_id, k.message_id];
                tx.execute(
                    &format!("UPDATE gmail_organize SET reason = '{to}', trash_state = 'none' WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND message_id = ?4"),
                    w,
                )?;
                tx.execute(
                    "UPDATE gmail_triage SET state = 'pending', attempts = 0, last_failure = NULL WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND message_id = ?4",
                    w,
                )?;
            }
            tx.commit()?;
            Ok(rows.len() as u32)
        })
        .await
    }

    /// Count a failed attempt with a fixed code. The revision stays `pending`.
    pub async fn gmail_triage_failed(
        &self,
        bot: &str,
        key: &RevKey,
        code: &'static str,
    ) -> anyhow::Result<()> {
        let (bot, key) = (bot.to_owned(), key.clone());
        self.run(move |c| {
            c.execute(
                "UPDATE gmail_triage SET attempts = attempts + 1, last_failure = ?5 WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND message_id = ?4 AND state = 'pending'",
                params![bot, key.account, key.thread_id, key.message_id, code],
            )?;
            Ok(())
        })
        .await
    }

    /// Move stored, unprocessed Gmail events into triage in one transaction. Each event becomes a triage row
    /// (nothing when its revision has a row already) and gets `HANDOFF_MARK` in `task_id`. That makes it processed
    /// and creates no task. The event row stays as the audit. A thread that had a triage row before this call gets no
    /// row, because the event is older and the latest revision decides. The event is still processed. An event with a bad payload is left untouched.
    /// Returns (events handed over, events left). A second call hands over nothing.
    pub async fn gmail_handoff_events(&self, bot: &str) -> anyhow::Result<(u32, u32)> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            let before: i64 = tx.query_row(
                "SELECT COALESCE(MAX(rowid), 0) FROM gmail_triage",
                [],
                |r| r.get(0),
            )?;
            let events = {
                let mut q = tx.prepare(
                    "SELECT id, payload, received_ms FROM bot_events WHERE bot_id = ?1 AND source = ?2 AND task_id IS NULL ORDER BY id",
                )?;
                q.query_map(params![bot, crate::bot::gmail_store::GMAIL_SOURCE], |r| {
                    Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?))
                })?
                .collect::<Result<Vec<_>, _>>()?
            };
            let (mut done, mut left) = (0, 0);
            for (id, payload, received) in events {
                let key = serde_json::from_str::<serde_json::Value>(&payload)
                    .ok()
                    .and_then(|v| {
                        let f = |k: &str| v[k].as_str().map(str::to_owned);
                        Some(RevKey {
                            account: f("account")?,
                            thread_id: f("thread")?,
                            message_id: f("latest_message")?,
                        })
                    })
                    .filter(valid_key);
                let Some(key) = key else {
                    left += 1;
                    continue;
                };
                let older: bool = tx.query_row(
                    "SELECT EXISTS(SELECT 1 FROM gmail_triage WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND rowid <= ?4)",
                    params![bot, key.account, key.thread_id, before],
                    |r| r.get(0),
                )?;
                if !older {
                    tx.execute(
                        "INSERT OR IGNORE INTO gmail_triage(bot_id, account, thread_id, message_id, first_seen_ms) VALUES (?1,?2,?3,?4,?5)",
                        params![bot, key.account, key.thread_id, key.message_id, received.max(1)],
                    )?;
                }
                tx.execute(
                    "UPDATE bot_events SET task_id = ?2 WHERE id = ?1",
                    params![id, HANDOFF_MARK],
                )?;
                done += 1;
            }
            tx.commit()?;
            Ok((done, left))
        })
        .await
    }

    /// True while a revision has `failed` label state. Only a lost label sets it, so label writes stay halted
    /// until the owner sets those rows to `skipped`.
    pub async fn gmail_label_halted(&self, bot: &str) -> anyhow::Result<bool> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.query_row(
                "SELECT EXISTS(SELECT 1 FROM gmail_triage WHERE bot_id = ?1 AND label_state = 'failed')",
                [&bot],
                |r| r.get(0),
            )
        })
        .await
    }

    /// Triaged latest revisions that still need a label write.
    /// With `skip_trash`, a thread that waits for its move to Trash gets no category label first.
    pub async fn gmail_label_candidates(
        &self,
        bot: &str,
        limit: u32,
        skip_trash: bool,
        focus: Option<&str>,
    ) -> anyhow::Result<Vec<Triaged>> {
        let (bot, focus) = (bot.to_owned(), focus.map(str::to_owned));
        let skip = if skip_trash {
            "AND NOT EXISTS (SELECT 1 FROM gmail_organize o WHERE o.bot_id = t.bot_id AND o.account = t.account AND o.thread_id = t.thread_id AND o.message_id = t.message_id AND o.decision = 'trash')"
        } else {
            ""
        };
        self.run(move |c| {
            let mut q = c.prepare(&format!(
                "SELECT account, thread_id, message_id, category, summary, label_state, label_attempts FROM gmail_triage t WHERE bot_id = ?1 AND state = 'triaged' AND label_state IN ('none','uncertain') AND label_attempts < {LABEL_ATTEMPTS} AND (?3 IS NULL OR origin <> 'backlog' OR account = ?3) AND {LATEST} {skip} ORDER BY origin = 'backlog', rowid LIMIT ?2"
            ))?;
            let rows = q.query_map(params![bot, limit, focus], triaged_row)?.collect::<Result<_, _>>()?;
            Ok(rows)
        })
        .await
    }

    /// Record the outcome of one label write. `attempt` counts a write that ran or may have run.
    pub async fn gmail_label_result(
        &self,
        bot: &str,
        key: &RevKey,
        state: LabelState,
        attempt: bool,
    ) -> anyhow::Result<()> {
        let (bot, key) = (bot.to_owned(), key.clone());
        self.run(move |c| {
            c.execute(
                "UPDATE gmail_triage SET label_state = ?5, label_attempts = label_attempts + ?6 WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND message_id = ?4 AND state = 'triaged'",
                params![bot, key.account, key.thread_id, key.message_id, state.code(), attempt as i64],
            )?;
            Ok(())
        })
        .await
    }

    /// Triaged latest revisions that no summary showed yet.
    pub async fn gmail_summary_items(&self, bot: &str) -> anyhow::Result<Vec<Triaged>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let mut q = c.prepare(&format!(
                "SELECT account, thread_id, message_id, category, summary, label_state, label_attempts FROM gmail_triage t WHERE bot_id = ?1 AND state = 'triaged' AND summarized_day IS NULL AND {LATEST} AND NOT EXISTS (SELECT 1 FROM gmail_organize o WHERE o.bot_id = t.bot_id AND o.account = t.account AND o.thread_id = t.thread_id AND o.message_id = t.message_id AND o.trash_state = 'done') ORDER BY rowid"
            ))?;
            let rows = q.query_map([&bot], triaged_row)?.collect::<Result<_, _>>()?;
            Ok(rows)
        })
        .await
    }

    /// Pending revisions and their failed attempts for each account.
    pub async fn gmail_triage_backlog(
        &self,
        bot: &str,
    ) -> anyhow::Result<BTreeMap<String, (u32, u32)>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let mut q = c.prepare(&format!(
                "SELECT account, COUNT(*), SUM(attempts > 0) FROM gmail_triage t WHERE bot_id = ?1 AND state = 'pending' AND {LATEST} GROUP BY account"
            ))?;
            let rows = q
                .query_map([&bot], |r| Ok((r.get::<_, String>(0)?, (r.get(1)?, r.get::<_, Option<u32>>(2)?.unwrap_or(0)))))?
                .collect::<Result<_, _>>()?;
            Ok(rows)
        })
        .await
    }

    /// The newest delivered local day and its time.
    pub async fn gmail_last_delivery(&self, bot: &str) -> anyhow::Result<Option<(String, i64)>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.query_row(
                "SELECT day, delivered_ms FROM gmail_summary_days WHERE bot_id = ?1 ORDER BY delivered_ms DESC LIMIT 1",
                [&bot],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
        })
        .await
    }

    /// Pending latest revisions by the fixed code of their last failure.
    pub async fn gmail_triage_errors(&self, bot: &str) -> anyhow::Result<BTreeMap<String, u32>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let mut q = c.prepare(&format!(
                "SELECT last_failure, COUNT(*) FROM gmail_triage t WHERE bot_id = ?1 AND state = 'pending' AND last_failure IS NOT NULL AND {LATEST} GROUP BY last_failure"
            ))?;
            q.query_map([&bot], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
        })
        .await
    }

    /// Triaged latest revisions by label state.
    pub async fn gmail_label_counts(&self, bot: &str) -> anyhow::Result<BTreeMap<String, u32>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let mut q = c.prepare(&format!(
                "SELECT label_state, COUNT(*) FROM gmail_triage t WHERE bot_id = ?1 AND state = 'triaged' AND {LATEST} GROUP BY label_state"
            ))?;
            q.query_map([&bot], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
        })
        .await
    }

    pub async fn gmail_summary_delivered(&self, bot: &str, day: &str) -> anyhow::Result<bool> {
        let (bot, day) = (bot.to_owned(), day.to_owned());
        self.run(move |c| {
            Ok(c.query_row(
                "SELECT 1 FROM gmail_summary_days WHERE bot_id = ?1 AND day = ?2",
                params![bot, day],
                |_| Ok(()),
            )
            .optional()?
            .is_some())
        })
        .await
    }

    /// Post the summary and close the local day in one transaction. False when the day has a summary already:
    /// then nothing is posted. Every revision of the shown threads is marked as summarized.
    pub async fn deliver_gmail_summary(
        &self,
        bot: &str,
        chat_id: i64,
        day: &str,
        text: &str,
        shown: &[(String, String)],
        at_ms: i64,
    ) -> anyhow::Result<bool> {
        let (bot, day, shown) = (bot.to_owned(), day.to_owned(), shown.to_vec());
        let content = serde_json::json!({"role":"assistant","content":text}).to_string();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            if tx.execute(
                "INSERT OR IGNORE INTO gmail_summary_days(bot_id, day, delivered_ms) VALUES (?1,?2,?3)",
                params![bot, day, at_ms],
            )? == 0
            {
                return Ok(false);
            }
            tx.execute(
                "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?1,'assistant',?2,?3)",
                params![chat_id, content, crate::store::now()],
            )?;
            for (account, thread) in &shown {
                tx.execute(
                    "UPDATE gmail_triage SET summarized_day = ?4 WHERE bot_id = ?1 AND account = ?2 AND thread_id = ?3 AND state = 'triaged' AND summarized_day IS NULL",
                    params![bot, account, thread, day],
                )?;
            }
            tx.commit()?;
            Ok(true)
        })
        .await
    }
}

fn triaged_row(r: &rusqlite::Row) -> rusqlite::Result<Triaged> {
    Ok(Triaged {
        key: key_row(r, 0)?,
        category: Category::parse(&r.get::<_, String>(3)?).unwrap_or(Category::Fyi),
        summary: r.get(4)?,
        label_state: r.get(5)?,
        label_attempts: r.get(6)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bot::gmail_store::{GmailThreadRev, GmailWindow, ScanMode};

    const T1: &str = "0000000000000001";
    const T2: &str = "0000000000000002";
    const MA: &str = "00000000000000a1";
    const MB: &str = "00000000000000b1";

    fn key(t: &str, m: &str) -> RevKey {
        RevKey {
            account: "acct1".into(),
            thread_id: t.into(),
            message_id: m.into(),
        }
    }

    async fn scan(s: &Store, v: &[(&str, &str)], at: i64) {
        s.record_gmail_window_scan_as(
            "b",
            "acct1",
            v.iter()
                .map(|(t, m)| GmailThreadRev {
                    thread_id: (*t).into(),
                    message_id: (*m).into(),
                })
                .collect(),
            GmailWindow {
                start_ms: at,
                lower_s: 1_000_000_000,
            },
            at,
            ScanMode::Triage,
        )
        .await
        .unwrap();
    }

    async fn chat(s: &Store) -> i64 {
        s.bot_flags("b", "Bot").await.unwrap().chat_id
    }

    #[tokio::test]
    async fn the_handoff_moves_old_events_into_triage_once_and_keeps_the_audit() {
        let s = Store::open_memory().unwrap();
        // Event mode: a baseline, then three events (T1 twice) and one that is outside any window.
        for (at, v) in [
            (1000, vec![(T1, MA)]),
            (2000, vec![(T1, MB), (T2, MA)]),
            (3000, vec![(T1, "00000000000000c1")]),
        ] {
            s.record_gmail_window_scan(
                "b",
                "acct1",
                v.iter()
                    .map(|(t, m)| GmailThreadRev {
                        thread_id: (*t).into(),
                        message_id: (*m).into(),
                    })
                    .collect(),
                GmailWindow {
                    start_ms: at,
                    lower_s: 1_000_000_000,
                },
                at,
            )
            .await
            .unwrap();
        }
        let marks = async |s: &Store| {
            s.run(|c| {
                c.query_row(
                    "SELECT (SELECT watermark_ms FROM gmail_accounts), (SELECT COUNT(*) FROM gmail_seen), (SELECT COUNT(*) FROM bot_events), (SELECT COUNT(*) FROM tasks)",
                    [],
                    |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?, r.get::<_, i64>(3)?)),
                )
            })
            .await
        };
        let before = marks(&s).await.unwrap();
        assert_eq!(s.unbatched_events("b").await.unwrap().len(), 3);
        assert_eq!(s.gmail_handoff_events("b").await.unwrap(), (3, 0));
        assert!(s.unbatched_events("b").await.unwrap().is_empty());
        assert_eq!(
            marks(&s).await.unwrap(),
            before,
            "watermark, seen, events and tasks are unchanged"
        );
        // Only the newest revision of T1 is pending, with the other revision superseded in order.
        let p = s.gmail_pending_triage("b", 10, None).await.unwrap();
        let ids: Vec<_> = p
            .iter()
            .map(|x| (x.key.thread_id.as_str(), x.key.message_id.as_str()))
            .collect();
        assert_eq!(ids, vec![(T2, MA), (T1, "00000000000000c1")]);
        // A second call and a restart change nothing.
        assert_eq!(s.gmail_handoff_events("b").await.unwrap(), (0, 0));
        assert_eq!(
            s.gmail_pending_triage("b", 10, None).await.unwrap().len(),
            2
        );
    }

    #[tokio::test]
    async fn the_handoff_marks_an_older_event_superseded_and_leaves_a_bad_payload_alone() {
        let s = Store::open_memory().unwrap();
        for (at, m) in [(1000, MA), (2000, MB)] {
            s.record_gmail_window_scan(
                "b",
                "acct1",
                vec![GmailThreadRev {
                    thread_id: T1.into(),
                    message_id: m.into(),
                }],
                GmailWindow {
                    start_ms: at,
                    lower_s: 1_000_000_000,
                },
                at,
            )
            .await
            .unwrap();
        }
        // The current window revision is seen and queued by a triage scan before the handoff.
        scan(&s, &[(T1, MB)], 3000).await;
        s.add_bot_event(
            "b",
            crate::bot::gmail_store::GMAIL_SOURCE,
            "old",
            "inbox_changed",
            format!("{{\"account\":\"acct1\",\"thread\":\"{T1}\",\"latest_message\":\"{MA}\"}}"),
            0,
        )
        .await
        .unwrap();
        s.add_bot_event(
            "b",
            crate::bot::gmail_store::GMAIL_SOURCE,
            "bad",
            "inbox_changed",
            "{\"account\":\"x\"}".into(),
            0,
        )
        .await
        .unwrap();
        assert_eq!(s.gmail_handoff_events("b").await.unwrap(), (2, 1));
        let p = s.gmail_pending_triage("b", 10, None).await.unwrap();
        assert_eq!(
            p.len(),
            1,
            "no duplicate row, and the older revision never becomes the latest"
        );
        assert_eq!(p[0].key.message_id, MB);
        assert_eq!(
            s.unbatched_events("b").await.unwrap().len(),
            1,
            "the bad payload stays unprocessed"
        );
    }

    #[tokio::test]
    async fn triage_mode_queues_rows_in_the_baseline_and_no_events() {
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA), (T2, MA)], 1000).await;
        scan(&s, &[(T1, MA), (T2, MA)], 2000).await;
        let p = s.gmail_pending_triage("b", 10, None).await.unwrap();
        assert_eq!(p.len(), 2, "a repeat of a seen revision adds no row");
        assert!(s.unbatched_events("b").await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn event_mode_is_unchanged_and_queues_no_triage() {
        let s = Store::open_memory().unwrap();
        for at in [1000, 2000] {
            s.record_gmail_window_scan(
                "b",
                "acct1",
                vec![GmailThreadRev {
                    thread_id: T1.into(),
                    message_id: if at == 1000 { MA } else { MB }.into(),
                }],
                GmailWindow {
                    start_ms: at,
                    lower_s: 1_000_000_000,
                },
                at,
            )
            .await
            .unwrap();
        }
        assert_eq!(s.unbatched_events("b").await.unwrap().len(), 1);
        assert!(
            s.gmail_pending_triage("b", 10, None)
                .await
                .unwrap()
                .is_empty()
        );
    }

    #[tokio::test]
    async fn a_failed_attempt_keeps_the_row_pending_and_a_validated_answer_ends_it_once() {
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA)], 1000).await;
        let k = key(T1, MA);
        s.gmail_triage_failed("b", &k, "model_error").await.unwrap();
        s.gmail_triage_failed("b", &k, "invalid_output")
            .await
            .unwrap();
        let p = s.gmail_pending_triage("b", 10, None).await.unwrap();
        assert_eq!((p.len(), p[0].attempts), (1, 2));
        assert_eq!(s.gmail_triage_backlog("b").await.unwrap()["acct1"], (1, 1));
        assert!(s.gmail_summary_items("b").await.unwrap().is_empty());
        assert!(
            s.gmail_triage_done("b", &k, Category::Fyi, "News", OrgRow::keep(), 5000)
                .await
                .unwrap()
        );
        assert!(
            !s.gmail_triage_done("b", &k, Category::Actionable, "Other", OrgRow::keep(), 6000)
                .await
                .unwrap()
        );
        let items = s.gmail_summary_items("b").await.unwrap();
        assert_eq!((items.len(), items[0].category), (1, Category::Fyi));
        assert!(
            s.gmail_pending_triage("b", 10, None)
                .await
                .unwrap()
                .is_empty()
        );
        s.gmail_triage_failed("b", &k, "model_error").await.unwrap();
        assert_eq!(
            s.gmail_summary_items("b").await.unwrap().len(),
            1,
            "a late failure changes nothing"
        );
    }

    #[tokio::test]
    async fn an_earlier_partial_read_is_read_again_once_and_a_second_cut_read_is_final() {
        use crate::bot::gmail_cleanup::{Action, Decision, Kind, Topic};
        let d = |action, topic, reason| {
            OrgRow::new(Decision {
                kind: Kind::Promotion,
                action,
                topic,
                reason,
            })
        };
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA), (T2, MB)], 1000).await;
        for (t, m) in [(T1, MA), (T2, MB)] {
            let org = d(Action::Review, Some(Topic::Review), "partial_read");
            s.gmail_triage_done("b", &key(t, m), Category::Fyi, "x", org, 2000)
                .await
                .unwrap();
        }
        let reason = async |t: &'static str| {
            s.run(move |c| {
                c.query_row(
                    "SELECT reason || '/' || decision || '/' || COALESCE(topic,'-') FROM gmail_organize WHERE thread_id = ?1",
                    [t],
                    |r| r.get::<_, String>(0),
                )
            })
            .await
            .unwrap()
        };
        assert_eq!(s.gmail_retry_partial_reads("b", 1).await.unwrap(), 1);
        assert_eq!(
            s.gmail_pending_triage("b", 10, None).await.unwrap().len(),
            1
        );
        assert_eq!(reason(T1).await, "partial_read_retry/review/review");
        // The retried thread reads in full and goes to Trash. The other one waits for a later pass.
        let trash = d(Action::Trash, None, "unwanted");
        assert!(
            s.gmail_triage_done("b", &key(T1, MA), Category::Fyi, "x", trash, 3000)
                .await
                .unwrap()
        );
        assert_eq!(reason(T1).await, "unwanted/trash/-");
        assert_eq!(s.gmail_retry_partial_reads("b", 5).await.unwrap(), 1);
        let cut = d(Action::Review, Some(Topic::Review), "partial_read");
        assert!(
            s.gmail_triage_done("b", &key(T2, MB), Category::Fyi, "x", cut, 3000)
                .await
                .unwrap()
        );
        assert_eq!(reason(T2).await, "partial_read_final/review/review");
        assert_eq!(
            s.gmail_retry_partial_reads("b", 5).await.unwrap(),
            0,
            "no second retry"
        );
    }

    #[tokio::test]
    async fn an_old_trash_refusal_is_read_again_once() {
        use crate::bot::gmail_cleanup::{Action, Decision, Kind};
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA)], 1000).await;
        let org = OrgRow::new(Decision {
            kind: Kind::Promotion,
            action: Action::Trash,
            topic: None,
            reason: "unwanted",
        });
        s.gmail_triage_done("b", &key(T1, MA), Category::Fyi, "x", org, 2000)
            .await
            .unwrap();
        let set = async |sql: &'static str| s.run(move |c| c.execute(sql, [])).await.unwrap();
        // The state that the old guard left: review, reason `trash_refused`, state `refused`.
        set("UPDATE gmail_organize SET decision = 'review', topic = 'review', reason = 'trash_refused', trash_state = 'refused'").await;
        assert_eq!(s.gmail_retry_refused_trashes("b", 5).await.unwrap(), 1);
        assert_eq!(
            s.gmail_pending_triage("b", 10, None).await.unwrap().len(),
            1
        );
        let again = OrgRow::new(Decision {
            kind: Kind::Promotion,
            action: Action::Trash,
            topic: None,
            reason: "unwanted",
        });
        s.gmail_triage_done("b", &key(T1, MA), Category::Fyi, "x", again, 3000)
            .await
            .unwrap();
        let row = async || {
            s.run(|c| {
                c.query_row(
                    "SELECT decision || '/' || reason || '/' || trash_state FROM gmail_organize",
                    [],
                    |r| r.get::<_, String>(0),
                )
            })
            .await
            .unwrap()
        };
        assert_eq!(row().await, "trash/unwanted/none");
        // A refusal of the guard now is final and is never read again.
        set("UPDATE gmail_organize SET decision = 'review', reason = 'trash_refused_final', trash_state = 'refused'").await;
        assert_eq!(s.gmail_retry_refused_trashes("b", 5).await.unwrap(), 0);
    }

    #[tokio::test]
    async fn a_newer_revision_supersedes_older_pending_ones_and_only_the_latest_shows() {
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA)], 1000).await;
        let a = key(T1, MA);
        assert!(
            s.gmail_triage_done("b", &a, Category::Fyi, "Old", OrgRow::keep(), 1500)
                .await
                .unwrap()
        );
        scan(&s, &[(T1, MB)], 2000).await;
        // The new revision is pending. The old one still shows until the new one is triaged.
        assert_eq!(
            s.gmail_summary_items("b").await.unwrap().len(),
            0,
            "the latest revision is pending"
        );
        let p = s.gmail_pending_triage("b", 10, None).await.unwrap();
        assert_eq!(p.len(), 1);
        assert_eq!(p[0].key.message_id, MB);
        assert!(
            s.gmail_triage_done(
                "b",
                &p[0].key,
                Category::ReplyNeeded,
                "New",
                OrgRow::keep(),
                2500
            )
            .await
            .unwrap()
        );
        let items = s.gmail_summary_items("b").await.unwrap();
        assert_eq!((items.len(), items[0].summary.as_str()), (1, "New"));
    }

    #[tokio::test]
    async fn a_pending_revision_with_a_newer_pending_one_is_superseded() {
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA)], 1000).await;
        scan(&s, &[(T1, MB)], 2000).await;
        let p = s.gmail_pending_triage("b", 10, None).await.unwrap();
        assert_eq!((p.len(), p[0].key.message_id.as_str()), (1, MB));
        let n: i64 = s
            .run(|c| {
                c.query_row(
                    "SELECT COUNT(*) FROM gmail_triage WHERE state = 'superseded'",
                    [],
                    |r| r.get(0),
                )
            })
            .await
            .unwrap();
        assert_eq!(n, 1);
    }

    #[tokio::test]
    async fn delivery_posts_once_for_a_day_and_marks_the_shown_threads() {
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA), (T2, MA)], 1000).await;
        for t in [T1, T2] {
            s.gmail_triage_done("b", &key(t, MA), Category::Fyi, "x", OrgRow::keep(), 2000)
                .await
                .unwrap();
        }
        let chat_id = chat(&s).await;
        let shown = vec![("acct1".to_string(), T1.to_string())];
        assert!(!s.gmail_summary_delivered("b", "2026-11-16").await.unwrap());
        assert!(
            s.deliver_gmail_summary("b", chat_id, "2026-11-16", "hello", &shown, 3000)
                .await
                .unwrap()
        );
        assert!(
            !s.deliver_gmail_summary("b", chat_id, "2026-11-16", "again", &shown, 4000)
                .await
                .unwrap()
        );
        assert!(s.gmail_summary_delivered("b", "2026-11-16").await.unwrap());
        let m = s.list_messages(chat_id).await.unwrap().unwrap();
        assert_eq!(m.len(), 1);
        assert!(m[0].content.contains("hello") && m[0].role == "assistant");
        let left = s.gmail_summary_items("b").await.unwrap();
        assert_eq!(
            (left.len(), left[0].key.thread_id.as_str()),
            (1, T2),
            "the unshown thread stays for the next summary"
        );
    }

    #[tokio::test]
    async fn label_progress_is_bounded_and_skips_nothing_silently() {
        let s = Store::open_memory().unwrap();
        scan(&s, &[(T1, MA)], 1000).await;
        let k = key(T1, MA);
        assert!(
            s.gmail_label_candidates("b", 10, false, None)
                .await
                .unwrap()
                .is_empty(),
            "not triaged yet"
        );
        s.gmail_triage_done("b", &k, Category::Actionable, "x", OrgRow::keep(), 2000)
            .await
            .unwrap();
        assert_eq!(
            s.gmail_label_candidates("b", 10, false, None)
                .await
                .unwrap()
                .len(),
            1
        );
        for _ in 0..LABEL_ATTEMPTS {
            s.gmail_label_result("b", &k, LabelState::Uncertain, true)
                .await
                .unwrap();
        }
        assert!(
            s.gmail_label_candidates("b", 10, false, None)
                .await
                .unwrap()
                .is_empty(),
            "attempts are capped"
        );
        assert_eq!(
            s.gmail_summary_items("b").await.unwrap()[0].label_state,
            "uncertain"
        );
        s.gmail_label_result("b", &k, LabelState::Applied, false)
            .await
            .unwrap();
        assert_eq!(
            s.gmail_summary_items("b").await.unwrap()[0].label_state,
            "applied"
        );
    }

    #[tokio::test]
    async fn a_key_with_bad_ids_is_refused() {
        let s = Store::open_memory().unwrap();
        let bad = key("ZZ", MA);
        assert!(
            s.gmail_triage_done("b", &bad, Category::Fyi, "x", OrgRow::keep(), 1)
                .await
                .is_err()
        );
        assert!(
            s.gmail_triage_done("b", &key(T1, MA), Category::Fyi, "", OrgRow::keep(), 1)
                .await
                .is_err()
        );
    }
}
