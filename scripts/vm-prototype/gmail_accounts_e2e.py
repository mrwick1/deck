#!/usr/bin/env python3
"""Real Gmail sign-in and account-switching trial in ONE guest browser session.

Staged, because a real sign-in needs a person to look at each page:
  up           boot one owned guest with explicit internet, install the pinned browser, deliver both
               vault accounts as protected guest secrets. The guest stays running. Every failed gate stops
               the run and cleans up what it started, before any secret is delivered where possible.
  call         run one bare tool through the guest entrypoint. Output is sanitized. See "Mailbox boundary".
  controls     print counts of header controls (numbers only, no text) to find the account-control selector.
  identity     report which account (A, B, ambiguous, blocked, unknown) the open Gmail page shows.
  switch       open /mail/u/<index>/ and report whether the verified identity is the wanted account.
  resume       start the RETAINED overlay of an earlier `up` (no new overlay, no new secret delivery, no vault read),
               restore the saved main profile from its file and verify account A. Own lock, memory guard, preconditions.
  register-c   deliver ONLY account C's two secrets to the running guest (A and B stay, their receipts are checked), then record C privately.
               Then save_state and `restore`: a running session reads the secret file only when it opens.
  add-account  navigate the existing session to Google's AddSession page. Fill the form with `call` and ${GMAIL_B_USER}.
  restore      open the mailbox from the saved main profile file (an explicit state restore, after an MCP restart).
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
unknown, `call` runs only open, save_state and close.

Identity: `bare` shows a delivered secret as [secret] in its output, so a label cannot be read. The check runs
INSIDE the page instead and returns pass or fail only: a bare `expect js` step with ${GMAIL_A_USER} or
${GMAIL_B_USER}, which bare expands. The page must be https mail.google.com with EXACTLY ONE element of
--account-control-selector, and the aria-label of that element must hold a complete email token equal to the
account address (case-insensitive, no longer or shorter token). Both pass is ambiguous, neither is unknown.
The helper sends and prints placeholders and booleans only. It never reads the vault for it, never prints a
raw result or error, and never uses a title, a path or an index as proof. The source is the Google UI. It
is not an absolute guard against a page that injects its own label.

Fidelity: direct guest bare-call over the guest agent, real VM with explicit guest internet, real guest
bare-mcp (pinned candidate in a private overlay), real Google. No deck server, no host executor relay, no model.
Raw private state stays under --out (0700) and never enters Git. Keep it with `down --keep-disk` only.
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
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
SELECTOR = "private/selector.json"
ACCOUNT_C = "private/account-c.json"  # the third vault item, in its own file so the first record stays unchanged
MANIFEST_UP = "manifest-up.json"
PROFILE = "main"  # the one shared browser profile
RESTORE_URL = f"https://{MAILBOX_HOST}/mail/u/0/"
ADD_ACCOUNT_URL = "https://accounts.google.com/AddSession?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2F"
CHECK_WAIT_MS = 8000
RESUME_ATTEMPTS = 4
SECRETS_FILE = "/home/deck/.config/deck/secrets.env"
MAIN_STATE = f"{sp.STATE_DIR}/browser/{PROFILE}.state"
UP_GATES = ("base_frozen_and_verified", "guest_internet_recorded_in_vm_state", "bare_candidate_installed_and_ready",
            "both_accounts_delivered_as_protected_secrets")
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


def valid_selector(sel):
    """A short printable selector that cannot hold a ${NAME} placeholder."""
    return isinstance(sel, str) and 0 < len(sel) <= 200 and sel.isprintable() and "${" not in sel


def account_check_steps(selector, who):
    """One bare step text: pass when the page is the https mailbox with ONE control whose label holds the complete email token of account `who`.

    The address is the placeholder ${GMAIL_<who>_USER}. bare expands it in the guest. The text holds no secret value.
    """
    js = ("(() => { const els = document.querySelectorAll(" + json.dumps(selector) + "); if (location.hostname !== " + json.dumps(MAILBOX_HOST)
          + " || location.protocol !== 'https:' || els.length !== 1) return false; const label = els[0].getAttribute('aria-label') || '';"
          " return (label.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}/g) || []).some(t => t.toLowerCase() === '${GMAIL_" + who + "_USER}'.toLowerCase()); })()")
    return f'timeout {CHECK_WAIT_MS}\nexpect js "' + js.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unique_check_steps(selector, who, labels):
    """One bare step text for ALL registered accounts: pass when the page is the https mailbox with ONE control whose label holds the complete
    email token of exactly ONE registered account, and that account is `who`. Two matches (two accounts in one label, or one address
    registered twice) fail. The addresses are placeholders ${GMAIL_<x>_USER}. The text holds no secret value."""
    if not (isinstance(labels, str) and re.fullmatch(r"[A-Z]{1,9}", labels) and len(set(labels)) == len(labels) and who in labels):
        raise TrialError("the wanted account must be one of the distinct registered accounts")
    known = ", ".join(f"'${{GMAIL_{x}_USER}}'.toLowerCase()" for x in labels)
    js = ("(() => { const els = document.querySelectorAll(" + json.dumps(selector) + "); if (location.hostname !== " + json.dumps(MAILBOX_HOST)
          + " || location.protocol !== 'https:' || els.length !== 1) return false; const label = els[0].getAttribute('aria-label') || '';"
          " const toks = (label.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}/g) || []).map(t => t.toLowerCase());"
          " const hit = [" + known + "].map(k => toks.some(t => t === k)); return hit.filter(Boolean).length === 1 && hit[" + str(labels.index(who)) + "] === true; })()")
    return f'timeout {CHECK_WAIT_MS}\nexpect js "' + js.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_passed(res):
    """True only for the exact success receipt of a compact `step`. bare reports a failed expectation without an error flag."""
    err, text = res
    return not err and isinstance(text, str) and re.fullmatch(r"ok \([0-9]+ ms\)", text.strip()) is not None


def verify_identity(env, selector, labels="AB", want=None):
    """The verified account (one of `labels`, ambiguous, blocked or unknown) and the host class. Only booleans leave the guest.

    With `want`, ONE expect checks all registered accounts at once: it passes only when `want` is the single registered match, so a pass is
    immediate. A fail is `unknown` (another, ambiguous or no account). Without `want`, exactly one account may pass. Two or more are ambiguous."""
    if not valid_selector(selector):
        return {"identity": "blocked", "host_class": "unknown"}
    hc = page_class(env)
    if hc != "mailbox":
        return {"identity": "blocked" if hc == "other" else "unknown", "host_class": hc}
    if want is not None:
        ok = check_passed(env.call("step", steps=unique_check_steps(selector, want, labels), compact=True))
        return {"identity": want if ok else "unknown", "host_class": hc}
    found = [w for w in labels if check_passed(env.call("step", steps=account_check_steps(selector, w), compact=True))]
    return {"identity": "ambiguous" if len(found) > 1 else found[0] if found else "unknown", "host_class": hc}


CONTROL_COUNTS = {"all": "*", "a": "a", "aria_label": "[aria-label]", "aria_at": '[aria-label*="@"]', "label_google_account": '[aria-label*="Google Account"]',
                  "signout_href": 'a[href*="SignOutOptions"]', "header": "header", "banner": '[role="banner"]', "main": '[role="main"]',
                  "iframe": "iframe", "input": "input"}  # fixed list: the probe returns numbers, never text


def controls_js():
    parts = ", ".join(f"{k}: document.querySelectorAll({json.dumps(v)}).length" for k, v in CONTROL_COUNTS.items())
    return ("JSON.stringify({host: location.hostname, proto: location.protocol, ready: document.readyState,"
            " u_path: /^\\/mail\\/u\\/[0-9]+\\//.test(location.pathname), counts: {" + parts + "}})")


def classify_controls(raw):
    """host class and the whitelisted integer counts, on the mailbox over https only. Never any other key or text."""
    try:
        d = json.loads(raw)
        if isinstance(d, str):
            d = json.loads(d)
    except (TypeError, ValueError):
        return {"host_class": "unknown"}
    if not isinstance(d, dict):
        return {"host_class": "unknown"}
    hc = host_class(d.get("host")) if d.get("proto") == "https:" else "unknown"
    if hc != "mailbox":
        return {"host_class": hc}
    c = d.get("counts") if isinstance(d.get("counts"), dict) else {}
    ready = d.get("ready") if d.get("ready") in ("loading", "interactive", "complete") else "unknown"
    return {"host_class": hc, "ready": ready, "u_path": d.get("u_path") is True,
            "counts": {k: c[k] for k in CONTROL_COUNTS if isinstance(c.get(k), int) and not isinstance(c.get(k), bool)}}


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


def full_address(user):
    """A vault user without a domain is a Gmail user name."""
    return user if "@" in user else user + "@gmail.com"


def load_creds(items, run=subprocess.run):
    """Every account, read separately before any browser session exists. They must differ."""
    creds = {k: (full_address(c[0]), c[1]) for k, c in ((k, vault_credentials(i, run)) for k, i in sorted(items.items()))}
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
        rebind = {} if self.a.rebind_guest_id is None else {"guest_id": self.a.rebind_guest_id, "max_epoch": self.a.rebind_epoch}
        v.install_executor(self.p, sp.BOT, **rebind)
        return {"adapter_sha256": self.guest_sha("/usr/local/bin/bare-call"), "bare_mcp_sha256": self.guest_sha(sp.BARE_MCP)}

    def resume_guest(self):
        """Start the existing overlay with explicit internet. No new overlay, no executor install, no secret delivery."""
        self.register(v.start_qemu(self.p, "run", self.a.fixture_port, internet=True))
        v.Qga(self.p.qga).ping_ready(90)

    def guest_ready(self):
        v.ready(self.p, sp.BOT, 120)

    def stat_files(self):
        """(secret file row, main state file row) as metadata only: kind, mode, owner, links. Nothing is read."""
        rows = {}
        for line in self.run(["/usr/bin/stat", "-c", "%F|%a|%U|%h|%n", SECRETS_FILE, MAIN_STATE])[1].splitlines():
            kind, mode, owner, links, path = line.split("|", 4)
            rows[path] = {"kind": kind, "mode": mode, "owner": owner, "links": int(links)}
        return rows.get(SECRETS_FILE), rows.get(MAIN_STATE)

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
    if isinstance(x, v.NotReady):
        return f"not ready: {x.code}"
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
            if isinstance(x, RuntimeError) and "secrets_delivered" not in m:  # nothing was sent yet: show the reason, scrubbed
                m["error"] += ": " + sanitize(str(x), {k: c[0] for k, c in creds.items()}, [c[1] for c in creds.values()], 200)
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
        try:
            c = json.loads((Path(a.out) / ACCOUNT_C).read_text())["item"]
            if not isinstance(c, str) or not c:
                raise ValueError
            items = {**items, "C": c}
        except OSError:
            pass
        except (ValueError, KeyError, TypeError):
            raise TrialError("the account C record is invalid") from None
        sel = d.get("account_control_selector")
        try:
            sel = json.loads((Path(a.out) / SELECTOR).read_text())["selector"]  # a later `resume --account-control-selector` wins
        except (OSError, ValueError, KeyError, TypeError):
            pass
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
    r = verify_identity(env, selector, "".join(sorted(items)))
    print(json.dumps(r))
    return 0 if r["identity"] in items else 1


def cmd_controls(a):
    env = attach(a)
    err, text = env.call("eval", js=controls_js())
    print(json.dumps({"host_class": "unknown"} if err else classify_controls(text)))
    return 0


def cmd_switch(a):
    env = attach(a)
    items, selector = read_refs(a)
    if a.to not in items:
        raise TrialError("that account is not registered")
    if not valid_selector(selector):
        print(json.dumps({"switched": False, "identity": "blocked", "host_class": "unknown"}))
        return 1
    err, _ = env.call("step", steps=f"goto https://{MAILBOX_HOST}/mail/u/{a.index}/", compact=True)  # preserve the shared session; never print raw output
    r = verify_identity(env, selector, "".join(sorted(items)))
    ok = not err and r["identity"] == a.to
    print(json.dumps({"switched": ok, **r}))
    return 0 if ok else 1


def cmd_add_account(a):
    """One fixed navigation of the existing session to Google's AddSession page. The raw result, the URL and any mailbox text stay hidden."""
    env = attach(a)
    err, _ = env.call("step", steps=f"goto {ADD_ACCOUNT_URL}", compact=True)
    print(json.dumps({"navigated": not err, "host_class": page_class(env)}))
    return 1 if err else 0


