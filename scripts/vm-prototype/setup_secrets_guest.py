#!/usr/bin/env python3
"""Fake guest harness for the protected-setup acceptance test. A fault injector, not production delivery.

Deck runs it where it runs vmproto.py: `python3 -I THIS --dir RUNTIME --fixture-port N SUB ...`.
It is self-contained because of `-I`. It keeps all state under RUNTIME and writes one journal line per
call. The journal holds argv and the stdin length only, never stdin.

Faults come from RUNTIME/fault.json: {"mode": MODE, "remaining": N}. Modes apply to deliver-secret:
  fail                no storage, no receipt, exit 1
  partial             value stored, no receipt, exit 1
  crash_after_deliver value stored, receipt written, exit 1 and no output
Without a fault, delivery stores NAME=value in RUNTIME/guest/secrets.env (mode 0600, one line per
name, replaced on overwrite) and writes a receipt: identifiers plus a salted digest that stays in
RUNTIME/guest/receipts.jsonl. A bot that differs from the executor binding is refused.

Receipt contract, as in `secretchan.py`. Every answer is {"ok": true, bot_id, request_id, label} or
exit 1 with {"ok": false, "error": CODE}. A missing receipt, or a receipt whose stored value was
overwritten since, is exit 1 "not_delivered". A replay of a request with another value is exit 1
"request_mismatch". This file does not copy the production lifecycle: no locks, no real QGA.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
MAX_VALUE = 4096


def journal(root, argv, stdin_len):
    with open(root / "journal.jsonl", "a") as f:
        f.write(json.dumps({"argv": argv, "stdin_bytes": stdin_len}) + "\n")


def fault(root):
    path = root / "fault.json"
    if not path.exists():
        return None
    spec = json.loads(path.read_text())
    if spec.get("remaining", 0) <= 0:
        return None
    spec["remaining"] -= 1
    path.write_text(json.dumps(spec))
    return spec["mode"]


def bound_bot(root):
    try:
        return json.loads((root / "executor.json").read_text())["bot_id"]
    except (OSError, ValueError, KeyError):
        return None


def public(r):
    return {k: r[k] for k in ("bot_id", "request_id", "label")}


def receipts(root):
    path = root / "guest" / "receipts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def digest(salt, value):
    return hashlib.sha256(bytes.fromhex(salt) + value.encode()).hexdigest()


def env_pairs(root):
    path = root / "guest" / "secrets.env"
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def store(root, label, value):
    """Private env store: one line per name, replaced on overwrite, mode 0600."""
    guest = root / "guest"
    guest.mkdir(exist_ok=True, mode=0o700)
    pairs = env_pairs(root)
    pairs[label] = value
    fd = os.open(guest / "secrets.env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in pairs.items()))
    os.chmod(guest / "secrets.env", 0o600)


def die(message):
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--fixture-port")
    sub = ap.add_subparsers(dest="sub", required=True)
    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("restore")
    sub.add_parser("kill")
    o = sub.add_parser("overlay")
    o.add_argument("--base", required=True)
    ie = sub.add_parser("install-executor")
    ie.add_argument("--bot", required=True)
    for name in ("deliver-secret", "secret-receipt"):
        s = sub.add_parser(name)
        s.add_argument("--bot", required=True)
        s.add_argument("--request-id", required=True)
    a = ap.parse_args()

    root = Path(a.dir)
    root.mkdir(parents=True, exist_ok=True)
    stdin = b"" if a.sub != "deliver-secret" else sys.stdin.buffer.read()
    journal(root, sys.argv[1:], len(stdin))

    if a.sub == "status":
        out = {"running": (root / "running").exists(), "state_saved": False}
        if (root / "disk").exists():
            out["disk_allocated_bytes"] = 1
            out["base"] = (root / "base").read_text()
        print(json.dumps(out))
    elif a.sub == "overlay":
        meta = json.loads(Path(a.base).with_suffix(".json").read_text())
        (root / "disk").write_text("overlay")
        (root / "base").write_text(meta["id"])
    elif a.sub in ("start", "restore"):
        (root / "running").write_text("1")
    elif a.sub == "kill":
        (root / "running").unlink(missing_ok=True)
    elif a.sub == "install-executor":
        (root / "executor.json").write_text(json.dumps({"bot_id": a.bot, "guest_id": "g" * 32}))
    elif a.sub == "secret-receipt":
        if bound_bot(root) != a.bot:
            die("bot_mismatch")
        hit = [r for r in receipts(root) if r["request_id"] == a.request_id and r["bot_id"] == a.bot]
        # a receipt counts only while the stored value still matches its digest
        if not hit or env_pairs(root).get(hit[-1]["label"]) is None or \
                digest(hit[-1]["salt"], env_pairs(root)[hit[-1]["label"]]) != hit[-1]["digest"]:
            die("not_delivered")
        print(json.dumps({"ok": True, **public(hit[-1])}))
    else:
        deliver(root, a, stdin)


def deliver(root, a, stdin):
    if bound_bot(root) != a.bot:
        die("bot_mismatch")
    mode = fault(root)
    if mode == "fail":
        die("guest_error")
    try:
        req = json.loads(stdin)
        label, value = req["label"], req["value"]
    except (ValueError, KeyError, TypeError):
        die("invalid_value")
    if not (isinstance(label, str) and NAME.fullmatch(label) and isinstance(value, str)):
        die("invalid_value")
    if not value or len(value.encode()) > MAX_VALUE or any(c in value for c in "\0\r\n"):
        die("invalid_value")
    prior = [r for r in receipts(root) if r["request_id"] == a.request_id and r["bot_id"] == a.bot]
    if prior:
        if prior[-1]["label"] != label or digest(prior[-1]["salt"], value) != prior[-1]["digest"]:
            die("request_mismatch")
        current = env_pairs(root).get(label)
        if current is None or digest(prior[-1]["salt"], current) != prior[-1]["digest"]:
            die("not_delivered")
        print(json.dumps({"ok": True, **public(prior[-1])}))
        return
    store(root, label, value)
    if mode == "partial":
        die("guest_error")
    salt = os.urandom(8).hex()
    receipt = {"bot_id": a.bot, "request_id": a.request_id, "label": label}
    with open(root / "guest" / "receipts.jsonl", "a") as f:
        f.write(json.dumps({**receipt, "salt": salt, "digest": digest(salt, value)}) + "\n")
    if mode == "crash_after_deliver":
        sys.exit(1)
    print(json.dumps({"ok": True, **receipt}))


if __name__ == "__main__":
    main()
