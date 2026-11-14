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
