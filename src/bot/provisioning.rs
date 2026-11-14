//! Reviewed setup driver. Host configuration supplies the image and capacity.
use std::collections::BTreeMap;
use std::sync::{Arc, RwLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use super::{
    Bot,
    setup::{Draft, HarnessProvisioner, Phase, Provisioner},
    setup_store::Record,
};
use crate::executor::client::private_dir;
use crate::executor::proto::{Sha256, valid_id};
use crate::{agent::Agent, config::Config, model::ModelClient, store::Store};
use anyhow::{Context, ensure};
use tokio::sync::Notify;

pub struct Runtime {
    pub bot: Arc<Bot>,
    pub agent: Arc<Agent>,
}

pub struct Service {
    pub config: Arc<Config>,
    pub store: Store,
    model: ModelClient,
    runtimes: RwLock<BTreeMap<String, Arc<Runtime>>>,
    pub errors: RwLock<BTreeMap<String, String>>,
    kick: Notify,
}

impl Service {
    pub fn new(config: Arc<Config>, store: Store, model: ModelClient) -> Arc<Self> {
        Arc::new(Self {
            config,
            store,
            model,
            runtimes: RwLock::default(),
            errors: RwLock::default(),
            kick: Notify::new(),
        })
    }

    pub fn notify(&self) {
        self.kick.notify_one();
    }

    pub fn runtime(&self, id: &str) -> Option<Arc<Runtime>> {
        self.runtimes.read().unwrap().get(id).cloned()
    }

    pub fn for_chat(&self, chat: i64) -> Option<Arc<Runtime>> {
        self.runtimes
            .read()
            .unwrap()
            .values()
            .find(|r| r.bot.chat_id() == chat)
            .cloned()
    }

    /// A changed host profile cannot silently move an approved computer to another disk.
    pub fn profile(&self) -> anyhow::Result<String> {
        let p = self
            .config
            .provisioning
            .as_ref()
            .context("provisioning is not configured")?;
        let mut hash = Sha256::new();
        hash.update(
            format!(
                "{:?};{:?};{:?};{:?};{};{};{};{};{}",
                p.base,
                p.runtime_root,
                p.template,
                p.resources,
                self.config.model.agent_model,
                p.accounting.price_version,
                p.accounting.input_per_million,
                p.accounting.output_per_million,
                p.accounting.extra_per_call
            )
            .as_bytes(),
        );
        Ok(format!("{}:{}", p.accounting.policy_version, hash.hex()))
    }

    pub fn check_draft(&self, d: &Draft) -> anyhow::Result<()> {
        let p = self
            .config
            .provisioning
            .as_ref()
            .context("provisioning is not configured")?;
        ensure!(
            d.model == self.config.model.agent_model,
            "the model has no configured provisioning price profile"
        );
        ensure!(
            d.resources == p.resources,
            "the current harness cannot apply those resource settings"
        );
        let b = d
            .budget
            .as_ref()
            .context("review the spending ceiling before approval")?;
        b.validate()?;
        ensure!(
            b.currency == p.accounting.currency && b.exponent == p.accounting.exponent,
            "budget currency and precision must match configured prices"
        );
        Ok(())
    }

    pub fn bot_config(&self, row: &Record) -> anyhow::Result<Config> {
        self.check_draft(&row.setup.draft)?;
        let p = self
            .config
            .provisioning
            .as_ref()
            .context("provisioning is not configured")?;
        let budget = row.setup.draft.budget.as_ref().unwrap();
        let mut c = (*self.config).clone();
        let mut vm = p.template.clone();
        vm.bot_id = row.setup.id.clone();
        vm.runtime_dir = p.runtime_root.join(&row.setup.id);
        c.vm = Some(vm);
        c.events = None;
        c.provisioning = None;
        c.data_dir = self.config.data_dir.join("bots").join(&row.setup.id);
        c.workspace_dir = Some(c.data_dir.join("workspace"));
        c.bare.secrets_file = None;
        c.bare.state_dir = None;
        let mut accounting = p.accounting.clone();
        accounting.policy_version = self.profile()?;
        accounting.currency = budget.currency.clone();
        accounting.exponent = budget.exponent;
        accounting.period = budget.period.clone();
        accounting.ceiling = budget.ceiling;
        accounting.allowance = budget.allowance;
        c.accounting = Some(accounting);
        Ok(c)
    }

    /// Create the private data folder and workspace of an approved bot. Only folders below the
    /// configured data root are made. A link or a foreign folder fails the setup.
    fn prepare_folders(&self, id: &str, c: &Config) -> anyhow::Result<()> {
        ensure!(valid_id(id), "bad bot ID");
        private_dir(&self.config.data_dir, &c.data_dir)?;
        private_dir(&self.config.data_dir, &c.workspace())
    }

    pub fn start(self: &Arc<Self>) {
        let this = self.clone();
        tokio::spawn(async move {
            loop {
                if let Err(e) = this.scan().await {
                    tracing::error!("setup driver failed: {e:#}");
                }
                this.kick.notified().await;
            }
        });
    }

    async fn scan(&self) -> anyhow::Result<()> {
        let Some(p) = &self.config.provisioning else {
            return Ok(());
        };
        let rows = self.store.setup_records().await?;
        for row in rows {
            if row.setup.approval.is_none() || self.runtime(&row.setup.id).is_some() {
                continue;
            }
            let id = row.setup.id.clone();
            if self.runtimes.read().unwrap().len() >= p.max_bots {
                self.errors
                    .write()
                    .unwrap()
                    .insert(id, "Waiting for configured computer capacity".into());
                continue;
            }
            let result = async {
                let a = self
                    .store
                    .accounting(&id)
                    .await?
                    .context("approved setup has no accounting record")?;
                ensure!(
                    a.policy_version == self.profile()?,
                    "the approved host profile changed; review migration before provisioning"
                );
                if row.setup.phase() != Phase::Ready && !self.store.holds(&id).await?.is_empty() {
                    return Ok(());
                }
                let provisioner: Arc<dyn Provisioner> = Arc::new(HarnessProvisioner {
                    python: p.template.python.clone(),
                    script: p.template.harness.clone(),
                    runtime_root: p.runtime_root.clone(),
                    base: p.base.clone(),
                    fixture_port: p.template.fixture_port,
                    supported: p.resources.clone(),
                    limit: Duration::from_secs(p.template.lifecycle_secs),
                });
                let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                drive(self.store.clone(), row, provisioner, now).await?;
                let ready = self
                    .store
                    .setup_record(&id)
                    .await?
                    .context("setup disappeared")?;
                if ready.setup.phase() == Phase::Ready {
                    self.store.finish_setup(&id).await?;
                    let c = Arc::new(self.bot_config(&ready)?);
                    self.prepare_folders(&id, &c)?;
                    let (agent, bot) =
                        super::runtime(c, self.store.clone(), self.model.clone()).await?;
                    self.runtimes
                        .write()
                        .unwrap()
                        .insert(id.clone(), Arc::new(Runtime { agent, bot }));
                }
                Ok::<_, anyhow::Error>(())
            }
            .await;
            match result {
                Ok(()) => {
                    self.errors.write().unwrap().remove(&id);
                }
                Err(e) => {
                    tracing::error!("setup {id} stopped: {e:#}");
                    self.errors.write().unwrap().insert(id, "Setup stopped. Review its saved state and server error log before retrying.".into());
                }
            }
        }
        Ok(())
    }
}

struct Guard {
    store: Store,
    inner: Arc<dyn Provisioner>,
}
impl Provisioner for Guard {
    fn run<'a>(
        &'a self,
        req: &'a super::setup::StepRequest,
    ) -> futures_util::future::BoxFuture<'a, super::setup::StepOutcome> {
        Box::pin(async move {
            match self.store.holds(&req.setup_id).await {
                Ok(holds) if holds.is_empty() => self.inner.run(req).await,
                Ok(_) => super::setup::StepOutcome::Deferred,
                Err(_) => super::setup::StepOutcome::Unknown(
                    "could not verify host holds before provisioning".into(),
                ),
            }
        })
    }
}

