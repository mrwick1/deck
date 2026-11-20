# Compact experience flow

Use one header per screen. Keep the back action, title and current status together. Show connection failures in an overlay status message. Do not reserve an empty status strip. Keep touch targets at least 44px.

## Home

Show requests first when requests exist. List bots as compact rows with a name, current task and status. Show recent completed tasks below. Open a result in its bot conversation. Keep setup drafts available. A short composer can create a bot or open the main chat. Do not show sample results in the authenticated app.

## Main chat and setup

The main chat is a normal Deck conversation. Creating a bot uses the setup API. Describe the job, then open its saved proposal. Review instructions, authority and the configured spending budget before approval. Keep model, resources and credential labels in advanced settings. Keep secrets out of the proposal and ordinary chat.

Setup review has its own form actions. Hide the chat composer there. Save the draft without approval. On approval, show provisioning progress and the server's requests. An owner answer remains pending until the service verifies it. Preserve stale-version errors and refresh the record before another attempt.

## Bot workspace

Use one header with back, bot name, status and a controls menu. Tabs select Chat, Activity, Requests and Computer. Hide the composer outside Chat. Keep a separate draft for each conversation. Preserve scroll position while the owner changes views. Do not force-scroll streamed replies while the owner reads earlier messages.

Chat sends either guidance or a queued task to the selected bot. Show the destination and mode before sending. Keep failed input available. Do not automatically resend an uncertain request. Use the existing streaming events, file links and approval records.

Activity shows task state and queued work. Offer only supported task actions. Pause stops new dispatch; it is not cancellation of current work. Stop reports unconfirmed commands. Unknown outcomes remain a separate owner decision.

Requests show protected input, phone or CAPTCHA instructions, spending requests and unresolved outcomes. Bind every response to its bot, request and version. Never treat owner absence as approval.

Computer subscribes only while visible and awake. Hide it during sign-in waits. Discard a cleared or refused frame. Unsubscribe and release images when the owner leaves. A saved computer offers Wake; an awake one offers Sleep.

## Recovery and boundaries

Keep the last successful overview during connection loss and mark it stale. Disable dependent actions until a fresh read succeeds. Keep chat drafts in memory. A refresh reloads saved setup, history and task state from the backend.

The public sample preview remains static. The real interface is the root app at `/` (D94), behind the existing Deck authentication middleware. `/experience.html` redirects to `/`. No new public API, auth bypass, model endpoint or credential storage is added.

## Design reference

Use the owner-approved calm layout and restrained motion. Apply the craft guidance from [Unslop](https://github.com/heydamir/unslop), by Damir Sokolovsky, licensed CC BY 4.0. Keep neutral surfaces, readable text and status as the repeated visual detail. Animate opacity and short translations. Honor reduced motion.

## Run and verify

Build with `npm --prefix web run build`. Build the Deck binary after the web assets. Open `/` through the existing authenticated server. During local development, use the existing Vite proxy. Do not publish that proxy.

Type checking, 64 web tests and the production build pass. Rust formatting and Clippy checks pass. Seven new tests cover setup approval revisions, concurrent edits, failed writes, currency precision, ready destinations and HTTP authentication failures. The build retains a shared chat bundle above the Vite size warning threshold.

Live API acceptance, phone keyboard behavior, visual layout, focus restoration and animation frame timing remain untested in this pass. The existing server and guest computers were not restarted. The public preview contains sample data only.

## First-time Home components

The sample and authenticated entries share `HomeStart`. It uses the existing shadcn Button and Textarea and a Radix dropdown menu. Suggestions fill an editable draft. Submission opens setup review. The empty screen omits duplicate creation actions, empty requests and empty results. A compact sample marker remains on the public preview.

The full mobile redesign and its two requested review rounds are in progress. The first-time Home implementation passes type checking, 64 web tests and the web build. It has not yet passed the new screen review gate.
