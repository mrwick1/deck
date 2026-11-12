//! Wire protocol v1 between the host and the guest executor. See docs/stage2-results.md.
//!
//! A frame is `DK`, one version byte, a big-endian u32 length and a JSON object.

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub const PROTO: u8 = 1;
pub const MAX_FRAME: usize = 256 * 1024;
pub const MAX_ARGV: usize = 64;
pub const MAX_ARG_BYTES: usize = 16 * 1024;
pub const MAX_DEADLINE_MS: u64 = 3_600_000;
pub const CHUNK: usize = 128 * 1024;
pub const MAX_ARTIFACT_BYTES: u64 = 8 << 20;

/// An ID is 1 to 64 characters of `A-Za-z0-9_-`. It is safe as a file name.
pub fn valid_id(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// An artifact name from the guest. It becomes a host file name, so it must be plain.
pub fn valid_artifact_name(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=128).contains(&b.len())
        && (b[0].is_ascii_alphanumeric() || b[0] == b'_')
        && b.iter()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.' | b'-'))
}

pub async fn write_frame<W: AsyncWrite + Unpin>(w: &mut W, msg: &Value) -> anyhow::Result<()> {
    let body = serde_json::to_vec(msg)?;
    if body.len() > MAX_FRAME {
        bail!("frame of {} bytes exceeds the limit", body.len());
    }
    let mut out = Vec::with_capacity(7 + body.len());
    out.extend_from_slice(b"DK");
    out.push(PROTO);
    out.extend_from_slice(&(body.len() as u32).to_be_bytes());
    out.extend_from_slice(&body);
    w.write_all(&out).await?;
    w.flush().await?;
    Ok(())
}

/// Read one frame. Any malformed frame is an error: the caller closes the connection.
pub async fn read_frame<R: AsyncRead + Unpin>(r: &mut R) -> anyhow::Result<Value> {
    let mut h = [0u8; 7];
    r.read_exact(&mut h).await.context("connection closed")?;
    if &h[..2] != b"DK" {
        bail!("bad frame magic");
    }
    if h[2] != PROTO {
        bail!("unsupported protocol version {}", h[2]);
    }
    let n = u32::from_be_bytes([h[3], h[4], h[5], h[6]]) as usize;
    if n > MAX_FRAME {
        bail!("frame of {n} bytes exceeds the limit");
    }
    let mut body = vec![0u8; n];
    r.read_exact(&mut body).await.context("short frame")?;
    let v: Value = serde_json::from_slice(&body).context("bad json in frame")?;
    if !v.is_object() {
        bail!("frame is not an object");
    }
    Ok(v)
}

/// What the guest executor and the host both hash. The epoch is not part of it, so a resend
/// after a reconnect has the same digest.
pub fn exec_digest(
    bot: &str,
    task: &str,
    command: &str,
    deadline_ms: u64,
    argv: &[String],
) -> String {
    let mut h = Sha256::new();
    h.update(b"deck-exec-v1");
    let n = argv.len().to_string();
    let d = deadline_ms.to_string();
    for part in [bot, task, command, &d, &n]
        .into_iter()
        .chain(argv.iter().map(String::as_str))
    {
        h.update(&(part.len() as u32).to_be_bytes());
        h.update(part.as_bytes());
    }
    h.hex()
}

/// What the guest says about one command. The host stores it as evidence, not as truth about
/// the outside world.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct Receipt {
    pub command_id: String,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub exec_epoch: u64,
    #[serde(default)]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub signal: Option<i64>,
    #[serde(default)]
    pub detail: Option<String>,
    #[serde(default)]
    pub group_confirmed: Option<bool>,
    #[serde(default)]
    pub stdout: String,
    #[serde(default)]
    pub stderr: String,
    #[serde(default)]
    pub truncated: bool,
    #[serde(default)]
    pub artifacts: Vec<GuestArtifact>,
    #[serde(default)]
    pub digest: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct GuestArtifact {
    pub handle: String,
    pub name: String,
    pub size: u64,
    pub sha256: String,
}

pub fn base64_decode(s: &str) -> anyhow::Result<Vec<u8>> {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD
        .decode(s)
        .context("bad base64")
}

/// Uses the SHA-256 implementation already present in the dependency graph.
#[derive(Default)]
pub struct Sha256(sha2::Sha256);

impl Sha256 {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn update(&mut self, data: &[u8]) {
        sha2::Digest::update(&mut self.0, data);
    }
    pub fn hex(self) -> String {
        sha2::Digest::finalize(self.0)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sha(data: &[u8]) -> String {
        let mut h = Sha256::new();
        h.update(data);
        h.hex()
    }

    #[test]
    fn sha256_known_vectors() {
        assert_eq!(
            sha(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        let mut h = Sha256::new();
        for _ in 0..1000 {
            h.update(&[b'a'; 1000]);
        }
        assert_eq!(
            h.hex(),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    #[test]
    fn digest_matches_the_guest_vector() {
        // The same constant is asserted in scripts/vm-prototype/test_executor.py.
        let argv = vec!["/bin/echo".to_string(), "h\u{e9}llo".to_string()];
        assert_eq!(
            exec_digest("bot-a", "task-1", "cmd-1", 5000, &argv),
            "62c9fd2f2cc72285cf2e5205394a7022d26edbfbf0028bc8045b69df2bd479ae"
        );
    }

    #[test]
    fn base64_decodes() {
        assert_eq!(base64_decode("aGVsbG8=").unwrap(), b"hello");
        assert_eq!(base64_decode("aGVsbG8h").unwrap(), b"hello!");
        assert_eq!(base64_decode("").unwrap(), b"");
        assert!(base64_decode("a$b=").is_err());
    }

    #[test]
    fn ids_and_names() {
        assert!(valid_id("cmd-1_A"));
        for bad in ["", "a/b", "..", "a b", &"x".repeat(65)] {
            assert!(!valid_id(bad), "{bad}");
        }
        assert!(valid_artifact_name("report.csv"));
        for bad in ["", ".hidden", "../x", "a/b", "a b", "-x"] {
            assert!(!valid_artifact_name(bad), "{bad}");
        }
    }

    #[tokio::test]
    async fn frames_round_trip_and_reject_bad_input() {
        let mut buf = Vec::new();
        write_frame(&mut buf, &serde_json::json!({"a": 1}))
            .await
            .unwrap();
        assert_eq!(read_frame(&mut &buf[..]).await.unwrap()["a"], 1);
        for bad in [
            &b"XX\x01\x00\x00\x00\x02{}"[..],
            b"DK\x09\x00\x00\x00\x02{}",
            b"DK\x01\xff\xff\xff\xff",
            b"DK\x01\x00\x00\x00\x03[1]",
            b"DK\x01\x00\x00\x00\x05{\"a\"",
        ] {
            assert!(read_frame(&mut &bad[..]).await.is_err());
        }
        let big = serde_json::json!({"x": "a".repeat(MAX_FRAME)});
        assert!(write_frame(&mut Vec::new(), &big).await.is_err());
    }
}
