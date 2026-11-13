//! Protected secret delivery.
//!
//! A value lives only in the memory of one request. It goes to the guest on stdin of the harness
//! command and nowhere else. The host keeps metadata: which request got which name, and when.
//! A guest receipt is the source of truth after a crash.
//!
//! Reference. Verified by reading the installed T3 Code client and server bundles (2026-11-13,
//! version 0.1.10-nightly.20261113.4616). No license for T3's own code ships in the bundle, so
//! nothing here is copied. Each mechanism below is an independent reimplementation.
//! - A request is a durable card with states pending, saved, declined and cancelled. An answer is
//!   accepted once, and only while the run that asked is alive.
//! - The server stores the value before it records the card. If recording fails, it removes the
//!   value. This module orders the same way: guest first, then metadata, and a crash between them
//!   is read back from the guest receipt.
//! - T3 keeps a value for 24 hours, binds it to a project, and exposes only an opaque HMAC
//!   reference to the agent. Its file mode writes a private directory (0700) and file (0600) by
//!   temporary file and rename. Here the guest command owns file modes; the host never writes a value.
//!
//! Not verified: T3's redaction of tool arguments, and its screenshot handling. Deck's gate for
//! those is its own design (`privacy_gates`).
//!
//! Proposal, not reference: names map to `DECK_` plus a label hash; the privacy gate stays open
//! until the model calls `signin_verified`; guest commands can read the value file (as in T3's
//! file delivery), so a shell command that prints it would put it in a command journal.

use std::process::Stdio;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{OptionalExtension, Transaction, TransactionBehavior, params};
use serde::Deserialize;
use serde_json::json;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use crate::config::Vm;
use crate::executor::proto::Sha256;
use crate::store::{Store, now};

/// Longest value. The guest stores `NAME=value` on one line.
pub const MAX_VALUE: usize = 4096;
/// Seconds an active-task request stays open.
pub const REQUEST_TTL_SECS: u64 = 3600;
const GUEST_LIMIT: Duration = Duration::from_secs(30);

/// Serializes every delivery in this process, so two posts cannot both deliver one request.
pub static DELIVERY: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

pub fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

/// First 16 uppercase hex characters of the SHA-256 of the display label.
pub fn label_hash(label: &str) -> String {
    let mut h = Sha256::new();
    h.update(label.as_bytes());
    h.hex()[..16].to_ascii_uppercase()
}

/// The environment name on the guest. A valid uppercase name stays. Any other label maps to a
/// stable `DECK_` name, so a display label never reaches a shell or a file as written.
pub fn canonical_name(label: &str) -> String {
    let ok = !label.is_empty()
        && label.len() <= 64
        && !label.as_bytes()[0].is_ascii_digit()
        && label
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_');
    if ok {
        label.to_owned()
    } else {
        format!("DECK_{}", label_hash(label))
    }
}

/// A protected value. It has no `Debug` output and wipes its buffer when dropped (best effort).
#[derive(Deserialize)]
#[serde(transparent)]
pub struct Secret(String);

impl std::fmt::Debug for Secret {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Secret([redacted])")
    }
}

impl Drop for Secret {
    fn drop(&mut self) {
        let mut b = std::mem::take(&mut self.0).into_bytes();
        b.fill(0);
        std::hint::black_box(&b);
    }
}

impl Secret {
    /// Rejects what `NAME=value` storage cannot hold. Never truncates.
    pub fn check(&self) -> Result<(), &'static str> {
        if self.0.is_empty() || self.0.len() > MAX_VALUE {
            return Err("The value must be 1 to 4096 bytes");
        }
        if self.0.bytes().any(|b| matches!(b, 0 | b'\r' | b'\n')) {
            return Err("The value cannot contain a null byte or a line break");
        }
        Ok(())
    }
}

/// The metadata the guest returns. Missing fields read as an absent receipt.
#[derive(Debug, Default, Deserialize)]
struct Reply {
    #[serde(default)]
    ok: bool,
    #[serde(default)]
    bot_id: String,
    #[serde(default)]
    request_id: String,
    #[serde(default)]
    label: String,
}

/// A failure with a fixed text. It never carries output of the guest command or the value.
#[derive(Debug, Clone, Copy, PartialEq, thiserror::Error)]
pub enum Failure {
    #[error("The guest secret command could not run")]
    Unavailable,
    #[error("The guest secret command timed out")]
    Timeout,
    #[error("The guest secret command failed")]
    Failed,
    #[error("The guest receipt does not match this request")]
    Mismatch,
    #[error("The guest no longer holds this value. Start a new request.")]
    NotDelivered,
}

