import json
import os
import tempfile
import unittest
from pathlib import Path

import vmproto as v
import stage1
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


if __name__ == "__main__":
    unittest.main()
