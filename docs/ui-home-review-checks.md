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
