//! Durable admission and settlement for the configured bot's model calls.
use std::sync::Arc;

use anyhow::{Context, ensure};
use futures_util::future::BoxFuture;

use super::state::{AccountingConfig, HoldKind, Outcome as Write};
use crate::accounting::{Pricing, Quote, Unit, Usage, quote};
use crate::config::{Accounting as Policy, Config};
use crate::model::{self, Accountant, Admission, CallRequest, Outcome, Ticket};
use crate::store::Store;

pub struct Meter {
    store: Store,
    bot: String,
    model: String,
    policy: Policy,
}

impl Meter {
    fn cost(&self, input: u64, output: u64) -> anyhow::Result<i64> {
        let p = &self.policy;
        let unit = Unit::new(&p.currency, p.exponent)?;
        let pricing = Pricing {
            version: p.price_version.clone(),
            input_per_million: unit.amount(p.input_per_million.try_into()?),
            output_per_million: unit.amount(p.output_per_million.try_into()?),
        };
        let Quote::Priced { amount, .. } = quote(
            Some(&pricing),
            Usage {
                input_tokens: input,
                output_tokens: output,
            },
            &unit.amount(p.extra_per_call.try_into()?),
        )?
        else {
            anyhow::bail!("missing pricing")
        };
        Ok(amount.units().try_into()?)
    }
}

impl Accountant for Meter {
    fn admit(&self, req: &CallRequest) -> BoxFuture<'_, anyhow::Result<Admission>> {
        let req = req.clone();
        Box::pin(async move {
            if req.model != self.model
                || req.input_bytes > self.policy.max_input_bytes
                || req.max_output_tokens > self.policy.max_output_tokens
            {
                return Ok(Admission::Hold(
                    "model request exceeds its configured accounting bounds".into(),
                ));
            }
            let amount = self.cost(
                self.policy.max_input_tokens,
                u64::from(req.max_output_tokens),
            )?;
            let out = self
                .store
                .admit_charge(
                    &self.bot,
                    &req.request_id,
                    amount,
                    self.policy.increase_increment,
                    &self.policy.price_version,
                )
                .await?;
            Ok(match out {
                Write::Applied(_) => Admission::Admitted(Ticket {
                    request_id: req.request_id,
                }),
                // A reused request identity must never dispatch the external request twice.
                Write::Duplicate(_) => {
                    Admission::Hold("model request identity was already admitted".into())
                }
                Write::Insufficient(_) => {
                    Admission::Hold("absolute spending ceiling needs an owner increase".into())
                }
                other => Admission::Hold(format!(
                    "model accounting did not admit the request: {other:?}"
                )),
            })
        })
    }

    fn settle(&self, ticket: &Ticket, outcome: Outcome) -> BoxFuture<'_, anyhow::Result<()>> {
        let ticket = ticket.clone();
        Box::pin(async move {
            match outcome {
                Outcome::Reported(usage) => {
                    let (input, output) = usage.complete().context("incomplete reported usage")?;
                    let actual = self.cost(input, output)?;
                    let out = self
                        .store
                        .settle_reservation(
                            &ticket.request_id,
                            &format!("usage:{}", ticket.request_id),
                            actual,
                        )
                        .await?;
                    ensure!(
                        matches!(out, Write::Applied(_) | Write::Duplicate(_)),
                        "usage settlement failed: {out:?}"
                    );
                    let a = self
                        .store
                        .accounting(&self.bot)
                        .await?
                        .context("missing accounting")?;
                    let e = self
                        .store
                        .exposure(&self.bot)
                        .await?
                        .context("missing exposure")?;
                    if a.allowance
                        .checked_sub(e.remaining)
                        .context("exposure overflow")?
                        > a.ceiling
                    {
                        self.store.set_hold(&self.bot, HoldKind::Ceiling, "reported usage exceeded the absolute ceiling; owner review is required").await?;
                    }
                    if input > self.policy.max_input_tokens
                        || output > u64::from(self.policy.max_output_tokens)
                    {
                        self.store
                            .set_hold(
                                &self.bot,
                                HoldKind::Investigation,
                                "provider usage exceeded configured request bounds",
                            )
                            .await?;
                    }
                }
                Outcome::Unknown(_) => {
                    ensure!(
                        self.store
                            .mark_unresolved(&self.bot, &ticket.request_id)
                            .await?,
                        "unknown reservation for unresolved charge"
                    );
                }
            }
            Ok(())
        })
    }
}

