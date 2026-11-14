"""Tests for shared_profile_e2e.py. They start no VM and no browser.

The harness decides each gate from what its boundary object reports. These tests drive the real
decision and control flow (`run`) against `FakeEnv`. The fake guest is one cookie-jar "browser" that talks
HTTP to the real `Fixture` (the multi-account service of test_multi_account.py), keeps a state file,
restarts its MCP process and saves or wakes. It is a MOCK of the guest: it shows that the harness fails each
fault, not that the real guest behaves this way. Each fault below must give a nonzero exit and no green
`acceptance_complete`."""
import json
import os
import re
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace

import setup_e2e as e
import shared_profile_e2e as s

TOKEN = re.compile(r"sid=([0-9a-f]+)")


class FakeEnv:
    """The boundary of the harness. `faults` is a set of names; each one breaks exactly one behavior."""

    def __init__(self, tmp, faults=()):
        self.faults, self.tmp = set(faults), Path(tmp)
        self.launches, self.calls, self.fixture, self.pid_seq = 0, [], None, 100
        self.vm = False
        self.files = {}  # guest state file: {"data": str, "mode": int}
        self.jar, self.page, self.url = CookieJar(), "", None
        self.mcp, self.boot, self.up, self.mem = [], "boot-1", 100.0, None
        self.saved = False
        self.base_ok_calls = 0
        self.closed = False

    # ---- host ----
    def lock(self):
        if "lock" in self.faults:
            raise SystemExit("another run holds the lock")
        return SimpleNamespace(close=lambda: None)

    def pressure(self):
        return "load average 17.0 is above 8.0" if "load" in self.faults else None

    def base_identity(self):
        return {"id": "base-x", "mode": "0o444", "virtual_size": 1}

    def base_ok(self):
        self.base_ok_calls += 1
        return "base_invalid" not in self.faults and not ("base_changed" in self.faults and self.base_ok_calls > 1)

    def host(self):
        return {}

    def head(self):
        return "abc123"

    def candidate_sha256(self):
        return "c" * 64

    def start_fixture(self):
        self.launches += 1
        self.fixture = s.Fixture(0)
        self.fixture_url = self.fixture.svc.url

    def effects(self):
        eff = self.fixture.effects()
        if "duplicate" in self.faults:
            eff["logins"]["alice"] += 1
        return eff

    # ---- guest lifecycle ----
    def boot_guest(self):
        self.launches += 1
        self.vm = True
        self.mcp = [self.new_pid()]
        return {"adapter_sha256": "d" * 64 if "adapter" in self.faults else self.candidate_sha256(), "bare_mcp_sha256": "b" * 64}

    def new_pid(self):
        self.pid_seq += 1
        return self.pid_seq

    # ---- guest tools through the entrypoint ----
    def fetch(self, url):
        try:
            with urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)).open(url, timeout=10) as r:
                body = r.read().decode()
        except urllib.error.HTTPError as err:
            body = err.read().decode()
        m = re.search(r"<p>(.*?)</p>", body)
        self.page = m.group(1) if m else ""

    def call(self, tool, **args):
        self.calls.append((tool, args))
        if tool == "open":
            if args.get("state"):
                f = self.files.get(args["state"])
                if not f:
                    return True, "no state file"
                for sid in TOKEN.findall(f["data"]):
                    self.jar.set_cookie(__import__("http.cookiejar").cookiejar.Cookie(0, "sid", sid, None, False, "127.0.0.1", True, False, "/", True, False, None, True, None, None, {}))
            self.url = args["url"]
            self.fetch(self.url)
            return False, "opened"
        if tool == "step":
            url = args["steps"].split(" ", 1)[1]
            if "wrong_switch" in self.faults and "/switch/bob" in url:
                url = url.replace("/switch/bob", "/switch/alice")
            if "missing_account" in self.faults and "/login/bob" in url:
                self.page = "user=alice accounts=alice"
                return False, "ok"
            self.fetch(url)
            return False, "ok"
        if tool == "read":
            return False, self.page
        if tool == "sessions":
            return False, "main " + self.fixture_url + (" other " + self.fixture_url if "two_sessions" in self.faults else "")
        if tool == "save_state":
            jar = "; ".join(f"sid={c.value}" for c in self.jar)
            self.files["shared"] = {"data": jar, "mode": 0o644 if "permissive" in self.faults else 0o600}
            if "missing_state" in self.faults:
                self.files.pop("shared")
            return False, "saved"
        if tool == "close":
            self.closed = True
            return False, "closed"
        raise AssertionError(tool)

    def state_stat(self):
        rows = [{"path": p, "kind": "directory", "mode": "700", "owner": "deck", "links": 2} for p in ("deck", "browser")]
        f = self.files.get("shared")
        if f:
            rows.append({"path": "shared.state", "kind": "regular file", "mode": format(f["mode"], "o"), "owner": "deck", "links": 1})
        return rows

    def state_has_token(self):
        f = self.files.get("shared")
        return bool(f) and all(t in f["data"] for t in self.fixture.svc.records)

    def state_sha256(self):
        return "s" * 64 + str(len(self.files.get("shared", {}).get("data", "")))

    def mcp_pids(self):
        return list(self.mcp)

    def restart_mcp(self):
        self.jar = CookieJar()  # new process memory
        if "no_restart" not in self.faults:
            self.mcp = [self.new_pid()]

    def boot_id(self):
        return self.boot

    def uptime(self):
        return self.up

    def set_marker(self, value):
        self.mem = value

    def marker(self):
        return self.mem

    def vm_save(self):
        self.saved, self.vm = True, False
        return {"total_s": 1.0}

    def vm_running(self):
        return self.vm

    def vm_wake(self):
        self.launches += 1
        self.vm, self.saved = True, False
        if "cold_boot" in self.faults:
            self.boot, self.up, self.mcp, self.mem, self.jar = "boot-2", 5.0, [self.new_pid()], None, CookieJar()
        else:
            self.up += 30.0
        return {"load_s": 1.0}

    def cleanup(self):
        if self.fixture:
            self.fixture.stop()
        return ["4242"] if "survivor" in self.faults else []


