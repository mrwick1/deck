use std::sync::Arc;

use std::convert::Infallible;
use std::time::Duration;

use axum::extract::{Path, Query, State};
use axum::http::{StatusCode, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router, middleware};
use futures_util::Stream;
use serde::Deserialize;
use serde_json::json;

use crate::agent::{Agent, StartError};
use crate::auth::{User, require_user};
use crate::config::Config;
use crate::store::{Chat, Message, Store};

#[derive(Clone)]
pub struct AppState {
    pub store: Store,
    pub agent: Arc<Agent>,
}

#[derive(Debug, thiserror::Error)]
enum ApiError {
    #[error("{0}")]
    BadRequest(&'static str),
    #[error("not found")]
    NotFound,
    #[error("a run is already active in this chat")]
    Conflict,
    #[error("internal error")]
    Internal(#[from] anyhow::Error),
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = match &self {
            Self::BadRequest(_) => StatusCode::BAD_REQUEST,
            Self::NotFound => StatusCode::NOT_FOUND,
            Self::Conflict => StatusCode::CONFLICT,
            Self::Internal(e) => {
                tracing::error!("{e:#}");
                StatusCode::INTERNAL_SERVER_ERROR
            }
        };
        (status, Json(json!({ "error": self.to_string() }))).into_response()
    }
}

#[derive(Deserialize)]
struct NewChat {
    title: Option<String>,
}

/// Build the router: the API and the web app. Every route sits behind the auth middleware.
pub fn router(config: Arc<Config>, store: Store, agent: Arc<Agent>) -> Router {
    Router::new()
        .route("/api/me", get(me))
        .route("/api/chats", get(list_chats).post(create_chat))
        .route(
            "/api/chats/{id}/messages",
            get(list_messages).post(post_message),
        )
        .route("/api/chats/{id}/events", get(events))
        .route("/api/runs/{id}/cancel", post(cancel_run))
        .route("/api/files/{id}", get(get_file))
        .fallback(crate::web::serve)
        .layer(middleware::from_fn_with_state(config, require_user))
        .with_state(AppState { store, agent })
}

async fn me(Extension(User(user)): Extension<User>) -> Json<serde_json::Value> {
    Json(json!({ "user": user }))
}

async fn list_chats(
    State(AppState { store, .. }): State<AppState>,
) -> Result<Json<Vec<Chat>>, ApiError> {
    Ok(Json(store.list_chats().await?))
}

async fn create_chat(
    State(AppState { store, .. }): State<AppState>,
    Json(body): Json<NewChat>,
) -> Result<(StatusCode, Json<Chat>), ApiError> {
    let title = body
        .title
        .ok_or(ApiError::BadRequest("title is required"))?;
    if !(1..=200).contains(&title.chars().count()) {
        return Err(ApiError::BadRequest("title must be 1 to 200 characters"));
    }
    Ok((StatusCode::CREATED, Json(store.create_chat(title).await?)))
}

async fn list_messages(
    State(AppState { store, .. }): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<Vec<Message>>, ApiError> {
    store
        .list_messages(id)
        .await?
        .map(Json)
        .ok_or(ApiError::NotFound)
}

#[derive(Deserialize)]
struct NewMessage {
    text: Option<String>,
}

async fn post_message(
    State(st): State<AppState>,
    Path(id): Path<i64>,
    Json(body): Json<NewMessage>,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    let text = body.text.ok_or(ApiError::BadRequest("text is required"))?;
    if !(1..=20_000).contains(&text.chars().count()) {
        return Err(ApiError::BadRequest("text must be 1 to 20000 characters"));
    }
    match st.agent.start(id, text).await? {
        Ok((run_id, _)) => Ok((StatusCode::ACCEPTED, Json(json!({ "run_id": run_id })))),
        Err(StartError::NoChat) => Err(ApiError::NotFound),
        Err(StartError::Busy) => Err(ApiError::Conflict),
    }
}

#[derive(Deserialize)]
struct EventsQuery {
    #[serde(default)]
    after: i64,
}

/// Replay stored events after `after`, then stream live events.
async fn events(
    State(st): State<AppState>,
    Path(id): Path<i64>,
    Query(q): Query<EventsQuery>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ApiError> {
    if !st.store.chat_exists(id).await? {
        return Err(ApiError::NotFound);
    }
    // Subscribe before the replay so no event falls in the gap.
    let mut rx = st.agent.subscribe(id);
    let stored = st.store.list_events(id, q.after).await?;
    let stream = async_stream::stream! {
        let mut last = q.after;
        for e in stored {
            last = e.id;
            yield Ok(Event::default().id(e.id.to_string()).event(e.kind).data(e.payload));
        }
        // A lagged or closed channel ends the stream. The client reconnects
        // with `after` and replays what it missed.
        while let Ok(e) = rx.recv().await {
            let mut out = Event::default().event(e.kind).data(e.data.to_string());
            if let Some(i) = e.id {
                if i <= last {
                    continue;
                }
                last = i;
                out = out.id(i.to_string());
            }
            yield Ok(out);
        }
    };
    Ok(Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))))
}

