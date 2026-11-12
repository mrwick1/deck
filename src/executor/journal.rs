//! The host journal: intent before send, outcome after reply, epochs and evidence.
//!
//! Host states of a command:
//! - `intent`: recorded, nothing sent. Never dispatched.
//! - `sent`: a frame may have reached the guest. Delivery is unknown until the guest says so.
//! - `running`: the guest acknowledged it.
//! - `succeeded`, `failed`, `cancelled`, `timed_out`: the guest reported the end. These never change.
//! - `not_delivered`: the guest proved it never ran the command.
//! - `unknown`: the outcome cannot be established. Nothing retries it.

use anyhow::bail;
use rusqlite::{OptionalExtension, params};
use serde::Serialize;

use super::proto::{GuestArtifact, Receipt};
use crate::store::{Store, now};

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Cmd {
    pub command_id: String,
    pub bot_id: String,
    pub task_id: String,
    pub digest: String,
    pub argv: Vec<String>,
    pub deadline_ms: u64,
    pub state: String,
    pub detail: Option<String>,
    pub dispatch_epoch: Option<u64>,
    pub cancel_state: String,
    pub exit_code: Option<i64>,
    pub signal: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub truncated: bool,
    pub artifacts: Vec<GuestArtifact>,
}

impl Cmd {
    pub fn is_final(&self) -> bool {
        matches!(
            self.state.as_str(),
            "succeeded" | "failed" | "cancelled" | "timed_out"
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Applied {
    /// The row changed.
    Updated,
    /// The row already held this state.
    Same,
    /// The row was final or did not accept it. The report is only evidence.
    Late,
}

/// Map a guest receipt state to a host state and detail.
pub fn host_state(guest: &str) -> (&'static str, Option<&'static str>) {
    match guest {
        "running" => ("running", None),
        "succeeded" => ("succeeded", None),
        "failed" => ("failed", None),
        "cancelled" => ("cancelled", None),
        "timed_out" => ("timed_out", None),
        "interrupted" => ("unknown", Some("guest_interrupted")),
        "termination_unconfirmed" => ("unknown", Some("termination_unconfirmed")),
        _ => ("unknown", Some("unrecognized_guest_state")),
    }
}

const COLS: &str = "command_id, bot_id, task_id, digest, argv, deadline_ms, state, detail, dispatch_epoch, cancel_state, exit_code, signal, stdout, stderr, truncated, artifacts";

fn row(r: &rusqlite::Row) -> rusqlite::Result<Cmd> {
    let json = |i: usize| -> rusqlite::Result<String> { r.get(i) };
    let bad = |e: serde_json::Error| {
        rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e))
    };
    Ok(Cmd {
        command_id: r.get(0)?,
        bot_id: r.get(1)?,
        task_id: r.get(2)?,
        digest: r.get(3)?,
        argv: serde_json::from_str(&json(4)?).map_err(bad)?,
        deadline_ms: r.get(5)?,
        state: r.get(6)?,
        detail: r.get(7)?,
        dispatch_epoch: r.get(8)?,
        cancel_state: r.get(9)?,
        exit_code: r.get(10)?,
        signal: r.get(11)?,
        stdout: r.get(12)?,
        stderr: r.get(13)?,
        truncated: r.get::<_, i64>(14)? != 0,
        artifacts: serde_json::from_str(&json(15)?).map_err(bad)?,
    })
}

fn event(
    c: &rusqlite::Connection,
    bot: &str,
    cmd: Option<&str>,
    epoch: Option<u64>,
    kind: &str,
    detail: &str,
) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO executor_events(bot_id, command_id, epoch, kind, detail, created_at) VALUES (?1,?2,?3,?4,?5,?6)",
        params![bot, cmd, epoch, kind, detail, now()],
    )?;
    Ok(())
}

impl Store {
    /// Allocate the next epoch for a bot and commit it before any connection uses it.
    pub async fn next_epoch(&self, bot: String) -> anyhow::Result<u64> {
        self.run(move |c| {
            c.execute(
                "INSERT INTO executor_bots(bot_id, epoch) VALUES (?1, 1) ON CONFLICT(bot_id) DO UPDATE SET epoch = epoch + 1",
                params![bot],
            )?;
            c.query_row("SELECT epoch FROM executor_bots WHERE bot_id = ?1", params![bot], |r| r.get(0))
        })
        .await
    }

