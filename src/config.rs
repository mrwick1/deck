use std::net::{IpAddr, SocketAddr};
use std::path::{Path, PathBuf};

use anyhow::Context;
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)] // `model` and `bare` are unused until later PRs.
pub struct Config {
    pub listen: SocketAddr,
    pub data_dir: PathBuf,
    pub trusted_proxies: Vec<IpAddr>,
    #[serde(default)]
    pub allowed_users: Vec<String>,
    pub model: Model,
    pub bare: Bare,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)] // Unused until the agent loop lands.
pub struct Model {
    pub base_url: String,
    pub api_key_env: String,
    pub agent_model: String,
    pub small_model: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)] // Unused until the browser lands.
pub struct Bare {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
}

impl Config {
    pub fn parse(text: &str) -> anyhow::Result<Self> {
        Ok(toml::from_str(text)?)
    }

    pub fn load() -> anyhow::Result<Self> {
        let path = std::env::var("DECK_CONFIG").unwrap_or_else(|_| "deck.toml".into());
        let text = std::fs::read_to_string(Path::new(&path))
            .with_context(|| format!("cannot read config {path}"))?;
        Self::parse(&text).with_context(|| format!("bad config {path}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXAMPLE: &str = include_str!("../deck.example.toml");

    #[test]
    fn parses_example() {
        let c = Config::parse(EXAMPLE).unwrap();
        assert_eq!(c.listen.to_string(), "127.0.0.1:8790");
        assert_eq!(c.trusted_proxies.len(), 1);
        assert!(c.allowed_users.is_empty());
    }

    #[test]
    fn rejects_unknown_key() {
        assert!(Config::parse(&format!("bogus = 1\n{EXAMPLE}")).is_err());
    }

    #[test]
    fn rejects_bad_listen() {
        let text = EXAMPLE.replace("127.0.0.1:8790", "not-an-address");
        assert!(Config::parse(&text).is_err());
    }
}