def receipts_ok(qga, labels):
    """True when the guest still holds each account's delivered values (a digest compare in the guest, no replay, no value)."""
    try:
        return all(secretchan.receipt(qga, sp.BOT, f"gmail-trial-{k.lower()}-{part}").get("ok") for k in sorted(labels) for part in ("user", "pass"))
    except secretchan.SecretError:
        return False


def cmd_register_c(a):
    """Deliver ONLY account C's two secrets to the running guest, then record C. The A and B values stay: the guest script rewrites
    the file with their lines kept, and their receipts are checked before and after. Nothing replays A or B. Not recorded on any failure.
    A running browser session reads the secret file only when it opens (`restore`), so save_state first, then `restore`."""
    env = attach(a)
    items, _ = read_refs(a)
    if "C" in items:
        raise TrialError("account C is already registered")
    secret_row, state_row = env.stat_files()
    if not (file_ok(secret_row) and file_ok(state_row)):
        raise TrialError("the guest secret file or the saved profile is not private and present")
    qga = v.Qga(env.p.qga)
    if not receipts_ok(qga, items):
        raise TrialError("an earlier account receipt is not valid. Nothing was delivered")
    creds = load_creds({**items, "C": a.item_c})
    if creds["C"][0].lower() != a.address.strip().lower():
        raise TrialError("the vault item does not name the requested address")
    done = deliver_all(qga, sp.BOT, {"C": creds["C"]})
    ok = len(done) == 2 and all(done.values()) and receipts_ok(qga, {*items, "C"}) and all(file_ok(r) for r in env.stat_files())
    if not ok:
        raise TrialError("account C delivery or the receipt check failed. C is not registered")
    write_private(Path(a.out) / ACCOUNT_C, {"item": a.item_c})
    print(json.dumps({"registered": True, "delivered": done, "receipts_intact": sorted({*items, "C"})}))
    return 0