/// The harness commands `deliver-secret` and `secret-receipt` of `vmproto.py`.
pub struct HarnessChannel {
    python: String,
    script: std::path::PathBuf,
    dir: std::path::PathBuf,
    fixture_port: u16,
}

impl HarnessChannel {
    pub fn new(vm: &Vm) -> Self {
        Self {
            python: vm.python.clone(),
            script: vm.harness.clone(),
            dir: vm.runtime_dir.clone(),
            fixture_port: vm.fixture_port,
        }
    }

    /// Run one harness command. Returns its exit code and its stdout.
    async fn run_raw(
        &self,
        sub: &[&str],
        input: Option<Vec<u8>>,
    ) -> Result<(Option<i32>, Vec<u8>), Failure> {
        let mut cmd = Command::new(&self.python);
        cmd.arg("-I")
            .arg(&self.script)
            .arg("--dir")
            .arg(&self.dir)
            .arg("--fixture-port")
            .arg(self.fixture_port.to_string())
            .args(sub)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            // The guest command's diagnostics could echo input. They are never read.
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let mut child = cmd.spawn().map_err(|_| Failure::Unavailable)?;
        let mut stdin = child.stdin.take().ok_or(Failure::Unavailable)?;
        if let Some(mut bytes) = input {
            // A closed pipe shows in the exit status.
            let _ = stdin.write_all(&bytes).await;
            bytes.fill(0);
            std::hint::black_box(&bytes);
        }
        drop(stdin);
        let out = tokio::time::timeout(GUEST_LIMIT, child.wait_with_output())
            .await
            .map_err(|_| Failure::Timeout)?
            .map_err(|_| Failure::Failed)?;
        if out.stdout.len() > 4096 {
            return Err(Failure::Failed);
        }
        Ok((out.status.code(), out.stdout))
    }

    /// Run one harness command that must exit 0.
    async fn run(&self, sub: &[&str], input: Option<Vec<u8>>) -> Result<Vec<u8>, Failure> {
        match self.run_raw(sub, input).await? {
            (Some(0), out) => Ok(out),
            _ => Err(Failure::Failed),
        }
    }

    fn matches(r: &Reply, bot: &str, request: &str, name: &str) -> bool {
        r.ok && r.bot_id == bot && r.request_id == request && r.label == name
    }

    /// The receipt of the guest for this request, if it holds one.
    pub async fn receipt(&self, bot: &str, request: &str, name: &str) -> Result<bool, Failure> {
        let (code, out) = self
            .run_raw(
                &["secret-receipt", "--bot", bot, "--request-id", request],
                None,
            )
            .await?;
        // `secretchan.py` prints this exact line and exits 1 when the guest holds no receipt, or
        // when the value behind it changed. Nothing else counts as absence: a bot, request or
        // path error, a transport error and any other text stay failures.
        if code != Some(0) {
            let absent = code == Some(1)
                && serde_json::from_slice::<serde_json::Value>(&out)
                    .is_ok_and(|v| v == json!({"ok": false, "error": "not_delivered"}));
            return if absent {
                Ok(false)
            } else {
                Err(Failure::Failed)
            };
        }
        let r: Reply = serde_json::from_slice(&out).map_err(|_| Failure::Failed)?;
        if !r.ok {
            return Err(Failure::Failed);
        }
        Self::matches(&r, bot, request, name)
            .then_some(true)
            .ok_or(Failure::Mismatch)
    }

    pub async fn deliver(
        &self,
        bot: &str,
        request: &str,
        name: &str,
        value: &Secret,
    ) -> Result<(), Failure> {
        let input = serde_json::to_vec(&json!({"label": name, "value": value.0}))
            .map_err(|_| Failure::Failed)?;
        let out = self
            .run(
                &["deliver-secret", "--bot", bot, "--request-id", request],
                Some(input),
            )
            .await?;
        let r: Reply = serde_json::from_slice(&out).map_err(|_| Failure::Failed)?;
        Self::matches(&r, bot, request, name)
            .then_some(())
            .ok_or(Failure::Mismatch)
    }

    /// Whether the harness has the delivery command. The value is never involved.
    pub async fn supported(&self) -> bool {
        self.run(&["deliver-secret", "--help"], None).await.is_ok()
    }
}

