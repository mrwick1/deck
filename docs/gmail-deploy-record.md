# Gmail service deploy record

This file names the exact source of each live deploy. It holds no host path, address or account.

## 2026-11-16: integrated build

- Source commit: `2c4d8f7d8300a98bf80a291d3d35412d0c27cdd4` (merge of `news-bot-prod` `330b1d7`, `main` `dd9a8c4` and the Gmail speed-up `f8c8620`).
- Source tree: `1ed5f7479e35af2c76175934a0e12ba82078c75d`.
- Binary SHA-256: `251e06c459f7b04f6a78dd65515c3a7304875f56b5a2da600094b2589bad1f09`.
- Web bundle: `app-CBzH6Qr7.js`, `compact-DZBdAsBI.js`, built with `npm run build` before `cargo build --release`.
- Checks before deploy: `npm run check`, 265 web tests, web build, `cargo fmt --check`, `cargo clippy --all-targets`, 510 Rust tests.
- Binary before: `64674e90…` (from `news-bot-prod` `330b1d7`). Rollback copy: `rollback/pre-integrate-*` in the service directory, with the binary, the config, the wrapper script and a consistent database copy.
- Config: `deck_sha256` was the only key changed for the swap.
- Live checks: the root serves the compact bundle, `/experience.html` redirects with 308 and keeps the query, an unknown API path answers 404. The login with the owner's single sign-on and the PWA upgrade were not tested.

## 2026-11-16: one-account backlog job and the done-payment rule (writes paused)

- Source commit: `e03fe2a`-based tree with the documentation commit on top. Tree of the build: `ccd199d9987f0bf4fd676ece9217d6de0f3f16a4`. The commit that holds it is the one after `e03fe2a`.
- Binary SHA-256: `09f222b476886c340c2380501710e4cb543bc30cc813fffdb84b1bc0de1333c4`. Web bundle names are unchanged.
- Service wrapper `gmail_service.py`: the boost version of this branch. The boost is off.
- Config: `deck_sha256` set. `labels` and `cleanup` are under `paused_writes`. No label or Trash write runs. Polling and triage run.
- Binary before: `251e06c4…`, kept as a rollback copy. Earlier rollback `rollback/pre-integrate-*` is intact.
- Not tested live: the new focus order, the done-payment lowering on real mail, and any write after the pause.

## 2026-11-16: merge with main, evidence rule for done payments, focus phase tests (writes still paused)

- Source commit: the commit that holds this record is the one after `d7cc6fd`. Tree of the build: `1352b27a14558cd01ef024d23f4e0262421b3f4a`. It merges `0b07417` into `da3e40e`.
- Binary SHA-256: `b742cb8926f59cf229f19c6e98b5b055046a7e8ce62162cf3c470b0dd0c7ddaf`. Web bundle names are unchanged (`app-CBzH6Qr7.js`, `compact-DZBdAsBI.js`), built before the release build.
- Config: only `deck_sha256` changed. `labels` and `cleanup` stay under `paused_writes`. The wrapper is unchanged.
- Binary before: `09f222b4…`. Rollback copy: `rollback/pre-evidence-*` with binary, config, wrapper and a database copy.
- Live checks: the service is active, no hold, no open journal entry, no unprocessed event, and the cycles complete. With writes paused, Deck triages no backlog row, so the new receipt rule made no live decision yet.
- Not done: the exact removal of one wrong Action label. Deck labels are additive and no removal step exists.

## 2026-11-16: exact label repair, then labels and cleanup restored

- Source: merge of `aab8a67`; the repair tool is a script only. The binary is unchanged: `b742cb89…`. Tree before docs commit: `ecd5d87d693636d32ad67e937d0b7108b87f3088`.
- Rollback: `rollback/pre-labelrepair-*` with binary, config, app and a consistent database copy.
- Live: one wrong Action label removed and FYI added on account A, each write read back after a reload. The store row follows. Then `labels` and `cleanup` moved back from `paused_writes` with the same normal caps. Boost stays off.
- Not tested live: the restored writes on accounts B and C.

## 2026-11-16: merge with main (bot setup fixes)

- Source commit: `8321296` on branch `prod-main-sync`. It merges `main` `c56c94e` into `a338600`.
- It adds from main: daily schedules, step limit per bot, owner message at the step limit, chat title on rename, retire a bot, current date for the model, safe executor install. The prod line renumbers them D90 to D92.
- Two fixes for the merge: the compact setup form compares `schedule` and `max_steps`, and the install test fakes answer `exec`.
- Binary SHA-256: `d58343c34be2e72c6d2a5533b80aec4f6ece9b76b6334047a6fc4db49af0a5ba`. Web bundle built before the release build.
- Checks before deploy: `npm run check`, 270 web tests, web build, `cargo fmt --check`, `cargo clippy --all-targets`, 526 Rust tests, 125 Python tests.
- Wrapper `gmail_service.py` is unchanged. The app scripts now include the safe executor install and the label repair tool.
- Config: only `deck_sha256` changed.
- Binary before: `b742cb89…`. Rollback copy: `rollback/pre-mainsync-*` with binary, config, app and a consistent database copy.
- Live: the service is active, the root answers 200, both bot setups list. The old test bot was retired through the new control action.
- Not tested live: a scheduled bot run, and the guest bare fixes (the guest still runs the pinned bare build).

