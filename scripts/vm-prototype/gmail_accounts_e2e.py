#!/usr/bin/env python3
"""Real Gmail sign-in and account-switching trial in ONE guest browser session.

Staged, because a real sign-in needs a person to look at each page:
  up           boot one owned guest with explicit internet, install the pinned browser, deliver both
               vault accounts as protected guest secrets. The guest stays running.
  call         run one bare tool through the guest entrypoint. Output is sanitized.
  identity     report which account (A, B, other, none) the open Gmail page shows. It prints no mailbox text.
  restart-mcp  close the browser session and restart the owned guest bare-mcp (after save_state).
  down         stop everything this trial started and verify that nothing is left.

Secrets: values come from the vault in this process's memory and travel only on the stdin of the guest
secret channel (secretchan). They are never in argv, files on the host, output or reports. Output goes
through `sanitize`, which maps the two account addresses to A and B and hides every other address.
Pages of mail.google.com are never printed by `call`. Use `identity`.

Fidelity: direct guest bare-call over the guest agent, real VM with explicit guest internet, real guest
bare-mcp (pinned candidate in a private overlay), real Google. No deck server, no host executor relay, no model.
Raw private state stays under --out (0700) and never enters Git.
"""
import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import secretchan
import setup_e2e as e
import shared_profile_e2e as sp
import vmproto as v

HERE = Path(__file__).resolve().parent
ITEMS = {"A": "05e55a6d-8d01-4ee8-a692-79cead6b162c", "B": "abc66ca4-1cad-4290-a744-a886ed79bbfc"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAILBOX_HOST = "mail.google.com"
OWNED = "private/owned.json"
OUT_LIMIT = 3000
TOOLS_WITH_PAGE_TEXT = {"read", "snapshot", "find", "eval", "step", "run_steps", "screenshot"}
IDENTITY_JS = (
    "JSON.stringify({host: location.hostname, path: location.pathname, hash: location.hash.split('/')[0].slice(0, 12),"
    " title: document.title, label: (document.querySelector('a[aria-label*=\"Google Account\"]') || {getAttribute: () => null}).getAttribute('aria-label')})"
)


class TrialError(Exception):
    """A stable failure with no secret in its text."""


# ---------- pure helpers (unit tested) ----------

def sanitize(text, accounts, secrets=(), limit=OUT_LIMIT):
    """Hide secrets, map the two account addresses to A and B, hide every other address, then cut."""
    text = text or ""
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, "[secret]")
    labels = {addr.lower(): label for label, addr in accounts.items() if addr}
    text = EMAIL.sub(lambda m: f"<{labels[m[0].lower()]}>" if m[0].lower() in labels else "<other-address>", text)
    return text if len(text) <= limit else text[:limit] + f"...[{len(text) - limit} more characters]"


def classify(raw, accounts):
    """The account an identity probe shows: A, B, other or none. `raw` is the probe's JSON text."""
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return {"identity": "none", "host": None, "view": None}
    found = {label for label, addr in accounts.items() if addr and addr.lower() in f"{d.get('label') or ''} {d.get('title') or ''}".lower()}
    seen = bool(EMAIL.search(f"{d.get('label') or ''}"))
    ident = found.pop() if len(found) == 1 else ("both" if found else ("other" if seen else "none"))
    return {"identity": ident, "host": d.get("host"), "view": (d.get("path") or "") + (d.get("hash") or "")}


def delivery_plan(creds):
    """(request id, secret name, value) for each account. Distinct names, never duplicate keys."""
    plan = []
    for key, (user, password) in sorted(creds.items()):
        plan.append((f"gmail-trial-{key.lower()}-user", f"GMAIL_{key}_USER", user))
        plan.append((f"gmail-trial-{key.lower()}-pass", f"GMAIL_{key}_PASS", password))
    return plan


def deliver_all(qga, bot, creds):
    """Deliver each value on guest stdin and confirm each receipt. Returns {name: receipt ok}. Raises on a failure."""
    done = {}
    for rid, name, value in delivery_plan(creds):
        try:
            secretchan.deliver(qga, bot, rid, name, value)
            done[name] = bool(secretchan.receipt(qga, bot, rid).get("ok"))
        except secretchan.SecretError as x:
            raise TrialError(f"delivery of {name} failed: {x.code}") from None
    return done


