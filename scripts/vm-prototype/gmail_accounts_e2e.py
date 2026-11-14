#!/usr/bin/env python3
"""Real Gmail sign-in and account-switching trial in ONE guest browser session.

Staged, because a real sign-in needs a person to look at each page:
  up           boot one owned guest with explicit internet, install the pinned browser, deliver both
               vault accounts as protected guest secrets. The guest stays running. Every failed gate stops
               the run and cleans up what it started, before any secret is delivered where possible.
  call         run one bare tool through the guest entrypoint. Output is sanitized. See "Mailbox boundary".
  identity     report which account (A, B, other, ambiguous, blocked, unknown) the open Gmail page shows.
  switch       open /mail/u/<index>/ and report whether the verified identity is the wanted account.
  restart-mcp  close the browser session and restart the owned guest bare-mcp (after save_state).
  down         stop everything this trial started and verify that nothing is left.

Staged ownership: `up` holds the shared host lock (--lock) only while it boots. The guest then outlives it.
From then on the trial is owned by its --out directory. `call`, `identity`, `switch`, `restart-mcp` and
`down` each take the per-trial lock <out>/private/op.lock (non-blocking, exit 4 when busy), then prove
ownership before any guest command: the PID in <out>/private/owned.json must have the recorded birth time
(setup_e2e registry) and must be a QEMU of this trial's runtime directory (vmproto.proc_ok). A gone,
reused or foreign PID stops the command. Cleanup acts only on identities registered that way.

Secrets: the vault item IDs (not secrets) are explicit options of `up` (--item-a, --item-b) and are stored
in <out>/private/trial.json. The values come from the vault (`rbw`, bounded timeout) into this process's
memory and travel only on the stdin of the guest secret channel (secretchan). They are never in argv, files
on the host, output or reports. Failures print stable text only, never an exception message.
`sanitize` maps the two addresses to A and B and hides every other address. It is a best-effort filter,
not a proof of redaction.

Mailbox boundary: `call` probes the page host before and after the tool. It prints the tool result only
when both probes name a host that is not mail.google.com. Any mailbox host, any failed probe and any
transition gives "WITHHELD" and no page text, not even in errors. While the page is in the mailbox or
unknown, `call` runs only open, save_state and close. Identity comes only from the account-switcher
control named by --account-control-selector (no default: it needs live evidence). Only its aria-label is
read, only on mail.google.com over https. A title or a URL path is never evidence. A mismatch is blocked
or unknown, never a guess. An account index is a way to navigate, not an identity.

Fidelity: direct guest bare-call over the guest agent, real VM with explicit guest internet, real guest
bare-mcp (pinned candidate in a private overlay), real Google. No deck server, no host executor relay, no model.
Raw private state stays under --out (0700) and never enters Git. Keep it with `down --keep-disk` only.
"""
import argparse
import fcntl
import json
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import secretchan
import setup_e2e as e
import shared_profile_e2e as sp
import vmproto as v

HERE = Path(__file__).resolve().parent
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAILBOX_HOST = "mail.google.com"
OWNED = "private/owned.json"
REFS = "private/trial.json"
OP_LOCK = "private/op.lock"
OUT_LIMIT = 3000
VAULT_TIMEOUT = 15
MAX_SECRET = 512
HOST_RE = re.compile(r"[a-z0-9.-]{1,100}")
SAFE_ON_MAILBOX = {"open", "save_state", "close"}  # navigation and state only, never mailbox content or actions


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


def host_class(host):
    """mailbox, other or unknown for a hostname text. Anything that is not a plain hostname is unknown."""
    h = host.strip().strip('"') if isinstance(host, str) else ""
    if not HOST_RE.fullmatch(h):
        return "unknown"
    return "mailbox" if h == MAILBOX_HOST else "other"


def identity_js(selector):
    """The probe: host, scheme, number of matching controls, and the aria-label of the first one on the mailbox host only."""
    sel = json.dumps(selector)
    return ("JSON.stringify({host: location.hostname, proto: location.protocol, n: document.querySelectorAll(" + sel + ").length,"
            " label: location.hostname === " + json.dumps(MAILBOX_HOST) + " ? ((document.querySelector(" + sel + ") || {getAttribute: () => null})"
            ".getAttribute('aria-label') || '').slice(0, 200) : null})")


