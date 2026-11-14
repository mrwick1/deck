#!/usr/bin/env python3
"""Shared-profile acceptance in one real guest: ONE browser session, two synthetic accounts.

The guest runs the installed candidate `bare-call` (the guest adapter) as the desktop user. Through that
entrypoint the harness signs in A and B together, alternates between them, saves the shared state, restarts
the owned guest MCP process, restores from the state file only, switches both accounts again, then saves
the whole VM and wakes it. Browser restart and VM wake are separate gates. The identity service is the
synthetic multi-account fixture of test_multi_account.py. It is not Google.

Fidelity: direct guest bare-call over the guest agent, real VM, real guest bare-mcp, no deck server, no
host executor relay, no model. The production server and relay route is not covered.

It reuses setup_e2e (lock, pressure, Manifest, Stack process registry and cleanup) and vmproto (overlay,
start, install_executor, ready, save and restore). Raw evidence stays in --out, outside Git.
The manifest holds identifiers, hashes, counts and statuses. Every gate is required: a gate that did not
run is a failure, never a pass. A run that stops early still cleans up what it started.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import setup_e2e as e
import test_multi_account as ma
import vmproto as v
from http.server import ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
PASS, FAIL = e.PASS, e.FAIL
BOT = "shared-profile"
OWNER = "deck"
GUEST_HOME = "/home/deck"
WORKSPACE = GUEST_HOME + "/workspace"
BARE_CALL = "/usr/local/bin/bare-call"
BARE_MCP = "/usr/local/bin/bare-mcp"
BARE_STAGE = "/usr/local/bin/.bare-mcp.pinned.new"
STATE_NAME = "shared"
STATE_DIR = GUEST_HOME + "/.local/state/deck"
STATE_FILE = STATE_DIR + f"/browser/{STATE_NAME}.state"
MEM_FILE = "/dev/shm/shared-profile-marker"
FIDELITY = ("direct guest bare-call over the guest agent, real VM, real guest bare-mcp, "
            "no deck server, no host executor relay, no model, synthetic multi-account fixture (not Google)")
PINNED_NAME = "bare-mcp.pinned"
OVERRIDE = ("; guest bare-mcp REPLACED in this private overlay by the pinned candidate (explicit override, "
            "standard image and frozen base unchanged)")
EXPECTED_EFFECTS = {"records": 1, "logins": {"alice": 1, "bob": 1}}


# ---------- pure helpers (unit tested) ----------

def required_gates(a):
    gates = ["base_frozen_and_verified", "guest_adapter_hash_equals_candidate"]
    if getattr(a, "bare_candidate", None):
        gates.append("bare_candidate_installed_and_ready")
    if a.expect_bare_sha256:
        gates.append("bare_mcp_sha256_equals_expected")
    return gates + ["both_accounts_signed_in_together", "alternating_identity_before_save", "one_bare_main_session_one_cookie_record",
                    "state_private_dir_and_file_0600", "state_saved_with_cookie_record", "mcp_restart_new_process",
                    "restore_from_disk_and_switch_both", "vm_wake_same_boot_and_profile", "no_duplicate_fixture_effects",
                    "base_unchanged", "no_owned_process_left"]


class CandidateError(Exception):
    pass


def check_candidate(src, expected):
    """Raise CandidateError unless src is a readable executable regular file with the expected SHA-256. Returns its size."""
    src = Path(src)
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        raise CandidateError("the expected digest is not 64 lowercase hex characters")
    try:
        if not src.is_file() or not os.access(src, os.R_OK | os.X_OK):
            raise CandidateError("the candidate is not a readable executable regular file")
        size, digest = src.stat().st_size, v.sha256_file(src)
    except OSError as x:
        raise CandidateError(f"the candidate cannot be read: {x.strerror}")
    if digest != expected:
        raise CandidateError("the candidate digest differs from the expected digest")
    return size


def pin_candidate(src, expected, dest):
    """Validate the candidate browser and copy it to a private read-only artifact. Returns its identity.

    The source is only read. The copy must match the expected digest, so a concurrent replacement of the
    shared binary cannot change what the test installs.
    """
    dest = Path(dest)
    size = check_candidate(src, expected)
    try:
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copy = v.sha256_file(dest)
    except OSError as x:
        dest.unlink(missing_ok=True)
        raise CandidateError(f"the candidate cannot be copied: {x.strerror}")
    if copy != expected:
        dest.unlink(missing_ok=True)
        raise CandidateError("the private copy differs from the expected digest (the source changed or the copy failed)")
    dest.chmod(0o500)
    return {"sha256": copy, "size": size, "override": True}


def parse_me(text):
    """(selected user, [accounts]) from the fixture's /me text, else None (anonymous or unreadable)."""
    m = re.search(r"user=(\S+) accounts=(\S*)", text or "")
    return (m[1], m[2].split(",") if m[2] else []) if m else None


