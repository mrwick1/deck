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
    /// Opt-in shared Gmail polling for the bot of `[vm]`. Missing means no poll and no wakeup.
    pub gmail: Option<Gmail>,
}

/// One browser worker checks every account in turn through one shared saved profile.
/// Account keys are opaque names. The address of an account stays in the guest secret named by `user_secret`.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Gmail {
    /// Saved browser profile name. It holds all accounts through the Gmail account switcher.
    #[serde(default = "default_gmail_profile")]
    pub profile: String,
    /// CSS selector of the one control whose label names the signed-in account.
    pub account_control_selector: String,
    /// CSS selector of the visible search box. Its value must equal the intended query.
    pub search_box_selector: String,
    /// Time from the start of one poll to the start of the next.
    #[serde(default = "default_gmail_interval")]
    pub interval_secs: u64,
    /// Look-back of the first scan of an account.
    #[serde(default = "default_gmail_initial")]
    pub initial_hours: u64,
    /// Seconds subtracted from the last scan start, so a late-indexed message stays inside the window.
    #[serde(default = "default_gmail_overlap")]
    pub overlap_secs: u64,
    /// Result pages read for one account.
    #[serde(default = "default_gmail_pages")]
    pub max_pages: u32,
    /// Longest time for one account, with every read, retry, navigation and recheck.
    #[serde(default = "default_gmail_scan")]
    pub max_scan_secs: u64,
    /// Collect events only. New events stay unprocessed until a run without this flag batches them.
    #[serde(default)]
    pub poll_only: bool,
    pub accounts: Vec<GmailAccount>,
    /// Opt-in daily summary. Missing means no triage and no summary.
    #[serde(default)]
    pub summary: Option<GmailSummary>,
}

/// The daily summary of all accounts. See docs/gmail-morning-summary.md.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GmailSummary {
    /// Local time of day, `HH:MM`, after which the day's summary is due.
    #[serde(default = "default_summary_time")]
    pub time: String,
    /// `UTC`, `Asia/Kolkata` or a fixed offset such as `+05:30`. The machine zone is never read.
    #[serde(default = "default_summary_zone")]
    pub timezone: String,
    /// Threads read and triaged in one summary run. The rest wait for the next run.
    #[serde(default = "default_summary_threads")]
    pub max_threads_per_run: u32,
    #[serde(default = "default_summary_batch")]
    pub batch_size: u32,
    /// Characters of one thread passed to the model.
    #[serde(default = "default_summary_chars")]
    pub max_read_chars: u32,
    /// Longest time of the summary phase of one poll.
    #[serde(default = "default_summary_run")]
    pub max_run_secs: u64,
    /// Label writes. Missing means labels are off.
    #[serde(default)]
    pub labels: Option<GmailLabels>,
    /// Run the label stage on every poll after the day's delivery too, so a label that was not written does not wait for the next day.
    #[serde(default)]
    pub label_catchup: bool,
    /// Mailbox cleanup: topic labels, moves to Trash and the backlog walk. Missing means none. See D83.
    #[serde(default)]
    pub cleanup: Option<GmailCleanup>,
}

/// The owner-approved cleanup of the mailbox. The trash step is host text from the private config, never from a model.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GmailCleanup {
    /// One bare step text that moves the selected thread to Trash. Alternatives are split by a line of three dashes.
    pub trash_step: String,
    /// Longest time of the cleanup pass of one poll.
    #[serde(default = "default_cleanup_run")]
    pub max_run_secs: u64,
    /// Threads triaged in one pass. New mail ranks before the backlog.
    #[serde(default = "default_cleanup_triage")]
    pub triage_per_pass: u32,
    /// Moves to Trash in one pass.
    #[serde(default = "default_cleanup_trash")]
    pub trash_per_pass: u32,
    /// Topic labels written in one pass.
    #[serde(default = "default_cleanup_topics")]
    pub topics_per_pass: u32,
    /// New dynamic topics, and new Gmail labels, in one pass (D106).
    #[serde(default = "default_topics_new")]
    pub topics_new_per_pass: u32,
    /// Topics in the registry, the four fixed ones included. A new topic beyond this goes to review.
    #[serde(default = "default_topics_cap")]
    pub topics_registry_cap: u32,
    /// Walk the older inbox, one page at a time. Off means the window scan only.
    #[serde(default = "default_true")]
    pub backlog: bool,
    /// Pages read for each account in one pass.
    #[serde(default = "default_cleanup_pages")]
    pub backlog_pages_per_pass: u32,
    /// The walk reads no more while this many backlog rows wait for triage.
    #[serde(default = "default_cleanup_queue")]
    pub backlog_queue_cap: u32,
    /// Extra search terms of the walk. Letters, digits, `-`, `_`, `:` and spaces only. Leave it empty: `-label:deck` excludes nothing, see docs/gmail-cleanup.md.
    #[serde(default)]
    pub backlog_exclude: String,
}

