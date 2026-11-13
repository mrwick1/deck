//! Owner requests of an active task: list the open protected-input requests, and wake the
//! waiting task once an owner answer is durable.
//!
//! A wake is one user message that carries a marker with the request id. The marker makes a
//! repeat safe: when a message with the marker is already in the chat, nothing is sent again.
//! The restart path of `Bot` resumes a waiting task whose chat ends with a user message, so the
//! message is also the durable record. The text holds identifiers only, never a value.
//!
//! Crash window of `task_secret`: `record_secret` closes the request before the wake. When the
//! wake fails, the request is `delivered` with a receipt and no marker in the chat. `reconcile`
//! and a repeated post find that state and wake the task without a second delivery.

use super::{
    challenge::Decision,
    secrets::{self, canonical_name},
    setup::Step,
};
use crate::{api::AppState, auth::User, store::Store};
use axum::{
    Extension, Json, Router,
    extract::{Path, State},
    http::StatusCode,
    routing::get,
};
use futures_util::future::BoxFuture;
use rusqlite::OptionalExtension;
use serde::Serialize;
use serde_json::{Value, json};

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/bots/{id}/secret-requests", get(list))
}

/// One open request. Metadata only: there is no value field.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Open {
    pub request_id: String,
    pub label: String,
    pub purpose: String,
    /// Unix seconds.
    pub expires_at: u64,
}

/// Sends one user message to the waiting task. It returns the id of the task that got it.
pub type Wake<'a> = &'a (dyn Fn(String) -> BoxFuture<'static, Result<String, String>> + Sync);

pub fn delivered_text(request: &str, label: &str) -> String {
    format!(
        "Protected value delivered for [request {request}] as ${{{}}}. Continue.",
        canonical_name(label)
    )
}

pub fn expired_text(request: &str) -> String {
    format!(
        "Protected input [request {request}] expired before a value arrived. Request it again if the work still needs it."
    )
}

pub fn challenge_marker(request: &str, d: Decision) -> String {
    let word = if d == Decision::Completed {
        "completed"
    } else {
        "cancelled"
    };
    format!("[challenge {request} {word}]")
}

pub fn challenge_text(request: &str, d: Decision) -> String {
    let marker = challenge_marker(request, d);
    match d {
        Decision::Completed => format!(
            "The owner marked {marker}. This does not prove the sign-in worked. Call challenge_state for request {request} and verify the service before you continue."
        ),
        Decision::Cancel => format!(
            "The owner cancelled {marker}. Do not retry this sign-in. Report what is blocked."
        ),
    }
}

#[derive(Debug, Clone)]
struct Row {
    request_id: String,
    task_id: String,
    label: String,
    state: String,
    expires_at: u64,
}

fn row(r: &rusqlite::Row) -> rusqlite::Result<Row> {
    Ok(Row {
        request_id: r.get(0)?,
        task_id: r.get(1)?,
        label: r.get(2)?,
        state: r.get(3)?,
        expires_at: r.get::<_, i64>(4)? as u64,
    })
}

const COLS: &str = "request_id,task_id,label,state,expires_at";

impl Store {
    /// Open requests whose task still waits. An expired request is not open.
    pub async fn open_secret_requests(&self, bot: &str, now: u64) -> anyhow::Result<Vec<Open>> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.prepare(
                "SELECT r.request_id,r.label,r.purpose,r.expires_at FROM secret_requests r \
                 JOIN tasks t ON t.id=r.task_id AND t.bot_id=r.bot_id \
                 WHERE r.bot_id=?1 AND r.state='pending' AND r.expires_at>?2 AND t.state='waiting' \
                 ORDER BY r.created_at DESC, r.rowid DESC",
            )?
            .query_map(rusqlite::params![bot, now as i64], |r| {
                Ok(Open {
                    request_id: r.get(0)?,
                    label: r.get(1)?,
                    purpose: r.get(2)?,
                    expires_at: r.get::<_, i64>(3)? as u64,
                })
            })?
            .collect()
        })
        .await
    }

    async fn secret_request_row(&self, bot: &str, id: &str) -> anyhow::Result<Option<Row>> {
        let (bot, id) = (bot.to_owned(), id.to_owned());
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {COLS} FROM secret_requests WHERE bot_id=?1 AND request_id=?2"),
                [&bot, &id],
                row,
            )
            .optional()
        })
        .await
    }

    /// The newest request of a task in the given state.
    async fn newest_secret_request(
        &self,
        bot: &str,
        task: &str,
        state: &str,
    ) -> anyhow::Result<Option<Row>> {
        let (bot, task, state) = (bot.to_owned(), task.to_owned(), state.to_owned());
        self.run(move |c| {
            c.query_row(
                &format!("SELECT {COLS} FROM secret_requests WHERE bot_id=?1 AND task_id=?2 AND state=?3 ORDER BY rowid DESC LIMIT 1"),
                [&bot, &task, &state],
                row,
            )
            .optional()
        })
        .await
    }

    async fn supersede_secret_request(&self, bot: &str, id: &str) -> anyhow::Result<()> {
        let (bot, id) = (bot.to_owned(), id.to_owned());
        self.run(move |c| {
            c.execute(
                "UPDATE secret_requests SET state='superseded' WHERE bot_id=?1 AND request_id=?2 AND state='pending'",
                [&bot, &id],
            )
            .map(drop)
        })
        .await
    }

    /// Whether the chat already holds a user message with this marker.
    async fn has_user_marker(&self, chat: i64, marker: &str) -> anyhow::Result<bool> {
        let like = format!("%{}%", marker.replace(['\\', '%', '_'], "_"));
        self.run(move |c| {
            c.query_row(
                "SELECT EXISTS(SELECT 1 FROM messages WHERE chat_id=?1 AND role='user' AND content LIKE ?2)",
                rusqlite::params![chat, like],
                |r| r.get(0),
            )
        })
        .await
    }
}

