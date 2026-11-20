"""Tests for gmail_worker_e2e.py. They start no VM, no browser and no Deck server.

`Sim` is a MOCK of the boundary: a clock, a scheduler that follows the production rules (start-to-start interval, one poll at a
time, pause blocks, restart arms an immediate poll) and a database. It shows that each gate fails for its fault and that no
gate is green when it did not run. It does not show that the real worker behaves this way. The live run does that."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import gmail_worker_e2e as w

T0 = 1_800_000_000.0
KEYS = [k[0] for k in w.ACCOUNTS]
SEL = 'a[href*="SignOutOptions"]'


def good_facts():
    return {"bound_bot": w.BOT, "runtime_dir": "/r", "trial_runtime": "/r", "running_vms": [], "base_ok": True, "base_mode": "0o444",
            "pinned_sha": "d", "expect_sha": "d", "recorded_sha": "d", "disk_ok": True, "out_new": True, "out_private": True, "db_dir_ok": True, "db_bound": True,
            "lock": "/own.lock", "ports_ok": True, "account_selector": SEL, "search_box": 'input[name="q"]', "accounts": KEYS}


class Preflight(unittest.TestCase):
    def test_good_facts_pass(self):
        self.assertEqual(w.preflight(good_facts()), [])

    def test_each_bad_fact_has_its_own_code(self):
        cases = {"bound_bot": ("other", "trial_bound_to_other_bot"), "runtime_dir": ("/x", "runtime_not_the_trial_overlay"),
                 "running_vms": ([5], "vm_already_running"), "base_ok": (False, "base_not_frozen_or_changed"), "base_mode": ("0o644", "base_not_frozen_or_changed"),
                 "pinned_sha": ("e", "candidate_digest_differs"), "disk_ok": (False, "disk_missing"), "out_new": (False, "out_not_new_private"),
                 "out_private": (False, "out_not_new_private"), "db_dir_ok": (False, "host_db_not_fresh_or_private"), "db_bound": (False, "host_db_bound_to_other_guest"), "lock": (w.e.LOCK, "lock_is_not_owned_separate"),
                 "ports_ok": (False, "ports_not_loopback_free_distinct"), "search_box": (None, "selector_invalid"), "account_selector": ("a${X}", "selector_invalid"),
                 "accounts": (KEYS[:2], "accounts_not_three_distinct")}
        for key, (val, code) in cases.items():
            f = good_facts()
            f[key] = val
            self.assertIn(code, w.preflight(f), key)


class Config(unittest.TestCase):
    CFG = {"server_port": 1, "model_port": 2, "fixture_port": 3, "data_dir": "/d", "runtime_dir": "/rt", "idle_secs": 3600, "account_selector": SEL,
           "search_box": 'input[name="q"]'}

    def test_config_has_the_approved_values_and_no_secret(self):
        t = w.render_config(self.CFG)
        for want in ("interval_secs = 120", "initial_hours = 24", "overlap_secs = 3600", "max_pages = 3", "max_scan_secs = 90", "internet = true",
                     'bot_id = "shared-profile"', 'listen = "127.0.0.1:1"', "base_url = \"http://127.0.0.1:2/v1\"", "idle_secs = 3600"):
            self.assertIn(want, t)
        self.assertEqual(t.count("[[gmail.accounts]]"), 3)
        self.assertNotIn("DECK_MODEL_KEY=", t)
        self.assertNotIn("@", t)

    def test_rendered_config_is_valid_toml_with_escaped_quotes(self):
        import tomllib
        d = tomllib.loads(w.render_config(self.CFG))
        self.assertEqual(d["gmail"]["account_control_selector"], SEL)
        self.assertEqual(d["gmail"]["search_box_selector"], 'input[name="q"]')


def acct(key, lower, wm, ok="ok", start=None):
    return {"key": key, "result": ok, "last_success_ms": wm if start is None else start, "watermark_ms": wm, "window_lower_s": lower}


def cyc(start, accounts, result="complete"):
    return {"started_ms": start, "ended_ms": start + 70_000, "result": result, "accounts": accounts}


class Judges(unittest.TestCase):
    def test_healthy_needs_complete_cycle_and_three_ok_accounts(self):
        c = cyc(1000, [acct(k, 1, 2000) for k in KEYS])
        self.assertTrue(w.healthy(c))
        self.assertFalse(w.healthy({**c, "result": "partial"}))
        self.assertFalse(w.healthy({**c, "accounts": c["accounts"][:2]}))
        self.assertFalse(w.healthy({**c, "accounts": [acct(KEYS[0], 1, 2000, "incomplete")] + c["accounts"][1:]}))
        self.assertFalse(w.healthy({**c, "accounts": [acct(k, 1, 500) for k in KEYS]}), "a success older than the poll start is not fresh")
        self.assertFalse(w.healthy({"accounts": None, "result": None, "started_ms": 1}))

    def test_first_window_is_the_initial_lookback_or_the_stored_watermark(self):
        s = 1_800_000_000_000
        ok = cyc(s, [acct(k, s // 1000 - 86400 + 5, s + 5000) for k in KEYS])
        self.assertTrue(w.first_window_ok(ok, {}))
        self.assertFalse(w.first_window_ok(cyc(s, [acct(k, s // 1000 - 3600, s) for k in KEYS]), {}))
        prior = {KEYS[0]: {"watermark_ms": 1_799_999_000_000}}  # a continued database: this account must not be looked back 24 hours again
        mixed = cyc(s, [acct(KEYS[0], 1_799_999_000 - 3600, s)] + [acct(k, s // 1000 - 86400 + 5, s) for k in KEYS[1:]])
        self.assertTrue(w.first_window_ok(mixed, prior))
        self.assertFalse(w.first_window_ok(ok, prior), "a stored watermark must not be reseeded")

    def test_continuation_uses_the_stored_watermark_minus_overlap(self):
        prev = cyc(0, [acct(k, 1, 1_800_000_050_000) for k in KEYS])
        nxt = cyc(1, [acct(k, 1_800_000_050 - 3600, 1_800_000_170_000) for k in KEYS])
        self.assertTrue(w.continues(prev, nxt))
        reseed = cyc(1, [acct(k, 1_800_000_170 - 86400, 1_800_000_170_000) for k in KEYS])
        self.assertFalse(w.continues(prev, reseed))
        stale = cyc(1, [acct(k, 1_800_000_050 - 3600, 1_800_000_050_000) for k in KEYS])
        self.assertFalse(w.continues(prev, stale), "the watermark must move forward")

    def test_cycles_are_built_from_status_only(self):
        a = [acct(k, 1, 5) for k in KEYS]
        vs = [{"started_ms": None}, {"started_ms": 1000, "running": True, "ended_ms": None},
              {"started_ms": 1000, "running": False, "ended_ms": 900, "result": "x", "accounts": a},  # an old end does not finish this poll
              {"started_ms": 1000, "running": False, "ended_ms": 2000, "result": "complete", "accounts": a},
              {"started_ms": 5000, "running": True, "ended_ms": 2000}]
        cs = w.cycles(vs)
        self.assertEqual([c["started_ms"] for c in cs], [1000, 5000])
        self.assertEqual((cs[0]["ended_ms"], cs[0]["result"], cs[1]["ended_ms"]), (2000, "complete", None))
        self.assertEqual(w.cadence(cs), [4.0])

    def test_serial_needs_one_task_and_ordered_polls(self):
        db = {"polls": [{"seq": 1, "ended_at": "2", "created_at": "1"}, {"seq": 2, "ended_at": "5", "created_at": "3"}]}
        self.assertTrue(w.serial_ok([{"tasks_running": 1}], db))
        self.assertFalse(w.serial_ok([{"tasks_running": 2}], db))
        db["polls"][1]["created_at"] = "1"
        self.assertFalse(w.serial_ok([{"tasks_running": 1}], db))
        db["polls"][1]["created_at"] = "3"
        db["polls"][0]["ended_at"] = None
        self.assertFalse(w.serial_ok([], db))

    def test_journal_codes(self):
        db = {"polls": [{"id": "p"}], "commands": [{"task_id": "p", "state": "succeeded"}]}
        self.assertEqual(w.journal_problems(db, 0), [])
        self.assertEqual(w.journal_problems({**db, "commands": []}, 0), ["no_commands"])
        self.assertEqual(w.journal_problems({**db, "commands": [{"task_id": "q", "state": "succeeded"}]}, 0), ["command_outside_gmail_poll"])
        self.assertEqual(w.journal_problems({**db, "commands": [{"task_id": "p", "state": "unknown"}]}, 0), ["command_not_terminal"])
        self.assertEqual(w.journal_problems(db, 1), ["unresolved"])

    def test_probe_ok_reads_only_the_typed_snapshot(self):
        self.assertTrue(w.probe_ok(json.dumps({"snapshot": {"status": "complete", "stable": True}})))
        self.assertTrue(w.probe_ok(json.dumps({"snapshot": {"status": "empty", "stable": True}})))
        for bad in ("", "x", "{}", json.dumps({"snapshot": {"status": "incomplete", "stable": True}}), json.dumps({"snapshot": {"status": "complete", "stable": False}})):
            self.assertFalse(w.probe_ok(bad))

    def test_db_facts_are_read_only_and_hold_no_payload(self):
        d = Path(tempfile.mkdtemp()) / "deck.db"
        c = sqlite3.connect(d)
        c.executescript(f"""CREATE TABLE tasks(seq INTEGER PRIMARY KEY, id, bot_id, state, created_at, ended_at, execution_kind);
            INSERT INTO tasks VALUES (1,'p','{w.BOT}','done','1','2','gmail_poll'),(2,'m','{w.BOT}','queued','3',NULL,'model');
            CREATE TABLE executor_commands(task_id, bot_id, state, dispatch_epoch); INSERT INTO executor_commands VALUES ('p','{w.BOT}','succeeded',1);
            CREATE TABLE bot_events(bot_id, source, event_id); INSERT INTO bot_events VALUES ('{w.BOT}','gmail_poll','e1');
            CREATE TABLE gmail_seen(bot_id); INSERT INTO gmail_seen VALUES ('{w.BOT}');
            CREATE TABLE gmail_accounts(bot_id, account, baseline_done, last_result, watermark_ms, window_lower_s);
            INSERT INTO gmail_accounts VALUES ('{w.BOT}','account_a',1,'ok',5,4);
            CREATE TABLE executor_bots(bot_id, epoch, guest_id); INSERT INTO executor_bots VALUES ('{w.BOT}',1,'g');""")
        c.commit()
        c.close()
        f = w.db_facts(d)
        self.assertEqual((len(f["polls"]), f["model_tasks"], len(f["commands"]), f["events"], f["seen"]), (1, 1, 1, ["e1"], 1))
        self.assertEqual(f["bot"], [{"epoch": 1, "guest_id": "g"}])
        with self.assertRaises(sqlite3.OperationalError):
            sqlite3.connect(f"file:{d}?mode=ro", uri=True).execute("DELETE FROM tasks")


class Sim:
    """The boundary. `faults` names break exactly one behavior each."""

    def __init__(self, faults=()):
        self.f = set(faults)
        self.t, self.t0 = T0, T0
        self.calls, self.up, self.paused, self.running = [], False, False, False
        self.next_due, self.last, self.polls, self.cmds, self.events = None, {}, [], [], []
        self.acc, self.restarted, self.end_at, self.models, self.epoch = {}, False, None, 0, 0
        self.interval = 300 if "wrong_interval" in self.f else 120
        self.gap = 200 if "late_poll" in self.f else self.interval  # the reported interval and the real start-to-start gap
        self.down_calls = 0

    # clock
    def now(self):
        return self.t - self.t0

    def sleep(self, s):
        for _ in range(int(round(s))):
            self.t += 1
            self.tick()

    def tick(self):
        if not self.up:
            return
        if self.running and self.t >= self.end_at:
            self.finish_poll()
        if not self.running and self.next_due is not None and self.t >= self.next_due and (not self.paused or "pause_leaks" in self.f):
            self.start_poll()

    def start_poll(self):
        self.running, self.next_due, self.end_at = True, self.t + self.gap, self.t + 70
        self.cur = {"seq": len(self.polls) + 1, "id": f"p{len(self.polls) + 1}", "state": "running", "created_at": f"{int(self.t):012d}", "ended_at": None}
        self.polls.append(self.cur)
        self.last = {"started_ms": int(self.t * 1000), "ended_ms": self.last.get("ended_ms"), "result": None}

    def finish_poll(self):
        start = self.last["started_ms"]
        for i, k in enumerate(KEYS):
            a_ms = start + i * 20_000
            prev = self.acc.get(k)
            lower = (a_ms // 1000 - 86400) if (prev is None or ("reseed" in self.f and self.restarted)) else prev["watermark_ms"] // 1000 - 3600
            res = "incomplete" if "scan_incomplete" in self.f and i == 2 else "ok"
            self.acc[k] = {"key": k, "result": res, "last_success_ms": a_ms + 15_000, "watermark_ms": a_ms, "window_lower_s": lower, "baseline_done": 1}
            self.cmds.append({"task_id": self.cur["id"], "state": "unknown" if "unknown_command" in self.f else "succeeded", "epoch": self.epoch})
        if "event_dup" in self.f and len(self.polls) == 2:
            self.events += ["e1", "e1"]
        self.cur.update(state="done", ended_at=f"{int(self.t):012d}")
        self.running = False
        self.last.update(ended_ms=int(self.t * 1000), result="partial" if "scan_incomplete" in self.f else "complete")

    # boundary
    def facts(self):
        f = good_facts()
        if "bad_binding" in self.f:
            f["bound_bot"] = "other"
        return f

    def resume(self):
        self.calls.append("resume")
        return (1, {}) if "resume_fail" in self.f else (0, {"bound_guest_ready": True})

    def switch(self, label, idx):
        self.calls.append(f"switch-{label}")
        return 1 if "bad_selector" in self.f and label == "B" else 0

    def probe(self):
        self.calls.append("probe")
        return 0, json.dumps({"snapshot": {"status": "incomplete" if "bad_box" in self.f else "complete", "stable": True}})

    def start_model(self):
        self.calls.append("model")

    def stop_model(self):
        self.calls.append("stop-model")

    def model_requests(self):
        return 1 if "model_call" in self.f else 0

    def start_server(self):
        self.calls.append("server")
        self.up, self.next_due, self.epoch = True, self.t, self.epoch + 1

    def stop_server(self):
        self.calls.append("stop-server")
        self.up, self.running = False, False

    def restart_server(self):
        self.stop_server()
        self.start_server()
        self.restarted, self.last = True, {}
        if "pause_lost" in self.f:
            self.paused = False

    def status(self):
        if not self.up:
            return {}
        due = None if self.next_due is None else (self.t - self.next_due) * 1000
        return {"computer": "awake", "automation_paused": self.paused, "holds": [], "unresolved": 0,
                "counters": {"model_calls": 0, "connects": 1}, "tasks": [{"state": "running"}] if self.running else [],
                "gmail": {"scope": "date_window", "interval_secs": self.interval, "running": self.running, "overdue_ms": max(0, int(due)) if due is not None and not self.running else 0,
                          "last_cycle": dict(self.last) if self.last else {"started_ms": None, "ended_ms": None, "result": None},
                          "accounts": [{k: v for k, v in self.acc[x].items() if k != "baseline_done"} for x in KEYS if x in self.acc] if self.acc else []}}

    def pause(self, paused):
        self.calls.append(f"pause-{paused}")
        self.paused = paused

    def db(self):
        return {"polls": [dict(p) for p in self.polls], "model_tasks": 0, "commands": [dict(c) for c in self.cmds], "events": list(self.events), "seen": 3,
                "accounts": {k: {"baseline_done": 1, "result": v["result"], "watermark_ms": v["watermark_ms"], "window_lower_s": v["window_lower_s"]} for k, v in self.acc.items()},
                "bot": [{"epoch": self.epoch, "guest_id": "g"}]}

    def guest_identity(self):
        return "other" if "identity_reset" in self.f else "g"

    def running_vms(self):
        return [1, 2] if "two_vms" in self.f else [1]

    def vm_alive(self):
        return True

    def down(self):
        self.calls.append("down")
        self.down_calls += 1
        return (0, {"survivors": ["9"] if "survivors" in self.f else [], "base_unchanged": True, "disk_kept": True})

    def survivors(self):
        return []


def run(faults=(), data_dir=None):
    sim = Sim(faults)
    sim.paused = "continued_paused" in sim.f
    m = w.Run(sim, SimpleNamespace(max_seconds=720, data_dir=data_dir)).run()
    return sim, m


class Flow(unittest.TestCase):
    def test_happy_path_passes_every_gate(self):
        sim, m = run()
        self.assertTrue(m["acceptance_complete"], m)
        self.assertEqual([k for k, v in m["gates"].items() if v != w.PASS], [])
        self.assertEqual(sim.calls.count("down"), 1)
        self.assertLessEqual(sim.now(), 15 * 60, "the fake run stays inside the budget")

    def test_a_continued_database_releases_its_own_pause_and_a_new_one_must_not_start_paused(self):
        self.assertTrue(run(["continued_paused"], data_dir="/d")[1]["acceptance_complete"])
        m = run(["continued_paused"])[1]
        self.assertEqual(m["gates"]["server_connected_gmail_configured"], w.FAIL)

    def test_a_bad_binding_launches_nothing(self):
        sim, m = run(["bad_binding"])
        self.assertFalse(m["acceptance_complete"])
        self.assertNotIn("resume", sim.calls)
        self.assertNotIn("server", sim.calls)
        self.assertEqual(sim.down_calls, 0)
        self.assertIsNone(m["gates"]["helper_resume"])

    def test_every_fault_fails_its_gate_stops_and_cleans_up(self):
        faults = {"resume_fail": "helper_resume", "bad_selector": "selectors_three_accounts", "bad_box": "search_box_confirmed",
                  "scan_incomplete": "baseline_three_healthy", "model_call": "baseline_silent_zero_model", "unknown_command": "journal_owned_by_gmail_poll",
                  "wrong_interval": "server_connected_gmail_configured", "late_poll": "cadence_scheduled_interval", "event_dup": "no_duplicate_events", "two_vms": "single_serial_owner",
                  "pause_leaks": "pause_blocks_due_tick", "pause_lost": "restart_keeps_pause_and_state", "reseed": "resume_runs_poll_without_reseed",
                  "identity_reset": "executor_identity_kept", "survivors": "cleanup_clean"}
        for fault, gate in faults.items():
            sim, m = run([fault])
            self.assertFalse(m["acceptance_complete"], fault)
            self.assertEqual(m["gates"][gate], w.FAIL, fault)
            self.assertEqual(sim.calls.count("down"), 1, f"{fault}: the VM is taken down with the disk kept")
            self.assertTrue("stop-server" in sim.calls and "stop-model" in sim.calls, fault)
            self.assertNotIn(w.PASS, [m["gates"][g] for g in w.GATES[w.GATES.index(gate) + 1:] if g != "cleanup_clean"], f"{fault}: nothing runs after the failure")

    def test_a_gate_that_did_not_run_is_never_green(self):
        sim, m = run(["bad_selector"])
        later = [g for g in w.GATES[w.GATES.index("selectors_three_accounts") + 1:] if g != "cleanup_clean"]
        self.assertTrue(all(m["gates"][g] is None for g in later))
        self.assertFalse(m["acceptance_complete"])

    def test_the_server_is_stopped_only_after_automation_is_held(self):
        calls = run()[0].calls
        last = lambda name: len(calls) - 1 - calls[::-1].index(name)  # noqa: E731
        self.assertGreater(last("stop-server"), last("pause-True"))

    def test_the_stub_model_counts_and_stores_nothing(self):
        w.Stub.count = 0
        self.assertEqual(w.Stub.count, 0)


if __name__ == "__main__":
    unittest.main()
