import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import vmproto as v
import stage1
import unittest.mock
from unittest.mock import patch


NO_SESSION = (3, json.dumps({"ready": False, "reason": "no_session"}), "")
READY = (0, json.dumps({"ready": True, "tools": 2, "bot_id": "bot-1", "guest_id": "g"}), "")


class FakeInstallQga:
    """Records the guest writes and commands of install_executor. Answers the readiness probe with `probe`."""

    def __init__(self, probe):
        self.probe = probe
        self.pushed = []
        self.scripts = []
        self.execs = []

    def push(self, guest_path, data):
        self.pushed.append(guest_path)

    def sh(self, script, stdin=None, timeout=30):
        self.scripts.append(script)
        return (1, "", "") if script.startswith("test -e") else (0, "", "")

    def exec(self, argv, stdin=None, timeout=30):
        self.execs.append(argv)
        return self.probe if "--ready" in argv else (0, "", "")


class BareMcpSync(unittest.TestCase):
    """DECK_VM_BARE_MCP names the bare-mcp file of the deploy. A guest that holds another digest gets it, with the shared install checks."""

    def setUp(self):
        import shared_profile_e2e as sp
        self.sp = sp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = v.Paths(Path(self.tmp.name) / "bot")
        self.p.mk()
        self.file = Path(self.tmp.name) / "bare-mcp"
        self.file.write_bytes(b"newer bare")
        self.file.chmod(0o755)
        self.want = v.sha256_file(self.file)
        (self.p.root / "executor.json").write_text(json.dumps({"bot_id": "bot-1", "guest_id": "g"}))
        self.installs = []

    def sync(self, guest, env=True, **kw):
        def install(live, pinned, sha):
            self.installs.append((live.bot, Path(pinned), sha))
            return {"installed_sha256": sha}

        with patch.dict(os.environ, {"DECK_VM_BARE_MCP": str(self.file)} if env else {}, clear=False), \
                patch.object(self.sp.Live, "guest_sha", lambda live, path: guest), patch.object(self.sp.Live, "install_candidate", install):
            if not env:
                os.environ.pop("DECK_VM_BARE_MCP", None)
            return v.sync_bare_mcp(self.p)

    def test_a_differing_guest_digest_installs_the_file_once_for_the_bound_bot(self):
        self.assertEqual(self.sync("0" * 64), {"bare_mcp_installed": self.want})
        self.assertEqual(self.installs, [("bot-1", self.file, self.want)])

    def test_an_equal_digest_a_missing_setting_or_a_missing_binding_install_nothing(self):
        self.assertEqual(self.sync(self.want), {})
        self.assertEqual(self.sync("0" * 64, env=False), {})
        (self.p.root / "executor.json").unlink()
        self.assertEqual(self.sync("0" * 64), {})
        self.assertEqual(self.installs, [])

    def test_a_file_that_cannot_be_read_stops_before_the_guest_is_touched(self):
        self.file.chmod(0o644)  # not executable
        with self.assertRaises(SystemExit):
            self.sync("0" * 64)
        self.assertEqual(self.installs, [])

    def test_the_readiness_wait_after_the_restart_uses_the_bound_bot_not_the_shared_profile(self):
        live = self.sp.Live.on(self.p, "bot-1")
        with patch.object(self.sp.Live, "run", lambda *a, **k: (0, "", "")), patch.object(v, "ready") as ready:
            live.start_mcp()
        ready.assert_called_once_with(self.p, "bot-1", 60)