def args(tmp, **kw):
    d = dict(base=str(Path(tmp) / "base.qcow2"), out=str(Path(tmp) / "out"), fixture_port=28961, min_avail_mib=6144, max_load=8.0,
             lock=str(Path(tmp) / "lock"), expect_bare_sha256=None, keep_disk=False, timeout=240)
    d.update(kw)
    return SimpleNamespace(**d)


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def go(self, *faults, **kw):
        self.env = FakeEnv(self.tmp.name, faults)
        self.a = args(self.tmp.name, **kw)
        self.rc = s.run(self.a, self.env)
        out = Path(self.a.out) / "manifest.json"
        try:
            self.m = json.loads(out.read_text())
        except (OSError, ValueError):
            self.m = None
        return self.rc

    def status(self, gate):
        return self.m["gates"].get(gate, {}).get("status", "absent")

    def assertRejected(self, *faults, gate=None):
        self.assertNotEqual(self.go(*faults), 0, f"fault {faults} was accepted")
        self.assertEqual(self.status("acceptance_complete"), "fail")
        if gate:
            self.assertEqual(self.status(gate), "fail", self.m["gates"])

    def test_invalid_base_stops_before_any_launch(self):
        self.assertRejected("base_invalid", gate="base_frozen_and_verified")
        self.assertEqual(self.env.launches, 0)

    def test_success_path_passes_every_required_gate(self):
        self.assertEqual(self.go(), 0, self.m and self.m["gates"])
        for g in s.required_gates(self.a):
            self.assertEqual(self.status(g), "pass", g)
        self.assertEqual(self.status("acceptance_complete"), "pass")
        self.assertEqual(self.m["open_gates"], [])
        self.assertTrue(self.env.closed)
        self.assertEqual(self.m["identity"]["guest_adapter_sha256"], self.m["identity"]["candidate_adapter_sha256"])
        self.assertIn("direct guest bare-call", self.m["identity"]["fidelity"])
        self.assertIn("no deck server", self.m["identity"]["fidelity"])

    def test_browser_restart_and_vm_wake_are_separate_steps_in_order(self):
        self.go()
        order = [g for g in self.m["gates"]]
        self.assertLess(order.index("mcp_restart_new_process"), order.index("restore_from_disk_and_switch_both"))
        self.assertLess(order.index("restore_from_disk_and_switch_both"), order.index("vm_wake_same_boot_and_profile"))
        self.assertTrue(self.env.vm)

    def test_occupied_lock_launches_nothing(self):
        self.assertNotEqual(self.go("lock"), 0)
        self.assertEqual(self.env.launches, 0)
        self.assertIsNone(self.m)
        self.assertFalse(Path(self.a.out).exists())

    def test_high_load_launches_nothing(self):
        self.assertNotEqual(self.go("load"), 0)
        self.assertEqual(self.env.launches, 0)
        self.assertFalse(Path(self.a.out).exists())

    def test_existing_out_directory_is_refused_and_kept(self):
        Path(self.tmp.name, "out").mkdir()
        Path(self.tmp.name, "out", "manifest.json").write_text("old failed result")
        self.assertNotEqual(self.go(), 0)
        self.assertEqual(self.env.launches, 0)
        self.assertEqual(Path(self.tmp.name, "out", "manifest.json").read_text(), "old failed result")

    def test_adapter_hash_mismatch_is_rejected(self):
        self.assertRejected("adapter", gate="guest_adapter_hash_equals_candidate")

    def test_expected_bare_hash_mismatch_is_rejected(self):
        self.assertNotEqual(self.go(expect_bare_sha256="e" * 64), 0)
        self.assertEqual(self.status("bare_mcp_sha256_equals_expected"), "fail")

    def test_two_sessions_are_rejected(self):
        self.assertRejected("two_sessions", gate="one_bare_main_session_one_cookie_record")

    def test_missing_account_is_rejected(self):
        self.assertRejected("missing_account", gate="both_accounts_signed_in_together")

    def test_wrong_switched_identity_is_rejected(self):
        self.assertRejected("wrong_switch", gate="alternating_identity_before_save")

    def test_missing_state_is_rejected(self):
        self.assertRejected("missing_state", gate="state_private_dir_and_file_0600")

    def test_permissive_state_is_rejected(self):
        self.assertRejected("permissive", gate="state_private_dir_and_file_0600")

    def test_unchanged_mcp_process_after_requested_restart_is_rejected(self):
        self.assertRejected("no_restart", gate="mcp_restart_new_process")

    def test_cold_boot_instead_of_wake_is_rejected(self):
        self.assertRejected("cold_boot", gate="vm_wake_same_boot_and_profile")

    def test_duplicate_fixture_effect_is_rejected(self):
        self.assertRejected("duplicate", gate="no_duplicate_fixture_effects")

    def test_cleanup_survivor_is_rejected(self):
        self.assertRejected("survivor", gate="no_owned_process_left")

    def test_changed_base_is_rejected(self):
        self.assertRejected("base_changed", gate="base_unchanged")

    def test_crash_midway_keeps_later_gates_not_green_and_cleans_up(self):
        env = FakeEnv(self.tmp.name)
        env.restart_mcp = lambda: (_ for _ in ()).throw(TimeoutError("guest did not answer"))
        cleaned = []
        orig = env.cleanup
        env.cleanup = lambda: cleaned.append(1) or orig()
        a = args(self.tmp.name)
        self.assertNotEqual(s.run(a, env), 0)
        m = json.loads((Path(a.out) / "manifest.json").read_text())
        self.assertEqual(cleaned, [1])
        self.assertEqual(m["gates"]["run_completed"]["status"], "fail")
        for g in ("restore_from_disk_and_switch_both", "vm_wake_same_boot_and_profile"):
            self.assertNotEqual(m["gates"].get(g, {}).get("status"), "pass")
        self.assertEqual(m["gates"]["acceptance_complete"]["status"], "fail")


