//! Demand-driven screen capture.
//!
//! One worker per bot runs only while a viewer watches and the computer is awake. It takes one
//! capture at a time and keeps only the newest frame, so a slow viewer cannot build a queue.
//! A frame that did not change is not delivered. Capture cost and delivery are counted apart.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::time::Duration;

use anyhow::{Context, bail};
use futures_util::future::BoxFuture;
use serde::Serialize;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::sync::watch;

use crate::executor::proto::Sha256;

pub trait Capture: Send + Sync {
    fn grab(&self) -> BoxFuture<'_, anyhow::Result<Vec<u8>>>;
}

/// QEMU `screendump` over the QMP socket. It reads the virtual display and needs no guest process.
pub struct QmpCapture {
    pub socket: PathBuf,
    pub dir: PathBuf,
}

impl Capture for QmpCapture {
    fn grab(&self) -> BoxFuture<'_, anyhow::Result<Vec<u8>>> {
        Box::pin(async move {
            tokio::fs::create_dir_all(&self.dir).await?;
            let file = self
                .dir
                .join(format!("frame-{}.png", uuid::Uuid::new_v4().simple()));
            let r = tokio::time::timeout(Duration::from_secs(5), self.dump(&file)).await;
            let png = match r {
                Ok(Ok(())) => tokio::fs::read(&file).await.context("no frame file"),
                Ok(Err(e)) => Err(e),
                Err(_) => Err(anyhow::anyhow!("capture timed out")),
            };
            let _ = tokio::fs::remove_file(&file).await;
            png
        })
    }
}

impl QmpCapture {
    async fn dump(&self, file: &std::path::Path) -> anyhow::Result<()> {
        // The harness shares this socket. A client holds it only for one command and closes it.
        let s = UnixStream::connect(&self.socket)
            .await
            .context("no QMP socket")?;
        let (r, mut w) = s.into_split();
        let mut lines = BufReader::new(r).lines();
        lines.next_line().await?.context("no QMP greeting")?;
        for cmd in [
            serde_json::json!({"execute":"qmp_capabilities"}),
            serde_json::json!({"execute":"screendump","arguments":{"filename":file,"format":"png"}}),
        ] {
            w.write_all(format!("{cmd}\n").as_bytes()).await?;
            loop {
                let line = lines.next_line().await?.context("QMP closed")?;
                let v: serde_json::Value = serde_json::from_str(&line)?;
                if v.get("error").is_some() {
                    bail!("QMP error: {}", v["error"]["desc"]);
                }
                if v.get("return").is_some() {
                    break;
                }
            }
        }
        Ok(())
    }
}

#[derive(Clone, Default)]
pub struct Frame {
    pub seq: u64,
    pub png: Arc<Vec<u8>>,
}

#[derive(Default)]
pub struct Stats {
    pub workers_started: AtomicU64,
    pub captures: AtomicU64,
    pub errors: AtomicU64,
    pub delivered: AtomicU64,
    pub unchanged: AtomicU64,
    pub bytes_delivered: AtomicU64,
    pub capture_us: AtomicU64,
}

#[derive(Serialize)]
pub struct StatsView {
    pub viewers: usize,
    pub worker: bool,
    pub captures: u64,
    pub errors: u64,
    pub delivered: u64,
    pub unchanged: u64,
    pub bytes_delivered: u64,
    pub capture_ms_total: u64,
}

pub struct Screen {
    cap: Arc<dyn Capture>,
    interval: Duration,
    awake: watch::Receiver<bool>,
    tx: watch::Sender<Frame>,
    running: AtomicBool,
    /// True while a protected value may show on the screen. Set once, when the bot is wired.
    gate: std::sync::OnceLock<PrivacyGate>,
    pub stats: Stats,
}

pub type PrivacyGate = Arc<dyn Fn() -> bool + Send + Sync>;

impl Screen {
    pub fn new(
        cap: Arc<dyn Capture>,
        interval: Duration,
        awake: watch::Receiver<bool>,
    ) -> Arc<Self> {
        Arc::new(Self {
            cap,
            interval,
            awake,
            tx: watch::channel(Frame::default()).0,
            running: AtomicBool::new(false),
            gate: std::sync::OnceLock::new(),
            stats: Stats::default(),
        })
    }

    /// While the gate answers true, nothing is captured and no frame is shown. The gate must
    /// answer true when it cannot tell. Only the first call has effect.
    pub fn set_privacy_gate(&self, gate: PrivacyGate) {
        let _ = self.gate.set(gate);
    }

    fn private(&self) -> bool {
        self.gate.get().is_some_and(|g| g())
    }

