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
    /// The desktop computer of the bot. Without it, the agent uses a local bare as before.
    pub vm: Option<Vm>,
    /// Synthetic event intake for the bot of `[vm]`.
    pub events: Option<Events>,
    /// Explicit model accounting for the configured bot. Missing leaves legacy mode unchanged.
    pub accounting: Option<Accounting>,
    pub provisioning: Option<Provisioning>,
}

/// Host-owned provisioning defaults. The owner reviews each bot's budget before creation.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Provisioning {
    pub base: PathBuf,
    pub runtime_root: PathBuf,
    pub template: Vm,
    pub resources: crate::bot::setup::Resources,
    pub limits: crate::bot::setup::Limits,
    pub accounting: Accounting,
    pub max_bots: usize,
}

/// Owner-supplied accounting. Monetary values use integer units of currency/exponent.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Accounting {
    pub currency: String,
    pub exponent: u8,
    pub period: String,
    pub policy_version: String,
    pub ceiling: i64,
    pub allowance: i64,
    pub increase_increment: i64,
    pub price_version: String,
    pub input_per_million: i64,
    pub output_per_million: i64,
    pub extra_per_call: i64,
    /// Provider's maximum billable input, including hidden request overhead.
    pub max_input_tokens: u64,
    pub max_input_bytes: usize,
    pub max_output_tokens: u32,
    pub max_response_bytes: usize,
    pub completion_token_field: bool,
    pub request_usage: bool,
}

impl Accounting {
    pub fn validate(&self) -> anyhow::Result<()> {
        let a = self;
        crate::accounting::Unit::new(&a.currency, a.exponent)?;
        anyhow::ensure!(
            a.exponent <= 9
                && !a.period.trim().is_empty()
                && !a.policy_version.trim().is_empty()
                && !a.price_version.trim().is_empty(),
            "accounting unit, period and versions must be explicit"
        );
        anyhow::ensure!(
            a.ceiling >= 0
                && a.allowance >= 0
                && a.allowance <= a.ceiling
                && a.increase_increment > 0,
            "accounting allowance must fit the ceiling and increase_increment must be positive"
        );
        anyhow::ensure!(
            a.input_per_million >= 0 && a.output_per_million >= 0 && a.extra_per_call >= 0,
            "accounting prices must be non-negative"
        );
        anyhow::ensure!(
            a.max_input_tokens > 0
                && a.max_input_bytes > 0
                && a.max_output_tokens > 0
                && a.max_response_bytes > 0,
            "accounting request bounds must be positive"
        );
        Ok(())
    }
}

pub const VM_CONFIG_VERSION: u32 = 1;

/// One bot with one private disk. The runtime directory holds the disk, sockets and saved state.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Vm {
    /// Must be 1. A later layout gets a new number, so an old file never starts on a new layout.
    pub version: u32,
    pub bot_id: String,
    pub runtime_dir: PathBuf,
    /// `vmproto.py` of `scripts/vm-prototype`.
    pub harness: PathBuf,
    #[serde(default = "default_python")]
    pub python: String,
    #[serde(default = "default_fixture_port")]
    pub fixture_port: u16,
    /// Allow outbound guest networking. False keeps the fixture-only network.
    #[serde(default)]
    pub internet: bool,
    /// Seconds without work before the computer saves itself.
    #[serde(default = "default_idle")]
    pub idle_secs: u64,
    /// Longest time for one shell command.
    #[serde(default = "default_command")]
    pub command_secs: u64,
    /// Longest time for one start, restore or save.
    #[serde(default = "default_lifecycle")]
    pub lifecycle_secs: u64,
    /// Saved-state codec: `none`, `lz4` or `zstd-N`.
    #[serde(default = "default_codec")]
    pub state_codec: String,
    /// Time between screen captures while a viewer watches.
    #[serde(default = "default_screen")]
    pub screen_interval_ms: u64,
}

