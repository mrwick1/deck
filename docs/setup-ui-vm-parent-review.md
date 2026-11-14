# Parent review of the combined UI and VM evidence

Date: 2026-11-14.
Verdict: accepted by manual evidence review for the scoped synthetic setup flow.
This is not an automatically green run.

## Original result remains unchanged

The final run's manifest records 44 passes and two failures: ui_privacy_viewer and its aggregate acceptance_complete. The worker failed the viewer check because internal request identifiers appeared in chat. The parent did not edit that manifest or the worker's report. A separate private review record pins the original manifest hash and the inspected image hashes.

The parent checked all 45 required conditions. The other 44 conditions already pass in the manifest. The one reviewed privacy condition passes on the evidence below. This verdict does not report a new run or change the original exit code. The raw finish response lists ui_privacy_viewer in pending_ui_gates. The worker report incorrectly describes that list as empty; the parent checked the saved JSON response.

## Privacy evidence

- The recorded candidate browser request during the private phone wait returned HTTP 204 with zero frame bytes. The parent read the actual tool output, not a paraphrase.
- The fill result has matching length and hash comparisons. Its visible-text leak flag is false. It returns no credential value.
- The parent inspected the masked-field PNG. It shows dots.
- The parent inspected the private viewer and both pending-verification views. The viewer is blank. Those views show no credential value.
- The parent inspected one native 1280 by 800 frame after verification. It shows an empty editor buffer. This proves only that inspected frame.

Owner-facing challenge instructions intentionally display their match number. The implementation in web/src/lib/wait.ts appends that number to the owner prompt. Request identifiers are metadata, not credential values. Their appearance is not a failure of the password privacy criterion. Raw wake instructions in chat remain a separate usability finding.

## Completed behavior

The real Deck UI proposed, edited, reviewed and approved setup. The real VM received the protected value. The local service authenticated its exact special-character value. Both owner-help answers stayed pending until service evidence was checked. The task submitted once. Nineteen scripted tool calls each ran once. Restarts during the wait and after completion added no work.

The final QGA check ran on a live socket. It verified the guest file exists, mode 0600, the expected name and exact value. Host and argument scans found no value. Cleanup has no registered survivor. The parent confirmed the released VM lock and removal of disk and memory-state files.

## Scope and settings

The browser uses the isolated candidate combining draft bare PRs 479 and 500. Its source is 9cf10333. The shared installation is unchanged. The server, UI, Store, guest executor and one private VM are real. The model and login service are scripted. The fill script sets the actual protected input node; the owner buttons and protected delivery use real UI clicks.

The owner authorized a load-limit override. The interactive test uses a 600-second idle grace period. Production settings are unchanged. The separate saved-wake run covers the short-idle lifecycle and a second task.

## Remaining limits

- The original automated manifest remains failed. This document is the manual review, not a replacement manifest.
- Bare issues 464 and 467 still affect colors and wrapping. The primary button can be visually missing even when its click works.
- Native pixels were inspected for one frame only. Universal raw shell, DOM and evaluation redaction is not claimed.
- Internal wake prose, a transient failed-run alert and transient 502 responses need separate UI and proxy investigation.
- Select innerText remains unsupported. Recovery from other Unsupported async operations is not established.
- Real accounts, real-service CAPTCHA, paid models, phone layout and performance are outside this result.
- No merge, deployment or shared browser replacement ran.
