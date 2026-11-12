"""Deterministic tests for the guest executor. No VM. Every wait is bounded."""
import base64
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "guest"))
import deck_executor as de  # noqa: E402

BOT = "bot-a"
WAIT = 15.0


class Client:
    """The host side of a socketpair. Frames out, frames in, with timeouts."""

    def __init__(self, ex):
        self.sock, srv = socket.socketpair()
        self.sock.settimeout(WAIT)
        self.reader = de.FrameReader()
        self.q = []
        self.n = 0
        self.thread = threading.Thread(target=lambda: (ex.serve(srv.fileno()), srv.close()), daemon=True)
        self.thread.start()
        self.srv = srv

    def send(self, **m):
        self.n += 1
        m.setdefault("id", str(self.n))
        self.sock.sendall(de.encode_frame(m))
        return m["id"]

    def recv(self, pred=lambda m: True, timeout=WAIT):
        end = time.monotonic() + timeout
        while True:
            for i, m in enumerate(self.q):
                if pred(m):
                    return self.q.pop(i)
            left = end - time.monotonic()
            if left <= 0:
                raise AssertionError(f"timeout; queued={self.q}")
            self.sock.settimeout(left)
            data = self.sock.recv(65536)
            if not data:
                raise AssertionError("connection closed")
            self.q += self.reader.feed(data)

    def of_type(self, t, **kw):
        return self.recv(lambda m: m.get("type") == t and all(m.get(k) == v for k, v in kw.items()))

    def close(self):
        self.sock.close()
        self.thread.join(5)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deck-exec-test-")
        self.state, self.ws = f"{self.tmp}/state", f"{self.tmp}/ws"
        os.makedirs(self.state)
        json.dump({"bot_id": BOT, "guest_id": "guest-1", "max_epoch": 0}, open(f"{self.state}/identity.json", "w"))
        self.clients = []
        self.ex = de.Executor(self.state, self.ws)

    def tearDown(self):
        for c in self.clients:
            c.close()
        for cid in list(self.ex.running):
            self.ex.running[cid].cancel.set()
        end = time.monotonic() + 10
        while self.ex.running and time.monotonic() < end:
            time.sleep(0.05)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self, epoch, bot=BOT):
        c = Client(self.ex)
        self.clients.append(c)
        c.send(type="hello", proto=1, bot_id=bot, epoch=epoch, nonce=f"n{epoch}")
        return c

    def hello(self, epoch):
        c = self.connect(epoch)
        ack = c.of_type("hello_ack")
        self.assertEqual(ack["epoch"], epoch)
        return c, ack

    def run_cmd(self, c, cid, argv, epoch, deadline=20000, task="t1", **over):
        digest = de.exec_digest(BOT, task, cid, deadline, argv)
        m = dict(type="exec", epoch=epoch, bot_id=BOT, task_id=task, command_id=cid, argv=argv, deadline_ms=deadline, digest=digest)
        m.update(over)
        return c.send(**m)

    def sh(self, c, cid, script, epoch, **kw):
        return self.run_cmd(c, cid, ["/bin/sh", "-c", script], epoch, **kw)


class Protocol(unittest.TestCase):
    def test_digest_vector(self):
        # The Rust host asserts the same value. Change both together.
        self.assertEqual(de.exec_digest("bot-a", "task-1", "cmd-1", 5000, ["/bin/echo", "héllo"]),
                         "62c9fd2f2cc72285cf2e5205394a7022d26edbfbf0028bc8045b69df2bd479ae")

    def test_frames(self):
        r = de.FrameReader()
        raw = de.encode_frame({"a": 1}) + de.encode_frame({"b": 2})
        self.assertEqual(r.feed(raw[:5]), [])
        self.assertEqual(r.feed(raw[5:]), [{"a": 1}, {"b": 2}])
        for bad in (b"XX\x01\x00\x00\x00\x02{}", b"DK\x09\x00\x00\x00\x02{}", b"DK\x01\xff\xff\xff\xff", b"DK\x01\x00\x00\x00\x03[1]", b"DK\x01\x00\x00\x00\x01x"):
            with self.assertRaises(de.ProtocolError):
                de.FrameReader().feed(bad)
        with self.assertRaises(ValueError):
            de.encode_frame({"x": "a" * de.MAX_FRAME})


