//! The host side of one connection to a guest executor.
//!
//! Each connection gets a new epoch from the journal. The guest rejects any command with another
//! epoch, and the host drops any reply with another epoch. A restored or reconnected guest
//! therefore cannot be confused with an older connection.

use std::collections::HashMap;
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, anyhow, bail};
use serde_json::{Value, json};
use tokio::net::UnixStream;
use tokio::net::unix::OwnedWriteHalf;
use tokio::sync::{Notify, oneshot};
use tokio::time::{Instant, sleep, timeout};

use super::journal::Cmd;
use super::proto::*;
use crate::store::Store;

#[derive(Debug, Clone)]
pub struct Options {
    pub bot: String,
    pub endpoint: PathBuf,
    /// Root of the host data. Artifacts go to `bots/<bot>/artifacts/<command>/`.
    pub data_dir: PathBuf,
    /// The process that must own the other end of the socket, when known.
    pub peer_pid: Option<u32>,
    /// A guest ID given by provisioning. When `None`, the first one seen is pinned.
    pub expected_guest: Option<String>,
    pub request_timeout: Duration,
}

impl Options {
    pub fn new(bot: &str, endpoint: &Path, data_dir: &Path) -> Self {
        Self {
            bot: bot.into(),
            endpoint: endpoint.into(),
            data_dir: data_dir.into(),
            peer_pid: None,
            expected_guest: None,
            request_timeout: Duration::from_secs(10),
        }
    }
}

type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>>;

pub struct Session {
    opts: Options,
    store: Store,
    pub epoch: u64,
    pub guest_id: String,
    /// The highest epoch the guest had seen before this connection.
    pub max_epoch_before: u64,
    w: tokio::sync::Mutex<OwnedWriteHalf>,
    pending: Pending,
    seq: AtomicU64,
    reader: tokio::task::JoinHandle<()>,
    /// Woken when a result changes a journal row, so waiters need no fast poll.
    changed: Arc<Notify>,
}

impl Drop for Session {
    fn drop(&mut self) {
        self.reader.abort();
    }
}

/// The socket must be a socket that this user owns and nobody else can write to.
pub fn check_endpoint(path: &Path) -> anyhow::Result<()> {
    let m = std::fs::symlink_metadata(path)
        .with_context(|| format!("endpoint {} is missing", path.display()))?;
    if !m.file_type().is_socket() {
        bail!("endpoint is not a unix socket");
    }
    // SAFETY: geteuid has no preconditions.
    if m.uid() != unsafe { libc::geteuid() } {
        bail!("endpoint is owned by another user");
    }
    if m.mode() & 0o022 != 0 {
        bail!("endpoint is writable by group or others");
    }
    Ok(())
}

fn guest_error(v: &Value) -> anyhow::Error {
    anyhow!(
        "guest error {}: {}",
        v["code"].as_str().unwrap_or("?"),
        v["message"].as_str().unwrap_or("")
    )
}