def cmd_restore(a):
    """Explicit state restore: open the mailbox from the saved main profile file. This replaces the live session. Raw output stays hidden."""
    env = attach(a)
    err, _ = env.call("open", url=RESTORE_URL, state=PROFILE)
    print(json.dumps({"restored": not err, "host_class": page_class(env)}))
    return 1 if err else 0


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


def private_ok(path, directory=False, bad=0o077):
    """True for a real (not linked) file or directory of this user with none of the `bad` mode bits."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR if directory else stat.S_ISREG)(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & bad


def running_vms(p):
    """PIDs of any QEMU of this runtime directory, owned or not."""
    pids = []
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and v.is_our_vm((d / "cmdline").read_bytes().decode(), root=p.root):
                pids.append(int(d.name))
        except OSError:
            continue
    return pids


def resume_preconditions(a, p):
    """Raise TrialError (stable text) unless the retained disk may be resumed. Starts nothing, reads no credential."""
    out = Path(a.out)
    pinned = out / "private" / sp.PINNED_NAME
    for path, directory in ((out, True), (out / "private", True), (out / OWNED, False), (out / REFS, False), (out / MANIFEST_UP, False), (pinned, False)):
        if not private_ok(path, directory):
            raise TrialError(f"not a private path of this user: {path.name}")
    try:
        up = json.loads((out / MANIFEST_UP).read_text())
        gates, base_id, cand = up["gates"], up["identity"]["base"]["id"], up["identity"]["candidate"]["sha256"]
        meta = v.read_json(p.diskmeta)
        bound = v.read_json(p.root / "executor.json")
        owned = json.loads((out / OWNED).read_text())
    except (OSError, ValueError, KeyError, TypeError):
        raise TrialError("a retained record is missing or invalid") from None
    if not all(gates.get(k) is True for k in UP_GATES):
        raise TrialError("the first run did not record every gate, including explicit guest internet")
    if a.bare_candidate:  # a newer file is installed after the boot; the record and the pinned copy follow it
        try:
            sp.check_candidate(a.bare_candidate, a.expect_bare_sha256)
        except sp.CandidateError as x:
            raise TrialError(f"the bare-mcp file is not usable: {x}") from None
    elif a.expect_bare_sha256 != cand or v.sha256_file(pinned) != cand:
        raise TrialError("the candidate digest differs from the record or the pinned copy")
    if not private_ok(p.disk, bad=0o022):
        raise TrialError("the retained disk is missing or not a plain file of this user")
    ident = e.base_identity(a.base)
    if meta.get("kind") != "overlay" or {meta.get("base", {}).get("id"), ident["id"]} != {base_id} or ident["mode"] != "0o444" or \
            Path(meta["base"].get("path", "")).resolve() != Path(a.base).resolve():
        raise TrialError("the retained overlay does not belong to the recorded frozen base")
    if not e.verify_base(a.base):
        raise TrialError("the base digest differs from its record")
    try:
        v.check_disk(p)
    except SystemExit:
        raise TrialError("the overlay does not match its base") from None
    if bound.get("bot_id") != sp.BOT:
        raise TrialError("the guest binding is not this trial's")
    pid = owned.get("pid")
    rec = e.process_record(pid) if isinstance(pid, int) else None
    if running_vms(p) or (rec and rec[2] != "Z" and str(owned.get("processes", {}).get(str(pid))) == rec[1] and v.proc_ok(p, pid)):
        raise TrialError("a VM of this trial is already running")


def follow_candidate(a, out):
    """Make the private pinned copy and the first run's record hold the file that `resume` installs. A stale pair is replaced, never edited."""
    pinned, up = out / "private" / sp.PINNED_NAME, json.loads((out / MANIFEST_UP).read_text())
    if up["identity"]["candidate"].get("sha256") == a.expect_bare_sha256 and v.sha256_file(pinned) == a.expect_bare_sha256:
        return
    new = pinned.with_name(sp.PINNED_NAME + ".new")
    new.unlink(missing_ok=True)
    cand = sp.pin_candidate(a.bare_candidate, a.expect_bare_sha256, new)
    os.replace(new, pinned)
    up["identity"]["candidate"] = cand
    write_private(out / MANIFEST_UP, up)