def classify(raw, accounts):
    """The verified account the identity probe shows: A, B, other, ambiguous, blocked or unknown. No path, hash or title."""
    try:
        d = json.loads(raw)
        if isinstance(d, str):
            d = json.loads(d)
    except (TypeError, ValueError):
        d = None
    if not isinstance(d, dict):
        return {"identity": "unknown", "host_class": "unknown"}
    hc = host_class(d.get("host")) if d.get("proto") == "https:" else "other" if isinstance(d.get("host"), str) else "unknown"
    if hc != "mailbox":
        return {"identity": "blocked" if hc == "other" else "unknown", "host_class": hc}
    label = d.get("label")
    if d.get("n") != 1 or not isinstance(label, str):
        return {"identity": "ambiguous" if isinstance(d.get("n"), int) and d["n"] > 1 else "unknown", "host_class": hc}
    found = {m.lower() for m in EMAIL.findall(label)}
    known = {addr.lower(): k for k, addr in accounts.items() if addr}
    if not found:
        ident = "unknown"
    elif len(found) > 1:
        ident = "ambiguous"
    else:
        ident = known.get(next(iter(found)), "other")
    return {"identity": ident, "host_class": hc}


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
    """(username, password) of one vault item, in memory only. Every failure is a stable text without the cause."""
    try:
        r = run(["rbw", "get", "--raw", item_id], capture_output=True, text=True, timeout=VAULT_TIMEOUT)
        ok = r.returncode == 0
    except Exception:  # noqa: BLE001 - the cause may hold a secret
        raise TrialError("the vault is unavailable") from None
    if not ok:
        raise TrialError("the vault is unavailable or the item is missing")
    try:
        data = json.loads(r.stdout)["data"]
        user, password = data["username"], data["password"]
    except (ValueError, KeyError, TypeError):
        raise TrialError("the vault item is incomplete") from None
    for x in (user, password):
        if not isinstance(x, str) or not 0 < len(x) <= MAX_SECRET or any(c in x for c in "\x00\r\n"):
            raise TrialError("the vault item has an invalid field")
    return user, password


def load_creds(items, run=subprocess.run):
    """Both accounts, read separately before any browser session exists. They must differ."""
    creds = {k: vault_credentials(i, run) for k, i in sorted(items.items())}
    if len({i for i in items.values()}) != len(items) or len({c[0].lower() for c in creds.values()}) != len(creds):
        raise TrialError("the two accounts are not distinct")
    return creds


# ---------- live boundary ----------

class Trial(sp.Live):
    """The shared-profile live boundary with explicit guest internet and no fixture service."""

    def boot_guest(self, probe=False):
        self.p.mk()
        v.create_overlay(self.p, self.a.base)
        self.register(v.start_qemu(self.p, "run", self.a.fixture_port, internet=True))
        v.Qga(self.p.qga).ping_ready(90)
        v.install_executor(self.p, sp.BOT)
        return {"adapter_sha256": self.guest_sha("/usr/local/bin/bare-call"), "bare_mcp_sha256": self.guest_sha(sp.BARE_MCP)}

    def call(self, tool, **arguments):
        """One tool call with its own output directory, so a reply of an earlier command is never read."""
        out = f"{sp.WORKSPACE}/e2e/{uuid.uuid4().hex}"
        self.run(["/usr/local/bin/as-deck", "mkdir", "-p", f"{sp.WORKSPACE}/e2e"])
        if self.run(["/usr/local/bin/as-deck", "mkdir", out])[0] != 0:
            return True, "the guest output directory could not be created"
        try:
            argv, reply = sp.guest_argv(out, tool, arguments)
            self.run(argv, timeout=100)
            code, text, _ = self.run(["/bin/cat", reply])
        finally:
            self.run(["/usr/local/bin/as-deck", "rm", "-rf", out])
        if code != 0:
            return True, "the guest entrypoint wrote no reply"
        try:
            r = json.loads(text)
            if not r.get("ok"):
                return True, str(r.get("error", ""))
            res = r.get("result") or {}
            return bool(res.get("isError")), (res.get("content") or [{}])[0].get("text", "")
        except (ValueError, AttributeError, TypeError, IndexError):
            return True, "the guest reply could not be read"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    path.chmod(0o600)


def op_lock(a):
    """The per-trial operation lock. Raises TrialError("busy") when another command holds it."""
    path = Path(a.out) / OP_LOCK
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise TrialError("busy: another command of this trial is running") from None
    return f


def need(m, name, ok):
    """Record a gate. A false gate stops the run at once."""
    m["gates"][name] = bool(ok)
    if not ok:
        raise TrialError(f"gate failed: {name}")