    /// Subscribe. The first viewer starts the worker, if the computer is awake.
    pub fn watch(self: &Arc<Self>) -> watch::Receiver<Frame> {
        let rx = self.tx.subscribe();
        self.ensure_worker();
        rx
    }

    /// Start the worker when a viewer waits and the computer is awake. Safe to call at any time.
    pub fn ensure_worker(self: &Arc<Self>) {
        if self.tx.receiver_count() == 0 || !*self.awake.borrow() {
            return;
        }
        if self
            .running
            .compare_exchange(false, true, Relaxed, Relaxed)
            .is_err()
        {
            return;
        }
        self.stats.workers_started.fetch_add(1, Relaxed);
        let this = self.clone();
        tokio::spawn(async move {
            this.work().await;
            this.running.store(false, Relaxed);
            // A viewer may have come while the worker ended.
            this.ensure_worker();
        });
    }

    async fn work(&self) {
        let mut awake = self.awake.clone();
        let mut last = String::new();
        let mut seq = self.tx.borrow().seq;
        while self.tx.receiver_count() > 0 && *awake.borrow() {
            if self.private() {
                last.clear();
                if !self.tx.borrow().png.is_empty() {
                    seq += 1;
                    self.tx.send_replace(Frame {
                        seq,
                        png: Arc::default(),
                    });
                }
                tokio::select! {
                    _ = self.tx.closed() => {}
                    _ = awake.changed() => {}
                    _ = tokio::time::sleep(self.interval) => {}
                }
                continue;
            }
            let t = std::time::Instant::now();
            let r = self.cap.grab().await;
            self.stats
                .capture_us
                .fetch_add(t.elapsed().as_micros() as u64, Relaxed);
            self.stats.captures.fetch_add(1, Relaxed);
            match r {
                Ok(png) => {
                    let mut h = Sha256::new();
                    h.update(&png);
                    let digest = h.hex();
                    if digest == last {
                        self.stats.unchanged.fetch_add(1, Relaxed);
                    } else {
                        last = digest;
                        seq += 1;
                        self.stats.delivered.fetch_add(1, Relaxed);
                        self.stats
                            .bytes_delivered
                            .fetch_add(png.len() as u64, Relaxed);
                        self.tx.send_replace(Frame {
                            seq,
                            png: Arc::new(png),
                        });
                    }
                }
                Err(_) => {
                    self.stats.errors.fetch_add(1, Relaxed);
                }
            }
            tokio::select! {
                _ = self.tx.closed() => {}
                _ = awake.changed() => {}
                _ = tokio::time::sleep(self.interval) => {}
            }
        }
    }

    pub fn latest(&self) -> Frame {
        if self.private() {
            return Frame::default();
        }
        self.tx.borrow().clone()
    }

