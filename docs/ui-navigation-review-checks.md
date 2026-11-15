# Mobile navigation review checks

Use this list for round two of the navigation review. Each item names what the code now does. Each item names its evidence level.

## Evidence levels

- **Test:** A unit test proves the outcome against a fake history.
- **Source:** The code does this. No browser run confirmed it.
- **Unverified:** Only a real device or a browser check can confirm it.

## Back

| Check | Expected result | Evidence |
| --- | --- | --- |
| Home, Conversations, New conversation, Back, Back | Conversations, then Home. No extra entry. | Test |
| Direct entry on a non-Home route, Back | Home replaces the entry. The app does not close. | Test |
| Browser Forward after Back | The next route opens. Back still works. | Test |
| Refresh on a deep route, Back | The previous in-app route opens. | Test |
| Open the current route again | No new entry. | Test |
| A typed hash | Counts as one step deeper. | Test |
| First message in a new chat | `chat/new` becomes `chat/<id>`. Back skips it. | Test (replace); Source (call site) |
| A pending create finishes after navigation | A toast replaces the redirect. | Source (`navigationEpoch` unchanged) |
| Drafts and accepted setups | Kept across Back and Forward. | Source |

## Cold load and errors

- Each route shows its own title, Back button and skeleton before data arrives.
- Only chat routes and New bot show a composer skeleton.
- Home keeps its earlier loading and error layout.
- Every header is 64px. Measure at 412x820, 320x640 and 412x450. Evidence: Source (CSS). Measurement: Unverified.

## Motion

- Each route is one keyed unit. The outgoing unit is `inert`.
- Reduced motion renders no entrance and no exit. Evidence: Source.
- The route unit remounts only when its route key changes. Polls do not remount it. A chat ID change keeps the wrapper key, but Chat still uses the full route as its key and remounts. First-message handoff and stream continuity require separate chat-screen verification.
- Frame smoothness: Unverified. Do not report it from source.

## Controls and scroll

- The bot controls menu is a Radix `DropdownMenu`. Outside click, Escape and Tab close it. Focus returns to the trigger. Items are 44px. Evidence: Source. Render: Unverified. See bare issues 557, 558 and 559.
- Tab scroll restores in a layout effect keyed to the id and tab. Polls do not write `scrollTop`. Evidence: Source.
- Home scroll restores through a callback ref keyed to the page.

## Safe areas

- Both entry pages use `viewport-fit=cover`.
- `.app` applies the top, left and right insets once. Composers keep the bottom inset.
- Notch, home indicator and landscape insets on a real device: Unverified.

## Known limits

- No React render tests exist. The repository has no DOM test environment.
- Recovery focus selects only the active route unit. On mount, a recovery route focuses its row or composer. Rendered verification remains required.
- The Home gate result is unchanged by this work.

## Parent fixture checks after implementation

At 412 × 450 in the isolated bare build, Home → Conversations → New conversation → Back returns to Conversations at depth 1. Another Back returns Home at depth 0. Both destination headings receive focus. Both headers measure 64px. A cold Conversations deep link shows its own title, a 64px header and no composer. After data arrives, the title and header height remain unchanged.

The parent corrected recovery focus to exclude the outgoing route, added explicit menu dismissal when the bot tab changes, and scoped menu width CSS to the portal class. These corrections are source-checked. Type checking, all 74 tests and the production build pass. The build retains its shared-chunk size warning.

Route transition frame pacing, physical keyboard behavior, safe areas and sheet motion remain unverified. The browser limitations in issues 557–559 remain separate from the navigation fixes.
