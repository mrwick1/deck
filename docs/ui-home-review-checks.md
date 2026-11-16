# Home review checks

Home is not finalized. Both requested reviewers must complete both rounds for this screen. The current work addresses first-round findings.

## Local fixture

Run the existing Vite development command. Open `/review/home.html?mode=empty` only on the local development server. The fixture replaces fetch and EventSource before loading the real experience entry. It blocks unmatched endpoints. It sends no requests to a real backend. It is not a production build entry.

Modes: `empty`, `populated`, `ready`, `disabled`, `failure`, `loading`, `refresh-failure`. The ready mode includes long bot and result titles. `window.__homeFixture.release()` completes a pending create or queued-message request. `releaseLoad()` completes the initial loading state. Set `fail` to simulate a later connection failure.

## Reproduced before the fixes

At commit f8f4d99, an initial failed read showed both “No bots yet” and “Bot creation is not configured”. A confirmed setup followed by overview failure showed “Setup is not available”. Typing a new draft during that request then returning Home revealed an empty composer.

## Observed after the fixes

- Initial failure shows reconnect controls. It makes no empty-workspace or configuration claim.
- Two immediate form submissions produce one POST.
- Confirmed creation followed by overview failure still displays the returned setup review. Focus moves to its heading.
- A later draft survives the confirmed submission and return Home.
- Leaving Home during the pending request does not redirect the owner after completion.
- At 412 × 820 CSS pixels, first-time controls measure 44px or 52px high. The textarea uses 16px text. No horizontal overflow was observed.
- At 360 × 440 CSS pixels, the first-time composer remains inside the viewport. The main area scrolls. This is a reduced-height check, not a real keyboard test.
- During the loading-to-empty transition at 360 × 440, header height and footer position each change by 0px. This is not a full-page CLS measurement.
- The destination sheet fits the 360 × 440 viewport. Options measure 52px high. Opening transfers focus inside it. Escape closes it and restores focus to its trigger after the exit transition.
- Long bot and result titles exposed clipped rows and 32px controls. After correction, all Home action rows are at least 44px high. Their width remains within the scrolling content.
- The request entry opens the matching bot's Requests tab. The result entry opens that bot's Chat tab.

## Limits

These checks use the actual React interface with in-memory responses in the shared preview browser. They do not prove real backend acceptance, a physical Vivo keyboard, touch gestures, frame pacing or full accessibility. Reduced-motion rules require a separate browser check. The public sample remains on its previous published version until the review gate passes.

## Additional first-round fixes and checks

The first-load retry state now handles 401 responses without configuration claims. Connection and submission feedback use a reserved footer region on first-time Home. A failed poll changes neither the heading top nor the composer top in the 412 × 450 fixture check. A manual refresh displays its pending state and then its confirmed result.

The Home content ref is stable. Scroll restoration runs on page changes, not on every poll. A confirmed create retains its returned record even when a read started before the create later returns an empty list. That race was checked in the fixture.

A server rejection keeps the draft, reports the error beside the composer and focuses the textarea. A lost confirmation blocks another create until the owner checks the workspace and explicitly permits a new setup. The fixture returned a created row after a lost reply; Home showed it and kept the send action disabled. This is a client recovery guard, not server idempotency.

The owner can keep editing during a request. The completion clears only the submitted text when it is unchanged. This preserves newer edits instead of locking the textarea. Suggestion replacement requires a choice. Ctrl+Enter and Cmd+Enter use the same guarded submission path. Enter alone keeps multiline input.

At 412 × 820, 360 × 740, 320 × 640 and 412 × 450, measured Home controls remained at least 44px, input text remained 16px and the footer stayed inside the viewport. The heading used two lines without horizontal overflow. The viewport meta requests layout resize for mobile keyboards. A real phone keyboard remains untested. A fallback sizes the textarea where CSS field sizing is unsupported.

## Final Home corrections awaiting browser checks

The second review found two remaining problems. Draft replacement controls could appear below the visible scroll area. Lost-reply recovery did not refresh and could lose focus when Home changed layouts.

The replacement group now scrolls into view and focuses Keep draft. Check workspace now calls the refresh action. Recovery controls sit inside the scrolling content. A layout effect restores focus after the Home layout changes. The populated composer also supports Ctrl+Enter and Cmd+Enter. Home preserves an existing new-chat draft.

Type checking, all 64 web tests and the production build pass after these changes. The build retains its large shared-chunk warning. These checks do not verify the two rendered fixes.

The required bare browser cannot run the fixture because its static Response.json method is missing. Tracking issue: https://github.com/mrwick1/bare/issues/549. Final Home verification remains open at 320 × 640 and 412 × 450. Do not treat the earlier preview measurements as verification of these corrections. Do not finalize Home or publish this version until these checks pass.

## Measured results for the final corrections

A regression test now checks the recovery placement in `web/src/experience/HomeStart.test.ts`. It renders `HomeStart` with `react-dom/server` and no DOM. It checks that the recovery node sits inside the `start-main` region. A temporary move of the node outside that region made the test fail. The file was restored after the check. The test does not check focus, scroll or layout.

Checks run on this change: `npm --prefix web run check` exits 0. `npm --prefix web test` passes 66 tests. `npm --prefix web run build` exits 0 and keeps the shared-chunk size warning.

The rendered checks did not run. In the `bare` browser, `Response.json` is `undefined`, so the fixture shows “Let’s reconnect” instead of Home. This matches [bare#549](https://github.com/mrwick1/bare/issues/549). No shim or other browser was used. These rendered checks remain open at 320 × 640 and 412 × 450: prompt visibility, Keep draft focus, one POST after a lost reply, returned setup visibility, Check workspace reads, focus outside BODY, composer use and the duplicate-create block.

Open risk to verify rendered: the Check workspace button is disabled while `data.refreshing` is true. A focused button that becomes disabled may drop focus to BODY. The focus effect does not run again when only `refreshing` changes. This is a hypothesis, not a measured result.

## Final rendered checks with the isolated browser fix

The isolated bare build at commit 0a0bdca0 implements Response.json. It loads the fixture without a shim. This supersedes the browser block above.

At 320 × 640 and 412 × 450, the replacement group scrolls into the main viewport and Keep draft receives focus. Keeping the draft preserves its text and focuses the textarea. At 320px the group bottom differs from the main bottom by 0.36px; its controls remain visible.

The lost-reply fixture sends one POST, shows one returned setup row and keeps creation disabled. The textarea retains focus after the layout switch. Check workspace triggers four reads. The first check reproduced focus loss when that button was disabled. It now uses aria-disabled with an activation guard. Repeating the check at both sizes retains button focus. The scrolling content has 406px and 216px of height respectively. The composer ends at the viewport bottom in both cases.

Type checking and all 66 web tests pass. These checks close the two remaining Home findings. They do not establish physical phone behavior, frame pacing, screen-reader output or real backend acceptance. The browser fix is proposed in bare pull request 556; the shared browser service is unchanged.
