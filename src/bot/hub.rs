//! The server's one long-lived executor session, and the tools that run through it.
//!
//! Everything the agent does on the computer is a journaled executor command: shell, files and
//! bare. A guest path never reaches the host file system. Files come back as checked artifacts.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, anyhow, bail};
use futures_util::future::BoxFuture;
use serde_json::{Value, json};

use super::vm::Vm;
use crate::executor::client::{Options, Session};
use crate::executor::journal::Cmd;
use crate::store::Store;
use crate::tools::BrowserHost;

/// Guest tools the executor runs. They are installed by `vmproto.py install-executor`.
const GUEST_BIN: &str = "/usr/local/bin";
const BARE_DEADLINE_SECS: u64 = 180;
const LIST_DEADLINE_SECS: u64 = 60;
/// Time that a command gets, beyond its deadline, to report its end.
const END_GRACE: Duration = Duration::from_secs(20);

pub struct Hub {
    store: Store,
    bot: String,
    data_dir: PathBuf,
    vm: Arc<dyn Vm>,
    session: Mutex<Option<Arc<Session>>>,
    /// The task that owns the desktop. Tool calls use it as the owner of their commands.
    task: Mutex<String>,
    bin_dir: String,
    pub command_secs: u64,
}

/// What a stop found. `unconfirmed` lists commands whose end the guest did not confirm.
#[derive(Debug, Default, PartialEq)]
pub struct StopReport {
    pub confirmed: Vec<String>,
    pub unconfirmed: Vec<String>,
}

impl Hub {
    pub fn new(
        store: Store,
        bot: &str,
        data_dir: PathBuf,
        vm: Arc<dyn Vm>,
        command_secs: u64,
    ) -> Arc<Self> {
        Arc::new(Self {
            store,
            bot: bot.into(),
            data_dir,
            vm,
            session: Mutex::new(None),
            task: Mutex::new(String::new()),
            bin_dir: GUEST_BIN.into(),
            command_secs,
        })
    }

    #[cfg(test)]
    pub fn with_bin_dir(mut self: Arc<Self>, dir: &str) -> Arc<Self> {
        Arc::get_mut(&mut self).unwrap().bin_dir = dir.into();
        self
    }

    pub fn artifacts_root(&self) -> PathBuf {
        self.data_dir.join("bots").join(&self.bot).join("artifacts")
    }

    pub fn set_task(&self, id: &str) {
        *self.task.lock().unwrap() = id.into();
    }

