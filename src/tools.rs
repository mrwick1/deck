//! Tools the model can call: browser steps through bare, and `send_file`.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, anyhow};
use futures_util::future::BoxFuture;
use serde_json::{Value, json};

use crate::bare::Bare;
use crate::store::Store;

const MAX_FILE: u64 = 25 * 1024 * 1024;
const MAX_REPORT: usize = 8000;
const KEEP_ENTRIES: usize = 20;

/// Runs bare-mcp tools. A trait so tests can replace the browser.
pub trait BrowserHost: Send + Sync {
    fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>>;
    /// Capture folders seen in reports so far.
    fn out_dirs(&self) -> Vec<PathBuf>;
}

impl BrowserHost for Bare {
    fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
        Box::pin(Bare::call(self, name, args))
    }

    fn out_dirs(&self) -> Vec<PathBuf> {
        Bare::out_dirs(self)
    }
}

/// A file that `send_file` added to the chat.
#[derive(Debug, Clone, PartialEq)]
pub struct SentFile {
    pub id: i64,
    pub name: String,
    pub mime: String,
}

pub struct Outcome {
    pub ok: bool,
    /// Text returned to the model.
    pub content: String,
    pub file: Option<SentFile>,
}

pub struct Tools {
    host: Arc<dyn BrowserHost>,
    store: Store,
    workspace: PathBuf,
    files_dir: PathBuf,
}

fn tool(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    json!({"type":"function","function":{
        "name": name,
        "description": description,
        "parameters": {"type":"object","properties":properties,"required":required},
    }})
}

pub fn schemas() -> Vec<Value> {
    let s = json!({"type":"string"});
    vec![
        tool(
            "browser_open",
            "Open a URL in a fresh browser session. This ends the previous session.",
            json!({"url": s}),
            &["url"],
        ),
        tool(
            "browser_step",
            "Run one or more step lines in the open page. One step per line.",
            json!({"steps": s}),
            &["steps"],
        ),
        tool(
            "browser_close",
            "Close the browser session.",
            json!({}),
            &[],
        ),
        tool(
            "browser_ensure_session",
            "Get a logged-in page: restore a saved session or log in again.",
            json!({"name": s, "url": s, "login": s, "check": s}),
            &["name", "url", "login", "check"],
        ),
        tool(
            "send_file",
            "Send a file into the chat. Use it for screenshots and any file the user asked for.",
            json!({"path": s, "caption": s}),
            &["path"],
        ),
    ]
}

fn mime_for(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("pdf") => "application/pdf",
        Some("txt") => "text/plain",
        Some("csv") => "text/csv",
        Some("json") => "application/json",
        _ => "application/octet-stream",
    }
}

/// Keep the last entries of `network` and `console`, and cap the length.
pub fn trim_report(mut report: Value) -> String {
    for key in ["network", "console"] {
        if let Some(a) = report[key].as_array_mut()
            && a.len() > KEEP_ENTRIES
        {
            a.drain(..a.len() - KEEP_ENTRIES);
        }
    }
    let mut text = report.to_string();
    if text.chars().count() > MAX_REPORT
        && let Some(o) = report.as_object_mut()
    {
        o.remove("network");
        o.remove("console");
        text = report.to_string();
    }
    if text.chars().count() > MAX_REPORT
        && let Some(steps) = report["steps"].as_array_mut()
    {
        for st in steps {
            if let Some(o) = st.as_object_mut() {
                o.retain(|k, _| matches!(k.as_str(), "line" | "step" | "ok" | "note" | "error"));
            }
        }
        text = report.to_string();
    }
    if text.chars().count() > MAX_REPORT {
        text = text.chars().take(MAX_REPORT).collect();
        text.push_str("…[truncated]");
    }
    text
}

impl Tools {
    pub fn new(
        host: Arc<dyn BrowserHost>,
        store: Store,
        workspace: PathBuf,
        data_dir: &Path,
    ) -> Self {
        Self {
            host,
            store,
            workspace,
            files_dir: data_dir.join("files"),
        }
    }