class Pure(unittest.TestCase):
    def test_parse_me(self):
        self.assertEqual(s.parse_me("user=alice accounts=alice,bob"), ("alice", ["alice", "bob"]))
        self.assertIsNone(s.parse_me("anonymous"))
        self.assertIsNone(s.parse_me(""))

    def test_state_problem(self):
        ok = [{"path": p, "kind": "directory", "mode": "700", "owner": "deck", "links": 2} for p in ("deck", "browser")]
        ok.append({"path": "shared.state", "kind": "regular file", "mode": "600", "owner": "deck", "links": 1})
        self.assertIsNone(s.state_problem(ok))
        for change in ({"mode": "640"}, {"owner": "root"}, {"links": 2}, {"kind": "symbolic link"}):
            bad = [dict(r) for r in ok]
            bad[2].update(change)
            self.assertIsNotNone(s.state_problem(bad), change)
        bad = [dict(r) for r in ok]
        bad[1]["mode"] = "755"
        self.assertIsNotNone(s.state_problem(bad))
        self.assertIsNotNone(s.state_problem(ok[:2]))

    def test_wake_problems(self):
        before = {"boot_id": "A", "uptime": 10.0, "mcp": [5], "marker": "m", "state_sha": "x"}
        self.assertEqual(s.wake_problems(before, dict(before, uptime=40.0), running_after_save=False), [])
        for change in ({"boot_id": "B"}, {"uptime": 3.0}, {"mcp": [6]}, {"marker": None}, {"state_sha": "y"}):
            self.assertTrue(s.wake_problems(before, {**before, "uptime": 40.0, **change}, False), change)
        self.assertTrue(s.wake_problems(before, dict(before, uptime=40.0), running_after_save=True))

    def test_guest_argv_uses_the_installed_entrypoint_as_the_desktop_user(self):
        argv, out = s.guest_argv("/home/deck/workspace/e2e/1", "save_state", {"name": "shared"})
        self.assertEqual(argv[:2], ["/usr/local/bin/as-deck", "env"])
        self.assertIn("DECK_OUT=/home/deck/workspace/e2e/1", argv)
        self.assertIn("DECK_WORKSPACE=/home/deck/workspace", argv)
        self.assertEqual(argv[-2], "/usr/local/bin/bare-call")
        req = json.loads(argv[-1])
        self.assertEqual((req["method"], req["params"]["name"], req["params"]["arguments"]), ("tools/call", "save_state", {"name": "shared"}))
        self.assertEqual(out, "/home/deck/workspace/e2e/1/reply.json")

    def test_required_gates_include_expected_hash_only_when_given(self):
        self.assertNotIn("bare_mcp_sha256_equals_expected", s.required_gates(SimpleNamespace(expect_bare_sha256=None)))
        self.assertIn("bare_mcp_sha256_equals_expected", s.required_gates(SimpleNamespace(expect_bare_sha256="a" * 64)))


