//! Tools the model can call: every bare-mcp tool as `browser_<name>`, and `send_file`.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, anyhow};
use futures_util::future::BoxFuture;
use serde_json::{Value, json};

use crate::bare::Bare;
use crate::bot::hub::Hub;
use crate::store::Store;

const MAX_FILE: u64 = 25 * 1024 * 1024;
const MAX_REPORT: usize = 8000;
/// The longest text reply (a snapshot, a page of text) that goes back to the model.
const MAX_TEXT: usize = 20_000;
/// bare-mcp tools that deck does not offer: a whole journey runner.
const HIDDEN_TOOLS: &[&str] = &["run_steps"];
/// Arguments that deck sets or forbids: a window on the desktop, files that the model must not pick, inline image data.
const HIDDEN_ARGS: &[&str] = &[
    "window",
    "secrets_file",
    "file",
    "path",
    "downloads_dir",
    "inline",
];
const KEEP_ENTRIES: usize = 20;

/// Runs bare-mcp tools. A trait so tests can replace the browser.
pub trait BrowserHost: Send + Sync {
    fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>>;
    /// The tools of bare-mcp (`tools/list`).
    fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>>;
    /// Capture folders seen in reports so far.
    fn out_dirs(&self) -> Vec<PathBuf>;
}

impl BrowserHost for Bare {
    fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
        Box::pin(Bare::call(self, name, args))
    }

    fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
        Box::pin(Bare::tools(self))
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
    /// The agent asked the owner a question: the run ends and the task waits.
    pub wait: Option<String>,
}

pub struct Tools {
    host: Arc<dyn BrowserHost>,
    store: Store,
    workspace: PathBuf,
    files_dir: PathBuf,
    /// The secrets file of the owner, from the config. The model never sees its path or values.
    secrets: Option<PathBuf>,
    /// The computer of the bot. When set, shell and files run there, and no host path is used.
    guest: Option<Arc<Hub>>,
}

fn tool(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    json!({"type":"function","function":{
        "name": name,
        "description": description,
        "parameters": {"type":"object","properties":properties,"required":required},
    }})
}

