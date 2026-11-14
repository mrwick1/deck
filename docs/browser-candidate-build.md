# Browser candidate with recorded source

Date: 2026-11-14.

## Source and build

Source commit: `6e2cbb73ca749b5e567cfca87ed65fba08754307`.
Source tree: `83e2f1a19513f2b94b2e1d9fe22e8d8d84e60f43`.
Cargo lockfile SHA-256: `9f2944c6c48dbb6c420a7a6565738a7d07a3a04f30d4e9c2474b3a32368ccecc`.

The build worker exported this commit with `git archive`. It used a private source directory and target directory. It changed no source file or shared installation.

```sh
CARGO_TARGET_DIR=<private-target> nice cargo build --release --locked -p bare-mcp -j8
```

The command exited 0. The toolchain was rustc 1.96.0 (`ac68faa20`) and cargo 1.96.0 (`30a34c682`). The target was the host default, x86_64 Linux.

## Artifact

SHA-256: `d9435503f412352c5d04dcd60f0ff48b4bf496bf48b12e2b9ac2d39ce0df831b`.
Size: 19528144 bytes.
BuildID: `b000d9ef004ec8bdb9c39b27dd556ebb81313048`.

The parent independently verified the artifact digest and retained a private read-only executable copy. The worker reported runtime dependencies on libxkbcommon, libgcc_s, libm and libc.

## Contract check and limits

The worker ran bounded `initialize` and `tools/list` requests. The browser reported 20 tools. `open` exposes `state` and `file`. `save_state` and `ensure_session` expose `name` and `file`. No page or browser session opened.

This candidate has not passed the live guest acceptance run. The earlier accepted binary has a different digest. Do not transfer its result to this candidate. One recorded build establishes source provenance; it does not prove bit-for-bit reproducibility. No shared binary or frozen image was updated. Actual Gmail remains untested.