impl Session {
    pub async fn connect(store: Store, opts: Options) -> anyhow::Result<Self> {
        if !valid_id(&opts.bot) {
            bail!("bad bot ID");
        }
        check_endpoint(&opts.endpoint)?;
        // The epoch is committed before the connection exists, so a crash cannot reuse it.
        let epoch = store.next_epoch(opts.bot.clone()).await?;
        let mut stream = timeout(opts.request_timeout, UnixStream::connect(&opts.endpoint))
            .await
            .context("connect timed out")?
            .context("cannot connect")?;
        let cred = stream.peer_cred()?;
        // SAFETY: geteuid has no preconditions.
        if cred.uid() != unsafe { libc::geteuid() } {
            bail!("the peer runs as another user");
        }
        if let Some(pid) = opts.peer_pid
            && cred.pid() != Some(pid as i32)
        {
            bail!("the peer is not the expected process");
        }
        let nonce = uuid::Uuid::new_v4().to_string();
        timeout(opts.request_timeout, write_frame(
            &mut stream,
            &json!({"type":"hello","id":"hello","proto":PROTO,"bot_id":opts.bot,"epoch":epoch,"nonce":nonce}),
        ))
        .await.context("hello write timed out")??;
        // Bytes left from an earlier connection may come first. Skip frames until our answer.
        let ack = timeout(opts.request_timeout, async {
            loop {
                let v = read_frame(&mut stream).await?;
                match v["type"].as_str() {
                    Some("hello_ack") if v["nonce"] == nonce => return Ok(v),
                    Some("error") if v["id"] == "hello" => return Err(guest_error(&v)),
                    _ => {}
                }
            }
        })
        .await
        .context("no hello answer")??;
        if ack["bot_id"] != opts.bot.as_str() || ack["epoch"] != epoch {
            bail!("the guest answered for another bot or epoch");
        }
        let guest_id = ack["guest_id"].as_str().context("no guest_id")?.to_string();
        if let Some(want) = &opts.expected_guest
            && *want != guest_id
        {
            bail!("the guest identity is not the expected one");
        }
        store.pin_guest(opts.bot.clone(), guest_id.clone()).await?;
        let (mut r, w) = stream.into_split();
        let pending: Pending = Arc::default();
        let changed = Arc::new(Notify::new());
        let reader = tokio::spawn({
            let (pending, store, bot, changed) = (
                pending.clone(),
                store.clone(),
                opts.bot.clone(),
                changed.clone(),
            );
            async move {
                while let Ok(v) = read_frame(&mut r).await {
                    if v["epoch"].as_u64() != Some(epoch) {
                        let _ = store
                            .log(
                                bot.clone(),
                                v["command_id"].as_str().map(String::from),
                                Some(epoch),
                                "stale_rejected",
                                format!("frame epoch {} on connection epoch {epoch}", v["epoch"]),
                            )
                            .await;
                        continue;
                    }
                    if v["type"] == "result" {
                        match serde_json::from_value::<Receipt>(v["receipt"].clone()) {
                            Ok(rc) => {
                                let _ = store.apply_receipt(bot.clone(), epoch, rc).await;
                                changed.notify_waiters();
                            }
                            Err(e) => {
                                let _ = store
                                    .log(
                                        bot.clone(),
                                        None,
                                        Some(epoch),
                                        "bad_result",
                                        e.to_string(),
                                    )
                                    .await;
                            }
                        }
                    } else if let Some(id) = v["id"].as_str() {
                        let tx = pending.lock().unwrap().remove(id);
                        if let Some(tx) = tx {
                            let _ = tx.send(v);
                        }
                    }
                }
                pending.lock().unwrap().clear(); // wakes every waiter with "connection lost"
                changed.notify_waiters();
            }
        });
        Ok(Self {
            opts,
            store,
            epoch,
            guest_id,
            max_epoch_before: ack["max_epoch_before"].as_u64().unwrap_or(0),
            w: tokio::sync::Mutex::new(w),
            pending,
            seq: AtomicU64::new(0),
            reader,
            changed,
        })
    }

    /// False once the connection is gone. The server then connects again with a new epoch.
    pub fn is_alive(&self) -> bool {
        !self.reader.is_finished()
    }

