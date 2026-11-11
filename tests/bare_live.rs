//! Runs the real `bare-mcp`. Needs it on PATH. Run with `cargo test -- --ignored bare_live`.
#![allow(dead_code)]

#[path = "../src/bare.rs"]
mod bare;

use serde_json::json;

#[tokio::test]
#[ignore = "needs bare-mcp on PATH"]
async fn bare_live() {
    let dir = std::env::temp_dir().join(format!("deck-bare-live-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let page = dir.join("p.html");
    std::fs::write(&page, "<html><body><h1>hello</h1></body></html>").unwrap();

    let b = bare::Bare::new("bare-mcp".into(), vec![]);
    let open = b
        .call("open", json!({"url": format!("file://{}", page.display())}))
        .await
        .unwrap();
    assert_eq!(open["ok"], true, "{open}");
    let step = b.call("step", json!({"steps": "shot home"})).await.unwrap();
    assert_eq!(step["ok"], true, "{step}");
    let note = step["steps"][0]["note"].as_str().expect("note path");
    let bytes = std::fs::read(note).unwrap();
    assert!(bytes.starts_with(&[0x89, b'P', b'N', b'G']), "{note}");
    assert!(
        b.out_dirs()
            .iter()
            .any(|d| note.starts_with(d.to_str().unwrap()))
    );
    b.call("close", json!({})).await.unwrap();
    std::fs::remove_dir_all(&dir).ok();
}