async fn cancel_run(State(st): State<AppState>, Path(id): Path<i64>) -> StatusCode {
    st.agent.cancel(id);
    StatusCode::NO_CONTENT
}

async fn get_file(
    State(AppState { store, .. }): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Response, ApiError> {
    let row = store.get_file(id).await?.ok_or(ApiError::NotFound)?;
    let bytes = tokio::fs::read(&row.path)
        .await
        .map_err(|_| ApiError::NotFound)?;
    let name: String = row
        .name
        .chars()
        .map(|c| {
            if c.is_ascii_graphic() && !matches!(c, '"' | '\\') || c == ' ' {
                c
            } else {
                '_'
            }
        })
        .collect();
    Ok((
        [
            (header::CONTENT_TYPE, row.mime),
            (
                header::CONTENT_DISPOSITION,
                format!("inline; filename=\"{name}\""),
            ),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff".into()),
        ],
        bytes,
    )
        .into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::extract::connect_info::MockConnectInfo;
    use axum::http::Request;
    use http_body_util::BodyExt;
    use std::net::SocketAddr;
    use tower::ServiceExt;

    use crate::model::ModelClient;
    use crate::testutil::{FakeHost, fake_model, sse, temp_dir};

    fn app() -> Router {
        test_app(
            Config::parse(include_str!("../deck.example.toml")).unwrap(),
            Store::open_memory().unwrap(),
        )
        .0
    }

    fn test_app(config: Config, store: Store) -> (Router, Arc<Agent>) {
        let config = Arc::new(config);
        let model = ModelClient::new("http://127.0.0.1:1", Some("k".into()), "K");
        let agent = Arc::new(Agent::new(
            config.clone(),
            store.clone(),
            model,
            Arc::new(FakeHost::default()),
        ));
        let app = router(config, store, agent.clone())
            .layer(MockConnectInfo(SocketAddr::from(([127, 0, 0, 1], 5000))));
        (app, agent)
    }

    async fn call(app: &Router, method: &str, uri: &str, body: &str) -> (StatusCode, String) {
        let req = Request::builder()
            .method(method)
            .uri(uri)
            .header("Remote-User", "test")
            .header("Content-Type", "application/json")
            .body(Body::from(body.to_owned()))
            .unwrap();
        let res = app.clone().oneshot(req).await.unwrap();
        let status = res.status();
        let bytes = res.into_body().collect().await.unwrap().to_bytes();
        (status, String::from_utf8(bytes.to_vec()).unwrap())
    }

    #[tokio::test]
    async fn serves_the_web_app_behind_auth() {
        let app = app();
        for uri in ["/", "/chats/1/some/spa/path"] {
            let req = |auth: bool| {
                let mut r = Request::builder().uri(uri);
                if auth {
                    r = r.header("Remote-User", "test");
                }
                r.body(Body::empty()).unwrap()
            };
            let res = app.clone().oneshot(req(true)).await.unwrap();
            assert_eq!(res.status(), StatusCode::OK, "{uri}");
            assert_eq!(res.headers()["content-type"], "text/html", "{uri}");
            assert_eq!(res.headers()["cache-control"], "no-cache", "{uri}");
            let res = app.clone().oneshot(req(false)).await.unwrap();
            assert_eq!(res.status(), StatusCode::UNAUTHORIZED, "{uri}");
        }
    }

    #[tokio::test]
    async fn unknown_api_path_is_a_json_404() {
        let (s, b) = call(&app(), "GET", "/api/nope", "").await;
        assert_eq!(
            (s, b.as_str()),
            (StatusCode::NOT_FOUND, r#"{"error":"not found"}"#)
        );
    }

    #[tokio::test]
    async fn me_returns_user() {
        let (s, b) = call(&app(), "GET", "/api/me", "").await;
        assert_eq!((s, b.as_str()), (StatusCode::OK, r#"{"user":"test"}"#));
    }

    #[tokio::test]
    async fn create_then_list() {
        let app = app();
        let (s, _) = call(&app, "POST", "/api/chats", r#"{"title":"hello"}"#).await;
        assert_eq!(s, StatusCode::CREATED);
        let (s, b) = call(&app, "GET", "/api/chats", "").await;
        assert_eq!(s, StatusCode::OK);
        assert!(b.contains(r#""title":"hello""#));
    }

    #[tokio::test]
    async fn bad_titles_are_400() {
        let app = app();
        let long = format!(r#"{{"title":"{}"}}"#, "x".repeat(201));
        for body in [r#"{"title":""}"#, "{}", long.as_str()] {
            let (s, b) = call(&app, "POST", "/api/chats", body).await;
            assert_eq!(s, StatusCode::BAD_REQUEST, "{body}");
            assert!(b.contains("error"));
        }
    }

    #[tokio::test]
    async fn messages_of_missing_chat_is_404() {
        let (s, b) = call(&app(), "GET", "/api/chats/999/messages", "").await;
        assert_eq!(
            (s, b.as_str()),
            (StatusCode::NOT_FOUND, r#"{"error":"not found"}"#)
        );
    }

    #[tokio::test]
    async fn messages_of_new_chat_is_empty() {
        let app = app();
        call(&app, "POST", "/api/chats", r#"{"title":"c"}"#).await;
        let (s, b) = call(&app, "GET", "/api/chats/1/messages", "").await;
        assert_eq!((s, b.as_str()), (StatusCode::OK, "[]"));
    }

    #[tokio::test]
    async fn post_message_statuses() {
        let app = app();
        let (s, _) = call(&app, "POST", "/api/chats/999/messages", r#"{"text":"hi"}"#).await;
        assert_eq!(s, StatusCode::NOT_FOUND);
        call(&app, "POST", "/api/chats", r#"{"title":"c"}"#).await;
        let long = format!(r#"{{"text":"{}"}}"#, "x".repeat(20_001));
        for body in [r#"{"text":""}"#, "{}", long.as_str()] {
            let (s, _) = call(&app, "POST", "/api/chats/1/messages", body).await;
            assert_eq!(s, StatusCode::BAD_REQUEST, "{body}");
        }
        // The model URL is unreachable, so the run fails fast. The first post is accepted.
        let (s, b) = call(&app, "POST", "/api/chats/1/messages", r#"{"text":"hi"}"#).await;
        assert_eq!((s, b.as_str()), (StatusCode::ACCEPTED, r#"{"run_id":1}"#));
    }

    #[tokio::test]
    async fn second_post_during_a_run_is_409() {
        let store = Store::open_memory().unwrap();
        let (url, _) = fake_model(vec![]).await;
        let mut config = Config::parse(include_str!("../deck.example.toml")).unwrap();
        config.model.base_url = url.clone();
        let config = Arc::new(config);
        let host = FakeHost {
            hang: true,
            ..FakeHost::default()
        };
        let (url2, _) = fake_model(vec![(
            200,
            sse(&[json!({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c","function":{"name":"browser_close","arguments":"{}"}}]}}]})]),
        )])
        .await;
        let model = ModelClient::new(&url2, Some("k".into()), "K");
        let agent = Arc::new(Agent::new(
            config.clone(),
            store.clone(),
            model,
            Arc::new(host),
        ));
        let app = router(config, store.clone(), agent.clone())
            .layer(MockConnectInfo(SocketAddr::from(([127, 0, 0, 1], 5000))));
        call(&app, "POST", "/api/chats", r#"{"title":"c"}"#).await;
        let (s, _) = call(&app, "POST", "/api/chats/1/messages", r#"{"text":"a"}"#).await;
        assert_eq!(s, StatusCode::ACCEPTED);
        let (s, _) = call(&app, "POST", "/api/chats/1/messages", r#"{"text":"b"}"#).await;
        assert_eq!(s, StatusCode::CONFLICT);
        let (s, _) = call(&app, "POST", "/api/runs/1/cancel", "").await;
        assert_eq!(s, StatusCode::NO_CONTENT);
    }

    #[tokio::test]
    async fn serves_a_file() {
        let store = Store::open_memory().unwrap();
        let (app, _) = test_app(
            Config::parse(include_str!("../deck.example.toml")).unwrap(),
            store.clone(),
        );
        let chat = store.create_chat("c".into()).await.unwrap();
        let path = temp_dir().join("f.bin");
        std::fs::write(&path, b"\x89PNGdata").unwrap();
        let row = store
            .add_file(
                chat.id,
                path.display().to_string(),
                "sh\"ot.png".into(),
                "image/png".into(),
                8,
            )
            .await
            .unwrap();
        let req = |uri: String| {
            Request::builder()
                .uri(uri)
                .header("Remote-User", "test")
                .body(Body::empty())
                .unwrap()
        };
        let res = app
            .clone()
            .oneshot(req(format!("/api/files/{}", row.id)))
            .await
            .unwrap();
        assert_eq!(res.status(), StatusCode::OK);
        assert_eq!(res.headers()["content-type"], "image/png");
        assert_eq!(
            res.headers()["content-disposition"],
            r#"inline; filename="sh_ot.png""#
        );
        let bytes = res.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(&bytes[..], b"\x89PNGdata");
        let res = app.oneshot(req("/api/files/99".into())).await.unwrap();
        assert_eq!(res.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn sse_replays_a_finished_run() {
        let store = Store::open_memory().unwrap();
        let (url, _) = fake_model(vec![(
            200,
            sse(&[json!({"choices":[{"delta":{"content":"pong"},"finish_reason":"stop"}]})]),
        )])
        .await;
        let mut config = Config::parse(include_str!("../deck.example.toml")).unwrap();
        config.model.base_url = url.clone();
        let config = Arc::new(config);
        let model = ModelClient::new(&url, Some("k".into()), "K");
        let agent = Arc::new(Agent::new(
            config.clone(),
            store.clone(),
            model,
            Arc::new(FakeHost::default()),
        ));
        let app = router(config, store.clone(), agent)
            .layer(MockConnectInfo(SocketAddr::from(([127, 0, 0, 1], 5000))));
        call(&app, "POST", "/api/chats", r#"{"title":"c"}"#).await;
        call(&app, "POST", "/api/chats/1/messages", r#"{"text":"ping"}"#).await;
        while store.run_status(1).await.unwrap().as_deref() != Some("done") {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        let req = Request::builder()
            .uri("/api/chats/1/events?after=0")
            .header("Remote-User", "test")
            .body(Body::empty())
            .unwrap();
        let res = app.clone().oneshot(req).await.unwrap();
        assert_eq!(res.status(), StatusCode::OK);
        assert_eq!(res.headers()["content-type"], "text/event-stream");
        let mut body = res.into_body();
        let mut text = String::new();
        let read = async {
            while !text.contains("event: run_finished") {
                let frame = body.frame().await.unwrap().unwrap();
                text.push_str(&String::from_utf8_lossy(frame.data_ref().unwrap()));
            }
        };
        tokio::time::timeout(Duration::from_secs(5), read)
            .await
            .unwrap();
        let order: Vec<_> = text
            .lines()
            .filter_map(|l| l.strip_prefix("event: "))
            .collect();
        assert_eq!(order, ["run_started", "text", "run_finished"]);
        assert!(text.contains("id: 1\n"));
        assert!(text.contains(r#"data: {"run_id":1,"text":"pong"}"#));
        // `after` skips what the client already has.
        let req = Request::builder()
            .uri("/api/chats/1/events?after=2")
            .header("Remote-User", "test")
            .body(Body::empty())
            .unwrap();
        let mut body = app.oneshot(req).await.unwrap().into_body();
        let frame = body.frame().await.unwrap().unwrap();
        let first = String::from_utf8_lossy(frame.data_ref().unwrap()).into_owned();
        assert!(first.contains("event: run_finished"), "{first}");
    }
}
