//! The M1 end-to-end test. A user sends a message in the web app, the agent takes a screenshot with `bare`, and the picture shows in
//! the chat. It runs the `deck` binary, the real `bare-mcp` for the agent, and a second `bare-mcp` that drives the web app. A scripted
//! model replaces the real one, so the test does not depend on a model. Needs `bare-mcp` on PATH and a built web app (`web/dist`).
//! Run with `cargo test -- --ignored screenshot`.

use std::io::{BufRead, BufReader, Write};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use axum::Router;
use axum::extract::State;
use axum::http::header;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const PAGE: &str = "<!doctype html><html><body style=\"margin:0;background:#2a6\"><h1>deck test page</h1></body></html>";

/// The picture that the agent's `bare-mcp` made, to remove its capture folder at the end.
static SHOT: Mutex<Option<String>> = Mutex::new(None);

/// One streamed model reply: a chunk and `[DONE]`.
fn reply(delta: Value, finish: &str) -> Response {
    let chunk = json!({"choices": [{"delta": delta, "finish_reason": finish}]});
    (
        [(header::CONTENT_TYPE, "text/event-stream")],
        format!("data: {chunk}\n\ndata: [DONE]\n\n"),
    )
        .into_response()
}

fn call(name: &str, args: Value) -> Response {
    let delta = json!({"tool_calls": [{"index": 0, "id": format!("call-{name}"), "function": {"name": name, "arguments": args.to_string()}}]});
    reply(delta, "tool_calls")
}

/// The scripted model. It picks its next step from the tool results that the request holds: open the page, take a shot, send the
/// file that the shot made, then answer.
async fn model(State(base): State<String>, body: String) -> Response {
    let body: Value = serde_json::from_str(&body).unwrap();
    let tools: Vec<&Value> = body["messages"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|m| m["role"] == "tool")
        .collect();
    match tools.len() {
        0 => call("browser_open", json!({"url": format!("{base}/page")})),
        1 => call("browser_screenshot", json!({})),
        2 => {
            // The reply is "PATH (WxH)".
            let said = tools[1]["content"].as_str().unwrap();
            let path = said
                .rsplit_once(" (")
                .expect("the screenshot reply gives its path")
                .0;
            *SHOT.lock().unwrap() = Some(path.to_owned());
            call(
                "send_file",
                json!({"path": path, "caption": "The test page"}),
            )
        }
        _ => reply(json!({"content": "Here is the screenshot."}), "stop"),
    }
}

/// The scripted model and the page that the agent opens, on one server. Gives its base URL.
async fn model_server() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/chat/completions", post(model))
        .route(
            "/page",
            get(|| async { ([(header::CONTENT_TYPE, "text/html")], PAGE) }),
        )
        .with_state(base.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    base
}

/// A proxy that adds `Remote-User`, as Authelia behind Caddy does. One request on each connection.
async fn auth_proxy(upstream: SocketAddr) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move {
        while let Ok((mut client, _)) = listener.accept().await {
            tokio::spawn(async move {
                let mut head = Vec::new();
                let mut byte = [0u8; 1];
                while !head.ends_with(b"\r\n\r\n") {
                    if client.read(&mut byte).await.unwrap_or(0) == 0 {
                        return;
                    }
                    head.push(byte[0]);
                }
                let text = String::from_utf8_lossy(&head);
                let mut lines: Vec<&str> = text.split("\r\n").filter(|l| !l.is_empty()).collect();
                lines.retain(|l| {
                    !l.to_ascii_lowercase().starts_with("connection:")
                        && !l.to_ascii_lowercase().starts_with("remote-user:")
                });
                let head = format!(
                    "{}\r\nRemote-User: e2e\r\nConnection: close\r\n\r\n",
                    lines.join("\r\n")
                );
                let Ok(mut server) = TcpStream::connect(upstream).await else {
                    return;
                };
                if server.write_all(head.as_bytes()).await.is_ok() {
                    let _ = tokio::io::copy_bidirectional(&mut client, &mut server).await;
                }
            });
        }
    });
    base
}

