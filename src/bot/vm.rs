//! Start, restore, save and inspect the computer of the bot.
//!
//! The lifecycle code stays in `scripts/vm-prototype/vmproto.py`. This module runs it with an
//! explicit argument list, never through a shell, and bounds every call with a timeout.

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, bail};
use futures_util::future::BoxFuture;
use serde_json::Value;
use tokio::process::Command;

use crate::config::Vm as VmConfig;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct VmStatus {
    pub running: bool,
    pub state_saved: bool,
    /// The QEMU process, when it runs. The executor check compares it with the socket peer.
    pub pid: Option<u32>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Woke {
    Restored,
    Booted,
}

pub trait Vm: Send + Sync {
    fn status(&self) -> BoxFuture<'_, anyhow::Result<VmStatus>>;
    /// Restore the saved state, or boot the disk when nothing is saved.
    fn wake(&self) -> BoxFuture<'_, anyhow::Result<Woke>>;
    /// Save the state and stop the process.
    fn save(&self) -> BoxFuture<'_, anyhow::Result<()>>;
    /// The host end of the executor channel.
    fn endpoint(&self) -> PathBuf;
    /// The QMP socket, for screen capture.
    fn qmp(&self) -> PathBuf;
}

pub struct Harness {
    python: String,
    script: PathBuf,
    dir: PathBuf,
    fixture_port: u16,
    internet: bool,
    codec: String,
    limit: Duration,
}

impl Harness {
    pub fn new(c: &VmConfig) -> Self {
        Self {
            python: c.python.clone(),
            script: c.harness.clone(),
            dir: c.runtime_dir.clone(),
            fixture_port: c.fixture_port,
            internet: c.internet,
            codec: c.state_codec.clone(),
            limit: Duration::from_secs(c.lifecycle_secs),
        }
    }

    async fn run(&self, sub: &[&str]) -> anyhow::Result<String> {
        let mut cmd = Command::new(&self.python);
        cmd.arg("-I")
            .arg(&self.script)
            .arg("--dir")
            .arg(&self.dir)
            .arg("--fixture-port")
            .arg(self.fixture_port.to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if self.internet {
            cmd.arg("--internet");
        }
        cmd.args(sub);
        let out = tokio::time::timeout(self.limit, cmd.output())
            .await
            .map_err(|_| anyhow::anyhow!("`{}` timed out after {:?}", sub[0], self.limit))?
            .with_context(|| format!("cannot run {}", self.python))?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            bail!(
                "`{}` failed: {}",
                sub[0],
                err.trim().chars().take(300).collect::<String>()
            );
        }
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }

    fn vm_pid(&self) -> Option<u32> {
        let text = std::fs::read_to_string(self.dir.join("run").join("vm.json")).ok()?;
        serde_json::from_str::<Value>(&text).ok()?["pid"]
            .as_u64()
            .map(|p| p as u32)
    }
}

impl Vm for Harness {
    fn status(&self) -> BoxFuture<'_, anyhow::Result<VmStatus>> {
        Box::pin(async move {
            let v: Value = serde_json::from_str(self.run(&["status"]).await?.trim())
                .context("the harness status is not JSON")?;
            let running = v["running"].as_bool().unwrap_or(false);
            Ok(VmStatus {
                running,
                state_saved: v["state_saved"].as_bool().unwrap_or(false),
                pid: running.then(|| self.vm_pid()).flatten(),
            })
        })
    }

    fn wake(&self) -> BoxFuture<'_, anyhow::Result<Woke>> {
        Box::pin(async move {
            let st = self.status().await?;
            if st.running {
                bail!("the computer already runs");
            }
            let (sub, woke) = if st.state_saved {
                ("restore", Woke::Restored)
            } else {
                ("start", Woke::Booted)
            };
            if let Err(e) = self.run(&[sub]).await {
                // A half-started process must not stay behind. The harness checks its identity.
                let _ = self.run(&["kill"]).await;
                return Err(e);
            }
            Ok(woke)
        })
    }

    fn save(&self) -> BoxFuture<'_, anyhow::Result<()>> {
        Box::pin(async move {
            self.run(&["stop", "--codec", &self.codec]).await?;
            Ok(())
        })
    }

    fn endpoint(&self) -> PathBuf {
        self.dir.join("run").join("exec.sock")
    }

    fn qmp(&self) -> PathBuf {
        self.dir.join("run").join("qmp.sock")
    }
}

/// A scripted computer for tests. It counts calls and can fail or stall on request.
#[cfg(test)]
pub struct FakeVm {
    pub inner: std::sync::Mutex<FakeState>,
    pub dir: PathBuf,
    /// Another socket to use as the executor channel.
    pub endpoint: std::sync::Mutex<Option<PathBuf>>,
}

#[cfg(test)]
#[derive(Default)]
pub struct FakeState {
    pub running: bool,
    pub saved: bool,
    pub wakes: usize,
    pub saves: usize,
    pub fail_wake: bool,
    pub fail_save: bool,
    pub wake_delay_ms: u64,
}

#[cfg(test)]
impl FakeVm {
    pub fn new(running: bool, saved: bool) -> std::sync::Arc<Self> {
        std::sync::Arc::new(Self {
            inner: std::sync::Mutex::new(FakeState {
                running,
                saved,
                ..FakeState::default()
            }),
            dir: crate::testutil::temp_dir(),
            endpoint: Default::default(),
        })
    }
}

