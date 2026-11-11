use std::net::{IpAddr, SocketAddr};
use std::path::{Path, PathBuf};

use anyhow::Context;
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub listen: SocketAddr,
    pub data_dir: PathBuf,
    /// Folder the agent may send files from. Defaults to `data_dir/workspace`.
    pub workspace_dir: Option<PathBuf>,
    pub trusted_proxies: Vec<IpAddr>,
    #[serde(default)]
    pub allowed_users: Vec<String>,
    pub model: Model,
    pub bare: Bare,
    #[serde(default)]
    pub agent: Agent,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Agent {
    #[serde(default = "default_max_steps")]
    pub max_steps: usize,
}

fn default_max_steps() -> usize {
    30
}

impl Default for Agent {
    fn default() -> Self {
        Self {
            max_steps: default_max_steps(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    pub base_url: String,
    pub api_key_env: String,
    pub agent_model: String,
    pub small_model: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bare {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
}

impl Config {
    pub fn parse(text: &str) -> anyhow::Result<Self> {
        Ok(toml::from_str(text)?)
    }

    pub fn workspace(&self) -> PathBuf {
        self.workspace_dir
            .clone()
            .unwrap_or_else(|| self.data_dir.join("workspace"))
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
        assert_eq!(c.agent.max_steps, 30);
        assert_eq!(c.workspace(), PathBuf::from("./data/workspace"));
    }

    #[test]
    fn workspace_and_max_steps_override() {
        let text =
            format!("workspace_dir = \"/w\"\n{EXAMPLE}").replace("max_steps = 30", "max_steps = 5");
        let c = Config::parse(&text).unwrap();
        assert_eq!(c.workspace(), PathBuf::from("/w"));
        assert_eq!(c.agent.max_steps, 5);
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