    pub fn has_session(&self) -> bool {
        self.session
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|s| s.is_alive())
    }

    pub fn drop_session(&self) {
        *self.session.lock().unwrap() = None;
    }

    fn session(&self) -> anyhow::Result<Arc<Session>> {
        self.session
            .lock()
            .unwrap()
            .clone()
            .filter(|s| s.is_alive())
            .ok_or_else(|| anyhow!("the computer is not connected"))
    }

    /// Connect with a new epoch and reconcile the journal. No work is dispatched before this ends.
    pub async fn connect(&self, peer_pid: Option<u32>) -> anyhow::Result<()> {
        let mut o = Options::new(&self.bot, &self.vm.endpoint(), &self.data_dir);
        o.peer_pid = peer_pid;
        o.request_timeout = Duration::from_secs(20);
        let s = Session::connect(self.store.clone(), o).await?;
        s.reconcile().await?;
        *self.session.lock().unwrap() = Some(Arc::new(s));
        Ok(())
    }

    /// Run one command to its end and fetch its artifacts. The guest decides the end state.
    pub async fn run(
        &self,
        argv: Vec<String>,
        deadline_secs: u64,
    ) -> anyhow::Result<(Cmd, Vec<PathBuf>)> {
        let task = self.task.lock().unwrap().clone();
        if task.is_empty() {
            bail!("no task owns the computer");
        }
        let s = self.session()?;
        let id = format!("c{}", uuid::Uuid::new_v4().simple());
        s.exec(&task, &id, argv, deadline_secs.clamp(1, 3600) * 1000)
            .await?;
        let cmd = s
            .wait(&id, Duration::from_secs(deadline_secs) + END_GRACE)
            .await?;
        let files = if cmd.artifacts.is_empty() || !cmd.is_final() {
            vec![]
        } else {
            s.fetch_artifacts(&id).await?
        };
        Ok((cmd, files))
    }

    /// The `shell` tool.
    pub async fn shell(&self, script: &str, secs: Option<u64>) -> anyhow::Result<String> {
        let secs = secs.unwrap_or(self.command_secs).clamp(1, 3600);
        let (cmd, files) = self
            .run(vec!["/bin/sh".into(), "-c".into(), script.into()], secs)
            .await?;
        let ok = cmd.state == "succeeded";
        Ok(json!({
            "ok": ok, "state": cmd.state, "exit_code": cmd.exit_code, "detail": cmd.detail,
            "stdout": cmd.stdout, "stderr": cmd.stderr, "truncated": cmd.truncated,
            "files": files.iter().map(|p| p.display().to_string()).collect::<Vec<_>>(),
        })
        .to_string())
    }

    /// Copy one workspace file of the guest into the host artifact store. Returns the host path.
    pub async fn publish(&self, guest_path: &str) -> anyhow::Result<PathBuf> {
        let argv = vec![format!("{}/deck-publish", self.bin_dir), guest_path.into()];
        let (cmd, files) = self.run(argv, 60).await?;
        if cmd.state != "succeeded" {
            bail!("cannot send the file: {}", cmd.stderr.trim());
        }
        files.into_iter().next().context("the guest sent no file")
    }

    async fn relay(&self, req: Value, secs: u64) -> anyhow::Result<Value> {
        let argv = vec![format!("{}/bare-call", self.bin_dir), req.to_string()];
        let (cmd, files) = self.run(argv, secs).await?;
        let reply_path = files
            .iter()
            .find(|p| p.file_name().is_some_and(|n| n == "reply.json"))
            .ok_or_else(|| {
                anyhow!(
                    "the relay gave no reply ({}): {}",
                    cmd.state,
                    cmd.stderr.trim()
                )
            })?;
        let reply: Value = serde_json::from_slice(&std::fs::read(reply_path)?)?;
        if reply["ok"] != true {
            bail!("{}", reply["error"].as_str().unwrap_or("relay failed"));
        }
        let mut result = reply["result"].clone();
        // Guest paths become host artifact paths. The guest names its output folder in the reply.
        let dir = reply_path.parent().context("no artifact folder")?;
        let mut swaps: Vec<(String, String)> = vec![];
        if let Some(out) = reply["out"].as_str() {
            for f in &files {
                if let Some(n) = f.file_name().and_then(|n| n.to_str()) {
                    swaps.push((format!("{out}/{n}"), f.display().to_string()));
                }
            }
        }
        for (guest, name) in reply["files"].as_object().into_iter().flatten() {
            if let Some(n) = name.as_str() {
                swaps.push((guest.clone(), dir.join(n).display().to_string()));
            }
        }
        if let Some(t) = result["content"][0]["text"].as_str() {
            let mut t = t.to_owned();
            for (g, h) in &swaps {
                t = t.replace(g.as_str(), h);
            }
            result["content"][0]["text"] = Value::String(t);
        }
        Ok(result)
    }

    /// Ask the guest to stop each live command of a task. A command counts as stopped only
    /// when the guest confirms the end. Otherwise it is listed, and it stays in the inbox.
    pub async fn stop_task(&self, task: &str) -> anyhow::Result<StopReport> {
        let mut report = StopReport::default();
        let rows: Vec<Cmd> = self
            .store
            .commands(self.bot.clone())
            .await?
            .into_iter()
            .filter(|c| c.task_id == task && matches!(c.state.as_str(), "sent" | "running"))
            .collect();
        let Ok(s) = self.session() else {
            report.unconfirmed = rows.into_iter().map(|c| c.command_id).collect();
            return Ok(report);
        };
        for c in rows {
            let id = c.command_id;
            let done = match s.cancel(&id).await {
                Ok(_) => s.wait(&id, Duration::from_secs(15)).await.ok(),
                Err(_) => None,
            };
            match done {
                Some(c) if c.is_final() => report.confirmed.push(id),
                _ => report.unconfirmed.push(id),
            }
        }
        Ok(report)
    }
}

/// The tools of the guest bare, as the agent sees them.
pub struct GuestHost(pub Arc<Hub>);