def error_text(x):
    """Stable text for a failure. Only our own errors keep their message."""
    return str(x)[:200] if isinstance(x, (TrialError, sp.CandidateError)) else type(x).__name__


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
        items = {"A": a.item_a, "B": a.item_b}
        try:
            sp.check_candidate(a.bare_candidate, a.expect_bare_sha256)
            creds = load_creds(items)  # before anything is created: no vault, no VM
        except (sp.CandidateError, TrialError) as x:
            print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
            return 2
        m = {"gates": {}, "identity": {}, "host": {}}
        try:
            out.mkdir(mode=0o700, parents=True)
            write_private(out / REFS, {"items": items, "account_control_selector": a.account_control_selector})
            pinned = sp.pin_candidate(a.bare_candidate, a.expect_bare_sha256, out / "private" / sp.PINNED_NAME)
            ident = env.base_identity()
            m["identity"] = {"head": env.head(), "base": {k: ident[k] for k in ("id", "virtual_size", "mode")}, "candidate": pinned,
                             "fidelity": __doc__.split("Fidelity: ")[1].split("\n\n")[0].replace("\n", " ")}
            m["host"]["start"] = env.host()
            need(m, "base_frozen_and_verified", ident["mode"] == "0o444" and env.base_ok())
            info = env.boot_guest()
            write_private(out / OWNED, {"pid": env.pid, "processes": {str(p): b for p, b in env.stack.owned.items()}})
            need(m, "guest_internet_recorded_in_vm_state", json.loads(env.p.vm.read_text()).get("internet") is True)
            got = env.install_candidate(out / "private" / sp.PINNED_NAME, pinned["sha256"])["installed_sha256"]
            need(m, "bare_candidate_installed_and_ready", got == pinned["sha256"])
            m["identity"].update(guest_adapter_sha256=info["adapter_sha256"], bare_mcp_sha256=got)
            m["secrets_delivered"] = deliver_all(v.Qga(env.p.qga), sp.BOT, creds)
            write_private(out / OWNED, {"pid": env.pid, "processes": {str(p): b for p, b in e.capture_processes([env.pid]).items()}})
            need(m, "both_accounts_delivered_as_protected_secrets", len(m["secrets_delivered"]) == 4 and all(m["secrets_delivered"].values()))
        except BaseException as x:  # noqa: BLE001 - every failure cleans up; only stable text is reported
            m["error"] = error_text(x)
            print(f"FAILED: {m['error']}", file=sys.stderr)
            env.a.keep_disk = False  # no authenticated state exists yet
            try:
                m["survivors"] = env.cleanup()
            except BaseException as y:  # noqa: BLE001
                m["cleanup_error"] = type(y).__name__
                print("FAILED: cleanup", file=sys.stderr)
        if out.exists():
            write_private(out / "manifest-up.json", m)
        print(json.dumps({"gates": m["gates"], "error": m.get("error"), "cleanup_error": m.get("cleanup_error"), "out": str(out)}))
        return 0 if m["gates"] and all(m["gates"].values()) and not m.get("error") else 1
    finally:
        lock.close()


def read_refs(a):
    """(vault item ids, account control selector or None) that `up` stored in the private directory."""
    try:
        d = json.loads((Path(a.out) / REFS).read_text())
        items = d["items"]
        if set(items) != {"A", "B"} or not all(isinstance(i, str) and i for i in items.values()):
            raise ValueError
        sel = d.get("account_control_selector")
        return items, sel if isinstance(sel, str) and sel else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise TrialError("the trial reference file is missing or invalid") from None


def attach(a, allow_gone=False):
    """Reattach to the guest that `up` started, after proof of ownership. No guest command runs before it.

    The recorded PID needs its recorded birth time, it must not be a zombie, and it must be a QEMU of this
    trial's runtime directory. Only identities that match are registered for cleanup.
    """
    try:
        owned = json.loads((Path(a.out) / OWNED).read_text())
        pid = owned["pid"]
        recorded = {int(p): str(b) for p, b in owned["processes"].items()}
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise TrialError("the ownership record is missing or invalid") from None
    env = Trial(a)
    rec = e.process_record(pid)
    ours = bool(rec) and rec[2] != "Z" and recorded.get(pid) == rec[1] and v.proc_ok(env.p, pid)
    if not ours and not allow_gone:
        raise TrialError("the owned guest is gone, or its PID is reused or foreign")
    env.pid = pid if ours else None
    if ours:
        for p, birth in recorded.items():
            r = e.process_record(p)
            if r and r[1] == birth:
                env.stack.owned[p] = birth
    return env


def page_class(env):
    err, text = env.call("eval", js="location.hostname")
    return "unknown" if err else host_class(text)


def withheld(why):
    print(f"WITHHELD: {why}. No page content is shown.")
    return 0