class Helpers(unittest.TestCase):
    def test_start_refuses_live_vm_before_touching_sockets(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            p.mk()
            p.qmp.write_text("owned socket marker")
            with patch.object(v, "read_vm", return_value={"pid": 123}), patch.object(v, "proc_ok", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    v.start_qemu(p, "run", 18080)
            self.assertEqual(p.qmp.read_text(), "owned socket marker")

    def test_result_validation_rejects_failed_markers_and_boundaries(self):
        result = {"boundary": {"fixture_reachable": True, "host_marker_leaked": False,
                  "loopback_fixture_via_gateway_reachable": False},
                  "artifacts": {"bare_screenshot_sha_match": True, "download_sha_match": True},
                  "cycles": [{"post": dict.fromkeys(("nano_process_same", "notes_file_not_written", "disk_marker", "shell_file", "downloaded_file_sha", "bare_tab_url", "bare_logged_in", "bare_cookie"), True), "terminal_pixels_same": True,
                              "vm_gone": True, "helpers_gone": True, "fixture_alive": True}],
                  "kill": {"gone": True}, "after_kill": {"disk_marker": True, "shell_file": True,
                  "downloaded_file_sha": True, "post_restore_marker": True}}
        stage1.validate_results(result)
        result["cycles"][0]["post"]["bare_cookie"] = False
        with self.assertRaisesRegex(RuntimeError, "resume"):
            stage1.validate_results(result)
        result["cycles"][0]["post"]["bare_cookie"] = True
        result["boundary"]["host_marker_leaked"] = True
        with self.assertRaisesRegex(RuntimeError, "boundary"):
            stage1.validate_results(result)

    def test_rss_parse(self):
        self.assertEqual(v.rss_kib("Name:\tqemu\nVmRSS:\t  1234 kB\nThreads:\t3\n"), 1234)
        self.assertEqual(v.rss_kib("Name:\tkthread\n"), 0)

    def test_identity_requires_name_and_root(self):
        p = v.Paths("/tmp/rt")
        cmd = ["qemu-system-x86_64", "-name", "deck-stage1,debug-threads=on", f"file={p.disk}"]
        self.assertTrue(v.is_our_vm(cmd, root=p.root))
        self.assertTrue(v.is_our_vm("\0".join(cmd), root=p.root))
        self.assertFalse(v.is_our_vm(cmd, root="/other"))
        self.assertFalse(v.is_our_vm(["qemu-system-x86_64", "-name", "someone-else"], root=p.root))
        self.assertFalse(v.is_our_vm(["python3", "-name", "deck-stage1,x"]))
        self.assertFalse(v.is_our_vm([]))

    def test_state_pairs_with_disk_until_disk_changes(self):
        with tempfile.TemporaryDirectory() as d:
            disk = Path(d) / "disk.qcow2"
            disk.write_bytes(b"a" * 10)
            meta = {"disk": v.disk_fingerprint(disk)}
            self.assertTrue(v.state_matches_disk(meta, disk))
            disk.write_bytes(b"b" * 10)  # the VM ran after the save
            os.utime(disk, ns=(1, meta["disk"]["mtime_ns"] + 1))
            self.assertFalse(v.state_matches_disk(meta, disk))
            self.assertFalse(v.state_matches_disk({}, disk))

    def test_run_mode_is_restricted_and_has_no_host_share(self):
        p = v.Paths("/tmp/rt")
        cmd = v.qemu_cmd(p, "run", 18080)
        joined = " ".join(cmd)
        self.assertIn("restrict=on", joined)
        self.assertIn(f"guestfwd=tcp:{v.FIXTURE_ADDR}:80-cmd:socat - TCP:127.0.0.1:18080", joined)
        for banned in ("virtfs", "9p", "hostfwd", "-vnc", "-spice", "-display gtk", "cdrom"):
            self.assertNotIn(banned, joined)
        self.assertNotIn("-incoming", cmd)
        self.assertIn("defer", v.qemu_cmd(p, "run", 18080, incoming=True))

    def test_internet_is_explicit_and_keeps_fixture_and_no_host_share(self):
        p = v.Paths("/tmp/rt")
        cmd = v.qemu_cmd(p, "run", 18080, internet=True)
        net = cmd[cmd.index("-netdev") + 1]
        self.assertIn("restrict=off", net)
        self.assertIn("guestfwd=", net)
        self.assertNotIn("hostfwd", net)
        self.assertNotIn("-virtfs", cmd)

    def test_restore_rejects_network_change_before_starting_vm(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            p.mk()
            p.statefile.write_bytes(b"saved")
            p.statemeta.write_text(json.dumps({"internet": False}))
            with patch.object(v, "start_qemu") as start:
                with self.assertRaisesRegex(SystemExit, "network"):
                    v.restore_vm(p, 18080, internet=True)
                start.assert_not_called()
                self.assertEqual(p.statefile.read_bytes(), b"saved")

    def test_executor_port_is_a_private_unix_socket_in_run_mode_only(self):
        p = v.Paths("/tmp/rt")
        run = " ".join(v.qemu_cmd(p, "run", 18080))
        self.assertIn(f"socket,id=exec,path={p.exec},server=on,wait=off", run)
        self.assertIn("virtserialport,chardev=exec,name=org.deck.executor.0", run)
        self.assertNotIn("tcp", run.split("-chardev socket,id=exec")[1].split(" -")[0])
        self.assertNotIn("executor", " ".join(v.qemu_cmd(p, "setup", 18080, seed_iso="/tmp/s.iso")))

    def test_install_executor_validates_before_touching_the_guest(self):
        p = v.Paths("/tmp/does-not-exist-rt")
        for bad in ("", "a b", "../x", "x" * 65):
            with self.assertRaises(SystemExit):
                v.install_executor(p, bad)

    def _rebind(self, guest_id=None, max_epoch=0):
        """Run install_executor against a fake guest channel. Returns (identity written to the guest, executor.json)."""
        seen = {}

        class FakeQga:
            def __init__(self, path):
                pass

            def sh(self, script, stdin=None, timeout=10):
                if script.startswith("test -e"):
                    return 0, "", ""
                seen["ident"] = json.loads(stdin)
                return 0, "", ""

            def push(self, path, data):
                pass

            def exec(self, argv, timeout=10):
                return 0, '{"ready": true}', ""

        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            with patch.object(v, "Qga", FakeQga):
                v.install_executor(p, "bot-1", **({"guest_id": guest_id, "max_epoch": max_epoch} if guest_id else {}))
            return seen["ident"], json.loads((p.root / "executor.json").read_text())

    def test_install_executor_restarts_the_service_after_it_writes_the_identity(self):
        """The udev trigger can start the executor before the identity file exists. It reads that file only at start."""
        scripts = []

        class FakeQga:
            def __init__(self, path):
                pass

            def sh(self, script, stdin=None, timeout=10):
                scripts.append(script)
                return 0, "", ""

            def push(self, path, data):
                pass

            def exec(self, argv, timeout=10):
                return 0, '{"ready": true}', ""

        with tempfile.TemporaryDirectory() as d, patch.object(v, "Qga", FakeQga):
            v.install_executor(v.Paths(d), "bot-1")
        last = scripts[-1]
        self.assertLess(last.index("identity.json"), last.index("systemctl restart deck-executor.service"))

    def test_install_executor_keeps_a_new_identity_by_default(self):
        ident, info = self._rebind()
        self.assertRegex(ident["guest_id"], r"^[0-9a-f]{32}$")
        self.assertEqual((ident["max_epoch"], info["guest_id"]), (0, ident["guest_id"]))

    def test_install_executor_rebinds_an_existing_logical_computer(self):
        """A fresh disk takes the pinned guest id and the host epoch, so the host pin and the fence stay monotonic."""
        ident, info = self._rebind("ab" * 16, 17)
        self.assertEqual(ident, {"bot_id": "bot-1", "guest_id": "ab" * 16, "max_epoch": 17})
        self.assertEqual(info, {"bot_id": "bot-1", "guest_id": "ab" * 16})

    def test_install_executor_rejects_a_bad_rebind_before_touching_the_guest(self):
        with patch.object(v, "Qga", side_effect=AssertionError("guest touched")):
            for gid, ep in (("short", 1), ("AB" * 16, 1), ("ab" * 16, -1), ("ab" * 16, True), ("ab" * 16, "1")):
                with self.assertRaises(SystemExit):
                    v.install_executor(v.Paths("/tmp/does-not-exist-rt"), "bot-1", guest_id=gid, max_epoch=ep)

    def install(self, probe):
        """Run install_executor against a fake QGA. `probe` answers `bare-call --ready`."""
        q = FakeInstallQga(probe)
        d = tempfile.mkdtemp(prefix="deck-install-")
        self.addCleanup(shutil.rmtree, d, True)
        p = v.Paths(d)
        p.mk()
        with patch.object(v, "Qga", return_value=q):
            v.install_executor(p, "bot-1")
        return q

    def test_install_writes_guest_files_under_a_temp_name_then_renames(self):
        q = self.install(NO_SESSION)
        finals = list(v.EXECUTOR_FILES.values())
        self.assertEqual(len(q.pushed), len(finals))
        script = "\n".join(q.scripts)
        for dst, tmp in zip(finals, q.pushed):
            self.assertNotEqual(tmp, dst)
            self.assertEqual(os.path.dirname(tmp), os.path.dirname(dst))
            self.assertIn(f"mv -f {tmp} {dst}", script)
        self.assertLess(script.index(f"chmod 755 {q.pushed[0]}"), script.index("mv -f"))

    def test_no_session_starts_bare_session_exactly_once(self):
        q = self.install(NO_SESSION)
        starts = [a for a in q.execs if any("swaymsg" in x for x in a)]
        self.assertEqual(starts, [["/usr/local/bin/as-deck", "sh", "-c", "swaymsg exec /usr/local/bin/bare-session"]])

    def test_ready_session_is_not_started_again(self):
        q = self.install(READY)
        self.assertEqual([a for a in q.execs if any("swaymsg" in x for x in a)], [])

    def test_executor_files_exist_and_seed_carries_them(self):
        for f in v.EXECUTOR_FILES:
            self.assertTrue((v.HERE / "guest" / f).is_file(), f)
        self.assertIn("deck-executor", (v.HERE / "guest" / "setup-guest.sh").read_text())

    def test_setup_mode_only_adds_seed(self):
        p = v.Paths("/tmp/rt")
        cmd = v.qemu_cmd(p, "setup", 18080, seed_iso="/tmp/s.iso")
        self.assertIn("restrict=off", " ".join(cmd))
        self.assertIn("file=/tmp/s.iso,media=cdrom,readonly=on,if=virtio", cmd)

    def test_scope_limits(self):
        c = v.scope_cmd(["x"], lifetime=5, mem_max="1G")
        self.assertIn("MemoryMax=1G", c)
        self.assertIn("RuntimeMaxSec=5", c)
        self.assertEqual(c[-1], "x")

    def test_default_lifetime_is_bounded_and_infinity_is_opt_in(self):
        self.assertEqual(v.vm_lifetime({}), v.LIFETIME_S)
        self.assertEqual(v.vm_lifetime({"DECK_VM_LIFETIME_S": "infinity"}), "infinity")
        with self.assertRaises(SystemExit):
            v.vm_lifetime({"DECK_VM_LIFETIME_S": "0"})
        with self.assertRaises(SystemExit):
            v.vm_lifetime({"DECK_VM_LIFETIME_S": "forever"})
        self.assertIn("RuntimeMaxSec=infinity", v.scope_cmd(["x"], lifetime="infinity"))

    def test_zombie_counts_as_gone(self):
        import subprocess, time
        pr = subprocess.Popen(["true"])
        for _ in range(100):  # wait for exit without reaping
            if not v.alive(pr.pid):
                break
            time.sleep(0.02)
        self.assertTrue(Path(f"/proc/{pr.pid}").exists())
        self.assertFalse(v.alive(pr.pid))
        self.assertTrue(v.wait_gone(pr.pid, 1))
        pr.wait()
        self.assertFalse(v.alive(pr.pid))


def qimg(*args):
    return subprocess.run(["qemu-img", *args], check=True, capture_output=True, text=True).stdout


def qwrite(path, byte, offset=0):
    subprocess.run(["qemu-io", "-c", f"write -P {byte} {offset} 4k", str(path)], check=True, capture_output=True)


def qread_is(path, byte, offset=0):
    r = subprocess.run(["qemu-io", "-c", f"read -P {byte} {offset} 4k", str(path)], capture_output=True, text=True)
    return r.returncode == 0 and "Pattern verification failed" not in r.stdout


@unittest.skipUnless(shutil.which("qemu-img") and shutil.which("qemu-io"), "needs qemu-img and qemu-io")
class BaseAndOverlay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bases = self.root / "bases"

    def provisioned(self, name="setup"):
        p = v.Paths(self.root / name)
        p.mk()
        qimg("create", "-f", "qcow2", str(p.disk), "8M")
        qwrite(p.disk, 0x11)
        v.write_disk_meta(p, {"kind": "standalone", "provisioned": "setup", "run_booted": False})
        return p

    def frozen(self):
        return v.freeze_base(self.provisioned(), self.bases)

    def overlay(self, name, base):
        p = v.Paths(self.root / name)
        p.mk()
        v.create_overlay(p, base)
        return p

    def test_freeze_moves_disk_into_read_only_base_with_digest(self):
        p = self.provisioned()
        digest = v.sha256_file(p.disk)
        base = v.freeze_base(p, self.bases)
        self.assertEqual(base["sha256"], digest)
        self.assertFalse(p.disk.exists())
        path = v.base_path(self.bases, base["id"])
        self.assertEqual(v.sha256_file(path), digest)
        self.assertEqual(path.stat().st_mode & 0o222, 0)
        self.assertEqual(v.read_json(path.with_suffix(".json"))["kind"], "base")

    def test_freeze_rejects_disk_that_booted_or_has_unknown_history(self):
        p = self.provisioned()
        v.write_disk_meta(p, {"kind": "standalone", "provisioned": "setup", "run_booted": True})
        with self.assertRaisesRegex(SystemExit, "booted"):
            v.freeze_base(p, self.bases)
        p.diskmeta.unlink()  # a legacy disk such as the Stage 1 one
        with self.assertRaisesRegex(SystemExit, "provenance"):
            v.freeze_base(p, self.bases)
        self.assertTrue(p.disk.exists())

    def test_freeze_refuses_saved_state_and_replacement(self):
        p = self.provisioned()
        p.statefile.write_text("x")
        with self.assertRaisesRegex(SystemExit, "saved state"):
            v.freeze_base(p, self.bases)
        p.statefile.unlink()
        base = v.freeze_base(p, self.bases)
        q = self.provisioned("again")  # identical bytes, same id
        with self.assertRaisesRegex(SystemExit, "exists"):
            v.freeze_base(q, self.bases)
        self.assertEqual(v.sha256_file(v.base_path(self.bases, base["id"])), base["sha256"])

    def test_overlays_keep_backing_chain_and_stay_private(self):
        base = self.frozen()
        bpath = v.base_path(self.bases, base["id"])
        a, b = self.overlay("a", bpath), self.overlay("b", bpath)
        for p in (a, b):
            self.assertEqual(Path(json.loads(qimg("info", "--output=json", str(p.disk)))["full-backing-filename"]), bpath)
        qwrite(a.disk, 0xAA, 4096)
        qwrite(b.disk, 0xBB, 4096)
        self.assertTrue(qread_is(a.disk, 0xAA, 4096))
        self.assertTrue(qread_is(b.disk, 0xBB, 4096))
        self.assertFalse(qread_is(a.disk, 0xBB, 4096))
        self.assertFalse(qread_is(b.disk, 0xAA, 4096))
        self.assertTrue(qread_is(a.disk, 0x11) and qread_is(b.disk, 0x11))  # both inherit the base
        self.assertEqual(v.sha256_file(bpath), base["sha256"])
        total = v.storage_report(a, b)
        self.assertEqual(total["total_bytes"], sum(total["parts"].values()))
        self.assertIn("base", total["parts"])

    def test_status_names_a_missing_base_without_a_traceback(self):
        base = self.frozen()
        bpath = v.base_path(self.bases, base["id"])
        a = self.overlay("a", bpath)
        bpath.chmod(0o644)
        bpath.unlink()
        before = a.disk.read_bytes()
        env = {**os.environ, "XDG_CACHE_HOME": str(self.root / "cache")}
        r = subprocess.run([sys.executable, "-I", str(Path(v.__file__)), "--dir", str(a.root), "status"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn(f"backing image {base['id']} is missing", r.stderr)
        self.assertIn("do not substitute", r.stderr)
        self.assertEqual(a.disk.read_bytes(), before)

    def test_overlay_refuses_replacing_a_disk_and_unknown_or_tampered_base(self):
        base = self.frozen()
        bpath = v.base_path(self.bases, base["id"])
        a = self.overlay("a", bpath)
        qwrite(a.disk, 0xAA)
        with self.assertRaisesRegex(SystemExit, "exists"):
            v.create_overlay(a, bpath)
        self.assertTrue(qread_is(a.disk, 0xAA))
        loose = self.root / "loose.qcow2"
        qimg("create", "-f", "qcow2", str(loose), "8M")
        with self.assertRaisesRegex(SystemExit, "not a frozen base"):
            v.create_overlay(v.Paths(self.root / "c"), loose)
        os.chmod(bpath, 0o644)
        with open(bpath, "r+b") as f:
            f.write(b"tamper")
        with self.assertRaisesRegex(SystemExit, "digest|changed"):
            v.create_overlay(v.Paths(self.root / "d"), bpath)

    def test_clean_refuses_to_delete_a_base_and_cleans_overlay_with_its_record(self):
        base = self.frozen()
        bpath = v.base_path(self.bases, base["id"])
        a = self.overlay("a", bpath)
        shutil.copy(bpath, self.root / "copy.qcow2")
        args = type("A", (), {"disk": True})
        with contextlib.redirect_stdout(io.StringIO()):
            v.cmd_clean(args, a)
        self.assertFalse(a.disk.exists() or a.diskmeta.exists())
        self.assertTrue(bpath.exists())
        # a runtime whose disk path is a frozen base must not delete it
        evil = v.Paths(self.root / "evil")
        evil.mk()
        os.symlink(bpath, evil.disk)
        shutil.copy(bpath.with_suffix(".json"), evil.diskmeta)
        with self.assertRaisesRegex(SystemExit, "frozen base"):
            v.cmd_clean(args, evil)
        self.assertTrue(bpath.exists())

    def test_disk_check_detects_missing_changed_and_unrecorded_backing(self):
        base = self.frozen()
        bpath = v.base_path(self.bases, base["id"])
        a = self.overlay("a", bpath)
        v.check_disk(a)  # passes
        c = v.Paths(self.root / "c")  # an overlay copied without its record is not a standalone disk
        c.mk()
        shutil.copy(a.disk, c.disk)
        with self.assertRaisesRegex(SystemExit, "backing file"):
            v.check_disk(c)
        os.utime(bpath, ns=(1, 1))
        with self.assertRaisesRegex(SystemExit, "base changed"):
            v.check_disk(a)
        bpath.unlink()
        with self.assertRaisesRegex(SystemExit, "base .*missing"):
            v.check_disk(a)

    def test_storage_counts_each_distinct_base_once(self):
        first = self.frozen()
        p = self.provisioned("second")
        qwrite(p.disk, 0x22)
        second = v.freeze_base(p, self.bases)
        a = self.overlay("a", v.base_path(self.bases, first["id"]))
        b = self.overlay("b", v.base_path(self.bases, second["id"]))
        c = self.overlay("c", v.base_path(self.bases, first["id"]))
        expected = sum(v.allocated_bytes(x.disk) for x in (a, b, c)) + sum(
            v.allocated_bytes(v.base_path(self.bases, x["id"])) for x in (first, second))
        self.assertEqual(v.storage_report(a, b, c)["total_bytes"], expected)

    def test_legacy_standalone_disk_still_passes(self):
        p = v.Paths(self.root / "legacy")
        p.mk()
        qimg("create", "-f", "qcow2", str(p.disk), "8M")
        v.check_disk(p)

    def test_saved_state_is_refused_against_another_base_or_disk(self):
        base = self.frozen()
        bpath = v.base_path(self.bases, base["id"])
        a = self.overlay("a", bpath)
        meta = {"disk": v.disk_fingerprint(a.disk), "base": base["id"]}
        self.assertTrue(v.state_matches_disk(meta, a.disk, base["id"]))
        self.assertFalse(v.state_matches_disk(meta, a.disk, "other"))
        self.assertFalse(v.state_matches_disk(meta, a.disk, None))
        self.assertFalse(v.state_matches_disk({"disk": meta["disk"]}, a.disk, base["id"]))
        a.state.mkdir(exist_ok=True)
        a.statefile.write_text("mem")
        a.statemeta.write_text(json.dumps({**meta, "base": "other", "codec": "none"}))
        with patch.object(v, "start_qemu") as sq:
            with self.assertRaisesRegex(SystemExit, "does not match"):
                v.restore_vm(a, 18080)
        sq.assert_not_called()
        self.assertTrue(a.statefile.exists())  # the refused state is kept


class Codec(unittest.TestCase):
    def test_uris_pair_with_codec_and_unknown_codec_is_refused(self):
        self.assertEqual(v.state_uri("none", Path("/s/vm.state"), "save"), "file:/s/vm.state")
        self.assertEqual(v.state_uri("none", Path("/s/vm.state"), "load"), "file:/s/vm.state")
        self.assertEqual(v.state_uri("zstd-1", Path("/s/a b"), "save"), "exec:zstd -q -f -1 -o '/s/a b'")
        self.assertEqual(v.state_uri("zstd-1", Path("/s/a b"), "load"), "exec:zstd -q -d -c '/s/a b'")
        self.assertEqual(v.state_uri("lz4", Path("/s/a"), "save"), "exec:lz4 -q -f - /s/a")
        self.assertEqual(v.state_uri("lz4", Path("/s/a"), "load"), "exec:lz4 -q -d -c /s/a")
        for bad in ("lzma", "zstd-0", "zstd-20", "zstd-x", "lz4-3"):
            with self.assertRaisesRegex(SystemExit, "codec"):
                v.state_uri(bad, Path("/s/x"), "load")

    def test_codec_rotation_pairs_every_codec_in_every_round(self):
        codecs = ["none", "zstd-1", "lz4"]
        rounds = [stage1.codec_order(codecs, i) for i in range(1, 4)]
        self.assertTrue(all(sorted(r) == sorted(codecs) for r in rounds))
        self.assertEqual(len({r[0] for r in rounds}), 3)  # no codec always goes first

    def test_helpers_check_uses_owned_pids_not_command_text(self):
        owned = subprocess.Popen(["sleep", "30"])
        decoy = subprocess.Popen(["sh", "-c", "sleep 30 # deck-stage1 socat - TCP:127.0.0.1:18180"])
        try:
            self.assertEqual(stage1.owned_left([owned.pid]), [owned.pid])
            owned.kill()
            owned.wait()
            self.assertEqual(stage1.owned_left([owned.pid]), [])  # the decoy with the VM name in its text does not count
        finally:
            decoy.kill()
            decoy.wait()
            owned.kill()
            owned.wait()

    def test_by_codec_groups_cycles_and_flags_marker_failures(self):
        cyc = [{"codec": "none", "save": {"total_s": 4, "state_bytes": 10, "cpu_s": 1, "peak_rss_kib_sampled": 5}, "restore": {"to_usable_s": 2, "cpu_s_since_launch": 1, "peak_rss_kib_sampled": 5}, "post": {"a": True}},
               {"codec": "lz4", "save": {"total_s": 5, "state_bytes": 4, "cpu_s": 2, "peak_rss_kib_sampled": 6}, "restore": {"to_usable_s": 3, "cpu_s_since_launch": 2, "peak_rss_kib_sampled": 6}, "post": {"a": False}}]
        r = stage1.by_codec(cyc)
        self.assertEqual(r["none"]["state_bytes"], [10])
        self.assertTrue(r["none"]["markers_ok"])
        self.assertFalse(r["lz4"]["markers_ok"])

    def test_missing_codec_tool_is_refused_before_restore_starts(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            p.mk()
            p.disk.write_bytes(b"disk")
            p.statefile.write_text("saved")
            p.statemeta.write_text(json.dumps({"disk": v.disk_fingerprint(p.disk), "codec": "zstd-1"}))
            with patch.object(v.shutil, "which", return_value=None), patch.object(v, "start_qemu") as start:
                with self.assertRaisesRegex(SystemExit, "zstd.*required"):
                    v.restore_vm(p, 18080)
            start.assert_not_called()
            self.assertEqual(p.statefile.read_text(), "saved")

    def test_invalid_codec_does_not_delete_existing_saved_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            p.mk()
            p.statefile.write_text("saved")
            with self.assertRaisesRegex(SystemExit, "codec"):
                v.save_vm(p, codec="unknown")
            self.assertEqual(p.statefile.read_text(), "saved")

    def test_stalled_restore_times_out_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            p.mk()
            p.disk.write_bytes(b"disk")
            p.statefile.write_text("saved")
            p.statemeta.write_text(json.dumps({"disk": v.disk_fingerprint(p.disk), "codec": "none"}))
            q = unittest.mock.MagicMock()
            q.cmd.return_value = {"status": "paused"}
            with patch.object(v, "start_qemu", return_value=123), patch.object(v, "Qmp", return_value=q), \
                    patch.object(v, "cgroup_dir", return_value=None), patch.object(v, "tree_rss_kib", return_value=0), \
                    patch.object(v.time, "monotonic", side_effect=[0, 61]), patch.object(v, "kill_vm") as kill:
                with self.assertRaisesRegex(TimeoutError, "restore"):
                    v.restore_vm(p, 18080)
                kill.assert_called_once_with(p)
            q.close.assert_called_once()

    def test_restore_uses_the_codec_recorded_at_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            p.mk()
            p.disk.write_bytes(b"d")
            p.statefile.write_text("m")
            p.statemeta.write_text(json.dumps({"disk": v.disk_fingerprint(p.disk), "codec": "bogus"}))
            with patch.object(v, "start_qemu") as sq:
                with self.assertRaisesRegex(SystemExit, "codec"):
                    v.restore_vm(p, 18080)
            sq.assert_not_called()


class CaptureAndCli(unittest.TestCase):
    def test_one_shot_capture_leaves_no_thread_process_or_socket(self):
        with tempfile.TemporaryDirectory() as d:
            p = v.Paths(d)
            qmp, qga = unittest.mock.MagicMock(), unittest.mock.MagicMock()
            qga.sh.return_value = (0, "", "")
            qga.pull.return_value = "sha"
            threads = threading.active_count()
            with patch.object(v, "Qmp", return_value=qmp), patch.object(v, "Qga", return_value=qga), \
                    patch.object(subprocess, "Popen") as popen, contextlib.redirect_stdout(io.StringIO()):
                v.cmd_capture(type("A", (), {"tag": "t"}), p)
            self.assertEqual(threading.active_count(), threads)
            popen.assert_not_called()
            qmp.close.assert_called_once()

    def test_help_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "DECK_VM_DIR": str(Path(d) / "rt"), "XDG_CACHE_HOME": str(Path(d) / "cache")}
            for argv in ([], ["setup"], ["freeze-base"], ["overlay"], ["verify-base"], ["stop"], ["clean"], ["install-executor"]):
                r = subprocess.run([sys.executable, "-I", str(Path(v.__file__)), *argv, "--help"], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, (argv, r.stderr))
            self.assertEqual(os.listdir(d), [])



class SaveBandwidth(unittest.TestCase):
    def test_save_limit_is_applied_and_verified_before_migration(self):
        class Q:
            def __init__(self): self.calls = []
            def cmd(self, name, **args):
                self.calls.append((name, args))
                if name == 'migrate-set-parameters' and set(args) != {'max-bandwidth'}:
                    raise RuntimeError('QMP rejects unexpected arguments')
                return {'max-bandwidth': 512 << 20} if name == 'query-migrate-parameters' else {}
        q = Q()
        v.start_save_migration(q, 'file:state', 512)
        self.assertEqual([x[0] for x in q.calls], ['migrate-set-parameters', 'query-migrate-parameters', 'migrate'])
        self.assertEqual(q.calls[0][1]['max-bandwidth'], 512 << 20)

    def test_refused_limit_never_starts_migration(self):
        class Q:
            def __init__(self): self.calls = []
            def cmd(self, name, **args):
                self.calls.append(name)
                return {'max-bandwidth': 128 << 20}
        q = Q()
        with self.assertRaisesRegex(RuntimeError, 'bandwidth'):
            v.start_save_migration(q, 'file:state', 512)
        self.assertNotIn('migrate', q.calls)

    def test_reference_call_preserves_the_current_qemu_limit(self):
        class Q:
            def __init__(self): self.calls = []
            def cmd(self, name, **args): self.calls.append(name)
        q = Q()
        v.start_save_migration(q, 'file:state', None)
        self.assertEqual(q.calls, ['migrate'])


if __name__ == "__main__":
    unittest.main()
