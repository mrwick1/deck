//! Minimal MCP client (JSON-RPC 2.0, one JSON object per line) for `bare-mcp`.

use std::collections::HashSet;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Mutex as StdMutex;
use std::time::Duration;

use anyhow::{Context, anyhow};
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

const PROTOCOL: &str = "2025-06-18";
const CALL_TIMEOUT: Duration = Duration::from_secs(180);

/// One JSON-RPC connection over any byte transport.
pub struct Conn<R, W> {
    reader: BufReader<R>,
    writer: W,
    next_id: u64,
    dead: bool,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> Conn<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            reader: BufReader::new(reader),
            writer,
            next_id: 1,
            dead: false,
        }
    }

    async fn send(&mut self, msg: &Value) -> anyhow::Result<()> {
        let mut line = msg.to_string();
        line.push('\n');
        let r = async {
            self.writer.write_all(line.as_bytes()).await?;
            self.writer.flush().await
        }
        .await;
        r.inspect_err(|_| self.dead = true)
            .context("cannot write to bare-mcp")
    }

    async fn notify(&mut self, method: &str) -> anyhow::Result<()> {
        self.send(&json!({"jsonrpc":"2.0","method":method})).await
    }

    /// Send a request and wait for the response with the same id.
    async fn request(&mut self, method: &str, params: Value) -> anyhow::Result<Value> {
        let id = self.next_id;
        self.next_id += 1;
        self.send(&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))
            .await?;
        loop {
            let mut line = String::new();
            let n = self
                .reader
                .read_line(&mut line)
                .await
                .inspect_err(|_| self.dead = true)
                .context("cannot read from bare-mcp")?;
            if n == 0 {
                self.dead = true;
                return Err(anyhow!("bare-mcp closed the connection"));
            }
            let Ok(v) = serde_json::from_str::<Value>(line.trim()) else {
                continue;
            };
            if v["id"] != json!(id) {
                continue; // a notification or a stale reply
            }
            if let Some(e) = v.get("error") {
                return Err(anyhow!("bare-mcp error: {e}"));
            }
            return Ok(v["result"].clone());
        }
    }

    pub async fn handshake(&mut self) -> anyhow::Result<()> {
        self.request(
            "initialize",
            json!({
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "deck", "version": env!("CARGO_PKG_VERSION")},
            }),
        )
        .await?;
        self.notify("notifications/initialized").await
    }

    pub async fn list_tools(&mut self) -> anyhow::Result<Value> {
        self.request("tools/list", json!({})).await
    }

    /// Call a tool and parse the JSON report in `content[0].text`.
    pub async fn call_tool(&mut self, name: &str, args: Value) -> anyhow::Result<Value> {
        let r = self
            .request("tools/call", json!({"name": name, "arguments": args}))
            .await?;
        let text = r["content"][0]["text"]
            .as_str()
            .ok_or_else(|| anyhow!("bare-mcp result has no text"))?;
        if r["isError"].as_bool() == Some(true) {
            return Err(anyhow!("bare-mcp tool error: {text}"));
        }
        serde_json::from_str(text).context("bare-mcp report is not JSON")
    }
}

type Pipes = Conn<tokio::process::ChildStdout, tokio::process::ChildStdin>;

struct Session {
    conn: Pipes,
    _child: Child,
}

/// One `bare-mcp` child process, started on first use and serialized by a mutex.
pub struct Bare {
    command: String,
    args: Vec<String>,
    session: Mutex<Option<Session>>,
    outs: StdMutex<HashSet<PathBuf>>,
}

impl Bare {
    pub fn new(command: String, args: Vec<String>) -> Self {
        Self {
            command,
            args,
            session: Mutex::new(None),
            outs: StdMutex::new(HashSet::new()),
        }
    }

