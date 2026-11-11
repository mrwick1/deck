use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Extension, Json, Router, middleware};
use serde::Deserialize;
use serde_json::json;

use crate::auth::{User, require_user};
use crate::config::Config;
use crate::store::{Chat, Message, Store};

#[derive(Debug, thiserror::Error)]
enum ApiError {
    #[error("{0}")]
    BadRequest(&'static str),
    #[error("not found")]
    NotFound,
    #[error("internal error")]
    Internal(#[from] anyhow::Error),
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = match &self {
            Self::BadRequest(_) => StatusCode::BAD_REQUEST,
            Self::NotFound => StatusCode::NOT_FOUND,
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

/// Build the API router. Every route sits behind the auth middleware.
pub fn router(config: Arc<Config>, store: Store) -> Router {
    Router::new()
        .route("/api/me", get(me))
        .route("/api/chats", get(list_chats).post(create_chat))
        .route("/api/chats/{id}/messages", get(list_messages))
        .layer(middleware::from_fn_with_state(config, require_user))
        .with_state(store)
}

async fn me(Extension(User(user)): Extension<User>) -> Json<serde_json::Value> {
    Json(json!({ "user": user }))
}

async fn list_chats(State(store): State<Store>) -> Result<Json<Vec<Chat>>, ApiError> {
    Ok(Json(store.list_chats().await?))
}

async fn create_chat(
    State(store): State<Store>,
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
    State(store): State<Store>,
    Path(id): Path<i64>,
) -> Result<Json<Vec<Message>>, ApiError> {
    store
        .list_messages(id)
        .await?
        .map(Json)
        .ok_or(ApiError::NotFound)
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

    fn app() -> Router {
        let config = Arc::new(Config::parse(include_str!("../deck.example.toml")).unwrap());
        router(config, Store::open_memory().unwrap())
            .layer(MockConnectInfo(SocketAddr::from(([127, 0, 0, 1], 5000))))
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
}