#[cfg(test)]
impl Vm for FakeVm {
    fn status(&self) -> BoxFuture<'_, anyhow::Result<VmStatus>> {
        Box::pin(async move {
            let s = self.inner.lock().unwrap();
            Ok(VmStatus {
                running: s.running,
                state_saved: s.saved,
                pid: None,
            })
        })
    }
    fn wake(&self) -> BoxFuture<'_, anyhow::Result<Woke>> {
        Box::pin(async move {
            let delay = self.inner.lock().unwrap().wake_delay_ms;
            tokio::time::sleep(Duration::from_millis(delay)).await;
            let mut s = self.inner.lock().unwrap();
            s.wakes += 1;
            if s.fail_wake {
                bail!("scripted wake failure");
            }
            let woke = if s.saved {
                Woke::Restored
            } else {
                Woke::Booted
            };
            s.running = true;
            s.saved = false;
            Ok(woke)
        })
    }
    fn save(&self) -> BoxFuture<'_, anyhow::Result<()>> {
        Box::pin(async move {
            let mut s = self.inner.lock().unwrap();
            s.saves += 1;
            if s.fail_save {
                bail!("scripted save failure");
            }
            s.running = false;
            s.saved = true;
            Ok(())
        })
    }
    fn endpoint(&self) -> PathBuf {
        let o = self.endpoint.lock().unwrap().clone();
        o.unwrap_or_else(|| self.dir.join("exec.sock"))
    }
    fn qmp(&self) -> PathBuf {
        self.dir.join("qmp.sock")
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::*;
    use crate::testutil::temp_dir;

    const FAKE: &str = r#"
import sys, json, os, time
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "log"), "a").write(json.dumps(sys.argv[1:]) + "\n")
sub = sys.argv[6] if "--internet" in sys.argv else sys.argv[5]
mode = open(os.path.join(here, "mode")).read().strip()
if os.path.exists(os.path.join(here, "hang")):
    time.sleep(60)
if sub == "status":
    print(json.dumps({"running": mode == "running", "state_saved": mode == "saved"}))
elif sub == "start" and os.path.exists(os.path.join(here, "failstart")):
    sys.exit("boom")
"#;

    fn harness(dir: &Path, runtime: &str, limit: u64) -> Harness {
        let script = dir.join("fake.py");
        std::fs::write(&script, FAKE).unwrap();
        Harness {
            python: "python3".into(),
            script,
            dir: runtime.into(),
            fixture_port: 1234,
            internet: false,
            codec: "zstd-3".into(),
            limit: Duration::from_secs(limit),
        }
    }

    fn log(dir: &Path) -> Vec<Vec<String>> {
        std::fs::read_to_string(dir.join("log"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    #[tokio::test]
    async fn wake_restores_saved_state_and_boots_otherwise() {
        let d = temp_dir();
        let h = harness(&d, "/rt", 20);
        std::fs::write(d.join("mode"), "saved").unwrap();
        assert_eq!(h.wake().await.unwrap(), Woke::Restored);
        std::fs::write(d.join("mode"), "off").unwrap();
        assert_eq!(h.wake().await.unwrap(), Woke::Booted);
        std::fs::write(d.join("mode"), "running").unwrap();
        assert!(h.wake().await.is_err(), "never replace a running computer");
        let subs: Vec<_> = log(&d).iter().map(|a| a[4].clone()).collect();
        assert_eq!(subs, ["status", "restore", "status", "start", "status"]);
    }

    #[tokio::test]
    async fn config_values_reach_the_harness_as_plain_arguments() {
        let d = temp_dir();
        let evil = format!("{}/x; touch {}/pwned $(id)", d.display(), d.display());
        let h = harness(&d, &evil, 20);
        std::fs::write(d.join("mode"), "running").unwrap();
        h.save().await.unwrap();
        let args = log(&d).pop().unwrap();
        assert_eq!(args[1], evil, "one argument, not interpreted");
        assert_eq!(args[4..], ["stop", "--codec", "zstd-3"]);
        assert!(!d.join("pwned").exists());
    }

    #[tokio::test]
    async fn internet_option_precedes_the_lifecycle_subcommand() {
        let d = temp_dir();
        let mut h = harness(&d, "/rt", 20);
        h.internet = true;
        std::fs::write(d.join("mode"), "saved").unwrap();
        assert_eq!(h.wake().await.unwrap(), Woke::Restored);
        for args in log(&d) {
            assert_eq!(args[4], "--internet");
            assert!(["status", "restore"].contains(&args[5].as_str()));
        }
    }

    #[tokio::test]
    async fn a_slow_call_times_out_and_the_child_is_killed() {
        let d = temp_dir();
        let h = harness(&d, "/rt", 1);
        std::fs::write(d.join("mode"), "running").unwrap();
        std::fs::write(d.join("hang"), "").unwrap();
        let t = std::time::Instant::now();
        let e = h.save().await.unwrap_err().to_string();
        assert!(e.contains("timed out"), "{e}");
        assert!(t.elapsed() < Duration::from_secs(10));
    }

    #[tokio::test]
    async fn a_failed_start_runs_the_identity_checked_kill() {
        let d = temp_dir();
        let h = harness(&d, "/rt", 20);
        std::fs::write(d.join("mode"), "off").unwrap();
        std::fs::write(d.join("failstart"), "").unwrap();
        assert!(h.wake().await.is_err());
        assert_eq!(log(&d).last().unwrap()[4], "kill");
    }
}
