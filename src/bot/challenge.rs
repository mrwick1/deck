//! Owner help for sign-in challenges: phone approval and CAPTCHA-style actions.
//!
//! A challenge is the open `Wait` of a setup snapshot, so it survives a restart and keeps its
//! task or session owner. The owner answers with metadata only. Codes and passwords use the
//! protected input route. The owner saying "completed" never counts as a verified sign-in.
//! A verification result closes a task request. A setup step verifies by running again.
//!
//! Integration adapter for `tools.rs` (a running task asks for help):
//! 1. `request_login_help(store, chat_id, task_key, Challenge::Phone, help, ttl, now)` returns
//!    `Requested::Open(request_id)`. End the run with a wait, as `ask_owner` does.
//! 2. `challenge_state(store, chat_id, request_id, now)` reports `Open`, `Answered`, `Closed`.
//! 3. On `Answered`, check the service in bare. Call `verify_login_help` with the result.
//!    Continue the task only on `Verified`. `NotVerified` opens a fresh request.
//!
//! A challenge that bare cannot complete is `Challenge::Unsupported`. It returns `Blocked`.

// The Tools adapter that calls the request helpers lands with the secret worker's change.
#![allow(dead_code)]

use super::setup::{Help, Phase, Setup, SetupError, Status, Step, WaitKind};
use crate::store::Store;
use serde::{Deserialize, Serialize};

const MAX_INSTRUCTIONS: usize = 500;
const MAX_REASON: usize = 200;
const MAX_RESOLVED: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Decision {
    Completed,
    Cancel,
}

