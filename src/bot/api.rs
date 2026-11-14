//! HTTP routes of the bot runtime. They sit behind the same auth as the rest of the API,
//! except `/api/bot/events`, which takes a bearer token for machine senders.

use std::convert::Infallible;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use futures_util::Stream;
use serde::Deserialize;
use serde_json::{Value, json};

use super::authority::{CeilingResponse, Rejection, validate};
use super::{Bot, BotError, Mode};

impl IntoResponse for BotError {
    fn into_response(self) -> Response {
        let (s, m) = match self {
            Self::Invalid(m) => (StatusCode::BAD_REQUEST, m.to_string()),
            Self::NotFound => (StatusCode::NOT_FOUND, "not found".into()),
            Self::Conflict(m) => (StatusCode::CONFLICT, m),
        };
        (s, Json(json!({ "error": m }))).into_response()
    }
}

type R<T> = Result<T, BotError>;
type B = State<Arc<Bot>>;

/// Routes that need a signed-in user.
pub fn routes() -> Router<Arc<Bot>> {
    Router::new()
        .route("/api/bot", get(status))
        .route("/api/bot/stream", get(stream))
        .route("/api/bot/messages", post(message))
        .route("/api/bot/stop", post(stop))
        .route("/api/bot/automation", post(automation))
        .route("/api/bot/sleep", post(sleep))
        .route("/api/bot/wake", post(wake))
        .route("/api/bot/budget-hold", post(budget_hold))
        .route("/api/bot/budget-hold/approve", post(budget_approve))
        .route("/api/bot/ceiling-requests/respond", post(ceiling_respond))
        .route("/api/bot/tasks", get(tasks))
        .route("/api/bot/tasks/{id}/cancel", post(task_cancel))
        .route("/api/bot/tasks/{id}/priority", post(task_priority))
        .route("/api/bot/tasks/{id}/retry", post(task_retry))
        .route("/api/bot/inbox", get(inbox))
        .route("/api/bot/inbox/commands/{id}", post(decide))
        .route("/api/bot/screen", get(screen_stream))
        .route("/api/bot/screen/latest", get(screen_latest))
}

/// The event intake. The caller authenticates with a bearer token, not a proxy header.
pub fn event_routes() -> Router<Arc<Bot>> {
    Router::new().route("/api/bot/events", post(ingest))
}

async fn status(State(b): B) -> R<Json<Value>> {
    Ok(Json(b.status().await?))
}

/// Send the status on each change. A slow client skips states: it only needs the newest.
async fn stream(State(b): B) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let mut rx = b.watch_changes();
    let s = async_stream::stream! {
        loop {
            if let Ok(v) = b.status().await {
                yield Ok(Event::default().event("status").data(v.to_string()));
            }
            if rx.changed().await.is_err() {
                return;
            }
            // Merge a burst of changes into one status.
            tokio::time::sleep(Duration::from_millis(100)).await;
            rx.borrow_and_update();
        }
    };
    Sse::new(s).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

#[derive(Deserialize)]
pub struct Message {
    pub text: Option<String>,
    pub mode: Option<String>,
    pub priority: Option<i64>,
}

pub fn parse_mode(m: Option<&str>) -> R<Mode> {
    match m {
        None | Some("steer") => Ok(Mode::Steer),
        Some("queue") => Ok(Mode::Queue),
        _ => Err(BotError::Invalid("mode must be steer or queue")),
    }
}

async fn message(State(b): B, Json(m): Json<Message>) -> R<(StatusCode, Json<Value>)> {
    let text = m.text.ok_or(BotError::Invalid("text is required"))?;
    let mode = parse_mode(m.mode.as_deref())?;
    let r = b
        .submit(text, mode, m.priority.unwrap_or(super::db::NORMAL))
        .await?;
    Ok((StatusCode::ACCEPTED, Json(serde_json::to_value(r).unwrap())))
}

async fn stop(State(b): B) -> R<Json<Value>> {
    Ok(Json(serde_json::to_value(b.stop().await?).unwrap()))
}

#[derive(Deserialize)]
struct Paused {
    paused: bool,
}

