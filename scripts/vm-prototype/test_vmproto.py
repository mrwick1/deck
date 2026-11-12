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
            for argv in ([], ["setup"], ["freeze-base"], ["overlay"], ["verify-base"], ["stop"], ["clean"]):
                r = subprocess.run([sys.executable, "-I", str(Path(v.__file__)), *argv, "--help"], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, (argv, r.stderr))
            self.assertEqual(os.listdir(d), [])


if __name__ == "__main__":
    unittest.main()
