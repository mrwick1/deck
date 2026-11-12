//! The agent loop: model calls, tool calls, stored history and run events.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use futures_util::{FutureExt, StreamExt};
use serde_json::{Value, json};
use tokio::sync::broadcast;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use crate::config::Config;
use crate::model::{Delta, ModelClient, ToolCall};
use crate::prompt::SYSTEM;
use crate::store::Store;
use crate::tools::{BrowserHost, Tools, schemas};

/// One run event. `id` is the database id; live text deltas have none.
#[derive(Debug, Clone)]
pub struct Event {
    pub id: Option<i64>,
    pub kind: String,
    pub data: Value,
}

#[derive(Debug, PartialEq)]
pub enum StartError {
    NoChat,
    Busy,
}

#[derive(Debug, PartialEq, Clone, Copy)]
enum Status {
    Done,
    Cancelled,
    /// The agent asked the owner and waits. The task keeps the computer.
    Waiting,
    /// A budget hold stopped the run at a model-call boundary.
    Held,
}

impl Status {
    fn as_str(self) -> &'static str {
        match self {
            Self::Done => "done",
            Self::Cancelled => "cancelled",
            Self::Waiting => "waiting",
            Self::Held => "held",
        }
    }
}

#[derive(Clone)]
struct Run {
    chat_id: i64,
    id: i64,
    cancel: CancellationToken,
    /// The task that this run works for. Steering and holds apply to it.
    task_id: Option<String>,
}

pub struct Agent {
    config: Arc<Config>,
    store: Store,
    model: ModelClient,
    tools: Tools,
    active: Mutex<HashMap<i64, (i64, CancellationToken)>>,
    buses: Mutex<HashMap<i64, broadcast::Sender<Event>>>,
    model_calls: std::sync::atomic::AtomicU64,
}

fn summary(text: &str) -> String {
    text.chars().take(300).collect()
}

impl Agent {
    pub fn new(
        config: Arc<Config>,
        store: Store,
        model: ModelClient,
        host: Arc<dyn BrowserHost>,
    ) -> Self {
        Self::with_tools(
            config.clone(),
            store.clone(),
            model,
            Tools::new(host, store, config.workspace(), &config.data_dir)
                .with_secrets(config.bare.secrets_file.clone()),
        )
    }

    pub fn with_tools(config: Arc<Config>, store: Store, model: ModelClient, tools: Tools) -> Self {
        Self {
            config,
            store,
            model,
            tools,
            active: Mutex::default(),
            buses: Mutex::default(),
            model_calls: Default::default(),
        }
    }

    /// Model calls since the server started.
    pub fn model_calls(&self) -> u64 {
        self.model_calls.load(std::sync::atomic::Ordering::Relaxed)
    }

    fn bus(&self, chat_id: i64) -> broadcast::Sender<Event> {
        self.buses
            .lock()
            .expect("buses lock")
            .entry(chat_id)
            .or_insert_with(|| broadcast::channel(1024).0)
            .clone()
    }

    pub fn subscribe(&self, chat_id: i64) -> broadcast::Receiver<Event> {
        self.bus(chat_id).subscribe()
    }

    /// Cancel a run. Returns false when no such run is active.
    pub fn cancel(&self, run_id: i64) -> bool {
        let active = self.active.lock().expect("active lock");
        match active.values().find(|(id, _)| *id == run_id) {
            Some((_, token)) => {
                token.cancel();
                true
            }
            None => false,
        }
    }

    /// Store the user message, create the run and start the loop.
    /// One run may be active per chat.
    pub async fn start(
        self: &Arc<Self>,
        chat_id: i64,
        user_text: String,
    ) -> anyhow::Result<Result<(i64, JoinHandle<()>), StartError>> {
        self.start_for(chat_id, Some(user_text), None).await
    }

    /// Start a run for a task. `user_text` is stored as the user message; `None` resumes a chat
    /// whose last message is already stored.
    pub async fn start_for(
        self: &Arc<Self>,
        chat_id: i64,
        user_text: Option<String>,
        task_id: Option<String>,
    ) -> anyhow::Result<Result<(i64, JoinHandle<()>), StartError>> {
        // Run in a task, so a dropped request cannot stop between the reserved slot,
        // the stored message and the new run.
        let this = self.clone();
        tokio::spawn(async move { this.start_inner(chat_id, user_text, task_id).await }).await?
    }