    pub fn view(&self) -> StatsView {
        let s = &self.stats;
        StatsView {
            viewers: self.tx.receiver_count(),
            worker: self.running.load(Relaxed),
            captures: s.captures.load(Relaxed),
            errors: s.errors.load(Relaxed),
            delivered: s.delivered.load(Relaxed),
            unchanged: s.unchanged.load(Relaxed),
            bytes_delivered: s.bytes_delivered.load(Relaxed),
            capture_ms_total: s.capture_us.load(Relaxed) / 1000,
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;
    use std::sync::atomic::AtomicUsize;

    use super::*;

    /// Returns the scripted frames in order, then the last one forever. `slow_ms` delays each grab.
    struct Fake {
        frames: Mutex<Vec<Vec<u8>>>,
        slow_ms: u64,
        now: AtomicUsize,
        max: AtomicUsize,
    }

    impl Fake {
        fn new(frames: &[&str], slow_ms: u64) -> Arc<Self> {
            Arc::new(Self {
                frames: Mutex::new(frames.iter().map(|f| f.as_bytes().to_vec()).rev().collect()),
                slow_ms,
                now: AtomicUsize::new(0),
                max: AtomicUsize::new(0),
            })
        }
    }

    impl Capture for Fake {
        fn grab(&self) -> BoxFuture<'_, anyhow::Result<Vec<u8>>> {
            Box::pin(async move {
                let n = self.now.fetch_add(1, Relaxed) + 1;
                self.max.fetch_max(n, Relaxed);
                tokio::time::sleep(Duration::from_millis(self.slow_ms)).await;
                self.now.fetch_sub(1, Relaxed);
                let mut f = self.frames.lock().unwrap();
                Ok(if f.len() > 1 {
                    f.pop().unwrap()
                } else {
                    f[0].clone()
                })
            })
        }
    }

    async fn until(mut f: impl FnMut() -> bool) {
        for _ in 0..400 {
            if f() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("condition not reached");
    }

    fn screen(cap: Arc<Fake>, awake: bool) -> (Arc<Screen>, watch::Sender<bool>) {
        let (tx, rx) = watch::channel(awake);
        (Screen::new(cap, Duration::from_millis(10), rx), tx)
    }

    #[tokio::test]
    async fn no_viewer_means_no_capture_and_no_worker() {
        let cap = Fake::new(&["a"], 0);
        let (s, _awake) = screen(cap, true);
        tokio::time::sleep(Duration::from_millis(100)).await;
        s.ensure_worker();
        assert_eq!(
            (
                s.stats.captures.load(Relaxed),
                s.stats.workers_started.load(Relaxed)
            ),
            (0, 0)
        );
    }

    #[tokio::test]
    async fn many_viewers_share_one_worker_and_the_last_disconnect_stops_it() {
        let cap = Fake::new(&["a", "b", "c"], 0);
        let (s, _awake) = screen(cap, true);
        let (a, b) = (s.watch(), s.watch());
        until(|| s.stats.delivered.load(Relaxed) >= 3).await;
        assert_eq!(s.stats.workers_started.load(Relaxed), 1);
        drop(a);
        assert!(s.view().worker, "one viewer is left");
        drop(b);
        until(|| !s.view().worker).await;
        let n = s.stats.captures.load(Relaxed);
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert_eq!(
            s.stats.captures.load(Relaxed),
            n,
            "a capture ran after the last viewer left"
        );
    }

    #[tokio::test]
    async fn unchanged_frames_are_captured_but_not_delivered() {
        let cap = Fake::new(&["same"], 0);
        let (s, _awake) = screen(cap, true);
        let _rx = s.watch();
        until(|| s.stats.captures.load(Relaxed) >= 5).await;
        assert_eq!(s.stats.delivered.load(Relaxed), 1);
        assert!(s.stats.unchanged.load(Relaxed) >= 4);
        assert_eq!(s.latest().seq, 1);
    }

    #[tokio::test]
    async fn a_slow_capture_never_overlaps_and_builds_no_queue() {
        let cap = Fake::new(&["a", "b", "c", "d", "e", "f", "g", "h"], 120);
        let (s, _awake) = screen(cap.clone(), true);
        let mut rx = s.watch();
        tokio::time::sleep(Duration::from_millis(600)).await;
        // Never more than one grab at a time. About 600/130 captures, not one per 10 ms tick.
        assert_eq!(cap.max.load(Relaxed), 1);
        assert!(
            s.stats.captures.load(Relaxed) <= 6,
            "{}",
            s.stats.captures.load(Relaxed)
        );
        // The slow viewer sees only the newest frame, not every frame.
        rx.changed().await.unwrap();
        assert_eq!(rx.borrow_and_update().seq, s.latest().seq);
    }

    #[tokio::test]
    async fn a_saved_computer_has_no_worker_and_waking_restarts_it_for_waiting_viewers() {
        let cap = Fake::new(&["a", "b"], 0);
        let (s, awake) = screen(cap, false);
        let _rx = s.watch();
        tokio::time::sleep(Duration::from_millis(80)).await;
        assert_eq!(s.stats.captures.load(Relaxed), 0);
        awake.send(true).unwrap();
        s.ensure_worker();
        until(|| s.stats.captures.load(Relaxed) >= 1).await;
        awake.send(false).unwrap();
        until(|| !s.view().worker).await;
        let n = s.stats.captures.load(Relaxed);
        tokio::time::sleep(Duration::from_millis(80)).await;
        assert_eq!(s.stats.captures.load(Relaxed), n);
    }

    #[tokio::test]
    async fn an_open_privacy_gate_stops_capture_and_clears_the_shown_frame() {
        let cap = Fake::new(&["secret-on-screen"], 0);
        let (s, _awake) = screen(cap.clone(), true);
        let private = Arc::new(AtomicBool::new(false));
        let flag = private.clone();
        s.set_privacy_gate(Arc::new(move || flag.load(Relaxed)));
        let _rx = s.watch();
        until(|| !s.latest().png.is_empty()).await;
        private.store(true, Relaxed);
        until(|| s.latest().png.is_empty()).await;
        let n = s.stats.captures.load(Relaxed);
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert_eq!(s.stats.captures.load(Relaxed), n, "captured while private");
        private.store(false, Relaxed);
        until(|| !s.latest().png.is_empty()).await;
    }
}