    /// Remember the guest identity on first contact. A different guest later is an error.
    pub async fn pin_guest(&self, bot: String, guest: String) -> anyhow::Result<()> {
        let pinned = self
            .run(move |c| {
                let cur: Option<String> = c.query_row(
                    "SELECT guest_id FROM executor_bots WHERE bot_id = ?1",
                    params![bot],
                    |r| r.get(0),
                )?;
                match cur {
                    None => {
                        c.execute(
                            "UPDATE executor_bots SET guest_id = ?2 WHERE bot_id = ?1",
                            params![bot, guest],
                        )?;
                        Ok(None)
                    }
                    Some(p) if p == guest => Ok(None),
                    Some(p) => Ok(Some(p)),
                }
            })
            .await?;
        if pinned.is_some() {
            bail!("the guest identity differs from the one pinned for this bot");
        }
        Ok(())
    }

    pub async fn command(&self, id: String) -> anyhow::Result<Option<Cmd>> {
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {COLS} FROM executor_commands WHERE command_id = ?1"),
                params![id],
                row,
            )
            .optional()
        })
        .await
    }

    pub async fn commands(&self, bot: String) -> anyhow::Result<Vec<Cmd>> {
        self.run(move |c| {
            c.prepare(&format!(
                "SELECT {COLS} FROM executor_commands WHERE bot_id = ?1 ORDER BY rowid"
            ))?
            .query_map(params![bot], row)?
            .collect()
        })
        .await
    }

    /// Record the intent. The same ID with the same payload returns the existing row. The same ID
    /// with a different payload or bot is an error and writes nothing.
    pub async fn begin(
        &self,
        bot: String,
        task: String,
        id: String,
        argv: Vec<String>,
        deadline_ms: u64,
        digest: String,
    ) -> anyhow::Result<Cmd> {
        let existing = self
            .run(move |c| {
                let argv_json = serde_json::to_string(&argv).unwrap_or_default();
                let prior = c
                    .query_row(&format!("SELECT {COLS} FROM executor_commands WHERE command_id = ?1"), params![id], row)
                    .optional()?;
                if let Some(p) = prior {
                    return Ok(Err(p));
                }
                c.execute("INSERT OR IGNORE INTO executor_bots(bot_id, epoch) VALUES (?1, 0)", params![bot])?;
                c.execute(
                    "INSERT INTO executor_commands(command_id, bot_id, task_id, digest, argv, deadline_ms, state, created_at) VALUES (?1,?2,?3,?4,?5,?6,'intent',?7)",
                    params![id, bot, task, digest, argv_json, deadline_ms, now()],
                )?;
                event(c, &bot, Some(&id), None, "intent", "recorded before send")?;
                Ok(Ok(c.query_row(&format!("SELECT {COLS} FROM executor_commands WHERE command_id = ?1"), params![id], row)?))
            })
            .await?;
        Ok(existing.unwrap_or_else(|prior| prior))
    }

    /// Commit "a frame may be sent" before the write. Only `intent` and `not_delivered` can go.
    pub async fn mark_sent(&self, id: String, epoch: u64) -> anyhow::Result<bool> {
        self.run(move |c| {
            let n = c.execute(
                "UPDATE executor_commands SET state='sent', dispatch_epoch=?2, sent_at=?3, detail=NULL WHERE command_id=?1 AND state IN ('intent','not_delivered')",
                params![id, epoch, now()],
            )?;
            if n == 1 {
                let bot: String = c.query_row("SELECT bot_id FROM executor_commands WHERE command_id=?1", params![id], |r| r.get(0))?;
                event(c, &bot, Some(&id), Some(epoch), "dispatch", "exec frame about to be written")?;
            }
            Ok(n == 1)
        })
        .await
    }

    /// Apply what the guest reported. A final row never changes: the report becomes evidence.
    pub async fn apply_receipt(
        &self,
        bot: String,
        epoch: u64,
        r: Receipt,
    ) -> anyhow::Result<Applied> {
        self.run(move |c| {
            let cur: Option<String> = c
                .query_row("SELECT state FROM executor_commands WHERE command_id=?1 AND bot_id=?2", params![r.command_id, bot], |x| x.get(0))
                .optional()?;
            let Some(cur) = cur else {
                event(c, &bot, Some(&r.command_id), Some(epoch), "unknown_command", &r.state)?;
                return Ok(Applied::Late);
            };
            let (new, detail) = host_state(&r.state);
            if !matches!(cur.as_str(), "sent" | "running" | "unknown") {
                event(c, &bot, Some(&r.command_id), Some(epoch), "late_result", &format!("guest says {}; host row is {cur}", r.state))?;
                return Ok(Applied::Late);
            }
            if cur == new && detail.is_none() {
                return Ok(Applied::Same);
            }
            if cur == "unknown" && new == "unknown" {
                return Ok(Applied::Same);
            }
            let fin = !matches!(new, "running" | "unknown");
            let cancel = match (r.state.as_str(), new) {
                ("cancelled", _) => Some("confirmed"),
                ("termination_unconfirmed", _) => Some("unconfirmed"),
                _ => None,
            };
            c.execute(
                "UPDATE executor_commands SET state=?2, detail=?3, exit_code=?4, signal=?5, stdout=?6, stderr=?7, truncated=?8, artifacts=?9,
                 cancel_state=COALESCE(?10, cancel_state), finished_at=CASE WHEN ?11 THEN ?12 ELSE finished_at END WHERE command_id=?1",
                params![
                    r.command_id, new, detail.map(str::to_string).or(r.detail.clone()), r.exit_code, r.signal, r.stdout, r.stderr,
                    i64::from(r.truncated), serde_json::to_string(&r.artifacts).unwrap_or_default(), cancel, fin, now()
                ],
            )?;
            event(c, &bot, Some(&r.command_id), Some(epoch), "guest_report", &format!("{} -> {new}", r.state))?;
            Ok(Applied::Updated)
        })
        .await
    }

    pub async fn set_cancel(&self, id: String, to: &'static str) -> anyhow::Result<()> {
        self.run(move |c| {
            // Requested -> accepted -> confirmed only moves forward.
            c.execute(
                "UPDATE executor_commands SET cancel_state=?2 WHERE command_id=?1 AND (
                   (?2='requested' AND cancel_state='none') OR (?2='accepted' AND cancel_state IN ('none','requested')))",
                params![id, to],
            )?;
            Ok(())
        })
        .await
    }

    /// Settle a non-final row without guest evidence of its own.
    pub async fn settle(
        &self,
        id: String,
        state: &'static str,
        detail: String,
        epoch: u64,
    ) -> anyhow::Result<bool> {
        self.run(move |c| {
            let n = c.execute(
                "UPDATE executor_commands SET state=?2, detail=?3 WHERE command_id=?1 AND state IN ('sent','running')",
                params![id, state, detail],
            )?;
            if n == 1 {
                let bot: String = c.query_row("SELECT bot_id FROM executor_commands WHERE command_id=?1", params![id], |r| r.get(0))?;
                event(c, &bot, Some(&id), Some(epoch), "reconcile", &format!("{state}: {detail}"))?;
            }
            Ok(n == 1)
        })
        .await
    }

    pub async fn log(
        &self,
        bot: String,
        id: Option<String>,
        epoch: Option<u64>,
        kind: &'static str,
        detail: String,
    ) -> anyhow::Result<()> {
        self.run(move |c| event(c, &bot, id.as_deref(), epoch, kind, &detail))
            .await
    }

    pub async fn events(
        &self,
        bot: String,
        id: Option<String>,
    ) -> anyhow::Result<Vec<(String, Option<u64>, String)>> {
        self.run(move |c| {
            c.prepare("SELECT kind, epoch, detail FROM executor_events WHERE bot_id=?1 AND (?2 IS NULL OR command_id=?2) ORDER BY id")?
                .query_map(params![bot, id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .collect()
        })
        .await
    }

    pub async fn record_artifact(
        &self,
        id: String,
        a: GuestArtifact,
        host_path: String,
    ) -> anyhow::Result<()> {
        self.run(move |c| {
            c.execute(
                "INSERT OR REPLACE INTO executor_artifacts(command_id, handle, name, size, sha256, host_path) VALUES (?1,?2,?3,?4,?5,?6)",
                params![id, a.handle, a.name, a.size, a.sha256, host_path],
            )?;
            Ok(())
        })
        .await
    }

    pub async fn stored_artifacts(&self, id: String) -> anyhow::Result<Vec<(String, String)>> {
        self.run(move |c| {
            c.prepare("SELECT handle, host_path FROM executor_artifacts WHERE command_id=?1 ORDER BY handle")?
                .query_map(params![id], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect()
        })
        .await
    }
}
