# Per-bot folder fix

## Problem

The live setup run reached Ready. Later executor work then failed with ENOENT.
`bot_config` sets `data_dir` to `<data>/bots/<id>`. No code created that folder.
`executor::client::private_dir` creates folders below the root. It does not create the root.

## Change

- After a setup reaches Ready, `Service::prepare_folders` creates the bot data folder and the workspace.
  It runs before the runtime starts.
- It uses `private_dir` under the configured data root. Links and foreign folders stop the setup.
- `private_dir` creates each new folder with mode 0700.
- `bot_config` does not create folders. Protected endpoints also read it.
- A draft or an unapproved setup creates no folder.

## Evidence

- The held-bot scan test failed before the change with `NotFound` on the bot folder.
  It passes after the change. It checks mode 0700 and the workspace.
- The linked-folder test failed before the change because a runtime started behind a symlink.
  It passes after the change. The link target stays empty.
- The unapproved-setup test passes before and after the change.
- Full `cargo test --bin deck` and `cargo test --tests` pass. `cargo clippy --all-targets -D warnings` passes.

## Not covered

- A foreign-owner folder test needs another UID. This shell cannot create one without sudo.
- No live VM run was done for this change.

## Artifact shape

Artifacts land at `<data>/bots/<id>/bots/<id>/artifacts/<command>`.
`fetch_artifacts` and `Hub::artifacts_root` both add `bots/<id>` below the bot data folder.
The path works. It is not changed here.
