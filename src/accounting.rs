//! Pure monetary contract for bot spending. See docs/accounting-contract.md.
//! No I/O, no clock, no storage. Every value comes from the caller.

use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum AccountingError {
    #[error("currency must be 3 ASCII uppercase letters")]
    BadCurrency,
    #[error("period id must not be empty")]
    EmptyPeriod,
    #[error("currency or precision mismatch")]
    Mismatch,
    #[error("amount overflow")]
    Overflow,
    #[error("amount underflow")]
    Underflow,
    #[error("invalid ledger: {0}")]
    InvalidLedger(&'static str),
}

type Result<T> = std::result::Result<T, AccountingError>;

/// Currency code and decimal precision. Both must match for any arithmetic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unit {
    currency: String,
    /// Decimal places of one integer unit, for example 2 means 1 unit = 0.01.
    scale: u8,
}

impl Unit {
    pub fn new(currency: &str, scale: u8) -> Result<Self> {
        let ok = currency.len() == 3 && currency.bytes().all(|b| b.is_ascii_uppercase());
        if !ok {
            return Err(AccountingError::BadCurrency);
        }
        Ok(Self {
            currency: currency.to_string(),
            scale,
        })
    }

    pub fn amount(&self, units: u64) -> Amount {
        Amount {
            unit: self.clone(),
            units,
        }
    }

    pub fn currency(&self) -> &str {
        &self.currency
    }

    pub fn scale(&self) -> u8 {
        self.scale
    }
}

/// A non-negative integer amount in the units of its `Unit`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Amount {
    unit: Unit,
    units: u64,
}

impl Amount {
    pub fn unit(&self) -> &Unit {
        &self.unit
    }

    pub fn units(&self) -> u64 {
        self.units
    }

    fn same(&self, other: &Amount) -> Result<()> {
        if self.unit == other.unit {
            Ok(())
        } else {
            Err(AccountingError::Mismatch)
        }
    }

    fn with(&self, units: u64) -> Amount {
        Amount {
            unit: self.unit.clone(),
            units,
        }
    }

    pub fn checked_add(&self, other: &Amount) -> Result<Amount> {
        self.same(other)?;
        let units = self
            .units
            .checked_add(other.units)
            .ok_or(AccountingError::Overflow)?;
        Ok(self.with(units))
    }

    pub fn checked_sub(&self, other: &Amount) -> Result<Amount> {
        self.same(other)?;
        let units = self
            .units
            .checked_sub(other.units)
            .ok_or(AccountingError::Underflow)?;
        Ok(self.with(units))
    }

    fn max(&self, other: &Amount) -> Result<Amount> {
        self.same(other)?;
        Ok(self.with(self.units.max(other.units)))
    }

    fn min(&self, other: &Amount) -> Result<Amount> {
        self.same(other)?;
        Ok(self.with(self.units.min(other.units)))
    }
}

/// Owner or config supplied period identity. Deck never derives it from a clock.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PeriodId(String);

