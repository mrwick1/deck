# Missing backing image on the Gmail computer (2026-11-15)

Issue: [#35](https://github.com/mrwick1/deck/issues/35).

## Result

No copy of the original backing image `a90de2e085a7ad52` exists in any source checked. The overlay is intact but cannot boot. Checked sources: mounted filesystems by size and name, open handles, the user trash, and `btrfs subvolume list /data` (empty, so no snapshots). No block-device scan or undelete was tried. This record holds no private content.

## Evidence

- The overlay `disk.qcow2` exists. Its sidecar names the base path `/data/tmp/deck-vm-opt/bases/a90de2e085a7ad52.qcow2`. The directory `/data/tmp/deck-vm-opt` is absent.
- No process holds the base open. No QEMU process is alive. No `/proc/*/fd` or `/proc/*/maps` entry names it.
- No file of the recorded size (1493565440 bytes) exists on any mounted filesystem. No path named for the base id exists. The user trash holds nothing. The btrfs `/data` volume has no visible snapshot directory.
- QEMU stopped at 19:41:14. Its systemd scope reached `RuntimeMaxSec`. The limit is `LIFETIME_S`, 3 hours, in `vmproto.py`. The scope started at 16:41:13. The kernel could keep the deleted base open until this stop. After the stop, no handle to the base remained.
- The timer `reap-data-tmp` runs every 6 hours. It removes any directory directly under `/data/tmp` whose newest file is older than 3 days. The newest known base mtime is 2026-11-12 14:28. The run at 10:53 on 2026-11-15 was inside the 3 days. The run at 16:52 was outside them.

## Cause

Established: the base lived under `/data/tmp`. A tool that deletes idle directories there runs on this machine. The base directory is gone.

Hypothesis: the 16:52 run of `reap-data-tmp` removed the directory. No log line records the removal, because `rm -rf` is silent. The timing and the rule match. Nothing else found removes it.

Unknown: whether other files in the directory had newer mtimes.

## Not done

- No rebuild, rebase, `qemu-img` write, or substitute base. Any of them would present a different disk as the original.
- No restart of the service. It stays up and reports `wake_failed` each cycle.

## Fix in this change

`vmproto.py status` now exits 1 with one line when the base is missing. The line names the base id, says the overlay and saved state are kept, and says not to substitute another base. No traceback appears.

## Proposed durable fix

1. Keep frozen bases under `~/.local/share/deck-gmail/bases/`. Keep each overlay with it. Leave `/data/tmp` for disposable files.
2. Move with the service stopped: copy the base, check its digest against the sidecar, then rewrite the overlay backing path. Use a supported rebase with a digest check. Do it once, not ad hoc.
3. Make `reap-data-tmp` skip any directory that holds a `*.qcow2` base.
4. Raise or remove the 3-hour `RuntimeMaxSec`, or make the service restart the VM from saved state before it expires.
5. Back up the base and its sidecar off this volume.

For the current overlay, a new base and a new Gmail login are needed. The owner must approve this.

## Preserved on 2026-11-15

A private copy of everything left under `/data/tmp` sits in `~/.local/share/deck-gmail/recovery/<stamp>-missing-base-35`. Directories are mode 0700. A `MANIFEST.sha256` lists every file. Each copied file matched its source by SHA-256. The overlay keeps its sparse layout. The host database came from the SQLite backup API and passed `integrity_check`. The copy holds:

- the overlay `disk.qcow2` with its sidecar, executor record, run logs and manifests;
- the host database, bot artifacts, probe watermarks and the acceptance manifests;
- the browser candidate (SHA-256 `d9435503…0df831b`, equal to the recorded hash);
- the service config, its poll-only variant, and the service state directory.

The original sources stay in place. The deployed `vmproto.py` before the fix is in the same directory.

Saved browser state: the checked host locations hold no export. The acceptance manifests record that the profile was restored from a file inside the guest. That file lives in the overlay's own clusters. The overlay cannot be read without its base.

Base provenance: the base id is the first 16 hex digits of the base's SHA-256. The full digest was in the deleted sidecar. No manifest left holds it. A byte-identical copy would prove itself by that prefix. A rebuild from the public cloud image would not match, because provisioning is not deterministic.

## Lifetime fix

`DECK_VM_LIFETIME_S=infinity` removes the 3 hour `RuntimeMaxSec`. It is the only accepted value. Unset keeps 3 hours. A bad value stops the start. `gmail_service.py` sets it for the Deck process and the helper when the config holds `"persistent_vm": true`. Deck passes its environment to `vmproto.py` unchanged. The memory cap stays.

The deployed helper now holds the status fix. The running service was not restarted. It reports `computer: failed`, not paused and not running.

## Recovery plan

1. Search once more for a byte-identical base: any copy whose SHA-256 starts with `a90de2e085a7ad52`. If one exists, copy it to `~/.local/share/deck-gmail/bases/`, make it read-only, rewrite the overlay backing path with `qemu-img rebase -u` on a copy of the recovered overlay, and boot that copy. Keep the original overlay untouched.
2. If no copy exists, the guest's browser sessions and the saved VM state are lost. The host database, its events and its watermarks stay. This is the data-loss tradeoff. The owner must accept it.
3. For the new computer: build a new base under `~/.local/share/deck-gmail/bases/`, create a new overlay under a new runtime directory in `~/.local/share/deck-gmail/`, set `persistent_vm`, and keep the old overlay as evidence. Never present the new base as the old one.
4. The three Gmail logins need the owner's protected challenge. Chat never carries passwords.
5. Do not attach the new executor to the existing bot until a reviewed plan covers the guest identity record, the database epoch and the fencing of the old guest id. The plan must keep the same host database.
6. Leave `reap-data-tmp` unchanged. Production state no longer lives under `/data/tmp`.

## Follow-up

The computer was rebuilt on a new base. See [gmail-rebuild-results.md](gmail-rebuild-results.md).