def file_ok(row):
    return isinstance(row, dict) and row["kind"] == "regular file" and row["mode"] == "600" and row["owner"] == sp.OWNER and row["links"] == 1


def cmd_resume(a):
    try:
        lock = e.take_lock(a.lock)
    except SystemExit as x:
        print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
        return 4
    out = Path(a.out)
    a.keep_disk = True  # the authenticated disk is never deleted, whatever fails
    try:
        env = Trial(a)
        why = env.pressure()
        if why:
            print(f"STOPPED: host pressure: {why}. Nothing was started.", file=sys.stderr)
            return 3
        try:
            resume_preconditions(a, env.p)
            if a.account_control_selector is not None:
                if not valid_selector(a.account_control_selector):
                    raise TrialError("the account control selector is not allowed")
                write_private(out / SELECTOR, {"selector": a.account_control_selector})
        except TrialError as x:
            print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
            return 2
        a.fixture_port = free_port()
        m = {"gates": {}, "host": {"start": env.host()}, "head": env.head(), "identity": "unknown"}
        running = False
        try:
            write_private(out / "private" / "owned-before-resume.json", json.loads((out / OWNED).read_text()))
            if a.bare_candidate:
                follow_candidate(a, out)
            env.resume_guest()
            running = True
            write_private(out / OWNED, {"pid": env.pid, "processes": {str(p): b for p, b in env.stack.owned.items()}})
            need(m, "guest_internet_recorded_in_vm_state", json.loads(env.p.vm.read_text()).get("internet") is True)
            env.guest_ready()
            need(m, "bound_guest_ready", True)
            got = env.guest_sha(sp.BARE_MCP)
            if a.bare_candidate and got != a.expect_bare_sha256:
                got = env.install_candidate(out / "private" / sp.PINNED_NAME, a.expect_bare_sha256)["installed_sha256"]
            need(m, "bare_candidate_hash_equals_record", got == a.expect_bare_sha256)
            secret_row, state_row = env.stat_files()
            need(m, "guest_secret_file_private_and_present", file_ok(secret_row))
            need(m, "main_state_file_private_and_present", file_ok(state_row))
            err, _ = env.call("open", url=RESTORE_URL, state=PROFILE)  # explicit restore of the saved profile; raw result hidden
            m["gates"]["profile_restored_from_file"] = not err
            selector = read_refs(a)[1]
            for i in range(0 if err else RESUME_ATTEMPTS):
                m["identity"] = verify_identity(env, selector)["identity"]
                if m["identity"] == "A":
                    break
                time.sleep(3)
            m["gates"]["account_a_identity_verified"] = m["identity"] == "A"
        except BaseException as x:  # noqa: BLE001 - own failures clean up what this run started and keep the disk
            m["error"] = error_text(x)
            print(f"FAILED: {m['error']}", file=sys.stderr)
            try:
                m["survivors"] = env.cleanup()
            except BaseException as y:  # noqa: BLE001
                m["cleanup_error"] = type(y).__name__
                print("FAILED: cleanup", file=sys.stderr)
            running = False
        m["guest_running"] = running
        write_private(out / "private" / "manifest-resume.json", m)
        print(json.dumps({"gates": m["gates"], "identity": m["identity"], "error": m.get("error"), "cleanup_error": m.get("cleanup_error"),
                          "guest_running": running, "out": str(out)}))
        return 0 if m["gates"] and all(m["gates"].values()) and not m.get("error") else 1
    finally:
        lock.close()


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
    u.add_argument("--rebind-guest-id", help="give the fresh disk this pinned guest ID (replacement computer). Needs --rebind-epoch")
    u.add_argument("--rebind-epoch", type=int, help="the host epoch of that guest ID, read with the host stopped. The guest then fences at or below it")
    u.add_argument("--fixture-port", type=int, default=0)
    u.add_argument("--keep-disk", action="store_true")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    sub.add_parser("identity")
    sub.add_parser("controls")
    s = sub.add_parser("switch")
    s.add_argument("--to", required=True, choices=("A", "B", "C"))
    s.add_argument("--index", required=True, type=int, choices=range(10), help="Gmail /mail/u/<index>/ to open. The identity check decides success")
    r = sub.add_parser("resume")
    r.add_argument("--expect-bare-sha256", required=True, help="the candidate digest that the first run recorded, or that of --bare-candidate")
    r.add_argument("--bare-candidate", help="a bare-mcp file. When the guest holds another digest, install this file after the boot (same checks as `up`). Needs --expect-bare-sha256 of the file")
    r.add_argument("--account-control-selector", help="replace the stored selector (kept in a separate private file)")
    sub.add_parser("add-account")
    rc = sub.add_parser("register-c")
    rc.add_argument("--item-c", required=True, help="vault item ID (not a secret) of account C")
    rc.add_argument("--address", required=True, help="the address of account C. The vault item must name it")
    sub.add_parser("restore")
    sub.add_parser("restart-mcp")
    d = sub.add_parser("down")
    d.add_argument("--keep-disk", action="store_true")
    a = ap.parse_args(argv)
    if a.sub in ("up", "down", "resume") and not a.base:
        ap.error(f"{a.sub} needs --base")
    if a.sub == "up" and (a.rebind_guest_id is None) != (a.rebind_epoch is None):
        ap.error("--rebind-guest-id and --rebind-epoch come together")
    if a.sub == "up" and a.rebind_guest_id is not None and (not re.fullmatch(r"[0-9a-f]{32}", a.rebind_guest_id) or a.rebind_epoch < 0):
        ap.error("--rebind-guest-id needs 32 lowercase hex digits and a non-negative --rebind-epoch")
    a.fixture_port = (a.fixture_port or free_port()) if a.sub == "up" else 0
    return a


COMMANDS = {"up": cmd_up, "call": cmd_call, "identity": cmd_identity, "controls": cmd_controls, "switch": cmd_switch, "add-account": cmd_add_account, "register-c": cmd_register_c,
            "restore": cmd_restore, "restart-mcp": cmd_restart, "down": cmd_down}


def main(argv=None):
    a = parse(argv)
    if a.sub in ("up", "resume"):
        return {"up": cmd_up, "resume": cmd_resume}[a.sub](a)
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
