//! Authenticated setup and managed-bot routes. Machine event intake exposes none of these.
use super::{
    provisioning::Service,
    secret_requests_api::{Woke, delivered_text, expire, resume_delivered, sender, wake},
    secrets::{self, Failure, HarnessChannel, Secret},
    setup::{Authority, Budget, Draft, Setup, WaitKind},
};
use crate::{api::AppState, auth::User};
use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{Path, Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;
use tower::ServiceExt;

type Error = (StatusCode, Json<Value>);
fn error(code: StatusCode, message: &str) -> Error {
    (code, Json(json!({"error":message})))
}
fn conflict(e: impl std::fmt::Display) -> Error {
    error(StatusCode::CONFLICT, &e.to_string())
}
fn service(st: &AppState) -> Result<Arc<Service>, Error> {
    st.setups.clone().ok_or_else(|| {
        error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Bot provisioning is not configured",
        )
    })
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/setup-options", get(options))
        .route("/api/setups", get(list).post(create))
        .route("/api/setups/{id}", post(edit))
        .route("/api/setups/{id}/approve", post(approve))
        .route("/api/setups/{id}/control", post(control))
        .route("/api/setups/{id}/secret", post(setup_secret))
        .route("/api/bots/{id}/secret", post(task_secret))
        .route("/api/bots/{id}", get(bot_root))
        .route("/api/bots/{id}/{*rest}", axum::routing::any(bot_path))
}

/// Whether the harness has the guest delivery command. The probe never carries a value. A
/// recent answer is reused, so a page load does not start a process each time.
async fn delivery_supported(vm: &crate::config::Vm) -> bool {
    type Seen = Option<(std::path::PathBuf, bool, std::time::Instant)>;
    static SEEN: std::sync::Mutex<Seen> = std::sync::Mutex::new(None);
    if let Some((path, ok, at)) = SEEN.lock().unwrap().as_ref()
        && *path == vm.harness
        && at.elapsed() < std::time::Duration::from_secs(60)
    {
        return *ok;
    }
    let ok = HarnessChannel::new(vm).supported().await;
    *SEEN.lock().unwrap() = Some((vm.harness.clone(), ok, std::time::Instant::now()));
    ok
}

async fn options(State(st): State<AppState>) -> Json<Value> {
    let Some(s) = st.setups else {
        return Json(json!({"enabled":false}));
    };
    let p = s.config.provisioning.as_ref().unwrap();
    let protected = delivery_supported(&p.template).await;
    Json(
        json!({"enabled":true, "model":s.config.model.agent_model, "resources":p.resources,
        "budget":Budget { currency:p.accounting.currency.clone(), exponent:p.accounting.exponent, period:p.accounting.period.clone(), allowance:p.accounting.allowance, ceiling:p.accounting.ceiling },
        "protected_input":protected }),
    )
}