    async fn start_inner(
        self: &Arc<Self>,
        chat_id: i64,
        user_text: Option<String>,
        task_id: Option<String>,
    ) -> anyhow::Result<Result<(i64, JoinHandle<()>), StartError>> {
        if !self.store.chat_exists(chat_id).await? {
            return Ok(Err(StartError::NoChat));
        }
        let cancel = CancellationToken::new();
        // Reserve the slot first so two requests cannot both start.
        {
            let mut active = self.active.lock().expect("active lock");
            if active.contains_key(&chat_id) {
                return Ok(Err(StartError::Busy));
            }
            active.insert(chat_id, (0, cancel.clone()));
        }
        let created = async {
            if let Some(text) = &user_text {
                let msg = json!({"role":"user","content":text});
                self.store
                    .add_message(chat_id, "user".into(), msg.to_string())
                    .await?;
            }
            self.store.create_run(chat_id).await
        }
        .await;
        let run_id = match created {
            Ok(id) => id,
            Err(e) => {
                self.active.lock().expect("active lock").remove(&chat_id);
                return Err(e);
            }
        };
        self.active
            .lock()
            .expect("active lock")
            .insert(chat_id, (run_id, cancel.clone()));
        let run = Run {
            chat_id,
            id: run_id,
            cancel,
            task_id,
        };
        let this = self.clone();
        let handle = tokio::spawn(async move { this.drive(run).await });
        Ok(Ok((run_id, handle)))
    }

    async fn drive(&self, run: Run) {
        self.emit(&run, "run_started", json!({}), true).await;
        // A panic in the loop must still finish the run and free the chat.
        let result = std::panic::AssertUnwindSafe(self.run_loop(&run))
            .catch_unwind()
            .await
            .unwrap_or_else(|p| {
                let why = p
                    .downcast_ref::<&str>()
                    .map(|s| s.to_string())
                    .or_else(|| p.downcast_ref::<String>().cloned())
                    .unwrap_or_default();
                Err(anyhow::anyhow!("the run panicked: {why}"))
            });
        let (status, error) = match result {
            Ok(s) => (s.as_str(), None),
            Err(e) => ("failed", Some(format!("{e:#}"))),
        };
        if let Err(e) = self.store.finish_run(run.id, status.into()).await {
            tracing::error!("cannot finish run {}: {e:#}", run.id);
        }
        let mut data = json!({"status": status});
        if let Some(e) = error {
            data["error"] = json!(e);
        }
        self.emit(&run, "run_finished", data, true).await;
        self.active
            .lock()
            .expect("active lock")
            .remove(&run.chat_id);
    }