/// The tool schemas for the model: each bare-mcp tool as `browser_<name>`, without the arguments that deck controls, and `send_file`.
pub fn schemas(bare: &[Value], computer: bool) -> Vec<Value> {
    let mut out: Vec<Value> = bare
        .iter()
        .filter_map(|t| {
            let name = t["name"].as_str()?;
            if HIDDEN_TOOLS.contains(&name) {
                return None;
            }
            let mut params = t["inputSchema"].clone();
            if let Some(p) = params["properties"].as_object_mut() {
                p.retain(|k, _| !HIDDEN_ARGS.contains(&k.as_str()));
            }
            if let Some(r) = params["required"].as_array_mut() {
                r.retain(|k| !k.as_str().is_some_and(|k| HIDDEN_ARGS.contains(&k)));
            }
            Some(json!({"type":"function","function":{
                "name": format!("browser_{name}"),
                "description": t["description"],
                "parameters": params,
            }}))
        })
        .collect();
    out.push(tool(
        "send_file",
        if computer {
            "Send a file into the chat. Give the path of a screenshot from a browser tool, or a file in your workspace."
        } else {
            "Send a file into the chat. Use it for screenshots and any file the user asked for."
        },
        json!({"path": {"type":"string"}, "caption": {"type":"string"}}),
        &["path"],
    ));
    if computer {
        out.push(tool(
            "shell",
            "Run a shell script on your computer as the desktop user. The folder is your workspace. Files that you write to $DECK_OUT come back as files that send_file can send. The reply has the exit code and the end of the output.",
            json!({"script": {"type":"string"}, "timeout_secs": {"type":"integer","minimum":1,"maximum":3600}}),
            &["script"],
        ));
        out.push(tool(
            "ask_owner",
            "Ask the owner a question and wait for the answer. Your task keeps the computer while it waits.",
            json!({"question": {"type":"string"}}),
            &["question"],
        ));
    }
    out
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
            secrets: None,
            guest: None,
        }
    }

    pub fn with_guest(mut self, hub: Arc<Hub>) -> Self {
        self.guest = Some(hub);
        self
    }

    pub fn has_guest(&self) -> bool {
        self.guest.is_some()
    }

    /// The names in the secrets file, for the prompt. Values are never read into the model's context.
    pub fn secret_names(&self) -> Vec<String> {
        let text = self
            .secrets
            .as_ref()
            .and_then(|f| std::fs::read_to_string(f).ok())
            .unwrap_or_default();
        text.lines()
            .filter(|l| !l.trim_start().starts_with('#'))
            .filter_map(|l| l.split_once('='))
            .map(|(k, _)| k.trim().to_string())
            .filter(|k| !k.is_empty())
            .collect()
    }

    /// Give bare the secrets file, so that `${NAME}` in typed text becomes the value.
    pub fn with_secrets(mut self, secrets: Option<PathBuf>) -> Self {
        self.secrets = secrets;
        self
    }

    pub async fn execute(&self, chat_id: i64, name: &str, args: &Value) -> Outcome {
        let r = match name {
            "send_file" => self.send_file(chat_id, args).await,
            "shell" if self.guest.is_some() => {
                self.shell(args).await.map(|content| (content, None))
            }
            "ask_owner" if self.guest.is_some() => {
                return match args["question"].as_str().filter(|q| !q.trim().is_empty()) {
                    Some(q) => Outcome {
                        ok: true,
                        content: json!({"ok": true, "waiting": "the owner was asked"}).to_string(),
                        file: None,
                        wait: Some(q.chars().take(2000).collect()),
                    },
                    None => Outcome {
                        ok: false,
                        content:
                            json!({"ok": false, "error": "argument question must be a string"})
                                .to_string(),
                        file: None,
                        wait: None,
                    },
                };
            }
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
                wait: None,
            },
            Err(e) => Outcome {
                ok: false,
                content: json!({"ok": false, "error": format!("{e:#}")}).to_string(),
                file: None,
                wait: None,
            },
        }
    }

    async fn shell(&self, args: &Value) -> anyhow::Result<String> {
        let script = args["script"]
            .as_str()
            .ok_or_else(|| anyhow!("argument script must be a string"))?;
        if script.len() > 12_000 {
            return Err(anyhow!(
                "the script is longer than 12000 bytes: write it to a file in pieces"
            ));
        }
        let hub = self.guest.as_ref().expect("checked by the caller");
        hub.shell(script, args["timeout_secs"].as_u64()).await
    }

    /// The bare-mcp tools, for the schemas of a run.
    pub async fn bare_tools(&self) -> anyhow::Result<Vec<Value>> {
        self.host.tools().await
    }

    async fn browser(&self, name: &str, args: &Value) -> anyhow::Result<String> {
        let bare_name = name
            .strip_prefix("browser_")
            .filter(|n| !HIDDEN_TOOLS.contains(n))
            .ok_or_else(|| anyhow!("unknown tool {name}"))?;
        let mut a = match args {
            Value::Object(o) => o.clone(),
            Value::Null => serde_json::Map::new(),
            _ => return Err(anyhow!("the arguments must be an object")),
        };
        a.retain(|k, _| !HIDDEN_ARGS.contains(&k.as_str()));
        // The computer sets paths, secrets and download folders on its own side.
        if self.guest.is_some() {
            return self.relay(bare_name, a).await;
        }
        // Screenshots and downloads land in the workspace, so send_file can send them.
        if bare_name == "screenshot" {
            let dir = self.workspace.join("shots");
            tokio::fs::create_dir_all(&dir).await?;
            let path = dir.join(format!("{}.png", uuid::Uuid::new_v4()));
            a.insert("path".into(), json!(path));
        }
        // A new session reads the secrets file of the config. Its tabs copy the secrets.
        if let (Some(f), "open" | "ensure_session") = (&self.secrets, bare_name) {
            a.insert("secrets_file".into(), json!(f));
        }
        if bare_name == "open" {
            let dir = self.workspace.join("downloads");
            tokio::fs::create_dir_all(&dir).await?;
            a.insert("downloads_dir".into(), json!(dir));
        }
        // An upload sends a local file to a site: only files of the workspace may go.
        // A relative path names a file in the workspace; bare gets the real path.
        if let Some(files) = a.get_mut("files").and_then(Value::as_array_mut) {
            for f in files.iter_mut() {
                let real = f
                    .as_str()
                    .and_then(|p| std::fs::canonicalize(self.workspace.join(p)).ok())
                    .filter(|p| self.allowed(p))
                    .ok_or_else(|| {
                        anyhow!(
                            "upload path not allowed: {f}: give a path relative to the workspace"
                        )
                    })?;
                *f = json!(real);
            }
        }
        self.relay(bare_name, a).await
    }

    fn no_upload_steps(bare_name: &str, a: &serde_json::Map<String, Value>) -> anyhow::Result<()> {
        if bare_name == "step"
            && a.get("steps")
                .and_then(Value::as_str)
                .is_some_and(|t| t.lines().any(|l| l.trim_start().starts_with("upload")))
        {
            return Err(anyhow!(
                "upload is not allowed in steps: use browser_act with op upload"
            ));
        }
        Ok(())
    }

    async fn relay(
        &self,
        bare_name: &str,
        a: serde_json::Map<String, Value>,
    ) -> anyhow::Result<String> {
        Self::no_upload_steps(bare_name, &a)?;
        Ok(match self.host.call(bare_name, Value::Object(a)).await? {
            Value::String(text) if text.chars().count() > MAX_TEXT => {
                text.chars().take(MAX_TEXT).collect::<String>() + "…[truncated]"
            }
            Value::String(text) => text,
            report @ Value::Object(_) => trim_report(report),
            other => other.to_string().chars().take(MAX_TEXT).collect(),
        })
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
        let real = match &self.guest {
            // The path is a guest path unless it names a file in the host artifact store.
            // A host file system lookup of a guest path could reveal a host file.
            Some(hub) => match std::fs::canonicalize(path) {
                Ok(p)
                    if std::fs::canonicalize(hub.artifacts_root())
                        .is_ok_and(|r| p.starts_with(r)) =>
                {
                    p
                }
                _ => hub.publish(path).await?,
            },
            None => std::fs::canonicalize(path).map_err(|_| anyhow!("path not allowed"))?,
        };
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
            (
                "open".to_owned(),
                json!({"url":"https://example.test", "downloads_dir": ws.join("downloads")})
            )
        );
        let bad = tools.execute(chat, "browser_run_steps", &json!({})).await;
        assert!(!bad.ok);
        // A text reply (a snapshot) goes back as it is; deck sets the screenshot path in the workspace.
        host.replies
            .lock()
            .unwrap()
            .push_back(json!("[e1] button \"Go\""));
        let out = tools
            .execute(
                chat,
                "browser_screenshot",
                &json!({"path": "/etc/x.png", "ref": "e1"}),
            )
            .await;
        assert_eq!(out.content, "[e1] button \"Go\"");
        let (name, args) = host.calls.lock().unwrap()[1].clone();
        assert_eq!((name.as_str(), &args["ref"]), ("screenshot", &json!("e1")));
        assert!(
            args["path"]
                .as_str()
                .unwrap()
                .starts_with(ws.join("shots").to_str().unwrap())
        );
    }

    #[tokio::test]
    async fn a_reply_that_is_not_an_object_goes_back_as_text() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        host.replies.lock().unwrap().push_back(json!([{"t": "a"}]));
        let tools = Tools::new(host.clone(), store, ws.clone(), &ws);
        let out = tools
            .execute(chat, "browser_eval", &json!({"js": "x"}))
            .await;
        assert!(out.ok, "{}", out.content);
        assert_eq!(out.content, r#"[{"t":"a"}]"#);
    }

    #[tokio::test]
    async fn the_configured_secrets_file_goes_to_open_and_ensure_session() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        let secrets = ws.join("secrets.env");
        let tools =
            Tools::new(host.clone(), store, ws.clone(), &ws).with_secrets(Some(secrets.clone()));
        for name in ["browser_open", "browser_ensure_session"] {
            let out = tools
                .execute(
                    chat,
                    name,
                    &json!({"url": "https://x.test", "secrets_file": "/etc/shadow"}),
                )
                .await;
            assert!(out.ok, "{}", out.content);
        }
        let calls = host.calls.lock().unwrap();
        assert_eq!(calls[0].1["secrets_file"], json!(secrets));
        assert_eq!(calls[1].1["secrets_file"], json!(secrets));
    }

    #[tokio::test]
    async fn secret_names_come_without_values() {
        let (_, store, _, ws) = setup().await;
        let f = ws.join("secrets.env");
        std::fs::write(
            &f,
            "# note\nSITE_PASS=hunter2\n\n MAIL_USER = a@b\nbad line\n",
        )
        .unwrap();
        let tools =
            Tools::new(Arc::new(FakeHost::default()), store, ws.clone(), &ws).with_secrets(Some(f));
        assert_eq!(tools.secret_names(), ["SITE_PASS", "MAIL_USER"]);
    }

    #[tokio::test]
    async fn downloads_land_in_the_workspace_and_uploads_stay_in_it() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        let tools = Tools::new(host.clone(), store, ws.clone(), &ws);
        let out = tools
            .execute(
                chat,
                "browser_open",
                &json!({"url": "https://x.test", "downloads_dir": "/tmp"}),
            )
            .await;
        assert!(out.ok, "{}", out.content);
        let dl = host.calls.lock().unwrap()[0].1["downloads_dir"].clone();
        assert_eq!(dl, json!(ws.join("downloads")));
        // A file outside the workspace never reaches bare.
        let bad = tools
            .execute(
                chat,
                "browser_act",
                &json!({"op": "upload", "ref": "e1", "files": ["/etc/passwd"]}),
            )
            .await;
        assert!(
            !bad.ok && bad.content.contains("not allowed"),
            "{}",
            bad.content
        );
        let bad = tools
            .execute(
                chat,
                "browser_step",
                &json!({"steps": "upload css=\"input\" \"/etc/passwd\""}),
            )
            .await;
        assert!(!bad.ok, "{}", bad.content);
        assert_eq!(host.calls.lock().unwrap().len(), 1);
        let f = ws.join("doc.txt");
        std::fs::write(&f, "x").unwrap();
        let ok = tools
            .execute(
                chat,
                "browser_act",
                &json!({"op": "upload", "ref": "e1", "files": [f]}),
            )
            .await;
        assert!(ok.ok, "{}", ok.content);
        // A relative path names a workspace file; bare gets the real path.
        let ok = tools
            .execute(
                chat,
                "browser_act",
                &json!({"op": "upload", "ref": "e1", "files": ["doc.txt"]}),
            )
            .await;
        assert!(ok.ok, "{}", ok.content);
        let sent = host.calls.lock().unwrap().last().unwrap().1["files"].clone();
        assert_eq!(sent, json!([std::fs::canonicalize(&f).unwrap()]));
        let bad = tools
            .execute(
                chat,
                "browser_act",
                &json!({"op": "upload", "ref": "e1", "files": ["../../etc/passwd"]}),
            )
            .await;
        assert!(!bad.ok, "{}", bad.content);
    }

    #[test]
    fn schemas_wrap_bare_tools_without_hidden_args() {
        let bare = vec![
            json!({"name":"run_steps","description":"x","inputSchema":{"type":"object","properties":{}}}),
            json!({"name":"snapshot","description":"Read","inputSchema":{"type":"object","properties":{"session":{"type":"string"},"window":{"type":"boolean"}}}}),
        ];
        let s = schemas(&bare, false);
        let names: Vec<&str> = s
            .iter()
            .map(|t| t["function"]["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, ["browser_snapshot", "send_file"]);
        assert!(
            s[0]["function"]["parameters"]["properties"]
                .get("window")
                .is_none()
        );
    }
}