impl PeriodId {
    pub fn new(id: &str) -> Result<Self> {
        if id.trim().is_empty() {
            return Err(AccountingError::EmptyPeriod);
        }
        Ok(Self(id.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Configured price of one model, per one million tokens. `version` is recorded with each charge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pricing {
    pub version: String,
    pub input_per_million: Amount,
    pub output_per_million: Amount,
}

/// Upper bounds for one paid request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
}

fn token_cost(tokens: u64, per_million: &Amount) -> Result<Amount> {
    let n = u128::from(tokens) * u128::from(per_million.units);
    let ceil = n.div_ceil(1_000_000);
    let units = u64::try_from(ceil).map_err(|_| AccountingError::Overflow)?;
    Ok(per_million.with(units))
}

/// The outcome of pricing a request. `Unpriced` is never zero cost.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Quote {
    Priced { version: String, amount: Amount },
    Unpriced,
}

/// Round up. `extra` covers provider-specific charges the caller bounds itself.
pub fn quote(pricing: Option<&Pricing>, usage: Usage, extra: &Amount) -> Result<Quote> {
    let Some(p) = pricing else {
        return Ok(Quote::Unpriced);
    };
    let input = token_cost(usage.input_tokens, &p.input_per_million)?;
    let output = token_cost(usage.output_tokens, &p.output_per_million)?;
    let amount = input.checked_add(&output)?.checked_add(extra)?;
    Ok(Quote::Priced {
        version: p.version.clone(),
        amount,
    })
}

/// Bounds for automatic working-allowance increases. All values come from configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IncreasePolicy {
    /// Largest single increase.
    pub max_step: Amount,
    /// Smallest useful increase. A larger shortfall or suggestion wins.
    pub min_increment: Amount,
    /// Largest sum of automatic increases in one period.
    pub max_period_total: Amount,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Clamp {
    Ceiling,
    Step,
    PeriodTotal,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Increase {
    pub from: Amount,
    pub to: Amount,
    /// Shortfall that caused the increase.
    pub shortfall: Amount,
    /// Increase wanted before clamping.
    pub wanted: Amount,
    /// Set when `wanted` was reduced. The shortfall still fits.
    pub clamped_by: Option<Clamp>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Admission {
    /// Reserve. `next` already holds the reservation as pending.
    Admitted {
        next: Box<Ledger>,
        increase: Option<Increase>,
    },
    /// Exposure plus reservation exceeds the owner ceiling. Needs an owner decision.
    CeilingHold {
        needed: Amount,
        ceiling: Amount,
        short_by: Amount,
    },
    /// Fits the ceiling, but policy bounds forbid a large enough automatic increase.
    /// Not an owner ceiling request. The caller investigates.
    IncreaseBoundHold { shortfall: Amount, bound: Clamp },
    /// No configured price. Hold; never admit as free.
    UnknownPricing,
}

/// Spending state of one bot in one period.
/// `ceiling` has no setter. Only `restore` builds a ledger, from owner-approved storage.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ledger {
    period: PeriodId,
    ceiling: Amount,
    allowance: Amount,
    settled: Amount,
    pending: Amount,
    unresolved: Amount,
    auto_increased: Amount,
    policy: IncreasePolicy,
}

/// Stored fields of a ledger.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LedgerParts {
    pub period: PeriodId,
    pub ceiling: Amount,
    pub allowance: Amount,
    pub settled: Amount,
    pub pending: Amount,
    pub unresolved: Amount,
    pub auto_increased: Amount,
    pub policy: IncreasePolicy,
}

impl Ledger {
    /// Validate stored parts. Allowance must not exceed the ceiling.
    /// Settled spend above the allowance is legal: a charge can exceed its estimate.
    pub fn restore(p: LedgerParts) -> Result<Self> {
        let c = &p.ceiling;
        for a in [
            &p.allowance,
            &p.settled,
            &p.pending,
            &p.unresolved,
            &p.auto_increased,
            &p.policy.max_step,
            &p.policy.min_increment,
            &p.policy.max_period_total,
        ] {
            c.same(a)?;
        }
        if p.allowance.units > c.units {
            return Err(AccountingError::InvalidLedger("allowance above ceiling"));
        }
        Ok(Self {
            period: p.period,
            ceiling: p.ceiling,
            allowance: p.allowance,
            settled: p.settled,
            pending: p.pending,
            unresolved: p.unresolved,
            auto_increased: p.auto_increased,
            policy: p.policy,
        })
    }

    pub fn period(&self) -> &PeriodId {
        &self.period
    }
    pub fn ceiling(&self) -> &Amount {
        &self.ceiling
    }
    pub fn allowance(&self) -> &Amount {
        &self.allowance
    }
    pub fn settled(&self) -> &Amount {
        &self.settled
    }
    pub fn pending(&self) -> &Amount {
        &self.pending
    }
    pub fn unresolved(&self) -> &Amount {
        &self.unresolved
    }
    pub fn auto_increased(&self) -> &Amount {
        &self.auto_increased
    }

    /// Settled + pending + unresolved.
    pub fn exposure(&self) -> Result<Amount> {
        self.settled
            .checked_add(&self.pending)?
            .checked_add(&self.unresolved)
    }

    /// Decide whether to reserve `quote`. `suggestion` is a model-suggested increase
    /// amount. It only enlarges a needed increase, inside every policy bound.
    pub fn admit(&self, quote: &Quote, suggestion: Option<&Amount>) -> Result<Admission> {
        let Quote::Priced { amount, .. } = quote else {
            return Ok(Admission::UnknownPricing);
        };
        let needed = self.exposure()?.checked_add(amount)?;
        if needed.units > self.ceiling.units {
            return Ok(Admission::CeilingHold {
                short_by: needed.checked_sub(&self.ceiling)?,
                needed,
                ceiling: self.ceiling.clone(),
            });
        }
        let mut next = self.clone();
        next.pending = self.pending.checked_add(amount)?;
        if needed.units <= self.allowance.units {
            return Ok(Admission::Admitted {
                next: Box::new(next),
                increase: None,
            });
        }
        let shortfall = needed.checked_sub(&self.allowance)?;
        let mut wanted = shortfall.max(&self.policy.min_increment)?;
        if let Some(s) = suggestion {
            wanted = wanted.max(s)?;
        }
        let headroom = self.ceiling.checked_sub(&self.allowance)?;
        let period_left = self
            .policy
            .max_period_total
            .checked_sub(&self.auto_increased);
        // Order is the tie-break for `clamped_by`: ceiling, then step, then period total.
        let mut applied = wanted.clone();
        let mut clamped_by = None;
        let mut bounds = vec![
            (Clamp::Ceiling, headroom),
            (Clamp::Step, self.policy.max_step.clone()),
        ];
        bounds.push((
            Clamp::PeriodTotal,
            period_left.unwrap_or_else(|_| self.policy.max_period_total.with(0)),
        ));
        for (kind, limit) in bounds {
            if limit.units < applied.units {
                applied = limit.min(&applied)?;
                clamped_by = Some(kind);
            }
        }
        if applied.units < shortfall.units {
            return Ok(Admission::IncreaseBoundHold {
                shortfall,
                bound: clamped_by.unwrap_or(Clamp::Step),
            });
        }
        next.allowance = self.allowance.checked_add(&applied)?;
        next.auto_increased = self.auto_increased.checked_add(&applied)?;
        Ok(Admission::Admitted {
            increase: Some(Increase {
                from: self.allowance.clone(),
                to: next.allowance.clone(),
                shortfall,
                wanted,
                clamped_by,
            }),
            next: Box::new(next),
        })
    }

    /// Settle one reservation. `actual` is the reported charge, or `None` when usage is
    /// missing or the reply was lost. `None` keeps the reserved amount as unresolved
    /// exposure. It never frees it.
    pub fn settle(&self, reserved: &Amount, actual: Option<&Amount>) -> Result<Ledger> {
        let mut next = self.clone();
        next.pending = self.pending.checked_sub(reserved)?;
        match actual {
            Some(a) => next.settled = self.settled.checked_add(a)?,
            None => next.unresolved = self.unresolved.checked_add(reserved)?,
        }
        Ok(next)
    }

    /// Replace unresolved exposure `held` with the later-known charge `actual`.
    pub fn resolve(&self, held: &Amount, actual: &Amount) -> Result<Ledger> {
        let mut next = self.clone();
        next.unresolved = self.unresolved.checked_sub(held)?;
        next.settled = self.settled.checked_add(actual)?;
        Ok(next)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u() -> Unit {
        Unit::new("TST", 2).unwrap()
    }
    fn a(n: u64) -> Amount {
        u().amount(n)
    }
    fn parts() -> LedgerParts {
        LedgerParts {
            period: PeriodId::new("p1").unwrap(),
            ceiling: a(100),
            allowance: a(40),
            settled: a(0),
            pending: a(0),
            unresolved: a(0),
            auto_increased: a(0),
            policy: IncreasePolicy {
                max_step: a(30),
                min_increment: a(5),
                max_period_total: a(50),
            },
        }
    }
    fn ledger(f: impl FnOnce(&mut LedgerParts)) -> Ledger {
        let mut p = parts();
        f(&mut p);
        Ledger::restore(p).unwrap()
    }
    fn priced(n: u64) -> Quote {
        Quote::Priced {
            version: "v".into(),
            amount: a(n),
        }
    }

    #[test]
    fn rejects_overflow_underflow_and_mismatch() {
        assert_eq!(
            a(u64::MAX).checked_add(&a(1)),
            Err(AccountingError::Overflow)
        );
        assert_eq!(a(1).checked_sub(&a(2)), Err(AccountingError::Underflow));
        let other = Unit::new("OTH", 2).unwrap().amount(1);
        assert_eq!(a(1).checked_add(&other), Err(AccountingError::Mismatch));
        let scale = Unit::new("TST", 3).unwrap().amount(1);
        assert_eq!(a(1).checked_add(&scale), Err(AccountingError::Mismatch));
        assert_eq!(Unit::new("usd", 2), Err(AccountingError::BadCurrency));
        assert_eq!(PeriodId::new(" "), Err(AccountingError::EmptyPeriod));
    }

    #[test]
    fn restore_rejects_mixed_currency_and_allowance_above_ceiling() {
        let mut p = parts();
        p.settled = Unit::new("OTH", 2).unwrap().amount(0);
        assert_eq!(Ledger::restore(p), Err(AccountingError::Mismatch));
        let mut p = parts();
        p.allowance = a(101);
        assert!(matches!(
            Ledger::restore(p),
            Err(AccountingError::InvalidLedger(_))
        ));
    }

    #[test]
    fn quote_rounds_up_and_unknown_price_is_not_free() {
        let p = Pricing {
            version: "v7".into(),
            input_per_million: a(300),
            output_per_million: a(1500),
        };
        let usage = Usage {
            input_tokens: 1,
            output_tokens: 1,
        };
        // 1 + 1 rounded up, plus extra 2.
        assert_eq!(
            quote(Some(&p), usage, &a(2)).unwrap(),
            Quote::Priced {
                version: "v7".into(),
                amount: a(4)
            }
        );
        assert_eq!(quote(None, usage, &a(0)).unwrap(), Quote::Unpriced);
        let big = Pricing {
            input_per_million: a(u64::MAX),
            ..p
        };
        let huge = Usage {
            input_tokens: u64::MAX,
            output_tokens: 0,
        };
        assert_eq!(
            quote(Some(&big), huge, &a(0)),
            Err(AccountingError::Overflow)
        );
    }

    #[test]
    fn unknown_pricing_holds() {
        assert_eq!(
            ledger(|_| {}).admit(&Quote::Unpriced, None).unwrap(),
            Admission::UnknownPricing
        );
    }

    #[test]
    fn within_allowance_admits_without_increase() {
        let Admission::Admitted { next, increase } =
            ledger(|_| {}).admit(&priced(40), None).unwrap()
        else {
            panic!()
        };
        assert_eq!(increase, None);
        assert_eq!(next.pending(), &a(40));
        assert_eq!(next.allowance(), &a(40));
    }

    #[test]
    fn exact_ceiling_admits_and_one_over_holds() {
        let l = ledger(|p| {
            p.allowance = a(100);
            p.settled = a(60);
        });
        assert!(matches!(
            l.admit(&priced(40), None).unwrap(),
            Admission::Admitted { .. }
        ));
        assert_eq!(
            l.admit(&priced(41), None).unwrap(),
            Admission::CeilingHold {
                needed: a(101),
                ceiling: a(100),
                short_by: a(1)
            }
        );
    }

    #[test]
    fn pending_and_unresolved_count_against_ceiling() {
        let l = ledger(|p| {
            p.allowance = a(100);
            p.settled = a(30);
            p.pending = a(30);
            p.unresolved = a(30);
        });
        assert_eq!(l.exposure().unwrap(), a(90));
        assert!(matches!(
            l.admit(&priced(11), None).unwrap(),
            Admission::CeilingHold { .. }
        ));
        assert!(matches!(
            l.admit(&priced(10), None).unwrap(),
            Admission::Admitted { .. }
        ));
    }

    #[test]
    fn increase_uses_min_increment_and_records_reason() {
        // exposure 38 + 4 = 42, shortfall 2, min 5 wanted.
        let l = ledger(|p| p.settled = a(38));
        let Admission::Admitted { next, increase } = l.admit(&priced(4), None).unwrap() else {
            panic!()
        };
        let inc = increase.unwrap();
        assert_eq!((inc.shortfall, inc.wanted), (a(2), a(5)));
        assert_eq!(inc.clamped_by, None);
        assert_eq!(next.allowance(), &a(45));
        assert_eq!(next.auto_increased(), &a(5));
        assert_eq!(next.ceiling(), &a(100));
    }

    #[test]
    fn suggestion_raises_increase_but_never_beyond_bounds() {
        let l = ledger(|p| p.settled = a(40));
        let Admission::Admitted { next, increase } = l.admit(&priced(1), Some(&a(20))).unwrap()
        else {
            panic!()
        };
        assert_eq!(increase.unwrap().wanted, a(20));
        assert_eq!(next.allowance(), &a(60));
        // A huge suggestion clamps to max_step (30), not to the ceiling.
        let Admission::Admitted { next, increase } =
            l.admit(&priced(1), Some(&a(u64::MAX))).unwrap()
        else {
            panic!()
        };
        assert_eq!(increase.unwrap().clamped_by, Some(Clamp::Step));
        assert_eq!(next.allowance(), &a(70));
        assert_eq!(next.ceiling(), &a(100));
    }

    #[test]
    fn increase_clamps_to_ceiling_headroom() {
        let l = ledger(|p| {
            p.allowance = a(95);
            p.settled = a(95);
            p.policy.min_increment = a(8);
        });
        let Admission::Admitted { next, increase } = l.admit(&priced(5), None).unwrap() else {
            panic!()
        };
        assert_eq!(increase.unwrap().clamped_by, Some(Clamp::Ceiling));
        assert_eq!(next.allowance(), &a(100));
    }

    #[test]
    fn shortfall_above_step_holds_without_owner_request() {
        // exposure 40 + 75 = 115 > ceiling; use ceiling 200 so only the step bound fails.
        let l = ledger(|p| {
            p.ceiling = a(200);
            p.policy.max_period_total = a(200);
        });
        assert_eq!(
            l.admit(&priced(75), None).unwrap(),
            Admission::IncreaseBoundHold {
                shortfall: a(35),
                bound: Clamp::Step
            }
        );
    }

    #[test]
    fn period_total_bound_holds() {
        let l = ledger(|p| {
            p.settled = a(40);
            p.auto_increased = a(48);
        });
        assert_eq!(
            l.admit(&priced(10), None).unwrap(),
            Admission::IncreaseBoundHold {
                shortfall: a(10),
                bound: Clamp::PeriodTotal
            }
        );
    }

    #[test]
    fn settle_frees_reservation_and_unknown_usage_keeps_it() {
        let Admission::Admitted { next, .. } = ledger(|_| {}).admit(&priced(10), None).unwrap()
        else {
            panic!()
        };
        let done = next.settle(&a(10), Some(&a(7))).unwrap();
        assert_eq!((done.pending(), done.settled()), (&a(0), &a(7)));
        let lost = next.settle(&a(10), None).unwrap();
        assert_eq!(
            (lost.pending(), lost.unresolved(), lost.exposure().unwrap()),
            (&a(0), &a(10), a(10))
        );
        let found = lost.resolve(&a(10), &a(9)).unwrap();
        assert_eq!(
            (
                found.unresolved(),
                found.settled(),
                found.exposure().unwrap()
            ),
            (&a(0), &a(9), a(9))
        );
        assert!(done.settle(&a(1), None).is_err());
    }

    #[test]
    fn overspend_beyond_estimate_blocks_next_admission() {
        let Admission::Admitted { next, .. } = ledger(|p| p.allowance = a(100))
            .admit(&priced(10), None)
            .unwrap()
        else {
            panic!()
        };
        let l = next.settle(&a(10), Some(&a(101))).unwrap();
        assert!(matches!(
            l.admit(&priced(0), None).unwrap(),
            Admission::CeilingHold { .. }
        ));
    }
}
