# Setup review checks

Use this list for round two of the setup review. Each row names what the code does and its evidence level.

## Evidence levels

- **Test:** A unit or markup test proves the outcome. Markup tests render the form to static HTML. They do not run events.
- **Fixture:** A `bare` run against the in-memory fixture observed it. See below.
- **Source:** The code does this. Nothing ran it.
- **Unverified:** Only a real device or a real server can confirm it.

## Local fixture

Run the Vite development server. Open `/review/home.html?mode=populated#setup/fixture-bot`. The fixture replaces `fetch` and `EventSource`. It sends nothing to a real backend. It is not a production entry.

Controls on `window.__homeFixture`:

- `hold = true` pauses each setup write. `releaseWrite()` completes it.
- `write` is `null`, `"reject"` (edit and approve fail with 400), `"reject-approve"` (only approve fails), `"lost"` (the write applies and the reply is a 503) or `"lost-unapplied"` (a 503 with no change).
- `fail = true` makes every read fail. Set it with `write = "lost"` to reach the unconfirmed state.
- `bump({ name: "x" })` acts as another client. It raises the version and revision.
- `calls` lists every request.

The fixture checks the version on edit and the revision on approve and answers 409 on a mismatch.

## Behavior

| Check | Expected result | Evidence |
| --- | --- | --- |
| Enter in Name | One save request. No approve request. Focus returns to Name. | Test (only Save is `type="submit"`); Fixture |
| Approve | Save, read the exact revision, approve that revision. | Test; Fixture |
| Poll with the same version while editing | No editor remount or draft-state change. Workspace polling can still render the parent. | Test |
| Newer row while editing | Typed fields, focus, selection and scroll stay. A conflict panel appears. Save and Approve are blocked. | Test (state, markup); Fixture (focus, selection, scroll) |
| Newer row, clean editor | The editor adopts the row. | Test |
| Own write confirmed by a poll | Not a conflict. | Test |
| Leave and reopen with unsaved edits | Fields and the conflict stay. | Test (store); Fixture (Back, then reopen) |
| Reload with unsaved edits or a running write | The browser warns. Nothing is stored. | Source |
| Reload saved draft, unchanged version | The editor resets. A dirty editor asks first. | Test; Fixture |
| Keep mine, replace latest | The base becomes the newer row. The next save succeeds. | Test; Fixture |
| Write in flight | Fieldset locked, `aria-busy`, both actions `aria-disabled`, one request for repeated clicks. | Test (markup); Fixture |
| Phase labels | Save shows "Saving draft…". Approve shows "Saving draft…", then "Approving…". | Test; Fixture |
| Success text | Shown only after the server confirms. It uses a live `status` region. | Test |
| Save works, approve is refused | "Draft saved, but approval did not happen". It survives polls. | Test; Fixture |
| Lost reply, read works | The server row decides: saved, not saved or conflict. Nothing is written again. | Test; Fixture |
| Lost reply during approve's save step | The draft shows as saved. Approval is not sent. | Test; Fixture |
| Lost reply, read fails | Save and Approve stay blocked. "Check saved state" settles it. | Test; Fixture |
| Invalid field on Save or Approve | `aria-invalid`, `aria-describedby`, the first invalid field takes focus, Advanced opens. | Test (rules, markup); Fixture |
| Resource text `1.5`, `1e3`, `0`, blank, above 4294967295 | An error. Nothing is sent. | Test |
| Budget `MAX_SAFE_INTEGER` at exponent 0 to 9 | Shows and reads back exactly. | Test |
| Zero allowance or ceiling, empty restricted list | Valid. The form says it allows no spending or no actions. | Test |
| No server budget | Save works. Approve is blocked with a reason. | Test |
| Duplicate credential labels | An error. | Test |
| Stale connection | Actions are blocked. The note says edits are kept. | Test |

## Layout

Measured in `bare` at 412x820, 320x640 and 412x450 against the fixture.

| Check | Result | Evidence |
| --- | --- | --- |
| Save and Approve visible without scrolling, flush with the viewport bottom | Yes at all three sizes | Fixture |
| Action height | 44px | Fixture |
| Field height and font | At least 44px and 16px | Fixture |
| Horizontal overflow | None | Fixture |
| Approve label at 320px | Fits in one line at 169px | Fixture |
| Long instructions | The textarea scrolls inside a fixed box | Fixture |
| Textarea growth with `field-sizing: content` | Capped at `min(40vh, 320px)` | Source. `bare` does not apply `field-sizing`, so the fallback box showed. |
| Safe-area padding under the actions | `env(safe-area-inset-bottom)` | Source |

## Not verified

- A phone keyboard, IME, touch, `select` pickers and screen readers.
- Motion and frame timing.
- Real server behavior. The fixture models the version and revision checks only.
- The `beforeunload` warning in a browser.
- Focus movement by Tab order beyond the cases above.

## Known limits

- `bare` cannot click inside a closed `details`. The runs open Advanced with a script.
- `bare` treats `aria-disabled` buttons as disabled. Blocked clicks used a script.
- The old `saveReview` function remains as a throwing wrapper for its unit tests.

## Parent checks before round two

At 320 × 640, Enter sent one save request and no approval. A newer row kept the edited name and showed a conflict with Save and Approve blocked. The session closed after the check.

An added regression failed before correction: Reload saved draft cleared an unconfirmed-write hold. Reset now retains that hold, and the reload action stays blocked until a read settles the write. Pending phases also use the live status region. The fixture checks the current version after its delayed-write gate, so another-client changes during that delay are rejected.

All 137 tests and type checking pass after these corrections. Frame pacing, physical input and real backend acceptance remain untested.