## 2026-11-16: production follows bare main (D93)

- Source commit: `a5e6b74` on branch `prod-main-sync`. The deck binary is unchanged: `d58343c3…`.
- bare: main `d6222406`. Built `bare-mcp` SHA-256: `fbe67ed3059a334463528b4730a237aa623209bf3694672fcf2df120f74eae1d`. It replaces the pinned `d9435503…`.
- Config: new key `bare_mcp` names the file in the service `bin` folder. The old `expect_bare_sha256` is ignored.
- Rollback: `rollback/pre-bare-main-*` with config, app and a consistent database copy. It also holds the old `bare-mcp` (`d9435503…`). To roll back, put that file at `bare_mcp` and restart; the service installs it.
- Live: the resume passed every gate, with `bare_candidate_hash_equals_record` true for the new hash. Deck answers 200.
- Not tested live: a bot wake that installs the file in a provisioned bot, and a full Gmail cycle after the restart.

## 2026-11-16: dynamic topic labels (D106)

- Source commit: `b76e60d` on branch `t3/dynamic-gmail-topic-labels`. It merges the prod line `ce7b6ab`. It does not include later `main` web merges.
- Binary SHA-256: `44e43f1823ef659002812bb1a34e4683beed90fda6d6d33c480e273dd49d1220`. Web bundle built before the release build.
- Schema: v14. All `gmail_organize` rows kept.
- Config: `deck_sha256`, `labels.topic_step`, `labels.topic_create_step`, `cleanup.topics_new_per_pass` 2, `cleanup.topics_registry_cap` 30. Boost stays off.
- Rollback: `rollback/pre-topics-*` with binary, config, app and a consistent database copy.
- Create UI checked live with `gmail_topic_probe.py`: the "Create new" menu item opens a "New label" dialog. Its text field has no name, so the step uses `type role=textbox`. The probe never clicked Create.
- Live after restart: service active, journal settled. One seed label (Receipts) was applied through the dynamic path on account B and marked verified, with no creation.
- Not tested live: creation of a new label, reuse of a created label, accounts A and C, restart recovery from `creating`.

## 2026-11-16: compact UI desktop fixes

