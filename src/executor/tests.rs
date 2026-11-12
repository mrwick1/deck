//! Deterministic tests: a scripted fake guest, then the real Python guest executor.
//! Every wait has a bound.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::mpsc;
use tokio::time::timeout;

use super::client::{Options, Session};
use super::journal::Cmd;
use super::proto::*;
use crate::store::Store;

const BOT: &str = "bot-a";

struct Dir(PathBuf);
impl Dir {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "deck-ex-{}",
            &uuid::Uuid::new_v4().to_string()[..8]
        ));
        std::fs::create_dir_all(&p).unwrap();
        std::fs::set_permissions(&p, std::os::unix::fs::PermissionsExt::from_mode(0o700)).unwrap();
        Self(p)
    }
}
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    /// Run the command at once: ack, then a succeeded result.
    Normal,
    /// Ack as running. Finish only when the test says so.
    Hold,
    /// Run the command, keep the receipt, then drop the connection without a reply.
    DropAfterExec,
    /// Drop the connection when the exec arrives. Nothing runs.
    DropBeforeExec,
    /// Read the exec and never answer.
    Silent,
    /// Refuse every exec with a guest error. Nothing runs.
    Refuse,
}

struct G {
    bot: String,
    mode: Mode,
    max_epoch: u64,
    execs: u32,
    receipts: HashMap<String, Value>,
    artifacts: HashMap<String, Vec<u8>>,
    overserve: bool,
    lie_epoch: bool,
    journal_state_at_exec: Vec<String>,
    tx: Option<mpsc::UnboundedSender<Value>>,
}

struct Fake {
    _dir: Dir,
    path: PathBuf,
    g: Arc<Mutex<G>>,
    store: Store,
    data: PathBuf,
}

fn receipt(id: &str, state: &str, epoch: u64) -> Value {
    json!({"command_id": id, "state": state, "exec_epoch": epoch, "exit_code": if state == "succeeded" {json!(0)} else {Value::Null},
           "stdout": "out", "stderr": "", "truncated": false, "artifacts": []})
}

impl Fake {
    async fn start(mode: Mode) -> Self {
        let dir = Dir::new();
        let path = dir.0.join("g.sock");
        let data = dir.0.join("data");
        let store = Store::open(&data).unwrap();
        let g = Arc::new(Mutex::new(G {
            bot: BOT.into(),
            mode,
            max_epoch: 0,
            execs: 0,
            receipts: HashMap::new(),
            artifacts: HashMap::new(),
            overserve: false,
            lie_epoch: false,
            journal_state_at_exec: vec![],
            tx: None,
        }));
        let l = UnixListener::bind(&path).unwrap();
        std::fs::set_permissions(&path, std::os::unix::fs::PermissionsExt::from_mode(0o600))
            .unwrap();
        let (g2, st2) = (g.clone(), store.clone());
        tokio::spawn(async move {
            loop {
                let Ok((s, _)) = l.accept().await else { return };
                let (g3, st3) = (g2.clone(), st2.clone());
                tokio::spawn(conn(s, g3, st3));
            }
        });
        Self {
            _dir: dir,
            path,
            g,
            store,
            data,
        }
    }

    fn opts(&self) -> Options {
        let mut o = Options::new(BOT, &self.path, &self.data);
        o.request_timeout = Duration::from_secs(5);
        o
    }

    async fn session(&self) -> Session {
        Session::connect(self.store.clone(), self.opts())
            .await
            .unwrap()
    }

    fn execs(&self) -> u32 {
        self.g.lock().unwrap().execs
    }

    fn set_mode(&self, m: Mode) {
        self.g.lock().unwrap().mode = m;
    }

    /// Push a frame to the current connection.
    fn push(&self, v: Value) {
        self.g.lock().unwrap().tx.as_ref().unwrap().send(v).unwrap();
    }
}

