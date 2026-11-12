//! Runs the real `bare-mcp`. Needs it on PATH. Run with `cargo test -- --ignored bare_live`.
#![allow(dead_code)]

#[path = "../src/bare.rs"]
mod bare;

use serde_json::json;

#[tokio::test]
#[ignore = "needs a bare-mcp with the agent tools on PATH"]
async fn bare_live() {
    let dir = std::env::temp_dir().join(format!("deck-bare-live-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    // bare loads only http(s) pages in a live session (bare#381).
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/", listener.local_addr().unwrap());
    let app = axum::Router::new().route(
        "/",
        axum::routing::get(|| async {
            axum::response::Html("<html><body><h1>hello</h1></body></html>")
        }),
    );
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let b = bare::Bare::new("bare-mcp".into(), vec![]);
    let open = b.call("open", json!({"url": url})).await.unwrap();
    assert_eq!(open["ok"], true, "{open}");
    let names: Vec<String> = b
        .tools()
        .await
        .unwrap()
        .iter()
        .filter_map(|t| t["name"].as_str().map(str::to_owned))
        .collect();
    assert!(names.iter().any(|n| n == "snapshot"), "{names:?}");
    let snap = b.call("snapshot", json!({})).await.unwrap();
    assert!(snap.as_str().unwrap().contains("hello"), "{snap}");
    let shot = dir.join("home.png");
    let said = b.call("screenshot", json!({"path": shot})).await.unwrap();
    assert!(
        said.as_str().unwrap().starts_with(shot.to_str().unwrap()),
        "{said}"
    );
    let bytes = std::fs::read(&shot).unwrap();
    assert!(bytes.starts_with(&[0x89, b'P', b'N', b'G']));
    b.call("close", json!({})).await.unwrap();
    std::fs::remove_dir_all(&dir).ok();
}