impl BrowserHost for GuestHost {
    fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
        Box::pin(async move {
            let req = json!({"method":"tools/call","params":{"name":name,"arguments":args},"timeout":150});
            let r = self.0.relay(req, BARE_DEADLINE_SECS).await?;
            crate::bare::tool_value(&r)
        })
    }

    fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
        Box::pin(async move {
            let r = self
                .0
                .relay(
                    json!({"method":"tools/list","timeout":30}),
                    LIST_DEADLINE_SECS,
                )
                .await?;
            Ok(r["tools"].as_array().cloned().unwrap_or_default())
        })
    }

    fn out_dirs(&self) -> Vec<PathBuf> {
        vec![self.0.artifacts_root()]
    }
}

#[cfg(test)]
pub mod tests {
    use std::io::{BufRead, BufReader};
    use std::path::Path;

    use super::*;
    use crate::bot::vm::FakeVm;
    use crate::testutil::temp_dir;

    /// The real Python executor on a unix socket, with a fake bare-mcp session behind `bare-call`.
    pub struct Guest {
        pub child: std::process::Child,
        pub fake_bare: std::process::Child,
        pub hub: Arc<Hub>,
        pub vm: Arc<FakeVm>,
        pub store: Store,
        pub dir: PathBuf,
        _fifo: std::fs::File,
    }

    impl Drop for Guest {
        fn drop(&mut self) {
            for c in [&mut self.child, &mut self.fake_bare] {
                let _ = c.kill();
                let _ = c.wait();
            }
        }
    }

    const FAKE_BARE: &str = r#"
import json, sys, os
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "tools/list":
        r = {"tools": [{"name": "open", "inputSchema": {"type": "object", "properties": {}}}, {"name": "screenshot", "inputSchema": {"type": "object", "properties": {}}}]}
    elif m["method"] == "tools/call":
        a = m["params"]["arguments"]; n = m["params"]["name"]
        rep = {"ok": True}
        if n == "screenshot":
            open(a["path"], "wb").write(b"\x89PNG-guest"); rep["path"] = a["path"]
        r = {"content": [{"type": "text", "text": json.dumps(rep)}]}
    else:
        r = {}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
"#;

