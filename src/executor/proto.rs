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
    let mut out = Vec::with_capacity(s.len() / 4 * 3);
    let (mut acc, mut bits) = (0u32, 0);
    for (i, c) in s.bytes().enumerate() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' if i + 2 >= s.len() => break,
            _ => bail!("bad base64"),
        };
        acc = acc << 6 | u32::from(v);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Ok(out)
}

/// SHA-256 (FIPS 180-4). The project has no hashing dependency and needs this one digest.
pub struct Sha256 {
    h: [u32; 8],
    buf: Vec<u8>,
    len: u64,
}

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256 {
    pub fn new() -> Self {
        Self {
            h: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
                0x5be0cd19,
            ],
            buf: Vec::new(),
            len: 0,
        }
    }

    pub fn update(&mut self, data: &[u8]) {
        self.len += data.len() as u64;
        self.buf.extend_from_slice(data);
        let whole = self.buf.len() / 64 * 64;
        let rest = self.buf.split_off(whole);
        let blocks = std::mem::replace(&mut self.buf, rest);
        for b in blocks.chunks_exact(64) {
            self.block(b);
        }
    }

    fn block(&mut self, b: &[u8]) {
        let mut w = [0u32; 64];
        for (i, c) in b.chunks_exact(4).enumerate() {
            w[i] = u32::from_be_bytes([c[0], c[1], c[2], c[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let mut v = self.h;
        for i in 0..64 {
            let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
            let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
            let t1 = v[7]
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
            let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
            let t2 = s0.wrapping_add(maj);
            v = [
                t1.wrapping_add(t2),
                v[0],
                v[1],
                v[2],
                v[3].wrapping_add(t1),
                v[4],
                v[5],
                v[6],
            ];
        }
        for (h, x) in self.h.iter_mut().zip(v) {
            *h = h.wrapping_add(x);
        }
    }

    pub fn hex(mut self) -> String {
        let bits = self.len * 8;
        let mut pad = vec![0x80u8];
        while (self.buf.len() + pad.len()) % 64 != 56 {
            pad.push(0);
        }
        pad.extend_from_slice(&bits.to_be_bytes());
        self.buf.extend_from_slice(&pad);
        let buf = std::mem::take(&mut self.buf);
        for b in buf.chunks_exact(64) {
            self.block(b);
        }
        self.h.iter().map(|x| format!("{x:08x}")).collect()
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
