//! Durable bot state: flags, tasks, steering, events and owner decisions.

use std::collections::HashSet;

use rusqlite::{OptionalExtension, Row, params};
use serde::Serialize;

use crate::executor::journal::Cmd;
use crate::store::{Store, now};

#[cfg(test)]
pub const LOW: i64 = 0;
pub const NORMAL: i64 = 1;
pub const HIGH: i64 = 2;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Flags {
    pub chat_id: i64,
    pub automation_paused: bool,
    pub budget_hold: bool,
    pub budget_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Task {
    pub id: String,
    pub chat_id: i64,
    pub source: String,
    pub title: String,
    pub body: String,
    pub priority: i64,
    pub state: String,
    pub detail: Option<String>,
    pub run_id: Option<i64>,
    pub created_at: String,
}

const TASK_COLS: &str =
    "id, chat_id, source, title, body, priority, state, detail, run_id, created_at";

fn task_row(r: &Row) -> rusqlite::Result<Task> {
    Ok(Task {
        id: r.get(0)?,
        chat_id: r.get(1)?,
        source: r.get(2)?,
        title: r.get(3)?,
        body: r.get(4)?,
        priority: r.get(5)?,
        state: r.get(6)?,
        detail: r.get(7)?,
        run_id: r.get(8)?,
        created_at: r.get(9)?,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct BotEvent {
    pub id: i64,
    pub source: String,
    pub event_id: String,
    pub kind: String,
    pub payload: String,
    pub priority: i64,
    pub received_ms: i64,
}

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}

impl Store {
    /// The flags of a bot. The first call creates the bot with a chat of its own.
    pub async fn bot_flags(&self, bot: &str, title: &str) -> anyhow::Result<Flags> {
        let (bot, title) = (bot.to_owned(), title.to_owned());
        self.run(move |c| {
            let q = "SELECT chat_id, automation_paused, budget_hold, budget_reason FROM bot_state WHERE bot_id = ?1";
            let get = |r: &Row| {
                Ok(Flags {
                    chat_id: r.get(0)?,
                    automation_paused: r.get::<_, i64>(1)? != 0,
                    budget_hold: r.get::<_, i64>(2)? != 0,
                    budget_reason: r.get(3)?,
                })
            };
            if let Some(f) = c.query_row(q, [&bot], get).optional()? {
                return Ok(f);
            }
            c.execute(
                "INSERT INTO chats(title, created_at) VALUES (?1, ?2)",
                params![title, now()],
            )?;
            c.execute(
                "INSERT INTO bot_state(bot_id, chat_id) VALUES (?1, ?2)",
                params![bot, c.last_insert_rowid()],
            )?;
            c.query_row(q, [&bot], get)
        })
        .await
    }

    pub async fn set_paused(&self, bot: &str, paused: bool) -> anyhow::Result<()> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.execute(
                "UPDATE bot_state SET automation_paused = ?2 WHERE bot_id = ?1",
                params![bot, paused],
            )?;
            Ok(())
        })
        .await
    }

    /// Set or clear the budget hold. Only the owner's approval route clears it.
    pub async fn set_budget_hold(&self, bot: &str, reason: Option<String>) -> anyhow::Result<()> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.execute(
                "UPDATE bot_state SET budget_hold = ?2, budget_reason = ?3 WHERE bot_id = ?1",
                params![bot, reason.is_some(), reason],
            )?;
            Ok(())
        })
        .await
    }

    pub async fn add_task(
        &self,
        bot: &str,
        chat_id: i64,
        source: &str,
        title: String,
        body: String,
        priority: i64,
    ) -> anyhow::Result<Task> {
        let (bot, source) = (bot.to_owned(), source.to_owned());
        self.run(move |c| {
            let id = uuid::Uuid::new_v4().simple().to_string();
            c.execute(
                "INSERT INTO tasks(id, bot_id, chat_id, source, title, body, priority, state, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7,'queued',?8)",
                params![id, bot, chat_id, source, title, body, priority, now()],
            )?;
            c.query_row(
                &format!("SELECT {TASK_COLS} FROM tasks WHERE id = ?1"),
                [&id],
                task_row,
            )
        })
        .await
    }

    pub async fn task(&self, id: &str) -> anyhow::Result<Option<Task>> {
        let id = id.to_owned();
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {TASK_COLS} FROM tasks WHERE id = ?1"),
                [&id],
                task_row,
            )
            .optional()
        })
        .await
    }

    /// Open tasks first, then the most recent finished ones.
    pub async fn tasks(&self, bot: &str, finished: usize) -> anyhow::Result<Vec<Task>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let open = c
                .prepare(&format!("SELECT {TASK_COLS} FROM tasks WHERE bot_id = ?1 AND state IN ('queued','running','waiting','held') ORDER BY seq"))?
                .query_map([&bot], task_row)?
                .collect::<Result<Vec<_>, _>>()?;
            let done = c
                .prepare(&format!("SELECT {TASK_COLS} FROM tasks WHERE bot_id = ?1 AND state IN ('done','failed','cancelled') ORDER BY seq DESC LIMIT ?2"))?
                .query_map(params![bot, finished as i64], task_row)?
                .collect::<Result<Vec<_>, _>>()?;
            Ok([open, done].concat())
        })
        .await
    }

    /// The next task to run: highest priority first, then oldest.
    pub async fn next_queued(&self, bot: &str) -> anyhow::Result<Option<Task>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {TASK_COLS} FROM tasks WHERE bot_id = ?1 AND state = 'queued' ORDER BY priority DESC, seq LIMIT 1"),
                [&bot],
                task_row,
            )
            .optional()
        })
        .await
    }

    /// The task that owns the desktop: running or waiting for the owner.
    pub async fn owner_task(&self, bot: &str) -> anyhow::Result<Option<Task>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {TASK_COLS} FROM tasks WHERE bot_id = ?1 AND state IN ('running','waiting') ORDER BY seq LIMIT 1"),
                [&bot],
                task_row,
            )
            .optional()
        })
        .await
    }

    /// Move a task to `to` when it is in one of the `from` states. Returns whether it moved.
    pub async fn move_task(
        &self,
        id: &str,
        from: &[&str],
        to: &str,
        detail: Option<String>,
    ) -> anyhow::Result<bool> {
        let (id, from, to) = (
            id.to_owned(),
            from.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            to.to_owned(),
        );
        self.run(move |c| {
            let marks = vec!["?"; from.len()].join(",");
            let mut args: Vec<Box<dyn rusqlite::ToSql>> = vec![
                Box::new(to.clone()),
                Box::new(detail),
                Box::new(matches!(to.as_str(), "done" | "failed" | "cancelled").then(now)),
                Box::new(id),
            ];
            args.extend(
                from.iter()
                    .map(|s| Box::new(s.clone()) as Box<dyn rusqlite::ToSql>),
            );
            let n = c.execute(
                &format!("UPDATE tasks SET state = ?1, detail = ?2, ended_at = ?3 WHERE id = ?4 AND state IN ({marks})"),
                rusqlite::params_from_iter(args.iter().map(|a| a.as_ref())),
            )?;
            Ok(n == 1)
        })
        .await
    }

    pub async fn set_task_run(&self, id: &str, run_id: i64) -> anyhow::Result<()> {
        let id = id.to_owned();
        self.run(move |c| {
            c.execute(
                "UPDATE tasks SET run_id = ?2 WHERE id = ?1",
                params![id, run_id],
            )?;
            Ok(())
        })
        .await
    }

    pub async fn set_task_priority(&self, id: &str, priority: i64) -> anyhow::Result<bool> {
        let id = id.to_owned();
        self.run(move |c| {
            Ok(c.execute(
                "UPDATE tasks SET priority = ?2 WHERE id = ?1 AND state IN ('queued','held')",
                params![id, priority],
            )? == 1)
        })
        .await
    }

    /// Requeue the tasks that a budget hold stopped, after the owner approved.
    pub async fn requeue_held(&self, bot: &str) -> anyhow::Result<usize> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.execute(
                "UPDATE tasks SET state = 'queued', detail = NULL WHERE bot_id = ?1 AND state = 'held' AND detail = 'budget hold'",
                [&bot],
            )
        })
        .await
    }

    pub async fn add_steering(&self, task_id: &str, text: &str) -> anyhow::Result<()> {
        let (t, x) = (task_id.to_owned(), text.to_owned());
        self.run(move |c| {
            c.execute(
                "INSERT INTO steering(task_id, text, created_at) VALUES (?1,?2,?3)",
                params![t, x, now()],
            )?;
            Ok(())
        })
        .await
    }

    /// Texts not yet shown to the model, oldest first. Each text is delivered once.
    pub async fn take_steering(&self, task_id: &str) -> anyhow::Result<Vec<String>> {
        let t = task_id.to_owned();
        self.run(move |c| {
            let texts = c
                .prepare(
                    "SELECT text FROM steering WHERE task_id = ?1 AND delivered = 0 ORDER BY id",
                )?
                .query_map([&t], |r| r.get::<_, String>(0))?
                .collect::<Result<Vec<_>, _>>()?;
            c.execute(
                "UPDATE steering SET delivered = 1 WHERE task_id = ?1 AND delivered = 0",
                [&t],
            )?;
            Ok(texts)
        })
        .await
    }

    /// Store an event once. Returns false for an event ID that the source sent before.
    pub async fn add_bot_event(
        &self,
        bot: &str,
        source: &str,
        event_id: &str,
        kind: &str,
        payload: String,
        priority: i64,
    ) -> anyhow::Result<Option<i64>> {
        let (bot, source, event_id, kind) = (
            bot.to_owned(),
            source.to_owned(),
            event_id.to_owned(),
            kind.to_owned(),
        );
        self.run(move |c| {
            let n = c.execute(
                "INSERT OR IGNORE INTO bot_events(bot_id, source, event_id, kind, payload, priority, received_ms) VALUES (?1,?2,?3,?4,?5,?6,?7)",
                params![bot, source, event_id, kind, payload, priority, now_ms()],
            )?;
            Ok((n == 1).then(|| c.last_insert_rowid()))
        })
        .await
    }

    pub async fn unbatched_events(&self, bot: &str) -> anyhow::Result<Vec<BotEvent>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.prepare("SELECT id, source, event_id, kind, payload, priority, received_ms FROM bot_events WHERE bot_id = ?1 AND task_id IS NULL ORDER BY id")?
                .query_map([&bot], |r| {
                    Ok(BotEvent {
                        id: r.get(0)?,
                        source: r.get(1)?,
                        event_id: r.get(2)?,
                        kind: r.get(3)?,
                        payload: r.get(4)?,
                        priority: r.get(5)?,
                        received_ms: r.get(6)?,
                    })
                })?
                .collect()
        })
        .await
    }

    /// Create the task for a batch and mark its events in one transaction.
    pub async fn batch_events(
        &self,
        bot: &str,
        chat_id: i64,
        ids: Vec<i64>,
        title: String,
        body: String,
        priority: i64,
    ) -> anyhow::Result<Task> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            let id = uuid::Uuid::new_v4().simple().to_string();
            tx.execute(
                "INSERT INTO tasks(id, bot_id, chat_id, source, title, body, priority, state, created_at) VALUES (?1,?2,?3,'event',?4,?5,?6,'queued',?7)",
                params![id, bot, chat_id, title, body, priority, now()],
            )?;
            for e in &ids {
                tx.execute("UPDATE bot_events SET task_id = ?2 WHERE id = ?1", params![e, id])?;
            }
            let t = tx.query_row(
                &format!("SELECT {TASK_COLS} FROM tasks WHERE id = ?1"),
                [&id],
                task_row,
            )?;
            tx.commit()?;
            Ok(t)
        })
        .await
    }

    pub async fn decide(&self, command_id: &str, decision: &str, note: &str) -> anyhow::Result<()> {
        let (c_id, d, n) = (command_id.to_owned(), decision.to_owned(), note.to_owned());
        self.run(move |c| {
            c.execute(
                "INSERT OR REPLACE INTO inbox_decisions(command_id, decision, note, created_at) VALUES (?1,?2,?3,?4)",
                params![c_id, d, n, now()],
            )?;
            Ok(())
        })
        .await
    }

    /// Commands whose outcome is not settled and that the owner has not decided on.
    /// They block the idle save and the replay of their task. The journal row never changes.
    pub async fn unresolved(&self, bot: &str) -> anyhow::Result<Vec<Cmd>> {
        let decided: HashSet<String> = self
            .run(|c| {
                c.prepare("SELECT command_id FROM inbox_decisions")?
                    .query_map([], |r| r.get(0))?
                    .collect()
            })
            .await?;
        Ok(self
            .commands(bot.to_owned())
            .await?
            .into_iter()
            .filter(|c| matches!(c.state.as_str(), "sent" | "running" | "unknown"))
            .filter(|c| !decided.contains(&c.command_id))
            .collect())
    }

    /// The payload of the newest event of a run, optionally of one kind.
    pub async fn last_event_payload(
        &self,
        run_id: i64,
        kind: Option<&str>,
    ) -> anyhow::Result<Option<String>> {
        let kind = kind.map(str::to_owned);
        self.run(move |c| {
            c.query_row(
                "SELECT payload FROM events WHERE run_id = ?1 AND (?2 IS NULL OR kind = ?2) ORDER BY id DESC LIMIT 1",
                params![run_id, kind],
                |r| r.get(0),
            )
            .optional()
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::temp_dir;

    async fn bot() -> (Store, Flags) {
        let s = Store::open_memory().unwrap();
        let f = s.bot_flags("b", "Bot").await.unwrap();
        (s, f)
    }

    #[tokio::test]
    async fn queue_order_is_priority_then_arrival() {
        let (s, f) = bot().await;
        for (t, p) in [
            ("low1", LOW),
            ("norm1", NORMAL),
            ("high1", HIGH),
            ("norm2", NORMAL),
            ("high2", HIGH),
        ] {
            s.add_task("b", f.chat_id, "message", t.into(), t.into(), p)
                .await
                .unwrap();
        }
        let mut order = vec![];
        while let Some(t) = s.next_queued("b").await.unwrap() {
            order.push(t.title.clone());
            assert!(s.move_task(&t.id, &["queued"], "done", None).await.unwrap());
        }
        assert_eq!(order, ["high1", "high2", "norm1", "norm2", "low1"]);
    }

    #[tokio::test]
    async fn a_state_change_needs_the_expected_state() {
        let (s, f) = bot().await;
        let t = s
            .add_task("b", f.chat_id, "message", "t".into(), "t".into(), NORMAL)
            .await
            .unwrap();
        assert!(
            s.move_task(&t.id, &["queued"], "running", None)
                .await
                .unwrap()
        );
        assert!(
            !s.move_task(&t.id, &["queued"], "cancelled", None)
                .await
                .unwrap()
        );
        assert_eq!(s.task(&t.id).await.unwrap().unwrap().state, "running");
        assert_eq!(s.owner_task("b").await.unwrap().unwrap().id, t.id);
    }

    #[tokio::test]
    async fn steering_is_delivered_once_in_order() {
        let (s, _) = bot().await;
        s.add_steering("t1", "first").await.unwrap();
        s.add_steering("t1", "second").await.unwrap();
        s.add_steering("t2", "other").await.unwrap();
        assert_eq!(s.take_steering("t1").await.unwrap(), ["first", "second"]);
        assert!(s.take_steering("t1").await.unwrap().is_empty());
        assert_eq!(s.take_steering("t2").await.unwrap(), ["other"]);
    }

    #[tokio::test]
    async fn duplicate_event_ids_are_stored_once_per_source() {
        let (s, _) = bot().await;
        assert!(
            s.add_bot_event("b", "mail", "e1", "k", "{}".into(), LOW)
                .await
                .unwrap()
                .is_some()
        );
        assert!(
            s.add_bot_event("b", "mail", "e1", "k", "{}".into(), LOW)
                .await
                .unwrap()
                .is_none()
        );
        assert!(
            s.add_bot_event("b", "jobs", "e1", "k", "{}".into(), LOW)
                .await
                .unwrap()
                .is_some()
        );
        assert_eq!(s.unbatched_events("b").await.unwrap().len(), 2);
    }

    #[tokio::test]
    async fn batching_marks_events_and_creates_one_task_atomically() {
        let (s, f) = bot().await;
        let a = s
            .add_bot_event("b", "mail", "1", "k", "{}".into(), LOW)
            .await
            .unwrap()
            .unwrap();
        let b = s
            .add_bot_event("b", "mail", "2", "k", "{}".into(), HIGH)
            .await
            .unwrap()
            .unwrap();
        let t = s
            .batch_events(
                "b",
                f.chat_id,
                vec![a, b],
                "2 mail".into(),
                "x".into(),
                HIGH,
            )
            .await
            .unwrap();
        assert_eq!(
            (t.source.as_str(), t.state.as_str(), t.priority),
            ("event", "queued", HIGH)
        );
        assert!(s.unbatched_events("b").await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn state_survives_a_restart() {
        let dir = temp_dir();
        let id = {
            let s = Store::open(&dir).unwrap();
            let f = s.bot_flags("b", "Bot").await.unwrap();
            s.set_paused("b", true).await.unwrap();
            s.set_budget_hold("b", Some("limit".into())).await.unwrap();
            let t = s
                .add_task("b", f.chat_id, "message", "t".into(), "t".into(), HIGH)
                .await
                .unwrap();
            s.move_task(&t.id, &["queued"], "waiting", None)
                .await
                .unwrap();
            s.add_bot_event("b", "mail", "e", "k", "{}".into(), LOW)
                .await
                .unwrap();
            t.id
        };
        let s = Store::open(&dir).unwrap();
        let f = s.bot_flags("b", "Bot").await.unwrap();
        assert!(f.automation_paused && f.budget_hold);
        assert_eq!(f.budget_reason.as_deref(), Some("limit"));
        assert_eq!(s.owner_task("b").await.unwrap().unwrap().id, id);
        assert_eq!(s.unbatched_events("b").await.unwrap().len(), 1);
        // The same bot keeps its chat.
        assert_eq!(s.bot_flags("b", "Other").await.unwrap().chat_id, f.chat_id);
    }

    #[tokio::test]
    async fn budget_requeue_only_touches_budget_held_tasks() {
        let (s, f) = bot().await;
        let a = s
            .add_task("b", f.chat_id, "message", "a".into(), "a".into(), NORMAL)
            .await
            .unwrap();
        let b = s
            .add_task("b", f.chat_id, "message", "b".into(), "b".into(), NORMAL)
            .await
            .unwrap();
        s.move_task(&a.id, &["queued"], "held", Some("budget hold".into()))
            .await
            .unwrap();
        s.move_task(
            &b.id,
            &["queued"],
            "held",
            Some("interrupted by a host restart".into()),
        )
        .await
        .unwrap();
        assert_eq!(s.requeue_held("b").await.unwrap(), 1);
        assert_eq!(s.task(&a.id).await.unwrap().unwrap().state, "queued");
        assert_eq!(s.task(&b.id).await.unwrap().unwrap().state, "held");
    }
}
