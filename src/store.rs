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

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Chat {
    pub id: i64,
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
fn rfc3339(secs: i64) -> String {
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

fn migrate(conn: &Connection) -> rusqlite::Result<()> {
    let version: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    if version < 1 {
        conn.execute_batch(&format!(
            "BEGIN; {SCHEMA_V1} PRAGMA user_version = 1; COMMIT;"
        ))?;
    }
    if version < 2 {
        conn.execute_batch(&format!(
            "BEGIN; {SCHEMA_V2} PRAGMA user_version = 2; COMMIT;"
        ))?;
    }
    if version < 3 {
        conn.execute_batch(&format!(
            "BEGIN; {SCHEMA_V3} PRAGMA user_version = 3; COMMIT;"
        ))?;
    }
    if version < 4 {
        conn.execute_batch(&format!(
            "BEGIN; {SCHEMA_V4} PRAGMA user_version = 4; COMMIT;"
        ))?;
    }
    Ok(())
}

impl Store {
    fn from_conn(conn: Connection) -> anyhow::Result<Self> {
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
                id: c.last_insert_rowid(),
                title,
                created_at,
            })
        })
        .await
    }

    pub async fn list_chats(&self) -> anyhow::Result<Vec<Chat>> {
        self.run(|c| {
            c.prepare("SELECT id, title, created_at FROM chats ORDER BY id DESC")?
                .query_map([], |r| {
                    Ok(Chat {
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
        assert_eq!(v, 4);
    }

    #[test]
    fn formats_rfc3339() {
        assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(1_709_210_096), "2024-02-29T12:34:56Z");
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