    async fn emit(&self, run: &Run, kind: &str, mut data: Value, persist: bool) {
        data["run_id"] = json!(run.id);
        // The web app shows how long steps take, also for replayed events.
        data["at"] = json!(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_millis() as u64)
        );
        let id = if persist {
            match self
                .store
                .add_event(run.id, kind.into(), data.to_string())
                .await
            {
                Ok(id) => Some(id),
                Err(e) => {
                    tracing::error!("cannot store event: {e:#}");
                    None
                }
            }
        } else {
            None
        };
        let _ = self.bus(run.chat_id).send(Event {
            id,
            kind: kind.into(),
            data,
        });
    }

    async fn history(&self, chat_id: i64) -> anyhow::Result<Vec<Value>> {
        let names = self.tools.secret_names();
        let system = if names.is_empty() {
            SYSTEM.to_string()
        } else {
            let list: Vec<String> = names.iter().map(|n| format!("`${{{n}}}`")).collect();
            format!(
                "{SYSTEM}\nSecrets that you can type: {}.\n",
                list.join(", ")
            )
        };
        let mut out = vec![json!({"role":"system","content":system})];
        let stored = self.store.list_messages(chat_id).await?.unwrap_or_default();
        for m in stored {
            let v: Value = serde_json::from_str(&m.content)?;
            // File messages are for the client, not for the model.
            if v["type"] != "file" {
                out.push(v);
            }
        }
        Ok(out)
    }

    async fn store_message(&self, chat_id: i64, msg: Value) -> anyhow::Result<()> {
        let role = msg["role"].as_str().unwrap_or("assistant").to_owned();
        self.store
            .add_message(chat_id, role, msg.to_string())
            .await?;
        Ok(())
    }

    async fn run_loop(&self, run: &Run) -> anyhow::Result<Status> {
        // The tools come from bare-mcp itself, so a new bare tool reaches the model with no change here.
        let bare = match self.tools.bare_tools().await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("bare-mcp tools unavailable: {e:#}");
                Vec::new()
            }
        };
        let tool_schemas = schemas(&bare, self.tools.has_guest());
        for _ in 0..self.config.agent.max_steps {
            // The model-call boundary: a budget hold stops the run here, and steering joins it here.
            if self.boundary(run).await? {
                return Ok(Status::Held);
            }
            let messages = self.history(run.chat_id).await?;
            self.model_calls
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let mut stream = Box::pin(self.model.stream(
                &self.config.model.agent_model,
                &messages,
                &tool_schemas,
            ));
            let mut text = String::new();
            let mut calls: Vec<ToolCall> = Vec::new();
            loop {
                let next = tokio::select! {
                    _ = run.cancel.cancelled() => return Ok(Status::Cancelled),
                    n = stream.next() => n,
                };
                match next {
                    None => break,
                    Some(Err(e)) => return Err(e),
                    Some(Ok(Delta::Text(t))) => {
                        self.emit(run, "text_delta", json!({"text": t}), false)
                            .await;
                        text.push_str(&t);
                    }
                    Some(Ok(Delta::ToolCall(mut c))) => {
                        if c.id.is_empty() {
                            c.id = format!("call_{}", uuid::Uuid::new_v4().simple());
                        }
                        calls.push(c);
                    }
                    Some(Ok(Delta::Finish(_))) => {}
                }
            }
            drop(stream);
            let mut assistant = json!({"role":"assistant","content":text});
            if !calls.is_empty() {
                assistant["tool_calls"] = calls
                    .iter()
                    .map(|c| {
                        json!({"id":c.id,"type":"function","function":{
                        "name":c.name,"arguments":c.arguments.to_string()}})
                    })
                    .collect();
            }
            self.store_message(run.chat_id, assistant).await?;
            if !text.is_empty() {
                self.emit(run, "text", json!({"text": text}), true).await;
            }
            if calls.is_empty() {
                return Ok(Status::Done);
            }
            for (i, c) in calls.iter().enumerate() {
                self.emit(
                    run,
                    "tool_call",
                    json!({"id":c.id,"name":c.name,"args":c.arguments}),
                    true,
                )
                .await;
                let outcome = tokio::select! {
                    _ = run.cancel.cancelled() => {
                        self.interrupt(run, &calls[i..]).await?;
                        return Ok(Status::Cancelled);
                    }
                    o = self.tools.execute(run.chat_id, &c.name, &c.arguments) => o,
                };
                self.store_message(
                    run.chat_id,
                    json!({"role":"tool","tool_call_id":c.id,"content":outcome.content}),
                )
                .await?;
                self.emit(
                    run,
                    "tool_result",
                    json!({"id":c.id,"ok":outcome.ok,"summary":summary(&outcome.content)}),
                    true,
                )
                .await;
                if let Some(f) = outcome.file {
                    self.emit(
                        run,
                        "file",
                        json!({"file_id":f.id,"name":f.name,"mime":f.mime}),
                        true,
                    )
                    .await;
                }
                if let Some(question) = outcome.wait {
                    // Later calls of this turn get an error result, so the history stays valid.
                    self.interrupt(run, &calls[i + 1..]).await?;
                    self.emit(run, "waiting", json!({"question": question}), true)
                        .await;
                    return Ok(Status::Waiting);
                }
            }
        }
        Err(anyhow::anyhow!(
            "stopped after {} model calls (max_steps)",
            self.config.agent.max_steps
        ))
    }

    /// Returns true when a budget hold stops the run. Otherwise stores the steering that arrived.
    async fn boundary(&self, run: &Run) -> anyhow::Result<bool> {
        let Some(task) = &run.task_id else {
            return Ok(false);
        };
        if self.hold_check(run).await? {
            self.emit(run, "held", json!({"reason": "budget hold"}), true)
                .await;
            return Ok(true);
        }
        for text in self.store.take_steering(task).await? {
            let msg = json!({"role":"user","content":format!("The owner sent this while you work. Follow it now: {text}")});
            self.store
                .add_message(run.chat_id, "user".into(), msg.to_string())
                .await?;
            self.emit(run, "steer_applied", json!({"text": text}), true)
                .await;
        }
        Ok(false)
    }

    async fn hold_check(&self, run: &Run) -> anyhow::Result<bool> {
        let Some(bot) = self.config.vm.as_ref().map(|v| v.bot_id.as_str()) else {
            return Ok(false);
        };
        Ok(run.task_id.is_some() && self.store.budget_held(bot).await?)
    }

    /// Give each tool call without a result an error result, so the history stays valid.
    async fn interrupt(&self, run: &Run, pending: &[ToolCall]) -> anyhow::Result<()> {
        for c in pending {
            let content = json!({"ok":false,"error":"cancelled by the user"}).to_string();
            self.store_message(
                run.chat_id,
                json!({"role":"tool","tool_call_id":c.id,"content":content}),
            )
            .await?;
            self.emit(
                run,
                "tool_result",
                json!({"id":c.id,"ok":false,"summary":summary(&content)}),
                true,
            )
            .await;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{FakeHost, fake_model, sse, temp_dir};

    fn text_reply(t: &str) -> (u16, String) {
        (
            200,
            sse(&[json!({"choices":[{"delta":{"content":t},"finish_reason":"stop"}]})]),
        )
    }

    fn call_reply(id: &str, name: &str, args: Value) -> (u16, String) {
        (
            200,
            sse(&[
                json!({"choices":[{"delta":{"tool_calls":[{"index":0,"id":id,
                "function":{"name":name,"arguments":args.to_string()}}]},
                "finish_reason":"tool_calls"}]}),
            ]),
        )
    }

    struct Fixture {
        agent: Arc<Agent>,
        store: Store,
        chat: i64,
        requests: Arc<Mutex<Vec<Value>>>,
        host: Arc<FakeHost>,
        dir: std::path::PathBuf,
    }

    async fn fixture(script: Vec<(u16, String)>, host: FakeHost, max_steps: usize) -> Fixture {
        let dir = temp_dir();
        let (url, requests) = fake_model(script).await;
        let mut config = Config::parse(include_str!("../deck.example.toml")).unwrap();
        config.data_dir = dir.clone();
        config.model.base_url = url.clone();
        config.agent.max_steps = max_steps;
        let store = Store::open_memory().unwrap();
        let chat = store.create_chat("c".into()).await.unwrap().id;
        let model = ModelClient::new(&url, Some("k".into()), "K");
        let host = Arc::new(host);
        let agent = Arc::new(Agent::new(
            Arc::new(config),
            store.clone(),
            model,
            host.clone(),
        ));
        Fixture {
            agent,
            store,
            chat,
            requests,
            host,
            dir,
        }
    }

    async fn kinds(f: &Fixture) -> Vec<String> {
        f.store
            .list_events(f.chat, 0)
            .await
            .unwrap()
            .into_iter()
            .map(|e| e.kind)
            .collect()
    }

    /// A client that drops the request must not leave the chat busy forever.
    #[tokio::test]
    async fn dropped_start_does_not_lock_the_chat() {
        let f = fixture(
            vec![text_reply("one"), text_reply("two")],
            FakeHost::default(),
            5,
        )
        .await;
        {
            let fut = f.agent.start(f.chat, "first".into());
            tokio::pin!(fut);
            // Poll until the user message is stored, then drop the future, as axum
            // does when the client goes away.
            loop {
                let polled =
                    std::future::poll_fn(|cx| std::task::Poll::Ready(fut.as_mut().poll(cx))).await;
                if polled.is_ready() {
                    break;
                }
                let stored = f.store.list_messages(f.chat).await.unwrap().unwrap();
                if !stored.is_empty() {
                    break;
                }
                tokio::task::yield_now().await;
            }
        }
        let mut started = false;
        for _ in 0..200 {
            if let Ok(Ok((_, h))) = f.agent.start(f.chat, "second".into()).await {
                h.await.unwrap();
                started = true;
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        assert!(started, "the chat stayed busy after a dropped start");
    }

    #[tokio::test]
    async fn plain_answer() {
        let f = fixture(vec![text_reply("pong")], FakeHost::default(), 5).await;
        let (run_id, h) = f.agent.start(f.chat, "ping".into()).await.unwrap().unwrap();
        h.await.unwrap();
        assert_eq!(
            f.store.run_status(run_id).await.unwrap().as_deref(),
            Some("done")
        );
        assert_eq!(kinds(&f).await, ["run_started", "text", "run_finished"]);
        let msgs = f.store.list_messages(f.chat).await.unwrap().unwrap();
        let roles: Vec<_> = msgs.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(roles, ["user", "assistant"]);
        let last: Value = serde_json::from_str(&msgs[1].content).unwrap();
        assert_eq!(last["content"], "pong");
        let sent = f.requests.lock().unwrap()[0].clone();
        assert_eq!(sent["messages"][0]["role"], "system");
        assert_eq!(sent["stream"], true);
        assert!(sent["tools"].as_array().unwrap().len() >= 5);
    }

    /// Decision D4: a screenshot the user asks for always reaches the chat.
    #[tokio::test]
    async fn screenshot_reaches_the_chat() {
        let out_dir = temp_dir();
        let png = out_dir.join("home.png");
        std::fs::write(&png, b"\x89PNG\r\n\x1a\n").unwrap();
        let host = FakeHost::default();
        host.replies.lock().unwrap().push_back(json!({
            "ok": true,
            "steps": [{"line":1,"step":"shot home","ok":true,"note":png}],
            "out": out_dir,
        }));
        let script = vec![
            call_reply("c1", "browser_step", json!({"steps":"shot home"})),
            call_reply("c2", "send_file", json!({"path": png, "caption": "home"})),
            text_reply("Here is the screenshot."),
        ];
        let f = fixture(script, host, 10).await;
        let (run_id, h) = f
            .agent
            .start(f.chat, "screenshot please".into())
            .await
            .unwrap()
            .unwrap();
        h.await.unwrap();
        assert_eq!(
            f.store.run_status(run_id).await.unwrap().as_deref(),
            Some("done")
        );
        let events = f.store.list_events(f.chat, 0).await.unwrap();
        let file = events
            .iter()
            .find(|e| e.kind == "file")
            .expect("file event");
        let p: Value = serde_json::from_str(&file.payload).unwrap();
        assert_eq!(
            (p["name"].as_str(), p["mime"].as_str()),
            (Some("home.png"), Some("image/png"))
        );
        let msgs = f.store.list_messages(f.chat).await.unwrap().unwrap();
        let file_msg = msgs
            .iter()
            .filter_map(|m| serde_json::from_str::<Value>(&m.content).ok())
            .find(|v| v["type"] == "file")
            .expect("file message");
        assert_eq!(file_msg["file_id"], p["file_id"]);
        let row = f
            .store
            .get_file(p["file_id"].as_i64().unwrap())
            .await
            .unwrap()
            .unwrap();
        assert!(std::fs::read(row.path).unwrap().starts_with(b"\x89PNG"));
        // The model saw the note path and the file message stayed out of its history.
        let third = f.requests.lock().unwrap()[2]["messages"].to_string();
        assert!(third.contains("home.png"));
        assert!(!third.contains("\"type\":\"file\""));
        let _ = &f.dir;
    }

    #[tokio::test]
    async fn cancel_during_a_tool_call() {
        let host = FakeHost {
            hang: true,
            ..FakeHost::default()
        };
        let script = vec![call_reply("c1", "browser_snapshot", json!({}))];
        let f = fixture(script, host, 5).await;
        let mut rx = f.agent.subscribe(f.chat);
        let (run_id, h) = f.agent.start(f.chat, "go".into()).await.unwrap().unwrap();
        loop {
            if rx.recv().await.unwrap().kind == "tool_call" {
                break;
            }
        }
        assert!(f.agent.cancel(run_id));
        h.await.unwrap();
        assert_eq!(
            f.store.run_status(run_id).await.unwrap().as_deref(),
            Some("cancelled")
        );
        let msgs = f.store.list_messages(f.chat).await.unwrap().unwrap();
        let tool: Value = serde_json::from_str(&msgs.last().unwrap().content).unwrap();
        assert_eq!(msgs.last().unwrap().role, "tool");
        assert_eq!(tool["tool_call_id"], "c1");
        assert!(tool["content"].as_str().unwrap().contains("cancelled"));
        assert!(!f.agent.cancel(run_id));
        assert_eq!(f.host.calls.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn a_panic_fails_the_run_and_frees_the_chat() {
        let host = FakeHost {
            panic: true,
            ..FakeHost::default()
        };
        let script = vec![call_reply("c1", "browser_close", json!({}))];
        let f = fixture(script, host, 5).await;
        let (run_id, h) = f.agent.start(f.chat, "go".into()).await.unwrap().unwrap();
        h.await.unwrap();
        assert_eq!(
            f.store.run_status(run_id).await.unwrap().as_deref(),
            Some("failed")
        );
        let events = f.store.list_events(f.chat, 0).await.unwrap();
        let fin: Value = serde_json::from_str(&events.last().unwrap().payload).unwrap();
        assert!(fin["error"].as_str().unwrap().contains("panicked"), "{fin}");
        assert!(f.agent.start(f.chat, "again".into()).await.unwrap().is_ok());
    }

    #[tokio::test]
    async fn max_steps_fails_the_run() {
        let call = || call_reply("c", "browser_close", json!({}));
        let f = fixture(vec![call(), call(), call()], FakeHost::default(), 2).await;
        let (run_id, h) = f.agent.start(f.chat, "loop".into()).await.unwrap().unwrap();
        h.await.unwrap();
        assert_eq!(
            f.store.run_status(run_id).await.unwrap().as_deref(),
            Some("failed")
        );
        let events = f.store.list_events(f.chat, 0).await.unwrap();
        let fin: Value = serde_json::from_str(&events.last().unwrap().payload).unwrap();
        assert_eq!(fin["status"], "failed");
        assert!(fin["error"].as_str().unwrap().contains("max_steps"));
        assert_eq!(f.requests.lock().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn one_run_per_chat_and_missing_chat() {
        let host = FakeHost {
            hang: true,
            ..FakeHost::default()
        };
        let script = vec![call_reply("c1", "browser_close", json!({}))];
        let f = fixture(script, host, 5).await;
        assert_eq!(
            f.agent.start(999, "x".into()).await.unwrap().err(),
            Some(StartError::NoChat)
        );
        let (run_id, h) = f.agent.start(f.chat, "a".into()).await.unwrap().unwrap();
        assert_eq!(
            f.agent.start(f.chat, "b".into()).await.unwrap().err(),
            Some(StartError::Busy)
        );
        f.agent.cancel(run_id);
        h.await.unwrap();
        // The slot is free again.
        assert!(f.agent.start(f.chat, "c".into()).await.unwrap().is_ok());
    }

    #[tokio::test]
    async fn model_error_fails_the_run_with_an_error_event() {
        let f = fixture(vec![(500, "boom".into())], FakeHost::default(), 5).await;
        let (_, h) = f.agent.start(f.chat, "x".into()).await.unwrap().unwrap();
        h.await.unwrap();
        let events = f.store.list_events(f.chat, 0).await.unwrap();
        let fin: Value = serde_json::from_str(&events.last().unwrap().payload).unwrap();
        assert_eq!(fin["status"], "failed");
        assert!(fin["error"].as_str().unwrap().contains("boom"));
    }
}
