//! Authenticated setup and managed-bot routes. Machine event intake exposes none of these.
use super::{
    provisioning::Service,
    setup::{Authority, Budget, Draft, Setup, WaitKind},
};
use crate::{api::AppState, auth::User};
use axum::{
    Extension, Json, Router,
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
        .route("/api/bots/{id}", get(bot_root))
        .route("/api/bots/{id}/{*rest}", axum::routing::any(bot_path))
}

async fn options(State(st): State<AppState>) -> Json<Value> {
    let Some(s) = st.setups else {
        return Json(json!({"enabled":false}));
    };
    let p = s.config.provisioning.as_ref().unwrap();
    Json(
        json!({"enabled":true, "model":s.config.model.agent_model, "resources":p.resources,
        "budget":Budget { currency:p.accounting.currency.clone(), exponent:p.accounting.exponent, period:p.accounting.period.clone(), allowance:p.accounting.allowance, ceiling:p.accounting.ceiling },
        "protected_input":false }),
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
            template: toml::from_str::<Vm>(
                "version=1\nbot_id='template'\nruntime_dir='unused'\nharness='unused'",
            )
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
}