- Source commit: `a250efc` on branch `prod-ui-fixes`. It merges `main` `80c4c27` into the prod line `68388e7`.
- It adds from main: the desktop layout, tab and setup widths, setup phase labels, status tones, reply line breaks, short link labels and short ids for duplicate bot names (deck #65 to #70).
- The merge changes only web files. Scripts and Rust sources are unchanged.
- Binary SHA-256: `76daf967e458cb2f8515930900c1849c1de66f5117eecc5e948b9a471fc3c470`. Web bundle built before the release build.
- Checks before deploy: 290 web tests, `cargo fmt --check`, `cargo clippy --all-targets`, 555 Rust tests. Python: `test_ratebench` has 1 failure and 1 error; both also occur on `68388e7`.
- Config: only `deck_sha256` changed. bare is unchanged (`bare-mcp` `fbe67ed3…` from bare main `d6222406`).
- Binary before: `44e43f18…`. Rollback copy: `rollback/pre-uifixes-*` with binary, config, app and a consistent database copy.
- Live: the deck process runs `76daf967…`. The site shows one brand, short ids on duplicate bot names, "Preparing the runtime", an aligned composer, and replies with line breaks and host-only links.
- Not tested live: a full Gmail cycle after the restart, the phone layout and the setup flow end to end.

## 2026-11-16: menu hint and wake error fixes

- Source commit: `da715e9` on branch `prod-error-fixes`. It merges `main` `8adcf9d` into the prod line `79d9665` (`prod-ui-fixes`).
- It adds from main: a readable bot menu hint (deck #78, PR #80) and a short task detail for a failed wake (deck #79, PR #81).
- The merge changes `web/src/experience/live.css` and `src/bot/mod.rs`. Scripts are unchanged.
- Binary SHA-256: `5aca64a3a511fda4bb522503e7b7c0d54ddbc61c9558a8a3ab5b79134b0fdf39`. Web bundle built before the release build.
- Checks before deploy: 290 web tests, `npm run check`, `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, 556 Rust tests.
- Config: only `deck_sha256` changed. bare is unchanged (`bare-mcp` `fbe67ed3…`).
- Binary before: `76daf967…`. Rollback copy: `rollback/pre-errorfixes-*` with binary, config, app and a consistent database copy (integrity check ok).
- Swap fault: the first restart ran with the new hash and the old binary. The service exited and systemd restarted it. After the binary copy, the service started. Downtime was about 8 seconds.
- Live: the deck process runs `5aca64a3…`. The served CSS has `.menu-note{color:#525258}`. `/api/setups` returns 200. All three accounts report `ok` after the restart.
- Not tested live: the bot menu and a failed wake on screen, and a full Gmail summary cycle after the restart.

## 2026-11-16: desktop Home trial at /next

- Source commit: `1e4be0d` on branch `prod-next-home`. It merges `design-system` `9b13dcf` into the prod line `164214e` (`prod-error-fixes`).
- It adds the design system and a real-data Home at `/next` (D107). The root app at `/` is unchanged. Scripts are unchanged.
- Binary SHA-256: `29303fbe9bab08508d0f119eb1552e2aa0a34c9efbf94e78b9a22db746749fd5`. Web bundle built before the release build.
- Checks before deploy: 290 web tests, `npm run check`, `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, 561 Rust tests.
- Config: only `deck_sha256` changed. bare is unchanged.
- Binary before: `5aca64a3…`. Rollback copy: `rollback/pre-nexthome-20261116T153905` with binary, config, app and a consistent database copy (integrity check ok).
- Live: the deck process runs `29303fbe…`. `/next` returns the new page and its assets. `/next` without login returns 401. `/api/setups` returns 200. The boot gates pass after the restart.
- Found before the swap: all three accounts reported `error` since about 13:21 IST. The cause is not checked.

## 2026-11-16: fluid motion kit at /next/kit

- Source: branch `prod-next-home`. It merges `fluid-kit` `8eaf1f0` into `1955f68`. The kit decision is D108 on this line.
- It adds a read-only motion and component trial at `/next/kit` (D108). `/` and `/next` are unchanged. Scripts are unchanged.
- Binary SHA-256: `5ac6e45ab97ca1a9960d6d362179c07c0efbcd395a18e3ae8389820e173b01f9`. Web bundle built before the release build.
- Checks before deploy: 290 web tests, `npm run check`, `cargo fmt --check`, 557 Rust tests on this line. Clippy ran on `fluid-kit`.
- Config: only `deck_sha256` changed. bare is unchanged.
- Binary before: `29303fbe…`. Rollback copy: `rollback/pre-kit-20261116T161333` with binary and config.
- Fault: the config rewrite left `service.json` at mode 644. The service refused to start with "service.json is not private". Mode 600 fixed it. Downtime was about 90 seconds.
- Live: `/next/kit`, `/next` and `/` return 200 with login and 401 without. `/next/kit` serves the "deck · Fluid kit" page.

## 2026-11-16: /next Home in the fluid look

- Source: branch `prod-next-home` `2464fc3`. It merges `fluid-home` `137ee1c` into `06cd9b7`.
- It moves the `/next` Home to the fluid look (D108 addendum). `/` is unchanged. Scripts are unchanged.
- Binary SHA-256: `99325afa3da7ee2b4bcc03e6116660f37ba1d975ad111d2cf646af987d67d025`. Web bundle built before the release build.
- Checks before deploy: 290 web tests, `npm run check`, 557 Rust tests on this line. `cargo fmt --check` and clippy ran on `fluid-home`.
- Config: only `deck_sha256` changed. The rewrite kept mode 600. bare is unchanged.
- Binary before: `5ac6e45a…`. Rollback copy: `rollback/pre-fluidhome-20261116T162911` with binary and config.
- Live: `/next`, `/next/kit` and `/` return 200 with login and 401 without. The served `/next` script holds the new sidebar code.

## 2026-11-16: layout prototypes at /next/layouts

- Source: branch `prod-next-home`. It merges `layouts` `eb26c35` into `4176ac9`.
- It adds a read-only page at `/next/layouts` with four layout prototypes. `/` and `/next` are unchanged. It also fixes a page scroll from hidden task-icon text.
- Binary SHA-256: `106f8b15a4ce588e5d4ff9b9a9cedc4018d701695ad9dff5ebd4618a3a14a761`.
- Checks before deploy: 290 web tests, `npm run check`, 557 Rust tests on this line. `cargo fmt --check` and clippy ran on `layouts`.
- Config: only `deck_sha256` changed, mode 600 kept.
- Binary before: `99325afa…`. Rollback copy: `rollback/pre-layouts-20261116T164044`.
- Live: `/next/layouts`, `/next` and `/` return 200 with login and 401 without.

## 2026-11-16: /next Home as a control room

- Source: branch `prod-next-home` `0065d2a`. It merges `control-room` `72c57b9`.
- It moves the `/next` Home to the Control room layout the owner picked (D108 addendum). It removes `/next/layouts`. `/` is unchanged.
- Binary SHA-256: `91979c2957f0ca5659970782e927aa401eacfdb09e27c67ac90930cf86d17885`.
- Checks before deploy: 290 web tests, `npm run check`, 557 Rust tests on this line. `cargo fmt --check` and clippy ran on `control-room`.
- Config: only `deck_sha256` changed, mode 600 kept.
- Binary before: `106f8b15…`. Rollback copy: `rollback/pre-controlroom-20261116T165054`.
- Live: `/next`, `/next/kit` and `/` return 200 with login and 401 without. `/next/layouts` now serves the root app.