async fn automation(State(b): B, Json(p): Json<Paused>) -> R<StatusCode> {
    b.set_paused(p.paused).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn sleep(State(b): B) -> R<StatusCode> {
    b.sleep().await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn wake(State(b): B) -> R<StatusCode> {
    b.wake().await?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
struct Hold {
    reason: Option<String>,
}

async fn budget_hold(State(b): B, Json(h): Json<Hold>) -> R<StatusCode> {
    let reason = h.reason.unwrap_or_else(|| "held by the owner".into());
    b.hold_budget(reason.chars().take(200).collect()).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn budget_approve(State(b): B) -> R<StatusCode> {
    b.approve_budget().await?;
    Ok(StatusCode::NO_CONTENT)
}

/// Only authenticated owner routes reach this handler. Event intake cannot answer it.
async fn ceiling_respond(
    State(b): B,
    Extension(crate::auth::User(actor)): Extension<crate::auth::User>,
    body: Bytes,
) -> Response {
    let response = apply_ceiling_answer(&b.store, &b.id, &actor, body).await;
    if response.status().is_success() {
        b.touch();
        b.kick();
    }
    response
}

async fn apply_ceiling_answer(
    store: &crate::store::Store,
    bot: &str,
    actor: &str,
    body: Bytes,
) -> Response {
    let r: CeilingResponse = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(_) => return reject(Rejection::Invalid("the body is not a ceiling answer")),
    };
    if let Err(e) = validate(&r) {
        return reject(e);
    }
    use super::state::Outcome;
    match store.answer_ceiling(bot, actor, &r).await {
        Ok(Outcome::Applied(_) | Outcome::Duplicate(_)) => StatusCode::NO_CONTENT.into_response(),
        Ok(_) => reject(Rejection::Stale),
        Err(e) => {
            tracing::error!("ceiling response failed: {e:#}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

fn reject(e: Rejection) -> Response {
    match e {
        Rejection::Invalid(m) => BotError::Invalid(m).into_response(),
        Rejection::Stale => {
            BotError::Conflict("the request changed; reload it".into()).into_response()
        }
    }
}

async fn tasks(State(b): B) -> R<Json<Value>> {
    Ok(Json(b.status().await?["tasks"].clone()))
}

async fn task_cancel(State(b): B, Path(id): Path<String>) -> R<StatusCode> {
    b.cancel_task(&id).await?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
struct Prio {
    priority: i64,
}

async fn task_priority(State(b): B, Path(id): Path<String>, Json(p): Json<Prio>) -> R<StatusCode> {
    b.set_priority(&id, p.priority).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn task_retry(State(b): B, Path(id): Path<String>) -> R<StatusCode> {
    b.retry_task(&id).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn inbox(State(b): B) -> R<Json<Value>> {
    Ok(Json(serde_json::to_value(b.inbox().await?).unwrap()))
}

#[derive(Deserialize)]
struct Decision {
    decision: String,
    note: Option<String>,
}

async fn decide(State(b): B, Path(id): Path<String>, Json(d): Json<Decision>) -> R<StatusCode> {
    b.decide(&id, &d.decision, d.note.as_deref().unwrap_or(""))
        .await?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
struct NewEvent {
    source: String,
    id: String,
    kind: String,
    #[serde(default)]
    payload: Value,
    priority: Option<i64>,
}

/// Compare in constant time, so the response time does not leak the token.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

async fn ingest(State(b): B, h: HeaderMap, Json(e): Json<NewEvent>) -> R<Response> {
    let Some(env) = b.events_token_env() else {
        return Err(BotError::NotFound);
    };
    let token = std::env::var(env).unwrap_or_default();
    let given = h
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("");
    if token.is_empty() || !same(token.as_bytes(), given.as_bytes()) {
        return Ok(StatusCode::UNAUTHORIZED.into_response());
    }
    let fresh = b
        .ingest(
            &e.source,
            &e.id,
            &e.kind,
            e.payload,
            e.priority.unwrap_or(super::db::NORMAL),
        )
        .await?;
    Ok((
        if fresh {
            StatusCode::ACCEPTED
        } else {
            StatusCode::OK
        },
        Json(json!({ "duplicate": !fresh })),
    )
        .into_response())
}

/// Send the sequence number of each new frame. The client fetches the PNG when it needs it.
/// The subscription holds the capture worker: when the client leaves, the worker stops.
async fn screen_stream(State(b): B) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let mut rx = b.screen.watch();
    let s = async_stream::stream! {
        loop {
            let seq = rx.borrow_and_update().seq;
            yield Ok(Event::default().event("frame").data(json!({"seq": seq}).to_string()));
            if rx.changed().await.is_err() {
                return;
            }
        }
    };
    Sse::new(s).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

async fn screen_latest(State(b): B) -> Response {
    latest_response(&b.screen)
}

fn latest_response(screen: &crate::bot::screen::Screen) -> Response {
    let f = screen.latest();
    if f.png.is_empty() {
        return StatusCode::NO_CONTENT.into_response();
    }
    (
        [
            (header::CONTENT_TYPE, "image/png".to_string()),
            (header::CACHE_CONTROL, "no-store".to_string()),
            (header::ETAG, format!("\"{}\"", f.seq)),
        ],
        f.png.as_ref().clone(),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn answer(body: &str) -> (StatusCode, Value) {
        let store = crate::store::Store::open_memory().unwrap();
        let res = apply_ceiling_answer(&store, "b", "owner", Bytes::from(body.to_string())).await;
        let status = res.status();
        let bytes = axum::body::to_bytes(res.into_body(), 4096).await.unwrap();
        (
            status,
            serde_json::from_slice(&bytes).unwrap_or(Value::Null),
        )
    }

    const GOOD: &str = r#"{"request_id":"r1","version":1,"decision":"approve","amount_minor":5000,"currency":"USD","precision":2,"idempotency_key":"k"}"#;

    #[tokio::test]
    async fn a_malformed_ceiling_answer_is_a_bad_request() {
        for body in [
            "not json",
            "{}",
            r#"{"request_id":"r1","version":1,"decision":"approve","idempotency_key":"k"}"#,
            r#"{"request_id":"r1","version":1,"decision":"deny","idempotency_key":"k","by":"bot"}"#,
        ] {
            assert_eq!(answer(body).await.0, StatusCode::BAD_REQUEST, "{body}");
        }
    }

    #[tokio::test]
    async fn a_valid_ceiling_answer_with_no_pending_request_is_stale() {
        let (s, v) = answer(GOOD).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert!(v["error"].as_str().unwrap().contains("request changed"));
    }

    #[test]
    fn the_machine_route_carries_no_owner_control() {
        let src = include_str!("api.rs");
        let events = src
            .split("pub fn event_routes()")
            .nth(1)
            .and_then(|s| s.split("\n}\n").next())
            .unwrap();
        assert_eq!(events.matches(".route(").count(), 1);
        assert!(events.contains("\"/api/bot/events\""));
        let owner = src
            .split("pub fn routes()")
            .nth(1)
            .and_then(|s| s.split("\n}\n").next())
            .unwrap();
        assert!(owner.contains("/api/bot/ceiling-requests/respond"));
    }

    struct Frames(std::sync::Mutex<Vec<u8>>, std::sync::atomic::AtomicUsize);
    impl crate::bot::screen::Capture for Frames {
        fn grab(&self) -> futures_util::future::BoxFuture<'_, anyhow::Result<Vec<u8>>> {
            Box::pin(async move {
                self.1.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                Ok(self.0.lock().unwrap().clone())
            })
        }
    }

    async fn latest(s: &crate::bot::screen::Screen) -> (StatusCode, Vec<u8>) {
        let r = latest_response(s);
        let status = r.status();
        let body = axum::body::to_bytes(r.into_body(), 1 << 20).await.unwrap();
        (status, body.to_vec())
    }

    #[tokio::test]
    async fn latest_is_204_with_no_image_while_the_real_gate_is_open_and_resumes_public() {
        use std::sync::atomic::Ordering::Relaxed;
        let store = crate::store::Store::open_memory().unwrap();
        let cap = Arc::new(Frames(
            std::sync::Mutex::new(b"\x89PNG-public-1".to_vec()),
            Default::default(),
        ));
        let (_, awake) = tokio::sync::watch::channel(true);
        let s = crate::bot::screen::Screen::new(cap.clone(), Duration::from_millis(10), awake);
        let (st, id) = (store.clone(), "b".to_string());
        // The same gate closure that Bot::new and the managed proxy install.
        s.set_privacy_gate(Arc::new(move || st.privacy_gate_open_sync(&id)));
        let _rx = s.watch();
        // Positive control: a public frame is served with its image.
        for _ in 0..200 {
            if latest(&s).await.0 == StatusCode::OK {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(
            latest(&s).await,
            (StatusCode::OK, b"\x89PNG-public-1".to_vec())
        );
        // The gate opens through the real store write. The route answers 204 with no body.
        store
            .record_secret("b", "r1", "l", "n", None)
            .await
            .unwrap();
        assert_eq!(latest(&s).await, (StatusCode::NO_CONTENT, vec![]));
        tokio::time::sleep(Duration::from_millis(60)).await;
        let n = cap.1.load(Relaxed);
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert_eq!(cap.1.load(Relaxed), n, "captured while the gate was open");
        // The gate shuts. The old frame stays gone. A new public frame replaces it.
        *cap.0.lock().unwrap() = b"\x89PNG-public-2".to_vec();
        store.close_privacy_gate("b").await.unwrap();
        for _ in 0..200 {
            let (code, body) = latest(&s).await;
            assert_ne!(
                body,
                b"\x89PNG-public-1".to_vec(),
                "the old frame came back"
            );
            if code == StatusCode::OK {
                assert_eq!(body, b"\x89PNG-public-2".to_vec());
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("no public frame after the gate shut");
    }
}
