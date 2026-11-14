# Experience prototype

Run the existing web development command in this worktree. Open `/prototype.html`. The existing `/` entry point retains the current app.

Start on Home with no bots. Select New bot or describe a job in the main composer. Review the name, job, authority and demo spending ceiling. Choose an amount, including zero. Create the bot. Continue through the demo connection and simulated phone approval. Use the preview controls to finish work or simulate a failure. Pause and resume through the workspace control. Switch between Chat, Activity and Computer. Return Home to see the request or result.

Create another bot without replacing existing bots. Each bot owns its messages, queue, activity and results. Home and each bot keep independent drafts. Use the home composer destination to queue a task for an existing bot. In a bot chat, choose Guide this task or Add a task. A queued task can start after the current sample task finishes. Paused, failed and sign-in-wait states cannot start it.

Return Home during setup review to retain the proposal in this session. Continue setup opens that review. Navigation retains scroll offsets for Home, home chat and each workspace tab. Tabs support arrow keys, Home and End. Page navigation restores heading focus after the transition.

The preview uses sample data. Do not enter credentials. It does not send messages, connect accounts or execute work. All state resets on reload. The offline control blocks the composer without discarding its draft.

Motion uses the installed Motion dependency and honors reduced motion. Home and workspace transitions use opacity and translation. Tabs use a shared indicator. The composer stays in chat. Connection notices overlay the content without an empty strip. No token animation or forced scrolling runs.

## Open work

Validate the journey in Bare on desktop and phone widths. Check keyboard focus, readable contrast, interrupted transitions and reduced motion. Measure layout shifts and frame delivery. The authenticated implementation is at `/experience.html`. See [ui-experience-flow.md](ui-experience-flow.md).

## Verification

The journey reducer tests cover approval order and pause/failure holds. Run web type checking, tests and the production build. Browser rendering and real account behavior are separate acceptance gates.

## Visual refinement

The owner approved refinement with [Unslop](https://github.com/heydamir/unslop), created by Damir Sokolovsky. The guidance is licensed under CC BY 4.0.

Keep the approved journey and calm layout. Give bot tasks priority over introductory copy. Use neutral surfaces, three text roles and a restrained green accent. Remove decorative sparkles and the fixed progress bar. Use task status consistently on cards and in workspaces. Keep the existing damped transitions and reduced-motion support.

The compact mobile title is 24px. Metadata is at least 12px. Text inputs use 16px. The send control is 44px square. The checked text roles and primary button exceed 4.5:1 contrast on their intended surfaces. This calculation does not replace a complete accessibility review.

Type checking, all 51 web tests and the production build pass. Live mobile rendering, keyboard behavior, frame delivery and layout-shift measurements remain untested for this pass.

## Interaction flow verification

The flow reducer has six tests. They cover separate bot creation, draft isolation, queued work, pause and sign-in holds, required setup fields and instruction messages. All 57 web tests, type checking and the production build pass. These checks do not establish browser-level focus, scroll restoration, animation quality or mobile keyboard behavior. The live application and Gmail work remain separate.