    /// Send one request and wait for the reply with the same ID.
    async fn request(&self, mut msg: Value) -> anyhow::Result<Value> {
        let id = format!("r{}", self.seq.fetch_add(1, Ordering::Relaxed));
        msg["id"] = id.clone().into();
        msg["epoch"] = self.epoch.into();
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id.clone(), tx);
        let sent = {
            timeout(self.opts.request_timeout, async {
                let mut w = self.w.lock().await;
                write_frame(&mut *w, &msg).await
            })
            .await
            .unwrap_or_else(|_| Err(anyhow!("request write timed out")))
        };
        if let Err(e) = sent {
            self.pending.lock().unwrap().remove(&id);
            return Err(e.context("connection lost while sending"));
        }
        match timeout(self.opts.request_timeout, rx).await {
            Ok(Ok(v)) => Ok(v),
            Ok(Err(_)) => bail!("connection lost before the reply"),
            Err(_) => {
                self.pending.lock().unwrap().remove(&id);
                bail!("no reply within {:?}", self.opts.request_timeout)
            }
        }
    }

    /// Dispatch a command, or report what is known. Never sends a command twice for one ID.
    pub async fn exec(
        &self,
        task: &str,
        id: &str,
        argv: Vec<String>,
        deadline_ms: u64,
    ) -> anyhow::Result<Cmd> {
        let bot = &self.opts.bot;
        if !valid_id(task) || !valid_id(id) {
            bail!("bad task or command ID");
        }
        if argv.is_empty()
            || argv.len() > MAX_ARGV
            || argv[0].is_empty()
            || argv
                .iter()
                .any(|a| a.len() > MAX_ARG_BYTES || a.contains('\0'))
        {
            bail!("bad argv");
        }
        if !(1..=MAX_DEADLINE_MS).contains(&deadline_ms) {
            bail!("deadline out of range");
        }
        let digest = exec_digest(bot, task, id, deadline_ms, &argv);
        let cmd = self
            .store
            .begin(
                bot.clone(),
                task.into(),
                id.into(),
                argv.clone(),
                deadline_ms,
                digest.clone(),
            )
            .await?;
        if cmd.bot_id != *bot || cmd.digest != digest {
            let _ = self
                .store
                .log(
                    bot.clone(),
                    Some(id.into()),
                    Some(self.epoch),
                    "payload_conflict",
                    "rejected before send".into(),
                )
                .await;
            bail!("payload conflict: command ID {id} was used with different arguments");
        }
        match cmd.state.as_str() {
            "intent" | "not_delivered" => {}
            "sent" | "running" | "unknown" => {
                self.reconcile_one(&cmd).await?;
                return self.get(id).await;
            }
            _ => return Ok(cmd),
        }
        if !self.store.mark_sent(id.into(), self.epoch).await? {
            return self.get(id).await;
        }
        let reply = self
            .request(json!({"type":"exec","bot_id":bot,"task_id":task,"command_id":id,"argv":argv,"deadline_ms":deadline_ms,"digest":digest}))
            .await;
        match reply {
            Ok(v) if v["type"] == "exec_ack" => {
                if let Ok(rc) = serde_json::from_value::<Receipt>(v["receipt"].clone()) {
                    self.store
                        .apply_receipt(bot.clone(), self.epoch, rc)
                        .await?;
                }
            }
            Ok(v) if v["type"] == "error" => {
                // The guest refused. Nothing ran.
                let code = v["code"].as_str().unwrap_or("?").to_string();
                self.store
                    .settle(
                        id.into(),
                        "not_delivered",
                        format!("guest refused: {code}"),
                        self.epoch,
                    )
                    .await?;
                return Err(guest_error(&v));
            }
            Ok(_) => bail!("unexpected reply to exec"),
            Err(e) => {
                // The frame may or may not have arrived. The row stays `sent`.
                let _ = self
                    .store
                    .log(
                        bot.clone(),
                        Some(id.into()),
                        Some(self.epoch),
                        "no_ack",
                        e.to_string(),
                    )
                    .await;
            }
        }
        self.get(id).await
    }

    async fn get(&self, id: &str) -> anyhow::Result<Cmd> {
        let cmd = self
            .store
            .command(id.into())
            .await?
            .context("no such command")?;
        if cmd.bot_id != self.opts.bot {
            bail!("command belongs to another bot");
        }
        Ok(cmd)
    }

    /// Poll the journal until the row is settled or the time is up.
    pub async fn wait(&self, id: &str, limit: Duration) -> anyhow::Result<Cmd> {
        let end = Instant::now() + limit;
        loop {
            // Arm the wake-up before the read, so a result between them is not lost.
            let changed = self.changed.notified();
            tokio::pin!(changed);
            changed.as_mut().enable();
            let c = self.get(id).await?;
            if c.is_final()
                || matches!(c.state.as_str(), "unknown" | "not_delivered")
                || Instant::now() >= end
                || !self.is_alive()
            {
                return Ok(c);
            }
            let left = end.saturating_duration_since(Instant::now());
            tokio::select! {
                _ = &mut changed => {}
                _ = sleep(left.min(Duration::from_secs(2))) => {}
            }
        }
    }

    /// Ask the guest to stop. The answer says only that the request was accepted.
    /// Termination is confirmed later by a `cancelled` result.
    pub async fn cancel(&self, id: &str) -> anyhow::Result<(String, Cmd)> {
        let cmd = self.get(id).await?;
        if cmd.is_final() || matches!(cmd.state.as_str(), "intent" | "not_delivered") {
            return Ok(("nothing_to_cancel".into(), cmd));
        }
        self.store.set_cancel(id.into(), "requested").await?;
        let v = self
            .request(json!({"type":"cancel","command_id":id}))
            .await?;
        if v["type"] == "error" {
            return Err(guest_error(&v));
        }
        let ack = v["ack"].as_str().unwrap_or("?").to_string();
        match ack.as_str() {
            "accepted" => self.store.set_cancel(id.into(), "accepted").await?,
            _ => self.reconcile_one(&cmd).await?,
        }
        Ok((ack, self.get(id).await?))
    }

    /// Compare one journal row with the guest receipt.
    async fn reconcile_one(&self, cmd: &Cmd) -> anyhow::Result<()> {
        let v = self
            .request(json!({"type":"status","command_id":cmd.command_id}))
            .await?;
        if v["type"] == "error" {
            return Err(guest_error(&v));
        }
        if v["found"] == true {
            let rc: Receipt = serde_json::from_value(v["receipt"].clone())?;
            self.store
                .apply_receipt(cmd.bot_id.clone(), self.epoch, rc)
                .await?;
            return Ok(());
        }
        let sent_at = cmd.dispatch_epoch.unwrap_or(u64::MAX);
        match cmd.state.as_str() {
            // Same connection: the stream is ordered, so the guest read the exec before this status.
            // An absent receipt then means the exec frame never arrived. Leave the row as it is.
            "sent" if sent_at == self.epoch => {}
            // A missing receipt cannot rule out rollback after hello but before receipt persistence.
            // Fence stale dispatches, but retain an unknown outcome rather than permit replay.
            "sent" | "running" => {
                self.store
                    .settle(
                        cmd.command_id.clone(),
                        "unknown",
                        "guest_state_lost".into(),
                        self.epoch,
                    )
                    .await?;
            }
            _ => {}
        }
        Ok(())
    }

    /// Startup reconciliation. Resolves rows that were in flight; never sends a command.
    pub async fn reconcile(&self) -> anyhow::Result<Vec<Cmd>> {
        let mut out = vec![];
        for c in self.store.commands(self.opts.bot.clone()).await? {
            if matches!(c.state.as_str(), "sent" | "running" | "unknown") {
                self.reconcile_one(&c).await?;
                out.push(self.get(&c.command_id).await?);
            }
        }
        Ok(out)
    }

    /// Copy the artifacts of a command into the host store, with size and checksum checks.
    pub async fn fetch_artifacts(&self, id: &str) -> anyhow::Result<Vec<PathBuf>> {
        let cmd = self.get(id).await?;
        let bot = &self.opts.bot;
        let dir = self
            .opts
            .data_dir
            .join("bots")
            .join(bot)
            .join("artifacts")
            .join(id);
        let mut out = vec![];
        let stored = self.store.stored_artifacts(id.into()).await?;
        for a in &cmd.artifacts {
            if !valid_artifact_name(&a.name) || a.size > MAX_ARTIFACT_BYTES || a.sha256.len() != 64
            {
                bail!("the guest declared an unacceptable artifact: {}", a.handle);
            }
            if let Some((_, p)) = stored.iter().find(|(h, _)| *h == a.handle) {
                out.push(PathBuf::from(p));
                continue;
            }
            private_dir(&self.opts.data_dir, &dir)?;
            let fin = dir.join(&a.name);
            let part = dir.join(format!("{}.part", a.name));
            let mut f = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true) // O_EXCL: never follows a link planted at this name
                .mode(0o600)
                .open(&part)
                .with_context(|| format!("cannot create {}", part.display()))?;
            let res = self.pull(id, a, &mut f).await;
            drop(f);
            if let Err(e) = res {
                let _ = std::fs::remove_file(&part);
                return Err(e);
            }
            if fin.exists() {
                let _ = std::fs::remove_file(&part);
                bail!("{} already exists in the bot store", fin.display());
            }
            std::fs::rename(&part, &fin)?;
            self.store
                .record_artifact(id.into(), a.clone(), fin.display().to_string())
                .await?;
            out.push(fin);
        }
        Ok(out)
    }

    async fn pull(&self, id: &str, a: &GuestArtifact, f: &mut std::fs::File) -> anyhow::Result<()> {
        use std::io::Write;
        let mut hash = Sha256::new();
        let mut got = 0u64;
        loop {
            let want = (a.size - got).clamp(1, CHUNK as u64);
            let v = self
                .request(json!({"type":"artifact_get","command_id":id,"handle":a.handle,"offset":got,"length":want}))
                .await?;
            if v["type"] == "error" {
                return Err(guest_error(&v));
            }
            if v["type"] != "artifact_chunk"
                || v["command_id"] != id
                || v["handle"] != a.handle.as_str()
                || v["offset"] != got
            {
                bail!("the guest answered for another artifact or offset");
            }
            let data = base64_decode(v["data"].as_str().unwrap_or(""))?;
            if data.len() as u64 > want || got + data.len() as u64 > a.size {
                bail!("the guest sent more bytes than declared");
            }
            f.write_all(&data)?;
            hash.update(&data);
            got += data.len() as u64;
            if got == a.size {
                break;
            }
            if data.is_empty() || v["eof"] == true {
                bail!("the artifact ended after {got} of {} bytes", a.size);
            }
        }
        f.flush()?;
        if hash.hex() != a.sha256 {
            bail!("checksum mismatch for {}", a.name);
        }
        Ok(())
    }
}

/// Create `dir` below `root`, refusing links and directories that another user owns.
/// `root` must exist. Each new folder is owner-only.
pub(crate) fn private_dir(root: &Path, dir: &Path) -> anyhow::Result<()> {
    let rel = dir
        .strip_prefix(root)
        .context("artifact path escapes the data directory")?;
    let mut cur = root.to_path_buf();
    for part in rel.components() {
        cur.push(part);
        match std::fs::symlink_metadata(&cur) {
            Ok(m) if m.is_dir() => {}
            Ok(_) => bail!("{} is not a plain directory", cur.display()),
            Err(_) => std::fs::DirBuilder::new().mode(0o700).create(&cur)?,
        }
        let m = std::fs::symlink_metadata(&cur)?;
        // SAFETY: geteuid has no preconditions.
        if !m.is_dir() || m.uid() != unsafe { libc::geteuid() } {
            bail!("{} is not a directory owned by this user", cur.display());
        }
    }
    Ok(())
}