fn default_true() -> bool {
    true
}
fn default_cleanup_run() -> u64 {
    600
}
fn default_cleanup_triage() -> u32 {
    10
}
fn default_cleanup_trash() -> u32 {
    5
}
fn default_cleanup_topics() -> u32 {
    3
}
fn default_topics_new() -> u32 {
    2
}
fn default_topics_cap() -> u32 {
    30
}
fn default_cleanup_pages() -> u32 {
    1
}
fn default_cleanup_queue() -> u32 {
    20
}

/// Words that a trash step must not hold: a write that is not a move to Trash.
const FORBIDDEN_TRASH_WORDS: [&str; 10] = [
    "archive",
    "spam",
    "send",
    "mute",
    "unread",
    "permanent",
    "forever",
    "empty",
    "report",
    "unsubscribe",
];

/// The browser contract of the label write. It comes from a live check, never from code.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GmailLabels {
    /// Selector of the visible elements of an open thread whose text is one applied label name.
    pub chips_selector: String,
    pub steps: GmailLabelSteps,
    /// Steps of the dynamic topic labels (D106). Both hold `{label}` inside a quoted argument. The host puts the checked
    /// label name there and nothing else. `topic_step` applies a label that exists. `topic_create_step` creates it from
    /// the label menu and applies it to the selected thread. Without `topic_step` no dynamic topic is written.
    #[serde(default)]
    pub topic_step: Option<String>,
    #[serde(default)]
    pub topic_create_step: Option<String>,
}

/// The place of the label name in a topic step.
pub const TOPIC_PLACEHOLDER: &str = "{label}";

/// Put the label name into a topic step. The name must be a checked label: `Deck/`, then letters, digits, spaces and
/// hyphens. Any other name gives `None`, so no text of a model reaches a step unchecked.
pub fn render_topic_step(template: &str, label: &str) -> Option<String> {
    let ok = label.strip_prefix("Deck/").is_some_and(|n| {
        (2..=24).contains(&n.len())
            && n.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b' ' || b == b'-')
    });
    ok.then(|| template.replace(TOPIC_PLACEHOLDER, label))
}

/// A topic step holds the placeholder at least once, and only inside a double-quoted argument of one line.
fn topic_step_ok(step: &str) -> bool {
    let low = step.to_ascii_lowercase();
    (1..=1500).contains(&step.len())
        && step
            .bytes()
            .all(|b| (0x20..0x7f).contains(&b) || b == b'\n')
        && !step.contains("${")
        && !FORBIDDEN_STEP_WORDS.iter().any(|w| low.contains(w))
        && step.contains(TOPIC_PLACEHOLDER)
        && step.lines().all(|line| {
            line.match_indices(TOPIC_PLACEHOLDER)
                .all(|(at, _)| line[..at].matches('"').count() % 2 == 1 && line[at..].contains('"'))
        })
        && !step.replace(TOPIC_PLACEHOLDER, "").contains(['{', '}'])
}

