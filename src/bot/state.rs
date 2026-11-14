//! Durable bot definitions, setup drafts, typed holds and the accounting ledger.
//!
//! Every operation here is an owner-control or runtime storage primitive. No bot tool
//! calls them. A raw shell or browser tool is not constrained by this module (D33).

// Stages 2 and 3 call this seam. Until they land, nothing in the binary uses it.
#![allow(dead_code)]

use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior, params};
use serde::Serialize;

use crate::store::{Store, now};

/// The result of a versioned or identified write. Nothing is applied except for `Applied`.
#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    /// Applied. The number is the new version, or the remaining allowance for `reserve` and `settle`.
    Applied(i64),
    /// The identity was applied before. The number is the same kind as for `Applied`, as of now.
    Duplicate(i64),
    /// The caller's expected version is old. The number is the current version.
    Stale(i64),
    /// A reservation does not fit the working allowance. The number is the remaining allowance.
    Insufficient(i64),
    /// The bot has no accounting policy. Missing policy is never zero cost.
    Unconfigured,
    /// The request contradicts stored state or is invalid.
    Conflict(String),
}

fn conflict(msg: &str) -> Outcome {
    Outcome::Conflict(msg.to_owned())
}

fn overflow() -> rusqlite::Error {
    rusqlite::Error::ToSqlConversionFailure("monetary overflow".into())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HoldKind {
    AutomationPause,
    Budget,
    Ceiling,
    Investigation,
    UnknownOutcome,
}

impl HoldKind {
    /// The `bot_holds` value of the kinds that table stores. Pause and budget keep their `bot_state` flags.
    fn stored(self) -> Option<&'static str> {
        match self {
            Self::Ceiling => Some("ceiling"),
            Self::Investigation => Some("investigation"),
            Self::UnknownOutcome => Some("unknown_outcome"),
            Self::AutomationPause | Self::Budget => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Hold {
    pub kind: HoldKind,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Authority {
    Full,
    Restricted,
    Revoked,
}

impl Authority {
    fn as_str(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::Restricted => "restricted",
            Self::Revoked => "revoked",
        }
    }

    fn parse(s: &str) -> Self {
        match s {
            "full" => Self::Full,
            "restricted" => Self::Restricted,
            _ => Self::Revoked,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Definition {
    pub bot_id: String,
    pub version: i64,
    pub instructions: String,
    pub authority: Authority,
    pub restrictions: Vec<String>,
    pub actor: String,
}

/// Explicit accounting configuration. Amounts are integer minor units: `exponent` is the decimal places.
#[derive(Debug, Clone)]
pub struct AccountingConfig<'a> {
    pub currency: &'a str,
    pub exponent: u8,
    pub period: &'a str,
    pub policy_version: &'a str,
    pub ceiling: i64,
    pub allowance: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Accounting {
    pub currency: String,
    pub exponent: u8,
    pub period: String,
    pub policy_version: String,
    pub ceiling: i64,
    pub allowance: i64,
    pub version: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Exposure {
    pub period: String,
    /// Reserved, not yet settled or flagged unresolved.
    pub reserved: i64,
    /// Reserved with an unknown outcome. Counts in full until settled.
    pub unresolved: i64,
    pub settled: i64,
    /// Allowance minus all exposure. Negative after an overrun.
    pub remaining: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Change {
    pub change_id: String,
    pub kind: String,
    pub old: i64,
    pub new: i64,
    pub reason: String,
    pub actor: String,
    pub version: i64,
}

fn accounting_row(c: &Connection, bot: &str) -> rusqlite::Result<Option<Accounting>> {
    c.query_row(
        "SELECT currency, exponent, period, policy_version, ceiling, allowance, version FROM bot_accounting WHERE bot_id = ?1",
        [bot],
        |r| {
            Ok(Accounting {
                currency: r.get(0)?,
                exponent: r.get(1)?,
                period: r.get(2)?,
                policy_version: r.get(3)?,
                ceiling: r.get(4)?,
                allowance: r.get(5)?,
                version: r.get(6)?,
            })
        },
    )
    .optional()
}

fn exposure_of(c: &Connection, a: &Accounting, bot: &str) -> rusqlite::Result<Exposure> {
    let sum = |state: &str, col: &str| -> rusqlite::Result<i64> {
        c.query_row(
            &format!("SELECT COALESCE(SUM({col}), 0) FROM accounting_reservations WHERE bot_id = ?1 AND period = ?2 AND state = ?3"),
            params![bot, a.period, state],
            |r| r.get(0),
        )
    };
    let (reserved, unresolved, settled) = (
        sum("reserved", "amount")?,
        sum("unresolved", "amount")?,
        sum("settled", "settled_amount")?,
    );
    let total = reserved
        .checked_add(unresolved)
        .and_then(|t| t.checked_add(settled))
        .ok_or_else(overflow)?;
    Ok(Exposure {
        period: a.period.clone(),
        reserved,
        unresolved,
        settled,
        remaining: a.allowance.checked_sub(total).ok_or_else(overflow)?,
    })
}

fn remaining(c: &Connection, bot: &str) -> rusqlite::Result<i64> {
    match accounting_row(c, bot)? {
        Some(a) => Ok(exposure_of(c, &a, bot)?.remaining),
        None => Ok(0),
    }
}

fn immediate(c: &Connection) -> rusqlite::Result<Transaction<'_>> {
    Transaction::new_unchecked(c, TransactionBehavior::Immediate)
}

/// An earlier change with this identity, if any: its version, or a conflict for another bot.
fn earlier_change(t: &Transaction, bot: &str, id: &str) -> rusqlite::Result<Option<Outcome>> {
    let row: Option<(String, i64)> = t
        .query_row(
            "SELECT bot_id, version FROM accounting_changes WHERE change_id = ?1",
            [id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    Ok(row.map(|(b, v)| {
        if b == bot {
            Outcome::Duplicate(v)
        } else {
            conflict("change id belongs to another bot")
        }
    }))
}

#[allow(clippy::too_many_arguments)]
fn record_change(
    t: &Transaction,
    bot: &str,
    id: &str,
    kind: &str,
    old: i64,
    new: i64,
    reason: &str,
    actor: &str,
    version: i64,
) -> rusqlite::Result<()> {
    t.execute(
        "INSERT INTO accounting_changes(change_id, bot_id, kind, old_value, new_value, reason, actor, version, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
        params![id, bot, kind, old, new, reason, actor, version, now()],
    )?;
    Ok(())
}

fn ceiling_row(
    c: &Connection,
    bot: &str,
) -> rusqlite::Result<Option<crate::bot::authority::PendingCeilingRequest>> {
    c.query_row("SELECT request_id, version, currency, exponent, ceiling, reason FROM ceiling_requests WHERE bot_id = ?1 AND state = 'pending'", [bot], |r| {
        Ok(crate::bot::authority::PendingCeilingRequest { id: r.get(0)?, version: r.get(1)?, currency: r.get(2)?, precision: r.get(3)?, current_ceiling_minor: r.get(4)?, reason: r.get(5)? })
    }).optional()
}

impl Store {
    /// Report an actual charge overrun without leaving the hold disconnected from owner control.
    pub async fn require_ceiling_review(&self, bot: &str, reason: &str) -> anyhow::Result<()> {
        let (bot, reason) = (bot.to_owned(), reason.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            let a = accounting_row(&t, &bot)?.ok_or(rusqlite::Error::QueryReturnedNoRows)?;
            t.execute("INSERT INTO bot_holds(bot_id, kind, reason, created_at) VALUES (?1,'ceiling',?2,?3) ON CONFLICT(bot_id,kind) DO UPDATE SET reason=excluded.reason", params![bot, reason, now()])?;
            t.execute("INSERT INTO ceiling_requests(request_id, bot_id, version, currency, exponent, ceiling, reason, state, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7,'pending',?8) ON CONFLICT DO NOTHING", params![uuid::Uuid::new_v4().to_string(), bot, a.version, a.currency, a.exponent, a.ceiling, reason, now()])?;
            t.commit()?;
            Ok(())
        }).await
    }

    pub async fn pending_ceiling(
        &self,
        bot: &str,
    ) -> anyhow::Result<Option<crate::bot::authority::PendingCeilingRequest>> {
        let bot = bot.to_owned();
        self.run(move |c| ceiling_row(c, &bot)).await
    }

    /// Apply an authenticated owner response in the same transaction as its receipt.
    pub async fn answer_ceiling(
        &self,
        bot: &str,
        actor: &str,
        reply: &crate::bot::authority::CeilingResponse,
    ) -> anyhow::Result<Outcome> {
        let (bot, actor, reply) = (bot.to_owned(), actor.to_owned(), reply.clone());
        let payload = serde_json::to_string(&reply)?;
        self.run(move |c| {
            let t = immediate(c)?;
            let prior: Option<(String, String, String, i64)> = t.query_row(
                "SELECT bot_id, actor, payload, version FROM ceiling_responses WHERE response_id = ?1", [&reply.idempotency_key],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            ).optional()?;
            if let Some((b, by, body, version)) = prior {
                return Ok(if b == bot && by == actor && body == payload { Outcome::Duplicate(version) }
                    else { conflict("response identity reused with different content") });
            }
            let Some(pending) = ceiling_row(&t, &bot)? else { return Ok(conflict("no pending ceiling request")) };
            if crate::bot::authority::check(&reply, &pending).is_err() { return Ok(conflict("ceiling request changed or response is invalid")); }
            let Some(a) = accounting_row(&t, &bot)? else { return Ok(Outcome::Unconfigured) };
            if u64::try_from(a.version).ok() != Some(pending.version) { return Ok(Outcome::Stale(a.version)); }
            let mut version = a.version;
            if reply.decision == crate::bot::authority::Decision::Approve {
                let Some(ceiling) = reply.amount_minor.and_then(|n| i64::try_from(n).ok()) else { return Ok(conflict("ceiling amount is out of range")) };
                version = version.checked_add(1).ok_or_else(overflow)?;
                t.execute("UPDATE bot_accounting SET ceiling = ?2, version = ?3 WHERE bot_id = ?1", params![bot, ceiling, version])?;
                record_change(&t, &bot, &format!("ceiling-response:{}", reply.idempotency_key), "ceiling", a.ceiling, ceiling, "owner approved ceiling increase", &actor, version)?;
                t.execute("UPDATE ceiling_requests SET state = 'approved' WHERE request_id = ?1", [&reply.request_id])?;
                t.execute("DELETE FROM bot_holds WHERE bot_id = ?1 AND kind = 'ceiling'", [&bot])?;
                // Only tasks held by model accounting become eligible again. Other gates remain independent.
                t.execute("UPDATE tasks SET state = 'queued', detail = NULL WHERE bot_id = ?1 AND state = 'held' AND detail = 'budget hold'", [&bot])?;
            }
            t.execute("INSERT INTO ceiling_responses(response_id, request_id, bot_id, actor, payload, version, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7)", params![reply.idempotency_key, reply.request_id, bot, actor, payload, version, now()])?;
            t.commit()?;
            Ok(Outcome::Applied(version))
        }).await
    }

    // ---- typed holds ----

    /// Every active hold. Pause and budget read the legacy `bot_state` flags, so old holds stay held.
    pub async fn holds(&self, bot: &str) -> anyhow::Result<Vec<Hold>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let mut out = Vec::new();
            let flags: Option<(i64, i64, Option<String>)> = c
                .query_row(
                    "SELECT automation_paused, budget_hold, budget_reason FROM bot_state WHERE bot_id = ?1",
                    [&bot],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            if let Some((paused, budget, reason)) = flags {
                if paused != 0 {
                    out.push(Hold { kind: HoldKind::AutomationPause, reason: None });
                }
                if budget != 0 {
                    out.push(Hold { kind: HoldKind::Budget, reason });
                }
            }
            let mut q = c.prepare("SELECT kind, reason FROM bot_holds WHERE bot_id = ?1 ORDER BY kind")?;
            let rows = q.query_map([&bot], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
            for row in rows {
                let (kind, reason) = row?;
                let kind = match kind.as_str() {
                    "ceiling" => HoldKind::Ceiling,
                    "investigation" => HoldKind::Investigation,
                    _ => HoldKind::UnknownOutcome,
                };
                out.push(Hold { kind, reason: Some(reason) });
            }
            Ok(out)
        })
        .await
    }

    /// Set one hold. Other kinds stay as they are.
    pub async fn set_hold(&self, bot: &str, kind: HoldKind, reason: &str) -> anyhow::Result<()> {
        match kind {
            HoldKind::AutomationPause => self.set_paused(bot, true).await,
            HoldKind::Budget => self.set_budget_hold(bot, Some(reason.to_owned())).await,
            _ => {
                let (bot, reason) = (bot.to_owned(), reason.to_owned());
                let kind = kind.stored().unwrap_or_default();
                self.run(move |c| {
                    c.execute(
                        "INSERT INTO bot_holds(bot_id, kind, reason, created_at) VALUES (?1,?2,?3,?4)
                         ON CONFLICT(bot_id, kind) DO UPDATE SET reason = excluded.reason",
                        params![bot, kind, reason, now()],
                    )?;
                    Ok(())
                })
                .await
            }
        }
    }

    /// Clear one hold. Other kinds stay as they are. An allowance change calls none of this.
    pub async fn clear_hold(&self, bot: &str, kind: HoldKind) -> anyhow::Result<()> {
        match kind {
            HoldKind::AutomationPause => self.set_paused(bot, false).await,
            HoldKind::Budget => self.set_budget_hold(bot, None).await,
            _ => {
                let bot = bot.to_owned();
                let kind = kind.stored().unwrap_or_default();
                self.run(move |c| {
                    c.execute(
                        "DELETE FROM bot_holds WHERE bot_id = ?1 AND kind = ?2",
                        params![bot, kind],
                    )?;
                    Ok(())
                })
                .await
            }
        }
    }

    // ---- setup drafts and definitions ----

    /// Create (`expected` = None) or edit a draft. A draft never grants authority.
    pub async fn save_draft(
        &self,
        draft: &str,
        bot: &str,
        expected: Option<i64>,
        body: &str,
    ) -> anyhow::Result<Outcome> {
        let (draft, bot, body) = (draft.to_owned(), bot.to_owned(), body.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            let cur: Option<(String, i64)> = t
                .query_row(
                    "SELECT bot_id, revision FROM bot_setup_drafts WHERE draft_id = ?1",
                    [&draft],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?;
            let out = match (cur, expected) {
                (None, None) => {
                    t.execute(
                        "INSERT INTO bot_setup_drafts(draft_id, bot_id, revision, body, updated_at) VALUES (?1,?2,1,?3,?4)",
                        params![draft, bot, body, now()],
                    )?;
                    Outcome::Applied(1)
                }
                (None, Some(_)) => conflict("unknown draft"),
                (Some((_, rev)), None) => Outcome::Stale(rev),
                (Some((b, _)), _) if b != bot => conflict("draft belongs to another bot"),
                (Some((_, rev)), Some(e)) if rev != e => Outcome::Stale(rev),
                (Some((_, rev)), Some(_)) => {
                    t.execute(
                        "UPDATE bot_setup_drafts SET revision = ?2, body = ?3, updated_at = ?4 WHERE draft_id = ?1",
                        params![draft, rev + 1, body, now()],
                    )?;
                    Outcome::Applied(rev + 1)
                }
            };
            t.commit()?;
            Ok(out)
        })
        .await
    }

    /// Owner approval of one draft revision creates the next definition version.
    /// A retry returns `Duplicate`. An old revision returns `Stale`.
    pub async fn approve_draft(
        &self,
        draft: &str,
        revision: i64,
        actor: &str,
        authority: Authority,
    ) -> anyhow::Result<Outcome> {
        let (draft, actor) = (draft.to_owned(), actor.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            let prior: Option<i64> = t
                .query_row(
                    "SELECT version FROM bot_definitions WHERE draft_id = ?1 AND draft_revision = ?2",
                    params![draft, revision],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(v) = prior {
                return Ok(Outcome::Duplicate(v));
            }
            let row: Option<(String, i64, String)> = t
                .query_row(
                    "SELECT bot_id, revision, body FROM bot_setup_drafts WHERE draft_id = ?1",
                    [&draft],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let Some((bot, rev, body)) = row else {
                return Ok(conflict("unknown draft"));
            };
            if rev != revision {
                return Ok(Outcome::Stale(rev));
            }
            if authority == Authority::Revoked {
                return Ok(conflict("approval cannot revoke"));
            }
            let version: i64 = t.query_row(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM bot_definitions WHERE bot_id = ?1",
                [&bot],
                |r| r.get(0),
            )?;
            t.execute(
                "INSERT INTO bot_definitions(bot_id, version, instructions, authority, actor, draft_id, draft_revision, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                params![bot, version, body, authority.as_str(), actor, draft, revision, now()],
            )?;
            t.commit()?;
            Ok(Outcome::Applied(version))
        })
        .await
    }

    /// Owner change of the active definition: new authority, restrictions or revocation.
    /// `expected` is the version the owner saw (0 when none exists).
    pub async fn revise_definition(
        &self,
        bot: &str,
        expected: i64,
        actor: &str,
        authority: Authority,
        instructions: &str,
        restrictions: &[String],
    ) -> anyhow::Result<Outcome> {
        let (bot, actor, instructions) =
            (bot.to_owned(), actor.to_owned(), instructions.to_owned());
        let restrictions = serde_json::to_string(restrictions)?;
        self.run(move |c| {
            let t = immediate(c)?;
            let cur: i64 = t.query_row(
                "SELECT COALESCE(MAX(version), 0) FROM bot_definitions WHERE bot_id = ?1",
                [&bot],
                |r| r.get(0),
            )?;
            if cur != expected {
                return Ok(Outcome::Stale(cur));
            }
            t.execute(
                "INSERT INTO bot_definitions(bot_id, version, instructions, authority, restrictions, actor, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7)",
                params![bot, cur + 1, instructions, authority.as_str(), restrictions, actor, now()],
            )?;
            t.commit()?;
            Ok(Outcome::Applied(cur + 1))
        })
        .await
    }

    /// The latest approved definition. None for a bot nobody configured: no authority is assumed.
    pub async fn active_definition(&self, bot: &str) -> anyhow::Result<Option<Definition>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.query_row(
                "SELECT bot_id, version, instructions, authority, restrictions, actor FROM bot_definitions WHERE bot_id = ?1 ORDER BY version DESC LIMIT 1",
                [&bot],
                |r| {
                    Ok(Definition {
                        bot_id: r.get(0)?,
                        version: r.get(1)?,
                        instructions: r.get(2)?,
                        authority: Authority::parse(&r.get::<_, String>(3)?),
                        restrictions: serde_json::from_str(&r.get::<_, String>(4)?).map_err(|e| {
                            rusqlite::Error::FromSqlConversionFailure(4, rusqlite::types::Type::Text, Box::new(e))
                        })?,
                        actor: r.get(5)?,
                    })
                },
            )
            .optional()
        })
        .await
    }

    // ---- accounting: owner-control operations ----

    /// Create the accounting policy of a bot, once. Owner operation.
    pub async fn configure_accounting(
        &self,
        bot: &str,
        change_id: &str,
        cfg: &AccountingConfig<'_>,
    ) -> anyhow::Result<Outcome> {
        let (bot, id) = (bot.to_owned(), change_id.to_owned());
        let cfg = (
            cfg.currency.to_owned(),
            cfg.exponent,
            cfg.period.to_owned(),
            cfg.policy_version.to_owned(),
            cfg.ceiling,
            cfg.allowance,
        );
        self.run(move |c| {
            let t = immediate(c)?;
            if let Some(o) = earlier_change(&t, &bot, &id)? {
                return Ok(o);
            }
            if accounting_row(&t, &bot)?.is_some() {
                return Ok(conflict("already configured"));
            }
            let (currency, exponent, period, policy, ceiling, allowance) = cfg;
            if currency.is_empty() || period.is_empty() || policy.is_empty() || exponent > 9 {
                return Ok(conflict("currency, period, policy and exponent must be explicit"));
            }
            if ceiling < 0 || allowance < 0 || allowance > ceiling {
                return Ok(conflict("allowance must be between 0 and the ceiling"));
            }
            t.execute(
                "INSERT INTO bot_accounting(bot_id, currency, exponent, period, policy_version, ceiling, allowance, version) VALUES (?1,?2,?3,?4,?5,?6,?7,1)",
                params![bot, currency, exponent, period, policy, ceiling, allowance],
            )?;
            record_change(&t, &bot, &id, "configure", 0, ceiling, "configured", "owner", 1)?;
            t.commit()?;
            Ok(Outcome::Applied(1))
        })
        .await
    }

    /// Change the absolute ceiling. Owner operation: no bot tool calls it. A lower ceiling clamps the allowance.
    pub async fn owner_set_ceiling(
        &self,
        bot: &str,
        change_id: &str,
        expected: i64,
        actor: &str,
        ceiling: i64,
    ) -> anyhow::Result<Outcome> {
        let (bot, id, actor) = (bot.to_owned(), change_id.to_owned(), actor.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            if let Some(o) = earlier_change(&t, &bot, &id)? {
                return Ok(o);
            }
            let Some(a) = accounting_row(&t, &bot)? else {
                return Ok(Outcome::Unconfigured);
            };
            if a.version != expected {
                return Ok(Outcome::Stale(a.version));
            }
            if ceiling < 0 {
                return Ok(conflict("ceiling must not be negative"));
            }
            let version = a.version + 1;
            let allowance = a.allowance.min(ceiling);
            t.execute(
                "UPDATE bot_accounting SET ceiling = ?2, allowance = ?3, version = ?4 WHERE bot_id = ?1",
                params![bot, ceiling, allowance, version],
            )?;
            record_change(&t, &bot, &id, "ceiling", a.ceiling, ceiling, "owner ceiling change", &actor, version)?;
            if allowance != a.allowance {
                record_change(&t, &bot, &format!("{id}:allowance"), "allowance", a.allowance, allowance, "clamped to ceiling", &actor, version)?;
            }
            t.commit()?;
            Ok(Outcome::Applied(version))
        })
        .await
    }

    /// Start a new accounting period with a stated allowance. Policy decides the period and amount.
    pub async fn begin_period(
        &self,
        bot: &str,
        change_id: &str,
        expected: i64,
        period: &str,
        allowance: i64,
    ) -> anyhow::Result<Outcome> {
        let (bot, id, period) = (bot.to_owned(), change_id.to_owned(), period.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            if let Some(o) = earlier_change(&t, &bot, &id)? {
                return Ok(o);
            }
            let Some(a) = accounting_row(&t, &bot)? else {
                return Ok(Outcome::Unconfigured);
            };
            if a.version != expected {
                return Ok(Outcome::Stale(a.version));
            }
            if period.is_empty() || period == a.period || allowance < 0 || allowance > a.ceiling {
                return Ok(conflict("period must be new and allowance within the ceiling"));
            }
            let version = a.version + 1;
            t.execute(
                "UPDATE bot_accounting SET period = ?2, allowance = ?3, version = ?4 WHERE bot_id = ?1",
                params![bot, period, allowance, version],
            )?;
            record_change(&t, &bot, &id, "period", a.allowance, allowance, &format!("period {} to {period}", a.period), "policy", version)?;
            t.commit()?;
            Ok(Outcome::Applied(version))
        })
        .await
    }

    // ---- accounting: runtime operations ----

    /// Raise the working allowance up to the ceiling. The caller supplies the amount from pure policy.
    /// A duplicate `change_id` or a stale version adds nothing.
    pub async fn raise_allowance(
        &self,
        bot: &str,
        change_id: &str,
        expected: i64,
        allowance: i64,
        reason: &str,
    ) -> anyhow::Result<Outcome> {
        let (bot, id, reason) = (bot.to_owned(), change_id.to_owned(), reason.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            if let Some(o) = earlier_change(&t, &bot, &id)? {
                return Ok(o);
            }
            let Some(a) = accounting_row(&t, &bot)? else {
                return Ok(Outcome::Unconfigured);
            };
            if a.version != expected {
                return Ok(Outcome::Stale(a.version));
            }
            if allowance <= a.allowance || allowance > a.ceiling {
                return Ok(conflict("allowance must rise and stay within the ceiling"));
            }
            let version = a.version + 1;
            t.execute(
                "UPDATE bot_accounting SET allowance = ?2, version = ?3 WHERE bot_id = ?1",
                params![bot, allowance, version],
            )?;
            record_change(
                &t,
                &bot,
                &id,
                "allowance",
                a.allowance,
                allowance,
                &reason,
                "policy",
                version,
            )?;
            t.commit()?;
            Ok(Outcome::Applied(version))
        })
        .await
    }

    /// Increase the working allowance and reserve a charge in one write transaction.
    /// The absolute ceiling is the only default monetary cap on automatic increases.
    pub async fn admit_charge(
        &self,
        bot: &str,
        reservation: &str,
        amount: i64,
        increment: i64,
        price_version: &str,
    ) -> anyhow::Result<Outcome> {
        let (bot, id, price) = (
            bot.to_owned(),
            reservation.to_owned(),
            price_version.to_owned(),
        );
        self.run(move |c| {
            let t = immediate(c)?;
            let Some(a) = accounting_row(&t, &bot)? else { return Ok(Outcome::Unconfigured) };
            let prior: Option<(String, i64)> = t.query_row(
                "SELECT bot_id, amount FROM accounting_reservations WHERE reservation_id = ?1",
                [&id], |r| Ok((r.get(0)?, r.get(1)?)),
            ).optional()?;
            if let Some((b, n)) = prior {
                return Ok(if b == bot && n == amount {
                    Outcome::Duplicate(exposure_of(&t, &a, &bot)?.remaining)
                } else { conflict("reservation identity conflict") });
            }
            if amount < 0 || increment <= 0 || price.trim().is_empty() {
                return Ok(conflict("charge, increase and pricing version must be valid"));
            }
            let paused: i64 = t.query_row(
                "SELECT automation_paused OR budget_hold FROM bot_state WHERE bot_id = ?1", [&bot], |r| r.get(0),
            )?;
            let held: i64 = t.query_row("SELECT COUNT(*) FROM bot_holds WHERE bot_id = ?1", [&bot], |r| r.get(0))?;
            if paused != 0 || held != 0 { return Ok(conflict("the bot is held or paused")); }
            let e = exposure_of(&t, &a, &bot)?;
            let invalid = |e: crate::accounting::AccountingError| rusqlite::Error::ToSqlConversionFailure(Box::new(e));
            use crate::accounting::{Admission, IncreasePolicy, Ledger, LedgerParts, PeriodId, Quote, Unit};
            let unit = Unit::new(&a.currency, a.exponent).map_err(invalid)?;
            let money = |n: i64| unit.amount(n as u64);
            let ledger = Ledger::restore(LedgerParts {
                period: PeriodId::new(&a.period).map_err(invalid)?,
                ceiling: money(a.ceiling), allowance: money(a.allowance),
                settled: money(e.settled), pending: money(e.reserved), unresolved: money(e.unresolved),
                auto_increased: money(0),
                policy: IncreasePolicy { max_step: money(a.ceiling), min_increment: money(increment), max_period_total: money(a.ceiling) },
            }).map_err(invalid)?;
            let quote = Quote::Priced { version: price.clone(), amount: money(amount) };
            match ledger.admit(&quote, None).map_err(invalid)? {
                Admission::Admitted { next, increase } => {
                    if let Some(increase) = increase {
                        let allowance = i64::try_from(increase.to.units()).map_err(|_| overflow())?;
                        let version = a.version.checked_add(1).ok_or_else(overflow)?;
                        t.execute("UPDATE bot_accounting SET allowance = ?2, version = ?3 WHERE bot_id = ?1", params![bot, allowance, version])?;
                        record_change(&t, &bot, &format!("auto:{id}"), "allowance", a.allowance, allowance,
                            &format!("reserve {id} at price {price}; recent exposure {}", a.allowance - e.remaining), "policy", version)?;
                        let content = serde_json::json!({"role":"assistant","content":format!("Working budget increased from {} to {} integer {} units to reserve the next model call. Absolute ceiling: {}. Price version: {}.", a.allowance, allowance, a.currency, a.ceiling, price)}).to_string();
                        t.execute("INSERT INTO messages(chat_id, role, content, created_at) SELECT chat_id, 'assistant', ?2, ?3 FROM bot_state WHERE bot_id = ?1", params![bot, content, now()])?;
                    }
                    t.execute("INSERT INTO accounting_reservations(reservation_id, bot_id, period, amount, state, created_at) VALUES (?1,?2,?3,?4,'reserved',?5)", params![id, bot, a.period, amount, now()])?;
                    let left = i64::try_from(next.allowance().units() - next.exposure().map_err(invalid)?.units()).map_err(|_| overflow())?;
                    t.commit()?;
                    Ok(Outcome::Applied(left))
                }
                Admission::CeilingHold { needed, .. } => {
                    let reason = format!("absolute spending ceiling reached; requested exposure {}", needed.units());
                    t.execute("INSERT INTO bot_holds(bot_id, kind, reason, created_at) VALUES (?1,'ceiling',?2,?3) ON CONFLICT(bot_id, kind) DO UPDATE SET reason = excluded.reason", params![bot, reason, now()])?;
                    t.execute("INSERT INTO ceiling_requests(request_id, bot_id, version, currency, exponent, ceiling, reason, state, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7,'pending',?8) ON CONFLICT DO NOTHING", params![uuid::Uuid::new_v4().to_string(), bot, a.version, a.currency, a.exponent, a.ceiling, reason, now()])?;
                    t.commit()?;
                    Ok(Outcome::Insufficient(a.ceiling - (a.allowance - e.remaining)))
                }
                _ => Ok(conflict("accounting policy did not admit the charge")),
            }
        }).await
    }

    /// Reserve `amount` before a priced dispatch. Fits only inside the remaining working allowance.
    pub async fn reserve(
        &self,
        bot: &str,
        reservation: &str,
        amount: i64,
    ) -> anyhow::Result<Outcome> {
        let (bot, id) = (bot.to_owned(), reservation.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            let Some(a) = accounting_row(&t, &bot)? else {
                return Ok(Outcome::Unconfigured);
            };
            let prior: Option<(String, i64)> = t
                .query_row(
                    "SELECT bot_id, amount FROM accounting_reservations WHERE reservation_id = ?1",
                    [&id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?;
            if let Some((b, amt)) = prior {
                return Ok(if b != bot {
                    conflict("reservation id belongs to another bot")
                } else if amt != amount {
                    conflict("reservation id reused with another amount")
                } else {
                    Outcome::Duplicate(exposure_of(&t, &a, &bot)?.remaining)
                });
            }
            if amount < 0 {
                return Ok(conflict("amount must not be negative"));
            }
            let left = exposure_of(&t, &a, &bot)?.remaining;
            if amount > left {
                return Ok(Outcome::Insufficient(left));
            }
            t.execute(
                "INSERT INTO accounting_reservations(reservation_id, bot_id, period, amount, state, created_at) VALUES (?1,?2,?3,?4,'reserved',?5)",
                params![id, bot, a.period, amount, now()],
            )?;
            t.commit()?;
            Ok(Outcome::Applied(left - amount))
        })
        .await
    }

    /// Flag a reservation whose outcome is unknown (timeout, dropped reply, restart).
    /// It keeps counting in full. Returns false for an unknown or settled reservation.
    pub async fn mark_unresolved(&self, bot: &str, reservation: &str) -> anyhow::Result<bool> {
        let (bot, id) = (bot.to_owned(), reservation.to_owned());
        self.run(move |c| {
            Ok(c.execute(
                "UPDATE accounting_reservations SET state = 'unresolved' WHERE reservation_id = ?1 AND bot_id = ?2 AND state IN ('reserved','unresolved')",
                params![id, bot],
            )? > 0)
        })
        .await
    }

    /// Settle a reservation once with the reported amount. `settlement_id` is unique across all bots.
    /// An overrun counts in full. Only settlement frees a reservation.
    pub async fn settle_reservation(
        &self,
        reservation: &str,
        settlement: &str,
        actual: i64,
    ) -> anyhow::Result<Outcome> {
        let (id, sid) = (reservation.to_owned(), settlement.to_owned());
        self.run(move |c| {
            let t = immediate(c)?;
            let row: Option<(String, String, Option<String>, Option<i64>)> = t
                .query_row(
                    "SELECT bot_id, state, settlement_id, settled_amount FROM accounting_reservations WHERE reservation_id = ?1",
                    [&id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
                )
                .optional()?;
            let Some((bot, state, old_sid, old_amt)) = row else {
                return Ok(conflict("unknown reservation"));
            };
            if state == "settled" {
                return Ok(if old_sid.as_deref() == Some(&sid) && old_amt == Some(actual) {
                    Outcome::Duplicate(remaining(&t, &bot)?)
                } else {
                    conflict("reservation already settled")
                });
            }
            if actual < 0 {
                return Ok(conflict("amount must not be negative"));
            }
            let used: i64 = t.query_row(
                "SELECT COUNT(*) FROM accounting_reservations WHERE settlement_id = ?1",
                [&sid],
                |r| r.get(0),
            )?;
            if used > 0 {
                return Ok(conflict("settlement id already used"));
            }
            t.execute(
                "UPDATE accounting_reservations SET state = 'settled', settlement_id = ?2, settled_amount = ?3, settled_at = ?4 WHERE reservation_id = ?1",
                params![id, sid, actual, now()],
            )?;
            let left = remaining(&t, &bot)?;
            t.commit()?;
            Ok(Outcome::Applied(left))
        })
        .await
    }

    // ---- accounting: reads ----

    pub async fn accounting(&self, bot: &str) -> anyhow::Result<Option<Accounting>> {
        let bot = bot.to_owned();
        self.run(move |c| accounting_row(c, &bot)).await
    }

    /// Exposure in the current period. None when the bot has no accounting policy.
    pub async fn exposure(&self, bot: &str) -> anyhow::Result<Option<Exposure>> {
        let bot = bot.to_owned();
        self.run(move |c| match accounting_row(c, &bot)? {
            Some(a) => Ok(Some(exposure_of(c, &a, &bot)?)),
            None => Ok(None),
        })
        .await
    }

    /// The change history, oldest first.
    pub async fn accounting_changes(&self, bot: &str) -> anyhow::Result<Vec<Change>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.prepare("SELECT change_id, kind, old_value, new_value, reason, actor, version FROM accounting_changes WHERE bot_id = ?1 ORDER BY rowid")?
                .query_map([&bot], |r| {
                    Ok(Change {
                        change_id: r.get(0)?,
                        kind: r.get(1)?,
                        old: r.get(2)?,
                        new: r.get(3)?,
                        reason: r.get(4)?,
                        actor: r.get(5)?,
                        version: r.get(6)?,
                    })
                })?
                .collect()
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::temp_dir;

    fn cfg(
        currency: &'static str,
        period: &'static str,
        ceiling: i64,
        allowance: i64,
    ) -> AccountingConfig<'static> {
        AccountingConfig {
            currency,
            exponent: 2,
            period,
            policy_version: "pol1",
            ceiling,
            allowance,
        }
    }

    async fn configured() -> Store {
        let s = Store::open_memory().unwrap();
        s.bot_flags("b", "Bot").await.unwrap();
        let w = s
            .configure_accounting("b", "cfg1", &cfg("USD", "p1", 10_000, 1_000))
            .await
            .unwrap();
        assert_eq!(w, Outcome::Applied(1));
        s
    }

    #[tokio::test]
    async fn ceiling_response_is_durable_idempotent_and_preserves_other_holds() {
        use crate::bot::authority::{CeilingResponse, Decision};
        let s = configured().await;
        assert!(matches!(
            s.admit_charge("b", "big", 11000, 100, "price")
                .await
                .unwrap(),
            Outcome::Insufficient(_)
        ));
        let request = s.pending_ceiling("b").await.unwrap().unwrap();
        s.set_hold("b", HoldKind::Investigation, "check")
            .await
            .unwrap();
        s.set_paused("b", true).await.unwrap();
        let reply = CeilingResponse {
            request_id: request.id,
            version: request.version,
            decision: Decision::Approve,
            amount_minor: Some(20000),
            currency: Some("USD".into()),
            precision: Some(2),
            idempotency_key: "owner-reply".into(),
        };
        assert!(matches!(
            s.answer_ceiling("b", "owner", &reply).await.unwrap(),
            Outcome::Applied(_)
        ));
        assert!(matches!(
            s.answer_ceiling("b", "owner", &reply).await.unwrap(),
            Outcome::Duplicate(_)
        ));
        assert!(s.pending_ceiling("b").await.unwrap().is_none());
        let holds = s.holds("b").await.unwrap();
        assert!(holds.iter().any(|h| h.kind == HoldKind::AutomationPause));
        assert!(holds.iter().any(|h| h.kind == HoldKind::Investigation));
        assert!(!holds.iter().any(|h| h.kind == HoldKind::Ceiling));
        let mut changed = reply;
        changed.amount_minor = Some(30000);
        assert!(matches!(
            s.answer_ceiling("b", "owner", &changed).await.unwrap(),
            Outcome::Conflict(_)
        ));
        assert_eq!(s.accounting("b").await.unwrap().unwrap().ceiling, 20000);
    }

    #[tokio::test]
    async fn automatic_admission_is_atomic_and_never_crosses_the_ceiling() {
        let s = configured().await;
        let (a, b) = tokio::join!(
            s.admit_charge("b", "one", 6000, 100, "test-price"),
            s.admit_charge("b", "two", 6000, 100, "test-price")
        );
        let results = [a.unwrap(), b.unwrap()];
        assert_eq!(
            results
                .iter()
                .filter(|o| matches!(o, Outcome::Applied(_)))
                .count(),
            1
        );
        assert_eq!(
            results
                .iter()
                .filter(|o| matches!(o, Outcome::Insufficient(_)))
                .count(),
            1
        );
        let a = s.accounting("b").await.unwrap().unwrap();
        assert!(a.allowance <= a.ceiling);
        let e = s.exposure("b").await.unwrap().unwrap();
        assert_eq!(e.reserved, 6000);
        let winner = if matches!(results[0], Outcome::Applied(_)) {
            "one"
        } else {
            "two"
        };
        assert_eq!(
            s.admit_charge("b", winner, 6000, 100, "test-price")
                .await
                .unwrap(),
            Outcome::Duplicate(e.remaining)
        );
    }

    #[tokio::test]
    async fn malformed_restrictions_fail_closed() {
        let s = configured().await;
        s.revise_definition(
            "b",
            0,
            "owner",
            Authority::Restricted,
            "job",
            &["no sends".into()],
        )
        .await
        .unwrap();
        s.run(|c| {
            c.execute("UPDATE bot_definitions SET restrictions = 'broken'", [])?;
            Ok(())
        })
        .await
        .unwrap();
        assert!(s.active_definition("b").await.is_err());
    }

    #[tokio::test]
    async fn legacy_bot_has_no_authority_and_no_ceiling() {
        let s = Store::open_memory().unwrap();
        s.bot_flags("b", "Bot").await.unwrap();
        assert!(s.active_definition("b").await.unwrap().is_none());
        assert!(s.accounting("b").await.unwrap().is_none());
        assert_eq!(
            s.reserve("b", "r1", 1).await.unwrap(),
            Outcome::Unconfigured
        );
    }

    #[tokio::test]
    async fn legacy_holds_survive_the_migration() {
        let dir = temp_dir();
        {
            let conn = rusqlite::Connection::open(dir.join("deck.db")).unwrap();
            crate::store::migrate_to(&conn, 4).unwrap();
            conn.execute("INSERT INTO chats(title, created_at) VALUES ('c','t')", [])
                .unwrap();
            conn.execute(
                "INSERT INTO bot_state(bot_id, chat_id, automation_paused, budget_hold, budget_reason) VALUES ('b',1,1,1,'limit')",
                [],
            )
            .unwrap();
        }
        let s = Store::open(&dir).unwrap();
        let kinds: Vec<_> = s
            .holds("b")
            .await
            .unwrap()
            .into_iter()
            .map(|h| h.kind)
            .collect();
        assert_eq!(kinds, vec![HoldKind::AutomationPause, HoldKind::Budget]);
        assert!(s.active_definition("b").await.unwrap().is_none());
        assert!(s.accounting("b").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn holds_are_independent() {
        let s = Store::open_memory().unwrap();
        s.bot_flags("b", "Bot").await.unwrap();
        s.set_hold("b", HoldKind::AutomationPause, "owner")
            .await
            .unwrap();
        s.set_hold("b", HoldKind::Ceiling, "needs more")
            .await
            .unwrap();
        s.set_hold("b", HoldKind::UnknownOutcome, "cmd 1")
            .await
            .unwrap();
        s.set_hold("b", HoldKind::Investigation, "spike")
            .await
            .unwrap();
        s.clear_hold("b", HoldKind::Ceiling).await.unwrap();
        let kinds: Vec<_> = s
            .holds("b")
            .await
            .unwrap()
            .into_iter()
            .map(|h| h.kind)
            .collect();
        assert_eq!(
            kinds,
            vec![
                HoldKind::AutomationPause,
                HoldKind::Investigation,
                HoldKind::UnknownOutcome
            ]
        );
        s.clear_hold("b", HoldKind::AutomationPause).await.unwrap();
        assert_eq!(s.holds("b").await.unwrap().len(), 2);
    }

    #[tokio::test]
    async fn a_draft_never_grants_authority_until_its_revision_is_approved() {
        let s = Store::open_memory().unwrap();
        s.bot_flags("b", "Bot").await.unwrap();
        assert_eq!(
            s.save_draft("d", "b", None, "v1").await.unwrap(),
            Outcome::Applied(1)
        );
        assert_eq!(
            s.save_draft("d", "b", Some(1), "v2").await.unwrap(),
            Outcome::Applied(2)
        );
        // A stale edit changes nothing.
        assert_eq!(
            s.save_draft("d", "b", Some(1), "v3").await.unwrap(),
            Outcome::Stale(2)
        );
        assert!(s.active_definition("b").await.unwrap().is_none());
        // Approving an old revision is refused.
        assert_eq!(
            s.approve_draft("d", 1, "owner", Authority::Full)
                .await
                .unwrap(),
            Outcome::Stale(2)
        );
        assert!(s.active_definition("b").await.unwrap().is_none());
        assert_eq!(
            s.approve_draft("d", 2, "owner", Authority::Full)
                .await
                .unwrap(),
            Outcome::Applied(1)
        );
        // A retry creates no second definition.
        assert_eq!(
            s.approve_draft("d", 2, "owner", Authority::Full)
                .await
                .unwrap(),
            Outcome::Duplicate(1)
        );
        let d = s.active_definition("b").await.unwrap().unwrap();
        assert_eq!(
            (d.version, d.instructions.as_str(), d.authority),
            (1, "v2", Authority::Full)
        );
        // Editing after approval does not touch the active definition.
        assert_eq!(
            s.save_draft("d", "b", Some(2), "v4").await.unwrap(),
            Outcome::Applied(3)
        );
        assert_eq!(
            s.active_definition("b")
                .await
                .unwrap()
                .unwrap()
                .instructions,
            "v2"
        );
    }

    #[tokio::test]
    async fn definitions_version_and_revocation_wins() {
        let s = Store::open_memory().unwrap();
        s.bot_flags("b", "Bot").await.unwrap();
        s.save_draft("d", "b", None, "job").await.unwrap();
        s.approve_draft("d", 1, "owner", Authority::Full)
            .await
            .unwrap();
        let r = vec!["no deletes in mail".to_owned()];
        assert_eq!(
            s.revise_definition("b", 1, "owner", Authority::Restricted, "job", &r)
                .await
                .unwrap(),
            Outcome::Applied(2)
        );
        assert_eq!(
            s.revise_definition("b", 1, "owner", Authority::Full, "job", &[])
                .await
                .unwrap(),
            Outcome::Stale(2)
        );
        s.revise_definition("b", 2, "owner", Authority::Revoked, "job", &r)
            .await
            .unwrap();
        let d = s.active_definition("b").await.unwrap().unwrap();
        assert_eq!(
            (d.version, d.authority, d.restrictions),
            (3, Authority::Revoked, r)
        );
    }

    #[tokio::test]
    async fn reservations_respect_the_allowance_and_are_idempotent() {
        let s = configured().await;
        assert_eq!(
            s.reserve("b", "r1", 600).await.unwrap(),
            Outcome::Applied(400)
        );
        assert_eq!(
            s.reserve("b", "r1", 600).await.unwrap(),
            Outcome::Duplicate(400)
        );
        assert_eq!(
            s.reserve("b", "r1", 700).await.unwrap(),
            Outcome::Conflict("reservation id reused with another amount".into())
        );
        assert_eq!(
            s.reserve("b", "r2", 401).await.unwrap(),
            Outcome::Insufficient(400)
        );
        assert_eq!(
            s.reserve("b", "r3", 400).await.unwrap(),
            Outcome::Applied(0)
        );
        assert!(matches!(
            s.reserve("b", "r4", -1).await.unwrap(),
            Outcome::Conflict(_)
        ));
    }

    #[tokio::test]
    async fn settlement_counts_once_and_unresolved_exposure_stays() {
        let s = configured().await;
        s.reserve("b", "r1", 600).await.unwrap();
        s.reserve("b", "r2", 300).await.unwrap();
        assert!(s.mark_unresolved("b", "r2").await.unwrap());
        assert_eq!(
            s.settle_reservation("r1", "s1", 250).await.unwrap(),
            Outcome::Applied(450)
        );
        assert_eq!(
            s.settle_reservation("r1", "s1", 250).await.unwrap(),
            Outcome::Duplicate(450)
        );
        assert!(matches!(
            s.settle_reservation("r1", "s9", 250).await.unwrap(),
            Outcome::Conflict(_)
        ));
        assert!(matches!(
            s.settle_reservation("r2", "s1", 10).await.unwrap(),
            Outcome::Conflict(_)
        ));
        let e = s.exposure("b").await.unwrap().unwrap();
        assert_eq!(
            (e.settled, e.reserved, e.unresolved, e.remaining),
            (250, 0, 300, 450)
        );
        // A timeout never frees the reservation. Reconciliation settles it.
        assert_eq!(
            s.settle_reservation("r2", "s2", 0).await.unwrap(),
            Outcome::Applied(750)
        );
        assert_eq!(s.exposure("b").await.unwrap().unwrap().remaining, 750);
        assert_eq!(
            s.settle_reservation("nope", "s3", 1).await.unwrap(),
            Outcome::Conflict("unknown reservation".into())
        );
    }

    #[tokio::test]
    async fn an_overrun_settlement_is_counted_in_full() {
        let s = configured().await;
        s.reserve("b", "r1", 100).await.unwrap();
        s.settle_reservation("r1", "s1", 1_200).await.unwrap();
        assert_eq!(s.exposure("b").await.unwrap().unwrap().remaining, -200);
        assert_eq!(
            s.reserve("b", "r2", 1).await.unwrap(),
            Outcome::Insufficient(-200)
        );
    }

    #[tokio::test]
    async fn allowance_increase_is_versioned_idempotent_and_capped() {
        let s = configured().await;
        assert_eq!(
            s.raise_allowance("b", "inc1", 1, 2_000, "work")
                .await
                .unwrap(),
            Outcome::Applied(2)
        );
        // Duplicate identity, even with a stale version, adds nothing.
        assert_eq!(
            s.raise_allowance("b", "inc1", 1, 2_000, "work")
                .await
                .unwrap(),
            Outcome::Duplicate(2)
        );
        // Stale version.
        assert_eq!(
            s.raise_allowance("b", "inc2", 1, 3_000, "work")
                .await
                .unwrap(),
            Outcome::Stale(2)
        );
        // Above the ceiling, or not an increase.
        assert!(matches!(
            s.raise_allowance("b", "inc3", 2, 10_001, "x")
                .await
                .unwrap(),
            Outcome::Conflict(_)
        ));
        assert!(matches!(
            s.raise_allowance("b", "inc4", 2, 2_000, "x").await.unwrap(),
            Outcome::Conflict(_)
        ));
        let a = s.accounting("b").await.unwrap().unwrap();
        assert_eq!((a.allowance, a.ceiling, a.version), (2_000, 10_000, 2));
    }

    #[tokio::test]
    async fn raising_the_allowance_clears_no_hold() {
        let s = configured().await;
        s.set_hold("b", HoldKind::AutomationPause, "owner")
            .await
            .unwrap();
        s.set_hold("b", HoldKind::Ceiling, "need more")
            .await
            .unwrap();
        s.set_hold("b", HoldKind::UnknownOutcome, "cmd")
            .await
            .unwrap();
        s.raise_allowance("b", "inc1", 1, 2_000, "work")
            .await
            .unwrap();
        assert_eq!(s.holds("b").await.unwrap().len(), 3);
    }

    #[tokio::test]
    async fn ceiling_changes_are_recorded_versioned_and_clamp_the_allowance() {
        let s = configured().await;
        assert_eq!(
            s.owner_set_ceiling("b", "c1", 1, "owner", 20_000)
                .await
                .unwrap(),
            Outcome::Applied(2)
        );
        assert_eq!(
            s.owner_set_ceiling("b", "c1", 1, "owner", 20_000)
                .await
                .unwrap(),
            Outcome::Duplicate(2)
        );
        assert_eq!(
            s.owner_set_ceiling("b", "c2", 1, "owner", 30_000)
                .await
                .unwrap(),
            Outcome::Stale(2)
        );
        assert_eq!(
            s.owner_set_ceiling("b", "c3", 2, "owner", 500)
                .await
                .unwrap(),
            Outcome::Applied(3)
        );
        let a = s.accounting("b").await.unwrap().unwrap();
        assert_eq!((a.ceiling, a.allowance), (500, 500));
        let log = s.accounting_changes("b").await.unwrap();
        let ceil: Vec<_> = log
            .iter()
            .filter(|c| c.kind == "ceiling")
            .map(|c| (c.old, c.new, c.actor.as_str()))
            .collect();
        assert_eq!(
            ceil,
            vec![(10_000, 20_000, "owner"), (20_000, 500, "owner")]
        );
    }

    #[tokio::test]
    async fn everything_survives_a_restart() {
        let dir = temp_dir();
        {
            let s = Store::open(&dir).unwrap();
            s.bot_flags("b", "Bot").await.unwrap();
            s.configure_accounting("b", "cfg", &cfg("EUR", "p1", 5_000, 500))
                .await
                .unwrap();
            s.reserve("b", "r1", 200).await.unwrap();
            s.mark_unresolved("b", "r1").await.unwrap();
            s.set_hold("b", HoldKind::Ceiling, "more").await.unwrap();
            s.save_draft("d", "b", None, "job").await.unwrap();
            s.approve_draft("d", 1, "owner", Authority::Full)
                .await
                .unwrap();
        }
        let s = Store::open(&dir).unwrap();
        let a = s.accounting("b").await.unwrap().unwrap();
        assert_eq!(
            (
                a.currency.as_str(),
                a.exponent,
                a.period.as_str(),
                a.ceiling
            ),
            ("EUR", 2, "p1", 5_000)
        );
        assert_eq!(s.exposure("b").await.unwrap().unwrap().unresolved, 200);
        assert_eq!(
            s.reserve("b", "r1", 200).await.unwrap(),
            Outcome::Duplicate(300)
        );
        assert_eq!(s.holds("b").await.unwrap().len(), 1);
        assert_eq!(
            s.active_definition("b").await.unwrap().unwrap().authority,
            Authority::Full
        );
    }

    #[tokio::test]
    async fn a_new_period_counts_only_its_own_reservations() {
        let s = configured().await;
        s.reserve("b", "r1", 900).await.unwrap();
        assert_eq!(
            s.begin_period("b", "pc", 1, "p2", 700).await.unwrap(),
            Outcome::Applied(2)
        );
        assert_eq!(s.exposure("b").await.unwrap().unwrap().remaining, 700);
        assert!(matches!(
            s.begin_period("b", "pc2", 2, "p3", 10_001).await.unwrap(),
            Outcome::Conflict(_)
        ));
        // The earlier period stays in the ledger.
        assert_eq!(
            s.reserve("b", "r1", 900).await.unwrap(),
            Outcome::Duplicate(700)
        );
    }
}