/// Record a verified delivery. If the guest does not hold it yet, deliver it first. The host
/// receipt follows the guest receipt, so a crash between them leaves only a receipt that the
/// next attempt reads again from the guest.
pub async fn ensure_delivered(
    store: &Store,
    channel: &HarnessChannel,
    bot: &str,
    request: &str,
    label: &str,
    task_request: Option<&str>,
    value: &Secret,
) -> Result<(), Failure> {
    let name = canonical_name(label);
    let known = store
        .secret_receipt(bot, request)
        .await
        .map_err(|_| Failure::Failed)?;
    match known {
        // The host receipt alone is not proof. The guest value may have changed since.
        Some(n) if n == name => {
            return match channel.receipt(bot, request, &name).await? {
                true => Ok(()),
                false => Err(Failure::NotDelivered),
            };
        }
        Some(_) => return Err(Failure::Mismatch),
        None => {}
    }
    if !channel.receipt(bot, request, &name).await? {
        channel.deliver(bot, request, &name, value).await?;
    }
    store
        .record_secret(bot, request, label, &name, task_request)
        .await
        .map_err(|_| Failure::Failed)
}

#[derive(Debug, Clone, PartialEq)]
pub struct TaskRequest {
    pub task_id: String,
    pub label: String,
    pub expires_at: u64,
}

impl Store {
    /// The name that a request received. Metadata only.
    pub async fn secret_receipt(&self, bot: &str, request: &str) -> anyhow::Result<Option<String>> {
        let (bot, request) = (bot.to_owned(), request.to_owned());
        self.run(move |c| {
            c.query_row(
                "SELECT name FROM secret_receipts WHERE bot_id=?1 AND request_id=?2",
                [&bot, &request],
                |r| r.get(0),
            )
            .optional()
        })
        .await
    }

    /// One transaction: the receipt, the closed task request and the open privacy gate.
    pub async fn record_secret(
        &self,
        bot: &str,
        request: &str,
        label: &str,
        name: &str,
        task_request: Option<&str>,
    ) -> anyhow::Result<()> {
        let (bot, request, label, name) = (
            bot.to_owned(),
            request.to_owned(),
            label.to_owned(),
            name.to_owned(),
        );
        let task_request = task_request.map(str::to_owned);
        self.run(move |c| {
            let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
            t.execute("INSERT OR IGNORE INTO secret_receipts(bot_id,request_id,label,name,delivered_at) VALUES (?1,?2,?3,?4,?5)", params![bot, request, label, name, now()])?;
            if let Some(id) = task_request {
                t.execute("UPDATE secret_requests SET state='delivered' WHERE request_id=?1 AND bot_id=?2 AND state='pending'", params![id, bot])?;
            }
            t.execute("INSERT OR IGNORE INTO privacy_gates(bot_id,opened_at) VALUES (?1,?2)", params![bot, now()])?;
            t.execute("INSERT INTO privacy_generations(bot_id,generation) VALUES (?1,1) ON CONFLICT(bot_id) DO UPDATE SET generation=generation+1", [&bot])?;
            t.commit()
        })
        .await
    }

    /// Screenshots and desktop capture stay off while this is open.
    pub async fn privacy_gate_open(&self, bot: &str) -> anyhow::Result<bool> {
        let bot = bot.to_owned();
        self.run(move |c| gate(c, &bot)).await
    }

    /// Fails closed: a read error counts as open.
    #[cfg(test)]
    pub fn privacy_gate_open_sync(&self, bot: &str) -> bool {
        self.run_sync(|c| gate(c, bot)).unwrap_or(true)
    }

    /// The gate and its generation, read in one statement. The generation grows at each open and
    /// stays after the close. Fails closed: a read error answers open.
    pub fn privacy_state_sync(&self, bot: &str) -> (bool, u64) {
        self.run_sync(|c| {
            c.query_row(
                "SELECT EXISTS(SELECT 1 FROM privacy_gates WHERE bot_id=?1), \
                 COALESCE((SELECT generation FROM privacy_generations WHERE bot_id=?1),0)",
                [bot],
                |r| Ok((r.get::<_, bool>(0)?, r.get::<_, i64>(1)? as u64)),
            )
        })
        .unwrap_or((true, 0))
    }

    pub async fn close_privacy_gate(&self, bot: &str) -> anyhow::Result<()> {
        let bot = bot.to_owned();
        self.run(move |c| {
            c.execute("DELETE FROM privacy_gates WHERE bot_id=?1", [&bot])
                .map(drop)
        })
        .await
    }

    pub async fn bot_for_chat(&self, chat: i64) -> anyhow::Result<Option<String>> {
        self.run(move |c| {
            c.query_row(
                "SELECT bot_id FROM bot_state WHERE chat_id=?1",
                [chat],
                |r| r.get(0),
            )
            .optional()
        })
        .await
    }