/// One bare step text for each label. Each applies its label and changes nothing else.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GmailLabelSteps {
    pub needs_reply: String,
    pub action: String,
    pub waiting: String,
    pub fyi: String,
    /// Steps of the topic labels. A missing step means the host never writes that label and counts the thread as not labeled.
    #[serde(default)]
    pub receipts: Option<String>,
    #[serde(default)]
    pub jobs: Option<String>,
    #[serde(default)]
    pub tech: Option<String>,
    #[serde(default)]
    pub review: Option<String>,
}

fn default_summary_time() -> String {
    "08:00".into()
}
fn default_summary_zone() -> String {
    "Asia/Kolkata".into()
}
fn default_summary_threads() -> u32 {
    30
}
fn default_summary_batch() -> u32 {
    5
}
fn default_summary_chars() -> u32 {
    6000
}
fn default_summary_run() -> u64 {
    900
}

/// Words that name a change the code never makes. A guard against a wrong step, not a proof.
const FORBIDDEN_STEP_WORDS: [&str; 7] = [
    "archive", "delete", "trash", "spam", "send", "mute", "unread",
];

impl GmailSummary {
    /// Minutes east of UTC. Valid after `validate`.
    pub fn offset_minutes(&self) -> i32 {
        parse_zone(&self.timezone).unwrap_or(0)
    }

    /// Minute of the local day. Valid after `validate`.
    pub fn minute_of_day(&self) -> u32 {
        parse_clock(&self.time).unwrap_or(0)
    }

    fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            parse_zone(&self.timezone).is_some(),
            "gmail.summary.timezone must be UTC, Asia/Kolkata or a fixed offset such as +05:30"
        );
        anyhow::ensure!(
            parse_clock(&self.time).is_some(),
            "gmail.summary.time must be HH:MM"
        );
        anyhow::ensure!(
            (1..=100).contains(&self.max_threads_per_run)
                && (1..=10).contains(&self.batch_size)
                && (500..=20_000).contains(&self.max_read_chars)
                && (60..=3600).contains(&self.max_run_secs),
            "gmail.summary limits are out of range"
        );
        if let Some(l) = &self.labels {
            anyhow::ensure!(
                selector_ok(&l.chips_selector),
                "gmail.summary.labels.chips_selector must be 1 to 200 printable ASCII characters without ${{"
            );
            for step in [
                &l.steps.needs_reply,
                &l.steps.action,
                &l.steps.waiting,
                &l.steps.fyi,
            ]
            .into_iter()
            .chain(
                [
                    &l.steps.receipts,
                    &l.steps.jobs,
                    &l.steps.tech,
                    &l.steps.review,
                ]
                .into_iter()
                .flatten(),
            ) {
                let low = step.to_ascii_lowercase();
                anyhow::ensure!(
                    (1..=500).contains(&step.len())
                        && step
                            .bytes()
                            .all(|b| (0x20..0x7f).contains(&b) || b == b'\n')
                        && !step.contains("${")
                        && !FORBIDDEN_STEP_WORDS.iter().any(|w| low.contains(w)),
                    "gmail.summary.labels.steps must be short plain steps that name no archive, delete, trash, spam, send, mute or unread"
                );
            }
        }
        if let Some(l) = &self.labels {
            anyhow::ensure!(
                l.topic_create_step.is_none() || l.topic_step.is_some(),
                "gmail.summary.labels.topic_create_step needs topic_step"
            );
            for step in [&l.topic_step, &l.topic_create_step].into_iter().flatten() {
                anyhow::ensure!(
                    topic_step_ok(step),
                    "gmail.summary.labels topic steps must be short plain steps that hold {{label}} only inside a quoted argument and name no archive, delete, trash, spam, send, mute or unread"
                );
            }
        }
        if let Some(c) = &self.cleanup {
            anyhow::ensure!(
                self.labels.is_some(),
                "gmail.summary.cleanup needs gmail.summary.labels: the owner-label guard reads its chip selector"
            );
            c.validate()?;
        }
        Ok(())
    }
}

