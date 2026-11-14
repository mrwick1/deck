"""Tests for setup_saved_wake_e2e.py. They start no VM and no browser.

Part one runs the two guest scripts with `sh` in a temporary workspace. Part two checks the model
adapter and the real collaborators on temporary files. Part three drives the whole flow, through the
restart and second-task branches, against a small simulation of Deck and the guest. The simulation is
a MOCK: it proves the checks catch violations, not that Deck behaves this way."""
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import setup_e2e as e
import setup_saved_wake_e2e as w

NONCE = "SWtest0123456789"


def sh(script, ws, env=None):
    r = subprocess.run(["sh", "-c", script], env={"PATH": os.environ["PATH"], "DECK_WORKSPACE": str(ws), **(env or {})}, capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout


class Scripts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        self.mem, self.boot = str(Path(self.tmp.name) / "mem"), Path(self.tmp.name) / "boot"
        self.boot.write_text("boot-A\n")

    def tearDown(self):
        self.tmp.cleanup()

    def one(self):
        return sh(w.script_one(NONCE, self.mem, str(self.boot)), self.ws)

    def two(self):
        return sh(w.script_two(NONCE, self.mem, str(self.boot)), self.ws)

    def log(self):
        return (self.ws / "effects.log").read_text().splitlines()

    def test_task_one_leaves_markers_and_one_effect_and_reports_the_workspace(self):
        code, out = self.one()
        self.assertEqual(code, 0)
        self.assertIn(f"WS={self.ws}", out)
        self.assertEqual((self.ws / "marker.txt").read_text().strip(), NONCE)
        self.assertEqual(Path(self.mem).read_text().strip(), NONCE)
        self.assertEqual(self.log(), [f"first-task-effect {NONCE}"])

    def test_task_two_appends_exactly_one_line_and_reports_the_state(self):
        self.one()
        code, out = self.two()
        self.assertEqual(code, 0)
        self.assertIn("SECOND-OK marker_ok=1 mem_ok=1 boot_same=1", out)
        self.assertEqual(self.log(), [f"first-task-effect {NONCE}", f"second-task-effect {NONCE}"])

    def test_a_missing_marker_appends_nothing(self):
        _, out = self.two()
        self.assertIn("marker_ok=0", out)
        self.assertFalse((self.ws / "effects.log").exists())

    def test_a_cold_boot_and_a_lost_memory_marker_are_reported(self):
        self.one()
        self.boot.write_text("boot-B\n")
        Path(self.mem).unlink()
        _, out = self.two()
        self.assertIn("mem_ok=0 boot_same=0", out)

    def test_the_effect_check_catches_a_duplicate_and_a_missing_line(self):
        self.one()
        self.two()
        self.two()
        facts = {"effects": self.log()}
        self.assertTrue(w.effect_problems(facts, NONCE, ["first", "second"]))
        self.assertFalse(w.effect_problems({"effects": facts["effects"][:2]}, NONCE, ["first", "second"]))
        self.assertTrue(w.effect_problems({"effects": facts["effects"][:1]}, NONCE, ["first", "second"]))
        self.assertTrue(w.effect_problems({"effects": []}, NONCE, ["first"]))


def user(text):
    return {"role": "user", "content": text}


def tool_reply(stdout):
    return {"role": "tool", "tool_call_id": "k1", "content": json.dumps({"ok": True, "stdout": stdout})}


class Adapter(unittest.TestCase):
    def test_each_task_gets_its_script_and_nothing_else(self):
        self.assertEqual(w.decide([user(w.TASK_ONE)], NONCE), {"kind": "tool", "name": "shell", "args": {"script": w.script_one(NONCE)}})
        self.assertEqual(w.decide([user(w.TASK_TWO)], NONCE)["args"], {"script": w.script_two(NONCE)})
        self.assertIn("blocked", w.decide([user("do something else")], NONCE)["text"])

    def test_the_closing_text_needs_the_guest_confirmation(self):
        self.assertEqual(w.decide([user(w.TASK_ONE), tool_reply("FIRST-OK WS=/x boot=b\n")], NONCE)["text"], w.DONE_ONE)
        t = w.decide([user(w.TASK_TWO), tool_reply("SECOND-OK marker_ok=1\n")], NONCE)["text"]
        self.assertTrue(t.startswith(w.DONE_TWO) and "marker_ok=1" in t)
        self.assertIn("blocked", w.decide([user(w.TASK_TWO), tool_reply("")], NONCE)["text"])
        self.assertIn("blocked", w.decide([user(w.TASK_TWO), {"role": "tool", "content": "not json"}], NONCE)["text"])

    def test_the_server_keeps_a_ledger_and_the_workspace(self):
        m = w.Model(NONCE)
        m.start()
        try:
            def post(history):
                r = urllib.request.Request(f"http://127.0.0.1:{m.port}/v1/chat/completions", data=json.dumps({"messages": history}).encode())
                r.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(r, timeout=5) as res:
                    return res.read().decode()
            self.assertIn('"shell"', post([user(w.TASK_ONE)]))
            self.assertIn(w.DONE_ONE, post([user(w.TASK_ONE), tool_reply("FIRST-OK WS=/home/deck/workspace boot=b\n")]))
            self.assertEqual((m.calls, [x["name"] for x in m.ledger], m.workspace), (2, ["shell"], "/home/deck/workspace"))
        finally:
            m.stop()

    def test_guest_facts_parse_effect_lines_after_the_marker_line(self):
        f = w.parse_guest("marker=A\nmem=\nboot=b\nuptime=1.5\neffects_begin\nfirst-task-effect A\n\nsecond-task-effect A\n")
        self.assertEqual((f["marker"], f["mem"], f["effects"]), ("A", "", ["first-task-effect A", "second-task-effect A"]))


class RealCollaborators(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.sid = "a" * 32
        self.rt = self.out / "computers"
        d = self.rt / self.sid
        (d / "run").mkdir(parents=True)
        (d / "state").mkdir()
        (d / "executor.json").write_text(json.dumps({"bot_id": self.sid, "guest_id": "g1"}))
        self.stack = SimpleNamespace(out=self.out, rt=self.rt, owned={}, client=None)
        self.world = w.World(self.stack, self.sid, SimpleNamespace(workspace="/home/deck/workspace"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_identity_comes_from_the_executor_file(self):
        self.assertEqual(self.world.identity(), {"bot_id": self.sid, "guest_id": "g1"})

    def test_vm_reports_no_process_and_no_artifact_by_default(self):
        self.assertEqual(self.world.vm(), {"running": False, "pid": None, "state_saved": False, "state_bytes": 0, "state_meta": None, "state_meta_sha256": None})

    def test_vm_reports_the_saved_state_artifact(self):
        (self.rt / self.sid / "state" / "vm.state").write_bytes(b"x" * 10)
        (self.rt / self.sid / "state" / "state.json").write_text(json.dumps({"codec": "none"}))
        got = self.world.vm()
        self.assertEqual((got["state_saved"], got["state_bytes"], got["state_meta"], len(got["state_meta_sha256"])), (True, 10, {"codec": "none"}, 64))

    def test_journal_reads_the_epoch_and_commands_read_only(self):
        (self.out / "data").mkdir()
        con = sqlite3.connect(self.out / "data" / "deck.db")
        con.executescript("CREATE TABLE executor_bots(bot_id TEXT PRIMARY KEY, epoch INTEGER, guest_id TEXT);"
                          "CREATE TABLE executor_commands(command_id TEXT PRIMARY KEY, bot_id TEXT, task_id TEXT, state TEXT, dispatch_epoch INTEGER);")
        con.execute("INSERT INTO executor_bots VALUES (?,?,?)", (self.sid, 3, "g1"))
        con.execute("INSERT INTO executor_commands VALUES ('c1',?, 't1','succeeded',3)", (self.sid,))
        con.commit()
        con.close()
        self.assertEqual(self.world.journal(), {"epoch": 3, "guest_id": "g1", "commands": [["t1", "succeeded", 3]]})

    def test_guest_reads_through_qga_and_refuses_an_unreported_workspace(self):
        with patch.object(w.v, "Qga") as qga:
            qga.return_value.sh.return_value = (0, f"marker={NONCE}\nmem={NONCE}\nboot=b\nboot_first=b\nuptime=9\nfirst_uptime=3\neffects_begin\nx\n", "")
            got = self.world.guest()
            self.assertEqual((got["marker"], got["effects"]), (NONCE, ["x"]))
            self.assertIn("W=/home/deck/workspace;", qga.return_value.sh.call_args[0][0])
            qga.return_value.sh.return_value = (1, "", "")
            with self.assertRaises(RuntimeError):
                self.world.guest()
        with self.assertRaises(RuntimeError):
            w.World(self.stack, self.sid, SimpleNamespace(workspace=None)).guest()
        with self.assertRaises(RuntimeError):
            w.World(self.stack, self.sid, SimpleNamespace(workspace="/x; rm -rf /")).guest()

    def test_restart_stops_starts_and_waits_for_the_bot(self):
        calls = []
        client = SimpleNamespace(raw=lambda *a: (200, b""), until=lambda what, cond, t: calls.append(cond()))
        stack = SimpleNamespace(out=self.out, rt=self.rt, owned={}, client=client, stop=lambda n: calls.append(("stop", n)), start_server=lambda: calls.append("start"))
        w.World(stack, self.sid, None).restart()
        self.assertEqual(calls, [("stop", "server"), "start", True])


# ---------- the simulation ----------

class Sim:
    """Deck and its guest in one object. Fault names turn on one violation each."""

    def __init__(self, **faults):
        self.f = faults
        self.model = SimpleNamespace(calls=0, ledger=[], workspace="/ws", outputs=[], texts=[])
        self.computer, self.paused, self.idle = "awake", False, 0
        self.tasks = [{"id": "setup-S", "state": "running"}]
        self.pc = self.fresh()
        self.effects, self.boot, self.boot_first, self.mem, self.uptime, self.first_uptime = [], "boot-1", "boot-1", "", 100.0, 100.0
        self.pid, self.epoch, self.guest_id, self.state, self.n = 1000, 1, "g1", None, 0
        self.exposure = {"unresolved": 0}

    @staticmethod
    def fresh():
        return {"model_calls": 0, "wakes": 0, "saves": 0, "connects": 1, "captures": 0}

    def status(self):
        if self.computer == "saved" and self.f.get("noisy"):
            self.pc["model_calls"] += 1
        return {"computer": self.computer, "automation_paused": self.paused, "holds": [{"kind": "automation_pause", "reason": None}] if self.paused else [], "working": None,
                "counters": {k: self.pc[k] for k in ("model_calls", "wakes", "saves", "connects")}, "screen": {"captures": self.pc["captures"]},
                "tasks": [dict(t) for t in self.tasks], "exposure": self.exposure,
                "accounting": {"currency": "TST", "exponent": 2, "period": "e2e", "policy_version": "e2e-1", "ceiling": 900 if self.f.get("ceiling") and self.n else 1000, "allowance": 100, "version": 1}}

    def run(self, task):
        if self.f.get("unknown") and task["id"] != "setup-S":
            task["state"] = "failed"
            return
        first = task["id"] == "setup-S"
        self.model.calls += 2
        self.pc["model_calls"] += 2
        self.model.ledger.append({"name": "shell"})
        if first:
            self.effects, self.mem, self.boot_first, self.first_uptime = [f"first-task-effect {NONCE}"], NONCE, self.boot, self.uptime
        else:
            self.effects += [f"second-task-effect {NONCE}"] * (2 if self.f.get("dup_effect") else 1)
        self.model.outputs.append("SECOND-OK" if not first else "FIRST-OK")
        task["state"] = "done"
        self.idle = 0

    def wake(self):
        self.computer, self.state, self.pid, self.pc["wakes"], self.epoch = "awake", None, self.pid + 1, self.pc["wakes"] + 1, self.epoch + (0 if self.f.get("epoch") else 1)
        if self.f.get("new_guest"):
            self.guest_id = "g2"
        if self.f.get("cold_boot"):
            self.boot, self.mem, self.uptime = "boot-2", "", 5.0
        else:
            self.uptime += 50

    def tick(self):
        queued = [t for t in self.tasks if t["state"] == "queued"]
        running = [t for t in self.tasks if t["state"] == "running"]
        if running:
            self.run(running[0])
        elif queued and not self.paused:
            self.n += 1
            if self.computer == "saved":
                self.wake()
            self.run(queued[0])
        elif self.computer == "awake":
            self.idle += 1
            if self.idle >= 2:
                self.computer, self.state, self.pc["saves"] = "saved", {"sha": "S1"}, self.pc["saves"] + 1


class FakeClient:
    def __init__(self, sim):
        self.sim = sim

    def until(self, what, cond, timeout, every=0.5):
        for _ in range(20):
            self.sim.tick()
            got = cond()
            if got:
                return got
        raise SystemExit(f"FAILED: timed out waiting for {what}")

    def setup(self, sid):
        return {"phase": "Ready", "runtime_ready": True, "setup": {"id": sid}}

    def json(self, method, path, body=None):
        return self.sim.status()

    def raw(self, method, path, body=None):
        s = self.sim
        if path.endswith("/automation"):
            s.paused = body["paused"]
            return 204, b""
        if path.endswith("/messages"):
            s.tasks.append({"id": "task-2", "state": "queued"})
            return 202, json.dumps({"task_id": "task-2", "kind": "queued", "held": "automation paused" if s.paused else None}).encode()
        return 200, b""


class FakeWorld:
    def __init__(self, sim):
        self.sim, self.model = sim, sim.model

    def vm(self):
        s = self.sim
        return {"running": s.computer == "awake", "pid": s.pid if s.computer == "awake" else None, "state_saved": bool(s.state), "state_bytes": 10 if s.state else 0,
                "state_meta": {"codec": "none"} if s.state else None, "state_meta_sha256": s.state["sha"] if s.state else None}

    def identity(self):
        return {"bot_id": "S", "guest_id": self.sim.guest_id}

    def journal(self):
        return {"epoch": self.sim.epoch, "guest_id": self.sim.guest_id, "commands": []}

    def guest(self):
        s = self.sim
        if s.computer != "awake":
            raise RuntimeError("no guest to read: the computer is saved")
        return {"marker": NONCE, "mem": s.mem, "boot": s.boot, "boot_first": s.boot_first, "uptime": str(s.uptime), "first_uptime": str(s.first_uptime), "effects": list(s.effects)}

    def restart(self):
        s = self.sim
        s.pc = s.fresh()
        if s.f.get("drop_task"):
            s.tasks = [t for t in s.tasks if t["state"] != "queued"]
        if s.f.get("dup_task"):
            s.tasks += [dict(t) for t in s.tasks if t["state"] == "queued"]
        if s.f.get("wake_on_restart") and s.computer == "saved":
            s.wake()


ARGS = SimpleNamespace(ready_timeout=1, task_timeout=1, idle_secs=0, settle=0)


def run_flow(**faults):
    sim = Sim(**faults)
    m = e.Manifest("saved-wake")
    stack = SimpleNamespace(client=FakeClient(sim))
    try:
        w.flow(stack, m, ARGS, FakeWorld(sim), "S", NONCE)
    except e.Stop:
        pass
    return m, sim


def failed(m):
    return sorted(n for n, g in m.data["gates"].items() if g["status"] == e.FAIL)


class FullFlow(unittest.TestCase):
    def test_a_correct_run_passes_every_required_gate_and_leaves_one_optional_gate_open(self):
        m, sim = run_flow()
        self.assertEqual(failed(m), [])
        missing = [g for g in w.REQUIRED if g not in m.data["gates"] and g not in ("base_frozen_and_verified", "base_unchanged", "no_owned_process_left")]
        self.assertEqual(missing, [])
        self.assertEqual(m.open_gates(), ["raster_secrecy"])
        self.assertEqual(sim.effects, [f"first-task-effect {NONCE}", f"second-task-effect {NONCE}"])
        self.assertEqual((sim.model.calls, sim.pid), (4, 1001))
        self.assertEqual([t["id"] for t in sim.tasks], ["setup-S", "task-2"])

    def test_the_closing_gate_passes_only_when_the_harness_gates_also_pass(self):
        m, _ = run_flow()
        for g in ("base_frozen_and_verified", "base_unchanged", "no_owned_process_left"):
            m.gate(g, e.PASS)
        m.require(w.REQUIRED, ["raster_secrecy"])
        self.assertEqual(m.data["gates"]["acceptance_complete"]["status"], e.PASS)
        m2, _ = run_flow(cold_boot=True)
        m2.require(w.REQUIRED, ["raster_secrecy"])
        self.assertEqual(m2.data["gates"]["acceptance_complete"]["status"], e.FAIL)

    def test_a_cold_boot_instead_of_a_restore_fails(self):
        m, _ = run_flow(cold_boot=True)
        self.assertIn("wake_restored_not_cold_boot", failed(m))
        self.assertNotIn("second_task_done", [n for n, g in m.data["gates"].items() if g["status"] == e.FAIL])

    def test_a_duplicate_second_effect_fails(self):
        m, _ = run_flow(dup_effect=True)
        self.assertEqual(failed(m), ["second_task_exactly_one_effect"])

    def test_a_changed_guest_identity_fails(self):
        m, _ = run_flow(new_guest=True)
        self.assertIn("executor_identity_unchanged", failed(m))

    def test_an_epoch_that_did_not_advance_fails(self):
        m, _ = run_flow(epoch=True)
        self.assertEqual(failed(m), ["executor_epoch_advanced"])

    def test_a_changed_ceiling_fails(self):
        m, _ = run_flow(ceiling=True)
        self.assertEqual(failed(m), ["limits_and_holds_preserved"])

    def test_an_unknown_outcome_stops_the_flow_without_a_retry(self):
        m, sim = run_flow(unknown=True)
        self.assertIn("no_unknown_outcome", failed(m))
        self.assertEqual(sim.model.calls, 2)
        self.assertNotIn("guest_state_after_wake", m.data["gates"])

    def test_activity_while_saved_fails_the_quiet_gate(self):
        m, _ = run_flow(noisy=True)
        self.assertIn("quiet_while_saved_after_restart", failed(m))

    def test_a_wake_caused_by_a_restart_fails_before_the_second_task(self):
        m, sim = run_flow(wake_on_restart=True)
        self.assertIn("restart_while_saved_keeps_artifact", failed(m))
        self.assertNotIn("second_task_held_not_dispatched", m.data["gates"])

    def test_a_dropped_or_duplicated_held_task_fails_after_the_held_restart(self):
        self.assertIn("held_restart_keeps_queued_task", failed(run_flow(drop_task=True)[0]))
        self.assertIn("held_restart_keeps_queued_task", failed(run_flow(dup_task=True)[0]))

    def test_the_flow_never_dispatches_the_second_task_while_paused(self):
        m, sim = run_flow()
        self.assertEqual(m.data["gates"]["second_task_held_not_dispatched"]["status"], e.PASS)
        self.assertEqual(m.data["gates"]["held_second_task_starts_nothing"]["status"], e.PASS)


class Entry(unittest.TestCase):
    def test_arguments_need_a_base_a_binary_and_an_output_directory(self):
        with self.assertRaises(SystemExit):
            w.parse([])
        a = w.parse(["--base", "b", "--deck", "d", "--out", "o"])
        self.assertEqual((a.max_load, a.min_avail_mib), (8.0, 6144))

    def test_the_stack_accepts_the_adapter_and_cleans_it_up(self):
        with tempfile.TemporaryDirectory() as d:
            a = w.parse(["--base", "b", "--deck", "d", "--out", d])
            (Path(d) / "private").mkdir()
            stack = e.Stack(a)
            stack.model = w.Model(NONCE, port=0)
            stack.model.start()
            self.assertEqual(stack.cleanup(), [])


if __name__ == "__main__":
    unittest.main()
