# Experience prototype

Run the existing web development command in this worktree. Open `/prototype.html`. The existing `/` entry point retains the current app.

Start on Home. Select New bot or send a job through the main composer. Review the proposal. Create the bot. Continue through the demo connection and simulated phone approval. Use the preview controls to finish work or simulate a failure. Pause and resume through the workspace control. Switch between Chat, Activity and Computer. Return Home to see the request or result.

The preview uses sample data. Do not enter credentials. It does not send messages, connect accounts or execute work. All state resets on reload. The offline control blocks the composer without discarding its draft.

Motion uses the installed Motion dependency and honors reduced motion. Home and workspace transitions use opacity and translation. Tabs use a shared indicator. The composer and connection status reserve space. No token animation or forced scrolling runs.

## Open work

Validate the journey in Bare on desktop and phone widths. Check keyboard focus, readable contrast, interrupted transitions and reduced motion. Measure layout shifts and frame delivery. Preserve independent chat drafts and scroll positions across navigation. Add editable spending and permission review. Add destination selection across multiple bots. Connect the prototype to the runtime only after design review.

## Verification

The journey reducer tests cover approval order and pause/failure holds. Run web type checking, tests and the production build. Browser rendering and real account behavior are separate acceptance gates.

## Visual refinement

The owner approved refinement with [Unslop](https://github.com/heydamir/unslop), created by Damir Sokolovsky. The guidance is licensed under CC BY 4.0.

Keep the approved journey and calm layout. Give bot tasks priority over introductory copy. Use neutral surfaces, three text roles and a restrained green accent. Remove decorative sparkles and the fixed progress bar. Use task status consistently on cards and in workspaces. Keep the existing damped transitions and reduced-motion support.

The mobile title is 28px. Metadata is at least 12px. Text inputs use 16px. The send control is 44px square. The checked text roles and primary button exceed 4.5:1 contrast on their intended surfaces. This calculation does not replace a complete accessibility review.

Type checking, all 51 web tests and the production build pass. Live mobile rendering, keyboard behavior, frame delivery and layout-shift measurements remain untested for this pass.