/// Opt-in migration preserves legacy bots. Existing owner changes remain authoritative.
pub async fn install(
    config: &Config,
    store: &Store,
) -> anyhow::Result<Option<crate::agent::Accounting>> {
    let Some(p) = &config.accounting else {
        return Ok(None);
    };
    let bot = &config
        .vm
        .as_ref()
        .context("accounting requires a configured bot")?
        .bot_id;
    store.bot_flags(bot, "Bot").await?;
    if let Some(a) = store.accounting(bot).await? {
        ensure!(
            a.currency == p.currency
                && a.exponent == p.exponent
                && a.period == p.period
                && a.policy_version == p.policy_version,
            "accounting configuration differs from durable unit, period or policy; review migration first"
        );
    } else {
        let out = store
            .configure_accounting(
                bot,
                &format!("configure:{bot}"),
                &AccountingConfig {
                    currency: &p.currency,
                    exponent: p.exponent,
                    period: &p.period,
                    policy_version: &p.policy_version,
                    ceiling: p.ceiling,
                    allowance: p.allowance,
                },
            )
            .await?;
        ensure!(
            matches!(out, Write::Applied(_)),
            "accounting configuration failed: {out:?}"
        );
    }
    let options = model::RequestOptions {
        max_output_tokens: Some(p.max_output_tokens),
        max_response_bytes: Some(p.max_response_bytes),
        token_field: if p.completion_token_field {
            model::TokenField::MaxCompletionTokens
        } else {
            model::TokenField::MaxTokens
        },
        usage: if p.request_usage {
            model::UsageRequest::StreamOptions
        } else {
            model::UsageRequest::Off
        },
        ..Default::default()
    };
    Ok(Some(crate::agent::Accounting {
        accountant: Arc::new(Meter {
            store: store.clone(),
            bot: bot.clone(),
            model: config.model.agent_model.clone(),
            policy: p.clone(),
        }),
        options,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::UnknownReason;

    async fn meter() -> Meter {
        let store = Store::open_memory().unwrap();
        store.bot_flags("b", "Bot").await.unwrap();
        store
            .configure_accounting(
                "b",
                "config",
                &AccountingConfig {
                    currency: "TST",
                    exponent: 2,
                    period: "test",
                    policy_version: "v1",
                    ceiling: 30,
                    allowance: 5,
                },
            )
            .await
            .unwrap();
        let policy = Policy {
            currency: "TST".into(),
            exponent: 2,
            period: "test".into(),
            policy_version: "v1".into(),
            ceiling: 30,
            allowance: 5,
            increase_increment: 5,
            price_version: "fixture".into(),
            input_per_million: 1_000_000,
            output_per_million: 1_000_000,
            extra_per_call: 0,
            max_input_tokens: 10,
            max_input_bytes: 100,
            max_output_tokens: 10,
            max_response_bytes: 1000,
            completion_token_field: false,
            request_usage: true,
        };
        Meter {
            store,
            bot: "b".into(),
            model: "fixture".into(),
            policy,
        }
    }

    fn request(id: &str) -> CallRequest {
        CallRequest {
            request_id: id.into(),
            model: "fixture".into(),
            input_bytes: 10,
            max_output_tokens: 10,
        }
    }

    #[tokio::test]
    async fn automatically_increases_then_holds_unknown_exposure_at_the_ceiling() {
        let m = meter().await;
        let Admission::Admitted(ticket) = m.admit(&request("one")).await.unwrap() else {
            panic!()
        };
        assert_eq!(
            m.store.accounting("b").await.unwrap().unwrap().allowance,
            20
        );
        m.settle(&ticket, Outcome::Unknown(UnknownReason::MissingUsage))
            .await
            .unwrap();
        assert!(matches!(
            m.admit(&request("two")).await.unwrap(),
            Admission::Hold(_)
        ));
        assert_eq!(m.store.exposure("b").await.unwrap().unwrap().unresolved, 20);
        assert_eq!(m.store.accounting("b").await.unwrap().unwrap().ceiling, 30);
    }

    #[tokio::test]
    async fn settles_reported_cost_once_and_rejects_duplicate_dispatch() {
        let m = meter().await;
        let req = request("one");
        let Admission::Admitted(ticket) = m.admit(&req).await.unwrap() else {
            panic!()
        };
        assert!(matches!(m.admit(&req).await.unwrap(), Admission::Hold(_)));
        let usage = model::Usage {
            prompt_tokens: Some(2),
            completion_tokens: Some(3),
            ..Default::default()
        };
        m.settle(&ticket, Outcome::Reported(usage.clone()))
            .await
            .unwrap();
        m.settle(&ticket, Outcome::Reported(usage)).await.unwrap();
        assert_eq!(m.store.exposure("b").await.unwrap().unwrap().settled, 5);
        assert!(matches!(
            m.admit(&request("two")).await.unwrap(),
            Admission::Admitted(_)
        ));
    }
}