    /// Open a request for an active task. It replaces the bot's older open request.
    pub async fn create_secret_request(
        &self,
        id: &str,
        bot: &str,
        task: &str,
        label: &str,
        purpose: &str,
        expires_at: u64,
    ) -> anyhow::Result<()> {
        let (id, bot, task, label, purpose) = (
            id.to_owned(),
            bot.to_owned(),
            task.to_owned(),
            label.to_owned(),
            purpose.to_owned(),
        );
        self.run(move |c| {
            let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
            t.execute("UPDATE secret_requests SET state='superseded' WHERE bot_id=?1 AND state='pending'", [&bot])?;
            t.execute("INSERT INTO secret_requests(request_id,bot_id,task_id,label,purpose,state,expires_at,created_at) VALUES (?1,?2,?3,?4,?5,'pending',?6,?7)", params![id, bot, task, label, purpose, expires_at as i64, now()])?;
            t.commit()
        })
        .await
    }

    pub async fn pending_secret_request(
        &self,
        bot: &str,
        id: &str,
    ) -> anyhow::Result<Option<TaskRequest>> {
        let (bot, id) = (bot.to_owned(), id.to_owned());
        self.run(move |c| {
            c.query_row("SELECT task_id,label,expires_at FROM secret_requests WHERE request_id=?1 AND bot_id=?2 AND state='pending'", [&id, &bot], |r| {
                Ok(TaskRequest { task_id: r.get(0)?, label: r.get(1)?, expires_at: r.get::<_, i64>(2)? as u64 })
            })
            .optional()
        })
        .await
    }
}