async fn conn(mut s: UnixStream, g: Arc<Mutex<G>>, store: Store) {
    let (tx, mut rx) = mpsc::unbounded_channel::<Value>();
    g.lock().unwrap().tx = Some(tx);
    let mut epoch = 0u64;
    loop {
        let m = tokio::select! {
            m = read_frame(&mut s) => match m { Ok(m) => m, Err(_) => return },
            Some(out) = rx.recv() => { let _ = write_frame(&mut s, &out).await; continue; }
        };
        let id = m["id"].clone();
        let cid = m["command_id"].as_str().unwrap_or("").to_string();
        let reply = |mut v: Value| {
            v["id"] = id.clone();
            v["epoch"] = epoch.into();
            v
        };
        let out: Option<Value> = match m["type"].as_str().unwrap_or("") {
            "hello" => {
                let mut g = g.lock().unwrap();
                let ep = m["epoch"].as_u64().unwrap();
                if m["bot_id"] != g.bot.as_str() {
                    Some(json!({"type":"error","id":id,"code":"wrong_bot","message":""}))
                } else if ep <= g.max_epoch {
                    Some(json!({"type":"error","id":id,"code":"stale_epoch","message":""}))
                } else {
                    let before = g.max_epoch;
                    g.max_epoch = ep;
                    epoch = ep;
                    Some(
                        json!({"type":"hello_ack","id":id,"bot_id":g.bot,"guest_id":"guest-1","epoch": if g.lie_epoch {ep + 1} else {ep},
                                "nonce":m["nonce"],"max_epoch_before":before}),
                    )
                }
            }
            "exec" => {
                let state = store
                    .command(cid.clone())
                    .await
                    .unwrap()
                    .map(|c| c.state)
                    .unwrap_or_default();
                let mut g = g.lock().unwrap();
                g.journal_state_at_exec.push(state);
                if m["epoch"] != epoch {
                    Some(reply(json!({"type":"error","code":"stale_epoch"})))
                } else if let Some(r) = g.receipts.get(&cid).cloned() {
                    Some(reply(
                        json!({"type":"exec_ack","command_id":cid,"state":r["state"],"duplicate":true,"receipt":r}),
                    ))
                } else {
                    match g.mode {
                        Mode::DropBeforeExec => return,
                        Mode::Silent => None,
                        Mode::Refuse => Some(reply(
                            json!({"type":"error","code":"busy","message":"full"}),
                        )),
                        Mode::Normal | Mode::Hold | Mode::DropAfterExec => {
                            g.execs += 1;
                            let fin = g.mode != Mode::Hold;
                            g.receipts.insert(
                                cid.clone(),
                                receipt(&cid, if fin { "succeeded" } else { "running" }, epoch),
                            );
                            if g.mode == Mode::DropAfterExec {
                                return;
                            }
                            let ack = reply(
                                json!({"type":"exec_ack","command_id":cid,"state":"running","duplicate":false,"receipt":receipt(&cid,"running",epoch)}),
                            );
                            if fin {
                                let tx = g.tx.clone().unwrap();
                                let _ = tx.send(json!({"type":"result","epoch":epoch,"exec_epoch":epoch,"command_id":cid,"receipt":receipt(&cid,"succeeded",epoch)}));
                            }
                            Some(ack)
                        }
                    }
                }
            }
            "status" => {
                let g = g.lock().unwrap();
                let r = g.receipts.get(&cid).cloned();
                Some(reply(
                    json!({"type":"status_reply","command_id":cid,"found":r.is_some(),"receipt":r}),
                ))
            }
            "cancel" => {
                let g = g.lock().unwrap();
                let ack = match g.receipts.get(&cid) {
                    None => "unknown_command",
                    Some(r) if r["state"] == "running" => "accepted",
                    Some(_) => "already_terminal",
                };
                Some(reply(
                    json!({"type":"cancel_ack","command_id":cid,"ack":ack}),
                ))
            }
            "artifact_get" => {
                let g = g.lock().unwrap();
                let (off, n) = (
                    m["offset"].as_u64().unwrap() as usize,
                    m["length"].as_u64().unwrap() as usize,
                );
                match g.artifacts.get(m["handle"].as_str().unwrap()) {
                    None => Some(reply(json!({"type":"error","code":"unknown_artifact"}))),
                    Some(b) => {
                        let n = if g.overserve { n + 5 } else { n };
                        let end = (off + n).min(b.len());
                        let data = &b[off.min(end)..end];
                        Some(reply(
                            json!({"type":"artifact_chunk","command_id":cid,"handle":m["handle"],"offset":off,
                            "data": b64(data), "eof": end >= b.len()}),
                        ))
                    }
                }
            }
            _ => None,
        };
        if let Some(o) = out
            && write_frame(&mut s, &o).await.is_err()
        {
            return;
        }
    }
}

fn b64(d: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut o = String::new();
    for c in d.chunks(3) {
        let n = (u32::from(c[0]) << 16)
            | (u32::from(*c.get(1).unwrap_or(&0)) << 8)
            | u32::from(*c.get(2).unwrap_or(&0));
        for i in 0..4 {
            if i <= c.len() {
                o.push(T[(n >> (18 - 6 * i) & 63) as usize] as char)
            } else {
                o.push('=')
            }
        }
    }
    o
}

fn argv(s: &[&str]) -> Vec<String> {
    s.iter().map(|x| x.to_string()).collect()
}

