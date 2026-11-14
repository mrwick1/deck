# Installed rendering checks

Date: 2026-11-14.

Bare color PR #511 and wrapping PR #517 are merged. The installed browser uses their combined production source at bcf61e5d. Its SHA-256 is 009b1459a3f236d545623705d15a93cce3188e78108837b61ea0b9b0e05cde79. The previous executable was backed up. Existing browser processes were not stopped.

## Checks

The merged rendering branch passed 1,954 serial release tests with seven ignored. All five selected cost rows passed the existing threshold under both allocators. The Bare repository stores the design, samples and limits in its rendering results documents.

The first installed-browser run could reach the Deck API but received 404 for the UI. The worktree lacked its ignored web build output. The web build, typecheck and 49 web tests then passed. Deck was rebuilt. Before the next browser run, the UI and both generated assets returned HTTP 200. No production source changed.

A replacement launch stopped before starting any process because of host load. The owner then authorized a load-check override. Memory and single-VM checks remained.

The next real UI run used a 390 by 844 viewport. The owner review and approval passed through the real form. The private computer started and the executor installed. The enabled Approve and Deliver buttons computed a blue background of rgb(27, 78, 216) and white text. The worker inspected both captures. The parent inspected the credential-wait capture. The installed browser renders these buttons visibly. This run did not repeat the long-text fixture. Earlier isolated fixtures verified wrapping; those are separate evidence.

## Protected-input blocker

The first same-origin fetch to the test-only protected-input route returned 403. No value was released. No protected form delivery, phone round, CAPTCHA-style round, restart or private-viewer check ran. The review UI gate passed. The other three UI evidence gates remain unrun.