    pub fn spawn_guest() -> Guest {
        let dir = temp_dir();
        let (sock, state, ws, rt, data) = (
            dir.join("g.sock"),
            dir.join("state"),
            dir.join("ws"),
            dir.join("rt"),
            dir.join("data"),
        );
        std::fs::create_dir_all(rt.join("bare")).unwrap();
        std::fs::create_dir_all(&state).unwrap();
        std::fs::write(
            state.join("identity.json"),
            json!({"bot_id":"main","guest_id":"guest-py","max_epoch":0}).to_string(),
        )
        .unwrap();
        let fifo = rt.join("bare/in");
        assert!(
            std::process::Command::new("mkfifo")
                .arg(&fifo)
                .status()
                .unwrap()
                .success()
        );
        let keep = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&fifo)
            .unwrap();
        std::fs::write(dir.join("fake_bare.py"), FAKE_BARE).unwrap();
        let fake_bare = std::process::Command::new("python3")
            .arg(dir.join("fake_bare.py"))
            .stdin(keep.try_clone().unwrap())
            .stdout(std::fs::File::create(rt.join("bare/out")).unwrap())
            .spawn()
            .unwrap();
        let scripts = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/vm-prototype/guest");
        let mut child = std::process::Command::new("python3")
            .args(["-I".as_ref(), scripts.join("deck_executor.py").as_os_str()])
            .args(["--listen-unix".as_ref(), sock.as_os_str()])
            .args(["--state".as_ref(), state.as_os_str()])
            .args(["--workspace".as_ref(), ws.as_os_str()])
            .args(["--runtime-dir".as_ref(), rt.as_os_str()])
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        assert_eq!(line.trim(), "ready");
        let store = Store::open(&data).unwrap();
        let vm = FakeVm::new(true, false);
        *vm.endpoint.lock().unwrap() = Some(sock);
        let hub = Hub::new(store.clone(), "main", data, vm.clone(), 20)
            .with_bin_dir(scripts.to_str().unwrap());
        Guest {
            child,
            fake_bare,
            hub,
            vm,
            store,
            dir,
            _fifo: keep,
        }
    }

    #[tokio::test]
    async fn shell_runs_in_the_guest_and_returns_files_as_host_artifacts() {
        let g = spawn_guest();
        g.hub.set_task("t1");
        g.hub
            .connect(None)
            .await
            .unwrap_or_else(|e| panic!("{e:#}"));
        let out = g
            .hub
            .shell(
                "echo hi; echo data > $DECK_OUT/r.txt; echo $DECK_WORKSPACE > $DECK_OUT/ws.txt",
                None,
            )
            .await
            .unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(
            (v["ok"].as_bool(), v["stdout"].as_str()),
            (Some(true), Some("hi\n"))
        );
        let files: Vec<String> = serde_json::from_value(v["files"].clone()).unwrap();
        assert!(
            files
                .iter()
                .all(|p| p.starts_with(g.hub.artifacts_root().to_str().unwrap()))
        );
        let r = files.iter().find(|p| p.ends_with("r.txt")).unwrap();
        assert_eq!(std::fs::read_to_string(r).unwrap(), "data\n");
        // The journal knows the task of the command.
        let rows = g.store.commands("main".into()).await.unwrap();
        assert_eq!((rows.len(), rows[0].task_id.as_str()), (1, "t1"));
    }

    #[tokio::test]
    async fn bare_tools_and_results_go_through_the_relay_with_host_paths() {
        let g = spawn_guest();
        g.hub.set_task("t1");
        g.hub.connect(None).await.unwrap();
        let host = GuestHost(g.hub.clone());
        let tools = host.tools().await.unwrap();
        assert_eq!(
            tools
                .iter()
                .map(|t| t["name"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["open", "screenshot"]
        );
        let r = host
            .call("screenshot", json!({"path": "/etc/passwd"}))
            .await
            .unwrap();
        let path = PathBuf::from(r["path"].as_str().unwrap());
        assert!(path.starts_with(g.hub.artifacts_root()), "{path:?}");
        assert_eq!(std::fs::read(&path).unwrap(), b"\x89PNG-guest");
        assert!(host.out_dirs().iter().any(|d| path.starts_with(d)));
    }

    #[tokio::test]
    async fn publish_refuses_paths_outside_the_guest_workspace() {
        let g = spawn_guest();
        g.hub.set_task("t1");
        g.hub.connect(None).await.unwrap();
        g.hub
            .shell("echo report > ../report.txt", None)
            .await
            .unwrap();
        let ok = g.hub.publish("../report.txt").await.unwrap();
        assert_eq!(std::fs::read_to_string(ok).unwrap(), "report\n");
        let e = g.hub.publish("/etc/passwd").await.unwrap_err().to_string();
        assert!(e.contains("cannot send"), "{e}");
    }

    #[tokio::test]
    async fn a_command_needs_a_task_and_a_connection() {
        let g = spawn_guest();
        assert!(
            g.hub
                .shell("true", None)
                .await
                .unwrap_err()
                .to_string()
                .contains("no task")
        );
        g.hub.set_task("t1");
        assert!(
            g.hub
                .shell("true", None)
                .await
                .unwrap_err()
                .to_string()
                .contains("not connected")
        );
    }

    #[tokio::test]
    async fn stop_confirms_a_setsid_descendant_is_gone_or_lists_the_command() {
        let g = spawn_guest();
        g.hub.set_task("t1");
        g.hub.connect(None).await.unwrap();
        let pidfile = g.dir.join("esc.pid");
        let h = g.hub.clone();
        let script = format!("setsid sleep 300 & echo $! > {}; wait", pidfile.display());
        let run = tokio::spawn(async move { h.shell(&script, Some(120)).await });
        let pid: i32 = loop {
            if let Ok(t) = std::fs::read_to_string(&pidfile)
                && let Ok(p) = t.trim().parse()
            {
                break p;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        };
        let rep = tokio::time::timeout(Duration::from_secs(30), g.hub.stop_task("t1"))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            (rep.confirmed.len(), rep.unconfirmed.len()),
            (1, 0),
            "{rep:?}"
        );
        let out: Value = serde_json::from_str(&run.await.unwrap().unwrap()).unwrap();
        assert_eq!(out["state"], "cancelled");
        // Needs a delegated cgroup, as the guest service has. Without one the escapee survives.
        let gone = std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .map(|s| s.rsplit(')').next().unwrap().trim_start().starts_with('Z'))
            .unwrap_or(true);
        assert!(gone, "the setsid descendant {pid} survived the stop");
        // Stop with a lost connection lists the live commands instead of claiming a stop.
        g.hub.drop_session();
        assert_eq!(g.hub.stop_task("t1").await.unwrap(), StopReport::default());
    }
}
