use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Context;
use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;

const SCHEMA_V1: &str = "
CREATE TABLE chats(
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE messages(
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','tool','system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE runs(
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE events(
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
";

const SCHEMA_V2: &str = "
CREATE TABLE files(
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
";

/// The executor journal lives beside the chat data, outside any VM saved state.
const SCHEMA_V3: &str = "
CREATE TABLE executor_bots(
    bot_id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL,
    guest_id TEXT
);
CREATE TABLE executor_commands(
    command_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES executor_bots(bot_id),
    task_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    argv TEXT NOT NULL,
    deadline_ms INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('intent','sent','running','succeeded','failed','cancelled','timed_out','not_delivered','unknown')),
    detail TEXT,
    dispatch_epoch INTEGER,
    cancel_state TEXT NOT NULL DEFAULT 'none' CHECK(cancel_state IN ('none','requested','accepted','confirmed','unconfirmed')),
    exit_code INTEGER,
    signal INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    truncated INTEGER NOT NULL DEFAULT 0,
    artifacts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    finished_at TEXT
);
CREATE TABLE executor_events(
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL,
    command_id TEXT,
    epoch INTEGER,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE executor_artifacts(
    command_id TEXT NOT NULL REFERENCES executor_commands(command_id),
    handle TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    host_path TEXT NOT NULL,
    PRIMARY KEY(command_id, handle)
);
";

/// The bot runtime: flags, durable tasks, steering, ingested events and owner decisions.
/// They live in the host database, outside the VM, so a saved or lost computer keeps them.
const SCHEMA_V4: &str = "
CREATE TABLE bot_state(
    bot_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    automation_paused INTEGER NOT NULL DEFAULT 0,
    budget_hold INTEGER NOT NULL DEFAULT 0,
    budget_reason TEXT
);
CREATE TABLE tasks(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    bot_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('message','event')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 2),
    state TEXT NOT NULL CHECK(state IN ('queued','running','waiting','done','failed','cancelled','held')),
    detail TEXT,
    run_id INTEGER,
    created_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE steering(
    id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    text TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE bot_events(
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL,
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    priority INTEGER NOT NULL,
    received_ms INTEGER NOT NULL,
    task_id TEXT,
    UNIQUE(bot_id, source, event_id)
);
CREATE TABLE inbox_decisions(
    command_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL CHECK(decision IN ('ran','not_run','dismissed')),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
";

/// Versioned bot definitions, setup drafts, typed holds and the accounting ledger.
/// Existing bots get no row in any of these tables: no authority and no ceiling are invented.
/// Amounts are integer minor units of the stated currency and exponent.
const SCHEMA_V5: &str = "
CREATE TABLE bot_setup_drafts(
    draft_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bot_state(bot_id),
    revision INTEGER NOT NULL,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE bot_definitions(
    bot_id TEXT NOT NULL REFERENCES bot_state(bot_id),
    version INTEGER NOT NULL,
    instructions TEXT NOT NULL,
    authority TEXT NOT NULL CHECK(authority IN ('full','restricted','revoked')),
    restrictions TEXT NOT NULL DEFAULT '[]',
    actor TEXT NOT NULL,
    draft_id TEXT,
    draft_revision INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY(bot_id, version),
    UNIQUE(draft_id, draft_revision)
);
CREATE TABLE bot_holds(
    bot_id TEXT NOT NULL REFERENCES bot_state(bot_id),
    kind TEXT NOT NULL CHECK(kind IN ('ceiling','investigation','unknown_outcome')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(bot_id, kind)
);
CREATE TABLE bot_accounting(
    bot_id TEXT PRIMARY KEY REFERENCES bot_state(bot_id),
    currency TEXT NOT NULL,
    exponent INTEGER NOT NULL CHECK(exponent BETWEEN 0 AND 9),
    period TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    ceiling INTEGER NOT NULL CHECK(ceiling >= 0),
    allowance INTEGER NOT NULL CHECK(allowance >= 0 AND allowance <= ceiling),
    version INTEGER NOT NULL
);
CREATE TABLE accounting_changes(
    change_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bot_accounting(bot_id),
    kind TEXT NOT NULL CHECK(kind IN ('configure','ceiling','allowance','period')),
    old_value INTEGER NOT NULL,
    new_value INTEGER NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE accounting_reservations(
    reservation_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bot_accounting(bot_id),
    period TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount >= 0),
    state TEXT NOT NULL CHECK(state IN ('reserved','unresolved','settled')),
    settlement_id TEXT UNIQUE,
    settled_amount INTEGER CHECK(settled_amount >= 0),
    created_at TEXT NOT NULL,
    settled_at TEXT
);
";

const SCHEMA_V6: &str = "
CREATE TABLE ceiling_requests(
    request_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bot_accounting(bot_id),
    version INTEGER NOT NULL,
    currency TEXT NOT NULL,
    exponent INTEGER NOT NULL,
    ceiling INTEGER NOT NULL,
    reason TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','approved','superseded')),
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_pending_ceiling ON ceiling_requests(bot_id) WHERE state = 'pending';
CREATE TABLE ceiling_responses(
    response_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES ceiling_requests(request_id),
    bot_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
";

const SCHEMA_V7: &str =
    "ALTER TABLE bot_setup_drafts ADD COLUMN lifecycle INTEGER NOT NULL DEFAULT 0;";

/// Metadata only. No column holds a protected value.
const SCHEMA_V8: &str = "
CREATE TABLE secret_receipts(
    bot_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY(bot_id, request_id)
);
CREATE TABLE secret_requests(
    request_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    label TEXT NOT NULL,
    purpose TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','delivered','superseded')),
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE privacy_gates(
    bot_id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL
);
";

/// One counter per bot. It grows at each gate open and is never deleted, so it survives the
/// close. It holds no protected value.
const SCHEMA_V9: &str = "
CREATE TABLE privacy_generations(
    bot_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL
);
";

/// Durable Gmail poll state. Account keys are opaque configuration identifiers.
/// No column holds an address, a body or a page string.
const SCHEMA_V10: &str = "
CREATE TABLE gmail_accounts(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    baseline_done INTEGER NOT NULL DEFAULT 0 CHECK(baseline_done IN (0,1)),
    last_attempt_ms INTEGER,
    last_success_ms INTEGER,
    last_result TEXT CHECK(last_result IN ('ok','incomplete','error','auth','identity_mismatch')),
    PRIMARY KEY(bot_id, account)
);
CREATE TABLE gmail_seen(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    PRIMARY KEY(bot_id, account, thread_id, message_id)
);
";

/// Task execution kind and the Gmail window watermark.
/// `execution_kind` routes a task. Only the store sets `gmail_poll`, so no title or payload can spoof it.
/// `watermark_ms` is the START time of the last successful window scan. Old rows get no watermark:
/// none is invented from the completion time, so the next scan seeds a silent baseline again.
const SCHEMA_V11: &str = "
ALTER TABLE tasks ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'model' CHECK(execution_kind IN ('model','gmail_poll'));
CREATE UNIQUE INDEX one_outstanding_gmail_poll ON tasks(bot_id) WHERE execution_kind = 'gmail_poll' AND state IN ('queued','running');
ALTER TABLE gmail_accounts ADD COLUMN watermark_ms INTEGER;
ALTER TABLE gmail_accounts ADD COLUMN window_lower_s INTEGER;
ALTER TABLE gmail_accounts ADD COLUMN scope TEXT CHECK(scope IN ('date_window'));
";

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Chat {
    pub id: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bot_id: Option<String>,
    pub title: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Message {
    pub id: i64,
    pub chat_id: i64,
    pub role: String,
    /// JSON text.
    pub content: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct FileRow {
    pub id: i64,
    pub chat_id: i64,
    pub path: String,
    pub name: String,
    pub mime: String,
    pub size: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StoredEvent {
    pub id: i64,
    pub kind: String,
    /// JSON text.
    pub payload: String,
}

#[derive(Clone)]
pub struct Store {
    conn: Arc<Mutex<Connection>>,
}

/// Current time as an RFC 3339 UTC string.
pub(crate) fn now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    rfc3339(secs)
}

/// Format Unix seconds as RFC 3339 UTC (civil-from-days algorithm).
pub(crate) fn rfc3339(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3_600,
        rem % 3_600 / 60,
        rem % 60
    )
}

/// Gmail triage progress and the daily summary record. Arrival (`gmail_seen`) is not triage:
/// a row leaves `pending` only after a validated model answer. No column holds an address or a page string;
/// `summary` is a short sanitized model line. `gmail_summary_days` makes delivery once for each local day.
const SCHEMA_V12: &str = "
CREATE TABLE gmail_triage(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','triaged','superseded')),
    category TEXT CHECK(category IN ('reply_needed','actionable','awaiting_reply','fyi')),
    summary TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_failure TEXT,
    triaged_ms INTEGER,
    label_state TEXT NOT NULL DEFAULT 'none' CHECK(label_state IN ('none','applied','uncertain','failed','skipped')),
    label_attempts INTEGER NOT NULL DEFAULT 0,
    summarized_day TEXT,
    PRIMARY KEY(bot_id, account, thread_id, message_id)
);
CREATE TABLE gmail_summary_days(
    bot_id TEXT NOT NULL,
    day TEXT NOT NULL,
    delivered_ms INTEGER NOT NULL,
    PRIMARY KEY(bot_id, day)
);
";

/// Mailbox cleanup. `origin` ranks new mail before the backlog. `gmail_organize` holds the host decision for a revision
/// and the progress of its topic label and its move to Trash. `gmail_org_journal` is written before each write, so a
/// restart sees an open entry and reads before it writes again. `gmail_backlog` is the checkpoint of the backlog walk.
/// No column holds a string from a page or from the model.
const SCHEMA_V13: &str = "
ALTER TABLE gmail_triage ADD COLUMN origin TEXT NOT NULL DEFAULT 'window' CHECK(origin IN ('window','backlog'));
CREATE TABLE gmail_organize(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('keep','topic','trash','review')),
    reason TEXT NOT NULL,
    topic TEXT CHECK(topic IN ('receipts','jobs','tech','review')),
    topic_state TEXT NOT NULL DEFAULT 'none' CHECK(topic_state IN ('none','applied','uncertain','failed')),
    topic_attempts INTEGER NOT NULL DEFAULT 0,
    trash_state TEXT NOT NULL DEFAULT 'none' CHECK(trash_state IN ('none','done','uncertain','failed','refused')),
    trash_attempts INTEGER NOT NULL DEFAULT 0,
    decided_ms INTEGER NOT NULL,
    acted_ms INTEGER,
    tried_ms INTEGER,
    PRIMARY KEY(bot_id, account, thread_id, message_id)
);
CREATE TABLE gmail_org_journal(
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('category','topic','trash')),
    attempt INTEGER NOT NULL,
    started_ms INTEGER NOT NULL,
    outcome TEXT,
    reason TEXT
);
CREATE TABLE gmail_backlog(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    ceiling_s INTEGER NOT NULL,
    page INTEGER NOT NULL DEFAULT 1,
    sweep INTEGER NOT NULL DEFAULT 1,
    sweep_new INTEGER NOT NULL DEFAULT 0,
    queued_total INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'running' CHECK(state IN ('running','complete')),
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY(bot_id, account)
);
";

/// Dynamic topic labels (D106). `gmail_organize.topic` held one of four fixed words behind a CHECK. It now holds a registry
/// key, so the table is rebuilt with every row kept. `gmail_topics` is the registry of a bot. `gmail_topic_labels` says,
/// for each account, whether a topic label may exist (`creating`) or a readback showed it (`verified`). Names are checked
/// by the host before they reach a column.
const SCHEMA_V14: &str = "
CREATE TABLE gmail_organize_new(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('keep','topic','trash','review')),
    reason TEXT NOT NULL,
    topic TEXT,
    topic_state TEXT NOT NULL DEFAULT 'none' CHECK(topic_state IN ('none','applied','uncertain','failed')),
    topic_attempts INTEGER NOT NULL DEFAULT 0,
    trash_state TEXT NOT NULL DEFAULT 'none' CHECK(trash_state IN ('none','done','uncertain','failed','refused')),
    trash_attempts INTEGER NOT NULL DEFAULT 0,
    decided_ms INTEGER NOT NULL,
    acted_ms INTEGER,
    tried_ms INTEGER,
    PRIMARY KEY(bot_id, account, thread_id, message_id)
);
INSERT INTO gmail_organize_new SELECT bot_id, account, thread_id, message_id, kind, decision, reason, topic, topic_state, topic_attempts, trash_state, trash_attempts, decided_ms, acted_ms, tried_ms FROM gmail_organize;
DROP TABLE gmail_organize;
ALTER TABLE gmail_organize_new RENAME TO gmail_organize;
CREATE TABLE gmail_topics(
    bot_id TEXT NOT NULL,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    seed INTEGER NOT NULL DEFAULT 0 CHECK(seed IN (0,1)),
    created_ms INTEGER NOT NULL,
    PRIMARY KEY(bot_id, key)
);
CREATE TABLE gmail_topic_labels(
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('creating','verified')),
    created INTEGER NOT NULL DEFAULT 0,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY(bot_id, account, key)
);
";

fn migrate(conn: &Connection) -> rusqlite::Result<()> {
    migrate_to(conn, i64::MAX)
}

/// Apply each schema step above the stored version, up to `max`. Tests use `max` to build old databases.
pub(crate) fn migrate_to(conn: &Connection, max: i64) -> rusqlite::Result<()> {
    let version: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    let steps = [
        SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6, SCHEMA_V7, SCHEMA_V8,
        SCHEMA_V9, SCHEMA_V10, SCHEMA_V11, SCHEMA_V12, SCHEMA_V13, SCHEMA_V14,
    ];
    for (i, sql) in steps.iter().enumerate() {
        let v = i as i64 + 1;
        if version < v && v <= max {
            conn.execute_batch(&format!("BEGIN; {sql} PRAGMA user_version = {v}; COMMIT;"))?;
        }
    }
    Ok(())
}

impl Store {
    pub(crate) fn from_conn(conn: Connection) -> anyhow::Result<Self> {
        conn.pragma_update(None, "foreign_keys", "ON")?;
        migrate(&conn)?;
        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    /// Open `data_dir/deck.db` and create the folder when needed.
    pub fn open(data_dir: &Path) -> anyhow::Result<Self> {
        std::fs::create_dir_all(data_dir)
            .with_context(|| format!("cannot create {}", data_dir.display()))?;
        let conn = Connection::open(data_dir.join("deck.db"))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        Self::from_conn(conn)
    }

    #[cfg(test)]
    pub fn open_memory() -> anyhow::Result<Self> {
        Self::from_conn(Connection::open_in_memory()?)
    }

    /// Used by the setup worker on a blocking thread before each provisioning side effect.
    pub(crate) fn run_sync<T>(
        &self,
        f: impl FnOnce(&Connection) -> rusqlite::Result<T>,
    ) -> anyhow::Result<T> {
        let conn = self
            .conn
            .lock()
            .map_err(|_| anyhow::anyhow!("store lock poisoned"))?;
        Ok(f(&conn)?)
    }

    pub(crate) async fn run<T: Send + 'static>(
        &self,
        f: impl FnOnce(&Connection) -> rusqlite::Result<T> + Send + 'static,
    ) -> anyhow::Result<T> {
        let conn = self.conn.clone();
        tokio::task::spawn_blocking(move || {
            let conn = conn
                .lock()
                .map_err(|_| anyhow::anyhow!("store lock poisoned"))?;
            Ok(f(&conn)?)
        })
        .await?
    }

    pub async fn create_chat(&self, title: String) -> anyhow::Result<Chat> {
        self.run(move |c| {
            let created_at = now();
            c.execute(
                "INSERT INTO chats(title, created_at) VALUES (?1, ?2)",
                params![title, created_at],
            )?;
            Ok(Chat {
                bot_id: None,
                id: c.last_insert_rowid(),
                title,
                created_at,
            })
        })
        .await
    }

    pub async fn list_chats(&self) -> anyhow::Result<Vec<Chat>> {
        self.run(|c| {
            c.prepare("SELECT c.id, c.title, c.created_at, b.bot_id FROM chats c LEFT JOIN bot_state b ON b.chat_id=c.id ORDER BY c.id DESC")?
                .query_map([], |r| {
                    Ok(Chat {
                        bot_id: r.get(3)?,
                        id: r.get(0)?,
                        title: r.get(1)?,
                        created_at: r.get(2)?,
                    })
                })?
                .collect()
        })
        .await
    }

    pub async fn add_message(
        &self,
        chat_id: i64,
        role: String,
        content_json: String,
    ) -> anyhow::Result<Message> {
        self.run(move |c| {
            let created_at = now();
            c.execute(
                "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?1, ?2, ?3, ?4)",
                params![chat_id, role, content_json, created_at],
            )?;
            Ok(Message {
                id: c.last_insert_rowid(),
                chat_id,
                role,
                content: content_json,
                created_at,
            })
        })
        .await
    }

    pub async fn chat_exists(&self, chat_id: i64) -> anyhow::Result<bool> {
        self.run(move |c| {
            Ok(
                c.query_row("SELECT 1 FROM chats WHERE id = ?1", [chat_id], |_| Ok(()))
                    .optional()?
                    .is_some(),
            )
        })
        .await
    }

    pub async fn create_run(&self, chat_id: i64) -> anyhow::Result<i64> {
        self.run(move |c| {
            c.execute(
                "INSERT INTO runs(chat_id, status, started_at) VALUES (?1, 'running', ?2)",
                params![chat_id, now()],
            )?;
            Ok(c.last_insert_rowid())
        })
        .await
    }

    pub async fn finish_run(&self, run_id: i64, status: String) -> anyhow::Result<()> {
        self.run(move |c| {
            c.execute(
                "UPDATE runs SET status = ?1, ended_at = ?2 WHERE id = ?3",
                params![status, now(), run_id],
            )?;
            Ok(())
        })
        .await
    }

    pub async fn run_status(&self, run_id: i64) -> anyhow::Result<Option<String>> {
        self.run(move |c| {
            c.query_row("SELECT status FROM runs WHERE id = ?1", [run_id], |r| {
                r.get(0)
            })
            .optional()
        })
        .await
    }

    pub async fn add_event(
        &self,
        run_id: i64,
        kind: String,
        payload: String,
    ) -> anyhow::Result<i64> {
        self.run(move |c| {
            c.execute(
                "INSERT INTO events(run_id, kind, payload, created_at) VALUES (?1, ?2, ?3, ?4)",
                params![run_id, kind, payload, now()],
            )?;
            Ok(c.last_insert_rowid())
        })
        .await
    }

    /// Events of all runs of a chat with id greater than `after`, oldest first.
    pub async fn list_events(&self, chat_id: i64, after: i64) -> anyhow::Result<Vec<StoredEvent>> {
        self.run(move |c| {
            c.prepare(
                "SELECT e.id, e.kind, e.payload FROM events e JOIN runs r ON r.id = e.run_id \
                 WHERE r.chat_id = ?1 AND e.id > ?2 ORDER BY e.id",
            )?
            .query_map(params![chat_id, after], |r| {
                Ok(StoredEvent {
                    id: r.get(0)?,
                    kind: r.get(1)?,
                    payload: r.get(2)?,
                })
            })?
            .collect()
        })
        .await
    }

    pub async fn add_file(
        &self,
        chat_id: i64,
        path: String,
        name: String,
        mime: String,
        size: i64,
    ) -> anyhow::Result<FileRow> {
        self.run(move |c| {
            c.execute(
                "INSERT INTO files(chat_id, path, name, mime, size, created_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![chat_id, path, name, mime, size, now()],
            )?;
            Ok(FileRow {
                id: c.last_insert_rowid(),
                chat_id,
                path,
                name,
                mime,
                size,
            })
        })
        .await
    }

    pub async fn get_file(&self, id: i64) -> anyhow::Result<Option<FileRow>> {
        self.run(move |c| {
            c.query_row(
                "SELECT id, chat_id, path, name, mime, size FROM files WHERE id = ?1",
                [id],
                |r| {
                    Ok(FileRow {
                        id: r.get(0)?,
                        chat_id: r.get(1)?,
                        path: r.get(2)?,
                        name: r.get(3)?,
                        mime: r.get(4)?,
                        size: r.get(5)?,
                    })
                },
            )
            .optional()
        })
        .await
    }

    /// Messages of a chat, oldest first. `None` when the chat does not exist.
    pub async fn list_messages(&self, chat_id: i64) -> anyhow::Result<Option<Vec<Message>>> {
        self.run(move |c| {
            let exists = c
                .query_row("SELECT 1 FROM chats WHERE id = ?1", [chat_id], |_| Ok(()))
                .optional()?;
            if exists.is_none() {
                return Ok(None);
            }
            let rows = c
                .prepare(
                    "SELECT id, chat_id, role, content, created_at FROM messages \
                     WHERE chat_id = ?1 ORDER BY id",
                )?
                .query_map([chat_id], |r| {
                    Ok(Message {
                        id: r.get(0)?,
                        chat_id: r.get(1)?,
                        role: r.get(2)?,
                        content: r.get(3)?,
                        created_at: r.get(4)?,
                    })
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            Ok(Some(rows))
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn migration_runs_twice() {
        let conn = Connection::open_in_memory().unwrap();
        migrate(&conn).unwrap();
        migrate(&conn).unwrap();
        let v: i64 = conn
            .query_row("PRAGMA user_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 14);
    }

    #[test]
    fn the_topic_migration_keeps_every_organize_row() {
        let conn = Connection::open_in_memory().unwrap();
        migrate_to(&conn, 13).unwrap();
        conn.execute_batch(
            "INSERT INTO gmail_organize(bot_id, account, thread_id, message_id, kind, decision, reason, topic, topic_state, topic_attempts, trash_state, decided_ms, acted_ms, tried_ms) VALUES \
             ('main','one','0000000000000001','00000000000000a1','tech','topic','useful_topic','tech','applied',1,'none',10,11,12), \
             ('main','one','0000000000000002','00000000000000a1','promotion','trash','unwanted',NULL,'none',0,'done',20,21,22), \
             ('main','two','0000000000000003','00000000000000a1','other','review','unsure_kind','review','uncertain',2,'none',30,NULL,NULL);",
        )
        .unwrap();
        migrate(&conn).unwrap();
        let rows: Vec<(String, Option<String>, String, i64, String)> = conn
            .prepare("SELECT thread_id, topic, topic_state, topic_attempts, trash_state FROM gmail_organize ORDER BY thread_id")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        assert_eq!(rows.len(), 3);
        assert_eq!(
            rows[0],
            (
                "0000000000000001".into(),
                Some("tech".into()),
                "applied".into(),
                1,
                "none".into()
            )
        );
        assert_eq!(rows[1].4, "done");
        assert_eq!(rows[2].2, "uncertain");
        // A registry key now fits the column.
        conn.execute(
            "UPDATE gmail_organize SET topic = 'travel' WHERE thread_id = '0000000000000003'",
            [],
        )
        .unwrap();
        // The old CHECK on decision stays.
        assert!(
            conn.execute("UPDATE gmail_organize SET decision = 'x'", [])
                .is_err()
        );
    }

    #[test]
    fn formats_rfc3339() {
        assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(1_709_210_096), "2024-02-29T12:34:56Z");
    }

    #[tokio::test]
    async fn listed_chats_identify_their_own_bot_for_navigation() {
        let store = Store::open_memory().unwrap();
        let a = store.bot_flags("a", "A").await.unwrap().chat_id;
        let b = store.bot_flags("b", "B").await.unwrap().chat_id;
        let plain = store.create_chat("plain".into()).await.unwrap().id;
        let chats = store.list_chats().await.unwrap();
        assert_eq!(
            chats.iter().find(|c| c.id == a).unwrap().bot_id.as_deref(),
            Some("a")
        );
        assert_eq!(
            chats.iter().find(|c| c.id == b).unwrap().bot_id.as_deref(),
            Some("b")
        );
        assert_eq!(chats.iter().find(|c| c.id == plain).unwrap().bot_id, None);
    }

    #[tokio::test]
    async fn chats_create_and_list() {
        let s = Store::open_memory().unwrap();
        let a = s.create_chat("one".into()).await.unwrap();
        let b = s.create_chat("two".into()).await.unwrap();
        assert_eq!(s.list_chats().await.unwrap(), vec![b, a]);
    }

    #[tokio::test]
    async fn messages_add_and_list() {
        let s = Store::open_memory().unwrap();
        let chat = s.create_chat("c".into()).await.unwrap();
        let m = s
            .add_message(chat.id, "user".into(), "\"hi\"".into())
            .await
            .unwrap();
        assert_eq!(s.list_messages(chat.id).await.unwrap(), Some(vec![m]));
        assert_eq!(s.list_messages(999).await.unwrap(), None);
    }

    #[tokio::test]
    async fn bad_role_and_missing_chat_fail() {
        let s = Store::open_memory().unwrap();
        let chat = s.create_chat("c".into()).await.unwrap();
        assert!(
            s.add_message(chat.id, "bogus".into(), "{}".into())
                .await
                .is_err()
        );
        assert!(
            s.add_message(999, "user".into(), "{}".into())
                .await
                .is_err()
        );
    }
}