    async fn spawn(&self) -> anyhow::Result<Session> {
        let mut cmd = Command::new(&self.command);
        cmd.args(&self.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        // SAFETY: the closure only calls prctl, which is async-signal-safe.
        unsafe {
            cmd.pre_exec(|| {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = cmd
            .spawn()
            .with_context(|| format!("cannot start {}", self.command))?;
        let stdin = child.stdin.take().context("no stdin")?;
        let stdout = child.stdout.take().context("no stdout")?;
        let mut conn = Conn::new(stdout, stdin);
        tokio::time::timeout(Duration::from_secs(30), async {
            conn.handshake().await?;
            conn.list_tools().await
        })
        .await
        .context("bare-mcp handshake timed out")??;
        Ok(Session {
            conn,
            _child: child,
        })
    }

    /// Call a bare-mcp tool. Restarts the process once when it died.
    pub async fn call(&self, name: &str, args: Value) -> anyhow::Result<Value> {
        let mut guard = self.session.lock().await;
        let mut restarted = false;
        loop {
            if guard.is_none() {
                *guard = Some(self.spawn().await?);
            }
            let s = guard.as_mut().expect("session set above");
            let r = tokio::time::timeout(CALL_TIMEOUT, s.conn.call_tool(name, args.clone())).await;
            let r = match r {
                Ok(r) => r,
                Err(_) => {
                    *guard = None; // kills the child
                    return Err(anyhow!("bare-mcp call {name} timed out"));
                }
            };
            if s.conn.dead {
                *guard = None;
                if !restarted {
                    restarted = true;
                    continue;
                }
            }
            if let Ok(report) = &r
                && let Some(out) = report["out"].as_str()
            {
                self.outs.lock().expect("outs lock").insert(out.into());
            }
            return r;
        }
    }

    /// Capture folders seen in reports so far.
    pub fn out_dirs(&self) -> Vec<PathBuf> {
        self.outs
            .lock()
            .expect("outs lock")
            .iter()
            .cloned()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{DuplexStream, duplex, split};

    /// Replays the real bare-mcp transcript over an in-process pipe.
    async fn fake_server(io: DuplexStream) {
        let (r, mut w) = split(io);
        let mut lines = BufReader::new(r).lines();
        while let Some(line) = lines.next_line().await.unwrap() {
            let v: Value = serde_json::from_str(&line).unwrap();
            let Some(id) = v.get("id") else { continue };
            let result = match v["method"].as_str().unwrap() {
                "initialize" => {
                    assert_eq!(v["params"]["protocolVersion"], PROTOCOL);
                    json!({"protocolVersion":PROTOCOL,"capabilities":{"tools":{}},"serverInfo":{"name":"bare-mcp"}})
                }
                "tools/list" => json!({"tools":[{"name":"open"},{"name":"step"}]}),
                "tools/call" => {
                    let report = match v["params"]["name"].as_str().unwrap() {
                        "open" => {
                            json!({"ok":true,"steps":[],"out":"/data/tmp/bare-mcp-3150233-1"})
                        }
                        "step" => {
                            json!({"ok":true,"steps":[{"line":2,"step":"shot home","ok":true,"note":"/data/tmp/bare-mcp-3150233-1/home.png"}],"out":"/data/tmp/bare-mcp-3150233-1"})
                        }
                        _ => json!({"ok":false}),
                    };
                    json!({"content":[{"type":"text","text":report.to_string()}]})
                }
                m => panic!("unexpected {m}"),
            };
            // A notification first: the client must skip it.
            let note = json!({"jsonrpc":"2.0","method":"notifications/message","params":{}});
            let reply = json!({"jsonrpc":"2.0","id":id,"result":result});
            w.write_all(format!("{note}\n{reply}\n").as_bytes())
                .await
                .unwrap();
        }
    }

    #[tokio::test]
    async fn replays_the_transcript() {
        let (a, b) = duplex(8192);
        tokio::spawn(fake_server(b));
        let (r, w) = split(a);
        let mut c = Conn::new(r, w);
        c.handshake().await.unwrap();
        assert_eq!(c.list_tools().await.unwrap()["tools"][0]["name"], "open");
        let open = c
            .call_tool("open", json!({"url":"file:///x/p.html"}))
            .await
            .unwrap();
        assert_eq!(open["out"], "/data/tmp/bare-mcp-3150233-1");
        let step = c
            .call_tool("step", json!({"steps":"shot home"}))
            .await
            .unwrap();
        assert_eq!(
            step["steps"][0]["note"],
            "/data/tmp/bare-mcp-3150233-1/home.png"
        );
    }

    #[tokio::test]
    async fn closed_pipe_marks_dead() {
        let (a, b) = duplex(64);
        drop(b);
        let (r, w) = split(a);
        let mut c = Conn::new(r, w);
        assert!(c.call_tool("open", json!({})).await.is_err());
        assert!(c.dead);
    }

    #[tokio::test]
    async fn missing_command_is_an_error() {
        let b = Bare::new("/nonexistent/bare-mcp".into(), vec![]);
        let e = b.call("open", json!({})).await.unwrap_err().to_string();
        assert!(e.contains("cannot start"), "{e}");
    }
}