def state_problem(rows):
    """None when the state directories are private and the state file is one 0600 regular file of the desktop user."""
    by = {r["path"]: r for r in rows}
    for name in ("deck", "browser"):
        r = by.get(name)
        if not r:
            return f"{name}: missing"
        if r["kind"] != "directory" or r["mode"] != "700" or r["owner"] != OWNER:
            return f"{name}: {r['kind']} mode {r['mode']} owner {r['owner']}"
    f = by.get(f"{STATE_NAME}.state")
    if not f:
        return "state file: missing"
    if f["kind"] != "regular file" or f["mode"] != "600" or f["owner"] != OWNER or int(f["links"]) != 1:
        return f"state file: {f['kind']} mode {f['mode']} owner {f['owner']} links {f['links']}"
    return None


def wake_problems(before, after, running_after_save):
    """Why the guest after the save is not the same running guest. Empty when it is."""
    out = []
    if running_after_save:
        out.append("a VM process was still running after the save")
    if after["boot_id"] != before["boot_id"]:
        out.append("boot id changed: cold boot, not a wake")
    if not after["uptime"] > before["uptime"]:
        out.append("uptime did not advance")
    if after["mcp"] != before["mcp"]:
        out.append("bare-mcp process changed")
    if after["marker"] != before["marker"]:
        out.append("memory-only marker lost")
    if after["state_sha"] != before["state_sha"]:
        out.append("state file changed")
    return out


def guest_argv(out, tool, arguments, timeout=60):
    """The command that runs one tool through the installed guest entrypoint, as the desktop user. Returns (argv, reply path)."""
    req = {"method": "tools/call", "params": {"name": tool, "arguments": arguments}, "timeout": timeout}
    return (["/usr/local/bin/as-deck", "env", f"DECK_OUT={out}", f"DECK_WORKSPACE={WORKSPACE}", BARE_CALL, json.dumps(req)], f"{out}/reply.json")


# ---------- fixture ----------

class Fixture:
    """The multi-account service of test_multi_account.py, on a chosen loopback port, with a count of each request."""

    def __init__(self, port=0):
        self.paths = []
        outer = self

        def server(_addr, handler):
            class Counting(handler):
                def do_GET(self):
                    outer.paths.append(self.path)
                    super().do_GET()

            return ThreadingHTTPServer(("127.0.0.1", port), Counting)

        saved, ma.ThreadingHTTPServer = ma.ThreadingHTTPServer, server
        try:
            self.svc = ma.Service()
        finally:
            ma.ThreadingHTTPServer = saved

    def effects(self):
        logins = Counter(p.rsplit("/", 1)[1] for p in list(self.paths) if p.startswith("/login/"))
        return {"records": len(self.svc.records), "logins": dict(logins)}

    def stop(self):
        self.svc.stop()


# ---------- live boundary ----------

