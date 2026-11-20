//! Dynamic topic labels of the Gmail cleanup. See D106 and docs/gmail-cleanup.md.
//!
//! The model proposes a short topic. The host checks its shape, folds it to a canonical registry topic and builds the
//! label name `Deck/<Name>`. A topic is data. It never reaches a selector, a program or a step except as the checked
//! name inside one quoted argument. The topic never changes the kind, so it never authorizes a move to Trash.

use rusqlite::{OptionalExtension, params};

use crate::store::Store;

/// The label namespace of the host. The owner's own labels are never read, renamed or changed.
pub const NAMESPACE: &str = "Deck/";
const MIN_CHARS: usize = 2;
const MAX_CHARS: usize = 24;
const MAX_WORDS: usize = 3;
const MAX_DIGITS: usize = 2;

/// The four fixed topics of D83, as registry seeds: (key, name).
pub const SEEDS: [(&str, &str); 4] = [
    ("receipts", "Receipts"),
    ("jobs", "Jobs"),
    ("tech", "Software and AI"),
    ("review", "Review"),
];

/// Names that are not topics: categories, controls, catch-alls and write words. Matched as whole phrases.
const RESERVED_PHRASES: [&str; 22] = [
    "needs reply",
    "needs a reply",
    "reply needed",
    "awaiting reply",
    "all mail",
    "other",
    "others",
    "misc",
    "miscellaneous",
    "general",
    "unknown",
    "unsorted",
    "uncategorized",
    "none",
    "null",
    "undefined",
    "topic",
    "topics",
    "email",
    "emails",
    "mail",
    "message",
];

/// Words that no topic may hold: write words of the fixed steps, the fixed labels and the Gmail system labels.
const RESERVED_WORDS: [&str; 24] = [
    "trash",
    "spam",
    "delete",
    "deleted",
    "archive",
    "archived",
    "send",
    "sent",
    "mute",
    "unread",
    "permanent",
    "forever",
    "empty",
    "report",
    "unsubscribe",
    "inbox",
    "drafts",
    "starred",
    "important",
    "review",
    "deck",
    "fyi",
    "action",
    "waiting",
];

/// A checked topic: the registry key and the display name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Name {
    /// Lower case, single spaces. The registry key of a new topic.
    pub key: String,
    /// The display name, for example `Travel`.
    pub name: String,
}

impl Name {
    /// The Gmail label name.
    pub fn label(&self) -> String {
        label_of(&self.name)
    }
}

pub fn label_of(name: &str) -> String {
    format!("{NAMESPACE}{name}")
}

/// A key for matching only. It ignores one final `s`, so `Receipt` and `Receipts` are one topic. Nothing else is merged.
pub fn fold(key: &str) -> String {
    let words: Vec<String> = key
        .split(' ')
        .map(|w| {
            if w.len() > 3 && w.ends_with('s') && !w.ends_with("ss") {
                w[..w.len() - 1].to_owned()
            } else {
                w.to_owned()
            }
        })
        .collect();
    words.join(" ")
}

