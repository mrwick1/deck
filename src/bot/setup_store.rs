//! Setup snapshots and their atomic transition to the first task.

use super::setup::{Authority, Limits, Phase, Setup, Start};
use crate::store::{Store, now};
use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior, params};
use serde::Serialize;

#[derive(Debug, thiserror::Error)]
#[error("setup changed; reload its current state")]
pub struct Conflict;

fn conflict() -> rusqlite::Error {
    rusqlite::Error::ToSqlConversionFailure(Box::new(Conflict))
}
fn encoded(e: impl std::error::Error + Send + Sync + 'static) -> rusqlite::Error {
    rusqlite::Error::ToSqlConversionFailure(Box::new(e))
}

#[derive(Clone, Serialize)]
pub struct Record {
    pub version: i64,
    pub chat_id: i64,
    pub setup: Setup,
}

fn record(c: &Connection, id: &str) -> rusqlite::Result<Option<Record>> {
    c.query_row("SELECT d.revision, s.chat_id, d.body FROM bot_setup_drafts d JOIN bot_state s ON s.bot_id=d.bot_id WHERE d.draft_id=?1 AND d.lifecycle=1", [id], |r| {
        Ok(Record { version: r.get(0)?, chat_id: r.get(1)?, setup: serde_json::from_str(&r.get::<_, String>(2)?).map_err(encoded)? })
    }).optional()
}

fn save(c: &Connection, expected: i64, setup: &Setup) -> rusqlite::Result<i64> {
    let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
    let prior = record(&t, &setup.id)?.ok_or_else(conflict)?;
    if prior.version != expected
        || prior.setup.approval != setup.approval
        || prior.setup.started != setup.started
        || (prior.setup.approval.is_some() && prior.setup.draft != setup.draft)
    {
        return Err(conflict());
    }
    let version = expected.checked_add(1).ok_or_else(conflict)?;
    t.execute("UPDATE bot_setup_drafts SET revision=?2, body=?3, updated_at=?4 WHERE draft_id=?1 AND lifecycle=1", params![setup.id, version, serde_json::to_string(setup).map_err(encoded)?, now()])?;
    t.commit()?;
    Ok(version)
}

impl Store {
    pub async fn create_setup(&self, setup: &Setup) -> anyhow::Result<()> {
        let setup = setup.clone();
        self.run(move |c| {
            if setup.approval.is_some() || setup.started || setup.phase() != Phase::Draft { return Err(conflict()); }
            let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
            let exists: bool = t.query_row("SELECT EXISTS(SELECT 1 FROM bot_state WHERE bot_id=?1)", [&setup.id], |r| r.get(0))?;
            if exists { return Err(conflict()); }
            t.execute("INSERT INTO chats(title,created_at) VALUES (?1,?2)", params![setup.draft.name, now()])?;
            let chat = t.last_insert_rowid();
            t.execute("INSERT INTO bot_state(bot_id,chat_id) VALUES (?1,?2)", params![setup.id, chat])?;
            t.execute("INSERT INTO bot_setup_drafts(draft_id,bot_id,revision,body,updated_at,lifecycle) VALUES (?1,?1,1,?2,?3,1)", params![setup.id, serde_json::to_string(&setup).map_err(encoded)?, now()])?;
            let message = serde_json::json!({"role":"assistant","content":"Setup draft saved. Review its instructions, authority and spending ceiling before approval."});
            t.execute("INSERT INTO messages(chat_id,role,content,created_at) VALUES (?1,'assistant',?2,?3)", params![chat, message.to_string(), now()])?;
            t.commit()?;
            Ok(())
        }).await
    }

    pub async fn setup_record(&self, id: &str) -> anyhow::Result<Option<Record>> {
        let id = id.to_owned();
        self.run(move |c| record(c, &id)).await
    }