/// What happened to a wake.
#[derive(Debug, PartialEq)]
pub enum Woke {
    Sent,
    /// The chat already holds this wake.
    Already,
    /// No waiting task owns the request. Nothing was sent.
    NoTask,
}

/// Wake the bot's waiting task `task_id` with `text`, once per `marker`. Pause, budget hold and
/// unknown-outcome holds stay in force: `Bot::submit` queues the message and the step loop gates
/// the run.
pub async fn wake(
    store: &Store,
    bot: &str,
    task_id: &str,
    marker: &str,
    text: String,
    send: Wake<'_>,
) -> Result<Woke, String> {
    let task = store
        .owner_task(bot)
        .await
        .map_err(|_| "internal error".to_string())?
        .filter(|t| t.id == task_id && t.state == "waiting");
    let Some(task) = task else {
        return Ok(Woke::NoTask);
    };
    if store
        .has_user_marker(task.chat_id, marker)
        .await
        .map_err(|_| "internal error".to_string())?
    {
        return Ok(Woke::Already);
    }
    if send(text).await? != task.id {
        return Err("the message reached another task".into());
    }
    Ok(Woke::Sent)
}

/// Heal what a crash or a refused wake left behind for the waiting task of `bot`:
/// a delivered request that never woke the task, an expired request, and an answered
/// sign-in help request. Returns the first failure after it tried each.
pub async fn reconcile(store: &Store, bot: &str, now: u64, send: Wake<'_>) -> Result<(), String> {
    let internal = |_| "internal error".to_string();
    let Some(task) = store
        .owner_task(bot)
        .await
        .map_err(internal)?
        .filter(|t| t.state == "waiting")
    else {
        return Ok(());
    };
    let mut first = None;
    let mut note = |r: Result<Woke, String>| {
        if let Err(e) = r {
            first.get_or_insert(e);
        }
    };
    if let Some(r) = store
        .newest_secret_request(bot, &task.id, "delivered")
        .await
        .map_err(internal)?
        && store
            .secret_receipt(bot, &r.request_id)
            .await
            .map_err(internal)?
            == Some(canonical_name(&r.label))
    {
        let marker = format!("[request {}]", r.request_id);
        note(
            wake(
                store,
                bot,
                &task.id,
                &marker,
                delivered_text(&r.request_id, &r.label),
                send,
            )
            .await,
        );
    }
    if let Some(r) = store
        .newest_secret_request(bot, &task.id, "pending")
        .await
        .map_err(internal)?
        .filter(|r| r.expires_at <= now)
    {
        let marker = format!("[request {}]", r.request_id);
        match wake(
            store,
            bot,
            &task.id,
            &marker,
            expired_text(&r.request_id),
            send,
        )
        .await
        {
            // The request is settled once the task knows. A later request supersedes it anyway.
            Ok(_) => {
                let _ = store.supersede_secret_request(bot, &r.request_id).await;
            }
            e => note(e),
        }
    }
    if let Some(w) = store
        .setup_record(bot)
        .await
        .map_err(internal)?
        .and_then(|r| r.setup.wait)
        .filter(|w| w.answered && w.step == Step::Login(task.id.clone()))
    {
        let d = Decision::Completed;
        note(
            wake(
                store,
                bot,
                &task.id,
                &challenge_marker(&w.request_id, d),
                challenge_text(&w.request_id, d),
                send,
            )
            .await,
        );
    }
    first.map_or(Ok(()), Err)
}

