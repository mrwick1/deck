//! Reviewed bot setup: draft revisions, approval, provisioning steps and owner waits.
//!
//! `Setup` is a plain serializable value. It owns no database. The caller loads it from the
//! store, calls one method and saves the result. `advance` calls `persist` before each side
//! effect and after each outcome, so a crash leaves an `InFlight` step that `advance` charges
//! as one attempt on resume.
//!
//! Rules: no computer exists before approval of a revision. System steps finish before any
//! credential request. An owner wait costs no attempt. A step gets `max_attempts` total
//! attempts. An unknown outcome blocks the step until the owner resolves it (D34).
//! Credential values never enter this module: a credential wait carries a request identity and
//! a label only. The protected store that accepts the value is outside this module.

// Nothing calls this module until Stage 1 storage and the API exist. Remove this then.
#![allow(dead_code)]

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use futures_util::future::BoxFuture;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::process::Command;

pub const DEFAULT_ATTEMPTS: u32 = 3;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Resources {
    pub cpus: u32,
    pub memory_mib: u32,
    pub disk_gib: u32,
}

/// Upper bounds from reviewed configuration. A draft cannot raise them.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Limits {
    pub max_cpus: u32,
    pub max_memory_mib: u32,
    pub max_disk_gib: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Authority {
    Full,
    /// Actions the owner kept. Everything else is blocked.
    Restricted(Vec<String>),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Budget {
    pub currency: String,
    pub exponent: u8,
    pub period: String,
    pub allowance: i64,
    pub ceiling: i64,
}

impl Budget {
    pub fn validate(&self) -> Result<(), SetupError> {
        if crate::accounting::Unit::new(&self.currency, self.exponent).is_err()
            || self.exponent > 9
            || self.period.trim().is_empty()
            || self.allowance < 0
            || self.ceiling < self.allowance
        {
            return Err(SetupError::Invalid(
                "the reviewed spending unit, period and ceiling are invalid".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Draft {
    pub revision: u64,
    pub name: String,
    pub instructions: String,
    pub model: String,
    pub authority: Authority,
    pub resources: Resources,
    #[serde(default)]
    pub budget: Option<Budget>,
    /// Labels of the credentials the job needs. Labels only, never values.
    pub credentials: Vec<String>,
    /// Specific work to start once ready. `None` means a general role.
    pub task: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Step {
    Allocate,
    Boot,
    InstallExecutor,
    Credential(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub enum Status {
    Pending,
    InFlight,
    Waiting,
    Unknown,
    Done,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StepState {
    pub step: Step,
    pub status: Status,
    pub attempts: u32,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum WaitKind {
    /// Protected input for this label. The value goes to the protected store, not here.
    Credential {
        label: String,
    },
    PhoneApproval,
    Captcha,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Wait {
    pub request_id: String,
    pub step: Step,
    pub kind: WaitKind,
    /// Unix seconds. `None` never expires.
    pub expires_at: Option<u64>,
    /// Seconds a re-issued request lives.
    pub ttl_secs: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Approval {
    pub revision: u64,
    pub by: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub enum Phase {
    Draft,
    Provisioning,
    Waiting,
    /// A step has an unknown outcome. Nothing retries it.
    Investigate,
    /// A step used all attempts.
    Exhausted,
    Ready,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Setup {
    pub id: String,
    pub draft: Draft,
    pub approval: Option<Approval>,
    pub max_attempts: u32,
    pub steps: Vec<StepState>,
    pub wait: Option<Wait>,
    pub wait_seq: u64,
    pub started: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Start {
    /// Start this supplied work, once.
    Task(String),
    /// Only a general role: tell the owner the bot is ready and ask for instructions.
    AnnounceReady,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ChecklistItem {
    pub label: String,
    pub done: bool,
}

#[derive(Debug, thiserror::Error, PartialEq)]
pub enum SetupError {
    #[error("the draft is at revision {current}, not {expected}")]
    RevisionConflict { expected: u64, current: u64 },
    #[error("the draft changed after approval; start a new setup")]
    Locked,
    #[error("the draft is not approved")]
    NotApproved,
    #[error("invalid draft: {0}")]
    Invalid(String),
    #[error("no open owner request has this identity")]
    UnknownRequest,
    #[error("the request expired; answer {0} instead")]
    Expired(String),
    #[error("the step is not waiting for a resolution")]
    NotUnknown,
    #[error("setup is not ready")]
    NotReady,
    #[error("the state could not be saved: {0}")]
    Persist(String),
}

/// What a provisioning step needs. `setup_id` is the allocation key: the same key must name
/// the same disk and runtime directory.
#[derive(Debug, Clone)]
pub struct StepRequest {
    pub setup_id: String,
    pub revision: u64,
    pub step: Step,
    pub resources: Resources,
}

#[derive(Debug, Clone, PartialEq)]
pub enum StepOutcome {
    Done,
    /// A host pause or capacity gate delays work without consuming an attempt.
    Deferred,
    Wait {
        kind: WaitKind,
        ttl_secs: Option<u64>,
    },
    /// Safe to retry. Costs one attempt.
    Retry(String),
    /// Cannot succeed on retry. Uses all attempts.
    Fatal(String),
    /// An external effect may or may not have happened.
    Unknown(String),
}

/// The seam to the computer. A step must be safe to run again after a crash.
pub trait Provisioner: Send + Sync {
    fn run<'a>(&'a self, req: &'a StepRequest) -> BoxFuture<'a, StepOutcome>;
}

impl Setup {
    /// `id` names the bot and its runtime directory. The harness accepts the same characters.
    pub fn new(id: &str, mut draft: Draft) -> Result<Self, SetupError> {
        let ok = !id.is_empty()
            && id.len() <= 64
            && id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
        if !ok {
            return Err(SetupError::Invalid(
                "the id needs 1 to 64 of A-Za-z0-9_-".into(),
            ));
        }
        draft.revision = 1;
        Ok(Self {
            id: id.into(),
            draft,
            approval: None,
            max_attempts: DEFAULT_ATTEMPTS,
            steps: vec![],
            wait: None,
            wait_seq: 0,
            started: false,
        })
    }

    /// Change the draft. The caller names the revision it saw. Returns the new revision.
    pub fn edit(&mut self, expected: u64, f: impl FnOnce(&mut Draft)) -> Result<u64, SetupError> {
        if self.approval.is_some() {
            return Err(SetupError::Locked);
        }
        let current = self.draft.revision;
        if expected != current {
            return Err(SetupError::RevisionConflict { expected, current });
        }
        f(&mut self.draft);
        self.draft.revision = current + 1;
        Ok(current + 1)
    }

    /// Approve one revision. The caller must be an authenticated owner operation. A repeat of
    /// the same approval changes nothing. `limits` come from reviewed configuration.
    pub fn approve(&mut self, revision: u64, by: &str, limits: &Limits) -> Result<(), SetupError> {
        let current = self.draft.revision;
        if revision != current {
            return Err(SetupError::RevisionConflict {
                expected: revision,
                current,
            });
        }
        if self.approval.is_some() {
            return Ok(());
        }
        let d = &self.draft;
        let r = &d.resources;
        let bad = |m: &str| Err(SetupError::Invalid(m.into()));
        if [&d.name, &d.instructions, &d.model]
            .iter()
            .any(|t| t.trim().is_empty())
        {
            return bad("name, instructions and model are required");
        }
        if by.trim().is_empty() {
            return bad("the approver is required");
        }
        if r.cpus == 0 || r.memory_mib == 0 || r.disk_gib == 0 {
            return bad("resources must be above zero");
        }
        if r.cpus > limits.max_cpus
            || r.memory_mib > limits.max_memory_mib
            || r.disk_gib > limits.max_disk_gib
        {
            return bad("resources exceed the configured limits");
        }
        let mut seen = std::collections::HashSet::new();
        if d.credentials
            .iter()
            .any(|c| c.trim().is_empty() || !seen.insert(c))
        {
            return bad("credential labels must be unique and not empty");
        }
        let mut order = vec![Step::Allocate, Step::Boot, Step::InstallExecutor];
        order.extend(d.credentials.iter().map(|c| Step::Credential(c.clone())));
        self.steps = order
            .into_iter()
            .map(|step| StepState {
                step,
                status: Status::Pending,
                attempts: 0,
                last_error: None,
            })
            .collect();
        self.approval = Some(Approval {
            revision,
            by: by.into(),
        });
        Ok(())
    }

    /// The owner changes the attempt limit. A higher limit lets an exhausted step try again.
    pub fn set_max_attempts(&mut self, n: u32) {
        self.max_attempts = n.max(1);
    }

    pub fn phase(&self) -> Phase {
        if self.approval.is_none() {
            return Phase::Draft;
        }
        let any = |f: &dyn Fn(&StepState) -> bool| self.steps.iter().any(f);
        if any(&|s| s.status == Status::Unknown) {
            Phase::Investigate
        } else if any(&|s| s.status == Status::Pending && s.attempts >= self.max_attempts) {
            Phase::Exhausted
        } else if any(&|s| s.status == Status::Waiting) {
            Phase::Waiting
        } else if self.steps.iter().all(|s| s.status == Status::Done) {
            Phase::Ready
        } else {
            Phase::Provisioning
        }
    }

    fn save(
        &self,
        persist: &mut dyn FnMut(&Setup) -> Result<(), String>,
    ) -> Result<(), SetupError> {
        persist(self).map_err(SetupError::Persist)
    }

    fn open_wait(&mut self, step: Step, kind: WaitKind, ttl_secs: Option<u64>, now: u64) {
        self.wait_seq += 1;
        self.wait = Some(Wait {
            request_id: format!("{}:{}", self.id, self.wait_seq),
            step,
            kind,
            expires_at: ttl_secs.map(|t| now + t),
            ttl_secs,
        });
    }

    /// Run steps in order until the setup waits, blocks, runs out of attempts or is ready.
    /// Calling it again after any of those changes nothing until the cause is resolved.
    pub async fn advance(
        &mut self,
        p: &dyn Provisioner,
        persist: &mut dyn FnMut(&Setup) -> Result<(), String>,
        now: u64,
    ) -> Result<Phase, SetupError> {
        let Some(approval) = self.approval.clone() else {
            return Err(SetupError::NotApproved);
        };
        // A step left in flight by a crash may have had effects. It costs one attempt.
        let mut dirty = false;
        for s in &mut self.steps {
            if s.status == Status::InFlight {
                s.status = Status::Pending;
                s.attempts += 1;
                s.last_error = Some("interrupted".into());
                dirty = true;
            }
        }
        if dirty {
            self.save(persist)?;
        }
        while self.phase() == Phase::Provisioning {
            let i = self
                .steps
                .iter()
                .position(|s| s.status != Status::Done)
                .unwrap_or(0);
            let step = self.steps[i].step.clone();
            if let Step::Credential(label) = &step {
                self.steps[i].status = Status::Waiting;
                self.open_wait(
                    step.clone(),
                    WaitKind::Credential {
                        label: label.clone(),
                    },
                    None,
                    now,
                );
                self.save(persist)?;
                continue;
            }
            self.steps[i].status = Status::InFlight;
            self.save(persist)?;
            let req = StepRequest {
                setup_id: self.id.clone(),
                revision: approval.revision,
                step: step.clone(),
                resources: self.draft.resources.clone(),
            };
            let outcome = p.run(&req).await;
            let max = self.max_attempts;
            let st = &mut self.steps[i];
            st.last_error = None;
            match outcome {
                StepOutcome::Deferred => {
                    st.status = Status::Pending;
                    self.save(persist)?;
                    return Ok(self.phase());
                }
                StepOutcome::Done => st.status = Status::Done,
                StepOutcome::Wait { kind, ttl_secs } => {
                    st.status = Status::Waiting;
                    self.open_wait(step, kind, ttl_secs, now);
                }
                StepOutcome::Retry(e) => {
                    st.status = Status::Pending;
                    st.attempts += 1;
                    st.last_error = Some(e);
                }
                StepOutcome::Fatal(e) => {
                    st.status = Status::Pending;
                    st.attempts = st.attempts.max(max);
                    st.last_error = Some(e);
                }
                StepOutcome::Unknown(e) => {
                    st.status = Status::Unknown;
                    st.last_error = Some(e);
                }
            }
            self.save(persist)?;
        }
        Ok(self.phase())
    }

    /// The owner finished the open request. For a credential, the caller confirms that the
    /// protected store accepted the value. An expired request opens a new one and fails with its
    /// identity. A stale or repeated identity fails.
    pub fn complete_wait(&mut self, request_id: &str, now: u64) -> Result<(), SetupError> {
        let Some(w) = self.wait.clone().filter(|w| w.request_id == request_id) else {
            return Err(SetupError::UnknownRequest);
        };
        if w.expires_at.is_some_and(|e| now >= e) {
            self.open_wait(w.step, w.kind, w.ttl_secs, now);
            let id = self
                .wait
                .as_ref()
                .map(|w| w.request_id.clone())
                .unwrap_or_default();
            return Err(SetupError::Expired(id));
        }
        self.wait = None;
        if let Some(s) = self.steps.iter_mut().find(|s| s.step == w.step) {
            s.status = if matches!(w.step, Step::Credential(_)) {
                Status::Done
            } else {
                Status::Pending
            };
        }
        Ok(())
    }

    /// The owner investigated a step with an unknown outcome and says whether it happened.
    pub fn resolve_unknown(&mut self, step: &Step, done: bool) -> Result<(), SetupError> {
        match self
            .steps
            .iter_mut()
            .find(|s| s.step == *step && s.status == Status::Unknown)
        {
            Some(s) => {
                s.status = if done { Status::Done } else { Status::Pending };
                s.last_error = None;
                Ok(())
            }
            None => Err(SetupError::NotUnknown),
        }
    }

    pub fn checklist(&self) -> Vec<ChecklistItem> {
        self.steps
            .iter()
            .map(|s| ChecklistItem {
                label: match &s.step {
                    Step::Allocate => "Private computer allocated".into(),
                    Step::Boot => "Computer started".into(),
                    Step::InstallExecutor => "Executor installed".into(),
                    Step::Credential(l) => format!("Credential: {l}"),
                },
                done: s.status == Status::Done,
            })
            .collect()
    }

    /// Claim the start transition once. The caller must save the setup before it acts on the
    /// result, so a crash loses the start rather than repeating it.
    pub fn claim_start(&mut self) -> Result<Option<Start>, SetupError> {
        if self.phase() != Phase::Ready {
            return Err(SetupError::NotReady);
        }
        if self.started {
            return Ok(None);
        }
        self.started = true;
        Ok(Some(match &self.draft.task {
            Some(t) => Start::Task(t.clone()),
            None => Start::AnnounceReady,
        }))
    }
}

/// Runs the supported harness (`scripts/vm-prototype/vmproto.py`) for one setup. It builds the
/// argument list without a shell and bounds each call.
pub struct HarnessProvisioner {
    pub python: String,
    pub script: PathBuf,
    /// Each setup gets `<runtime_root>/<setup id>`.
    pub runtime_root: PathBuf,
    /// A frozen base image from `freeze-base`.
    pub base: PathBuf,
    pub fixture_port: u16,
    /// The harness fixes these. A draft that asks for others fails at once.
    pub supported: Resources,
    pub limit: Duration,
}

enum Fail {
    /// The call may have had effects.
    Timeout(String),
    Error(String),
}

impl Fail {
    fn msg(&self) -> &str {
        match self {
            Fail::Timeout(m) | Fail::Error(m) => m,
        }
    }
    /// A failed read is safe to retry. A timed-out change is not known.
    fn for_change(self) -> StepOutcome {
        match self {
            Fail::Timeout(m) => StepOutcome::Unknown(m),
            Fail::Error(m) => StepOutcome::Retry(m),
        }
    }
}

impl HarnessProvisioner {
    async fn call(&self, id: &str, sub: &[&str]) -> Result<String, Fail> {
        let mut cmd = Command::new(&self.python);
        cmd.arg("-I")
            .arg(&self.script)
            .arg("--dir")
            .arg(self.runtime_root.join(id))
            .arg("--fixture-port")
            .arg(self.fixture_port.to_string())
            .args(sub)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let out = tokio::time::timeout(self.limit, cmd.output())
            .await
            .map_err(|_| Fail::Timeout(format!("`{}` timed out after {:?}", sub[0], self.limit)))?
            .map_err(|e| Fail::Error(format!("cannot run {}: {e}", self.python)))?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            let err: String = err.trim().chars().take(300).collect();
            return Err(Fail::Error(format!("`{}` failed: {err}", sub[0])));
        }
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }

    async fn status(&self, id: &str) -> Result<Value, Fail> {
        let text = self.call(id, &["status"]).await?;
        serde_json::from_str(text.trim())
            .map_err(|_| Fail::Error("the harness status is not JSON".into()))
    }

    async fn step(&self, req: &StepRequest) -> Result<StepOutcome, Fail> {
        let id = req.setup_id.as_str();
        match &req.step {
            Step::Allocate => {
                if req.resources != self.supported {
                    return Ok(StepOutcome::Fatal(
                        "the harness cannot apply these resources".into(),
                    ));
                }
                let metadata = std::fs::read(self.base.with_extension("json"))
                    .ok()
                    .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
                let Some(metadata) = metadata.filter(|m| m["kind"] == "base") else {
                    return Ok(StepOutcome::Fatal(
                        "the frozen image metadata is missing or invalid".into(),
                    ));
                };
                let Some(expected_base) = metadata["id"].as_str() else {
                    return Ok(StepOutcome::Fatal(
                        "the frozen image has no identity".into(),
                    ));
                };
                if metadata["virtual_size"].as_u64()
                    != Some(u64::from(req.resources.disk_gib) << 30)
                {
                    return Ok(StepOutcome::Fatal(
                        "the frozen image cannot provide the reviewed disk size".into(),
                    ));
                }
                let st = self.status(id).await?;
                if let Some(base) = st["base"].as_str() {
                    return Ok(if base == expected_base {
                        StepOutcome::Done
                    } else {
                        StepOutcome::Unknown(
                            "the existing disk belongs to another image; it was not changed".into(),
                        )
                    });
                }
                if st.get("disk_allocated_bytes").is_some() {
                    return Ok(StepOutcome::Unknown(
                        "a disk exists without an overlay record".into(),
                    ));
                }
                let base = self.base.to_string_lossy();
                Ok(match self.call(id, &["overlay", "--base", &base]).await {
                    Ok(_) => StepOutcome::Done,
                    Err(e) => e.for_change(),
                })
            }
            Step::Boot => {
                let st = self.status(id).await?;
                if st["running"].as_bool().unwrap_or(false) {
                    return Ok(StepOutcome::Done);
                }
                let sub = if st["state_saved"].as_bool().unwrap_or(false) {
                    "restore"
                } else {
                    "start"
                };
                match self.call(id, &[sub]).await {
                    Ok(_) => Ok(StepOutcome::Done),
                    Err(e) => {
                        // A half-started process must not stay behind.
                        let _ = self.call(id, &["kill"]).await;
                        Ok(e.for_change())
                    }
                }
            }
            Step::InstallExecutor => {
                let binding = self.runtime_root.join(id).join("executor.json");
                if binding.exists() {
                    let info = std::fs::read(binding)
                        .ok()
                        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
                    return Ok(
                        if info.as_ref().is_some_and(|i| {
                            i["bot_id"] == id
                                && i["guest_id"].as_str().is_some_and(|g| !g.is_empty())
                        }) {
                            StepOutcome::Done
                        } else {
                            StepOutcome::Unknown("the executor identity is invalid or belongs to another bot; it was not changed".into())
                        },
                    );
                }
                if !self.status(id).await?["running"].as_bool().unwrap_or(false) {
                    return Ok(StepOutcome::Retry("the computer is not running".into()));
                }
                Ok(
                    match self.call(id, &["install-executor", "--bot", id]).await {
                        Ok(_) => StepOutcome::Done,
                        Err(e) => e.for_change(),
                    },
                )
            }
            Step::Credential(_) => Ok(StepOutcome::Fatal(
                "a credential is not a harness step".into(),
            )),
        }
    }
}

impl Provisioner for HarnessProvisioner {
    fn run<'a>(&'a self, req: &'a StepRequest) -> BoxFuture<'a, StepOutcome> {
        Box::pin(async move {
            match self.step(req).await {
                Ok(o) => o,
                Err(e) => StepOutcome::Retry(e.msg().to_string()),
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;

    fn draft() -> Draft {
        Draft {
            revision: 1,
            name: "mailer".into(),
            instructions: "Triage mail".into(),
            model: "m".into(),
            authority: Authority::Full,
            resources: Resources {
                cpus: 2,
                memory_mib: 2048,
                disk_gib: 10,
            },
            budget: None,
            credentials: vec!["mail".into()],
            task: Some("Sort the inbox".into()),
        }
    }

    fn limits() -> Limits {
        Limits {
            max_cpus: 4,
            max_memory_mib: 4096,
            max_disk_gib: 20,
        }
    }

    fn approved() -> Setup {
        let mut s = Setup::new("bot-1", draft()).unwrap();
        s.approve(1, "owner", &limits()).unwrap();
        s
    }

    /// Scripted provisioner: pops one outcome per call for the matching step, else Done.
    #[derive(Default)]
    struct Fake {
        script: Mutex<Vec<(Step, StepOutcome)>>,
        calls: Mutex<Vec<Step>>,
    }

    impl Fake {
        fn with(script: Vec<(Step, StepOutcome)>) -> Self {
            Self {
                script: Mutex::new(script),
                calls: Default::default(),
            }
        }
        fn calls(&self) -> Vec<Step> {
            self.calls.lock().unwrap().clone()
        }
    }

    impl Provisioner for Fake {
        fn run<'a>(&'a self, req: &'a StepRequest) -> BoxFuture<'a, StepOutcome> {
            Box::pin(async move {
                self.calls.lock().unwrap().push(req.step.clone());
                let mut s = self.script.lock().unwrap();
                match s.iter().position(|(st, _)| *st == req.step) {
                    Some(i) => s.remove(i).1,
                    None => StepOutcome::Done,
                }
            })
        }
    }

    fn keep(saved: &mut Vec<Setup>) -> impl FnMut(&Setup) -> Result<(), String> + '_ {
        move |s| {
            saved.push(s.clone());
            Ok(())
        }
    }

    async fn run(s: &mut Setup, p: &Fake, now: u64) -> Phase {
        let mut saved = vec![];
        s.advance(p, &mut keep(&mut saved), now).await.unwrap()
    }

    #[tokio::test]
    async fn no_computer_before_approval() {
        let mut s = Setup::new("bot-1", draft()).unwrap();
        let p = Fake::default();
        let mut saved = vec![];
        let r = s.advance(&p, &mut keep(&mut saved), 0).await;
        assert_eq!(r, Err(SetupError::NotApproved));
        assert!(p.calls().is_empty());
        assert_eq!(s.phase(), Phase::Draft);
    }

    #[test]
    fn edit_checks_the_revision_and_locks_after_approval() {
        let mut s = Setup::new("bot-1", draft()).unwrap();
        assert_eq!(s.edit(1, |d| d.name = "b".into()), Ok(2));
        assert_eq!(
            s.edit(1, |d| d.name = "c".into()),
            Err(SetupError::RevisionConflict {
                expected: 1,
                current: 2
            })
        );
        assert_eq!(s.draft.name, "b");
        s.approve(2, "owner", &limits()).unwrap();
        assert_eq!(s.edit(2, |d| d.name = "d".into()), Err(SetupError::Locked));
    }

    #[test]
    fn approval_names_one_revision() {
        let mut s = Setup::new("bot-1", draft()).unwrap();
        s.edit(1, |d| d.model = "n".into()).unwrap();
        assert_eq!(
            s.approve(1, "owner", &limits()),
            Err(SetupError::RevisionConflict {
                expected: 1,
                current: 2
            })
        );
        assert!(s.approval.is_none());
        assert!(s.steps.is_empty());
    }

    #[test]
    fn approval_is_idempotent_and_keeps_the_first_approver() {
        let mut s = approved();
        let before = s.clone();
        s.approve(1, "other", &limits()).unwrap();
        assert_eq!(s, before);
    }

    #[test]
    fn approval_checks_limits_and_completeness() {
        let mut s = Setup::new("bot-1", draft()).unwrap();
        s.edit(1, |d| d.resources.memory_mib = 9999).unwrap();
        assert!(matches!(
            s.approve(2, "o", &limits()),
            Err(SetupError::Invalid(_))
        ));
        s.edit(2, |d| {
            d.resources.memory_mib = 2048;
            d.name = " ".into();
        })
        .unwrap();
        assert!(matches!(
            s.approve(3, "o", &limits()),
            Err(SetupError::Invalid(_))
        ));
        assert!(Setup::new("bad id!", draft()).is_err());
    }

    #[tokio::test]
    async fn system_steps_run_before_the_credential_request() {
        let mut s = approved();
        let p = Fake::default();
        assert_eq!(run(&mut s, &p, 0).await, Phase::Waiting);
        assert_eq!(
            p.calls(),
            [Step::Allocate, Step::Boot, Step::InstallExecutor]
        );
        let w = s.wait.clone().unwrap();
        assert_eq!(
            w.kind,
            WaitKind::Credential {
                label: "mail".into()
            }
        );
        assert_eq!(w.step, Step::Credential("mail".into()));
    }

    #[tokio::test]
    async fn duplicate_advance_allocates_once() {
        let mut s = approved();
        let p = Fake::default();
        run(&mut s, &p, 0).await;
        run(&mut s, &p, 0).await;
        run(&mut s, &p, 0).await;
        assert_eq!(
            p.calls().iter().filter(|c| **c == Step::Allocate).count(),
            1
        );
    }

    #[tokio::test]
    async fn partial_failure_resumes_after_the_done_steps() {
        let mut s = approved();
        let p = Fake::with(vec![(Step::Boot, StepOutcome::Retry("boot".into()))]);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Waiting);
        assert_eq!(
            p.calls(),
            [
                Step::Allocate,
                Step::Boot,
                Step::Boot,
                Step::InstallExecutor
            ]
        );
        assert_eq!(s.steps[1].attempts, 1);
    }

    #[tokio::test]
    async fn three_total_attempts_then_exhausted_and_owner_can_extend() {
        let mut s = approved();
        let fail = || (Step::Boot, StepOutcome::Retry("no".into()));
        let p = Fake::with(vec![fail(), fail(), fail(), fail()]);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Exhausted);
        assert_eq!(p.calls().iter().filter(|c| **c == Step::Boot).count(), 3);
        // Another advance makes no call.
        assert_eq!(run(&mut s, &p, 0).await, Phase::Exhausted);
        assert_eq!(p.calls().iter().filter(|c| **c == Step::Boot).count(), 3);
        s.set_max_attempts(4);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Exhausted);
        assert_eq!(p.calls().iter().filter(|c| **c == Step::Boot).count(), 4);
        s.set_max_attempts(5);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Waiting);
    }

    #[tokio::test]
    async fn fatal_outcome_uses_all_attempts_at_once() {
        let mut s = approved();
        let p = Fake::with(vec![(Step::Allocate, StepOutcome::Fatal("size".into()))]);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Exhausted);
        assert_eq!(p.calls(), [Step::Allocate]);
    }

    #[tokio::test]
    async fn owner_waits_consume_no_attempt() {
        let mut s = approved();
        let w = |kind| StepOutcome::Wait {
            kind,
            ttl_secs: None,
        };
        let mut script = vec![];
        for _ in 0..5 {
            script.push((Step::InstallExecutor, w(WaitKind::PhoneApproval)));
        }
        let p = Fake::with(script);
        for _ in 0..5 {
            assert_eq!(run(&mut s, &p, 0).await, Phase::Waiting);
            let id = s.wait.clone().unwrap().request_id;
            s.complete_wait(&id, 0).unwrap();
        }
        assert_eq!(s.steps[2].attempts, 0);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Waiting);
        assert_eq!(
            s.wait.as_ref().unwrap().step,
            Step::Credential("mail".into())
        );
    }

    #[tokio::test]
    async fn a_wait_survives_a_restart_through_serialization() {
        let mut s = approved();
        let p = Fake::default();
        run(&mut s, &p, 0).await;
        let json = serde_json::to_string(&s).unwrap();
        let mut back: Setup = serde_json::from_str(&json).unwrap();
        assert_eq!(back, s);
        let p2 = Fake::default();
        assert_eq!(run(&mut back, &p2, 0).await, Phase::Waiting);
        assert!(
            p2.calls().is_empty(),
            "a restart must not redo finished steps"
        );
    }

    #[tokio::test]
    async fn stale_wrong_and_expired_responses_are_rejected() {
        let mut s = approved();
        let p = Fake::with(vec![(
            Step::InstallExecutor,
            StepOutcome::Wait {
                kind: WaitKind::Captcha,
                ttl_secs: Some(60),
            },
        )]);
        run(&mut s, &p, 100).await;
        let w = s.wait.clone().unwrap();
        assert_eq!(w.expires_at, Some(160));
        assert_eq!(
            s.complete_wait("nope", 100),
            Err(SetupError::UnknownRequest)
        );
        let Err(SetupError::Expired(new_id)) = s.complete_wait(&w.request_id, 160) else {
            panic!("expected expiry");
        };
        assert_ne!(new_id, w.request_id);
        assert_eq!(s.wait.as_ref().unwrap().request_id, new_id);
        assert_eq!(s.wait.as_ref().unwrap().expires_at, Some(220));
        assert_eq!(s.steps[2].attempts, 0);
        assert_eq!(
            s.complete_wait(&w.request_id, 161),
            Err(SetupError::UnknownRequest)
        );
        s.complete_wait(&new_id, 161).unwrap();
        assert_eq!(
            s.complete_wait(&new_id, 161),
            Err(SetupError::UnknownRequest)
        );
    }

    #[tokio::test]
    async fn unknown_outcome_blocks_until_the_owner_resolves_it() {
        let mut s = approved();
        let p = Fake::with(vec![(Step::Boot, StepOutcome::Unknown("lost".into()))]);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Investigate);
        assert_eq!(run(&mut s, &p, 0).await, Phase::Investigate);
        assert_eq!(p.calls(), [Step::Allocate, Step::Boot]);
        assert_eq!(
            s.resolve_unknown(&Step::Allocate, true),
            Err(SetupError::NotUnknown)
        );
        s.resolve_unknown(&Step::Boot, false).unwrap();
        assert_eq!(run(&mut s, &p, 0).await, Phase::Waiting);
        assert_eq!(p.calls().iter().filter(|c| **c == Step::Boot).count(), 2);
    }

    #[tokio::test]
    async fn a_crash_in_flight_costs_one_attempt_on_resume() {
        let mut s = approved();
        let p = Fake::default();
        let mut saved = vec![];
        // The persist before the Boot call fails the second time: simulate a crash there.
        let mut n = 0;
        let r = s
            .advance(
                &p,
                &mut |st: &Setup| {
                    n += 1;
                    saved.push(st.clone());
                    if n == 3 { Err("disk".into()) } else { Ok(()) }
                },
                0,
            )
            .await;
        assert!(matches!(r, Err(SetupError::Persist(_))));
        let mut resumed = saved.last().unwrap().clone();
        assert!(resumed.steps.iter().any(|x| x.status == Status::InFlight));
        let p2 = Fake::default();
        run(&mut resumed, &p2, 0).await;
        let boot = &resumed.steps[1];
        assert_eq!(boot.attempts, 1);
        assert_eq!(boot.status, Status::Done);
    }

    #[tokio::test]
    async fn ready_checklist_and_the_task_starts_once() {
        let mut s = approved();
        let p = Fake::default();
        run(&mut s, &p, 0).await;
        assert_eq!(s.claim_start(), Err(SetupError::NotReady));
        let id = s.wait.clone().unwrap().request_id;
        s.complete_wait(&id, 0).unwrap();
        assert_eq!(run(&mut s, &p, 0).await, Phase::Ready);
        assert!(s.checklist().iter().all(|c| c.done));
        assert_eq!(s.checklist().len(), 4);
        assert_eq!(
            s.claim_start(),
            Ok(Some(Start::Task("Sort the inbox".into())))
        );
        assert_eq!(s.claim_start(), Ok(None));
        let back: Setup = serde_json::from_str(&serde_json::to_string(&s).unwrap()).unwrap();
        let mut back = back;
        assert_eq!(back.claim_start(), Ok(None));
    }

    #[tokio::test]
    async fn a_general_role_announces_readiness_once() {
        let mut s = Setup::new(
            "bot-1",
            Draft {
                task: None,
                credentials: vec![],
                ..draft()
            },
        )
        .unwrap();
        s.approve(1, "o", &limits()).unwrap();
        assert_eq!(run(&mut s, &Fake::default(), 0).await, Phase::Ready);
        assert_eq!(s.claim_start(), Ok(Some(Start::AnnounceReady)));
        assert_eq!(s.claim_start(), Ok(None));
    }

    // --- HarnessProvisioner ---

    const FAKE: &str = r#"
import sys, json, os
a = sys.argv[1:]
d = a[a.index("--dir") + 1]
sub = a[a.index("--dir") + 4]
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "log"), "a").write(json.dumps(a) + "\n")
if sub == "status":
    print(json.dumps({"running": os.path.exists(d + "/running"), "state_saved": False,
                      "base": "b" if os.path.exists(d + "/disk.qcow2.json") else None,
                      **({"disk_allocated_bytes": 1} if os.path.exists(d + "/disk.qcow2") else {})}))
elif sub == "overlay":
    os.makedirs(d, exist_ok=True)
    open(d + "/disk.qcow2", "w").write("")
    open(d + "/disk.qcow2.json", "w").write("{}")
elif sub == "start":
    open(d + "/running", "w").write("")
elif sub == "install-executor":
    open(d + "/executor.json", "w").write(json.dumps({"bot_id":"bot-1","guest_id":"fixture"}))
"#;

    fn harness(dir: &std::path::Path) -> HarnessProvisioner {
        std::fs::write(dir.join("fake.py"), FAKE).unwrap();
        std::fs::write(
            dir.join("base.json"),
            serde_json::json!({"kind":"base","id":"b","virtual_size":10_u64 << 30}).to_string(),
        )
        .unwrap();
        HarnessProvisioner {
            python: "python3".into(),
            script: dir.join("fake.py"),
            runtime_root: dir.join("rt"),
            base: dir.join("base.qcow2"),
            fixture_port: 1,
            supported: Resources {
                cpus: 2,
                memory_mib: 2048,
                disk_gib: 10,
            },
            limit: Duration::from_secs(20),
        }
    }

    fn req(step: Step, resources: Resources) -> StepRequest {
        StepRequest {
            setup_id: "bot-1".into(),
            revision: 1,
            step,
            resources,
        }
    }

    fn log(dir: &std::path::Path) -> Vec<String> {
        std::fs::read_to_string(dir.join("log"))
            .unwrap_or_default()
            .lines()
            .map(|l| {
                let a: Vec<String> = serde_json::from_str(l).unwrap();
                a[a.iter().position(|x| x == "--dir").unwrap() + 4..].join(" ")
            })
            .collect()
    }

    #[tokio::test]
    async fn harness_steps_are_idempotent_and_ordered() {
        let d = crate::testutil::temp_dir();
        let h = harness(&d);
        let r = draft().resources;
        for _ in 0..2 {
            for st in [Step::Allocate, Step::Boot, Step::InstallExecutor] {
                assert_eq!(h.run(&req(st, r.clone())).await, StepOutcome::Done);
            }
        }
        let subs: Vec<String> = log(&d)
            .into_iter()
            .filter(|l| !l.starts_with("status"))
            .collect();
        assert_eq!(
            subs,
            [
                format!("overlay --base {}", d.join("base.qcow2").display()),
                "start".to_string(),
                "install-executor --bot bot-1".to_string()
            ]
        );
        assert!(d.join("rt/bot-1/executor.json").exists());
    }

    #[tokio::test]
    async fn harness_rejects_an_executor_bound_to_another_bot() {
        let d = crate::testutil::temp_dir();
        let h = harness(&d);
        std::fs::create_dir_all(d.join("rt/bot-1")).unwrap();
        std::fs::write(
            d.join("rt/bot-1/executor.json"),
            serde_json::json!({"bot_id":"other","guest_id":"fixture"}).to_string(),
        )
        .unwrap();
        assert!(matches!(
            h.run(&req(Step::InstallExecutor, draft().resources)).await,
            StepOutcome::Unknown(_)
        ));
        assert!(log(&d).is_empty());
    }

    #[tokio::test]
    async fn harness_rejects_a_runtime_from_another_base() {
        let d = crate::testutil::temp_dir();
        let h = harness(&d);
        std::fs::create_dir_all(d.join("rt/bot-1")).unwrap();
        std::fs::write(d.join("rt/bot-1/disk.qcow2.json"), "{}").unwrap();
        std::fs::write(
            d.join("base.json"),
            serde_json::json!({"kind":"base","id":"other","virtual_size":10_u64 << 30}).to_string(),
        )
        .unwrap();
        assert!(matches!(
            h.run(&req(Step::Allocate, draft().resources)).await,
            StepOutcome::Unknown(_)
        ));
    }

    #[tokio::test]
    async fn harness_rejects_a_disk_size_it_cannot_provide() {
        let d = crate::testutil::temp_dir();
        let h = harness(&d);
        std::fs::write(
            d.join("base.json"),
            serde_json::json!({"kind":"base","id":"b","virtual_size":20_u64 << 30}).to_string(),
        )
        .unwrap();
        assert!(matches!(
            h.run(&req(Step::Allocate, draft().resources)).await,
            StepOutcome::Fatal(_)
        ));
        assert!(!d.join("rt/bot-1").exists());
    }

    #[tokio::test]
    async fn harness_refuses_resources_it_cannot_apply() {
        let d = crate::testutil::temp_dir();
        let h = harness(&d);
        let mut r = draft().resources;
        r.cpus = 4;
        assert!(matches!(
            h.run(&req(Step::Allocate, r)).await,
            StepOutcome::Fatal(_)
        ));
        assert!(log(&d).is_empty(), "the harness must not run");
    }

    #[tokio::test]
    async fn harness_half_made_disk_is_unknown_not_overwritten() {
        let d = crate::testutil::temp_dir();
        let h = harness(&d);
        std::fs::create_dir_all(d.join("rt/bot-1")).unwrap();
        std::fs::write(d.join("rt/bot-1/disk.qcow2"), "").unwrap();
        let out = h.run(&req(Step::Allocate, draft().resources)).await;
        assert!(matches!(out, StepOutcome::Unknown(_)));
        assert!(log(&d).iter().all(|l| l.starts_with("status")));
    }

    #[tokio::test]
    async fn harness_failure_is_retryable_and_never_leaks_through_a_shell() {
        let d = crate::testutil::temp_dir();
        let mut h = harness(&d);
        h.runtime_root = d.join("x; touch pwned");
        std::fs::write(d.join("fake.py"), "import sys; sys.exit('boom')").unwrap();
        let out = h.run(&req(Step::Boot, draft().resources)).await;
        assert!(matches!(out, StepOutcome::Retry(ref m) if m.contains("boom")));
        assert!(!d.join("pwned").exists());
    }

    #[test]
    fn credential_requests_carry_no_secret_field() {
        let mut s = approved();
        s.steps[0].status = Status::Done;
        let text = serde_json::to_string(&s).unwrap();
        assert!(text.contains("\"Credential\""));
        assert!(!text.to_lowercase().contains("secret"));
    }
}
