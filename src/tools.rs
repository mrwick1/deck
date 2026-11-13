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
    /// The folder of the session files. The model never sees its path.
    state_dir: Option<PathBuf>,
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
        out.push(tool(
            "request_secret",
            "Ask the owner for a password, code or key through protected input. Give a short label and the purpose. The reply names a reference such as ${NAME}. Type that reference. You never see the value. Your task waits.",
            json!({"label": {"type":"string"}, "purpose": {"type":"string"}}),
            &["label", "purpose"],
        ));
        out.push(closed(tool(
            "request_owner_help",
            "Ask the owner to finish a sign-in step that only a person can do: approve a login on a phone, or solve a CAPTCHA. Give short instructions for the owner and, when the service shows one, the number to match. Never put a code, password or other secret in any field: use request_secret for those. The reply has a request_id. Your task waits until the owner answers. The answer is not proof of success.",
            json!({
                "kind": {"type":"string","enum":["phone","captcha","unsupported"],"description":"unsupported: nobody can complete this challenge here. It blocks and opens no request."},
                "instructions": {"type":"string","maxLength":500},
                "match_number": {"type":"string","description":"1 to 8 digits shown by the service"},
                "ttl_secs": {"type":"integer","minimum":1}
            }),
            &["kind", "instructions"],
        )));
        out.push(closed(tool(
            "owner_help_status",
            "Read the state of an owner help request that you opened: open, answered or closed. Answered means the owner says the step is done. It does not mean the sign-in worked.",
            json!({"request_id": {"type":"string"}}),
            &["request_id"],
        )));
        out.push(closed(tool(
            "report_help_verification",
            "After the owner answered, check the service yourself with a read-only browser tool, then report what you saw. Only your own observation can verify. Use not_verified to ask the owner to try again. Use blocked when the sign-in cannot go on.",
            json!({
                "request_id": {"type":"string"},
                "result": {"type":"string","enum":["verified","not_verified","blocked"]},
                "observation": {"type":"string","maxLength":200,"description":"What you saw on the service. Never a secret."}
            }),
            &["request_id", "result", "observation"],
        )));
        out.push(tool(
            "signin_verified",
            "Call this after a sign-in with a protected value has succeeded. Screenshots stay off until you do.",
            json!({}),
            &[],
        ));
    }
    out
}

