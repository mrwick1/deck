# Conversations review checks

## Round one feedback exchange

Both reviewers used committed b5ef209 and measured the current worktree.

| Finding | Source | Correction |
| --- | --- | --- |
| Unbroken titles overflow the panel, though the document fits | Both | The title column can shrink and wrap anywhere. Icons do not shrink. |
| Back loses list scroll and selected row focus | Both | History uses the existing scroll map. A selected row becomes the route focus target on return. |
| Existing chats have a generic heading | Opus | Existing chat headers use the known conversation title. New chat retains Chat with Deck. |
| Empty history looks blank | Both | Show No conversations yet and an invitation to start one. |
| Stale history permits navigation to a disabled composer | Opus | Keep reading and navigation available. Explain that the list is cached and sending needs reconnection. |
| Bot rows have no identity and ordering is unclear | Opus | Label rows with the bot name or Deck. State newest conversations first; retain server order. |
| Cold loading focus remains on BODY | Opus, optional | Defer. Loaded routes already focus their heading. |

No last-activity timestamp exists in this list contract. The change adds no invented date or activity ordering.
Explicit navigation to Conversations starts at the top. Back restores the prior position and row.
A missing selected row falls back to the route heading.

## Verification

Type checking, all 173 tests and the build pass. The build retains the shared-chunk warning.
Both second-round reviews remain required.
Physical input, keyboard viewport, screen readers and animation frame timing are untested.

The parent measured the current screen in bare at 320×640 with synthetic chats.
An unbroken title stayed inside the panel: clientWidth and scrollWidth were both 305px.
Opening Conversation 20 showed its title. Back focused its row and restored scrollTop 1788.
The driver scrolled to activate that row, so this is evidence of nonzero restoration, not an exact comparison with its earlier 900px setting.
An initial probe hit a toast from the fixture's unsupported history endpoint. The repeated probe used an isolated empty-history response and completed. All owned sessions closed.

## Final round-two correction (2026-11-15)

Both reviewers completed round two. The remaining selected-row defect is corrected: New conversation clears the previous row selection. A bare fixture run at 320×640 opened a row, returned, opened New conversation and returned again. Focus landed on the Conversations heading. The panel measured 305px for both client and scroll width. See `/tmp/deck-history-return-final.log` for local evidence.

The list retains one return position, not one position per browser history entry. An explicit visit resets it. Returning to an older history entry after that visit does not recover its older position. Cold existing chats now use the neutral loading title “Conversation”. Physical input, screen-reader output and frame timing remain untested.