fn gate(c: &rusqlite::Connection, bot: &str) -> rusqlite::Result<bool> {
    c.query_row(
        "SELECT EXISTS(SELECT 1 FROM privacy_gates WHERE bot_id=?1)",
        [bot],
        |r| r.get(0),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_follow_the_frozen_mapping() {
        assert_eq!(canonical_name("SITE_PASS"), "SITE_PASS");
        assert_eq!(canonical_name("_X1"), "_X1");
        for odd in ["Site pass", "1ABC", "lower", "", "A=B", "A\nB"] {
            let n = canonical_name(odd);
            assert_eq!(n, format!("DECK_{}", label_hash(odd)), "{odd:?}");
            assert_eq!(n.len(), 5 + 16);
        }
        // The first 16 uppercase hex characters of SHA-256("abc").
        assert_eq!(label_hash("abc"), "BA7816BF8F01CFEA");
    }

    #[test]
    fn debug_never_shows_the_value_and_checks_reject_unsafe_bytes() {
        let s: Secret = serde_json::from_str("\"hunter2\"").unwrap();
        assert!(!format!("{s:?}").contains("hunter2"));
        assert!(s.check().is_ok());
        for bad in ["\"\"", "\"a\\nb\"", "\"a\\u0000b\""] {
            assert!(
                serde_json::from_str::<Secret>(bad)
                    .unwrap()
                    .check()
                    .is_err()
            );
        }
    }

    /// A stand-in for `vmproto.py` that replays what the real CLI prints and returns. Each
    /// subcommand reads `<dir>/<sub>.out` and `<dir>/<sub>.code`, and leaves `<dir>/<sub>.called`.
    fn fake_channel(replies: &[(&str, i32, &str)]) -> (HarnessChannel, std::path::PathBuf) {
        let dir = crate::testutil::temp_dir();
        let script = dir.join("fake.py");
        std::fs::write(
            &script,
            "import sys, pathlib\n\
             a = sys.argv[1:]\n\
             d = pathlib.Path(a[a.index('--dir') + 1])\n\
             sub = a[a.index('--fixture-port') + 2]\n\
             sys.stdin.read()\n\
             (d / (sub + '.called')).write_text('1')\n\
             sys.stdout.write((d / (sub + '.out')).read_text())\n\
             sys.exit(int((d / (sub + '.code')).read_text()))\n",
        )
        .unwrap();
        for (sub, code, out) in replies {
            std::fs::write(dir.join(format!("{sub}.out")), out).unwrap();
            std::fs::write(dir.join(format!("{sub}.code")), code.to_string()).unwrap();
        }
        let toml = format!(
            "{}\n[vm]\nversion = 1\nbot_id = \"b\"\nruntime_dir = {:?}\nharness = {:?}\n",
            include_str!("../../deck.example.toml"),
            dir,
            script
        );
        let config = crate::config::Config::parse(&toml).unwrap();
        (HarnessChannel::new(config.vm.as_ref().unwrap()), dir)
    }

    const ABSENT: &str = "{\"ok\": false, \"error\": \"not_delivered\"}\n";

    fn present(label: &str) -> String {
        json!({"ok": true, "bot_id": "b", "request_id": "r1", "label": label}).to_string()
    }

    #[tokio::test]
    async fn the_real_cli_absence_reads_as_absent_and_other_failures_stay_failures() {
        // The real `secret-receipt` prints this JSON line and exits 1 when it holds no receipt.
        let (c, _) = fake_channel(&[("secret-receipt", 1, ABSENT)]);
        assert_eq!(c.receipt("b", "r1", "N").await, Ok(false));
        for (code, out) in [
            (1, "{\"ok\": false, \"error\": \"bot_mismatch\"}"),
            (1, "{\"ok\": false, \"error\": \"request_mismatch\"}"),
            (1, "{\"ok\": false, \"error\": \"unsafe_path\"}"),
            (1, "{\"ok\": false, \"error\": \"transport_error\"}"),
            (1, "{\"ok\": false}"),
            (1, "{\"ok\": false, \"error\": \"not_delivered\", \"x\": 1}"),
            (1, "not json"),
            (1, ""),
            (2, ABSENT),
            // Absence needs the failing exit status of the real command.
            (0, ABSENT),
            (0, "{\"ok\": false}"),
        ] {
            let (c, _) = fake_channel(&[("secret-receipt", code, out)]);
            assert_eq!(
                c.receipt("b", "r1", "N").await,
                Err(Failure::Failed),
                "{code} {out}"
            );
        }
        let (c, _) = fake_channel(&[("secret-receipt", 0, &present("N"))]);
        assert_eq!(c.receipt("b", "r1", "N").await, Ok(true));
        assert_eq!(c.receipt("b", "r1", "OTHER").await, Err(Failure::Mismatch));
    }

    #[tokio::test]
    async fn a_first_delivery_works_with_the_real_cli_exit_codes() {
        let store = Store::open_memory().unwrap();
        let (c, dir) = fake_channel(&[
            ("secret-receipt", 1, ABSENT),
            ("deliver-secret", 0, &present("N")),
        ]);
        let v: Secret = serde_json::from_str("\"sentinel-value\"").unwrap();
        ensure_delivered(&store, &c, "b", "r1", "N", None, &v)
            .await
            .unwrap();
        assert!(dir.join("deliver-secret.called").exists());
        assert_eq!(store.secret_receipt("b", "r1").await.unwrap().unwrap(), "N");
        assert!(store.privacy_gate_open("b").await.unwrap());
    }

    #[tokio::test]
    async fn a_failed_guest_delivery_records_nothing() {
        let store = Store::open_memory().unwrap();
        let (c, _) = fake_channel(&[
            ("secret-receipt", 1, ABSENT),
            (
                "deliver-secret",
                1,
                "{\"ok\": false, \"error\": \"not_delivered\"}",
            ),
        ]);
        let v: Secret = serde_json::from_str("\"sentinel-value\"").unwrap();
        assert!(
            ensure_delivered(&store, &c, "b", "r1", "N", None, &v)
                .await
                .is_err()
        );
        assert!(store.secret_receipt("b", "r1").await.unwrap().is_none());
        assert!(!store.privacy_gate_open("b").await.unwrap());
    }

    #[tokio::test]
    async fn a_host_receipt_is_checked_against_the_guest_before_it_counts() {
        let store = Store::open_memory().unwrap();
        store
            .record_secret("b", "r1", "N", "N", None)
            .await
            .unwrap();
        let v: Secret = serde_json::from_str("\"sentinel-value\"").unwrap();
        // The guest value was overwritten or removed: the receipt no longer verifies.
        let (c, dir) = fake_channel(&[
            ("secret-receipt", 1, ABSENT),
            ("deliver-secret", 0, &present("N")),
        ]);
        let r = ensure_delivered(&store, &c, "b", "r1", "N", None, &v).await;
        assert!(r.is_err(), "a stale host receipt must not count");
        assert!(!dir.join("deliver-secret.called").exists());
        // A repeat of a delivery that the guest still holds succeeds without a second delivery.
        let (c, dir) = fake_channel(&[
            ("secret-receipt", 0, &present("N")),
            ("deliver-secret", 1, ABSENT),
        ]);
        ensure_delivered(&store, &c, "b", "r1", "N", None, &v)
            .await
            .unwrap();
        assert!(!dir.join("deliver-secret.called").exists());
        // A receipt for another name stays a mismatch.
        let (c, _) = fake_channel(&[("secret-receipt", 0, &present("N"))]);
        assert_eq!(
            ensure_delivered(&store, &c, "b", "r1", "OTHER", None, &v).await,
            Err(Failure::Mismatch)
        );
    }
}