    pub async fn setup_records(&self) -> anyhow::Result<Vec<Record>> {
        self.run(|c| {
            let ids = c.prepare("SELECT draft_id FROM bot_setup_drafts WHERE lifecycle=1 ORDER BY updated_at, draft_id")?
                .query_map([], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
            ids.into_iter().map(|id| record(c, &id)?.ok_or_else(conflict)).collect()
        }).await
    }

    pub async fn save_setup_state(&self, expected: i64, setup: &Setup) -> anyhow::Result<i64> {
        let setup = setup.clone();
        self.run(move |c| save(c, expected, &setup)).await
    }

    /// The provisioning driver calls this from its blocking worker before each side effect.
    pub fn persist_setup(&self, expected: i64, setup: &Setup) -> anyhow::Result<i64> {
        self.run_sync(|c| save(c, expected, setup))
    }

    pub async fn approve_setup(
        &self,
        id: &str,
        expected: i64,
        revision: u64,
        actor: &str,
        limits: &Limits,
        policy: &str,
    ) -> anyhow::Result<()> {
        let (id, actor, limits, policy) = (
            id.to_owned(),
            actor.to_owned(),
            limits.clone(),
            policy.to_owned(),
        );
        self.run(move |c| {
            let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
            let mut row = record(&t, &id)?.ok_or_else(conflict)?;
            // A retry of the approved revision changes nothing.
            if row.setup.approval.as_ref().is_some_and(|a| a.revision == revision) { return Ok(()); }
            if row.version != expected { return Err(conflict()); }
            row.setup.approve(revision, &actor, &limits).map_err(encoded)?;
            let budget = row.setup.draft.budget.as_ref().ok_or_else(conflict)?;
            budget.validate().map_err(encoded)?;
            let (authority, restrictions) = match &row.setup.draft.authority {
                Authority::Full => ("full", vec![]),
                Authority::Restricted(allowed) => ("restricted", vec![format!("Only these actions are allowed: {}", allowed.join(", "))]),
            };
            t.execute("INSERT INTO bot_definitions(bot_id,version,instructions,authority,restrictions,actor,draft_id,draft_revision,created_at) VALUES (?1,1,?2,?3,?4,?5,?1,?6,?7)", params![id, row.setup.draft.instructions, authority, serde_json::to_string(&restrictions).map_err(encoded)?, actor, revision, now()])?;
            t.execute("INSERT INTO bot_accounting(bot_id,currency,exponent,period,policy_version,ceiling,allowance,version) VALUES (?1,?2,?3,?4,?5,?6,?7,1)", params![id,budget.currency,budget.exponent,budget.period,policy,budget.ceiling,budget.allowance])?;
            t.execute("INSERT INTO accounting_changes(change_id,bot_id,kind,old_value,new_value,reason,actor,version,created_at) VALUES (?1,?2,'configure',0,?3,'approved setup',?4,1,?5)", params![format!("setup:{id}"),id,budget.ceiling,actor,now()])?;
            t.execute("UPDATE bot_setup_drafts SET revision=revision+1,body=?2,updated_at=?3 WHERE draft_id=?1",params![id,serde_json::to_string(&row.setup).map_err(encoded)?,now()])?;
            t.commit()?;
            Ok(())
        }).await
    }

    /// The ready marker, checklist message and first task commit together.
    pub async fn finish_setup(&self, id: &str) -> anyhow::Result<Option<String>> {
        let id = id.to_owned();
        self.run(move |c| {
            let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
            let mut row = record(&t, &id)?.ok_or_else(conflict)?;
            let Some(start) = row.setup.claim_start().map_err(encoded)? else { return Ok(None) };
            let task = match start {
                Start::Task(body) => {
                    let task = format!("setup-{id}");
                    let title: String = body.chars().take(80).collect();
                    t.execute("INSERT INTO tasks(id,bot_id,chat_id,source,title,body,priority,state,created_at) VALUES (?1,?2,?3,'message',?4,?5,1,'queued',?6)", params![task,id,row.chat_id,title,body,now()])?;
                    Some(task)
                }
                Start::AnnounceReady => None,
            };
            let content = serde_json::json!({"role":"assistant","content": if task.is_some() { "Setup complete. Starting the work you supplied." } else { "Setup complete. I am ready. Tell me what to work on." },"checklist":row.setup.checklist()});
            t.execute("INSERT INTO messages(chat_id,role,content,created_at) VALUES (?1,'assistant',?2,?3)",params![row.chat_id,content.to_string(),now()])?;
            t.execute("UPDATE bot_setup_drafts SET revision=revision+1,body=?2,updated_at=?3 WHERE draft_id=?1",params![id,serde_json::to_string(&row.setup).map_err(encoded)?,now()])?;
            t.commit()?;
            Ok(task)
        }).await
    }
}

#[cfg(test)]
mod tests {
    use crate::bot::setup::{Authority, Draft, Limits, Resources, Setup, Status};
    use crate::store::Store;
    use crate::testutil::temp_dir;