fn default_python() -> String {
    "python3".into()
}
fn default_fixture_port() -> u16 {
    18080
}
fn default_idle() -> u64 {
    120
}
fn default_command() -> u64 {
    120
}
fn default_lifecycle() -> u64 {
    120
}
fn default_codec() -> String {
    "none".into()
}
fn default_screen() -> u64 {
    1000
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Events {
    /// Name of the environment variable with the bearer token. Never the token itself.
    pub token_env: String,
    /// Quiet time after the last event before a batch closes.
    #[serde(default = "default_window")]
    pub batch_window_ms: u64,
    /// Longest time from the first event of a batch to its close, so a steady stream cannot postpone work.
    #[serde(default = "default_max_delay")]
    pub batch_max_delay_ms: u64,
}

fn default_window() -> u64 {
    2000
}
fn default_max_delay() -> u64 {
    10_000
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
    #[allow(dead_code)] // Unused until small tasks land.
    pub small_model: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bare {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    /// A file of `NAME=value` lines. The model types `${NAME}`; bare fills in the value and hides it in every reply.
    #[serde(default)]
    pub secrets_file: Option<PathBuf>,
    /// A folder with one `<name>.state` file per browser session, so a login survives restarts. Holds live credentials: keep it outside the repository.
    #[serde(default)]
    pub state_dir: Option<PathBuf>,
}

impl Config {
    pub fn parse(text: &str) -> anyhow::Result<Self> {
        let c: Self = toml::from_str(text)?;
        if let Some(vm) = &c.vm {
            anyhow::ensure!(
                vm.version == VM_CONFIG_VERSION,
                "vm.version {} is not supported: use {VM_CONFIG_VERSION}",
                vm.version
            );
            anyhow::ensure!(
                crate::executor::proto::valid_id(&vm.bot_id),
                "vm.bot_id must be 1 to 64 characters of A-Za-z0-9_-"
            );
            let codec_ok = vm.state_codec == "none"
                || vm.state_codec == "lz4"
                || vm
                    .state_codec
                    .strip_prefix("zstd-")
                    .is_some_and(|l| (1..=19).contains(&l.parse::<u8>().unwrap_or(0)));
            anyhow::ensure!(codec_ok, "vm.state_codec must be none, lz4 or zstd-N");
            anyhow::ensure!(
                vm.idle_secs >= 1 && vm.command_secs >= 1,
                "vm times must be at least 1"
            );
            anyhow::ensure!(
                vm.screen_interval_ms >= 100,
                "vm.screen_interval_ms must be at least 100"
            );
        }
        if let Some(a) = &c.accounting {
            anyhow::ensure!(c.vm.is_some(), "[accounting] needs [vm]");
            a.validate()?;
        }
        if let Some(p) = &c.provisioning {
            p.accounting.validate()?;
            anyhow::ensure!(p.max_bots > 0, "provisioning.max_bots must be positive");
            anyhow::ensure!(
                p.template.version == VM_CONFIG_VERSION,
                "provisioning VM template version is unsupported"
            );
            anyhow::ensure!(
                p.resources.cpus == 2 && p.resources.memory_mib == 2048 && p.resources.disk_gib > 0,
                "the current harness supports 2 CPUs and 2048 MiB; disk size must match the frozen base"
            );
            anyhow::ensure!(
                p.resources.cpus <= p.limits.max_cpus
                    && p.resources.memory_mib <= p.limits.max_memory_mib
                    && p.resources.disk_gib <= p.limits.max_disk_gib,
                "provisioning defaults exceed configured capacity"
            );
            anyhow::ensure!(
                p.template.lifecycle_secs > 0
                    && p.template.command_secs > 0
                    && p.template.idle_secs > 0
                    && p.template.screen_interval_ms >= 100,
                "provisioning lifecycle limits are invalid"
            );
        }
        if let Some(e) = &c.events {
            anyhow::ensure!(c.vm.is_some(), "[events] needs [vm]");
            anyhow::ensure!(
                e.batch_window_ms >= 1 && e.batch_max_delay_ms >= e.batch_window_ms,
                "events.batch_max_delay_ms must be at least events.batch_window_ms"
            );
        }
        Ok(c)
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
    fn bare_state_dir_is_accepted() {
        let c = Config::parse(EXAMPLE).unwrap();
        assert!(c.bare.state_dir.is_none());
        let text = EXAMPLE.replace("[bare]\n", "[bare]\nstate_dir = \"/var/lib/deck/state\"\n");
        let c = Config::parse(&text).unwrap();
        assert_eq!(c.bare.state_dir, Some(PathBuf::from("/var/lib/deck/state")));
    }

    #[test]
    fn workspace_and_max_steps_override() {
        let text =
            format!("workspace_dir = \"/w\"\n{EXAMPLE}").replace("max_steps = 30", "max_steps = 5");
        let c = Config::parse(&text).unwrap();
        assert_eq!(c.workspace(), PathBuf::from("/w"));
        assert_eq!(c.agent.max_steps, 5);
    }

    const VM: &str = "
[vm]
version = 1
bot_id = \"main\"
runtime_dir = \"/rt\"
harness = \"/h/vmproto.py\"
";

    #[test]
    fn vm_section_is_optional_and_versioned() {
        assert!(Config::parse(EXAMPLE).unwrap().vm.is_none());
        let c = Config::parse(&format!("{EXAMPLE}{VM}")).unwrap();
        let vm = c.vm.unwrap();
        assert!(!vm.internet);
        let enabled = Config::parse(&format!("{EXAMPLE}{VM}internet = true\n")).unwrap();
        assert!(enabled.vm.unwrap().internet);
        assert_eq!(
            (vm.idle_secs, vm.fixture_port, vm.state_codec.as_str()),
            (120, 18080, "none")
        );
        let bad = format!("{EXAMPLE}{}", VM.replace("version = 1", "version = 2"));
        assert!(
            Config::parse(&bad)
                .unwrap_err()
                .to_string()
                .contains("not supported")
        );
    }

    #[test]
    fn vm_values_that_could_reach_a_command_line_are_checked() {
        for (from, to) in [
            ("\"main\"", "\"a b; rm\""),
            ("version = 1", "version = 1\nstate_codec = \"zstd-3 -o x\""),
        ] {
            assert!(
                Config::parse(&format!("{EXAMPLE}{}", VM.replace(from, to))).is_err(),
                "{to}"
            );
        }
    }

    #[test]
    fn events_need_a_bot_and_a_sane_window() {
        let ev = "\n[events]\ntoken_env = \"T\"\nbatch_window_ms = 500\nbatch_max_delay_ms = 100\n";
        assert!(Config::parse(&format!("{EXAMPLE}{ev}")).is_err());
        let ok = ev.replace("= 100", "= 5000");
        assert!(
            Config::parse(&format!("{EXAMPLE}{ok}")).is_err(),
            "events without vm"
        );
        assert!(Config::parse(&format!("{EXAMPLE}{VM}{ok}")).is_ok());
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