def cmd_call(a):
    env = attach(a)
    items, _ = read_refs(a)
    creds = load_creds(items)
    accounts = {k: c[0] for k, c in creds.items()}
    secrets = [c[1] for c in creds.values()]  # addresses are mapped to A and B by sanitize
    before = page_class(env)
    if before != "other" and a.tool not in SAFE_ON_MAILBOX:
        return withheld(f"{a.tool} is not allowed while the page is {before}")
    args = json.loads(a.args)
    err, text = env.call(a.tool, **args)
    after = page_class(env)
    if before != "other" or after != "other":
        return withheld(f"the page was {before} before and {after} after {a.tool}")
    print(("ERROR: " if err else "") + sanitize(text, accounts, secrets))
    return 1 if err else 0


def identity_of(env, selector, accounts):
    if not selector:
        return {"identity": "blocked", "host_class": "unknown"}
    err, text = env.call("eval", js=identity_js(selector))
    return {"identity": "unknown", "host_class": "unknown"} if err else classify(text, accounts)


def cmd_identity(a):
    env = attach(a)
    items, selector = read_refs(a)
    accounts = {k: c[0] for k, c in load_creds(items).items()}
    r = identity_of(env, selector, accounts)
    print(json.dumps(r))
    return 0 if r["identity"] in ("A", "B") else 1


def cmd_switch(a):
    env = attach(a)
    items, selector = read_refs(a)
    accounts = {k: c[0] for k, c in load_creds(items).items()}
    if not selector:
        print(json.dumps({"switched": False, "identity": "blocked", "host_class": "unknown"}))
        return 1
    env.call("open", url=f"https://{MAILBOX_HOST}/mail/u/{a.index}/")  # the raw result is never shown
    r = identity_of(env, selector, accounts)
    ok = r["identity"] == a.to
    print(json.dumps({"switched": ok, **r}))
    return 0 if ok else 1


def cmd_restart(a):
    env = attach(a)
    err, _ = env.call("close")
    before = env.mcp_pids()
    env.restart_mcp()
    after = env.mcp_pids()
    print(json.dumps({"close_error": bool(err), "new_process": len(before) == 1 and len(after) == 1 and not set(before) & set(after)}))
    return 0


def cmd_down(a):
    env = attach(a, allow_gone=True)
    env.a.keep_disk = a.keep_disk
    left = env.cleanup()
    base_ok = env.base_ok()
    write_private(Path(a.out) / "private" / "manifest-down.json", {"survivors": left, "base_unchanged": base_ok, "disk_kept": a.keep_disk, "host": env.host()})
    print(json.dumps({"survivors": left, "base_unchanged": base_ok, "disk_kept": a.keep_disk}))
    return 0 if not left and base_ok else 1


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="private evidence directory (new for `up`)")
    ap.add_argument("--base", help="frozen base .qcow2 with its sidecar; never modified")
    ap.add_argument("--lock", default=e.LOCK, help="the shared host lock file `up` holds while it boots")
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    sub = ap.add_subparsers(dest="sub", required=True)
    u = sub.add_parser("up")
    u.add_argument("--bare-candidate", required=True)
    u.add_argument("--expect-bare-sha256", required=True)
    u.add_argument("--item-a", required=True, help="vault item ID (not a secret) of account A")
    u.add_argument("--item-b", required=True, help="vault item ID (not a secret) of account B")
    u.add_argument("--account-control-selector", help="CSS selector of the Gmail account-switcher control. It needs live evidence; without it identity is blocked")
    u.add_argument("--fixture-port", type=int, default=0)
    u.add_argument("--keep-disk", action="store_true")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    sub.add_parser("identity")
    s = sub.add_parser("switch")
    s.add_argument("--to", required=True, choices=("A", "B"))
    s.add_argument("--index", required=True, type=int, choices=range(10), help="Gmail /mail/u/<index>/ to open. The identity check decides success")
    sub.add_parser("restart-mcp")
    d = sub.add_parser("down")
    d.add_argument("--keep-disk", action="store_true")
    a = ap.parse_args(argv)
    if a.sub in ("up", "down") and not a.base:
        ap.error(f"{a.sub} needs --base")
    a.fixture_port = (a.fixture_port or free_port()) if a.sub == "up" else 0
    return a


COMMANDS = {"up": cmd_up, "call": cmd_call, "identity": cmd_identity, "switch": cmd_switch, "restart-mcp": cmd_restart, "down": cmd_down}


def main(argv=None):
    a = parse(argv)
    if a.sub == "up":
        return cmd_up(a)
    try:
        lock = op_lock(a)
    except TrialError as x:
        print(f"STOPPED: {x}", file=sys.stderr)
        return 4
    try:
        return COMMANDS[a.sub](a)
    except TrialError as x:
        print(f"STOPPED: {x}", file=sys.stderr)
        return 5
    except Exception as x:  # noqa: BLE001 - the message may hold private text
        print(f"FAILED: {type(x).__name__}", file=sys.stderr)
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