/// Check a topic that the model proposed. Only plain words pass: no separator, no control character, no address, no
/// identifier, no number of a mail, and no word that names a fixed label, a control or a write.
pub fn parse_name(raw: &str) -> Result<Name, &'static str> {
    // A tab, a line break or any control character is no part of a name. Only plain spaces collapse.
    if raw
        .chars()
        .any(|c| c.is_control() || (c.is_whitespace() && c != ' '))
    {
        return Err("characters");
    }
    let collapsed = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    if !(MIN_CHARS..=MAX_CHARS).contains(&collapsed.chars().count()) {
        return Err("length");
    }
    if !collapsed
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == ' ' || c == '-')
    {
        return Err("characters");
    }
    if !collapsed.starts_with(|c: char| c.is_ascii_alphabetic()) || collapsed.ends_with('-') {
        return Err("shape");
    }
    if collapsed.split(' ').count() > MAX_WORDS || collapsed.contains("--") {
        return Err("shape");
    }
    if collapsed.chars().filter(char::is_ascii_digit).count() > MAX_DIGITS {
        return Err("digits");
    }
    let key = collapsed.to_ascii_lowercase();
    if RESERVED_PHRASES.contains(&key.as_str())
        || key.split([' ', '-']).any(|w| RESERVED_WORDS.contains(&w))
    {
        return Err("reserved");
    }
    let name = collapsed
        .split(' ')
        .map(|w| {
            if w.len() >= 2 && w.chars().all(|c| c.is_ascii_uppercase()) {
                w.to_owned()
            } else {
                let lower = w.to_ascii_lowercase();
                let mut cs = lower.chars();
                cs.next()
                    .map(|f| f.to_ascii_uppercase().to_string() + cs.as_str())
                    .unwrap_or_default()
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    Ok(Name { key, name })
}

/// A topic of the registry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub key: String,
    pub name: String,
    pub seed: bool,
}

/// What the host decided for a proposed topic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Choice {
    /// No topic was proposed.
    None,
    /// A registry topic. `new` is true when this call added it.
    Topic { key: String, new: bool },
    /// The proposal failed the shape check.
    Invalid,
    /// A new topic would pass a limit of the pass or of the registry.
    Limit,
}

/// The state of a topic on one account.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AccountState {
    /// A creation step may have run. The next write looks first.
    Creating,
    /// A readback showed the label on a thread of this account.
    Verified,
}

impl AccountState {
    fn code(self) -> &'static str {
        match self {
            Self::Creating => "creating",
            Self::Verified => "verified",
        }
    }
}

/// Counts for the status. Fixed words and numbers only.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TopicCounts {
    pub registry: u32,
    pub dynamic: u32,
    pub verified: u32,
    pub creating: u32,
    /// Labels that a creation step made.
    pub created: u32,
    /// Rows that went to review because of a topic limit or a bad proposal.
    pub overflow: u32,
    pub invalid: u32,
}

impl Store {
    /// The registry of a bot. The four fixed topics are added when first needed.
    pub async fn gmail_topic_registry(&self, bot: &str) -> anyhow::Result<Vec<Entry>> {
        let bot = bot.to_owned();
        self.run(move |c| registry(c, &bot)).await
    }

    /// One topic of the registry, by key.
    pub async fn gmail_topic_entry(&self, bot: &str, key: &str) -> anyhow::Result<Option<Entry>> {
        Ok(self
            .gmail_topic_registry(bot)
            .await?
            .into_iter()
            .find(|e| e.key == key))
    }

    /// Match a proposed topic with the registry, or add it. A match ignores case, spaces and a final `s`. A new topic
    /// needs `new_left` above zero and a registry below `cap` topics. The caller lowers `new_left` when `new` is true.
    pub async fn gmail_topic_resolve(
        &self,
        bot: &str,
        raw: Option<&str>,
        new_left: u32,
        cap: u32,
        at_ms: i64,
    ) -> anyhow::Result<Choice> {
        let Some(raw) = raw.map(str::trim).filter(|r| !r.is_empty()) else {
            return Ok(Choice::None);
        };
        let Ok(name) = parse_name(raw) else {
            return Ok(Choice::Invalid);
        };
        let bot = bot.to_owned();
        self.run(move |c| {
            let tx = c.unchecked_transaction()?;
            let reg = registry(&tx, &bot)?;
            let want = fold(&name.key);
            // A seed answers to its old name and to its code. Review is a host decision, never a proposed topic.
            let hit = reg.iter().find(|e| {
                e.key != "review" && (fold(&e.key) == want || fold(&e.name.to_ascii_lowercase()) == want)
            });
            if let Some(e) = hit {
                return Ok(Choice::Topic {
                    key: e.key.clone(),
                    new: false,
                });
            }
            if new_left == 0 || reg.len() as u32 >= cap {
                return Ok(Choice::Limit);
            }
            tx.execute(
                "INSERT INTO gmail_topics(bot_id, key, name, seed, created_ms) VALUES (?1,?2,?3,0,?4)",
                params![bot, name.key, name.name, at_ms],
            )?;
            tx.commit()?;
            Ok(Choice::Topic {
                key: name.key,
                new: true,
            })
        })
        .await
    }