/// Run asynchronous harness calls from a blocking worker. Its save callback commits synchronously.
pub async fn drive(
    store: Store,
    mut row: Record,
    p: Arc<dyn Provisioner>,
    now: u64,
) -> anyhow::Result<Phase> {
    let p = Guard {
        store: store.clone(),
        inner: p,
    };
    let handle = tokio::runtime::Handle::current();
    tokio::task::spawn_blocking(move || {
        handle
            .block_on(row.setup.advance(
                &p,
                &mut |s| {
                    row.version = store
                        .persist_setup(row.version, s)
                        .map_err(|e| e.to_string())?;
                    Ok(())
                },
                now,
            ))
            .map_err(anyhow::Error::from)
    })
    .await?
}

#[cfg(test)]
mod tests {
    use super::super::setup::{
        Authority, Budget, Limits, Resources, Step, StepOutcome, StepRequest, WaitKind,
    };
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Mutex;
    struct Fake {
        calls: Mutex<Vec<Step>>,
        wait: std::sync::atomic::AtomicBool,
    }
    impl Provisioner for Fake {
        fn run<'a>(
            &'a self,
            req: &'a StepRequest,
        ) -> futures_util::future::BoxFuture<'a, StepOutcome> {
            Box::pin(async move {
                self.calls.lock().unwrap().push(req.step.clone());
                if req.step == Step::Boot
                    && self.wait.swap(false, std::sync::atomic::Ordering::SeqCst)
                {
                    StepOutcome::Wait {
                        kind: WaitKind::PhoneApproval,
                        ttl_secs: None,
                    }
                } else {
                    StepOutcome::Done
                }
            })
        }
    }
    #[tokio::test]
    async fn a_real_store_preserves_waits_and_starts_the_task_once_after_restart() {
        let dir = crate::testutil::temp_dir();
        let store = Store::open(&dir).unwrap();
        let setup = super::super::setup::Setup::new(
            "bot",
            Draft {
                revision: 1,
                name: "Fixture".into(),
                instructions: "Read fixture".into(),
                model: "fixture".into(),
                authority: Authority::Full,
                resources: Resources {
                    cpus: 2,
                    memory_mib: 2048,
                    disk_gib: 10,
                },
                budget: Some(Budget {
                    currency: "TST".into(),
                    exponent: 2,
                    period: "test".into(),
                    allowance: 10,
                    ceiling: 100,
                }),
                credentials: vec![],
                task: Some("Read fixture".into()),
            },
        )
        .unwrap();
        store.create_setup(&setup).await.unwrap();
        store
            .approve_setup(
                "bot",
                1,
                1,
                "owner",
                &Limits {
                    max_cpus: 2,
                    max_memory_mib: 2048,
                    max_disk_gib: 10,
                },
                "test",
            )
            .await
            .unwrap();
        let fake = Arc::new(Fake {
            calls: Mutex::default(),
            wait: std::sync::atomic::AtomicBool::new(true),
        });
        let row = store.setup_record("bot").await.unwrap().unwrap();
        assert_eq!(
            drive(store.clone(), row, fake.clone(), 100).await.unwrap(),
            Phase::Waiting
        );
        drop(store);
        let store = Store::open(&dir).unwrap();
        let mut row = store.setup_record("bot").await.unwrap().unwrap();
        assert_eq!(row.setup.steps[1].attempts, 0);
        let request = row.setup.wait.as_ref().unwrap().request_id.clone();
        row.setup.complete_wait(&request, 200).unwrap();
        store
            .save_setup_state(row.version, &row.setup)
            .await
            .unwrap();
        store.set_paused("bot", true).await.unwrap();
        let row = store.setup_record("bot").await.unwrap().unwrap();
        assert_eq!(
            drive(store.clone(), row, fake.clone(), 200).await.unwrap(),
            Phase::Provisioning
        );
        assert_eq!(fake.calls.lock().unwrap().len(), 2);
        store.set_paused("bot", false).await.unwrap();
        let row = store.setup_record("bot").await.unwrap().unwrap();
        assert_eq!(
            drive(store.clone(), row, fake.clone(), 200).await.unwrap(),
            Phase::Ready
        );
        assert!(store.finish_setup("bot").await.unwrap().is_some());
        drop(store);
        let store = Store::open(&dir).unwrap();
        assert!(store.finish_setup("bot").await.unwrap().is_none());
        assert_eq!(store.tasks("bot", 10).await.unwrap().len(), 1);
        assert_eq!(
            fake.calls
                .lock()
                .unwrap()
                .iter()
                .filter(|s| **s == Step::Allocate)
                .count(),
            1
        );
    }
    #[tokio::test]
    async fn a_ready_but_held_bot_registers_without_waking_so_the_owner_can_respond() {
        let (_, store, dir, original) = super::super::setup_api::tests::app();
        let script = dir.join("status-only.py");
        std::fs::write(&script,"import sys\nassert sys.argv[-1] == 'status'\nprint('{\"running\":false,\"state_saved\":true}')\n").unwrap();
        let mut config = (*original.config).clone();
        config.provisioning.as_mut().unwrap().template.harness = script;
        let model = ModelClient::new(&config.model.base_url, None, &config.model.api_key_env);
        let service = Service::new(Arc::new(config), store.clone(), model);
        let p = service.config.provisioning.as_ref().unwrap();
        let setup = super::super::setup::Setup::new(
            "held",
            Draft {
                revision: 1,
                name: "Held bot".into(),
                instructions: "Read fixture".into(),
                model: service.config.model.agent_model.clone(),
                authority: Authority::Full,
                resources: p.resources.clone(),
                budget: Some(Budget {
                    currency: "TST".into(),
                    exponent: 2,
                    period: "test".into(),
                    allowance: 10,
                    ceiling: 100,
                }),
                credentials: vec![],
                task: None,
            },
        )
        .unwrap();
        store.create_setup(&setup).await.unwrap();
        store
            .approve_setup(
                "held",
                1,
                1,
                "owner",
                &p.limits,
                &service.profile().unwrap(),
            )
            .await
            .unwrap();
        let mut row = store.setup_record("held").await.unwrap().unwrap();
        for step in &mut row.setup.steps {
            step.status = super::super::setup::Status::Done;
        }
        store
            .save_setup_state(row.version, &row.setup)
            .await
            .unwrap();
        store
            .require_ceiling_review("held", "test ceiling hold")
            .await
            .unwrap();
        // The store creates the configured root at startup. Only the bot folders may be missing.
        std::fs::create_dir_all(&service.config.data_dir).unwrap();
        service.scan().await.unwrap();
        let runtime = service
            .runtime("held")
            .unwrap_or_else(|| panic!("{:?}", service.errors.read().unwrap()));
        let bot = service.config.data_dir.join("bots").join("held");
        assert_eq!(
            std::fs::metadata(&bot).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert!(bot.join("workspace").is_dir(), "no workspace folder");
        assert!(
            runtime
                .bot
                .inbox()
                .await
                .unwrap()
                .iter()
                .any(|i| i.kind == "ceiling_request")
        );
        assert_eq!(runtime.agent.model_calls(), 0);
        assert!(
            store
                .holds("held")
                .await
                .unwrap()
                .iter()
                .any(|h| h.kind == super::super::state::HoldKind::Ceiling)
        );
        runtime.bot.stop_loop();
    }

    fn fixture_draft(service: &Service) -> Draft {
        let p = service.config.provisioning.as_ref().unwrap();
        Draft {
            revision: 1,
            name: "Fixture bot".into(),
            instructions: "Read fixture".into(),
            model: service.config.model.agent_model.clone(),
            authority: Authority::Full,
            resources: p.resources.clone(),
            budget: Some(Budget {
                currency: "TST".into(),
                exponent: 2,
                period: "test".into(),
                allowance: 10,
                ceiling: 100,
            }),
            credentials: vec![],
            task: None,
        }
    }

    #[tokio::test]
    async fn an_unapproved_setup_allocates_no_bot_folders() {
        let (_, store, _dir, service) = super::super::setup_api::tests::app();
        std::fs::create_dir_all(&service.config.data_dir).unwrap();
        let setup = super::super::setup::Setup::new("draft", fixture_draft(&service)).unwrap();
        store.create_setup(&setup).await.unwrap();
        service.scan().await.unwrap();
        let row = store.setup_record("draft").await.unwrap().unwrap();
        // bot_config is also read for protected endpoints. It must not create folders either.
        service.bot_config(&row).unwrap();
        assert!(!service.config.data_dir.join("bots").exists());
        assert!(service.runtime("draft").is_none());
    }

    #[tokio::test]
    async fn a_linked_bot_folder_stops_setup_and_writes_nothing_behind_the_link() {
        let (_, store, dir, original) = super::super::setup_api::tests::app();
        let script = dir.join("status-only.py");
        std::fs::write(&script,"import sys\nassert sys.argv[-1] == 'status'\nprint('{\"running\":false,\"state_saved\":true}')\n").unwrap();
        let mut config = (*original.config).clone();
        config.provisioning.as_mut().unwrap().template.harness = script;
        let model = ModelClient::new(&config.model.base_url, None, &config.model.api_key_env);
        let service = Service::new(Arc::new(config), store.clone(), model);
        let data = &service.config.data_dir;
        let target = dir.join("elsewhere");
        std::fs::create_dir_all(data.join("bots")).unwrap();
        std::fs::create_dir(&target).unwrap();
        std::os::unix::fs::symlink(&target, data.join("bots").join("linked")).unwrap();
        let setup = super::super::setup::Setup::new("linked", fixture_draft(&service)).unwrap();
        store.create_setup(&setup).await.unwrap();
        let p = service.config.provisioning.as_ref().unwrap();
        store
            .approve_setup(
                "linked",
                1,
                1,
                "owner",
                &p.limits,
                &service.profile().unwrap(),
            )
            .await
            .unwrap();
        let mut row = store.setup_record("linked").await.unwrap().unwrap();
        for step in &mut row.setup.steps {
            step.status = super::super::setup::Status::Done;
        }
        store
            .save_setup_state(row.version, &row.setup)
            .await
            .unwrap();
        store
            .require_ceiling_review("linked", "test ceiling hold")
            .await
            .unwrap();
        service.scan().await.unwrap();
        assert!(service.runtime("linked").is_none());
        assert!(service.errors.read().unwrap().contains_key("linked"));
        assert_eq!(std::fs::read_dir(&target).unwrap().count(), 0);
    }
}