[Bare issue #475](https://github.com/mrwick1/bare/issues/475) tracks Fetch Metadata on page fetch. The worker added [the installed-build evidence](https://github.com/mrwick1/bare/issues/475#issuecomment-6874865119). The route checks Sec-Fetch-Site before it checks the request identity. The exact outgoing header was not captured. Missing or incorrect metadata remains an inference from the refusal. Do not remove the route check to make acceptance pass.

## Retained evidence and limits

The failed manifests, screenshots and reports remain in private local evidence directories. They are not public attachments. The final controller reports zero registered process survivors, no credential release and an unchanged frozen base. The worker stopped its browser, bridge and temporary screen. Other sessions were left alone.

The browser ran headless. A mirrored temporary screen existed, but Bare did not bind to it. No mirrored rendering or universal secrecy is claimed. No recording, real account or real credential was used. The installed protected-input and viewer journey remains incomplete until the browser blocker is fixed and a new run passes.

## Follow-up with the existing browser fixes

The approved Fetch Metadata fix in Bare PR #479 was rebased onto main at 28a8fbc2. Its 53 Fetch/XHR release tests pass. A rebuilt MCP browser sends same-origin, cors and empty metadata to a local header-gated endpoint and receives HTTP 200. The existing textarea fix in PR #500 is stacked on it. Their combined serial release library run passes 1,464 tests with six ignored. Async SHA-256, textarea body text and a later timer resolve in the MCP smoke check. Both PRs remain drafts. Resource benchmarks were not repeated and neither fix was merged.

The combined candidate was installed for validation. Its SHA-256 is 18488a21c5ede585bd60980e2681ca46e72ad6acd340d468c4b019b9435d42bf. The prior installed build was retained as a backup. Existing browser processes were not replaced.

The new real UI run passed protected fill and delivery. The returned length and hash matched; the visible-text leak flag was false. The actual Deliver click produced one HTTP 204. The guest file had mode 0600 and the exact value. The local service authenticated the special-character password. Restart during the phone wait preserved ownership and added no work. The host evidence scan found zero matches in 64 files. The process-argument scan found zero matches across 3,785 samples. The parent inspected the masked post-fill capture.

The run remains failed. The worker invoked an unsupported body innerText read over a select control. This operation is outside the textarea fix. Its probes also left pending fetches in the page. Hypothesis: those fetches contributed to the later owner click producing no request. This cause is not verified. The worker updated Bare issues #481 and #482. No phone answer POST or CAPTCHA round completed. The done state and final restart did not run.

The private latest-frame route was observed as HTTP 204 with zero bytes. The full viewer UI gate failed. The worker marked its screenshot gate as passed from the masked form and phone view; the CAPTCHA view was never reached. Treat that as partial image evidence, not a complete screenshot gate. Controller cleanup reports no survivors and an unchanged base. The worker stopped its browser, bridge and temporary screen. A new run must use the planned supported read and find tools and avoid extra page fetch probes. The earlier failed manifest stays unchanged.

## Clean follow-up and controller lifecycle

The next clean run passed 43 recorded checks. Protected delivery, service authentication, both owner rounds, the private viewer and cleanup passed. The viewer returned HTTP 204 with zero bytes while private. Supported Bare read and find checks were used without extra page fetch probes.

Acceptance still failed. The controller was launched with --wait. Its main loop stops as soon as the first task reaches done. It therefore cleaned up before the worker could record the inspected screenshot gate or test the post-completion restart. This cause is verified in the controller loop. The images include the masked form and both pending views. The parent inspected the CAPTCHA pending view. No final restart is claimed. The failed manifest remains unchanged.

The generated interactive start command now omits --wait. The controller still has a deadline, loss checks and explicit stop cleanup. A regression first failed because prepare included the early-stop flag. This is a harness invocation correction, not a change to Deck or its privacy gates. The worker reported printing the local control token once. That controller has ended and the token is inert; do not publish the transcript or token.

## Accepted interactive run

The corrected interactive controller ran from harness commit 92e23fc. The installed browser hash remained 18488a21c5ede585bd60980e2681ca46e72ad6acd340d468c4b019b9435d42bf. The real Deck UI and one real guest completed the local synthetic sign-in. The manifest is green: 46 entries pass, including acceptance_complete, which checks all 45 required gates. No gate is absent or failed. The prior failed manifests remain unchanged.

The actual UI created, reviewed and approved the setup, delivered the protected value and answered both owner requests. All four UI evidence gates passed. The owner answers remained pending until the scripted model checked the local service. Nineteen scripted tool calls ran once each. The service recorded one successful submission from one attempt. The protected guest file had mode 0600 and the exact value.

At the phone wait, the managed latest-frame route returned HTTP 204 with zero bytes. The worker inspected the visible page with supported Bare reads. The parent inspected the masked form and both pending-verification captures. These images show masked dots and pending status, with no credential visible. This is bounded visual evidence. It does not prove whole-page value absence, raw DOM protection, shell redaction or all-frame secrecy.

Restart during the wait preserved ownership and added no work. After completion, the second restart kept the same counts: 20 model requests, 19 tool calls, 44 messages and two wakes. The final restart gate passed. The worker recorded the screenshot gate before release and explicitly called finish and stop. Finish returned ok true with no pending UI gates.

The host evidence scan found zero matches in 31 files. The process-argument scan found zero matches across 1,717 samples. The frozen base was unchanged. Cleanup reported no survivors. The parent confirmed that all four registered subprocess identities and the worker bridge were absent, and that both controller listeners were closed. The worker also closed its Bare session and destroyed its temporary screen.

The model, local service owner steps, fill script and restart control were scripted. The UI, server, private VM, protected transfer and browser were real. Bare ran headless; its temporary mirror did not render the browser. No recording, real account or real credential was used. The long-text fixture was not repeated in this run. Existing rendering evidence remains separate.

Bare PRs #479 and #500 remain draft and unmerged. Their combined candidate is the installed validation build, not a released main build. The misleading run-failed alert, raw wake prose and legacy bot 404 remain separate usability findings. They did not prevent the accepted journey. No deployment or universal privacy claim follows from this result.