async fn list(State(st): State<AppState>) -> Result<Json<Value>, Error> {
    let rows = st.store.setup_records().await.map_err(conflict)?;
    let mut out = Vec::new();
    for row in rows {
        let message = st
            .setups
            .as_ref()
            .and_then(|s| s.errors.read().unwrap().get(&row.setup.id).cloned());
        let ready = st
            .setups
            .as_ref()
            .is_some_and(|s| s.runtime(&row.setup.id).is_some());
        out.push(json!({"version":row.version,"chat_id":row.chat_id,"phase":row.setup.phase(),"checklist":row.setup.checklist(),"setup":row.setup,"runtime_ready":ready,"error":message}));
    }
    Ok(Json(json!(out)))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct New {
    job: String,
}
async fn create(
    State(st): State<AppState>,
    Json(body): Json<New>,
) -> Result<(StatusCode, Json<Value>), Error> {
    let s = service(&st)?;
    if body.job.trim().is_empty() || body.job.chars().count() > 20000 {
        return Err(error(
            StatusCode::BAD_REQUEST,
            "Describe a job in 1 to 20000 characters",
        ));
    }
    let p = s.config.provisioning.as_ref().unwrap();
    let name = body
        .job
        .split_whitespace()
        .take(5)
        .collect::<Vec<_>>()
        .join(" ");
    let id = uuid::Uuid::new_v4().simple().to_string();
    let setup = Setup::new(
        &id,
        Draft {
            revision: 1,
            name,
            instructions: body.job,
            model: s.config.model.agent_model.clone(),
            authority: Authority::Full,
            resources: p.resources.clone(),
            budget: Some(Budget {
                currency: p.accounting.currency.clone(),
                exponent: p.accounting.exponent,
                period: p.accounting.period.clone(),
                allowance: p.accounting.allowance,
                ceiling: p.accounting.ceiling,
            }),
            credentials: vec![],
            task: None,
        },
    )
    .map_err(conflict)?;
    st.store.create_setup(&setup).await.map_err(conflict)?;
    Ok((
        StatusCode::CREATED,
        Json(
            serde_json::to_value(st.store.setup_record(&id).await.map_err(conflict)?.unwrap())
                .map_err(conflict)?,
        ),
    ))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Edit {
    version: i64,
    draft: Draft,
}
async fn edit(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<Edit>,
) -> Result<StatusCode, Error> {
    service(&st)?;
    let mut row = st
        .store
        .setup_record(&id)
        .await
        .map_err(conflict)?
        .ok_or_else(|| error(StatusCode::NOT_FOUND, "Setup not found"))?;
    if row.version != body.version {
        return Err(error(StatusCode::CONFLICT, "Setup changed; reload it"));
    }
    if body.draft.name.chars().count() > 200 || body.draft.instructions.chars().count() > 20000 {
        return Err(error(
            StatusCode::BAD_REQUEST,
            "Setup text exceeds its limit",
        ));
    }
    row.setup
        .edit(row.setup.draft.revision, |d| *d = body.draft)
        .map_err(conflict)?;
    st.store
        .save_setup_state(row.version, &row.setup)
        .await
        .map_err(conflict)?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Approval {
    version: i64,
    revision: u64,
}
async fn approve(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Extension(User(actor)): Extension<User>,
    Json(body): Json<Approval>,
) -> Result<StatusCode, Error> {
    let s = service(&st)?;
    let row = st
        .store
        .setup_record(&id)
        .await
        .map_err(conflict)?
        .ok_or_else(|| error(StatusCode::NOT_FOUND, "Setup not found"))?;
    s.check_draft(&row.setup.draft).map_err(conflict)?;
    let p = s.config.provisioning.as_ref().unwrap();
    st.store
        .approve_setup(
            &id,
            body.version,
            body.revision,
            &actor,
            &p.limits,
            &s.profile().map_err(conflict)?,
        )
        .await
        .map_err(conflict)?;
    s.notify();
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
enum Action {
    Attempts {
        value: u32,
    },
    ResolveUnknown {
        step: super::setup::Step,
        done: bool,
    },
    CompleteWait {
        request_id: String,
    },
    Pause {
        value: bool,
    },
    Retry,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Control {
    version: i64,
    command: Action,
}
async fn control(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<Control>,
) -> Result<StatusCode, Error> {
    let s = service(&st)?;
    let mut row = st
        .store
        .setup_record(&id)
        .await
        .map_err(conflict)?
        .ok_or_else(|| error(StatusCode::NOT_FOUND, "Setup not found"))?;
    if row.version != body.version {
        return Err(error(StatusCode::CONFLICT, "Setup changed; reload it"));
    }
    match body.command {
        Action::Attempts { value } => {
            if value == 0 {
                return Err(error(
                    StatusCode::BAD_REQUEST,
                    "Attempt limit must be positive",
                ));
            }
            row.setup.set_max_attempts(value);
        }
        Action::ResolveUnknown { step, done } => {
            row.setup.resolve_unknown(&step, done).map_err(conflict)?
        }
        Action::CompleteWait { request_id } => {
            if row
                .setup
                .wait
                .as_ref()
                .is_some_and(|w| matches!(w.kind, WaitKind::Credential { .. }))
            {
                return Err(error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Protected credential transfer is not available yet. Do not send credentials in chat.",
                ));
            }
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(conflict)?
                .as_secs();
            let answer = row.setup.complete_wait(&request_id, now);
            st.store
                .save_setup_state(row.version, &row.setup)
                .await
                .map_err(conflict)?;
            answer.map_err(conflict)?;
            s.notify();
            return Ok(StatusCode::NO_CONTENT);
        }
        Action::Pause { value } => {
            if let Some(runtime) = s.runtime(&id) {
                runtime.bot.set_paused(value).await.map_err(|_| {
                    error(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "Could not update automation pause",
                    )
                })?;
            } else {
                st.store.set_paused(&id, value).await.map_err(conflict)?;
            }
            s.notify();
            return Ok(StatusCode::NO_CONTENT);
        }
        Action::Retry => {}
    }
    st.store
        .save_setup_state(row.version, &row.setup)
        .await
        .map_err(conflict)?;
    s.notify();
    Ok(StatusCode::NO_CONTENT)
}

fn body<T: serde::de::DeserializeOwned>(bytes: &Bytes) -> Result<T, Error> {
    // The parse error stays out of the reply: it could quote part of the value.
    serde_json::from_slice(bytes)
        .map_err(|_| error(StatusCode::BAD_REQUEST, "The request body is not valid"))
}

fn refused(f: Failure) -> Error {
    let code = if f == Failure::Mismatch {
        StatusCode::CONFLICT
    } else {
        StatusCode::BAD_GATEWAY
    };
    error(code, &format!("{f}. The request is still open."))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SecretBody {
    version: i64,
    request_id: String,
    value: Secret,
}

/// Protected input for the credential wait of a setup. The label comes from the open request.
/// The value goes to the guest and nowhere else. The wait completes after the guest receipt.
async fn setup_secret(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Extension(User(_owner)): Extension<User>,
    raw: Bytes,
) -> Result<StatusCode, Error> {
    let s = service(&st)?;
    let b: SecretBody = body(&raw)?;
    drop(raw);
    b.value
        .check()
        .map_err(|m| error(StatusCode::BAD_REQUEST, m))?;
    let _one = secrets::DELIVERY.lock().await;
    let mut row = st
        .store
        .setup_record(&id)
        .await
        .map_err(conflict)?
        .ok_or_else(|| error(StatusCode::NOT_FOUND, "Setup not found"))?;
    if row.version != b.version {
        return Err(error(StatusCode::CONFLICT, "Setup changed; reload it"));
    }
    let label = match row.setup.wait.as_ref() {
        Some(w) if w.request_id == b.request_id && row.setup.approval.is_some() => match &w.kind {
            WaitKind::Credential { label } => label.clone(),
            _ => {
                return Err(error(
                    StatusCode::CONFLICT,
                    "This request does not take a value",
                ));
            }
        },
        _ => {
            return Err(error(
                StatusCode::CONFLICT,
                "No open request has this identity",
            ));
        }
    };
    let now = secrets::unix_now();
    if row
        .setup
        .wait
        .as_ref()
        .is_some_and(|w| w.expires_at.is_some_and(|e| now >= e))
    {
        // The state machine opens a replacement request and refuses this one.
        let _ = row.setup.complete_wait(&b.request_id, now);
        st.store
            .save_setup_state(row.version, &row.setup)
            .await
            .map_err(conflict)?;
        return Err(error(
            StatusCode::CONFLICT,
            "The request expired; use the new one",
        ));
    }
    // The host profile that approval recorded must still be the configured one.
    let bound = st.store.accounting(&id).await.map_err(conflict)?;
    if bound.map(|a| a.policy_version) != Some(s.profile().map_err(conflict)?) {
        return Err(error(
            StatusCode::CONFLICT,
            "The host profile changed; review the setup",
        ));
    }
    let vm = s.bot_config(&row).map_err(conflict)?.vm.ok_or_else(|| {
        error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Protected input is not available",
        )
    })?;
    secrets::ensure_delivered(
        &st.store,
        &HarnessChannel::new(&vm),
        &id,
        &b.request_id,
        &label,
        None,
        &b.value,
    )
    .await
    .map_err(refused)?;
    row.setup
        .complete_wait(&b.request_id, now)
        .map_err(conflict)?;
    st.store
        .save_setup_state(row.version, &row.setup)
        .await
        .map_err(|_| {
            error(
                StatusCode::CONFLICT,
                "Setup changed after delivery; send the value again",
            )
        })?;
    s.notify();
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskSecretBody {
    request_id: String,
    value: Secret,
}

/// Protected input for a request that an active task opened with `request_secret`.
async fn task_secret(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Extension(User(_owner)): Extension<User>,
    raw: Bytes,
) -> Result<StatusCode, Error> {
    let s = service(&st)?;
    let b: TaskSecretBody = body(&raw)?;
    drop(raw);
    b.value
        .check()
        .map_err(|m| error(StatusCode::BAD_REQUEST, m))?;
    let _one = secrets::DELIVERY.lock().await;
    let runtime = s
        .runtime(&id)
        .ok_or_else(|| error(StatusCode::CONFLICT, "The bot is not ready"))?;
    let stale = || error(StatusCode::CONFLICT, "No open request has this identity");
    let send = sender(runtime.bot.clone());
    let wake_failed = |m: String| {
        error(
            StatusCode::CONFLICT,
            &format!("Delivered, but the task did not resume: {m}. Send the same request again."),
        )
    };
    let now = secrets::unix_now();
    // An expired request tells the task to ask again. The owner gets a replacement path.
    if let Some(w) = expire(&st.store, &id, &b.request_id, now, &send)
        .await
        .map_err(wake_failed)?
    {
        return Err(match w {
            Woke::NoTask => stale(),
            _ => error(
                StatusCode::CONFLICT,
                "The request expired. The task was asked to request it again.",
            ),
        });
    }
    // A delivered request whose resume failed: resume without a second delivery.
    if let Some(w) = resume_delivered(&st.store, &id, &b.request_id, &send)
        .await
        .map_err(wake_failed)?
    {
        return match w {
            Woke::Sent => Ok(StatusCode::NO_CONTENT),
            _ => Err(stale()),
        };
    }
    let request = st
        .store
        .pending_secret_request(&id, &b.request_id)
        .await
        .map_err(conflict)?
        .ok_or_else(stale)?;
    // The task that asked must still wait for this answer.
    let task = st.store.owner_task(&id).await.map_err(conflict)?;
    if !task.is_some_and(|t| t.id == request.task_id && t.state == "waiting") {
        return Err(stale());
    }
    let row = st
        .store
        .setup_record(&id)
        .await
        .map_err(conflict)?
        .ok_or_else(|| {
            error(
                StatusCode::SERVICE_UNAVAILABLE,
                "Protected input is not available",
            )
        })?;
    let vm = s.bot_config(&row).map_err(conflict)?.vm.ok_or_else(|| {
        error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Protected input is not available",
        )
    })?;
    secrets::ensure_delivered(
        &st.store,
        &HarnessChannel::new(&vm),
        &id,
        &b.request_id,
        &request.label,
        Some(&b.request_id),
        &b.value,
    )
    .await
    .map_err(refused)?;
    let marker = format!("[request {}]", b.request_id);
    wake(
        &st.store,
        &id,
        &request.task_id,
        &marker,
        delivered_text(&b.request_id, &request.label),
        &send,
    )
    .await
    .map_err(wake_failed)?;
    Ok(StatusCode::NO_CONTENT)
}

async fn bot_root(State(st): State<AppState>, Path(id): Path<String>, req: Request) -> Response {
    proxy(st, id, String::new(), req).await
}
async fn bot_path(
    State(st): State<AppState>,
    Path((id, rest)): Path<(String, String)>,
    req: Request,
) -> Response {
    proxy(st, id, rest, req).await
}
async fn proxy(st: AppState, id: String, rest: String, req: Request) -> Response {
    let bot = st
        .setups
        .as_ref()
        .and_then(|s| s.runtime(&id))
        .map(|r| r.bot.clone())
        .or_else(|| st.bot.filter(|b| b.id == id));
    let Some(bot) = bot else {
        return error(StatusCode::NOT_FOUND, "Bot runtime is not ready").into_response();
    };
    // The screen routes pass here, so the gate is set before any viewer can watch. A lookup
    // error counts as an open gate.
    let (store, bot_id) = (st.store.clone(), bot.id.clone());
    bot.screen
        .set_privacy_gate(Arc::new(move || store.privacy_gate_open_sync(&bot_id)));
    let query = req
        .uri()
        .query()
        .map(|q| format!("?{q}"))
        .unwrap_or_default();
    let suffix = if rest.is_empty() {
        String::new()
    } else {
        format!("/{rest}")
    };
    let (parts, body) = req.into_parts();
    let mut mapped = match Request::builder()
        .method(parts.method)
        .uri(format!("/api/bot{suffix}{query}"))
        .body(body)
    {
        Ok(r) => r,
        Err(_) => return StatusCode::BAD_REQUEST.into_response(),
    };
    *mapped.headers_mut() = parts.headers;
    if let Some(user) = parts.extensions.get::<User>() {
        mapped.extensions_mut().insert(user.clone());
    }
    super::api::routes()
        .with_state(bot)
        .oneshot(mapped)
        .await
        .unwrap_or_else(|e| match e {})
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::{
        agent::Agent,
        config::{Accounting, Config, Provisioning, Vm},
        model::ModelClient,
        store::Store,
        testutil::{FakeHost, temp_dir},
    };
    use axum::{body::Body, extract::connect_info::MockConnectInfo};

    pub(crate) fn app() -> (Router, Store, std::path::PathBuf, Arc<Service>) {
        let mut c = Config::parse(include_str!("../../deck.example.toml")).unwrap();
        let dir = temp_dir();
        c.data_dir = dir.join("data");
        c.provisioning = Some(Provisioning {
            base: dir.join("base"),
            runtime_root: dir.join("computers"),
            template: toml::from_str::<Vm>(&format!(
                "version=1\nbot_id='template'\nruntime_dir='unused'\nharness='{}'",
                fake_harness(&dir).display()
            ))
            .unwrap(),
            resources: super::super::setup::Resources {
                cpus: 2,
                memory_mib: 2048,
                disk_gib: 10,
            },
            limits: super::super::setup::Limits {
                max_cpus: 2,
                max_memory_mib: 2048,
                max_disk_gib: 10,
            },
            max_bots: 1,
            accounting: Accounting {
                currency: "TST".into(),
                exponent: 2,
                period: "test".into(),
                policy_version: "fixture".into(),
                ceiling: 100,
                allowance: 10,
                increase_increment: 10,
                price_version: "fixture".into(),
                input_per_million: 1,
                output_per_million: 1,
                extra_per_call: 0,
                max_input_tokens: 100,
                max_input_bytes: 100,
                max_output_tokens: 10,
                max_response_bytes: 10000,
                completion_token_field: false,
                request_usage: false,
            },
        });
        let addr = c.listen;
        let c = Arc::new(c);
        let store = Store::open_memory().unwrap();
        let model = ModelClient::new(&c.model.base_url, None, &c.model.api_key_env);
        let agent = Arc::new(Agent::new(
            c.clone(),
            store.clone(),
            model.clone(),
            Arc::new(FakeHost::default()),
        ));
        let service = Service::new(c.clone(), store.clone(), model);
        // Do not start a real worker. The separate driver test exercises fake provisioning.
        let router =
            crate::api::router_with_setups(c, store.clone(), agent, None, Some(service.clone()))
                .layer(MockConnectInfo(addr));
        (router, store, dir, service)
    }

    /// A stand-in for `vmproto.py` that stores what the guest command contract says it stores.
    /// Files next to the runtime dir switch failure modes: `fail` and `crash_after`.
    const FAKE_HARNESS: &str = r#"
import sys, json, pathlib
a = sys.argv[1:]
if '--help' in a:
    sys.exit(0)
d = pathlib.Path(a[a.index('--dir') + 1])
d.mkdir(parents=True, exist_ok=True)
with open(d / 'argv.log', 'a') as f:
    f.write(json.dumps(a) + '\n')
if 'deliver-secret' not in a and 'secret-receipt' not in a:
    sys.exit(2)
bot = a[a.index('--bot') + 1]
req = a[a.index('--request-id') + 1]
rf = d / ('receipt-' + req.replace(':', '_'))
if 'secret-receipt' in a:
    print(rf.read_text() if rf.exists() else json.dumps({'ok': False}))
    sys.exit(0)
body = json.load(sys.stdin)
if (d / 'fail').exists():
    sys.stderr.write('boom ' + body['value'])
    sys.exit(3)
with open(d / 'guest.env', 'a') as f:
    f.write(body['label'] + '=' + body['value'] + '\n')
rec = json.dumps({'ok': True, 'bot_id': bot, 'request_id': req, 'label': body['label']})
rf.write_text(rec)
if (d / 'crash_after').exists():
    sys.exit(9)
print(rec)
"#;

    fn fake_harness(dir: &std::path::Path) -> std::path::PathBuf {
        std::fs::create_dir_all(dir).unwrap();
        let p = dir.join("fake_vmproto.py");
        std::fs::write(&p, FAKE_HARNESS).unwrap();
        p
    }

    async fn request(
        app: &Router,
        method: &str,
        path: &str,
        body: Value,
        owner: bool,
    ) -> (StatusCode, Value) {
        let mut req = Request::builder()
            .method(method)
            .uri(path)
            .header("Content-Type", "application/json");
        if owner {
            req = req.header("Remote-User", "test-owner");
        }
        let res = app
            .clone()
            .oneshot(req.body(Body::from(body.to_string())).unwrap())
            .await
            .unwrap();
        let status = res.status();
        let bytes = axum::body::to_bytes(res.into_body(), 1 << 20)
            .await
            .unwrap();
        (
            status,
            serde_json::from_slice(&bytes).unwrap_or(Value::Null),
        )
    }

    #[tokio::test]
    async fn authenticated_revision_approval_creates_authority_but_never_an_early_computer() {
        let (app, store, dir, _) = app();
        assert_eq!(
            request(
                &app,
                "POST",
                "/api/setups",
                json!({"job":"Read fixture"}),
                false
            )
            .await
            .0,
            StatusCode::UNAUTHORIZED
        );
        assert!(store.setup_records().await.unwrap().is_empty());
        let (status, row) = request(
            &app,
            "POST",
            "/api/setups",
            json!({"job":"Read fixture"}),
            true,
        )
        .await;
        assert_eq!(status, StatusCode::CREATED);
        let id = row["setup"]["id"].as_str().unwrap();
        assert!(store.active_definition(id).await.unwrap().is_none());
        assert!(!dir.join("computers").exists());
        let path = format!("/api/setups/{id}/approve");
        assert_eq!(
            request(&app, "POST", &path, json!({"version":0,"revision":1}), true)
                .await
                .0,
            StatusCode::CONFLICT
        );
        assert!(store.active_definition(id).await.unwrap().is_none());
        let approval = json!({"version":1,"revision":1});
        assert_eq!(
            request(&app, "POST", &path, approval.clone(), true).await.0,
            StatusCode::NO_CONTENT
        );
        assert_eq!(
            request(&app, "POST", &path, approval, true).await.0,
            StatusCode::NO_CONTENT
        );
        assert_eq!(
            store.active_definition(id).await.unwrap().unwrap().actor,
            "test-owner"
        );
        assert_eq!(store.accounting(id).await.unwrap().unwrap().ceiling, 100);
        assert!(!dir.join("computers").exists());
        let row = store.setup_record(id).await.unwrap().unwrap();
        let path = format!("/api/setups/{id}/control");
        assert_eq!(
            request(
                &app,
                "POST",
                &path,
                json!({"version":row.version,"command":{"action":"pause","value":true}}),
                true
            )
            .await
            .0,
            StatusCode::NO_CONTENT
        );
        assert!(
            store
                .holds(id)
                .await
                .unwrap()
                .iter()
                .any(|h| h.kind == super::super::state::HoldKind::AutomationPause)
        );
        let path = format!("/api/chats/{}/messages", row.chat_id);
        assert_eq!(
            request(
                &app,
                "POST",
                &path,
                json!({"text":"Start before setup is ready"}),
                true
            )
            .await
            .0,
            StatusCode::BAD_REQUEST
        );
    }

    use super::super::setup::{Provisioner, StepOutcome, StepRequest};
    use futures_util::future::BoxFuture;

    struct AllDone;
    impl Provisioner for AllDone {
        fn run<'a>(&'a self, _: &'a StepRequest) -> BoxFuture<'a, StepOutcome> {
            Box::pin(async { StepOutcome::Done })
        }
    }

    const LABEL: &str = "Fixture password";
    /// Generated per run. It is not a real credential.
    fn synthetic() -> String {
        format!("synthetic-{}", uuid::Uuid::new_v4().simple())
    }

    /// An approved setup that waits for the credential. Returns its id, version and request.
    async fn waiting(app: &Router, store: &Store) -> (String, i64, String) {
        let (_, row) = request(
            app,
            "POST",
            "/api/setups",
            json!({"job":"Use fixture"}),
            true,
        )
        .await;
        let id = row["setup"]["id"].as_str().unwrap().to_owned();
        let mut draft = row["setup"]["draft"].clone();
        draft["credentials"] = json!([LABEL]);
        let edit = json!({"version":1,"draft":draft});
        assert_eq!(
            request(app, "POST", &format!("/api/setups/{id}"), edit, true)
                .await
                .0,
            StatusCode::NO_CONTENT
        );
        let edited = store.setup_record(&id).await.unwrap().unwrap();
        let approve = json!({"version":edited.version,"revision":edited.setup.draft.revision});
        assert_eq!(
            request(
                app,
                "POST",
                &format!("/api/setups/{id}/approve"),
                approve,
                true
            )
            .await
            .0,
            StatusCode::NO_CONTENT
        );
        let mut r = store.setup_record(&id).await.unwrap().unwrap();
        for s in &mut r.setup.steps {
            if !matches!(s.step, super::super::setup::Step::Credential(_)) {
                s.status = super::super::setup::Status::Done;
            }
        }
        r.setup
            .advance(&AllDone, &mut |_| Ok(()), 1_000)
            .await
            .unwrap();
        let version = store.save_setup_state(r.version, &r.setup).await.unwrap();
        (id, version, r.setup.wait.unwrap().request_id)
    }

    fn secret(id: &str, version: i64, request: &str, value: &str) -> (String, Value) {
        (
            format!("/api/setups/{id}/secret"),
            json!({"version":version,"request_id":request,"value":value}),
        )
    }

    fn guest(dir: &std::path::Path, id: &str) -> std::path::PathBuf {
        dir.join("computers").join(id)
    }
    fn guest_lines(dir: &std::path::Path, id: &str) -> Vec<String> {
        std::fs::read_to_string(guest(dir, id).join("guest.env"))
            .unwrap_or_default()
            .lines()
            .map(str::to_owned)
            .collect()
    }

    /// Every column of every table, as text. The value must appear in none of them.
    fn db_has(store: &Store, needle: &str) -> bool {
        store
            .run_sync(|c| {
                let tables: Vec<String> = c
                    .prepare("SELECT name FROM sqlite_master WHERE type='table'")?
                    .query_map([], |r| r.get(0))?
                    .collect::<Result<_, _>>()?;
                for t in tables {
                    let mut q = c.prepare(&format!("SELECT * FROM \"{t}\""))?;
                    let n = q.column_count();
                    let mut rows = q.query([])?;
                    while let Some(r) = rows.next()? {
                        for i in 0..n {
                            let hit = match r.get_ref(i)? {
                                rusqlite::types::ValueRef::Text(b)
                                | rusqlite::types::ValueRef::Blob(b) => {
                                    String::from_utf8_lossy(b).contains(needle)
                                }
                                _ => false,
                            };
                            if hit {
                                return Ok(true);
                            }
                        }
                    }
                }
                Ok(false)
            })
            .unwrap()
    }

    #[tokio::test]
    async fn protected_secret_reaches_the_guest_and_completes_the_wait_once() {
        let (app, store, dir, _) = app();
        let (id, version, req) = waiting(&app, &store).await;
        let (path, body) = secret(&id, version, &req, &synthetic());
        let value = body["value"].as_str().unwrap().to_owned();
        let (status, res) = request(&app, "POST", &path, body.clone(), false).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
        assert!(!guest(&dir, &id).join("argv.log").exists());
        let (status, res2) = request(&app, "POST", &path, body.clone(), true).await;
        assert_eq!(status, StatusCode::NO_CONTENT, "{res2}");
        assert!(!res.to_string().contains(&value) && !res2.to_string().contains(&value));
        let row = store.setup_record(&id).await.unwrap().unwrap();
        assert!(row.setup.wait.is_none());
        let lines = guest_lines(&dir, &id);
        let name = format!("DECK_{}", crate::bot::secrets::label_hash(LABEL));
        assert_eq!(lines, vec![format!("{name}={value}")]);
        assert!(
            !std::fs::read_to_string(guest(&dir, &id).join("argv.log"))
                .unwrap()
                .contains(&value)
        );
        assert!(!db_has(&store, &value));
        assert!(store.privacy_gate_open(&id).await.unwrap());
        // A repeat after completion changes nothing and delivers nothing.
        let (status, _) = request(&app, "POST", &path, body, true).await;
        assert_eq!(status, StatusCode::CONFLICT);
        assert_eq!(guest_lines(&dir, &id).len(), 1);
    }

    #[tokio::test]
    async fn stale_foreign_labelled_and_expired_requests_fail_without_delivery() {
        let (app, store, dir, _) = app();
        let (id, version, req) = waiting(&app, &store).await;
        let v = synthetic();
        for (path, body) in [
            secret(&id, version + 1, &req, &v),
            secret(&id, version, "other:9", &v),
            secret(&id, version, &format!("{req}x"), &v),
        ] {
            assert_eq!(
                request(&app, "POST", &path, body, true).await.0,
                StatusCode::CONFLICT
            );
        }
        let (path, mut body) = secret(&id, version, &req, &v);
        body["label"] = json!("OTHER");
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::BAD_REQUEST
        );
        let (path, _) = secret("nobody", version, &req, &v);
        assert_eq!(
            request(
                &app,
                "POST",
                &path,
                json!({"version":version,"request_id":req,"value":v}),
                true
            )
            .await
            .0,
            StatusCode::NOT_FOUND
        );
        for bad in ["", "a\nb", "a\rb", "a\u{0}b"] {
            let (path, body) = secret(&id, version, &req, bad);
            assert_eq!(
                request(&app, "POST", &path, body, true).await.0,
                StatusCode::BAD_REQUEST
            );
        }
        let (path, body) = secret(&id, version, &req, &"x".repeat(5000));
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::BAD_REQUEST
        );
        assert!(!guest(&dir, &id).join("guest.env").exists());
        // An expired request opens a new one and delivers nothing.
        let mut row = store.setup_record(&id).await.unwrap().unwrap();
        row.setup.wait.as_mut().unwrap().expires_at = Some(1);
        let version = store
            .save_setup_state(row.version, &row.setup)
            .await
            .unwrap();
        let (path, body) = secret(&id, version, &req, &v);
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::CONFLICT
        );
        assert!(!guest(&dir, &id).join("guest.env").exists());
        let now = store.setup_record(&id).await.unwrap().unwrap();
        assert_ne!(now.setup.wait.unwrap().request_id, req);
    }

    #[tokio::test]
    async fn failed_delivery_keeps_the_wait_and_never_echoes_the_value() {
        let (app, store, dir, _) = app();
        let (id, version, req) = waiting(&app, &store).await;
        std::fs::create_dir_all(guest(&dir, &id)).unwrap();
        std::fs::write(guest(&dir, &id).join("fail"), "").unwrap();
        let v = synthetic();
        let (path, body) = secret(&id, version, &req, &v);
        let (status, res) = request(&app, "POST", &path, body.clone(), true).await;
        assert!(status.is_server_error(), "{status}");
        assert!(!res.to_string().contains(&v));
        let row = store.setup_record(&id).await.unwrap().unwrap();
        assert_eq!(row.version, version);
        assert_eq!(row.setup.wait.unwrap().request_id, req);
        assert!(!store.privacy_gate_open(&id).await.unwrap());
        std::fs::remove_file(guest(&dir, &id).join("fail")).unwrap();
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::NO_CONTENT
        );
    }

    #[tokio::test]
    async fn a_crash_after_guest_receipt_reconciles_without_a_second_delivery() {
        let (app, store, dir, _) = app();
        let (id, version, req) = waiting(&app, &store).await;
        std::fs::create_dir_all(guest(&dir, &id)).unwrap();
        std::fs::write(guest(&dir, &id).join("crash_after"), "").unwrap();
        let (path, body) = secret(&id, version, &req, &synthetic());
        assert!(
            request(&app, "POST", &path, body.clone(), true)
                .await
                .0
                .is_server_error()
        );
        assert!(
            store
                .setup_record(&id)
                .await
                .unwrap()
                .unwrap()
                .setup
                .wait
                .is_some()
        );
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::NO_CONTENT
        );
        assert_eq!(guest_lines(&dir, &id).len(), 1);
        assert!(
            store
                .setup_record(&id)
                .await
                .unwrap()
                .unwrap()
                .setup
                .wait
                .is_none()
        );
    }

    #[tokio::test]
    async fn a_receipt_for_another_request_cannot_clear_this_wait() {
        let (app, store, dir, _) = app();
        let (id, version, req) = waiting(&app, &store).await;
        let name = format!("DECK_{}", crate::bot::secrets::label_hash(LABEL));
        store
            .record_secret(&id, "stale:1", LABEL, &name, None)
            .await
            .unwrap();
        let (path, body) = secret(&id, version, &req, &synthetic());
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::NO_CONTENT
        );
        assert_eq!(guest_lines(&dir, &id).len(), 1);
    }

    #[tokio::test]
    async fn options_advertise_protected_input_only_with_a_capable_guest_command() {
        let (app, _, _, _) = app();
        let (_, o) = request(&app, "GET", "/api/setup-options", json!(null), true).await;
        assert_eq!(o["protected_input"], json!(true));
    }

    #[tokio::test]
    async fn a_task_secret_request_needs_auth_a_ready_bot_and_an_open_request() {
        let (app, store, dir, _) = app();
        let (id, _, _) = waiting(&app, &store).await;
        let path = format!("/api/bots/{id}/secret");
        let body = json!({"request_id":"secret-x","value":synthetic()});
        assert_eq!(
            request(&app, "POST", &path, body.clone(), false).await.0,
            StatusCode::UNAUTHORIZED
        );
        // No runtime is registered: nothing is delivered and nothing is cleared.
        assert_eq!(
            request(&app, "POST", &path, body, true).await.0,
            StatusCode::CONFLICT
        );
        assert!(!guest(&dir, &id).join("guest.env").exists());
    }
}