async fn eventually<F: Fn(&Cmd) -> bool>(s: &Session, id: &str, f: F) -> Cmd {
    let end = tokio::time::Instant::now() + Duration::from_secs(5);
    loop {
        let c = s.wait(id, Duration::ZERO).await.unwrap();
        if f(&c) || tokio::time::Instant::now() > end {
            return c;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

#[tokio::test]
async fn intent_is_recorded_before_send_and_result_after() {
    let f = Fake::start(Mode::Normal).await;
    let s = f.session().await;
    let c = s
        .exec("t1", "c1", argv(&["echo", "hi"]), 5000)
        .await
        .unwrap();
    assert!(matches!(c.state.as_str(), "running" | "succeeded"));
    // The journal already said `sent` when the exec frame arrived at the guest.
    assert_eq!(f.g.lock().unwrap().journal_state_at_exec, vec!["sent"]);
    let c = eventually(&s, "c1", |c| c.is_final()).await;
    assert_eq!(
        (c.state.as_str(), c.dispatch_epoch),
        ("succeeded", Some(s.epoch))
    );
    let kinds: Vec<String> = f
        .store
        .events(BOT.into(), Some("c1".into()))
        .await
        .unwrap()
        .into_iter()
        .map(|e| e.0)
        .collect();
    assert_eq!(kinds[..2], ["intent", "dispatch"]);
}

#[tokio::test]
async fn duplicate_and_conflicting_submissions_do_not_reach_the_guest() {
    let f = Fake::start(Mode::Normal).await;
    let s = f.session().await;
    s.exec("t1", "c1", argv(&["a"]), 5000).await.unwrap();
    eventually(&s, "c1", |c| c.is_final()).await;
    let again = s.exec("t1", "c1", argv(&["a"]), 5000).await.unwrap();
    assert_eq!(again.state, "succeeded");
    let before = f.g.lock().unwrap().journal_state_at_exec.len();
    for (task, args, dl) in [
        ("t1", &["b"][..], 5000),
        ("t2", &["a"], 5000),
        ("t1", &["a"], 6000),
    ] {
        let e = s.exec(task, "c1", argv(args), dl).await.unwrap_err();
        assert!(e.to_string().contains("payload conflict"), "{e}");
    }
    assert_eq!(f.execs(), 1);
    assert_eq!(f.g.lock().unwrap().journal_state_at_exec.len(), before);
    let ev = f.store.events(BOT.into(), Some("c1".into())).await.unwrap();
    assert_eq!(ev.iter().filter(|e| e.0 == "payload_conflict").count(), 3);
}

#[tokio::test]
async fn invalid_requests_are_rejected_before_anything_is_written() {
    let f = Fake::start(Mode::Normal).await;
    let s = f.session().await;
    for (task, id, a, dl) in [
        ("t", "../x", argv(&["a"]), 1000),
        ("t", "c", vec![], 1000),
        ("t", "c", argv(&[""]), 1000),
        ("t", "c", argv(&["a\0b"]), 1000),
        ("t", "c", argv(&["a"]), 0),
        ("t", "c", argv(&["a"]), MAX_DEADLINE_MS + 1),
    ] {
        assert!(s.exec(task, id, a, dl).await.is_err());
    }
    assert!(f.store.commands(BOT.into()).await.unwrap().is_empty());
}

#[tokio::test]
async fn wrong_bot_and_lying_hello_are_rejected() {
    let f = Fake::start(Mode::Normal).await;
    f.g.lock().unwrap().bot = "other".into();
    let e = Session::connect(f.store.clone(), f.opts())
        .await
        .err()
        .unwrap();
    assert!(e.to_string().contains("wrong_bot"), "{e}");
    f.g.lock().unwrap().bot = BOT.into();
    f.g.lock().unwrap().lie_epoch = true;
    let e = Session::connect(f.store.clone(), f.opts())
        .await
        .err()
        .unwrap();
    assert!(e.to_string().contains("another bot or epoch"), "{e}");
}

#[tokio::test]
async fn guest_identity_is_pinned_and_peer_is_checked() {
    let f = Fake::start(Mode::Normal).await;
    f.session().await;
    let mut o = f.opts();
    o.expected_guest = Some("guest-2".into());
    assert!(
        Session::connect(f.store.clone(), o)
            .await
            .err()
            .unwrap()
            .to_string()
            .contains("not the expected one")
    );
    f.store
        .run(|c| {
            c.execute("UPDATE executor_bots SET guest_id='guest-9'", [])
                .map(|_| ())
        })
        .await
        .unwrap();
    assert!(
        Session::connect(f.store.clone(), f.opts())
            .await
            .err()
            .unwrap()
            .to_string()
            .contains("pinned")
    );
    f.store
        .run(|c| {
            c.execute("UPDATE executor_bots SET guest_id='guest-1'", [])
                .map(|_| ())
        })
        .await
        .unwrap();
    let mut o = f.opts();
    o.peer_pid = Some(1);
    assert!(
        Session::connect(f.store.clone(), o)
            .await
            .err()
            .unwrap()
            .to_string()
            .contains("expected process")
    );
    let mut o = f.opts();
    o.peer_pid = Some(std::process::id());
    Session::connect(f.store.clone(), o).await.unwrap();
}

#[tokio::test]
async fn endpoint_must_be_a_private_socket() {
    let d = Dir::new();
    let p = d.0.join("file");
    std::fs::write(&p, "x").unwrap();
    assert!(
        super::client::check_endpoint(&p)
            .unwrap_err()
            .to_string()
            .contains("not a unix socket")
    );
    let sock = d.0.join("s");
    let _l = UnixListener::bind(&sock).unwrap();
    std::fs::set_permissions(&sock, std::os::unix::fs::PermissionsExt::from_mode(0o666)).unwrap();
    assert!(
        super::client::check_endpoint(&sock)
            .unwrap_err()
            .to_string()
            .contains("writable")
    );
    std::fs::set_permissions(&sock, std::os::unix::fs::PermissionsExt::from_mode(0o600)).unwrap();
    super::client::check_endpoint(&sock).unwrap();
    assert!(super::client::check_endpoint(&d.0.join("none")).is_err());
}

#[tokio::test]
async fn epochs_increase_across_restarts_and_guest_rollback_cannot_lower_them() {
    let f = Fake::start(Mode::Normal).await;
    let a = f.session().await.epoch;
    let b = f.session().await.epoch;
    let store2 = Store::open(&f.data).unwrap(); // a host restart
    assert_eq!(store2.next_epoch(BOT.into()).await.unwrap(), b + 1);
    f.g.lock().unwrap().max_epoch = 0; // the guest comes back from an old saved state
    let c = f.session().await;
    assert!(a < b && b + 1 < c.epoch);
    assert_eq!(c.max_epoch_before, 0);
}

#[tokio::test]
async fn stale_replies_are_rejected_and_logged() {
    let f = Fake::start(Mode::Hold).await;
    let s = f.session().await;
    s.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap();
    f.push(json!({"type":"result","epoch": s.epoch - 1,"exec_epoch":1,"command_id":"c1","receipt":receipt("c1","succeeded",1)}));
    f.push(json!({"type":"result","command_id":"c1","receipt":receipt("c1","succeeded",1)}));
    let end = tokio::time::Instant::now() + Duration::from_secs(5);
    loop {
        let n = f
            .store
            .events(BOT.into(), Some("c1".into()))
            .await
            .unwrap()
            .iter()
            .filter(|e| e.0 == "stale_rejected")
            .count();
        if n == 2 || tokio::time::Instant::now() > end {
            assert_eq!(n, 2);
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(s.wait("c1", Duration::ZERO).await.unwrap().state, "running");
}

#[tokio::test]
async fn journal_survives_restart_and_never_dispatched_stays_visible() {
    let f = Fake::start(Mode::Normal).await;
    let digest = exec_digest(BOT, "t1", "c1", 5000, &argv(&["x"]));
    f.store
        .begin(
            BOT.into(),
            "t1".into(),
            "c1".into(),
            argv(&["x"]),
            5000,
            digest,
        )
        .await
        .unwrap();
    let store2 = Store::open(&f.data).unwrap(); // the host process died after recording the intent
    let row = store2.command("c1".into()).await.unwrap().unwrap();
    assert_eq!(row.state, "intent");
    let s = Session::connect(store2, f.opts()).await.unwrap();
    assert!(s.reconcile().await.unwrap().is_empty()); // reconcile sends nothing
    assert_eq!(f.execs(), 0);
    let c = s.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap(); // the explicit submission dispatches it
    assert_ne!(c.state, "intent");
    assert_eq!(f.execs(), 1);
}

#[tokio::test]
async fn dropped_reply_is_never_replayed() {
    let f = Fake::start(Mode::DropAfterExec).await;
    let s1 = f.session().await;
    let c = s1
        .exec("t1", "c1", argv(&["send-mail"]), 5000)
        .await
        .unwrap();
    assert_eq!(c.state, "sent"); // executed, but the host has no reply: delivery unknown
    assert_eq!(f.execs(), 1);
    drop(s1);
    let s2 = f.session().await;
    // Submitting again does not send a second exec: the host reconciles first.
    let c = s2
        .exec("t1", "c1", argv(&["send-mail"]), 5000)
        .await
        .unwrap();
    assert_eq!(c.state, "succeeded");
    assert_eq!(f.execs(), 1);
    assert_eq!(f.g.lock().unwrap().journal_state_at_exec.len(), 1); // the guest saw one exec frame
}

#[tokio::test]
async fn lost_guest_state_leaves_an_unknown_outcome_that_is_not_retried() {
    let f = Fake::start(Mode::DropAfterExec).await;
    let s1 = f.session().await;
    s1.exec("t1", "c1", argv(&["pay"]), 5000).await.unwrap();
    drop(s1);
    {
        let mut g = f.g.lock().unwrap();
        g.receipts.clear(); // the guest restarts from an older state
        g.max_epoch = 0;
        g.mode = Mode::Normal;
    }
    let s2 = f.session().await;
    let rows = s2.reconcile().await.unwrap();
    assert_eq!(
        (rows[0].state.as_str(), rows[0].detail.as_deref()),
        ("unknown", Some("guest_state_lost"))
    );
    for _ in 0..2 {
        let c = s2.exec("t1", "c1", argv(&["pay"]), 5000).await.unwrap();
        assert_eq!(c.state, "unknown");
    }
    assert_eq!(f.execs(), 1); // never executed again
}

#[tokio::test]
async fn rollback_after_hello_keeps_missing_receipt_unknown() {
    let f = Fake::start(Mode::DropAfterExec).await;
    let s1 = f.session().await;
    s1.exec("t1", "c1", argv(&["pay"]), 5000).await.unwrap();
    drop(s1);
    {
        let mut g = f.g.lock().unwrap();
        g.receipts.clear(); // the guest restarts from an older state
        // Restored snapshot retained hello but predates the command receipt.
        g.mode = Mode::Normal;
    }
    let s2 = f.session().await;
    let rows = s2.reconcile().await.unwrap();
    assert_eq!(
        (rows[0].state.as_str(), rows[0].detail.as_deref()),
        ("unknown", Some("guest_state_lost"))
    );
    for _ in 0..2 {
        let c = s2.exec("t1", "c1", argv(&["pay"]), 5000).await.unwrap();
        assert_eq!(c.state, "unknown");
    }
    assert_eq!(f.execs(), 1); // never executed again
}

#[tokio::test]
async fn disconnect_before_exec_remains_unknown_without_a_receipt() {
    let f = Fake::start(Mode::DropBeforeExec).await;
    let s1 = f.session().await;
    let c = s1.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap();
    assert_eq!(c.state, "sent");
    drop(s1);
    f.set_mode(Mode::Normal);
    let s2 = f.session().await;
    let rows = s2.reconcile().await.unwrap();
    assert_eq!(rows[0].state, "unknown");
    assert_eq!(f.execs(), 0); // reconcile did not retry
    let c = s2.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap(); // no retry without outcome evidence
    assert_eq!(c.state, "unknown");
    assert_eq!(f.execs(), 0);
}

#[tokio::test]
async fn silent_guest_leaves_a_sent_row_after_a_bounded_wait() {
    let f = Fake::start(Mode::Silent).await;
    let mut o = f.opts();
    o.request_timeout = Duration::from_millis(300);
    let s = Session::connect(f.store.clone(), o).await.unwrap();
    let t = std::time::Instant::now();
    let c = s.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap();
    assert_eq!(c.state, "sent");
    assert!(t.elapsed() < Duration::from_secs(3));
    // Same connection: absence of a receipt proves nothing yet, so the row is left alone.
    assert!(
        s.reconcile()
            .await
            .unwrap()
            .iter()
            .all(|c| c.state == "sent")
    );
}

#[tokio::test]
async fn cancel_ack_is_not_confirmation_and_late_results_do_not_revive() {
    let f = Fake::start(Mode::Hold).await;
    let s = f.session().await;
    s.exec("t1", "c1", argv(&["sleep", "99"]), 60000)
        .await
        .unwrap();
    let (ack, c) = s.cancel("c1").await.unwrap();
    assert_eq!(
        (ack.as_str(), c.state.as_str(), c.cancel_state.as_str()),
        ("accepted", "running", "accepted")
    );
    f.push(json!({"type":"result","epoch":s.epoch,"exec_epoch":s.epoch,"command_id":"c1","receipt":receipt("c1","cancelled",s.epoch)}));
    let c = eventually(&s, "c1", |c| c.is_final()).await;
    assert_eq!(
        (c.state.as_str(), c.cancel_state.as_str()),
        ("cancelled", "confirmed")
    );
    f.push(json!({"type":"result","epoch":s.epoch,"exec_epoch":s.epoch,"command_id":"c1","receipt":receipt("c1","succeeded",s.epoch)}));
    let end = tokio::time::Instant::now() + Duration::from_secs(5);
    while f
        .store
        .events(BOT.into(), Some("c1".into()))
        .await
        .unwrap()
        .iter()
        .all(|e| e.0 != "late_result")
    {
        assert!(tokio::time::Instant::now() < end, "no late_result event");
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(
        s.wait("c1", Duration::ZERO).await.unwrap().state,
        "cancelled"
    );
    assert_eq!(f.execs(), 1);
    let (ack, _) = s.cancel("c1").await.unwrap();
    assert_eq!(ack, "nothing_to_cancel");
}

#[tokio::test]
async fn unconfirmed_termination_is_unknown_not_cancelled() {
    let f = Fake::start(Mode::Hold).await;
    let s = f.session().await;
    s.exec("t1", "c1", argv(&["x"]), 60000).await.unwrap();
    s.cancel("c1").await.unwrap();
    f.push(json!({"type":"result","epoch":s.epoch,"exec_epoch":s.epoch,"command_id":"c1","receipt":receipt("c1","termination_unconfirmed",s.epoch)}));
    let c = eventually(&s, "c1", |c| c.state == "unknown").await;
    assert_eq!(
        (
            c.state.as_str(),
            c.detail.as_deref(),
            c.cancel_state.as_str()
        ),
        ("unknown", Some("termination_unconfirmed"), "unconfirmed")
    );
}

#[tokio::test]
async fn guest_refusal_marks_not_delivered() {
    let f = Fake::start(Mode::Refuse).await;
    let s = f.session().await;
    let e = s.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap_err();
    assert!(e.to_string().contains("busy"), "{e}");
    let c = s.wait("c1", Duration::ZERO).await.unwrap();
    assert_eq!(
        (c.state.as_str(), c.detail.as_deref()),
        ("not_delivered", Some("guest refused: busy"))
    );
    assert_eq!(f.execs(), 0);
    f.set_mode(Mode::Normal);
    assert_ne!(
        s.exec("t1", "c1", argv(&["x"]), 5000).await.unwrap().state,
        "not_delivered"
    ); // explicit resubmission
}

async fn fake_with_artifact(
    name: &str,
    declared_sha: Option<String>,
    declared_size: Option<u64>,
    bytes: &[u8],
) -> (Fake, Session) {
    let f = Fake::start(Mode::Hold).await;
    let s = f.session().await;
    s.exec("t1", "c1", argv(&["x"]), 60000).await.unwrap();
    let mut h = Sha256::new();
    h.update(bytes);
    let art = json!({"handle":"a0","name":name,"size":declared_size.unwrap_or(bytes.len() as u64),"sha256":declared_sha.unwrap_or(h.hex())});
    f.g.lock()
        .unwrap()
        .artifacts
        .insert("a0".into(), bytes.to_vec());
    let mut r = receipt("c1", "succeeded", s.epoch);
    r["artifacts"] = json!([art]);
    f.push(
        json!({"type":"result","epoch":s.epoch,"exec_epoch":s.epoch,"command_id":"c1","receipt":r}),
    );
    eventually(&s, "c1", |c| c.is_final()).await;
    (f, s)
}

fn bot_art(f: &Fake) -> PathBuf {
    f.data.join("bots").join(BOT).join("artifacts").join("c1")
}

#[tokio::test]
async fn artifact_is_copied_with_checksum_and_stays_in_the_bot_store() {
    let bytes: Vec<u8> = (0..300_000u32).map(|i| (i * 7) as u8).collect();
    let (f, s) = fake_with_artifact("report.bin", None, None, &bytes).await;
    let files = s.fetch_artifacts("c1").await.unwrap();
    assert_eq!(files, vec![bot_art(&f).join("report.bin")]);
    assert_eq!(std::fs::read(&files[0]).unwrap(), bytes);
    assert_eq!(
        std::fs::metadata(&files[0]).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert_eq!(s.fetch_artifacts("c1").await.unwrap(), files); // idempotent
    assert!(
        std::fs::read_dir(bot_art(&f)).unwrap().all(|e| !e
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".part"))
    );
}
use std::os::unix::fs::PermissionsExt;

#[tokio::test]
async fn artifact_checksum_size_name_and_overserve_failures_leave_nothing() {
    let b = b"hello artifact";
    let cases = vec![
        (
            "a.txt",
            Some("0".repeat(64)),
            None,
            false,
            "checksum mismatch",
        ),
        (
            "a.txt",
            None,
            Some(b.len() as u64 + 10),
            false,
            "ended after",
        ),
        (
            "a.txt",
            None,
            Some(MAX_ARTIFACT_BYTES + 1),
            false,
            "unacceptable",
        ),
        ("../evil", None, None, false, "unacceptable"),
        (".hidden", None, None, false, "unacceptable"),
        ("sub/x", None, None, false, "unacceptable"),
        ("a.txt", None, Some(4), true, "more bytes than declared"),
    ];
    for (name, sha, size, over, want) in cases {
        let (f, s) = fake_with_artifact(name, sha, size, b).await;
        f.g.lock().unwrap().overserve = over;
        let e = s.fetch_artifacts("c1").await.unwrap_err();
        assert!(e.to_string().contains(want), "{name}: {e}");
        let left: Vec<_> = std::fs::read_dir(bot_art(&f))
            .map(|d| d.map(|e| e.unwrap().file_name()).collect())
            .unwrap_or_default();
        assert!(left.is_empty(), "{name}: {left:?}");
        assert!(
            !f.data.join("evil").exists() && !f.data.join("bots").join(BOT).join("evil").exists()
        );
    }
}

#[tokio::test]
async fn missing_artifact_and_planted_links_are_refused() {
    let (f, s) = fake_with_artifact("a.txt", None, None, b"data").await;
    f.g.lock().unwrap().artifacts.clear();
    let e = s.fetch_artifacts("c1").await.unwrap_err();
    assert!(e.to_string().contains("unknown_artifact"), "{e}");
    assert!(std::fs::read_dir(bot_art(&f)).unwrap().next().is_none());
    // A link planted at the final name is not followed.
    f.g.lock()
        .unwrap()
        .artifacts
        .insert("a0".into(), b"data".to_vec());
    let outside = f.data.join("outside");
    std::fs::write(&outside, "keep").unwrap();
    std::os::unix::fs::symlink(&outside, bot_art(&f).join("a.txt.part")).unwrap();
    assert!(s.fetch_artifacts("c1").await.is_err());
    assert_eq!(std::fs::read_to_string(&outside).unwrap(), "keep");
    // A link planted in place of the command directory is refused.
    std::fs::remove_dir_all(bot_art(&f)).unwrap();
    std::os::unix::fs::symlink(&f.data, bot_art(&f)).unwrap();
    let e = s.fetch_artifacts("c1").await.unwrap_err();
    assert!(e.to_string().contains("not a plain directory"), "{e}");
    assert!(!f.data.join("a.txt.part").exists() && !f.data.join("a.txt").exists());
}

// ---------- the real Python guest executor ----------

struct Guest {
    _dir: Dir,
    child: std::process::Child,
    sock: PathBuf,
    state: PathBuf,
    ws: PathBuf,
    data: PathBuf,
    store: Store,
}

impl Drop for Guest {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn guest_script() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/vm-prototype/guest/deck_executor.py")
}

impl Guest {
    fn spawn(sock: &Path, state: &Path, ws: &Path) -> std::process::Child {
        use std::io::{BufRead, BufReader};
        let mut child = std::process::Command::new("python3")
            .args([
                "-I".as_ref(),
                guest_script().as_os_str(),
                "--listen-unix".as_ref(),
                sock.as_os_str(),
                "--state".as_ref(),
                state.as_os_str(),
                "--workspace".as_ref(),
                ws.as_os_str(),
            ])
            .stdout(std::process::Stdio::piped())
            .spawn()
            .expect("python3 is required for the guest executor tests");
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        assert_eq!(line.trim(), "ready");
        child
    }

    async fn start() -> Self {
        let dir = Dir::new();
        let (sock, state, ws, data) = (
            dir.0.join("g.sock"),
            dir.0.join("state"),
            dir.0.join("ws"),
            dir.0.join("data"),
        );
        std::fs::create_dir_all(&state).unwrap();
        std::fs::write(
            state.join("identity.json"),
            json!({"bot_id":BOT,"guest_id":"guest-py","max_epoch":0}).to_string(),
        )
        .unwrap();
        let child = Self::spawn(&sock, &state, &ws);
        let store = Store::open(&data).unwrap();
        Self {
            _dir: dir,
            child,
            sock,
            state,
            ws,
            data,
            store,
        }
    }

    async fn session(&self) -> Session {
        let mut o = Options::new(BOT, &self.sock, &self.data);
        o.request_timeout = Duration::from_secs(10);
        Session::connect(self.store.clone(), o).await.unwrap()
    }
}

fn alive(pid: i64) -> bool {
    std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .is_ok_and(|s| s.rsplit(')').next().unwrap().split_whitespace().next() != Some("Z"))
}

#[tokio::test]
async fn real_guest_runs_reconnects_and_returns_the_prior_outcome_without_a_second_side_effect() {
    timeout(Duration::from_secs(60), async {
        let g = Guest::start().await;
        let marker = g.ws.join("count");
        let script = format!(
            "echo x >> {}; echo $DECK_OUT > /dev/null; echo hello > $DECK_OUT/r.txt",
            marker.display()
        );
        let s1 = g.session().await;
        let c = s1
            .exec("t1", "c1", argv(&["/bin/sh", "-c", &script]), 20000)
            .await
            .unwrap();
        assert!(matches!(c.state.as_str(), "running" | "succeeded"));
        let c = s1.wait("c1", Duration::from_secs(20)).await.unwrap();
        assert_eq!((c.state.as_str(), c.exit_code), ("succeeded", Some(0)));
        let e1 = s1.epoch;
        drop(s1);
        let s2 = g.session().await;
        assert!(s2.epoch > e1 && s2.max_epoch_before == e1);
        let c = s2
            .exec("t1", "c1", argv(&["/bin/sh", "-c", &script]), 20000)
            .await
            .unwrap();
        assert_eq!(c.state, "succeeded");
        assert_eq!(std::fs::read_to_string(&marker).unwrap(), "x\n");
        let e = s2
            .exec("t1", "c1", argv(&["/bin/sh", "-c", "echo other"]), 20000)
            .await
            .unwrap_err();
        assert!(e.to_string().contains("payload conflict"));
        let files = s2.fetch_artifacts("c1").await.unwrap();
        assert_eq!(std::fs::read_to_string(&files[0]).unwrap(), "hello\n");
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn real_guest_status_while_running_and_cancel_stops_the_process_tree() {
    timeout(Duration::from_secs(60), async {
        let g = Guest::start().await;
        let pidfile = g.ws.join("child.pid");
        let s = g.session().await;
        let script = format!("sleep 60 & echo $! > {}; wait", pidfile.display());
        s.exec("t1", "long", argv(&["/bin/sh", "-c", &script]), 40000)
            .await
            .unwrap();
        let end = std::time::Instant::now() + Duration::from_secs(10);
        while std::fs::read_to_string(&pidfile).map_or(true, |s| s.trim().is_empty()) {
            assert!(std::time::Instant::now() < end);
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let child: i64 = std::fs::read_to_string(&pidfile)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(alive(child));
        // A second connection queries while the first runs the command? One link at a time: query on this one.
        let during = s.reconcile().await.unwrap();
        assert_eq!(during[0].state, "running");
        let (ack, c) = s.cancel("long").await.unwrap();
        assert_eq!(ack, "accepted");
        assert!(matches!(c.state.as_str(), "running" | "cancelled"));
        let c = s.wait("long", Duration::from_secs(15)).await.unwrap();
        assert_eq!(
            (c.state.as_str(), c.cancel_state.as_str()),
            ("cancelled", "confirmed")
        );
        assert!(!alive(child), "the child of the command survived");
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn real_guest_restart_turns_a_running_command_into_an_unknown_outcome() {
    timeout(Duration::from_secs(60), async {
        let mut g = Guest::start().await;
        let s = g.session().await;
        s.exec("t1", "mid", argv(&["/bin/sleep", "60"]), 50000)
            .await
            .unwrap();
        s.wait("mid", Duration::from_millis(300)).await.unwrap();
        drop(s);
        let _ = g.child.kill();
        let _ = g.child.wait();
        g.child = Guest::spawn(&g.sock, &g.state, &g.ws); // the executor restarts over the same receipts
        let s2 = g.session().await;
        let rows = s2.reconcile().await.unwrap();
        assert_eq!(
            (rows[0].state.as_str(), rows[0].detail.as_deref()),
            ("unknown", Some("guest_interrupted"))
        );
        let c = s2
            .exec("t1", "mid", argv(&["/bin/sleep", "60"]), 50000)
            .await
            .unwrap();
        assert_eq!(c.state, "unknown"); // not run again
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn session_cannot_cancel_or_fetch_another_bots_command() {
    let f = Fake::start(Mode::Normal).await;
    let s = f.session().await;
    f.store
        .begin(
            "other-bot".into(),
            "task".into(),
            "foreign".into(),
            argv(&["x"]),
            5000,
            "digest".into(),
        )
        .await
        .unwrap();
    assert!(s.cancel("foreign").await.is_err());
    assert!(s.fetch_artifacts("foreign").await.is_err());
    assert_eq!(f.execs(), 0);
}
