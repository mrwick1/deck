#!/usr/bin/env python3
"""Fake guest harness for the protected-setup acceptance test. A fault injector, not production delivery.

Deck runs it where it runs vmproto.py: `python3 -I THIS --dir RUNTIME --fixture-port N SUB ...`.
It is self-contained because of `-I`. It keeps all state under RUNTIME and writes one journal line per
call. The journal holds argv and the stdin length only, never stdin.

Faults come from RUNTIME/fault.json: {"mode": MODE, "remaining": N}. Modes apply to deliver-secret:
  fail                no storage, no receipt, exit 1
  partial             value stored, no receipt, exit 1
  crash_after_deliver value stored, receipt written, exit 1 and no output
Without a fault, delivery stores NAME=value in RUNTIME/guest/secrets.env (mode 0600) and writes a
receipt with identifiers only. A bot that differs from the executor binding is refused.
"""
import argparse
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


def receipts(root):
    path = root / "guest" / "receipts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


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
            die("bot is not bound to this guest")
        hit = [r for r in receipts(root) if r["request_id"] == a.request_id and r["bot_id"] == a.bot]
        if not hit:
            die("no receipt")
        print(json.dumps({"ok": True, **hit[-1]}))
    else:
        deliver(root, a, stdin)


def deliver(root, a, stdin):
    if bound_bot(root) != a.bot:
        die("bot is not bound to this guest")
    mode = fault(root)
    if mode == "fail":
        die("injected delivery failure")
    try:
        req = json.loads(stdin)
        label, value = req["label"], req["value"]
    except (ValueError, KeyError, TypeError):
        die("stdin is not {label,value}")
    if not (isinstance(label, str) and NAME.fullmatch(label) and isinstance(value, str)):
        die("invalid label or value")
    if not value or len(value.encode()) > MAX_VALUE or any(c in value for c in "\0\r\n"):
        die("value rejected")
    guest = root / "guest"
    guest.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(guest / "secrets.env", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(f"{label}={value}\n")
    if mode == "partial":
        die("injected partial delivery")
    receipt = {"bot_id": a.bot, "request_id": a.request_id, "label": label}
    with open(guest / "receipts.jsonl", "a") as f:
        f.write(json.dumps(receipt) + "\n")
    if mode == "crash_after_deliver":
        sys.exit(1)
    print(json.dumps({"ok": True, **receipt}))


if __name__ == "__main__":
    main()