impl GmailCleanup {
    fn validate(&self) -> anyhow::Result<()> {
        let low = self.trash_step.to_ascii_lowercase();
        anyhow::ensure!(
            (1..=500).contains(&self.trash_step.len())
                && self
                    .trash_step
                    .bytes()
                    .all(|b| (0x20..0x7f).contains(&b) || b == b'\n')
                && !self.trash_step.contains("${")
                && (low.contains("delete") || low.contains("trash"))
                && !FORBIDDEN_TRASH_WORDS.iter().any(|w| low.contains(w)),
            "gmail.summary.cleanup.trash_step must be a short plain step that moves to Trash and names no archive, spam, send, mute, unread, permanent, forever, empty, report or unsubscribe"
        );
        anyhow::ensure!(
            (60..=3600).contains(&self.max_run_secs)
                && (1..=30).contains(&self.triage_per_pass)
                && (0..=20).contains(&self.trash_per_pass)
                && (0..=10).contains(&self.topics_per_pass)
                && (0..=5).contains(&self.topics_new_per_pass)
                && (5..=100).contains(&self.topics_registry_cap)
                && (1..=5).contains(&self.backlog_pages_per_pass)
                && (1..=200).contains(&self.backlog_queue_cap),
            "gmail.summary.cleanup limits are out of range"
        );
        anyhow::ensure!(
            self.backlog_exclude.len() <= 100
                && self
                    .backlog_exclude
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b':' | b' ')),
            "gmail.summary.cleanup.backlog_exclude may hold only letters, digits, - _ : and spaces"
        );
        Ok(())
    }
}

/// Minutes east of UTC for the supported zones. A zone with daylight saving is refused.
fn parse_zone(z: &str) -> Option<i32> {
    match z {
        "UTC" => return Some(0),
        "Asia/Kolkata" => return Some(330),
        _ => {}
    }
    let sign = match z.as_bytes().first()? {
        b'+' => 1,
        b'-' => -1,
        _ => return None,
    };
    let (h, m) = z[1..].split_once(':')?;
    if h.len() != 2 || m.len() != 2 {
        return None;
    }
    let (h, m): (i32, i32) = (h.parse().ok()?, m.parse().ok()?);
    ((0..=14).contains(&h) && (0..60).contains(&m)).then_some(sign * (h * 60 + m))
}