def vault_credentials(item_id, run=subprocess.run):
    """(username, password) of one vault item, in memory only."""
    r = run(["rbw", "get", "--raw", item_id], capture_output=True, text=True)
    if r.returncode != 0:
        raise TrialError("the vault is unavailable or the item is missing")
    try:
        data = json.loads(r.stdout)["data"]
        user, password = data["username"], data["password"]
    except (ValueError, KeyError, TypeError):
        raise TrialError("the vault item is incomplete") from None
    if not user or not password:
        raise TrialError("the vault item is incomplete")
    return user, password


def load_creds(run=subprocess.run):
    return {k: vault_credentials(i, run) for k, i in ITEMS.items()}


# ---------- live boundary ----------

class Trial(sp.Live):
    """The shared-profile live boundary with explicit guest internet and no fixture service."""

    def __init__(self, a):
        super().__init__(a)
        self.n = int(time.time())  # a stale reply from an earlier command is never read

    def boot_guest(self, probe=False):
        self.p.mk()
        v.create_overlay(self.p, self.a.base)
        self.register(v.start_qemu(self.p, "run", self.a.fixture_port, internet=True))
        v.Qga(self.p.qga).ping_ready(90)
        v.install_executor(self.p, sp.BOT)
        return {"adapter_sha256": self.guest_sha("/usr/local/bin/bare-call"), "bare_mcp_sha256": self.guest_sha(sp.BARE_MCP)}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def out_path(a, *parts):
    return Path(a.out).joinpath(*parts)


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    path.chmod(0o600)


def cmd_up(a):
    try:
        lock = e.take_lock(a.lock)
    except SystemExit as x:
        print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
        return 4
    out = Path(a.out)
    env = Trial(a)
    try:
        why = env.pressure()
        if why:
            print(f"STOPPED: host pressure: {why}. Nothing was started.", file=sys.stderr)
            return 3
        if out.exists():
            print(f"STOPPED: {out} exists. An earlier result is never overwritten.", file=sys.stderr)
            return 2
        if not e.socket_path_ok(out):
            print(f"STOPPED: --out is too long for QEMU unix sockets: {out}", file=sys.stderr)
            return 2
        try:
            sp.check_candidate(a.bare_candidate, a.expect_bare_sha256)
            creds = load_creds()  # before anything is created: no vault, no VM
        except (sp.CandidateError, TrialError) as x:
            print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
            return 2
        out.mkdir(mode=0o700, parents=True)
        (out / "private").mkdir(mode=0o700)
        pinned = sp.pin_candidate(a.bare_candidate, a.expect_bare_sha256, out / "private" / sp.PINNED_NAME)
        m = {"identity": {"head": env.head(), "base": {k: env.base_identity()[k] for k in ("id", "virtual_size", "mode")},
                          "candidate": pinned, "fidelity": __doc__.split("Fidelity: ")[1].split("\n\n")[0].replace("\n", " ")},
             "host": {"start": env.host()}, "gates": {}}
        try:
            m["gates"]["base_frozen_and_verified"] = env.base_identity()["mode"] == "0o444" and env.base_ok()
            info = env.boot_guest()
            write_private(out / OWNED, {"pid": env.pid, "processes": {str(p): b for p, b in env.stack.owned.items()}})
            vm = json.loads(env.p.vm.read_text())
            m["gates"]["guest_internet_recorded_in_vm_state"] = vm.get("internet") is True
            got = env.install_candidate(out / "private" / sp.PINNED_NAME, pinned["sha256"])["installed_sha256"]
            m["gates"]["bare_candidate_installed_and_ready"] = got == pinned["sha256"]
            m["identity"].update(guest_adapter_sha256=info["adapter_sha256"], bare_mcp_sha256=got)
            m["secrets_delivered"] = deliver_all(v.Qga(env.p.qga), sp.BOT, creds)
            write_private(out / OWNED, {"pid": env.pid, "processes": {str(p): b for p, b in e.capture_processes([env.pid]).items()}})
            m["gates"]["both_accounts_delivered_as_protected_secrets"] = len(m["secrets_delivered"]) == 4 and all(m["secrets_delivered"].values())
        except BaseException as x:
            m["error"] = f"{type(x).__name__}: {str(x)[:200]}"
            print(f"FAILED: {m['error']}", file=sys.stderr)
            env.cleanup()
        write_private(out / "manifest-up.json", m)
        print(json.dumps({"gates": m["gates"], "error": m.get("error"), "out": str(out)}))
        return 0 if m["gates"] and all(m["gates"].values()) and not m.get("error") else 1
    finally:
        lock.close()