class Behavior(Base):
    def test_unprovisioned_and_wrong_bot_and_stale_hello(self):
        os.unlink(f"{self.state}/identity.json")
        ex = de.Executor(self.state, self.ws)
        c = Client(ex)
        self.clients.append(c)
        c.send(type="hello", proto=1, bot_id=BOT, epoch=1, nonce="n")
        self.assertEqual(c.of_type("error")["code"], "unprovisioned")
        c = self.connect(1, bot="other")
        self.assertEqual(c.of_type("error")["code"], "wrong_bot")
        self.hello(5)
        c = self.connect(5)
        self.assertEqual(c.of_type("error")["code"], "stale_epoch")
        c = self.connect(3)
        self.assertEqual(c.of_type("error")["code"], "stale_epoch")
        c, ack = self.hello(6)
        self.assertEqual(ack["max_epoch_before"], 5)

    def test_epoch_fences_commands(self):
        c, _ = self.hello(2)
        self.sh(c, "c1", "echo hi", epoch=1)
        self.assertEqual(c.of_type("error")["code"], "stale_epoch")
        c.send(type="status", epoch=1, command_id="c1")
        self.assertEqual(c.of_type("error")["code"], "stale_epoch")
        c.send(type="status", epoch=2, command_id="c1")
        self.assertFalse(c.of_type("status_reply")["found"])  # the stale exec never ran

    def test_exec_and_result(self):
        c, _ = self.hello(1)
        self.sh(c, "c1", "echo out; echo err >&2; exit 3", epoch=1)
        self.assertEqual(c.of_type("exec_ack")["state"], "running")
        r = c.of_type("result")["receipt"]
        self.assertEqual((r["state"], r["exit_code"], r["stdout"], r["stderr"]), ("failed", 3, "out\n", "err\n"))
        self.assertTrue(r["group_confirmed"])
        self.assertNotIn("pgid", r)

    def test_duplicate_delivery_runs_once(self):
        c, _ = self.hello(1)
        marker = f"{self.tmp}/count"
        for i in range(3):
            self.sh(c, "dup", f"echo x >> {marker}", epoch=1)
        acks = [c.of_type("exec_ack") for _ in range(3)]
        self.assertEqual([a["duplicate"] for a in acks], [False, True, True])
        c.of_type("result")
        self.sh(c, "dup", f"echo x >> {marker}", epoch=1)
        late = c.of_type("exec_ack")
        self.assertTrue(late["duplicate"])
        self.assertEqual(late["state"], "succeeded")
        self.assertEqual(Path(marker).read_text(), "x\n")

    def test_same_id_changed_args_rejected(self):
        c, _ = self.hello(1)
        self.sh(c, "c1", "true", epoch=1)
        c.of_type("exec_ack")
        c.of_type("result")
        self.sh(c, "c1", "false", epoch=1)
        self.assertEqual(c.of_type("error")["code"], "payload_conflict")
        self.sh(c, "c1", "true", epoch=1, deadline=21000)  # a different deadline is a different payload
        self.assertEqual(c.of_type("error")["code"], "payload_conflict")

    def test_bad_digest_and_wrong_bot_and_bad_input(self):
        c, _ = self.hello(1)
        self.sh(c, "c1", "true", epoch=1, digest="0" * 64)
        self.assertEqual(c.of_type("error")["code"], "bad_digest")
        self.sh(c, "c1", "true", epoch=1, bot_id="other")
        self.assertEqual(c.of_type("error")["code"], "wrong_bot")
        self.sh(c, "../x", "true", epoch=1)
        self.assertEqual(c.of_type("error")["code"], "bad_request")
        self.run_cmd(c, "c2", [], epoch=1)
        self.assertEqual(c.of_type("error")["code"], "bad_request")
        self.sh(c, "c3", "true", epoch=1, deadline=de.MAX_DEADLINE_MS + 1)
        self.assertEqual(c.of_type("error")["code"], "bad_request")
        self.assertIsNone(self.ex.load("c1"))

    def test_status_while_running_then_cancel_stops_process_tree(self):
        c, _ = self.hello(1)
        pidfile = f"{self.tmp}/child.pid"
        self.sh(c, "long", f"sleep 300 & echo $! > {pidfile}; wait", epoch=1)
        c.of_type("exec_ack")
        end = time.monotonic() + WAIT
        while not (os.path.exists(pidfile) and open(pidfile).read().strip()) and time.monotonic() < end:
            time.sleep(0.02)
        child = int(open(pidfile).read())
        self.assertTrue(os.path.exists(f"/proc/{child}"))
        c.send(type="status", epoch=1, command_id="long")
        self.assertEqual(c.of_type("status_reply")["receipt"]["state"], "running")  # status works while it runs
        c.send(type="cancel", epoch=1, command_id="long")
        ack = c.of_type("cancel_ack")
        self.assertEqual((ack["ack"], ack["state"]), ("accepted", "running"))  # accepted is not confirmed
        r = c.of_type("result")["receipt"]
        self.assertEqual(r["state"], "cancelled")
        self.assertTrue(r["cancel"]["confirmed"] and r["group_confirmed"])
        self.assertEqual(de.group_members(child), [])
        self.assertFalse(os.path.exists(f"/proc/{child}") and open(f"/proc/{child}/stat").read().rsplit(")", 1)[1].split()[0] != "Z")

    def test_cancel_unknown_and_finished(self):
        c, _ = self.hello(1)
        c.send(type="cancel", epoch=1, command_id="nope")
        self.assertEqual(c.of_type("cancel_ack")["ack"], "unknown_command")
        self.sh(c, "c1", "true", epoch=1)
        c.of_type("result")
        c.send(type="cancel", epoch=1, command_id="c1")
        ack = c.of_type("cancel_ack")
        self.assertEqual((ack["ack"], ack["state"]), ("already_terminal", "succeeded"))

    def test_term_ignoring_command_is_killed_after_grace(self):
        c, _ = self.hello(1)
        self.sh(c, "stub", "trap '' TERM; while :; do sleep 1; done", epoch=1)
        c.of_type("exec_ack")
        time.sleep(0.3)
        t = time.monotonic()
        c.send(type="cancel", epoch=1, command_id="stub", grace_ms=300)
        c.of_type("cancel_ack")
        r = c.of_type("result")["receipt"]
        self.assertEqual(r["state"], "cancelled")
        self.assertLess(time.monotonic() - t, 8)

    def test_deadline_stops_group(self):
        c, _ = self.hello(1)
        self.sh(c, "dl", "sleep 300 & sleep 300", epoch=1, deadline=400)
        r = c.of_type("result", command_id="dl")["receipt"]
        self.assertEqual(r["state"], "timed_out")
        self.assertTrue(r["group_confirmed"])

    def test_leftover_background_process_is_stopped(self):
        c, _ = self.hello(1)
        pidfile = f"{self.tmp}/bg.pid"
        self.sh(c, "bg", f"sleep 300 >/dev/null 2>&1 & echo $! > {pidfile}", epoch=1)
        r = c.of_type("result")["receipt"]
        self.assertEqual(r["state"], "succeeded")
        self.assertEqual(de.group_members(int(open(pidfile).read())), [])

    def test_large_output_does_not_deadlock_and_is_bounded(self):
        c, _ = self.hello(1)
        self.sh(c, "big", "head -c 5000000 /dev/zero | tr '\\0' a; head -c 3000000 /dev/zero | tr '\\0' b >&2", epoch=1)
        r = c.of_type("result", command_id="big")["receipt"]
        self.assertEqual((r["state"], r["stdout_bytes"], r["stderr_bytes"], r["truncated"]), ("succeeded", 5_000_000, 3_000_000, True))
        self.assertEqual(len(r["stdout"]), de.TAIL_BYTES)
        self.assertLessEqual(os.path.getsize(f"{self.state}/spool/big.out"), de.SPOOL_BYTES)

    def test_receipt_survives_restart_and_running_becomes_interrupted(self):
        c, _ = self.hello(1)
        self.sh(c, "done", "echo ok", epoch=1)
        c.of_type("result")
        self.sh(c, "mid", "sleep 300", epoch=1)
        c.of_type("exec_ack")
        pg = None
        end = time.monotonic() + WAIT
        while not pg and time.monotonic() < end:
            pg = (self.ex.load("mid") or {}).get("pgid")
            time.sleep(0.02)
        self.assertTrue(pg and de.group_members(pg))
        self.ex.save = lambda rc: None  # the old process is gone: it can write nothing more
        self.ex.session = None
        ex2 = de.Executor(self.state, self.ws)  # a restart over the same state
        self.assertEqual(ex2.load("done")["state"], "succeeded")
        mid = ex2.load("mid")
        self.assertEqual((mid["state"], mid["detail"], mid["group_confirmed"]), ("interrupted", "executor_restarted", True))
        self.assertEqual(de.group_members(pg), [])
        self.assertEqual(ex2.ident["max_epoch"], 1)

    def test_reconnect_gets_prior_outcome_and_late_result(self):
        marker = f"{self.tmp}/count"
        c1, _ = self.hello(1)
        self.sh(c1, "slow", f"sleep 1; echo x >> {marker}", epoch=1)
        c1.of_type("exec_ack")
        c1.close()  # the host connection drops while the command runs
        c2, ack = self.hello(2)
        self.assertEqual(ack["max_epoch_before"], 1)
        late = c2.of_type("result", command_id="slow")  # delivered on the new connection
        self.assertEqual((late["epoch"], late["exec_epoch"], late["receipt"]["state"]), (2, 1, "succeeded"))
        self.sh(c2, "slow", f"sleep 1; echo x >> {marker}", epoch=2)  # a resend is not a re-run
        self.assertTrue(c2.of_type("exec_ack")["duplicate"])
        self.assertEqual(Path(marker).read_text(), "x\n")

    def test_artifacts(self):
        c, _ = self.hello(1)
        script = ("echo hello > $DECK_OUT/a.txt; ln -s /etc/passwd $DECK_OUT/link; mkdir $DECK_OUT/sub; "
                  "truncate -s 9000000 $DECK_OUT/huge.bin; echo x > $DECK_OUT/.hidden; echo y > \"$DECK_OUT/b c\"; "
                  "head -c 300000 /dev/urandom > $DECK_OUT/rand.bin")
        self.sh(c, "art", script, epoch=1)
        r = c.of_type("result")["receipt"]
        names = {a["name"]: a for a in r["artifacts"]}
        self.assertEqual(sorted(names), ["a.txt", "rand.bin"])
        why = {x["name"]: x["reason"] for x in r["rejected_artifacts"]}
        self.assertEqual(why, {"link": "symlink", "sub": "not_regular", "huge.bin": "too_large", ".hidden": "bad_name", "b c": "bad_name"})
        self.assertEqual(names["a.txt"]["sha256"], hashlib.sha256(b"hello\n").hexdigest())
        got = b""
        while True:
            c.send(type="artifact_get", epoch=1, command_id="art", handle=names["rand.bin"]["handle"], offset=len(got), length=de.CHUNK_MAX)
            ch = c.of_type("artifact_chunk")
            got += base64.b64decode(ch["data"])
            if ch["eof"]:
                break
        self.assertEqual(hashlib.sha256(got).hexdigest(), names["rand.bin"]["sha256"])
        c.send(type="artifact_get", epoch=1, command_id="art", handle="a0", offset=0, length=de.CHUNK_MAX + 1)
        self.assertEqual(c.of_type("error")["code"], "bad_request")
        c.send(type="artifact_get", epoch=1, command_id="art", handle="a9", offset=0, length=10)
        self.assertEqual(c.of_type("error")["code"], "unknown_artifact")
        c.send(type="artifact_get", epoch=1, command_id="other", handle="a0", offset=0, length=10)
        self.assertEqual(c.of_type("error")["code"], "unknown_artifact")
        out = Path(self.ws, "out", "art", "a.txt")
        out.write_text("changed!\n")  # content changed after registration
        c.send(type="artifact_get", epoch=1, command_id="art", handle="a0", offset=0, length=100)
        self.assertEqual(c.of_type("error")["code"], "artifact_changed")
        out.unlink()
        out.symlink_to("/etc/passwd")  # swapped for a symlink
        c.send(type="artifact_get", epoch=1, command_id="art", handle="a0", offset=0, length=100)
        self.assertEqual(c.of_type("error")["code"], "artifact_changed")

    def test_runs_as_the_executor_user(self):
        c, _ = self.hello(1)
        self.sh(c, "who", "id -u", epoch=1)
        self.assertEqual(c.of_type("result")["receipt"]["stdout"].strip(), str(os.geteuid()))


if __name__ == "__main__":
    unittest.main()
