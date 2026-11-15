# Setup progress review checks

This file records the round one findings from two reviewers and the correction for each. Both round two reviewers can read it. The screen is not final until both complete round two.

## Fixture

Run the Vite dev server. Open `/review/home.html?mode=<mode>#setup/fixture-bot`. Modes: `setup-phone`, `setup-secret`, `setup-unsupported`, `setup-investigate`, `setup-exhausted`, `setup-preparing`. The fixture replaces fetch and EventSource. It sends nothing to a real backend. The progress endpoints are strict. A stale version or request id returns 409. Pause and Resume change no row, as on the server. The secret body is stored as `[redacted]`. Only synthetic values are used.

Hooks on `window.__homeFixture`: `holdAct` with `releaseAct()` holds an action reply. `actFail` makes actions fail. `revise()` raises the row version. `hits` counts action POSTs. `control` lists accepted pause commands. `fail` makes reads fail.

## Findings and corrections

| Finding | Correction |
| --- | --- |
| The key was `id-version` outside Draft. Each poll lost typing, focus, scroll and errors. | The key is the setup id. `setupKey` in `setup-progress.ts`. |
| Two fast clicks sent two challenge answers. | `guarded` in `lib/wait.ts` holds a synchronous guard. Controls use `aria-disabled`, so focus stays. |
| A duplicate protected submit set busy to false while the first was held. | `deliverSecret` flags `duplicate`. `SecretForm` returns before any state change. |
| Answer errors showed in a toast only. | The error shows inline, names the request and has no secret text. |
| Buttons measured 28px. Cancel passed the panel edge. | Rows wrap. Buttons are at least 44px. Inputs use 16px text. |
| Recovery came above the timed wait. | Phase, error and wait come first. Recovery comes last, with full fields and Discard. |
| Pause and Resume gave no feedback. | A pending line, then Pause requested or Resume requested after 204. No paused state is invented. |
| Resume stayed on for an unsupported challenge. | Resume is off with a stated reason. It is also off for an unknown outcome. |
| Refresh progress gave no result. | A pending line, then a result read from the workspace error. |
| The header always said Review setup. | Draft says Review setup. Later phases say Setup progress. The cold route says Setup. |
| The attempt limit accepted any number above the limit. | A whole number above the limit up to 4294967295. An invalid value sends nothing and focuses the field. |
| Unknown outcome showed one sentence. | It shows step text and says Deck cannot resolve it yet. |
| A server error hid a local control error. | Both show. |

## Red results before the fix

The first red run used stubs that copy the old behavior. Four of seven tests failed:

- `setupKey`: expected `bot-one-4` to be `bot-one-5`.
- `guarded`: expected the send mock to be called 1 time, got 2.
- `deliverSecret`: expected `{ ok: false, duplicate: true }`. The result had no `duplicate`.
- `checkAttempts`: expected an empty message to match `/whole number/i`.

After the fix, all ten tests in `setup-progress.test.ts` pass.

## Measured in `bare` (fixture, `window:false`)

- 412 × 820, 320 × 640 and 412 × 450: every button and input measured at least 44px. No horizontal overflow. The right edge of every control stayed inside the viewport.
- Phone approval: the wait panel starts at 158px, above the checklist, in all three sizes.
- Two synchronous clicks on I completed it with a held reply produced one POST. Focus stayed on the button. The pending line read Sending your answer…. A failed reply showed an inline error.
- A cancelled request was replaced by `req-2`. The panel showed no outcome from the old request.
- Protected input kept its value, focus and scroll of 40px across a real five second poll after a version change. Two `requestSubmit` calls with a held reply produced one POST. The field stayed disabled and the button read Delivering…. The recorded body was `[redacted]`.
- Attempt limit: empty, `4.5`, `3`, `4294967296` and `1e1` sent no POST and focused the field with an error. `7` sent one POST and showed the success line.
- Pause with two fast clicks sent one POST. The pending line read Requesting pause…. Focus stayed. After 204 the line read Pause requested.
- Refresh progress showed Refresh failed after a failed read and Progress is up to date after a good read.
- Unsupported and unknown-outcome rows kept Resume off. A click sent no POST.

## Not measured

- No physical keyboard, IME, touch, screen reader or frame timing.
- Scripted clicks and `requestSubmit` calls are not pointer proof.
- No real backend, provisioning, approval or delivery ran.
- Phone approval with `expires_at` countdown and the expired state were not re-run in the browser. Their text is unchanged.
- The original app and the Requests tab share `WaitPanel`. Only its type checks, tests and the shared guard cover them. No browser run covered them.

## Parent checks before round two

The parent read the diff and independently ran type checking, all 173 tests, the build and the whitespace check. All passed. The build retains the shared-chunk warning.
The parent narrowed the new form spacing selector to direct progress forms. It must not override the accepted draft review form.
The copied-stub red tests are not execution against the old components. Browser evidence above comes from the implementation agent. Independent round two checks remain required.