/// A tool schema that refuses extra keys.
fn closed(mut t: Value) -> Value {
    t["function"]["parameters"]["additionalProperties"] = json!(false);
    t
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
            state_dir: None,
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

    /// Keep each browser session in a file of this folder, so a login survives restarts.
    pub fn with_state_dir(mut self, state_dir: Option<PathBuf>) -> Self {
        self.state_dir = state_dir;
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
            "request_secret" if self.guest.is_some() => {
                return self.request_secret(chat_id, args).await;
            }
            "request_owner_help" | "owner_help_status" | "report_help_verification"
                if self.guest.is_some() =>
            {
                return self.owner_help(chat_id, name, args).await;
            }
            "signin_verified" if self.guest.is_some() => {
                self.signin_verified(chat_id).await.map(|c| (c, None))
            }
            _ => match self.screen_refused(chat_id, name, args).await {
                Some(why) => Err(anyhow!(why)),
                None => self
                    .browser(name, args)
                    .await
                    .map(|content| (content, None)),
            },
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

    /// A screenshot could show a value that sign-in typed. Refuse it while the bot's privacy gate
    /// is open. A failed lookup also refuses.
    async fn screen_refused(&self, chat_id: i64, name: &str, args: &Value) -> Option<&'static str> {
        // bare writes a PNG with the step line `shot NAME`. Comment lines start with `#`.
        let shot = name == "browser_screenshot"
            || (name == "browser_step"
                && args["steps"].as_str().is_some_and(|t| {
                    t.lines().any(|l| {
                        let w = l
                            .split_whitespace()
                            .next()
                            .unwrap_or("")
                            .to_ascii_lowercase();
                        w == "shot" || w == "screenshot"
                    })
                }));
        if !shot || self.guest.is_none() {
            return None;
        }
        let open = match self.store.bot_for_chat(chat_id).await {
            Ok(Some(bot)) => self.store.privacy_gate_open(&bot).await.unwrap_or(true),
            Ok(None) => false,
            Err(_) => true,
        };
        open.then_some("Screenshots are off until the sign-in is verified. Call signin_verified after it succeeds.")
    }

    /// Owner help for a sign-in step. Metadata only. The request belongs to the running task: its
    /// key is the task id. The owner's answer never counts as a verified sign-in. Only the task's
    /// own observation can verify, and deck cannot check that observation against the service.
    async fn owner_help(&self, chat_id: i64, name: &str, args: &Value) -> Outcome {
        use crate::bot::challenge::{self, Challenge, Requested, State, Verification};
        use crate::bot::setup::{Help, Step, Wait};
        let reply = |ok: bool, v: Value, wait: Option<String>| Outcome {
            ok,
            content: v.to_string(),
            file: None,
            wait,
        };
        let fail = |why: &str| reply(false, json!({"ok": false, "error": why}), None);
        let (allowed, required): (&[&str], &[&str]) = match name {
            "request_owner_help" => (
                &["kind", "instructions", "match_number", "ttl_secs"],
                &["kind", "instructions"],
            ),
            "owner_help_status" => (&["request_id"], &["request_id"]),
            _ => (
                &["request_id", "result", "observation"],
                &["request_id", "result", "observation"],
            ),
        };
        let Some(obj) = args.as_object() else {
            return fail("the arguments must be an object");
        };
        if obj.keys().any(|k| !allowed.contains(&k.as_str())) {
            return fail(
                "Pass only the documented fields. Never pass a code, password or other secret",
            );
        }
        let text = |k: &str, max: usize| {
            obj.get(k)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|t| {
                    !t.is_empty() && t.chars().count() <= max && !t.chars().any(char::is_control)
                })
        };
        if required.iter().any(|k| text(k, 500).is_none()) {
            return fail("a required field is missing, empty, too long or not plain text");
        }
        let task = async {
            let bot = self
                .store
                .bot_for_chat(chat_id)
                .await?
                .ok_or_else(|| anyhow!("no bot for this chat"))?;
            self.store
                .owner_task(&bot)
                .await?
                .filter(|t| t.state == "running")
                .ok_or_else(|| anyhow!("no running task"))
        }
        .await;
        let Ok(task) = task else {
            return fail("no running task for this chat");
        };
        let now = crate::bot::secrets::unix_now();
        // The open request of this task, if any. A request of another task or session is not.
        let mine = || async {
            let rows = self.store.setup_records().await.ok()?;
            let row = rows.into_iter().find(|r| r.chat_id == chat_id)?;
            row.setup
                .wait
                .filter(|w: &Wait| w.step == Step::Login(task.id.clone()))
        };
        let waiting =
            |id: &str, what: &str| Some(format!("Owner help needed ({what}), request {id}"));
        let unavailable = |_| fail("could not read or save the owner help request");
        match name {
            "request_owner_help" => {
                let kind = match text("kind", 20) {
                    Some("phone") => Challenge::Phone,
                    Some("captcha") => Challenge::Captcha,
                    Some("unsupported") => Challenge::Unsupported,
                    _ => return fail("kind must be phone, captcha or unsupported"),
                };
                let ttl = match obj.get("ttl_secs") {
                    None => None,
                    Some(v) => match v.as_u64().filter(|t| *t > 0) {
                        Some(t) => Some(t),
                        None => return fail("ttl_secs must be a whole number above zero"),
                    },
                };
                let instructions = text("instructions", 500).map(str::to_owned);
                let help = Help {
                    instructions: instructions.clone(),
                    match_number: obj
                        .get("match_number")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                };
                if obj.contains_key("match_number") && help.match_number.is_none() {
                    return fail("match_number must be a string of 1 to 8 digits");
                }
                match challenge::request_login_help(
                    &self.store,
                    chat_id,
                    &task.id,
                    kind,
                    help,
                    ttl,
                    now,
                )
                .await
                {
                    Ok(Ok(Requested::Open(id))) => reply(
                        true,
                        json!({"ok": true, "request_id": id, "waiting": "the owner was asked"}),
                        waiting(&id, instructions.as_deref().unwrap_or("")),
                    ),
                    Ok(Ok(Requested::Blocked(id))) => reply(
                        false,
                        json!({"ok": false, "blocked": true, "request_id": id, "error": "nobody can complete this challenge here. Tell the owner and stop this sign-in"}),
                        None,
                    ),
                    Ok(Err(e)) => fail(&e.to_string()),
                    Err(e) => unavailable(e),
                }
            }
            "owner_help_status" => {
                let id = text("request_id", 128).unwrap_or_default().to_owned();
                let state = match challenge::challenge_state(&self.store, chat_id, &id, now).await {
                    Ok(s) => s,
                    Err(e) => return unavailable(e),
                };
                let current = mine().await;
                let owned = current.as_ref().is_some_and(|w| w.request_id == id);
                match state {
                    State::Open { expires_at } if owned => reply(
                        true,
                        json!({"ok": true, "state": "open", "request_id": id, "expires_at": expires_at}),
                        waiting(&id, "still open"),
                    ),
                    State::Answered if owned => reply(
                        true,
                        json!({"ok": true, "state": "answered", "request_id": id, "note": "The owner says the step is done. That is not proof. Check the service with a read-only tool, then call report_help_verification."}),
                        None,
                    ),
                    State::Closed(outcome) => {
                        let mut v = json!({"ok": true, "state": "closed", "request_id": id, "outcome": outcome});
                        let again = matches!(outcome, challenge::Outcome::Expired)
                            .then_some(current)
                            .flatten();
                        match again {
                            Some(w) => {
                                v["replacement_request_id"] = json!(w.request_id);
                                reply(
                                    true,
                                    v,
                                    waiting(&w.request_id, "request expired and was renewed"),
                                )
                            }
                            None => reply(true, v, None),
                        }
                    }
                    _ => reply(
                        true,
                        json!({"ok": true, "state": "unknown", "request_id": id}),
                        None,
                    ),
                }
            }
            _ => {
                let id = text("request_id", 128).unwrap_or_default().to_owned();
                let note = text("observation", 200).map(str::to_owned);
                let Some(note) = note else {
                    return fail("observation must be up to 200 characters");
                };
                let v = match text("result", 20) {
                    Some("verified") => Verification::Verified,
                    Some("not_verified") => Verification::NotVerified(note),
                    Some("blocked") => Verification::Blocked(note),
                    _ => return fail("result must be verified, not_verified or blocked"),
                };
                if mine().await.is_none_or(|w| w.request_id != id) {
                    return fail("this is not an open help request of your task");
                }
                let retry = matches!(v, Verification::NotVerified(_));
                match challenge::verify_login_help(&self.store, chat_id, &id, v, now).await {
                    Ok(Ok(())) if retry => match mine().await {
                        Some(w) => reply(
                            true,
                            json!({"ok": true, "request_id": w.request_id, "waiting": "the owner was asked again"}),
                            waiting(&w.request_id, "try again"),
                        ),
                        None => reply(true, json!({"ok": true}), None),
                    },
                    Ok(Ok(())) => reply(true, json!({"ok": true}), None),
                    Ok(Err(e)) => fail(&e.to_string()),
                    Err(e) => unavailable(e),
                }
            }
        }
    }

    async fn signin_verified(&self, chat_id: i64) -> anyhow::Result<String> {
        let bot = self
            .store
            .bot_for_chat(chat_id)
            .await?
            .ok_or_else(|| anyhow!("no bot for this chat"))?;
        self.store.close_privacy_gate(&bot).await?;
        Ok(json!({"ok": true}).to_string())
    }

    /// Open a durable request for protected input. The reply has a reference, never a value.
    async fn request_secret(&self, chat_id: i64, args: &Value) -> Outcome {
        let fail = |why: &str| Outcome {
            ok: false,
            content: json!({"ok": false, "error": why}).to_string(),
            file: None,
            wait: None,
        };
        let text = |key: &str, max: usize| {
            args[key].as_str().map(str::trim).filter(|t| {
                !t.is_empty() && t.chars().count() <= max && !t.chars().any(char::is_control)
            })
        };
        let (Some(label), Some(purpose)) = (text("label", 100), text("purpose", 300)) else {
            return fail(
                "label (up to 100 characters) and purpose (up to 300) are required, with no control characters",
            );
        };
        if args
            .as_object()
            .is_some_and(|o| o.keys().any(|k| k != "label" && k != "purpose"))
        {
            return fail(
                "Pass only label and purpose. Never pass a value: the owner enters it in protected input",
            );
        }
        let opened = async {
            let bot = self
                .store
                .bot_for_chat(chat_id)
                .await?
                .ok_or_else(|| anyhow!("no bot for this chat"))?;
            let task = self
                .store
                .owner_task(&bot)
                .await?
                .filter(|t| t.state == "running")
                .ok_or_else(|| anyhow!("no running task"))?;
            let id = format!("secret-{}", uuid::Uuid::new_v4().simple());
            let expires = crate::bot::secrets::unix_now() + crate::bot::secrets::REQUEST_TTL_SECS;
            self.store
                .create_secret_request(&id, &bot, &task.id, label, purpose, expires)
                .await?;
            anyhow::Ok(id)
        }
        .await;
        match opened {
            Ok(id) => Outcome {
                ok: true,
                content: json!({"ok": true, "waiting": "protected input requested", "reference": format!("${{{}}}", crate::bot::secrets::canonical_name(label))}).to_string(),
                file: None,
                wait: Some(format!("Protected input needed for {label}: {purpose} (request {id})")),
            },
            Err(_) => fail("could not open the request"),
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
        // A session file lives in the state folder. The model names the session, never the path.
        // `open` names a saved session with `state`; the other two with `name`.
        let key = if bare_name == "open" { "state" } else { "name" };
        if let (Some(dir), "save_state" | "ensure_session" | "open") = (&self.state_dir, bare_name)
            && (bare_name != "open" || a.contains_key("state"))
        {
            let name = a
                .get(key)
                .and_then(Value::as_str)
                .filter(|n| {
                    (1..=64).contains(&n.len())
                        && n.bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                })
                .ok_or_else(|| anyhow!("session name must be 1 to 64 characters of A-Za-z0-9_-"))?;
            let file = dir.join(format!("{name}.state"));
            let mut b = std::fs::DirBuilder::new();
            b.recursive(true);
            #[cfg(unix)]
            std::os::unix::fs::DirBuilderExt::mode(&mut b, 0o700);
            b.create(dir)?;
            a.insert("file".into(), json!(file));
        }
        if let "open" | "ensure_session" = bare_name {
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
    async fn session_files_live_under_the_state_dir() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        let state = ws.join("state");
        let tools =
            Tools::new(host.clone(), store, ws.clone(), &ws).with_state_dir(Some(state.clone()));
        for name in ["browser_save_state", "browser_ensure_session"] {
            let out = tools
                .execute(chat, name, &json!({"name": "google", "file": "/etc/x"}))
                .await;
            assert!(out.ok, "{}", out.content);
        }
        // `open` names the session with `state`. It gets the same file, so a new bare-mcp can read it.
        let out = tools
            .execute(
                chat,
                "browser_open",
                &json!({"url": "https://x.test", "state": "google"}),
            )
            .await;
        assert!(out.ok, "{}", out.content);
        assert!(state.is_dir());
        let calls = host.calls.lock().unwrap();
        assert_eq!(calls[0].1["file"], json!(state.join("google.state")));
        assert_eq!(calls[1].1["file"], json!(state.join("google.state")));
        assert_eq!(calls[2].1["file"], json!(state.join("google.state")));
        // A session that ensure_session opens downloads into the workspace too.
        assert_eq!(calls[1].1["downloads_dir"], json!(ws.join("downloads")));
    }

    #[tokio::test]
    async fn a_bad_session_name_makes_no_host_call() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        let tools =
            Tools::new(host.clone(), store, ws.clone(), &ws).with_state_dir(Some(ws.join("state")));
        let out = tools
            .execute(chat, "browser_save_state", &json!({"name": "../x"}))
            .await;
        assert!(!out.ok, "{}", out.content);
        assert!(out.content.contains("session name must be 1 to 64"));
        assert!(host.calls.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn without_a_state_dir_no_file_is_sent() {
        let (_, store, chat, ws) = setup().await;
        let host = Arc::new(FakeHost::default());
        let tools = Tools::new(host.clone(), store, ws.clone(), &ws);
        let out = tools
            .execute(
                chat,
                "browser_save_state",
                &json!({"name": "google", "file": "/etc/x"}),
            )
            .await;
        assert!(out.ok, "{}", out.content);
        assert!(host.calls.lock().unwrap()[0].1.get("file").is_none());
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

    /// A bot with a running task and a computer. The computer is never used by these tools.
    async fn computer_bot() -> (Tools, Store, i64, Arc<FakeHost>) {
        let (_, store, _, ws) = setup().await;
        let chat = store.bot_flags("b", "Bot").await.unwrap().chat_id;
        let task = store
            .add_task("b", chat, "message", "t".into(), "work".into(), 1)
            .await
            .unwrap();
        store
            .move_task(&task.id, &["queued"], "running", None)
            .await
            .unwrap();
        let host = Arc::new(FakeHost::default());
        let hub = Hub::new(
            store.clone(),
            "b",
            ws.clone(),
            crate::bot::vm::FakeVm::new(true, false),
            5,
        );
        let tools = Tools::new(host.clone(), store.clone(), ws.clone(), &ws).with_guest(hub);
        (tools, store, chat, host)
    }

    #[tokio::test]
    async fn request_secret_waits_and_names_a_reference_without_taking_a_value() {
        let (tools, store, chat, _) = computer_bot().await;
        let names: Vec<String> = schemas(&[], true)
            .iter()
            .map(|t| t["function"]["name"].as_str().unwrap().to_owned())
            .collect();
        assert!(
            names.contains(&"request_secret".to_owned())
                && names.contains(&"signin_verified".to_owned())
        );
        let args = json!({"label": "Site password", "purpose": "sign in to the site"});
        let out = tools.execute(chat, "request_secret", &args).await;
        assert!(out.ok, "{}", out.content);
        let name = crate::bot::secrets::canonical_name("Site password");
        assert!(out.content.contains(&format!("${{{name}}}")));
        let question = out.wait.expect("the task waits");
        let id = question
            .split("request ")
            .nth(1)
            .unwrap()
            .trim_end_matches(')')
            .to_owned();
        let open = store
            .pending_secret_request("b", &id)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(open.label, "Site password");
        // The model cannot pass a value, and a refused call opens nothing.
        let bad = tools
            .execute(
                chat,
                "request_secret",
                &json!({"label": "L", "purpose": "p", "value": "hunter2"}),
            )
            .await;
        assert!(!bad.ok && bad.wait.is_none() && !bad.content.contains("hunter2"));
        assert!(
            store
                .pending_secret_request("b", "x")
                .await
                .unwrap()
                .is_none()
        );
        for args in [
            json!({"purpose": "p"}),
            json!({"label": "L\nM", "purpose": "p"}),
            json!({"label": "L"}),
        ] {
            assert!(!tools.execute(chat, "request_secret", &args).await.ok);
        }
    }

    #[tokio::test]
    async fn screenshots_stay_off_until_the_sign_in_is_verified() {
        let (tools, store, chat, host) = computer_bot().await;
        store
            .record_secret("b", "r1", "L", "DECK_X", None)
            .await
            .unwrap();
        for (name, args) in [
            ("browser_screenshot", json!({})),
            (
                "browser_step",
                json!({"steps": "open https://x.test\nscreenshot"}),
            ),
        ] {
            let out = tools.execute(chat, name, &args).await;
            assert!(
                !out.ok && out.content.contains("signin_verified"),
                "{name}: {}",
                out.content
            );
        }
        assert!(host.calls.lock().unwrap().is_empty());
        // Other browser calls still work, and verification lifts the gate.
        host.replies.lock().unwrap().push_back(json!({"ok": true}));
        assert!(
            tools
                .execute(chat, "browser_open", &json!({"url": "https://x.test"}))
                .await
                .ok
        );
        assert!(tools.execute(chat, "signin_verified", &json!({})).await.ok);
        assert!(!store.privacy_gate_open("b").await.unwrap());
        host.replies.lock().unwrap().push_back(json!({"ok": true}));
        assert!(
            tools
                .execute(chat, "browser_screenshot", &json!({}))
                .await
                .ok
        );
    }

    /// A ready, set-up bot with a running task, as the driver leaves it. Returns the task id.
    async fn help_bot() -> (Tools, Store, i64, String, Arc<FakeHost>) {
        use crate::bot::setup::{Authority, Budget, Draft, Limits, Resources, Setup, Status};
        let (_, store, _, ws) = setup().await;
        let id = "help-bot".to_string();
        let s = Setup::new(
            &id,
            Draft {
                revision: 1,
                name: "Fixture".into(),
                instructions: "Use the local fixture".into(),
                model: "fixture".into(),
                authority: Authority::Full,
                resources: Resources {
                    cpus: 2,
                    memory_mib: 2048,
                    disk_gib: 10,
                },
                budget: Some(Budget {
                    currency: "TST".into(),
                    exponent: 2,
                    period: "test".into(),
                    allowance: 10,
                    ceiling: 100,
                }),
                credentials: vec![],
                task: Some("Sign in to the fixture".into()),
            },
        )
        .unwrap();
        store.create_setup(&s).await.unwrap();
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let limits = Limits {
            max_cpus: 2,
            max_memory_mib: 2048,
            max_disk_gib: 10,
        };
        store
            .approve_setup(&id, v, 1, "test-owner", &limits, "fixture")
            .await
            .unwrap();
        let row = store.setup_record(&id).await.unwrap().unwrap();
        let mut s = row.setup;
        s.steps.iter_mut().for_each(|t| t.status = Status::Done);
        store.save_setup_state(row.version, &s).await.unwrap();
        store.finish_setup(&id).await.unwrap();
        let chat = store.setup_record(&id).await.unwrap().unwrap().chat_id;
        let task = store
            .add_task(&id, chat, "message", "t".into(), "work".into(), 1)
            .await
            .unwrap();
        store
            .move_task(&task.id, &["queued"], "running", None)
            .await
            .unwrap();
        let host = Arc::new(FakeHost::default());
        let hub = Hub::new(
            store.clone(),
            &id,
            ws.clone(),
            crate::bot::vm::FakeVm::new(true, false),
            5,
        );
        let tools = Tools::new(host.clone(), store.clone(), ws.clone(), &ws).with_guest(hub);
        (tools, store, chat, task.id, host)
    }

    async fn wait_of(store: &Store, chat: i64) -> Option<crate::bot::setup::Wait> {
        let rows = store.setup_records().await.unwrap();
        rows.into_iter()
            .find(|r| r.chat_id == chat)
            .unwrap()
            .setup
            .wait
    }

    /// What the owner route does: the owner marks the action done.
    async fn owner_says_done(store: &Store, chat: i64, request: &str) {
        let row = store
            .setup_records()
            .await
            .unwrap()
            .into_iter()
            .find(|r| r.chat_id == chat)
            .unwrap();
        let mut s = row.setup;
        s.respond(request, crate::bot::challenge::Decision::Completed, 0)
            .unwrap();
        store.save_setup_state(row.version, &s).await.unwrap();
    }

    fn request_id(out: &Outcome) -> String {
        let v: Value = serde_json::from_str(&out.content).unwrap();
        v["request_id"].as_str().unwrap().to_owned()
    }

    fn phone() -> Value {
        json!({"kind": "phone", "instructions": "Approve the sign-in on your phone", "match_number": "42"})
    }

    #[test]
    fn the_help_tools_have_closed_schemas_and_only_a_computer_bot_gets_them() {
        let names = |computer| -> Vec<String> {
            schemas(&[], computer)
                .iter()
                .map(|t| t["function"]["name"].as_str().unwrap().to_owned())
                .collect()
        };
        for n in [
            "request_owner_help",
            "owner_help_status",
            "report_help_verification",
        ] {
            assert!(names(true).contains(&n.to_owned()), "{n}");
            assert!(!names(false).contains(&n.to_owned()), "{n}");
        }
        let all = schemas(&[], true);
        let get = |n: &str| {
            all.iter().find(|t| t["function"]["name"] == n).unwrap()["function"]["parameters"]
                .clone()
        };
        let ask = get("request_owner_help");
        assert_eq!(ask["additionalProperties"], false);
        assert_eq!(
            ask["properties"]["kind"]["enum"],
            json!(["phone", "captcha", "unsupported"])
        );
        assert_eq!(ask["required"], json!(["kind", "instructions"]));
        let st = get("owner_help_status");
        assert_eq!(st["additionalProperties"], false);
        assert_eq!(st["required"], json!(["request_id"]));
        let rep = get("report_help_verification");
        assert_eq!(rep["additionalProperties"], false);
        assert_eq!(
            rep["properties"]["result"]["enum"],
            json!(["verified", "not_verified", "blocked"])
        );
        assert_eq!(
            rep["required"],
            json!(["request_id", "result", "observation"])
        );
    }

    #[tokio::test]
    async fn owner_help_waits_for_the_owner_and_ends_the_run_with_a_request_identity() {
        let (tools, store, chat, task, _) = help_bot().await;
        let out = tools.execute(chat, "request_owner_help", &phone()).await;
        assert!(out.ok, "{}", out.content);
        let id = request_id(&out);
        assert!(out.wait.as_deref().is_some_and(|w| w.contains(&id)));
        let w = wait_of(&store, chat).await.unwrap();
        assert_eq!(w.request_id, id);
        assert_eq!(w.step, crate::bot::setup::Step::Login(task));
        // The same ask returns the same request. A different kind is refused.
        assert_eq!(
            request_id(&tools.execute(chat, "request_owner_help", &phone()).await),
            id
        );
        let other = tools
            .execute(
                chat,
                "request_owner_help",
                &json!({"kind": "captcha", "instructions": "Solve it"}),
            )
            .await;
        assert!(!other.ok && other.wait.is_none());
        assert_eq!(wait_of(&store, chat).await.unwrap().request_id, id);
    }

    #[tokio::test]
    async fn owner_help_takes_metadata_only_and_rejects_anything_else() {
        let (tools, store, chat, _, _) = help_bot().await;
        let bad = [
            json!({"kind": "phone", "instructions": "x", "code": "123456"}),
            json!({"kind": "phone", "instructions": "x", "value": "sentinel"}),
            json!({"kind": "sms", "instructions": "x"}),
            json!({"kind": "phone"}),
            json!({"kind": "phone", "instructions": "  "}),
            json!({"kind": "phone", "instructions": "x", "match_number": "12ab"}),
            json!({"kind": "phone", "instructions": "x", "ttl_secs": 0}),
            json!({"kind": "phone", "instructions": "x", "ttl_secs": "9"}),
            json!({"kind": "phone", "instructions": 5}),
            json!(null),
        ];
        for args in bad {
            let out = tools.execute(chat, "request_owner_help", &args).await;
            assert!(!out.ok && out.wait.is_none(), "{args}");
            assert!(!out.content.contains("sentinel") && !out.content.contains("123456"));
        }
        assert!(wait_of(&store, chat).await.is_none());
    }

    #[tokio::test]
    async fn an_unsupported_challenge_blocks_without_a_wait() {
        let (tools, store, chat, _, _) = help_bot().await;
        let out = tools
            .execute(
                chat,
                "request_owner_help",
                &json!({"kind": "unsupported", "instructions": "A hardware key is required"}),
            )
            .await;
        assert!(!out.ok && out.wait.is_none());
        assert!(out.content.contains("blocked"), "{}", out.content);
        assert!(wait_of(&store, chat).await.is_none());
    }

    #[tokio::test]
    async fn an_owner_answer_is_not_proof_and_the_task_verifies_what_it_saw() {
        let (tools, store, chat, _, _) = help_bot().await;
        store
            .set_hold(
                "help-bot",
                crate::bot::state::HoldKind::AutomationPause,
                "owner",
            )
            .await
            .unwrap();
        let id = request_id(&tools.execute(chat, "request_owner_help", &phone()).await);
        let status = |id: String| {
            let tools = &tools;
            async move {
                tools
                    .execute(chat, "owner_help_status", &json!({"request_id": id}))
                    .await
            }
        };
        // Still open: the status re-parks the task instead of letting it poll.
        let s = status(id.clone()).await;
        assert!(
            s.ok && s.content.contains("\"open\"") && s.wait.is_some(),
            "{}",
            s.content
        );
        // A verification before the owner answers fails and changes nothing.
        let report = |id: String, result: &str, note: &str| {
            let args = json!({"request_id": id, "result": result, "observation": note});
            let tools = &tools;
            async move { tools.execute(chat, "report_help_verification", &args).await }
        };
        assert!(!report(id.clone(), "verified", "inbox is visible").await.ok);
        assert_eq!(wait_of(&store, chat).await.unwrap().request_id, id);
        owner_says_done(&store, chat, &id).await;
        let s = status(id.clone()).await;
        assert!(s.ok && s.content.contains("answered") && s.wait.is_none());
        assert!(s.content.contains("not proof"), "{}", s.content);
        // The observation is required, and the task must not close on the answer alone.
        assert!(!report(id.clone(), "verified", "  ").await.ok);
        assert!(wait_of(&store, chat).await.is_some());
        let v = report(id.clone(), "verified", "the inbox page showed the mailbox").await;
        assert!(v.ok && v.wait.is_none(), "{}", v.content);
        assert!(wait_of(&store, chat).await.is_none());
        let s = status(id.clone()).await;
        assert!(s.content.contains("closed") && s.content.contains("verified"));
        // A report cannot close the same request twice.
        assert!(!report(id, "verified", "again").await.ok);
        // Holds stay in force through the whole exchange.
        assert!(!store.holds("help-bot").await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_rejected_verification_opens_a_fresh_request_and_waits() {
        let (tools, store, chat, _, _) = help_bot().await;
        let id = request_id(&tools.execute(chat, "request_owner_help", &phone()).await);
        owner_says_done(&store, chat, &id).await;
        let args = json!({"request_id": id, "result": "not_verified", "observation": "the page still asks for approval"});
        let out = tools.execute(chat, "report_help_verification", &args).await;
        assert!(out.ok, "{}", out.content);
        let fresh = request_id(&out);
        assert_ne!(fresh, id);
        assert!(out.wait.as_deref().is_some_and(|w| w.contains(&fresh)));
        assert_eq!(wait_of(&store, chat).await.unwrap().request_id, fresh);
        // Blocked closes the exchange without a wait.
        owner_says_done(&store, chat, &fresh).await;
        let args = json!({"request_id": fresh, "result": "blocked", "observation": "the account is locked"});
        let out = tools.execute(chat, "report_help_verification", &args).await;
        assert!(out.ok && out.wait.is_none() && wait_of(&store, chat).await.is_none());
    }

    #[tokio::test]
    async fn a_task_cannot_verify_or_read_the_request_of_another_task() {
        let (tools, store, chat, _, _) = help_bot().await;
        let theirs = match crate::bot::challenge::request_login_help(
            &store,
            chat,
            "another-task",
            crate::bot::challenge::Challenge::Phone,
            crate::bot::setup::Help {
                instructions: Some("Approve".into()),
                match_number: None,
            },
            None,
            0,
        )
        .await
        .unwrap()
        .unwrap()
        {
            crate::bot::challenge::Requested::Open(id) => id,
            other => panic!("{other:?}"),
        };
        owner_says_done(&store, chat, &theirs).await;
        let args = json!({"request_id": theirs, "result": "verified", "observation": "seen"});
        assert!(
            !tools
                .execute(chat, "report_help_verification", &args)
                .await
                .ok
        );
        let s = tools
            .execute(chat, "owner_help_status", &json!({"request_id": theirs}))
            .await;
        assert!(!s.content.contains("answered"), "{}", s.content);
        // Asking is refused while the other request is open.
        assert!(!tools.execute(chat, "request_owner_help", &phone()).await.ok);
        let w = wait_of(&store, chat).await.unwrap();
        assert!(w.answered && w.request_id == theirs);
    }

    #[tokio::test]
    async fn an_expired_help_request_reports_its_replacement() {
        let (tools, store, chat, task, _) = help_bot().await;
        let old = match crate::bot::challenge::request_login_help(
            &store,
            chat,
            &task,
            crate::bot::challenge::Challenge::Phone,
            crate::bot::setup::Help {
                instructions: Some("Approve".into()),
                match_number: None,
            },
            Some(5),
            1,
        )
        .await
        .unwrap()
        .unwrap()
        {
            crate::bot::challenge::Requested::Open(id) => id,
            other => panic!("{other:?}"),
        };
        let s = tools
            .execute(chat, "owner_help_status", &json!({"request_id": old}))
            .await;
        assert!(s.ok && s.content.contains("expired"), "{}", s.content);
        let fresh = wait_of(&store, chat).await.unwrap().request_id;
        assert_ne!(fresh, old);
        assert!(s.content.contains(&fresh), "{}", s.content);
        assert!(s.wait.as_deref().is_some_and(|w| w.contains(&fresh)));
    }

    #[tokio::test]
    async fn an_expired_secret_request_gets_a_fresh_identity() {
        let (tools, store, chat, _) = computer_bot().await;
        let args = json!({"label": "Site password", "purpose": "sign in"});
        let first = tools.execute(chat, "request_secret", &args).await;
        let old = first
            .wait
            .unwrap()
            .split("request ")
            .nth(1)
            .unwrap()
            .trim_end_matches(')')
            .to_owned();
        let o = old.clone();
        store
            .run(move |c| {
                c.execute(
                    "UPDATE secret_requests SET expires_at=1 WHERE request_id=?1",
                    [&o],
                )
            })
            .await
            .unwrap();
        let again = tools.execute(chat, "request_secret", &args).await;
        let new = again
            .wait
            .unwrap()
            .split("request ")
            .nth(1)
            .unwrap()
            .trim_end_matches(')')
            .to_owned();
        assert_ne!(old, new);
        assert!(
            store
                .pending_secret_request("b", &old)
                .await
                .unwrap()
                .is_none()
        );
        assert!(
            store
                .pending_secret_request("b", &new)
                .await
                .unwrap()
                .is_some()
        );
    }

    #[tokio::test]
    async fn read_only_checks_work_while_the_screenshot_gate_is_open() {
        let (tools, store, chat, host) = computer_bot().await;
        store
            .record_secret("b", "r1", "L", "DECK_X", None)
            .await
            .unwrap();
        for name in [
            "browser_snapshot",
            "browser_read",
            "browser_find",
            "browser_links",
        ] {
            host.replies.lock().unwrap().push_back(json!({"ok": true}));
            assert!(tools.execute(chat, name, &json!({})).await.ok, "{name}");
        }
        // A step list without a screenshot also works. One with a screenshot does not.
        host.replies.lock().unwrap().push_back(json!({"ok": true}));
        assert!(
            tools
                .execute(chat, "browser_step", &json!({"steps": "snapshot"}))
                .await
                .ok
        );
        // bare writes a PNG with the `shot NAME` step line.
        let comment = json!({"steps": "# shot later\nsnapshot"});
        host.replies.lock().unwrap().push_back(json!({"ok": true}));
        assert!(tools.execute(chat, "browser_step", &comment).await.ok);
        for steps in ["shot a", "snapshot\n  shot x", "SHOT x", "screenshot"] {
            let out = tools
                .execute(chat, "browser_step", &json!({"steps": steps}))
                .await;
            assert!(!out.ok, "{steps}");
        }
    }
}