/// A repeated post for a request that is already `delivered`: wake without delivering again.
/// `Ok(None)` means the request is not in that state.
pub async fn resume_delivered(
    store: &Store,
    bot: &str,
    request: &str,
    send: Wake<'_>,
) -> Result<Option<Woke>, String> {
    let internal = |_| "internal error".to_string();
    let Some(r) = store
        .secret_request_row(bot, request)
        .await
        .map_err(internal)?
    else {
        return Ok(None);
    };
    if r.state != "delivered"
        || store.secret_receipt(bot, request).await.map_err(internal)?
            != Some(canonical_name(&r.label))
    {
        return Ok(None);
    }
    let marker = format!("[request {request}]");
    wake(
        store,
        bot,
        &r.task_id,
        &marker,
        delivered_text(request, &r.label),
        send,
    )
    .await
    .map(Some)
}

/// A post for a request that expired: tell the task, so it can ask again, and close the request.
/// `Ok(None)` means the request is not an expired pending one.
pub async fn expire(
    store: &Store,
    bot: &str,
    request: &str,
    now: u64,
    send: Wake<'_>,
) -> Result<Option<Woke>, String> {
    let internal = |_| "internal error".to_string();
    let Some(r) = store
        .secret_request_row(bot, request)
        .await
        .map_err(internal)?
        .filter(|r| r.state == "pending" && r.expires_at <= now)
    else {
        return Ok(None);
    };
    let marker = format!("[request {request}]");
    let woke = wake(store, bot, &r.task_id, &marker, expired_text(request), send).await?;
    if woke != Woke::NoTask {
        store
            .supersede_secret_request(bot, request)
            .await
            .map_err(internal)?;
    }
    Ok(Some(woke))
}

/// The owner answered a sign-in help request of the task `key`. Wake that task and no other.
pub async fn resume_challenge(
    store: &Store,
    bot: &str,
    key: &str,
    request: &str,
    d: Decision,
    send: Wake<'_>,
) -> Result<Woke, String> {
    wake(
        store,
        bot,
        key,
        &challenge_marker(request, d),
        challenge_text(request, d),
        send,
    )
    .await
}

