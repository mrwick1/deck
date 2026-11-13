//! The owner's answer to a challenge request. It carries metadata only.
use super::{challenge::Decision, setup::SetupError};
use crate::api::AppState;
use axum::{
    Json, Router,
    extract::{Path, State},
    http::StatusCode,
    routing::post,
};
use serde::Deserialize;
use serde_json::{Value, json};

type Error = (StatusCode, Json<Value>);
fn error(code: StatusCode, message: &str) -> Error {
    (code, Json(json!({"error":message})))
}

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/setups/{id}/challenge/respond", post(respond))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Body {
    version: i64,
    request_id: String,
    decision: Decision,
}

/// The owner answers from an authenticated session. A pause, a budget hold or a ceiling hold
/// does not block the answer: the owner can always settle a request.
async fn respond(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<Body>,
) -> Result<StatusCode, Error> {
    let s = st.setups.clone().ok_or_else(|| {
        error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Bot provisioning is not configured",
        )
    })?;
    let conflict = |e: &dyn std::fmt::Display| error(StatusCode::CONFLICT, &e.to_string());
    let mut row = st
        .store
        .setup_record(&id)
        .await
        .map_err(|e| conflict(&e))?
        .ok_or_else(|| error(StatusCode::NOT_FOUND, "Setup not found"))?;
    if row.version != body.version {
        return Err(error(StatusCode::CONFLICT, "Setup changed; reload it"));
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| conflict(&e))?
        .as_secs();
    let before = row.setup.clone();
    let answer = row.setup.respond(&body.request_id, body.decision, now);
    // An expired answer opens a replacement request, so it saves even though it fails.
    if row.setup != before {
        st.store
            .save_setup_state(row.version, &row.setup)
            .await
            .map_err(|e| conflict(&e))?;
        s.notify();
    }
    match answer {
        Ok(()) => Ok(StatusCode::NO_CONTENT),
        Err(e @ SetupError::Invalid(_)) => Err(error(StatusCode::BAD_REQUEST, &e.to_string())),
        Err(e) => Err(conflict(&e)),
    }
}

#[cfg(test)]
mod tests {
    use crate::bot::{
        challenge::{
            Challenge, Requested, State, Verification, challenge_state, request_login_help,
            verify_login_help,
        },
        setup::{Help, Limits, Setup, Status},
        setup_api::tests::app,
        state::HoldKind,
    };
    use crate::store::Store;
    use axum::{
        Router,
        body::Body,
        http::{Request, StatusCode},
    };
    use serde_json::{Value, json};
    use tower::ServiceExt;

