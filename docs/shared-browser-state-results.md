# Shared browser state verification

Date: 2026-11-14.

## Change

The guest adapter now supplies a private state file for save and restore. One browser session can retain the site's shared multi-account login state. The change does not create a browser per account.

## Parent verification

The original adapter probes failed on both missing state paths. After integration, all 15 multi-account tests pass with `DECK_LIVE_BARE=1`. Four use the real bare MCP binary and a local synthetic service. All 15 desktop readiness tests also pass.

The live fixture holds two signed-in identities in one cookie record. The test switches between them in the one `main` browser session. It saves through the production adapter, restarts MCP, restores through the adapter and switches between both identities again. A second test restores with `ensure_session` and requires its `restored` result. The fixture's logout behavior is explicit and is not evidence of Google's logout behavior.

The parent found a test cleanup gap when MCP initialization fails. A new regression failed before the cleanup fix and passes after it. Test cleanup stops only the owned browser process group and removes temporary state.

Tested bare binary SHA-256: `009b1459a3f236d545623705d15a93cce3188e78108837b61ea0b9b0e05cde79`.

## Commands

```sh
python3 -m unittest discover -s scripts/vm-prototype -p 'test_multi_account.py' -v
DECK_LIVE_BARE=1 python3 -m unittest discover -s scripts/vm-prototype -p 'test_multi_account.py' -v
python3 -m unittest discover -s scripts/vm-prototype -p 'test_desktop_readiness.py' -v
git diff --check
```

The default multi-account run skips the four live tests. The explicit live run skips none.

## Later real-guest acceptance

The pinned-candidate run passes all 15 required gates through the actual guest adapter. The parent checked the retained manifest, candidate digest and cleanup, and reproduced all 45 harness tests. See [the VM results](shared-profile-vm-results.md). The standard frozen image failed restore because its older browser rejects `open.file`. The accepted run changes only its private overlay. Production needs a compatible guest browser.

## Limits of the original host checks

These checks do not cover real Gmail, the full host relay, the guest executor, VM recovery, account identity enforcement or credential reload. The fixture uses navigation routes to select an account; it does not reproduce Gmail's account-switcher UI. Concurrent path replacement by a guest shell, ACLs and resource costs remain untested. This change is not a complete multi-account Gmail implementation. No deployment or shared browser update ran.