/// The wake of a real bot: a steering message to the task that waits.
pub fn sender(
    bot: std::sync::Arc<super::Bot>,
) -> impl Fn(String) -> BoxFuture<'static, Result<String, String>> + Sync {
    move |text| {
        let bot = bot.clone();
        Box::pin(async move {
            bot.submit(text, super::Mode::Steer, super::db::HIGH)
                .await
                .map(|s| s.task_id)
                .map_err(|e| match e {
                    super::BotError::Conflict(m) => m,
                    _ => "the task did not resume".into(),
                })
        })
    }
}

/// Metadata of the open protected-input requests of one bot. It also heals a task that an
/// earlier wake missed. A delivery in progress skips the healing, so no wake can run twice.
async fn list(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Extension(User(_owner)): Extension<User>,
) -> Result<Json<Vec<Open>>, (StatusCode, Json<Value>)> {
    let fail = |m: &str| (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": m})));
    let now = secrets::unix_now();
    if let Some(bot) = st
        .setups
        .as_ref()
        .and_then(|s| s.runtime(&id))
        .map(|r| r.bot.clone())
        && let Ok(_one) = secrets::DELIVERY.try_lock()
    {
        let send = sender(bot);
        // A hold refuses the wake. The next poll tries again once the owner lifts it.
        let _ = reconcile(&st.store, &id, now, &send).await;
    }
    st.store
        .open_secret_requests(&id, now)
        .await
        .map(Json)
        .map_err(|_| fail("internal error"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bot::setup_api::tests::app;
    use axum::{body::Body, http::Request};
    use std::sync::{Arc, Mutex};
    use tower::ServiceExt;

    const FUTURE: u64 = 4_000_000_000;

    /// A fake bot: records each wake, answers with the task id, and adds the user message as
    /// `Bot::submit` does.
    struct Fake {
        store: Store,
        chat: i64,
        task: String,
        sent: Arc<Mutex<Vec<String>>>,
        fail: Arc<Mutex<Option<String>>>,
    }

    impl Fake {
        fn send(
            &self,
        ) -> impl Fn(String) -> BoxFuture<'static, Result<String, String>> + Sync + use<> {
            let (store, chat, task) = (self.store.clone(), self.chat, self.task.clone());
            let (sent, fail) = (self.sent.clone(), self.fail.clone());
            move |text| {
                let (store, task, sent, fail) =
                    (store.clone(), task.clone(), sent.clone(), fail.clone());
                Box::pin(async move {
                    if let Some(e) = fail.lock().unwrap().clone() {
                        return Err(e);
                    }
                    store
                        .add_message(
                            chat,
                            "user".into(),
                            json!({"role":"user","content":text}).to_string(),
                        )
                        .await
                        .unwrap();
                    sent.lock().unwrap().push(text);
                    Ok(task)
                })
            }
        }
        fn count(&self) -> usize {
            self.sent.lock().unwrap().len()
        }
    }

    async fn waiting_task() -> Fake {
        let store = Store::open_memory().unwrap();
        let chat = store.create_chat("bot".into()).await.unwrap().id;
        store
            .run(move |c| {
                c.execute(
                    "INSERT INTO bot_state(bot_id,chat_id) VALUES ('b',?1)",
                    [chat],
                )
                .map(drop)
            })
            .await
            .unwrap();
        let t = store
            .add_task("b", chat, "message", "t".into(), "t".into(), 1)
            .await
            .unwrap();
        store
            .move_task(&t.id, &["queued"], "running", None)
            .await
            .unwrap();
        store
            .move_task(&t.id, &["running"], "waiting", None)
            .await
            .unwrap();
        Fake {
            store,
            chat,
            task: t.id,
            sent: Arc::default(),
            fail: Arc::default(),
        }
    }

    async fn open(f: &Fake, id: &str, expires: u64) {
        f.store
            .create_secret_request(id, "b", &f.task, "Site pass", "to sign in", expires)
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn only_a_pending_unexpired_request_of_a_waiting_task_is_listed_without_a_value() {
        let f = waiting_task().await;
        open(&f, "secret-1", FUTURE).await;
        let got = f.store.open_secret_requests("b", 10).await.unwrap();
        assert_eq!(
            serde_json::to_value(&got).unwrap(),
            json!([{"request_id":"secret-1","label":"Site pass","purpose":"to sign in","expires_at":FUTURE}])
        );
        assert!(
            f.store
                .open_secret_requests("other", 10)
                .await
                .unwrap()
                .is_empty()
        );
        assert!(
            f.store
                .open_secret_requests("b", FUTURE)
                .await
                .unwrap()
                .is_empty(),
            "expired"
        );
        // A newer request replaces the older one.
        open(&f, "secret-2", FUTURE).await;
        let ids: Vec<_> = f
            .store
            .open_secret_requests("b", 10)
            .await
            .unwrap()
            .into_iter()
            .map(|o| o.request_id)
            .collect();
        assert_eq!(ids, ["secret-2"]);
        // A task that no longer waits has no open request.
        f.store
            .move_task(&f.task, &["waiting"], "running", None)
            .await
            .unwrap();
        assert!(
            f.store
                .open_secret_requests("b", 10)
                .await
                .unwrap()
                .is_empty()
        );
    }

    #[tokio::test]
    async fn the_route_needs_the_owner_and_answers_with_the_exact_shape() {
        let (app, store, _, _) = app();
        let get = |owner: bool, path: &'static str| {
            let mut r = Request::builder().uri(path);
            if owner {
                r = r.header("Remote-User", "test-owner");
            }
            app.clone().oneshot(r.body(Body::empty()).unwrap())
        };
        assert_eq!(
            get(false, "/api/bots/b/secret-requests")
                .await
                .unwrap()
                .status(),
            StatusCode::UNAUTHORIZED
        );
        let res = get(true, "/api/bots/b/secret-requests").await.unwrap();
        assert_eq!(res.status(), StatusCode::OK);
        let body = axum::body::to_bytes(res.into_body(), 1 << 20)
            .await
            .unwrap();
        assert_eq!(serde_json::from_slice::<Value>(&body).unwrap(), json!([]));
        drop(store);
    }

    #[tokio::test]
    async fn a_failed_resume_after_delivery_is_healed_once_without_redelivery() {
        let f = waiting_task().await;
        open(&f, "secret-1", FUTURE).await;
        let name = canonical_name("Site pass");
        // The crash window: the request is closed and the receipt exists, but no wake happened.
        f.store
            .record_secret("b", "secret-1", "Site pass", &name, Some("secret-1"))
            .await
            .unwrap();
        assert!(
            f.store
                .open_secret_requests("b", 10)
                .await
                .unwrap()
                .is_empty()
        );
        *f.fail.lock().unwrap() = Some("a budget hold blocks the task".into());
        assert!(reconcile(&f.store, "b", 10, &f.send()).await.is_err());
        assert_eq!(f.count(), 0);
        *f.fail.lock().unwrap() = None;
        reconcile(&f.store, "b", 10, &f.send()).await.unwrap();
        reconcile(&f.store, "b", 10, &f.send()).await.unwrap();
        assert_eq!(f.count(), 1, "one wake");
        assert!(f.sent.lock().unwrap()[0].contains("[request secret-1]"));
        // A repeated post finds the same state and does not wake again.
        assert_eq!(
            resume_delivered(&f.store, "b", "secret-1", &f.send())
                .await
                .unwrap(),
            Some(Woke::Already)
        );
        assert_eq!(f.count(), 1);
    }

    #[tokio::test]
    async fn a_repeated_post_wakes_a_delivered_request_that_was_not_resumed() {
        let f = waiting_task().await;
        open(&f, "secret-1", FUTURE).await;
        assert_eq!(
            resume_delivered(&f.store, "b", "secret-1", &f.send())
                .await
                .unwrap(),
            None,
            "still pending"
        );
        f.store
            .record_secret(
                "b",
                "secret-1",
                "Site pass",
                &canonical_name("Site pass"),
                Some("secret-1"),
            )
            .await
            .unwrap();
        assert_eq!(
            resume_delivered(&f.store, "b", "secret-1", &f.send())
                .await
                .unwrap(),
            Some(Woke::Sent)
        );
        assert_eq!(
            resume_delivered(&f.store, "other", "secret-1", &f.send())
                .await
                .unwrap(),
            None,
            "another bot"
        );
        assert_eq!(f.count(), 1);
    }

    #[tokio::test]
    async fn an_expired_request_tells_the_task_to_ask_again_exactly_once() {
        let f = waiting_task().await;
        open(&f, "secret-1", 100).await;
        assert_eq!(
            expire(&f.store, "b", "secret-1", 50, &f.send())
                .await
                .unwrap(),
            None,
            "not yet"
        );
        assert_eq!(
            expire(&f.store, "b", "secret-1", 100, &f.send())
                .await
                .unwrap(),
            Some(Woke::Sent)
        );
        assert!(f.sent.lock().unwrap()[0].contains("expired"));
        assert_eq!(
            expire(&f.store, "b", "secret-1", 101, &f.send())
                .await
                .unwrap(),
            None,
            "closed"
        );
        reconcile(&f.store, "b", 200, &f.send()).await.unwrap();
        assert_eq!(f.count(), 1);
        // Reconcile alone also closes an expired request, once.
        open(&f, "secret-2", 300).await;
        reconcile(&f.store, "b", 400, &f.send()).await.unwrap();
        reconcile(&f.store, "b", 401, &f.send()).await.unwrap();
        assert_eq!(f.count(), 2);
    }

    #[tokio::test]
    async fn a_wake_never_reaches_a_task_that_does_not_own_the_request() {
        let f = waiting_task().await;
        let other = f
            .store
            .add_task("b", f.chat, "message", "o".into(), "o".into(), 1)
            .await
            .unwrap();
        let d = Decision::Completed;
        assert_eq!(
            resume_challenge(&f.store, "b", &other.id, "b:1", d, &f.send())
                .await
                .unwrap(),
            Woke::NoTask,
            "queued, not waiting"
        );
        assert_eq!(
            resume_challenge(&f.store, "b", "no-such", "b:1", d, &f.send())
                .await
                .unwrap(),
            Woke::NoTask
        );
        assert_eq!(
            resume_challenge(&f.store, "x", &f.task, "b:1", d, &f.send())
                .await
                .unwrap(),
            Woke::NoTask,
            "another bot"
        );
        assert_eq!(f.count(), 0);
        assert_eq!(
            resume_challenge(&f.store, "b", &f.task, "b:1", d, &f.send())
                .await
                .unwrap(),
            Woke::Sent
        );
        assert_eq!(
            resume_challenge(&f.store, "b", &f.task, "b:1", d, &f.send())
                .await
                .unwrap(),
            Woke::Already
        );
        // A cancel is a different fact from a completion of the same request.
        assert_eq!(
            resume_challenge(&f.store, "b", &f.task, "b:1", Decision::Cancel, &f.send())
                .await
                .unwrap(),
            Woke::Sent
        );
        let sent = f.sent.lock().unwrap().clone();
        assert!(sent[0].contains("challenge_state") && sent[0].contains("b:1"));
        assert!(sent[1].contains("cancelled"));
        // A wake that lands on a different task is an error, not a success.
        let g = waiting_task().await;
        *g.fail.lock().unwrap() = None;
        let wrong = |_t: String| -> BoxFuture<'static, Result<String, String>> {
            Box::pin(async { Ok("other".to_string()) })
        };
        assert!(
            resume_challenge(&g.store, "b", &g.task, "b:2", d, &wrong)
                .await
                .is_err()
        );
    }
}
