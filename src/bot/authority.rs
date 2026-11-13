//! The owner's answer to a request to raise the absolute spending ceiling.
//!
//! Only the authenticated owner routes may build a `CeilingResponse`. The bot tools and the
//! machine event intake never reach this module. The pending request and the ceiling live in the
//! ledger (stage 1), which does not exist yet: `check` takes the pending request as an argument.

#![allow(dead_code)] // `check` and `PendingCeilingRequest` wait for the ledger (stage 1).

use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Approve,
    Deny,
}

/// The body of `POST /api/bot/ceiling-requests/respond`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CeilingResponse {
    pub request_id: String,
    /// The request version the owner saw. A newer request makes this answer stale.
    pub version: u64,
    pub decision: Decision,
    /// The new ceiling, in minor units of `currency` at `precision` decimals.
    pub amount_minor: Option<u64>,
    pub currency: Option<String>,
    pub precision: Option<u8>,
    /// Repeats of one key apply once.
    pub idempotency_key: String,
}

/// The ledger's view of the open request.
#[derive(Debug, Clone, PartialEq)]
pub struct PendingCeilingRequest {
    pub id: String,
    pub version: u64,
    pub currency: String,
    pub precision: u8,
    pub current_ceiling_minor: u64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Rejection {
    Invalid(&'static str),
    /// The request was replaced or answered.
    Stale,
}

const MAX_PRECISION: u8 = 6;

fn token_ok(s: &str) -> bool {
    !s.is_empty() && s.len() <= 128 && s.bytes().all(|b| b.is_ascii_graphic())
}

/// Check the shape of an answer. This needs no ledger.
pub fn validate(r: &CeilingResponse) -> Result<(), Rejection> {
    if !token_ok(&r.request_id) {
        return Err(Rejection::Invalid("request_id is not valid"));
    }
    if !token_ok(&r.idempotency_key) {
        return Err(Rejection::Invalid("idempotency_key is not valid"));
    }
    match r.decision {
        Decision::Deny => {
            if r.amount_minor.is_some() || r.currency.is_some() || r.precision.is_some() {
                return Err(Rejection::Invalid("a denial carries no amount"));
            }
        }
        Decision::Approve => {
            if r.amount_minor.is_none_or(|a| a == 0) {
                return Err(Rejection::Invalid("amount_minor must be above zero"));
            }
            let c = r.currency.as_deref().unwrap_or("");
            if c.len() != 3 || !c.bytes().all(|b| b.is_ascii_uppercase()) {
                return Err(Rejection::Invalid("currency must be three capital letters"));
            }
            if r.precision.is_none_or(|p| p > MAX_PRECISION) {
                return Err(Rejection::Invalid("precision is missing or too large"));
            }
        }
    }
    Ok(())
}

/// Check an answer against the open request. An approval must name the request's currency and
/// precision and must raise the ceiling.
pub fn check(r: &CeilingResponse, pending: &PendingCeilingRequest) -> Result<(), Rejection> {
    validate(r)?;
    if r.request_id != pending.id || r.version != pending.version {
        return Err(Rejection::Stale);
    }
    if r.decision == Decision::Approve {
        if r.currency.as_deref() != Some(pending.currency.as_str())
            || r.precision != Some(pending.precision)
        {
            return Err(Rejection::Invalid(
                "currency or precision differs from the request",
            ));
        }
        if r.amount_minor <= Some(pending.current_ceiling_minor) {
            return Err(Rejection::Invalid("the amount does not raise the ceiling"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approve() -> CeilingResponse {
        CeilingResponse {
            request_id: "r1".into(),
            version: 3,
            decision: Decision::Approve,
            amount_minor: Some(5_000),
            currency: Some("USD".into()),
            precision: Some(2),
            idempotency_key: "k1".into(),
        }
    }

    fn pending() -> PendingCeilingRequest {
        PendingCeilingRequest {
            id: "r1".into(),
            version: 3,
            currency: "USD".into(),
            precision: 2,
            current_ceiling_minor: 2_000,
        }
    }

    #[test]
    fn a_good_approval_passes() {
        assert_eq!(check(&approve(), &pending()), Ok(()));
    }

    #[test]
    fn a_denial_needs_no_amount_and_rejects_one() {
        let d = CeilingResponse {
            decision: Decision::Deny,
            amount_minor: None,
            currency: None,
            precision: None,
            ..approve()
        };
        assert_eq!(check(&d, &pending()), Ok(()));
        let with_amount = CeilingResponse {
            amount_minor: Some(9),
            ..d
        };
        assert!(matches!(validate(&with_amount), Err(Rejection::Invalid(_))));
    }

    #[test]
    fn malformed_approvals_are_invalid() {
        let bad = [
            CeilingResponse {
                amount_minor: None,
                ..approve()
            },
            CeilingResponse {
                amount_minor: Some(0),
                ..approve()
            },
            CeilingResponse {
                currency: None,
                ..approve()
            },
            CeilingResponse {
                currency: Some("usd".into()),
                ..approve()
            },
            CeilingResponse {
                currency: Some("US".into()),
                ..approve()
            },
            CeilingResponse {
                precision: None,
                ..approve()
            },
            CeilingResponse {
                precision: Some(7),
                ..approve()
            },
            CeilingResponse {
                request_id: "".into(),
                ..approve()
            },
            CeilingResponse {
                idempotency_key: "a b".into(),
                ..approve()
            },
            CeilingResponse {
                idempotency_key: "x".repeat(129),
                ..approve()
            },
        ];
        for r in bad {
            assert!(matches!(validate(&r), Err(Rejection::Invalid(_))), "{r:?}");
        }
    }

    #[test]
    fn a_stale_answer_is_rejected() {
        let old_version = CeilingResponse {
            version: 2,
            ..approve()
        };
        assert_eq!(check(&old_version, &pending()), Err(Rejection::Stale));
        let other = CeilingResponse {
            request_id: "r0".into(),
            ..approve()
        };
        assert_eq!(check(&other, &pending()), Err(Rejection::Stale));
    }

    #[test]
    fn an_approval_must_match_the_request_and_raise_the_ceiling() {
        let eur = CeilingResponse {
            currency: Some("EUR".into()),
            ..approve()
        };
        assert!(matches!(
            check(&eur, &pending()),
            Err(Rejection::Invalid(_))
        ));
        let prec = CeilingResponse {
            precision: Some(3),
            ..approve()
        };
        assert!(matches!(
            check(&prec, &pending()),
            Err(Rejection::Invalid(_))
        ));
        let same = CeilingResponse {
            amount_minor: Some(2_000),
            ..approve()
        };
        assert!(matches!(
            check(&same, &pending()),
            Err(Rejection::Invalid(_))
        ));
    }

    #[test]
    fn unknown_fields_do_not_parse() {
        let j = r#"{"request_id":"r1","version":3,"decision":"deny","idempotency_key":"k","bot_approved":true}"#;
        assert!(serde_json::from_str::<CeilingResponse>(j).is_err());
        let j = r#"{"request_id":"r1","version":3,"decision":"maybe","idempotency_key":"k"}"#;
        assert!(serde_json::from_str::<CeilingResponse>(j).is_err());
    }
}