    pub async fn gmail_topic_account(
        &self,
        bot: &str,
        account: &str,
        key: &str,
    ) -> anyhow::Result<Option<AccountState>> {
        let (bot, account, key) = (bot.to_owned(), account.to_owned(), key.to_owned());
        self.run(move |c| {
            let s: Option<String> = c
                .query_row(
                    "SELECT state FROM gmail_topic_labels WHERE bot_id = ?1 AND account = ?2 AND key = ?3",
                    params![bot, account, key],
                    |r| r.get(0),
                )
                .optional()?;
            Ok(match s.as_deref() {
                Some("creating") => Some(AccountState::Creating),
                Some("verified") => Some(AccountState::Verified),
                _ => None,
            })
        })
        .await
    }

    /// Record the state of a topic on an account. `created` counts a label that a creation step made. A verified state
    /// is never lowered to `creating`.
    pub async fn gmail_topic_account_set(
        &self,
        bot: &str,
        account: &str,
        key: &str,
        state: AccountState,
        created: bool,
        at_ms: i64,
    ) -> anyhow::Result<()> {
        let (bot, account, key) = (bot.to_owned(), account.to_owned(), key.to_owned());
        self.run(move |c| {
            c.execute(
                "INSERT INTO gmail_topic_labels(bot_id, account, key, state, created, updated_ms) VALUES (?1,?2,?3,?4,?5,?6) \
                 ON CONFLICT(bot_id, account, key) DO UPDATE SET \
                 state = CASE WHEN gmail_topic_labels.state = 'verified' AND excluded.state = 'creating' THEN 'verified' ELSE excluded.state END, \
                 created = gmail_topic_labels.created + excluded.created, updated_ms = excluded.updated_ms",
                params![bot, account, key, state.code(), created as i64, at_ms],
            )?;
            Ok(())
        })
        .await
    }

    /// The label is gone from the account. The next write may create it again.
    pub async fn gmail_topic_account_clear(
        &self,
        bot: &str,
        account: &str,
        key: &str,
    ) -> anyhow::Result<()> {
        let (bot, account, key) = (bot.to_owned(), account.to_owned(), key.to_owned());
        self.run(move |c| {
            c.execute(
                "DELETE FROM gmail_topic_labels WHERE bot_id = ?1 AND account = ?2 AND key = ?3",
                params![bot, account, key],
            )?;
            Ok(())
        })
        .await
    }

    pub async fn gmail_topic_counts(&self, bot: &str) -> anyhow::Result<TopicCounts> {
        let bot = bot.to_owned();
        self.run(move |c| {
            let n = |sql: &str| c.query_row(sql, [&bot], |r| r.get::<_, u32>(0));
            Ok(TopicCounts {
                registry: n("SELECT COUNT(*) FROM gmail_topics WHERE bot_id = ?1")?,
                dynamic: n("SELECT COUNT(*) FROM gmail_topics WHERE bot_id = ?1 AND seed = 0")?,
                verified: n("SELECT COUNT(*) FROM gmail_topic_labels WHERE bot_id = ?1 AND state = 'verified'")?,
                creating: n("SELECT COUNT(*) FROM gmail_topic_labels WHERE bot_id = ?1 AND state = 'creating'")?,
                created: n("SELECT COALESCE(SUM(created), 0) FROM gmail_topic_labels WHERE bot_id = ?1")?,
                overflow: n("SELECT COUNT(*) FROM gmail_organize WHERE bot_id = ?1 AND reason = 'topic_limit'")?,
                invalid: n("SELECT COUNT(*) FROM gmail_organize WHERE bot_id = ?1 AND reason = 'topic_invalid'")?,
            })
        })
        .await
    }
}