class Live:
    """The real boundary: the host, the VM, the guest agent and the fixture. Nothing here runs at construction."""

    def __init__(self, a):
        self.a = a
        self.stack = e.Stack(SimpleNamespace(out=a.out, server_port=0))
        self.p = v.Paths(self.stack.rt / BOT)
        self.fixture = None
        self.fixture_url = f"http://{v.FIXTURE_ADDR}"
        self.pid = None
        self.n = 0

    # host
    def lock(self):
        return e.take_lock(self.a.lock)

    def pressure(self):
        return e.pressure(Path("/proc/meminfo").read_text(), Path("/proc/loadavg").read_text(), self.a.min_avail_mib, self.a.max_load)

    def host(self):
        avail = re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text())
        return {"loadavg": Path("/proc/loadavg").read_text().split()[:3], "mem_available_mib": int(avail[1]) // 1024, "max_load_allowed": self.a.max_load}

    def base_identity(self):
        return e.base_identity(self.a.base)

    def base_ok(self):
        return e.verify_base(self.a.base)

    def head(self):
        return subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def candidate_sha256(self):
        return v.sha256_file(HERE / "guest" / "bare-call")

    def start_fixture(self):
        self.fixture = Fixture(self.a.fixture_port)
        self.stack.service = self.fixture

    def effects(self):
        return self.fixture.effects()

    # guest lifecycle
    def sh(self, script, timeout=30):
        return v.Qga(self.p.qga).sh(script, timeout=timeout)

    def run(self, argv, timeout=30):
        return v.Qga(self.p.qga).exec(argv, timeout=timeout)

    def push(self, path, data):
        return v.Qga(self.p.qga).push(path, data)

    def register(self, pid):
        self.pid = pid
        self.stack.owned.update(e.capture_processes([pid]))

    def boot_guest(self, probe=True):
        """Boot the guest and install the executor. `probe` waits for the guest browser to be ready.

        A run with a pinned candidate passes probe=False. The frozen base browser may fail the readiness gate
        by design. The candidate must pass that same gate in install_candidate before any scenario tool runs.
        """
        self.p.mk()
        v.create_overlay(self.p, self.a.base)
        self.register(v.start_qemu(self.p, "run", self.a.fixture_port))
        v.Qga(self.p.qga).ping_ready(90)
        v.install_executor(self.p, BOT)  # pushes the candidate bare-call into the private overlay only
        if probe:
            v.ready(self.p, BOT, 120)
        return {"adapter_sha256": self.guest_sha("/usr/local/bin/bare-call"), "bare_mcp_sha256": self.guest_sha("/usr/local/bin/bare-mcp")}

    def guest_sha(self, path):
        code, out, _ = self.run(["/usr/bin/sha256sum", path])
        return out.split()[0] if code == 0 and out.split() else ""

    # tools through the entrypoint
    def call(self, tool, **arguments):
        self.n += 1
        out = f"{WORKSPACE}/e2e/{self.n}"
        self.run(["/usr/local/bin/as-deck", "mkdir", "-p", out])
        argv, reply = guest_argv(out, tool, arguments)
        self.run(argv, timeout=100)
        code, text, _ = self.run(["/bin/cat", reply])
        if code != 0:
            return True, "the guest entrypoint wrote no reply"
        r = json.loads(text)
        if not r.get("ok"):
            return True, str(r.get("error", ""))
        res = r.get("result") or {}
        return bool(res.get("isError")), (res.get("content") or [{}])[0].get("text", "")

    def state_stat(self):
        code, out, _ = self.run(["/usr/bin/stat", "-c", "%F|%a|%U|%h|%n", STATE_DIR, STATE_DIR + "/browser", STATE_FILE])
        rows = []
        for line in out.splitlines():
            kind, mode, owner, links, path = line.split("|", 4)
            rows.append({"path": path.rsplit("/", 1)[1], "kind": kind, "mode": mode, "owner": owner, "links": int(links)})
        return rows

    def state_has_token(self):
        tokens = list(self.fixture.svc.records)
        return bool(tokens) and all(self.run(["/bin/grep", "-c", "-F", t, STATE_FILE])[0] == 0 for t in tokens)

    def state_sha256(self):
        return self.guest_sha(STATE_FILE)

    def mcp_pids(self):
        code, out, _ = self.run(["/usr/bin/pgrep", "-u", OWNER, "-x", "bare-mcp"])
        return sorted(int(x) for x in out.split()) if code == 0 else []

    def stop_mcp(self):
        """Kill the one bare-mcp of this run's guest and its direct children. It must be the installed executable of the desktop user."""
        old = self.mcp_pids()
        if len(old) != 1:
            raise RuntimeError(f"expected one bare-mcp before the stop, found {len(old)}")
        exe = self.run(["/usr/bin/readlink", "-f", f"/proc/{old[0]}/exe"])[1].strip()
        if exe != BARE_MCP:
            raise RuntimeError(f"the bare-mcp process runs {exe or 'an unknown executable'}, not the installed one")
        self.sh(f"kids=$(pgrep -P {old[0]}); kill -KILL $kids {old[0]}")
        end = time.monotonic() + 15
        while self.mcp_pids():
            if time.monotonic() > end:
                raise TimeoutError("bare-mcp did not exit")
            time.sleep(0.2)

    def start_mcp(self):
        self.run(["/usr/local/bin/as-deck", "setsid", "-f", "/usr/local/bin/bare-session"])
        v.ready(self.p, BOT, 60)

    def restart_mcp(self):
        self.stop_mcp()
        self.start_mcp()

    def install_candidate(self, pinned, sha256):
        """Replace the guest bare-mcp with the pinned candidate, in this owned guest only.

        Stop the old process, stage the file under another name, check its digest and libraries, rename it over
        the installed path, check the installed digest, start the session script and wait for readiness.
        """
        self.stop_mcp()
        self.push(BARE_STAGE, Path(pinned).read_bytes())
        try:
            if self.guest_sha(BARE_STAGE) != sha256:
                raise RuntimeError("the staged candidate digest differs in the guest")
            code, out, _ = self.run(["/usr/bin/ldd", BARE_STAGE])
            if code != 0 or "not found" in out:
                raise RuntimeError("the guest cannot load the candidate: " + out.strip()[:200])
            code, _, err = self.run(["/bin/chmod", "755", BARE_STAGE])
            if code == 0:
                code, _, err = self.run(["/bin/mv", "-f", BARE_STAGE, BARE_MCP])
            if code != 0:
                raise RuntimeError("the install rename failed: " + err.strip()[:200])
        finally:
            self.run(["/bin/rm", "-f", BARE_STAGE])
        installed = self.guest_sha(BARE_MCP)
        if installed != sha256:
            raise RuntimeError("the installed candidate digest differs in the guest")
        self.start_mcp()
        return {"installed_sha256": installed}

    def boot_id(self):
        return self.run(["/bin/cat", "/proc/sys/kernel/random/boot_id"])[1].strip()

    def uptime(self):
        return float(self.run(["/bin/cat", "/proc/uptime"])[1].split()[0])

    def set_marker(self, value):
        self.run(["/bin/sh", "-c", f"printf %s {value} > {MEM_FILE}"])

    def marker(self):
        code, out, _ = self.run(["/bin/cat", MEM_FILE])
        return out if code == 0 else None

    def vm_save(self):
        return v.save_vm(self.p)

    def vm_running(self):
        return v.alive(self.pid)

    def vm_wake(self):
        r = v.restore_vm(self.p, self.a.fixture_port)
        self.register(r["pid"])
        return r

    def cleanup(self):
        """Stop only what this run registered. Returns the registered identities still running."""
        left = self.stack.cleanup()
        if not self.a.keep_disk:
            for d in self.stack.vm_dirs():
                for f in (d / "disk.qcow2", d / "state" / "vm.state"):
                    f.unlink(missing_ok=True)
        return left


# ---------- the flow ----------

def scenario(a, env, m, deadline):
    base = env.fixture_url

    def tool(tool_name, /, **kw):
        if time.monotonic() > deadline:
            raise e.Stop("the run exceeded --timeout")
        err, text = env.call(tool_name, **kw)
        if err:
            raise e.Stop(f"{tool_name} failed: {text[:200]}")
        return text

    def me(url):
        tool("step", steps=f"goto {url}", compact=True)
        return parse_me(tool("read").strip())

    def one_session():
        text = tool("sessions")
        return "main" in text and text.count(base) == 1 and env.effects()["records"] == 1

    both = ("alice", "bob")

    def switches(order):
        return [me(f"{base}/switch/{u}") for u in order]

    def holds(seen, order):
        return all(s and s[0] == u and sorted(s[1]) == list(both) for s, u in zip(seen, order))

    e.need(m, "guest_adapter_hash_equals_candidate", m.data["identity"]["guest_adapter_sha256"] == m.data["identity"]["candidate_adapter_sha256"])
    if a.expect_bare_sha256:
        e.need(m, "bare_mcp_sha256_equals_expected", m.data["identity"]["bare_mcp_sha256"] == a.expect_bare_sha256)

    tool("open", url=f"{base}/login/alice")
    first = me(f"{base}/me")
    second = me(f"{base}/login/bob")
    e.need(m, "both_accounts_signed_in_together", first == ("alice", ["alice"]) and second and second[0] == "bob" and sorted(second[1]) == list(both),
           f"after A {first}; after B {second}")
    order = ["alice", "bob", "alice", "bob"]
    e.need(m, "alternating_identity_before_save", holds(switches(order), order), "switched A, B, A, B")
    e.need(m, "one_bare_main_session_one_cookie_record", one_session())

    tool("save_state", name=STATE_NAME)
    problem = state_problem(env.state_stat())
    e.need(m, "state_private_dir_and_file_0600", problem is None, problem or "private directories, one 0600 regular file")
    e.need(m, "state_saved_with_cookie_record", env.state_has_token(), "the one fixture cookie token is in the file")

    err, _ = env.call("close")
    before_pids = env.mcp_pids()
    env.restart_mcp()
    after_pids = env.mcp_pids()
    e.need(m, "mcp_restart_new_process", len(before_pids) == 1 and len(after_pids) == 1 and not set(before_pids) & set(after_pids),
           f"close error={err}; processes {len(before_pids)} before, {len(after_pids)} after; none shared")

    tool("open", state=STATE_NAME, url=f"{base}/me")
    order = ["alice", "bob", "alice"]
    restored = me(f"{base}/me")
    e.need(m, "restore_from_disk_and_switch_both", restored and sorted(restored[1]) == list(both) and holds(switches(order), order) and one_session(),
           f"restored accounts {restored[1] if restored else None}; switched A, B, A")

    env.set_marker(hashlib.sha256(str(time.time()).encode()).hexdigest()[:16])
    snap = lambda: {"boot_id": env.boot_id(), "uptime": env.uptime(), "mcp": env.mcp_pids(), "marker": env.marker(), "state_sha": env.state_sha256()}
    before = snap()
    m.data["evidence"]["save"] = env.vm_save()
    running = env.vm_running()
    m.data["evidence"]["wake"] = env.vm_wake()
    after = snap()
    problems = wake_problems(before, after, running)
    order = ["alice", "bob", "alice"]
    if not problems:
        seen = me(f"{base}/me")
        if not (seen and seen[0] == "alice" and sorted(seen[1]) == list(both) and holds(switches(order), order) and one_session()):
            problems.append("the live session did not keep both accounts after the wake")
    e.need(m, "vm_wake_same_boot_and_profile", not problems, "; ".join(problems) or "same boot id, same bare-mcp, marker kept, state file unchanged, both accounts switch")

    eff = env.effects()
    m.gate("no_duplicate_fixture_effects", PASS if eff == EXPECTED_EFFECTS else FAIL, json.dumps(eff))


def run(a, env):
    """Preflight, then the flow. Returns the exit code: 0 accepted, 1 not accepted, 2 bad setup, 3 host pressure, 4 lock held."""
    try:
        lock = env.lock()
    except SystemExit as x:
        print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
        return 4
    try:
        why = env.pressure()
        if why:
            print(f"STOPPED: host pressure: {why}. Nothing was started.", file=sys.stderr)
            return 3
        out = Path(a.out)
        if out.exists():
            print(f"STOPPED: {out} exists. An earlier result is never overwritten.", file=sys.stderr)
            return 2
        if not e.socket_path_ok(out):
            print(f"STOPPED: --out is too long for QEMU unix sockets: {out}", file=sys.stderr)
            return 2
        pinned = None
        if a.bare_candidate:
            try:  # validate the source before anything is created
                check_candidate(a.bare_candidate, a.expect_bare_sha256)
            except CandidateError as x:
                print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
                return 2
        out.mkdir(mode=0o700, parents=True)
        (out / "private").mkdir(mode=0o700)
        if a.bare_candidate:
            try:
                pinned = pin_candidate(a.bare_candidate, a.expect_bare_sha256, out / "private" / PINNED_NAME)
            except CandidateError as x:
                print(f"STOPPED: {x}. Nothing was started.", file=sys.stderr)
                return 2
        return execute(a, env, out, pinned)
    finally:
        lock.close()


def execute(a, env, out, pinned=None):
    m = e.Manifest("shared-profile")
    base = env.base_identity()
    candidate = env.candidate_sha256()
    m.data["identity"] = {"head": env.head(), "base": {k: base[k] for k in ("id", "virtual_size", "mode")}, "candidate_adapter_sha256": candidate,
                          "harness_sha256": v.sha256_file(Path(__file__)), "guest_adapter_sha256": None, "bare_mcp_sha256": None, "fidelity": FIDELITY + (OVERRIDE if pinned else "")}
    if pinned:
        m.data["identity"]["bare_candidate"] = dict(pinned)
    m.data["host"] = {"start": env.host()}
    left = []
    try:
        e.need(m, "base_frozen_and_verified", base["mode"] == "0o444" and env.base_ok(), f"mode {base['mode']}")
        env.start_fixture()
        info = env.boot_guest(probe=not pinned)
        m.data["identity"].update(guest_adapter_sha256=info["adapter_sha256"], bare_mcp_sha256=info["bare_mcp_sha256"])
        if pinned:
            m.data["identity"]["original_guest_bare_mcp_sha256"] = info["bare_mcp_sha256"]
            got = env.install_candidate(out / "private" / PINNED_NAME, pinned["sha256"])["installed_sha256"]
            m.data["identity"]["bare_mcp_sha256"] = got
            e.need(m, "bare_candidate_installed_and_ready", got == pinned["sha256"], "pinned candidate installed in the private overlay; guest bare-mcp restarted and ready")
        scenario(a, env, m, time.monotonic() + a.timeout)
    except BaseException as x:  # keep the evidence, then report
        m.gate("run_completed", FAIL, f"{type(x).__name__}: {str(x)[:300]}")
    finally:
        try:
            left = env.cleanup()
        except BaseException as x:
            left = [f"cleanup failed: {type(x).__name__}"]
        m.data["host"]["end"] = env.host()
        m.gate("base_unchanged", PASS if env.base_ok() else FAIL)
        m.data["evidence"]["cleanup"] = {"survivors": left, "method": "registered subprocess and bound VM identities plus descendants"}
        m.gate("no_owned_process_left", PASS if not left else FAIL, f"{len(left)} registered identities still running; survivors: {left}")
        m.require(required_gates(a))
        m.write(out / "manifest.json")
        print(f"manifest: {out / 'manifest.json'}")
    return 0 if m.ok() else 1


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="frozen base .qcow2 with its sidecar; never modified")
    ap.add_argument("--out", required=True, help="new evidence directory; an existing one is refused")
    ap.add_argument("--fixture-port", type=int, default=28961, help="host loopback port that the guest reaches as the fixture address")
    ap.add_argument("--lock", default=e.LOCK, help="the shared one-VM lock file")
    ap.add_argument("--min-avail-mib", type=int, default=6144)
    ap.add_argument("--max-load", type=float, default=8.0)
    ap.add_argument("--timeout", type=int, default=900, help="seconds allowed for the whole flow after the guest boots")
    ap.add_argument("--expect-bare-sha256", help="optional: require this guest bare-mcp SHA-256 (adds a gate)")
    ap.add_argument("--bare-candidate", help="optional: a bare-mcp to install into this run's private overlay only; needs --expect-bare-sha256")
    ap.add_argument("--keep-disk", action="store_true", help="keep the private overlay and saved state after the run")
    a = ap.parse_args(argv)
    if a.bare_candidate and not a.expect_bare_sha256:
        ap.error("--bare-candidate needs --expect-bare-sha256")
    return a


def main(argv=None):
    a = parse(argv)
    return run(a, Live(a))


if __name__ == "__main__":
    sys.exit(main())