    pub async fn execute(&self, chat_id: i64, name: &str, args: &Value) -> Outcome {
        let r = match name {
            "send_file" => self.send_file(chat_id, args).await,
            _ => self
                .browser(name, args)
                .await
                .map(|content| (content, None)),
        };
        match r {
            Ok((content, file)) => Outcome {
                ok: true,
                content,
                file,
            },
            Err(e) => Outcome {
                ok: false,
                content: json!({"ok": false, "error": format!("{e:#}")}).to_string(),
                file: None,
            },
        }
    }

    async fn browser(&self, name: &str, args: &Value) -> anyhow::Result<String> {
        let pick = |keys: &[&str]| -> anyhow::Result<Value> {
            let mut o = serde_json::Map::new();
            for k in keys {
                let v = args[k]
                    .as_str()
                    .ok_or_else(|| anyhow!("argument {k} must be a string"))?;
                o.insert((*k).into(), json!(v));
            }
            Ok(Value::Object(o))
        };
        let (bare_name, bare_args) = match name {
            "browser_open" => ("open", pick(&["url"])?),
            "browser_step" => ("step", pick(&["steps"])?),
            "browser_close" => ("close", json!({})),
            "browser_ensure_session" => {
                ("ensure_session", pick(&["name", "url", "login", "check"])?)
            }
            _ => return Err(anyhow!("unknown tool {name}")),
        };
        Ok(trim_report(self.host.call(bare_name, bare_args).await?))
    }

    fn allowed(&self, path: &Path) -> bool {
        let roots = std::iter::once(self.workspace.clone()).chain(self.host.out_dirs());
        roots
            .filter_map(|r| std::fs::canonicalize(r).ok())
            .any(|r| path.starts_with(r))
    }