fn registry(c: &rusqlite::Connection, bot: &str) -> rusqlite::Result<Vec<Entry>> {
    for (key, name) in SEEDS {
        c.execute(
            "INSERT OR IGNORE INTO gmail_topics(bot_id, key, name, seed, created_ms) VALUES (?1,?2,?3,1,0)",
            params![bot, key, name],
        )?;
    }
    let mut q =
        c.prepare("SELECT key, name, seed FROM gmail_topics WHERE bot_id = ?1 ORDER BY rowid")?;
    q.query_map([bot], |r| {
        Ok(Entry {
            key: r.get(0)?,
            name: r.get(1)?,
            seed: r.get::<_, i64>(2)? == 1,
        })
    })?
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_plain_topic_passes_and_is_shown_in_title_case() {
        let n = parse_name("  travel  plans ").unwrap();
        assert_eq!(
            (n.key.as_str(), n.name.as_str()),
            ("travel plans", "Travel Plans")
        );
        assert_eq!(n.label(), "Deck/Travel Plans");
        assert_eq!(parse_name("AI tools").unwrap().name, "AI Tools");
        assert_eq!(parse_name("Check-in").unwrap().name, "Check-in");
    }

    #[test]
    fn a_malformed_or_injected_topic_is_refused() {
        for bad in [
            "",
            "a",
            "Travel/Flights",
            "Deck/Travel",
            "Travel\nFlights",
            "Travel\u{0}",
            "Tra\u{202e}vel",
            "travel\"; click css=\"#x",
            "${SECRET}",
            "{label}",
            "a@b.example",
            "https://x.example",
            "Flight 12345",
            "INR 500 paid",
            "Booking x1y2z3",
            "'; DROP TABLE x; --",
            "<b>Travel</b>",
            "Travel\\",
            "1 travel",
            "-travel",
            "travel-",
            "travel--plans",
            "Four words are too many here",
            "This name is much too long to be a label",
        ] {
            assert!(parse_name(bad).is_err(), "{bad:?} must be refused");
        }
        assert!(
            parse_name("Voyage voyage voyage").is_ok(),
            "three words pass"
        );
    }

    #[test]
    fn a_reserved_or_catch_all_name_is_refused() {
        for bad in [
            "Review",
            "Inbox",
            "Trash",
            "Spam",
            "Sent",
            "Deck",
            "FYI",
            "Action",
            "Waiting",
            "Needs reply",
            "Reply needed",
            "Awaiting reply",
            "Other",
            "Misc",
            "Unknown",
            "Delete old",
            "Unsubscribe",
            "Archive",
            "Starred",
            "Important",
            "All mail",
            "Permanent",
        ] {
            assert_eq!(parse_name(bad), Err("reserved"), "{bad:?}");
        }
    }

    #[test]
    fn a_final_s_folds_and_nothing_else_does() {
        assert_eq!(fold("receipts"), fold("receipt"));
        assert_eq!(fold("travel plans"), fold("travel plan"));
        assert_ne!(fold("travel"), fold("trip"));
        assert_ne!(fold("transactions"), fold("receipts"));
        assert_ne!(fold("class"), fold("clas"));
    }

    const AT: i64 = 5_000;

    #[tokio::test]
    async fn a_new_travel_topic_is_added_once_and_reused() {
        let s = Store::open_memory().unwrap();
        let first = s
            .gmail_topic_resolve("main", Some("Travel"), 2, 30, AT)
            .await
            .unwrap();
        assert_eq!(
            first,
            Choice::Topic {
                key: "travel".into(),
                new: true
            }
        );
        for again in ["Travel", "travel", " TRAVEL ", "Travels"] {
            let c = s
                .gmail_topic_resolve("main", Some(again), 0, 30, AT + 1)
                .await
                .unwrap();
            assert_eq!(
                c,
                Choice::Topic {
                    key: "travel".into(),
                    new: false
                },
                "{again}"
            );
        }
        let reg = s.gmail_topic_registry("main").await.unwrap();
        assert_eq!(reg.iter().filter(|e| e.key == "travel").count(), 1);
        assert_eq!(
            reg.iter().filter(|e| e.seed).count(),
            4,
            "the four old topics are seeded"
        );
    }

    #[tokio::test]
    async fn the_old_names_are_seeded_aliases_and_review_is_never_proposed() {
        let s = Store::open_memory().unwrap();
        for (raw, key) in [
            ("Receipts", "receipts"),
            ("receipt", "receipts"),
            ("Jobs", "jobs"),
            ("Software and AI", "tech"),
            ("software and ai", "tech"),
            ("tech", "tech"),
        ] {
            let c = s
                .gmail_topic_resolve("main", Some(raw), 0, 30, AT)
                .await
                .unwrap();
            assert_eq!(
                c,
                Choice::Topic {
                    key: key.into(),
                    new: false
                },
                "{raw}"
            );
        }
        assert_eq!(
            s.gmail_topic_resolve("main", Some("Review"), 2, 30, AT)
                .await
                .unwrap(),
            Choice::Invalid
        );
    }

    #[tokio::test]
    async fn limits_stop_a_new_topic_and_never_an_existing_one() {
        let s = Store::open_memory().unwrap();
        assert_eq!(
            s.gmail_topic_resolve("main", Some("Travel"), 0, 30, AT)
                .await
                .unwrap(),
            Choice::Limit
        );
        assert!(
            s.gmail_topic_resolve("main", Some("Travel"), 1, 30, AT)
                .await
                .unwrap()
                != Choice::Limit
        );
        // The registry holds 5 topics now. A cap of 5 refuses the next new one and keeps the old ones.
        assert_eq!(
            s.gmail_topic_resolve("main", Some("Delivery"), 1, 5, AT)
                .await
                .unwrap(),
            Choice::Limit
        );
        assert_eq!(
            s.gmail_topic_resolve("main", Some("Travel"), 0, 5, AT)
                .await
                .unwrap(),
            Choice::Topic {
                key: "travel".into(),
                new: false
            }
        );
        assert_eq!(
            s.gmail_topic_resolve("main", Some("a@b"), 1, 30, AT)
                .await
                .unwrap(),
            Choice::Invalid
        );
        assert_eq!(
            s.gmail_topic_resolve("main", None, 1, 30, AT)
                .await
                .unwrap(),
            Choice::None
        );
        assert_eq!(
            s.gmail_topic_resolve("main", Some("  "), 1, 30, AT)
                .await
                .unwrap(),
            Choice::None
        );
    }

    #[tokio::test]
    async fn accounts_verify_the_same_topic_on_their_own() {
        let s = Store::open_memory().unwrap();
        s.gmail_topic_resolve("main", Some("Travel"), 1, 30, AT)
            .await
            .unwrap();
        assert_eq!(
            s.gmail_topic_account("main", "one", "travel")
                .await
                .unwrap(),
            None
        );
        s.gmail_topic_account_set("main", "one", "travel", AccountState::Creating, false, AT)
            .await
            .unwrap();
        s.gmail_topic_account_set(
            "main",
            "one",
            "travel",
            AccountState::Verified,
            true,
            AT + 1,
        )
        .await
        .unwrap();
        // A later `creating` never lowers a verified label.
        s.gmail_topic_account_set(
            "main",
            "one",
            "travel",
            AccountState::Creating,
            false,
            AT + 2,
        )
        .await
        .unwrap();
        assert_eq!(
            s.gmail_topic_account("main", "one", "travel")
                .await
                .unwrap(),
            Some(AccountState::Verified)
        );
        assert_eq!(
            s.gmail_topic_account("main", "two", "travel")
                .await
                .unwrap(),
            None,
            "no other account is touched"
        );
        s.gmail_topic_account_set(
            "main",
            "two",
            "travel",
            AccountState::Verified,
            false,
            AT + 3,
        )
        .await
        .unwrap();
        let c = s.gmail_topic_counts("main").await.unwrap();
        assert_eq!((c.verified, c.creating, c.created, c.dynamic), (2, 0, 1, 1));
        s.gmail_topic_account_clear("main", "two", "travel")
            .await
            .unwrap();
        assert_eq!(
            s.gmail_topic_account("main", "two", "travel")
                .await
                .unwrap(),
            None
        );
    }
}