    async fn post(app: &Router, path: &str, body: Value, owner: bool) -> (StatusCode, Value) {
        let mut req = Request::builder()
            .method("POST")
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

    /// A ready bot with the fixture's first task started, as the driver leaves it.
    async fn ready(store: &Store) -> (String, i64) {
        let id = "chal-bot".to_string();
        let mut s = Setup::new(
            &id,
            crate::bot::setup::Draft {
                revision: 1,
                name: "Fixture".into(),
                instructions: "Use the local fixture".into(),
                model: "fixture".into(),
                authority: crate::bot::setup::Authority::Full,
                resources: crate::bot::setup::Resources {
                    cpus: 2,
                    memory_mib: 2048,
                    disk_gib: 10,
                },
                budget: Some(crate::bot::setup::Budget {
                    currency: "TST".into(),
                    exponent: 2,
                    period: "test".into(),
                    allowance: 10,
                    ceiling: 100,
                }),
                credentials: vec![],
                task: Some("Sign in to the fixture".into()),
            },
        )
        .unwrap();
        store.create_setup(&s).await.unwrap();
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let limits = Limits {
            max_cpus: 2,
            max_memory_mib: 2048,
            max_disk_gib: 10,
        };
        store
            .approve_setup(&id, v, 1, "test-owner", &limits, "fixture")
            .await
            .unwrap();
        let row = store.setup_record(&id).await.unwrap().unwrap();
        s = row.setup;
        s.steps.iter_mut().for_each(|t| t.status = Status::Done);
        store.save_setup_state(row.version, &s).await.unwrap();
        store.finish_setup(&id).await.unwrap();
        let row = store.setup_record(&id).await.unwrap().unwrap();
        (id, row.chat_id)
    }

    fn help() -> Help {
        Help {
            instructions: Some("Approve on your phone".into()),
            match_number: Some("17".into()),
        }
    }

    async fn ask(store: &Store, chat: i64, ttl: Option<u64>, now: u64) -> String {
        match request_login_help(store, chat, "task-1", Challenge::Phone, help(), ttl, now)
            .await
            .unwrap()
            .unwrap()
        {
            Requested::Open(id) => id,
            other => panic!("{other:?}"),
        }
    }

    #[tokio::test]
    async fn the_owner_answers_through_the_route_and_a_task_verifies_before_it_resumes() {
        let (app, store, _, _) = app();
        let (id, chat) = ready(&store).await;
        let req = ask(&store, chat, None, 0).await;
        let path = format!("/api/setups/{id}/challenge/respond");
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let body = json!({"version":v,"request_id":req,"decision":"completed"});
        assert_eq!(
            post(&app, &path, body.clone(), false).await.0,
            StatusCode::UNAUTHORIZED
        );
        assert_eq!(
            post(&app, &path, body.clone(), true).await.0,
            StatusCode::NO_CONTENT
        );
        assert_eq!(
            challenge_state(&store, chat, &req, 1).await.unwrap(),
            State::Answered
        );
        // A repeat with the old version is stale. A repeat with the new one is a duplicate.
        assert_eq!(post(&app, &path, body, true).await.0, StatusCode::CONFLICT);
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let dup = json!({"version":v,"request_id":req,"decision":"completed"});
        assert_eq!(post(&app, &path, dup, true).await.0, StatusCode::NO_CONTENT);
        // The service rejected the action: the old identity cannot answer the new request.
        verify_login_help(
            &store,
            chat,
            &req,
            Verification::NotVerified("no".into()),
            2,
        )
        .await
        .unwrap()
        .unwrap();
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let stale = json!({"version":v,"request_id":req,"decision":"completed"});
        assert_eq!(post(&app, &path, stale, true).await.0, StatusCode::CONFLICT);
        assert!(
            store
                .setup_record(&id)
                .await
                .unwrap()
                .unwrap()
                .setup
                .wait
                .is_some_and(|w| !w.answered)
        );
    }

    #[tokio::test]
    async fn the_route_rejects_stale_versions_unknown_setups_and_extra_fields() {
        let (app, store, _, _) = app();
        let (id, chat) = ready(&store).await;
        let req = ask(&store, chat, None, 0).await;
        let path = format!("/api/setups/{id}/challenge/respond");
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let go = |version: i64, extra: Option<(&str, Value)>| {
            let mut b = json!({"version":version,"request_id":req,"decision":"completed"});
            if let Some((k, x)) = extra {
                b[k] = x;
            }
            b
        };
        assert_eq!(
            post(&app, &path, go(v - 1, None), true).await.0,
            StatusCode::CONFLICT
        );
        assert_eq!(
            post(
                &app,
                "/api/setups/none/challenge/respond",
                go(v, None),
                true
            )
            .await
            .0,
            StatusCode::NOT_FOUND
        );
        // A code or a secret is not accepted here, even in an extra field.
        let (status, _) = post(&app, &path, go(v, Some(("value", json!("123456")))), true).await;
        assert!(status.is_client_error());
        let (status, _) = post(
            &app,
            &path,
            json!({"version":v,"request_id":req,"decision":"maybe"}),
            true,
        )
        .await;
        assert!(status.is_client_error());
        assert!(
            store
                .setup_record(&id)
                .await
                .unwrap()
                .unwrap()
                .setup
                .wait
                .is_some_and(|w| !w.answered)
        );
    }

    #[tokio::test]
    async fn an_expired_answer_saves_a_replacement_and_cancel_closes_the_request() {
        let (app, store, _, _) = app();
        let (id, chat) = ready(&store).await;
        let req = ask(&store, chat, Some(1), 0).await; // expired in 1970
        let path = format!("/api/setups/{id}/challenge/respond");
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let (status, body) = post(
            &app,
            &path,
            json!({"version":v,"request_id":req,"decision":"completed"}),
            true,
        )
        .await;
        assert_eq!(status, StatusCode::CONFLICT);
        assert!(body["error"].as_str().unwrap().contains(&format!("{id}:")));
        let row = store.setup_record(&id).await.unwrap().unwrap();
        let fresh = row.setup.wait.clone().unwrap();
        assert_ne!(fresh.request_id, req);
        assert!(fresh.expires_at.unwrap() > 1_000_000_000);
        let (status, _) = post(
            &app,
            &path,
            json!({"version":row.version,"request_id":fresh.request_id,"decision":"cancel"}),
            true,
        )
        .await;
        assert_eq!(status, StatusCode::NO_CONTENT);
        let row = store.setup_record(&id).await.unwrap().unwrap();
        assert!(row.setup.wait.is_none());
        assert_eq!(
            challenge_state(&store, chat, &fresh.request_id, 5)
                .await
                .unwrap(),
            State::Closed(crate::bot::challenge::Outcome::Cancelled)
        );
    }

    #[tokio::test]
    async fn the_owner_can_answer_under_every_hold_and_the_holds_stay() {
        let (app, store, _, _) = app();
        let (id, chat) = ready(&store).await;
        let req = ask(&store, chat, None, 0).await;
        for kind in [
            HoldKind::AutomationPause,
            HoldKind::Budget,
            HoldKind::Ceiling,
        ] {
            store.set_hold(&id, kind, "test hold").await.unwrap();
        }
        let path = format!("/api/setups/{id}/challenge/respond");
        let v = store.setup_record(&id).await.unwrap().unwrap().version;
        let (status, _) = post(
            &app,
            &path,
            json!({"version":v,"request_id":req,"decision":"completed"}),
            true,
        )
        .await;
        assert_eq!(status, StatusCode::NO_CONTENT);
        assert_eq!(store.holds(&id).await.unwrap().len(), 3);
    }

    #[tokio::test]
    async fn a_pending_request_and_its_owner_survive_a_restart() {
        let dir = crate::testutil::temp_dir();
        let store = Store::open(&dir).unwrap();
        let (id, chat) = ready(&store).await;
        let req = ask(&store, chat, Some(4_000_000_000), 0).await;
        store
            .set_hold(&id, HoldKind::Ceiling, "independent")
            .await
            .unwrap();
        drop(store);
        let store = Store::open(&dir).unwrap();
        let w = store
            .setup_record(&id)
            .await
            .unwrap()
            .unwrap()
            .setup
            .wait
            .unwrap();
        assert_eq!(w.request_id, req);
        assert_eq!(w.step, crate::bot::setup::Step::Login("task-1".into()));
        assert_eq!(w.match_number.as_deref(), Some("17"));
        assert_eq!(ask(&store, chat, Some(4_000_000_000), 9).await, req);
        assert_eq!(store.holds(&id).await.unwrap().len(), 1);
    }

    #[tokio::test]
    async fn an_unsupported_challenge_is_blocked_and_leaves_no_wait() {
        let store = Store::open_memory().unwrap();
        let (id, chat) = ready(&store).await;
        let got = request_login_help(
            &store,
            chat,
            "task-1",
            Challenge::Unsupported,
            help(),
            None,
            0,
        )
        .await
        .unwrap()
        .unwrap();
        assert!(matches!(got, Requested::Blocked(_)));
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
}
