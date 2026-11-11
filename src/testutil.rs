//! Helpers shared by tests.

use std::sync::{Arc, Mutex};

use axum::Router;
use axum::extract::State;
use axum::http::{StatusCode, header};
use axum::response::IntoResponse;
use axum::routing::post;
use serde_json::Value;

/// Build an SSE body from chunk objects, ended by `[DONE]`.
pub fn sse(chunks: &[Value]) -> String {
    let mut s: String = chunks.iter().map(|c| format!("data: {c}\n\n")).collect();
    s.push_str("data: [DONE]\n\n");
    s
}

type Script = Arc<Mutex<(Vec<(u16, String)>, Vec<Value>)>>;

/// Fake model server on 127.0.0.1. Each request pops the next scripted
/// `(status, body)`. Returns the base URL and the request bodies seen.
pub async fn fake_model(script: Vec<(u16, String)>) -> (String, Arc<Mutex<Vec<Value>>>) {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let state: Script = Arc::new(Mutex::new((script.into_iter().rev().collect(), vec![])));
    let seen2 = seen.clone();
    async fn handle(
        State((script, seen)): State<(Script, Arc<Mutex<Vec<Value>>>)>,
        body: String,
    ) -> impl IntoResponse {
        seen.lock()
            .unwrap()
            .push(serde_json::from_str(&body).unwrap_or(Value::Null));
        let next = script.lock().unwrap().0.pop();
        match next {
            Some((status, body)) => (
                StatusCode::from_u16(status).unwrap(),
                [(header::CONTENT_TYPE, "text/event-stream")],
                body,
            ),
            None => (
                StatusCode::GONE,
                [(header::CONTENT_TYPE, "text/plain")],
                "script empty".into(),
            ),
        }
    }
    let app = Router::new()
        .route("/chat/completions", post(handle))
        .with_state((state, seen2));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (url, seen)
}

use std::collections::VecDeque;
use std::path::PathBuf;

use futures_util::future::BoxFuture;

use crate::tools::BrowserHost;

/// A unique empty temp folder.
pub fn temp_dir() -> PathBuf {
    let d = std::env::temp_dir().join(format!("deck-test-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// Fake browser: replies come from a queue; `hang` makes every call wait forever.
#[derive(Default)]
pub struct FakeHost {
    pub replies: Mutex<VecDeque<Value>>,
    pub calls: Mutex<Vec<(String, Value)>>,
    pub outs: Mutex<Vec<PathBuf>>,
    pub hang: bool,
}

impl BrowserHost for FakeHost {
    fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
        Box::pin(async move {
            self.calls.lock().unwrap().push((name.to_owned(), args));
            if self.hang {
                std::future::pending::<()>().await;
            }
            Ok(self
                .replies
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or_else(|| serde_json::json!({"ok": true})))
        })
    }

    fn out_dirs(&self) -> Vec<PathBuf> {
        self.outs.lock().unwrap().clone()
    }
}
