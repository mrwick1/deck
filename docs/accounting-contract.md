# Accounting contract

This document specifies `src/accounting.rs`. The module is pure. It does no I/O, reads no clock and picks no default value. Callers supply every number. This is a proposal for review, not an approved decision under D61.

## Terms

- **Ceiling:** the owner-set absolute limit for one period. Only an authenticated owner operation in the Store may change it. `Ledger` has no ceiling setter. A model suggestion cannot reach it.
- **Working allowance:** the limit the bot may spend now. It never exceeds the ceiling. It may rise automatically under `IncreasePolicy`.
- **Exposure:** settled + pending + unresolved. Every admission test uses exposure, not the allowance alone.
  - *Settled:* charges that are known.
  - *Pending:* reservations of requests in flight.
  - *Unresolved:* reservations whose charge is unknown after a lost reply, restart or missing usage.

## Amounts

- `Unit` is a three-letter uppercase currency code plus a decimal scale. The owner or config chooses both.
- `Amount` is a `u64` count of units. Addition and subtraction are checked. Overflow, underflow, a currency mismatch or a scale mismatch is an error. Nothing wraps or saturates.
- Prices are per one million tokens, in units. Token cost rounds up. Reservations over-estimate by design.
- `Pricing.version` is returned in `Quote::Priced` so the caller can store it with the charge.

## Period identity

`PeriodId` is an opaque non-empty string from the owner or config. The module never derives a period from time. Rollover is not implemented. Calendar, timezone and the fate of a held ceiling request at rollover stay open until the owner approves them.

## Admission

`Ledger::admit(quote, suggestion)` returns one of these:

1. `UnknownPricing`: the quote is `Unpriced`. The request is held. It is never admitted as free.
2. `CeilingHold`: exposure + reservation is above the ceiling. Equal to the ceiling is allowed. The caller holds the work and creates one owner request. `short_by` is the amount to add.
3. `Admitted` with no increase: exposure + reservation fits the allowance. `next` holds the reservation as pending.
4. `Admitted` with `Increase`: the reservation fits the ceiling but not the allowance. The allowance rises by

   `applied = min(max(shortfall, min_increment, suggestion), ceiling - allowance, max_step, max_period_total - auto_increased)`

   `shortfall` is exposure + reservation - allowance. `clamped_by` names the bound that reduced `wanted`, so the caller can record the reason.
5. `IncreaseBoundHold`: the reservation fits the ceiling, but `applied` would be below `shortfall`. Step or period-total policy blocks it. This is not an owner ceiling request. The caller pauses the task for investigation.

`suggestion` is an amount a model proposed. It can only enlarge a needed increase, inside every bound. With no shortfall it has no effect.

## Reserve and settle

- `admit` returns the next ledger. The caller must persist it and the reservation identity in one transaction. Concurrent callers must serialize on that transaction. The module does not lock.
- `settle(reserved, Some(actual))`: pending drops by `reserved`. Settled rises by `actual`. Actual may exceed the reservation. Exposure may then pass the allowance or the ceiling. The next admission holds.
- `settle(reserved, None)`: pending drops by `reserved`. Unresolved rises by the same amount. A timeout does not free money.
- `resolve(held, actual)`: unresolved drops by `held`. Settled rises by `actual`.
- The caller must call each of these once per reservation identity. The module cannot detect duplicates.

## Examples

Units are abstract. Currency `TST`, scale 2. Ceiling 100. Allowance 40. Step 30. Minimum increment 5. Period total 50.

| State | Request | Result |
| --- | --- | --- |
| settled 0 | 40 | Admitted. No increase. |
| settled 38 | 4 | Shortfall 2. Wanted 5. Allowance 45. |
| settled 40 | 1, suggestion 20 | Allowance 60. |
| settled 40 | 1, suggestion huge | Clamped by step. Allowance 70. |
| allowance 95, settled 95, minimum 8 | 5 | Clamped by ceiling. Allowance 100. |
| allowance 100, settled 60 | 40 | Admitted. Exposure equals the ceiling. |
| allowance 100, settled 60 | 41 | `CeilingHold`. Short by 1. |
| settled 30, pending 30, unresolved 30 | 11 | `CeilingHold`. |
| ceiling 200, allowance 40 | 75 | Shortfall 35 is above the step. `IncreaseBoundHold`. |

## Integration seam

- Store: build a `Ledger` with `Ledger::restore(LedgerParts)` from stored rows. It rejects mixed units and an allowance above the ceiling. Write back from the accessors. The Store owns ceiling changes and builds a new ledger from the owner-approved value.
- Model: call `quote(pricing, Usage, extra)` with bounded input and output tokens. `extra` bounds provider-specific charges. Missing pricing gives `Quote::Unpriced`.
- Not covered: spike and no-progress thresholds, rollover, idempotency, notifications, and spending through raw browser or shell tools under D33.

## Integration checkpoint

The configured-bot adapter uses the absolute ceiling as the default monetary bound on automatic increases. It does not activate a separate step or period-increase cap. The configured increment can add headroom for the next request. The Store applies the allowance change and reservation in one transaction.

Model accounting is opt-in for existing configured bots. The owner supplies currency, precision, period, allowance, ceiling, increment, prices and request bounds. Restart does not overwrite owner ceiling changes from the stored record. Missing usage retains the reservation as unresolved exposure. Provider bounds require verification before claiming an invoice cap. External purchases remain outside model-call accounting.

Owner ceiling responses now use durable request and response records. They preserve independent holds. Setup provisioning and spike/no-progress policy are not complete at this checkpoint. No period rollover runs automatically.