    fn draft() -> Draft {
        Draft {
            revision: 1,
            name: "Research".into(),
            instructions: "Research supplied topics".into(),
            model: "fixture".into(),
            authority: Authority::Full,
            resources: Resources {
                cpus: 2,
                memory_mib: 2048,
                disk_gib: 10,
            },
            budget: Some(crate::bot::setup::Budget {
                currency: "TST".into(),
                exponent: 2,
                period: "test".into(),
                allowance: 10,
                ceiling: 100,
            }),
            credentials: vec![],
            task: Some("Read the supplied fixture".into()),
        }
    }

    #[tokio::test]
    async fn setup_survives_restart_and_rejects_a_stale_snapshot() {
        let dir = temp_dir();
        let store = Store::open(&dir).unwrap();
        let setup = Setup::new("draft", draft()).unwrap();
        store.create_setup(&setup).await.unwrap();
        assert!(store.active_definition("draft").await.unwrap().is_none());
        let first = store.setup_record("draft").await.unwrap().unwrap();
        let mut changed = first.setup.clone();
        changed.edit(1, |d| d.name = "Changed".into()).unwrap();
        store
            .save_setup_state(first.version, &changed)
            .await
            .unwrap();
        assert!(
            store
                .save_setup_state(first.version, &first.setup)
                .await
                .is_err()
        );
        drop(store);
        let store = Store::open(&dir).unwrap();
        assert_eq!(
            store
                .setup_record("draft")
                .await
                .unwrap()
                .unwrap()
                .setup
                .draft
                .name,
            "Changed"
        );
    }

    #[tokio::test]
    async fn first_task_and_start_claim_commit_together() {
        let store = Store::open_memory().unwrap();
        let setup = Setup::new("bot", draft()).unwrap();
        store.create_setup(&setup).await.unwrap();
        let mut record = store.setup_record("bot").await.unwrap().unwrap();
        store
            .approve_setup(
                "bot",
                record.version,
                1,
                "owner",
                &Limits {
                    max_cpus: 2,
                    max_memory_mib: 2048,
                    max_disk_gib: 10,
                },
                "fixture-policy",
            )
            .await
            .unwrap();
        record = store.setup_record("bot").await.unwrap().unwrap();
        for step in &mut record.setup.steps {
            step.status = Status::Done;
        }
        store
            .save_setup_state(record.version, &record.setup)
            .await
            .unwrap();
        assert_eq!(
            store.finish_setup("bot").await.unwrap().as_deref(),
            Some("setup-bot")
        );
        assert_eq!(store.finish_setup("bot").await.unwrap(), None);
        assert!(
            store
                .setup_record("bot")
                .await
                .unwrap()
                .unwrap()
                .setup
                .started
        );
        assert_eq!(store.tasks("bot", 10).await.unwrap().len(), 1);
    }
}
