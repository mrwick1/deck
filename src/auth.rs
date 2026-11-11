use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::{ConnectInfo, Request, State};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::Response;

use crate::config::Config;

/// The user that the trusted proxy authenticated.
#[derive(Debug, Clone)]
pub struct User(pub String);

/// Trust `Remote-User` only when the TCP peer is a trusted proxy.
pub async fn require_user(
    State(config): State<Arc<Config>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    mut req: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    if !config.trusted_proxies.contains(&peer.ip()) {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let user = req
        .headers()
        .get("Remote-User")
        .and_then(|v| v.to_str().ok())
        .filter(|v| !v.is_empty())
        .ok_or(StatusCode::UNAUTHORIZED)?
        .to_owned();
    if !config.allowed_users.is_empty() && !config.allowed_users.contains(&user) {
        return Err(StatusCode::FORBIDDEN);
    }
    req.extensions_mut().insert(User(user));
    Ok(next.run(req).await)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::extract::{Extension, connect_info::MockConnectInfo};
    use axum::routing::get;
    use axum::{Router, middleware};
    use tower::ServiceExt;

    fn app(peer: &str, allowed: &[&str]) -> Router {
        let mut config = Config::parse(include_str!("../deck.example.toml")).unwrap();
        config.allowed_users = allowed.iter().map(|s| s.to_string()).collect();
        let config = Arc::new(config);
        Router::new()
            .route(
                "/api/me",
                get(|Extension(u): Extension<User>| async move { u.0 }),
            )
            .layer(middleware::from_fn_with_state(config, require_user))
            .layer(MockConnectInfo(SocketAddr::new(
                peer.parse().unwrap(),
                5000,
            )))
    }

    async fn status(app: Router, user: Option<&str>) -> StatusCode {
        let mut req = Request::builder().uri("/api/me");
        if let Some(u) = user {
            req = req.header("Remote-User", u);
        }
        app.oneshot(req.body(Body::empty()).unwrap())
            .await
            .unwrap()
            .status()
    }

    #[tokio::test]
    async fn untrusted_peer_is_401() {
        assert_eq!(
            status(app("10.9.9.9", &[]), Some("a")).await,
            StatusCode::UNAUTHORIZED
        );
    }

    #[tokio::test]
    async fn missing_header_is_401() {
        assert_eq!(
            status(app("127.0.0.1", &[]), None).await,
            StatusCode::UNAUTHORIZED
        );
        assert_eq!(
            status(app("127.0.0.1", &[]), Some("")).await,
            StatusCode::UNAUTHORIZED
        );
    }

    #[tokio::test]
    async fn trusted_with_header_is_200() {
        assert_eq!(
            status(app("127.0.0.1", &[]), Some("a")).await,
            StatusCode::OK
        );
    }

    #[tokio::test]
    async fn other_user_is_403() {
        assert_eq!(
            status(app("127.0.0.1", &["a"]), Some("b")).await,
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            status(app("127.0.0.1", &["a"]), Some("a")).await,
            StatusCode::OK
        );
    }
}