    async fn send_file(
        &self,
        chat_id: i64,
        args: &Value,
    ) -> anyhow::Result<(String, Option<SentFile>)> {
        let path = args["path"]
            .as_str()
            .ok_or_else(|| anyhow!("argument path must be a string"))?;
        let caption = args["caption"].as_str().unwrap_or("");
        let real = std::fs::canonicalize(path).map_err(|_| anyhow!("path not allowed"))?;
        if !self.allowed(&real) {
            return Err(anyhow!("path not allowed"));
        }
        let size = std::fs::metadata(&real)?.len();
        if !real.is_file() {
            return Err(anyhow!("path is not a file"));
        }
        if size > MAX_FILE {
            return Err(anyhow!("file is larger than 25 MB"));
        }
        let name = real
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("file")
            .to_owned();
        let mime = mime_for(&real);
        let ext: String = real
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("bin")
            .chars()
            .filter(char::is_ascii_alphanumeric)
            .take(8)
            .collect();
        tokio::fs::create_dir_all(&self.files_dir).await?;
        let dest = self
            .files_dir
            .join(format!("{}.{ext}", uuid::Uuid::new_v4()));
        tokio::fs::copy(&real, &dest)
            .await
            .context("cannot copy the file")?;
        let row = self
            .store
            .add_file(
                chat_id,
                dest.to_string_lossy().into_owned(),
                name.clone(),
                mime.into(),
                size as i64,
            )
            .await?;
        let msg = json!({"type":"file","file_id":row.id,"name":name,"mime":mime,"caption":caption});
        self.store
            .add_message(chat_id, "assistant".into(), msg.to_string())
            .await?;
        let sent = SentFile {
            id: row.id,
            name: name.clone(),
            mime: mime.into(),
        };
        Ok((
            json!({"ok":true,"file_id":row.id,"name":name}).to_string(),
            Some(sent),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{FakeHost, temp_dir};

    async fn setup() -> (Tools, Store, i64, PathBuf) {
        let dir = temp_dir();
        let ws = dir.join("workspace");
        std::fs::create_dir_all(&ws).unwrap();
        let store = Store::open_memory().unwrap();
        let chat = store.create_chat("c".into()).await.unwrap();
        let tools = Tools::new(
            Arc::new(FakeHost::default()),
            store.clone(),
            ws.clone(),
            &dir,
        );
        (tools, store, chat.id, ws)
    }

    #[tokio::test]
    async fn sends_a_workspace_file() {
        let (tools, store, chat, ws) = setup().await;
        let f = ws.join("a.txt");
        std::fs::write(&f, "hello").unwrap();
        let out = tools
            .execute(chat, "send_file", &json!({"path": f, "caption": "cap"}))
            .await;
        assert!(out.ok, "{}", out.content);
        let sent = out.file.unwrap();
        assert_eq!(
            (sent.name.as_str(), sent.mime.as_str()),
            ("a.txt", "text/plain")
        );
        let row = store.get_file(sent.id).await.unwrap().unwrap();
        assert_eq!(std::fs::read(&row.path).unwrap(), b"hello");
        let msgs = store.list_messages(chat).await.unwrap().unwrap();
        let m: Value = serde_json::from_str(&msgs[0].content).unwrap();
        assert_eq!(
            (msgs[0].role.as_str(), m["type"].as_str()),
            ("assistant", Some("file"))
        );
        assert_eq!(m["caption"], "cap");
    }

    #[tokio::test]
    async fn rejects_paths_outside() {
        let (tools, _, chat, ws) = setup().await;
        let outside = concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml");
        let traversal = format!("{}/../../../../../../../../{}", ws.display(), outside);
        let sneaky = ws.join("..").join("x");
        for p in [outside.to_owned(), traversal, sneaky.display().to_string()] {
            let out = tools.execute(chat, "send_file", &json!({"path": p})).await;
            assert!(!out.ok);
            assert!(
                out.content.contains("path not allowed"),
                "{p}: {}",
                out.content
            );
            assert!(out.file.is_none());
        }
    }

    #[tokio::test]
    async fn sends_a_file_from_a_bare_out_folder() {
        let (_, store, chat, ws) = setup().await;
        let out_dir = temp_dir();
        std::fs::write(out_dir.join("home.png"), b"\x89PNG").unwrap();
        let host = FakeHost::default();
        host.outs.lock().unwrap().push(out_dir.clone());
        let tools = Tools::new(Arc::new(host), store, ws.clone(), &ws);
        let out = tools
            .execute(
                chat,
                "send_file",
                &json!({"path": out_dir.join("home.png")}),
            )
            .await;
        assert!(out.ok, "{}", out.content);
        assert_eq!(out.file.unwrap().mime, "image/png");
    }

    #[tokio::test]
    async fn rejects_a_large_file() {
        let (tools, _, chat, ws) = setup().await;
        let f = ws.join("big.bin");
        std::fs::File::create(&f)
            .unwrap()
            .set_len(MAX_FILE + 1)
            .unwrap();
        let out = tools.execute(chat, "send_file", &json!({"path": f})).await;
        assert!(!out.ok && out.content.contains("25 MB"), "{}", out.content);
    }

    #[test]
    fn trims_network_and_console() {
        let net: Vec<_> = (0..50).map(|i| json!({"n": i})).collect();
        let t = trim_report(json!({"ok":true,"network":net,"console":net.clone()}));
        let v: Value = serde_json::from_str(&t).unwrap();
        assert_eq!(v["network"].as_array().unwrap().len(), 20);
        assert_eq!(v["network"][0]["n"], 30);
        assert_eq!(v["console"].as_array().unwrap().len(), 20);
    }

    #[test]
    fn caps_the_length_and_keeps_notes() {
        let big = "x".repeat(1000);
        let net: Vec<_> = (0..20).map(|_| json!({"u": big})).collect();
        let steps =
            json!([{"line":1,"step":"shot a","ok":true,"note":"/p/a.png","junk":"y".repeat(100)}]);
        let t = trim_report(json!({"ok":true,"failure":null,"steps":steps,"network":net}));
        assert!(t.chars().count() <= MAX_REPORT);
        let v: Value = serde_json::from_str(&t).unwrap();
        assert_eq!(v["steps"][0]["note"], "/p/a.png");
        let huge = trim_report(json!({"steps":[{"step":"x".repeat(20000)}]}));
        assert!(huge.chars().count() <= MAX_REPORT + 20);
    }

    #[tokio::test]
    async fn browser_tools_map_to_bare_calls() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        host.replies.lock().unwrap().push_back(json!({"ok":true}));
        let tools = Tools::new(host.clone(), store, ws.clone(), &ws);
        let out = tools
            .execute(chat, "browser_open", &json!({"url":"https://example.test"}))
            .await;
        assert!(out.ok);
        assert_eq!(
            host.calls.lock().unwrap()[0],
            ("open".to_owned(), json!({"url":"https://example.test"}))
        );
        let bad = tools.execute(chat, "browser_step", &json!({})).await;
        assert!(!bad.ok);
    }
}