def attach(a):
    """Reattach to the guest that `up` started. Fails closed when the owned process is gone."""
    env = Trial(a)
    owned = json.loads(out_path(a, OWNED).read_text())
    env.pid = owned["pid"]
    env.stack.owned.update({int(p): b for p, b in owned["processes"].items()})
    return env


def page_host(env):
    err, text = env.call("eval", js="location.hostname")
    return None if err else text.strip().strip('"')


def cmd_call(a):
    env = attach(a)
    creds = load_creds()
    accounts = {k: c[0] for k, c in creds.items()}
    secrets = [x for c in creds.values() for x in c]
    if a.tool in TOOLS_WITH_PAGE_TEXT and page_host(env) == MAILBOX_HOST:
        print(f"WITHHELD: {a.tool} on a {MAILBOX_HOST} page. Use the identity command.")
        return 0
    err, text = env.call(a.tool, **json.loads(a.args))
    print(("ERROR: " if err else "") + sanitize(text, accounts, secrets))
    return 1 if err else 0


def cmd_identity(a):
    env = attach(a)
    accounts = {k: c[0] for k, c in load_creds().items()}
    err, text = env.call("eval", js=IDENTITY_JS)
    print(json.dumps({"error": True} if err else classify(text, accounts)))
    return 1 if err else 0


def cmd_restart(a):
    env = attach(a)
    err, _ = env.call("close")
    before = env.mcp_pids()
    env.restart_mcp()
    after = env.mcp_pids()
    print(json.dumps({"close_error": err, "new_process": len(before) == 1 and len(after) == 1 and not set(before) & set(after)}))
    return 0


def cmd_down(a):
    env = attach(a)
    env.a.keep_disk = a.keep_disk
    left = env.cleanup()
    base_ok = env.base_ok()
    write_private(out_path(a, "private", "manifest-down.json"), {"survivors": left, "base_unchanged": base_ok, "disk_kept": a.keep_disk, "host": env.host()})
    print(json.dumps({"survivors": left, "base_unchanged": base_ok, "disk_kept": a.keep_disk}))
    return 0 if not left and base_ok else 1


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="private evidence directory (new for `up`)")
    ap.add_argument("--base", help="frozen base .qcow2 with its sidecar; never modified")
    ap.add_argument("--lock", default=e.LOCK, help="the lock file of this trial (the owner authorized a private lock)")
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    sub = ap.add_subparsers(dest="sub", required=True)
    u = sub.add_parser("up")
    u.add_argument("--bare-candidate", required=True)
    u.add_argument("--expect-bare-sha256", required=True)
    u.add_argument("--fixture-port", type=int, default=0)
    u.add_argument("--keep-disk", action="store_true")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    sub.add_parser("identity")
    sub.add_parser("restart-mcp")
    d = sub.add_parser("down")
    d.add_argument("--keep-disk", action="store_true")
    a = ap.parse_args(argv)
    if a.sub in ("up", "down") and not a.base:
        ap.error(f"{a.sub} needs --base")
    a.fixture_port = (a.fixture_port or free_port()) if a.sub == "up" else 0
    return a


def main(argv=None):
    a = parse(argv)
    return {"up": cmd_up, "call": cmd_call, "identity": cmd_identity, "restart-mcp": cmd_restart, "down": cmd_down}[a.sub](a)


if __name__ == "__main__":
    sys.exit(main())