fn parse_clock(t: &str) -> Option<u32> {
    let (h, m) = t.split_once(':')?;
    if h.len() != 2 || m.len() != 2 {
        return None;
    }
    let (h, m): (u32, u32) = (h.parse().ok()?, m.parse().ok()?);
    (h < 24 && m < 60).then_some(h * 60 + m)
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GmailAccount {
    /// Opaque key of the account in the store and the status. Not an address.
    pub key: String,
    /// Position of the account in the Gmail account switcher: `/mail/u/<index>/`.
    pub index: u8,
    /// Name of the guest secret that holds the address, for example `ACCOUNT_ONE_USER`.
    pub user_secret: String,
}

fn default_gmail_profile() -> String {
    "main".into()
}
fn default_gmail_interval() -> u64 {
    120
}
fn default_gmail_initial() -> u64 {
    24
}
fn default_gmail_overlap() -> u64 {
    3600
}
fn default_gmail_pages() -> u32 {
    3
}
fn default_gmail_scan() -> u64 {
    90
}

/// A short printable ASCII selector that cannot hold a `${NAME}` placeholder.
fn selector_ok(s: &str) -> bool {
    (1..=200).contains(&s.len())
        && s.bytes().all(|b| (0x20..0x7f).contains(&b))
        && !s.contains("${")
}

impl Gmail {
    fn validate(&self) -> anyhow::Result<()> {
        use std::collections::HashSet;
        anyhow::ensure!(
            crate::executor::proto::valid_id(&self.profile),
            "gmail.profile must be 1 to 64 characters of A-Za-z0-9_-"
        );
        anyhow::ensure!(
            selector_ok(&self.account_control_selector) && selector_ok(&self.search_box_selector),
            "gmail selectors must be 1 to 200 printable ASCII characters without ${{"
        );
        anyhow::ensure!(
            (30..=3600).contains(&self.interval_secs)
                && (1..=168).contains(&self.initial_hours)
                && self.overlap_secs <= 86_400
                && (1..=10).contains(&self.max_pages)
                && (10..=600).contains(&self.max_scan_secs),
            "gmail interval, window, page and time limits are out of range"
        );
        anyhow::ensure!(
            (1..=10).contains(&self.accounts.len()),
            "gmail needs 1 to 10 accounts"
        );
        let (mut keys, mut secrets, mut indexes) = (HashSet::new(), HashSet::new(), HashSet::new());
        for a in &self.accounts {
            anyhow::ensure!(
                crate::executor::proto::valid_id(&a.key),
                "gmail account key must be 1 to 64 characters of A-Za-z0-9_-"
            );
            anyhow::ensure!(
                a.index <= 9
                    && (1..=64).contains(&a.user_secret.len())
                    && a.user_secret.starts_with(|c: char| c.is_ascii_uppercase())
                    && a.user_secret
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                "gmail account index must be 0 to 9 and user_secret an uppercase NAME"
            );
            anyhow::ensure!(
                keys.insert(&a.key) && secrets.insert(&a.user_secret) && indexes.insert(a.index),
                "gmail account keys, secrets and indexes must be unique"
            );
        }
        if let Some(s) = &self.summary {
            anyhow::ensure!(
                !self.poll_only,
                "gmail.summary cannot run in poll_only mode"
            );
            s.validate()?;
        }
        Ok(())
    }
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
        if let Some(g) = &c.gmail {
            anyhow::ensure!(c.vm.is_some(), "[gmail] needs [vm]");
            g.validate()?;
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

    const GMAIL: &str = "
[gmail]
account_control_selector = \"a[aria-label*=Account]\"
search_box_selector = \"input[name=q]\"
[[gmail.accounts]]
key = \"acct_one\"
index = 0
user_secret = \"ACCOUNT_ONE_USER\"
[[gmail.accounts]]
key = \"acct_two\"
index = 1
user_secret = \"ACCOUNT_TWO_USER\"
";

    #[test]
    fn gmail_is_opt_in_and_needs_a_bot() {
        assert!(Config::parse(EXAMPLE).unwrap().gmail.is_none());
        assert!(
            Config::parse(&format!("{EXAMPLE}{VM}{GMAIL}"))
                .unwrap()
                .gmail
                .is_some()
        );
        let e = Config::parse(&format!("{EXAMPLE}{GMAIL}")).unwrap_err();
        assert!(e.to_string().contains("needs [vm]"), "{e}");
    }

    #[test]
    fn gmail_defaults_are_the_approved_limits() {
        let g = Config::parse(&format!("{EXAMPLE}{VM}{GMAIL}"))
            .unwrap()
            .gmail
            .unwrap();
        assert_eq!(
            (
                g.profile.as_str(),
                g.interval_secs,
                g.initial_hours,
                g.overlap_secs,
                g.max_pages,
                g.max_scan_secs
            ),
            ("main", 120, 24, 3600, 3, 90)
        );
        assert_eq!(g.accounts.len(), 2);
    }

    #[test]
    fn gmail_rejects_bad_values_and_unknown_fields() {
        let bad = |from: &str, to: &str| {
            let t = format!("{EXAMPLE}{VM}{}", GMAIL.replacen(from, to, 1));
            assert!(Config::parse(&t).is_err(), "{to}");
        };
        // Duplicates.
        bad("key = \"acct_two\"", "key = \"acct_one\"");
        bad("ACCOUNT_TWO_USER", "ACCOUNT_ONE_USER");
        bad("index = 1", "index = 0");
        // Not a secret name: lowercase, an address, a password-like name or a placeholder.
        for v in ["account_two", "a@b.example", "1ACCOUNT", "A B", "${X}", ""] {
            bad("ACCOUNT_TWO_USER", v);
        }
        bad("index = 1", "index = 10");
        for k in ["", "a b", "a@b.example", &"k".repeat(65)] {
            bad("acct_two", k);
        }
        // Selectors.
        bad("input[name=q]", "");
        bad("input[name=q]", "input${NAME}");
        bad("input[name=q]", "input\\u0001");
        bad("a[aria-label*=Account]", &"x".repeat(201));
        // Unknown fields, including anything that looks like script.
        bad(
            "[[gmail.accounts]]\nkey = \"acct_one\"",
            "script = \"1\"\n[[gmail.accounts]]\nkey = \"acct_one\"",
        );
        bad("index = 0", "index = 0\npassword = \"x\"");
        // Limits.
        for (k, v) in [
            ("interval_secs", "5"),
            ("interval_secs", "100000"),
            ("initial_hours", "0"),
            ("initial_hours", "1000"),
            ("overlap_secs", "100000"),
            ("max_pages", "0"),
            ("max_pages", "99"),
            ("max_scan_secs", "1"),
            ("max_scan_secs", "100000"),
            ("profile", "\"a b\""),
        ] {
            bad(
                "search_box_selector",
                &format!("{k} = {v}\nsearch_box_selector"),
            );
        }
        // No accounts.
        let none = format!(
            "{EXAMPLE}{VM}\n[gmail]\naccount_control_selector = \"a\"\nsearch_box_selector = \"b\"\naccounts = []\n"
        );
        assert!(Config::parse(&none).is_err());
    }

    const SUMMARY: &str = "
[gmail.summary]
";

    #[test]
    fn the_summary_is_opt_in_and_defaults_to_eight_in_kolkata() {
        let g = Config::parse(&format!("{EXAMPLE}{VM}{GMAIL}"))
            .unwrap()
            .gmail
            .unwrap();
        assert!(g.summary.is_none());
        let g = Config::parse(&format!("{EXAMPLE}{VM}{GMAIL}{SUMMARY}"))
            .unwrap()
            .gmail
            .unwrap();
        let s = g.summary.unwrap();
        assert_eq!((s.offset_minutes(), s.minute_of_day()), (330, 480));
        assert_eq!(
            (
                s.max_threads_per_run,
                s.batch_size,
                s.max_read_chars,
                s.max_run_secs
            ),
            (30, 5, 6000, 900)
        );
        assert!(s.labels.is_none());
    }

    #[test]
    fn gmail_poll_only_refuses_summary_configuration() {
        let gmail = GMAIL.replace("[gmail]", "[gmail]\npoll_only = true");
        let config = format!("{EXAMPLE}{VM}{gmail}{SUMMARY}");
        assert!(Config::parse(&config).is_err());
    }

    #[test]
    fn the_summary_zone_is_a_fixed_offset_and_never_the_machine_zone() {
        let ok = |tz: &str, off: i32| {
            let t = format!(
                "{EXAMPLE}{VM}{GMAIL}[gmail.summary]\ntimezone = \"{tz}\"\ntime = \"23:59\"\n"
            );
            let s = Config::parse(&t).unwrap().gmail.unwrap().summary.unwrap();
            assert_eq!((s.offset_minutes(), s.minute_of_day()), (off, 1439), "{tz}");
        };
        ok("UTC", 0);
        ok("Asia/Kolkata", 330);
        ok("+05:30", 330);
        ok("-08:00", -480);
        for tz in [
            "America/New_York",
            "Europe/London",
            "local",
            "+25:00",
            "+5:30",
            "",
            "+05:60",
        ] {
            let t = format!("{EXAMPLE}{VM}{GMAIL}[gmail.summary]\ntimezone = \"{tz}\"\n");
            assert!(Config::parse(&t).is_err(), "{tz}");
        }
        for time in ["8:00", "24:00", "08:60", "0800", "", "08:00:00"] {
            let t = format!("{EXAMPLE}{VM}{GMAIL}[gmail.summary]\ntime = \"{time}\"\n");
            assert!(Config::parse(&t).is_err(), "{time}");
        }
    }

    const LABELS: &str = "
[gmail.summary.labels]
chips_selector = \"span.chip\"
[gmail.summary.labels.steps]
needs_reply = \"click css=\\\"#one\\\"\"
action = \"click css=\\\"#two\\\"\"
waiting = \"click css=\\\"#three\\\"\"
fyi = \"click css=\\\"#four\\\"\"
";

    #[test]
    fn label_steps_are_complete_and_never_name_a_destructive_action() {
        let base = format!("{EXAMPLE}{VM}{GMAIL}{SUMMARY}{LABELS}");
        let s = Config::parse(&base)
            .unwrap()
            .gmail
            .unwrap()
            .summary
            .unwrap();
        assert!(s.labels.is_some());
        for bad in ["#one", "#four"] {
            for word in [
                "archive", "Delete", "trash", "spam", "send", "mute", "unread", "${X}",
            ] {
                let t = base.replacen(
                    &format!("css=\\\"{bad}\\\""),
                    &format!("css=\\\"{bad}\\\" {word}"),
                    1,
                );
                assert!(Config::parse(&t).is_err(), "{bad} {word}");
            }
        }
        let empty = base.replacen(
            "needs_reply = \"click css=\\\"#one\\\"\"",
            "needs_reply = \"\"",
            1,
        );
        assert!(Config::parse(&empty).is_err());
        // A missing step, or a chip selector with a placeholder.
        assert!(Config::parse(&base.replace("fyi = ", "other = ")).is_err());
        assert!(Config::parse(&base.replace("span.chip", "span${X}")).is_err());
    }

    #[test]
    fn topic_steps_hold_the_label_only_inside_a_quoted_argument() {
        let with = |apply: &str, create: Option<&str>| {
            let mut extra = format!("topic_step = {apply}\n");
            if let Some(c) = create {
                extra.push_str(&format!("topic_create_step = {c}\n"));
            }
            format!(
                "{EXAMPLE}{VM}{GMAIL}{SUMMARY}{}",
                LABELS.replacen(
                    "chips_selector = \"span.chip\"\n",
                    &format!("chips_selector = \"span.chip\"\n{extra}"),
                    1
                )
            )
        };
        let apply = r#""click role=menuitemcheckbox name=\"{label}\"""#;
        let create = r#""click role=menuitem name=\"Create new\"\ntype role=textbox name=\"Name\" \"{label}\"\nclick role=button name=\"Create\"""#;
        let l = Config::parse(&with(apply, Some(create)))
            .unwrap()
            .gmail
            .unwrap()
            .summary
            .unwrap()
            .labels
            .unwrap();
        assert!(l.topic_step.is_some() && l.topic_create_step.is_some());
        // Off by default.
        assert!(
            Config::parse(&format!("{EXAMPLE}{VM}{GMAIL}{SUMMARY}{LABELS}"))
                .unwrap()
                .gmail
                .unwrap()
                .summary
                .unwrap()
                .labels
                .unwrap()
                .topic_step
                .is_none()
        );
        for bad in [
            r#""click role=menuitemcheckbox name={label}""#,
            r#""click css=\"x\" {label}""#,
            r#""click role=menuitemcheckbox name=\"x\"""#,
            r#""click role=menuitemcheckbox name=\"{label}\" css={label}""#,
            r#""click name=\"{label}\" delete""#,
            r#""click name=\"${label}\"""#,
            r#""click name=\"{label}{x}\"""#,
            r#""""#,
        ] {
            assert!(Config::parse(&with(bad, None)).is_err(), "{bad}");
        }
        // A create step needs an apply step.
        let t = with(apply, None).replace("topic_step = ", "topic_create_step = ");
        assert!(Config::parse(&t).is_err());
    }

    #[test]
    fn only_a_checked_label_reaches_a_step() {
        let t = r#"click role=menuitemcheckbox name="{label}""#;
        assert_eq!(
            render_topic_step(t, "Deck/Travel Plans").as_deref(),
            Some("click role=menuitemcheckbox name=\"Deck/Travel Plans\"")
        );
        for bad in [
            "Travel",
            "Deck/",
            "Deck/a\"b",
            "Deck/a\nb",
            "Deck/{label}",
            "Deck/a/b",
            "Deck/a\\b",
            "Deck/${X}",
            "Deck/ok; click",
        ] {
            assert_eq!(render_topic_step(t, bad), None, "{bad:?}");
        }
    }

    #[test]
    fn the_topic_limits_have_safe_defaults_and_ranges() {
        let base = format!("{EXAMPLE}{VM}{GMAIL}{SUMMARY}{LABELS}{CLEANUP}");
        let c = Config::parse(&base)
            .unwrap()
            .gmail
            .unwrap()
            .summary
            .unwrap()
            .cleanup
            .unwrap();
        assert_eq!((c.topics_new_per_pass, c.topics_registry_cap), (2, 30));
        for bad in [
            "topics_new_per_pass = 6",
            "topics_registry_cap = 4",
            "topics_registry_cap = 101",
        ] {
            assert!(Config::parse(&format!("{base}{bad}\n")).is_err(), "{bad}");
        }
        assert!(Config::parse(&format!("{base}topics_new_per_pass = 0\n")).is_ok());
    }

    const CLEANUP: &str = "
[gmail.summary.cleanup]
trash_step = \"click role=button name=\\\"Delete\\\"\"
";

    #[test]
    fn the_cleanup_needs_labels_and_a_trash_step_that_moves_to_trash_and_nothing_else() {
        let base = format!("{EXAMPLE}{VM}{GMAIL}{SUMMARY}{LABELS}{CLEANUP}");
        let s = Config::parse(&base)
            .unwrap()
            .gmail
            .unwrap()
            .summary
            .unwrap();
        let c = s.cleanup.unwrap();
        assert_eq!(
            (c.trash_per_pass, c.triage_per_pass, c.backlog),
            (5, 10, true)
        );
        // The cleanup reads the owner-label chips, so it needs the label section.
        assert!(Config::parse(&format!("{EXAMPLE}{VM}{GMAIL}{SUMMARY}{CLEANUP}")).is_err());
        for word in [
            "archive",
            "spam",
            "send",
            "mute",
            "unread",
            "permanent",
            "forever",
            "empty",
            "report",
            "unsubscribe",
            "${X}",
        ] {
            let t = base.replace("Delete", &format!("Delete {word}"));
            assert!(Config::parse(&t).is_err(), "{word}");
        }
        // A step that names no move to Trash is refused too.
        assert!(Config::parse(&base.replace("Delete", "Label")).is_err());
        // Limits and unknown fields.
        for (k, v) in [
            ("max_run_secs", "5"),
            ("triage_per_pass", "0"),
            ("trash_per_pass", "21"),
            ("topics_per_pass", "11"),
            ("backlog_pages_per_pass", "6"),
            ("backlog_queue_cap", "0"),
            ("backlog_exclude", "\"a;b\""),
            ("backlog_exclude", "\"-label:x$\""),
            ("permanent", "true"),
        ] {
            let t = format!("{base}{k} = {v}\n");
            assert!(Config::parse(&t).is_err(), "{k}");
        }
        let ok = format!("{base}backlog_exclude = \"-label:deck\"\n");
        assert!(Config::parse(&ok).is_ok());
    }

    #[test]
    fn the_label_steps_still_refuse_the_trash_words_and_the_topic_steps_follow_the_same_rule() {
        let with_topics = format!(
            "{EXAMPLE}{VM}{GMAIL}{SUMMARY}{LABELS}receipts = \"click css=\\\"#r\\\"\"\njobs = \"click css=\\\"#j\\\"\"\n"
        );
        assert!(Config::parse(&with_topics).is_ok());
        let bad = with_topics.replace("#j", "#j delete");
        assert!(Config::parse(&bad).is_err());
    }

    #[test]
    fn the_summary_rejects_out_of_range_limits_and_unknown_fields() {
        for (k, v) in [
            ("max_threads_per_run", "0"),
            ("max_threads_per_run", "101"),
            ("batch_size", "0"),
            ("batch_size", "11"),
            ("max_read_chars", "10"),
            ("max_read_chars", "100000"),
            ("max_run_secs", "5"),
            ("max_run_secs", "100000"),
            ("password", "\"x\""),
        ] {
            let t = format!("{EXAMPLE}{VM}{GMAIL}[gmail.summary]\n{k} = {v}\n");
            assert!(Config::parse(&t).is_err(), "{k}");
        }
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