/// A child process that is killed when the test ends.
struct Kill(Child);

impl Drop for Kill {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// Run a step file in a new `bare-mcp`. Gives the report.
fn run_steps(steps: &str, timeout_ms: u64) -> Value {
    let child = Command::new("bare-mcp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("bare-mcp on PATH");
    let mut child = Kill(child);
    let mut stdin = child.0.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.0.stdout.take().unwrap());
    let mut line = String::new();
    let init = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "deck-e2e", "version": "0"}}});
    writeln!(stdin, "{init}").unwrap();
    stdout.read_line(&mut line).unwrap();
    writeln!(
        stdin,
        "{}",
        json!({"jsonrpc": "2.0", "method": "notifications/initialized"})
    )
    .unwrap();
    let call = json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "run_steps", "arguments": {"steps": steps, "timeout_ms": timeout_ms}}});
    writeln!(stdin, "{call}").unwrap();
    line.clear();
    stdout.read_line(&mut line).unwrap();
    let reply: Value = serde_json::from_str(&line).unwrap();
    serde_json::from_str(reply["result"]["content"][0]["text"].as_str().unwrap()).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs bare-mcp on PATH and web/dist"]
async fn screenshot_reaches_the_chat_in_the_web_app() {
    let dir = std::env::temp_dir().join(format!("deck-e2e-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let model = model_server().await;
    let addr: SocketAddr = {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap()
    };
    let config = format!(
        "listen = \"{addr}\"\ndata_dir = \"{data}\"\ntrusted_proxies = [\"127.0.0.1\"]\nallowed_users = []\n\
         [model]\nbase_url = \"{model}\"\napi_key_env = \"DECK_E2E_KEY\"\nagent_model = \"scripted\"\nsmall_model = \"scripted\"\n\
         [bare]\ncommand = \"bare-mcp\"\nargs = []\n[agent]\nmax_steps = 10\n",
        data = dir.join("data").display()
    );
    std::fs::write(dir.join("deck.toml"), config).unwrap();
    let _deck = Kill(
        Command::new(env!("CARGO_BIN_EXE_deck"))
            .env("DECK_CONFIG", dir.join("deck.toml"))
            .env("DECK_E2E_KEY", "scripted")
            .spawn()
            .unwrap(),
    );
    for _ in 0..100 {
        if TcpStream::connect(addr).await.is_ok() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let deck = auth_proxy(addr).await;

    let steps = format!(
        "open {deck}/\nexpect label=\"Message\" visible\ntype label=\"Message\" \"Send me a screenshot of the test page.\"\n\
         click role=button name=\"Send\"\ntimeout 60000\nexpect css=\"img[src*=files]\" visible\n\
         expect text=\"Here is the screenshot.\" visible\nshot chat"
    );
    let report = tokio::task::spawn_blocking(move || run_steps(&steps, 90_000))
        .await
        .unwrap();
    assert_eq!(report["ok"], true, "{report}");

    // The chat holds the file message, and the file is the PNG of the page.
    let http = reqwest::Client::new();
    let get = |path: String| http.get(format!("{deck}{path}")).send();
    let chats: Value = get("/api/chats".into())
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let chat = chats[0]["id"].as_i64().unwrap();
    let messages: Value = get(format!("/api/chats/{chat}/messages"))
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let file = messages
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|m| serde_json::from_str::<Value>(m["content"].as_str()?).ok())
        .find(|c| c["type"] == "file")
        .expect("a file message");
    assert_eq!(
        (file["mime"].as_str(), file["caption"].as_str()),
        (Some("image/png"), Some("The test page"))
    );
    let png = get(format!("/api/files/{}", file["file_id"]))
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"), "{} bytes", png.len());

    let out: PathBuf = report["out"].as_str().unwrap().into();
    std::fs::remove_dir_all(out).ok();
    if let Some(shot) = SHOT.lock().unwrap().take() {
        std::fs::remove_dir_all(PathBuf::from(shot).parent().unwrap()).ok();
    }
    std::fs::remove_dir_all(&dir).ok();
}