class RealFixture(unittest.TestCase):
    """The real multi-account service, with real cookies, on loopback. No browser."""

    def test_counts_login_effects_and_keeps_one_record(self):
        f = s.Fixture(0)
        self.addCleanup(f.stop)
        jar = CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        read = lambda p: re.search(r"<p>(.*?)</p>", op.open(f.svc.url + p, timeout=10).read().decode()).group(1)
        self.assertEqual(read("/login/alice"), "user=alice accounts=alice")
        self.assertEqual(read("/login/bob"), "user=bob accounts=alice,bob")
        self.assertEqual(read("/switch/alice"), "user=alice accounts=alice,bob")
        self.assertEqual(f.effects(), {"records": 1, "logins": {"alice": 1, "bob": 1}})

    def test_fixed_port_is_honored(self):
        probe = s.Fixture(0)
        port = probe.svc.httpd.server_port
        probe.stop()
        f = s.Fixture(port)
        self.addCleanup(f.stop)
        self.assertEqual(f.svc.httpd.server_port, port)


class LiveCleanup(unittest.TestCase):
    def test_cleanup_stops_only_registered_children_and_reports_survivors(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = s.Live(args(tmp, out=str(Path(tmp) / "out")))
            (Path(tmp) / "out").mkdir()
            child = subprocess.Popen(["sleep", "30"])
            self.addCleanup(lambda: (child.kill(), child.wait()))
            live.stack.procs["fixture"] = child
            self.assertEqual(live.cleanup(), [])
            self.assertIsNotNone(child.poll())
            self.assertIn(child.pid, live.stack.owned)
            other = subprocess.Popen(["sleep", "30"])  # not registered: cleanup must not touch it
            self.addCleanup(lambda: (other.kill(), other.wait()))
            live.cleanup()
            self.assertIsNone(other.poll())


class RealLock(unittest.TestCase):
    def test_a_held_lock_stops_run_before_any_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            holder = e.take_lock(str(Path(tmp) / "lock"))
            self.addCleanup(holder.close)
            live = s.Live(args(tmp, out=str(Path(tmp) / "out")))
            launched = []
            live.start_fixture = live.boot_guest = lambda: launched.append(1)
            self.assertEqual(s.run(live.a, live), 4)
            self.assertEqual(launched, [])
            self.assertFalse((Path(tmp) / "out").exists())


class Cli(unittest.TestCase):
    def test_help_launches_nothing_and_needs_no_private_path(self):
        r = subprocess.run(["python3", str(Path(__file__).with_name("shared_profile_e2e.py")), "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--base", r.stdout)
        self.assertIn("--out", r.stdout)

    def test_base_and_out_are_required(self):
        with self.assertRaises(SystemExit):
            s.parse([])

    def test_no_private_path_in_source(self):
        text = Path(s.__file__).read_text()
        self.assertNotIn("/home/", text.replace("/home/deck", ""))
        self.assertEqual(s.parse(["--base", "b", "--out", "o"]).lock, e.LOCK)


if __name__ == "__main__":
    unittest.main()