/// What a running task asks the owner for.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Challenge {
    Phone,
    Captcha,
    /// Bare cannot complete this challenge. No owner action can help.
    Unsupported,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Verification {
    Verified,
    /// The service did not accept the action. The owner can try again.
    NotVerified(String),
    /// The sign-in cannot proceed.
    Blocked(String),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    /// A setup step ran again after the owner finished. That run decides success.
    OwnerCompleted,
    Verified,
    Rejected(String),
    Cancelled,
    Expired,
    Blocked(String),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Resolved {
    pub request_id: String,
    pub outcome: Outcome,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Requested {
    Open(String),
    Blocked(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum State {
    Open {
        expires_at: Option<u64>,
    },
    /// The owner finished. Verification is pending.
    Answered,
    Closed(Outcome),
    Unknown,
}

fn clean(t: &str, max: usize) -> String {
    t.chars()
        .filter(|c| !c.is_control())
        .take(max)
        .collect::<String>()
        .trim()
        .to_string()
}

impl Setup {
    fn close(&mut self, request_id: &str, outcome: Outcome) {
        self.resolved.push(Resolved {
            request_id: request_id.into(),
            outcome,
        });
        let extra = self.resolved.len().saturating_sub(MAX_RESOLVED);
        self.resolved.drain(..extra);
    }

    /// Reissue an expired, unanswered request. Returns its new identity.
    pub fn sweep_expired(&mut self, now: u64) -> Option<String> {
        let w = self.wait.clone()?;
        if w.answered || w.expires_at.is_none_or(|e| now < e) {
            return None;
        }
        self.close(&w.request_id, Outcome::Expired);
        self.reissue(w, now)
    }

    fn reissue(&mut self, w: super::setup::Wait, now: u64) -> Option<String> {
        self.open_wait(
            w.step,
            w.kind,
            w.ttl_secs,
            Help {
                instructions: w.instructions,
                match_number: w.match_number,
            },
            now,
        );
        self.wait.as_ref().map(|w| w.request_id.clone())
    }

    /// A running task asks for owner help. The same task asking again for the same kind gets
    /// the open request. Another open request refuses it.
    pub fn request_help(
        &mut self,
        key: &str,
        kind: Challenge,
        help: Help,
        ttl_secs: Option<u64>,
        now: u64,
    ) -> Result<Requested, SetupError> {
        if self.phase() != Phase::Ready {
            return Err(SetupError::NotReady);
        }
        let key = clean(key, 64);
        let instructions = help
            .instructions
            .map(|t| clean(&t, MAX_INSTRUCTIONS))
            .filter(|t| !t.is_empty());
        let bad = |m: &str| Err(SetupError::Invalid(m.into()));
        if key.is_empty() || instructions.is_none() {
            return bad("a task key and instructions are required");
        }
        if help
            .match_number
            .as_ref()
            .is_some_and(|n| n.is_empty() || n.len() > 8 || !n.bytes().all(|b| b.is_ascii_digit()))
        {
            return bad("a match number has 1 to 8 digits");
        }
        if ttl_secs == Some(0) {
            return bad("the lifetime must be above zero");
        }
        let wait_kind = match kind {
            Challenge::Phone => WaitKind::PhoneApproval,
            Challenge::Captcha => WaitKind::Captcha,
            Challenge::Unsupported => {
                self.wait_seq += 1;
                let id = format!("{}:{}", self.id, self.wait_seq);
                self.close(&id, Outcome::Blocked(instructions.unwrap_or_default()));
                return Ok(Requested::Blocked(id));
            }
        };
        let step = Step::Login(key);
        if let Some(w) = &self.wait {
            return if w.step == step && w.kind == wait_kind {
                Ok(Requested::Open(w.request_id.clone()))
            } else {
                Err(SetupError::Busy(w.request_id.clone()))
            };
        }
        self.open_wait(
            step,
            wait_kind,
            ttl_secs,
            Help {
                instructions,
                match_number: help.match_number,
            },
            now,
        );
        Ok(Requested::Open(
            self.wait.as_ref().unwrap().request_id.clone(),
        ))
    }

    /// The owner answers the open request. A stale identity fails. A repeat of an answer that
    /// was already applied succeeds without a change. An expired request opens a replacement
    /// and fails with its identity.
    pub fn respond(&mut self, request_id: &str, d: Decision, now: u64) -> Result<(), SetupError> {
        let Some(w) = self.wait.clone().filter(|w| w.request_id == request_id) else {
            let same = self.resolved.iter().any(|r| {
                r.request_id == request_id
                    && match d {
                        Decision::Cancel => r.outcome == Outcome::Cancelled,
                        Decision::Completed => {
                            matches!(r.outcome, Outcome::OwnerCompleted | Outcome::Verified)
                        }
                    }
            });
            return if same {
                Ok(())
            } else {
                Err(SetupError::UnknownRequest)
            };
        };
        if matches!(w.kind, WaitKind::Credential { .. }) && d == Decision::Completed {
            return Err(SetupError::Invalid(
                "send a credential through protected input".into(),
            ));
        }
        if !w.answered && w.expires_at.is_some_and(|e| now >= e) {
            self.close(request_id, Outcome::Expired);
            let id = self.reissue(w, now).unwrap_or_default();
            return Err(SetupError::Expired(id));
        }
        match (d, &w.step) {
            (Decision::Completed, Step::Login(_)) => {
                if let Some(w) = &mut self.wait {
                    w.answered = true;
                }
            }
            (Decision::Completed, _) => {
                self.complete_wait(request_id, now)?;
                self.close(request_id, Outcome::OwnerCompleted);
            }
            (Decision::Cancel, step) => {
                self.wait = None;
                if let Some(s) = self.steps.iter_mut().find(|s| s.step == *step) {
                    // The step cannot go on. A higher attempt limit lets the owner retry it.
                    s.status = Status::Pending;
                    s.attempts = s.attempts.max(self.max_attempts);
                    s.last_error = Some("the owner cancelled the request".into());
                }
                self.close(request_id, Outcome::Cancelled);
            }
        }
        Ok(())
    }

    /// Record what the task found after the owner finished. Only an answered request can be
    /// verified.
    pub fn verify_help(
        &mut self,
        request_id: &str,
        v: Verification,
        now: u64,
    ) -> Result<(), SetupError> {
        let Some(w) = self
            .wait
            .clone()
            .filter(|w| w.request_id == request_id && matches!(w.step, Step::Login(_)))
        else {
            return Err(SetupError::UnknownRequest);
        };
        if !w.answered {
            return Err(SetupError::Invalid("the owner has not answered".into()));
        }
        match v {
            Verification::Verified => {
                self.wait = None;
                self.close(request_id, Outcome::Verified);
            }
            Verification::Blocked(r) => {
                self.wait = None;
                self.close(request_id, Outcome::Blocked(clean(&r, MAX_REASON)));
            }
            Verification::NotVerified(r) => {
                self.close(request_id, Outcome::Rejected(clean(&r, MAX_REASON)));
                self.reissue(w, now);
            }
        }
        Ok(())
    }

    pub fn challenge_state(&self, request_id: &str) -> State {
        if let Some(w) = self.wait.as_ref().filter(|w| w.request_id == request_id) {
            return if w.answered {
                State::Answered
            } else {
                State::Open {
                    expires_at: w.expires_at,
                }
            };
        }
        self.resolved
            .iter()
            .find(|r| r.request_id == request_id)
            .map_or(State::Unknown, |r| State::Closed(r.outcome.clone()))
    }
}

async fn edit<T: Send>(
    store: &Store,
    chat_id: i64,
    mut f: impl FnMut(&mut Setup) -> Result<T, SetupError>,
) -> anyhow::Result<Result<T, SetupError>> {
    let mut last = None;
    for _ in 0..3 {
        let Some(mut row) = store
            .setup_records()
            .await?
            .into_iter()
            .find(|r| r.chat_id == chat_id)
        else {
            anyhow::bail!("no bot setup owns this chat");
        };
        let answer = f(&mut row.setup);
        // Expiry reissues a request even when the answer is an error, so always save.
        match store.save_setup_state(row.version, &row.setup).await {
            Ok(_) => return Ok(answer),
            Err(e) => last = Some(e),
        }
    }
    Err(last.unwrap())
}

/// Ask for owner help for the task `key` of the bot that owns `chat_id`.
pub async fn request_login_help(
    store: &Store,
    chat_id: i64,
    key: &str,
    kind: Challenge,
    help: Help,
    ttl_secs: Option<u64>,
    now: u64,
) -> anyhow::Result<Result<Requested, SetupError>> {
    edit(store, chat_id, |s| {
        s.request_help(key, kind, help.clone(), ttl_secs, now)
    })
    .await
}

/// Read a request. An expired, unanswered request is reissued first. The new identity shows as
/// a new request, so the caller must ask the task to read the current wait again.
pub async fn challenge_state(
    store: &Store,
    chat_id: i64,
    request_id: &str,
    now: u64,
) -> anyhow::Result<State> {
    let id = request_id.to_string();
    Ok(edit(store, chat_id, |s| {
        s.sweep_expired(now);
        Ok(s.challenge_state(&id))
    })
    .await??)
}

/// Record the service check. Continue the task only after `Verified`.
pub async fn verify_login_help(
    store: &Store,
    chat_id: i64,
    request_id: &str,
    v: Verification,
    now: u64,
) -> anyhow::Result<Result<(), SetupError>> {
    let id = request_id.to_string();
    edit(store, chat_id, |s| s.verify_help(&id, v.clone(), now)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bot::setup::{
        Authority, Draft, Limits, Phase, Provisioner, Resources, StepOutcome, StepRequest,
    };
    use futures_util::future::BoxFuture;
    use std::sync::Mutex;

    fn approved(credentials: Vec<String>) -> Setup {
        let mut s = Setup::new(
            "bot-1",
            Draft {
                revision: 1,
                name: "n".into(),
                instructions: "i".into(),
                model: "m".into(),
                authority: Authority::Full,
                resources: Resources {
                    cpus: 2,
                    memory_mib: 2048,
                    disk_gib: 10,
                },
                budget: None,
                credentials,
                task: None,
            },
        )
        .unwrap();
        s.approve(
            1,
            "owner",
            &Limits {
                max_cpus: 2,
                max_memory_mib: 2048,
                max_disk_gib: 10,
            },
        )
        .unwrap();
        s
    }

    fn ready() -> Setup {
        let mut s = approved(vec![]);
        s.steps.iter_mut().for_each(|t| t.status = Status::Done);
        s
    }

    fn help() -> Help {
        Help {
            instructions: Some("Approve the sign-in on your phone".into()),
            match_number: Some("42".into()),
        }
    }

    fn open(s: &mut Setup, now: u64, ttl: Option<u64>) -> String {
        match s.request_help("task-1", Challenge::Phone, help(), ttl, now) {
            Ok(Requested::Open(id)) => id,
            other => panic!("{other:?}"),
        }
    }

    /// Setup steps ask for help once, then succeed or ask again as scripted.
    struct Script(Mutex<Vec<StepOutcome>>);
    impl Provisioner for Script {
        fn run<'a>(&'a self, _: &'a StepRequest) -> BoxFuture<'a, StepOutcome> {
            Box::pin(async move {
                let mut s = self.0.lock().unwrap();
                if s.is_empty() {
                    StepOutcome::Done
                } else {
                    s.remove(0)
                }
            })
        }
    }
    fn ask() -> StepOutcome {
        StepOutcome::Help {
            kind: WaitKind::Captcha,
            ttl_secs: Some(60),
            help: help(),
        }
    }
    async fn advance(s: &mut Setup, p: &Script, now: u64) -> Phase {
        s.advance(p, &mut |_| Ok(()), now).await.unwrap()
    }

    #[test]
    fn a_stale_identity_changes_nothing() {
        let mut s = ready();
        let id = open(&mut s, 0, None);
        let before = s.clone();
        assert_eq!(
            s.respond("bot-1:99", Decision::Completed, 1),
            Err(SetupError::UnknownRequest)
        );
        assert_eq!(s, before);
        assert_eq!(s.challenge_state(&id), State::Open { expires_at: None });
    }

    #[test]
    fn an_owner_answer_is_not_a_verified_sign_in() {
        let mut s = ready();
        let id = open(&mut s, 0, Some(60));
        s.respond(&id, Decision::Completed, 1).unwrap();
        assert_eq!(s.challenge_state(&id), State::Answered);
        assert!(s.wait.is_some(), "the request stays open until verified");
        // The service rejects the action: a fresh request opens and the old identity is dead.
        s.verify_help(
            &id,
            Verification::NotVerified("still on the sign-in page".into()),
            2,
        )
        .unwrap();
        let next = s.wait.clone().unwrap();
        assert_ne!(next.request_id, id);
        assert!(!next.answered);
        assert_eq!(next.match_number.as_deref(), Some("42"));
        assert_eq!(
            s.challenge_state(&id),
            State::Closed(Outcome::Rejected("still on the sign-in page".into()))
        );
        assert_eq!(
            s.respond(&id, Decision::Completed, 3),
            Err(SetupError::UnknownRequest)
        );
        s.respond(&next.request_id, Decision::Completed, 3).unwrap();
        s.verify_help(&next.request_id, Verification::Verified, 4)
            .unwrap();
        assert!(s.wait.is_none());
        assert_eq!(
            s.challenge_state(&next.request_id),
            State::Closed(Outcome::Verified)
        );
    }

    #[test]
    fn verification_needs_an_owner_answer_and_the_current_identity() {
        let mut s = ready();
        let id = open(&mut s, 0, None);
        assert!(matches!(
            s.verify_help(&id, Verification::Verified, 1),
            Err(SetupError::Invalid(_))
        ));
        assert_eq!(
            s.verify_help("bot-1:99", Verification::Verified, 1),
            Err(SetupError::UnknownRequest)
        );
        assert!(s.wait.is_some());
    }

    #[test]
    fn an_expired_request_is_replaced_and_the_old_answer_fails() {
        let mut s = ready();
        let id = open(&mut s, 0, Some(60));
        let Err(SetupError::Expired(new)) = s.respond(&id, Decision::Completed, 60) else {
            panic!("expected expiry");
        };
        assert_ne!(new, id);
        assert_eq!(s.wait.as_ref().unwrap().expires_at, Some(120));
        assert_eq!(s.challenge_state(&id), State::Closed(Outcome::Expired));
        assert_eq!(
            s.respond(&id, Decision::Completed, 61),
            Err(SetupError::UnknownRequest)
        );
        // A sweep reissues too, once.
        assert_eq!(s.sweep_expired(119), None);
        let again = s.sweep_expired(120).unwrap();
        assert_ne!(again, new);
        // An answered request does not expire while the task verifies it.
        s.respond(&again, Decision::Completed, 121).unwrap();
        assert_eq!(s.sweep_expired(10_000), None);
    }

    #[test]
    fn a_duplicate_answer_is_idempotent_and_a_contradiction_fails() {
        let mut s = ready();
        let id = open(&mut s, 0, None);
        s.respond(&id, Decision::Completed, 1).unwrap();
        let once = s.clone();
        s.respond(&id, Decision::Completed, 2).unwrap();
        assert_eq!(s, once);
        s.verify_help(&id, Verification::Verified, 3).unwrap();
        s.respond(&id, Decision::Completed, 4).unwrap();
        assert_eq!(
            s.respond(&id, Decision::Cancel, 4),
            Err(SetupError::UnknownRequest)
        );
    }

    #[test]
    fn cancel_closes_a_task_request() {
        let mut s = ready();
        let id = open(&mut s, 0, None);
        s.respond(&id, Decision::Cancel, 1).unwrap();
        assert!(s.wait.is_none());
        assert_eq!(s.challenge_state(&id), State::Closed(Outcome::Cancelled));
        s.respond(&id, Decision::Cancel, 2).unwrap();
        assert_eq!(s.phase(), Phase::Ready);
    }

    #[tokio::test]
    async fn cancel_blocks_a_setup_step_until_the_owner_allows_another_try() {
        let mut s = approved(vec![]);
        let p = Script(Mutex::new(vec![ask()]));
        assert_eq!(advance(&mut s, &p, 0).await, Phase::Waiting);
        let id = s.wait.clone().unwrap().request_id;
        s.respond(&id, Decision::Cancel, 1).unwrap();
        assert_eq!(s.phase(), Phase::Exhausted);
        assert_eq!(advance(&mut s, &p, 2).await, Phase::Exhausted);
        s.set_max_attempts(4);
        assert_eq!(advance(&mut s, &p, 3).await, Phase::Ready);
    }

    #[tokio::test]
    async fn a_setup_step_resumes_only_when_the_service_check_succeeds() {
        let mut s = approved(vec![]);
        let p = Script(Mutex::new(vec![ask(), ask()]));
        assert_eq!(advance(&mut s, &p, 0).await, Phase::Waiting);
        let w = s.wait.clone().unwrap();
        assert_eq!(
            w.instructions.as_deref(),
            Some("Approve the sign-in on your phone")
        );
        s.respond(&w.request_id, Decision::Completed, 1).unwrap();
        // The owner said done, but the next run still finds the challenge: false success.
        assert_eq!(advance(&mut s, &p, 2).await, Phase::Waiting);
        let next = s.wait.clone().unwrap();
        assert_ne!(next.request_id, w.request_id);
        s.respond(&w.request_id, Decision::Completed, 3).unwrap(); // duplicate of closed
        s.respond(&next.request_id, Decision::Completed, 3).unwrap();
        assert_eq!(advance(&mut s, &p, 4).await, Phase::Ready);
    }

    #[test]
    fn an_unsupported_challenge_blocks_and_opens_no_request() {
        let mut s = ready();
        let Ok(Requested::Blocked(id)) =
            s.request_help("task-1", Challenge::Unsupported, help(), None, 0)
        else {
            panic!("expected a blocked result");
        };
        assert!(s.wait.is_none());
        assert!(matches!(
            s.challenge_state(&id),
            State::Closed(Outcome::Blocked(_))
        ));
        assert_eq!(
            s.respond(&id, Decision::Completed, 1),
            Err(SetupError::UnknownRequest)
        );
    }

    #[test]
    fn one_open_request_at_a_time_and_the_same_ask_returns_it() {
        let mut s = ready();
        let id = open(&mut s, 0, None);
        assert_eq!(open(&mut s, 5, None), id);
        assert_eq!(
            s.request_help("task-2", Challenge::Phone, help(), None, 1),
            Err(SetupError::Busy(id.clone()))
        );
        assert_eq!(
            s.request_help("task-1", Challenge::Captcha, help(), None, 1),
            Err(SetupError::Busy(id))
        );
    }

    #[test]
    fn invalid_requests_and_unready_setups_fail() {
        let mut s = ready();
        for h in [
            Help {
                instructions: None,
                match_number: None,
            },
            Help {
                instructions: Some("x".into()),
                match_number: Some("12ab".into()),
            },
        ] {
            assert!(matches!(
                s.request_help("t", Challenge::Phone, h, None, 0),
                Err(SetupError::Invalid(_))
            ));
        }
        assert!(matches!(
            s.request_help("t", Challenge::Phone, help(), Some(0), 0),
            Err(SetupError::Invalid(_))
        ));
        let mut waiting = approved(vec![]);
        assert_eq!(
            waiting.request_help("t", Challenge::Phone, help(), None, 0),
            Err(SetupError::NotReady)
        );
    }

    #[test]
    fn the_plain_wait_route_cannot_close_task_help_and_credentials_need_protected_input() {
        let mut s = ready();
        let id = open(&mut s, 0, None);
        assert!(matches!(
            s.complete_wait(&id, 1),
            Err(SetupError::Invalid(_))
        ));
        assert!(s.wait.is_some());
        let mut c = approved(vec!["mail".into()]);
        c.steps[..3]
            .iter_mut()
            .for_each(|t| t.status = Status::Done);
        let p = Script(Mutex::new(vec![]));
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        rt.block_on(advance(&mut c, &p, 0));
        let id = c.wait.clone().unwrap().request_id;
        assert!(matches!(
            c.respond(&id, Decision::Completed, 1),
            Err(SetupError::Invalid(_))
        ));
        assert!(c.wait.is_some());
    }

    #[test]
    fn old_snapshots_and_plain_waits_keep_their_shape() {
        let old = r#"{"request_id":"a:1","step":"Boot","kind":"PhoneApproval","expires_at":null,"ttl_secs":null}"#;
        let w: crate::bot::setup::Wait = serde_json::from_str(old).unwrap();
        assert_eq!(w.kind, WaitKind::PhoneApproval);
        assert!(!w.answered && w.instructions.is_none());
        assert_eq!(serde_json::to_string(&w).unwrap(), old);
    }
}
