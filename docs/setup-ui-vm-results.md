# Combined UI and VM attempt

Date: 2026-11-13.

The run did not pass acceptance. The real Deck UI proposed, reviewed and approved setup. The real guest reached the credential wait. The review gate passed.

The protected fill helper received HTTP 403 before releasing its value. The test route requires `Sec-Fetch-Site: same-origin`. The worker filed [bare issue 475](https://github.com/mrwick1/bare/issues/475). The exact outgoing header remains unverified. Deck did not weaken the route or use another browser.

A separate helper defect rejected the colon in real setup request identities. A regression reproduced that failure. The parent fixed validation and reproduced two passing tests. This fix does not resolve the 403.

The run did not deliver a credential, authenticate, answer a phone or CAPTCHA wait, or complete a first task. Raster and viewer privacy remain unverified. The scripted model and local service replace real external services and models. The owner authorized a load-threshold override for this run. The memory limit and exclusive VM lock stayed active.

Cleanup passed. The manifest reports zero registered survivors. The parent confirmed those processes are absent, the lock is free, and no overlay or memory-state file remains. No bare session or isolated screen remains. The frozen base stayed unchanged. Private evidence remains outside Git.
