"""Synthetic acceptance for protected setup input and challenge responses.

Real HTTP against the real `deck` binary and its SQLite file. The model is scripted. The guest is
`setup_secrets_guest.py`, a fault injector. No VM, no browser, no real account.

Run (from the repository root, after `cargo build`):
  CARGO_TARGET_DIR=<private dir> python3 -m unittest discover -s scripts/vm-prototype -p 'test_setup_secrets_e2e.py'
Set DECK_BIN to use another binary. Tests that need a missing endpoint fail with "RED:", never skip.
Failure output carries counts and short hashes only. It never carries the generated sentinel.
"""
import json
import os
import stat
import unittest

from setup_secrets_fixture import Stack, deck_binary


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not deck_binary().exists():
            raise unittest.SkipTest(f"BLOCKED: deck binary not built at {deck_binary()}")

    def setUp(self):
        self.s = Stack()
        self.addCleanup(self.s.close)
        self.s.start()

    def need_secret(self, sid):
        if not self.s.secret_endpoint_present(sid):
            self.fail("RED: POST /api/setups/{id}/secret is not routed (404/405). Needs the host secret lifecycle companion.")

    def need_respond(self, sid):
        if not self.s.respond_endpoint_present(sid):
            self.fail("RED: POST /api/setups/{id}/challenge/respond is not routed (404/405). Needs the login-challenge companion.")

    def req(self, sid):
        cur = self.s.setup(sid)
        return cur["version"], cur["setup"]["wait"]["request_id"]

    def assert_open_credential_wait(self, sid, request_id=None):
        w = self.s.setup(sid)["setup"]["wait"]
        self.assertIsNotNone(w, "the wait must stay open")
        self.assertIn("Credential", w["kind"])
        if request_id:
            self.assertEqual(w["request_id"], request_id)

    def assert_no_delivery(self, sid):
        self.assertEqual(len(self.s.calls(sid, "deliver-secret")), 0, "no guest delivery may happen")
        self.assertEqual(self.s.guest_secrets(sid), [])

    def assert_clean(self):
        counts = self.s.leak_counts()
        self.assertEqual(counts, {}, f"sentinel found on {len(counts)} surface(s): {sorted(counts)}")


class Harness(Base):
    """Baseline checks that need no companion code. They must pass on the baseline head."""

    def test_driver_reaches_a_credential_wait_through_the_fake_guest(self):
        sid = self.s.new_waiting_setup()
        w = self.s.setup(sid)["setup"]["wait"]
        self.assertEqual(w["kind"], {"Credential": {"label": "FIXTURE_PASS"}})
        self.assertEqual([c["argv"][-1] if "--bot" not in c["argv"] else "install" for c in self.s.journal(sid)][:1], ["status"])
        self.assertEqual(self.s.model.requests, 0)

    def test_guest_delivery_contract_stores_privately_and_returns_metadata_only(self):
        sid = self.s.new_waiting_setup()
        import subprocess, sys
        from setup_secrets_fixture import GUEST
        rt = self.s.rt(sid)
        run = lambda *a, stdin=b"": subprocess.run([sys.executable, "-I", str(GUEST), "--dir", str(rt), *a], input=stdin, capture_output=True)
        ok = run("deliver-secret", "--bot", sid, "--request-id", "r1", stdin=json.dumps({"label": "FIXTURE_PASS", "value": self.s.sentinel.value}).encode())
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(json.loads(ok.stdout), {"ok": True, "bot_id": sid, "request_id": "r1", "label": "FIXTURE_PASS"})
        self.assertEqual(self.s.sentinel.count(ok.stdout + ok.stderr), 0)
        self.assertEqual(stat.S_IMODE(os.stat(rt / "guest" / "secrets.env").st_mode), 0o600)
        rec = run("secret-receipt", "--bot", sid, "--request-id", "r1")
        self.assertEqual(rec.returncode, 0)
        self.assertEqual(self.s.sentinel.count(rec.stdout), 0)
        self.assertEqual(run("secret-receipt", "--bot", "other", "--request-id", "r1").returncode, 1)
        self.assertEqual(run("secret-receipt", "--bot", sid, "--request-id", "r2").returncode, 1)
        for bad in ("a\nb", "a\rb", "a\0b", "x" * 5000, ""):
            r = run("deliver-secret", "--bot", sid, "--request-id", "r3", stdin=json.dumps({"label": "FIXTURE_PASS", "value": bad}).encode())
            self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.s.guest_secrets(sid)), 1, "a rejected value must store nothing")
        self.assertEqual(self.s.sentinel.count_file(rt / "journal.jsonl"), 0)

    def test_legacy_wait_route_never_accepts_a_credential_wait(self):
        sid = self.s.new_waiting_setup()
        v, rid = self.req(sid)
        st, _ = self.s.call("POST", f"/api/setups/{sid}/control", {"version": v, "command": {"action": "complete_wait", "request_id": rid}})
        self.assertGreaterEqual(st, 400)
        self.assert_open_credential_wait(sid, rid)

    def test_legacy_wait_route_still_completes_a_phone_wait(self):
        sid = self.s.new_waiting_setup()
        self.seed_phone_wait(sid)
        v, rid = self.req(sid)
        st, _ = self.s.call("POST", f"/api/setups/{sid}/control", {"version": v, "command": {"action": "complete_wait", "request_id": rid}})
        self.assertEqual(st, 204)

    def seed_phone_wait(self, sid, expires_at=None):
        """A phone wait on InstallExecutor, then the credential steps pending. Snapshot seeding only."""
        def fn(o):
            for st in o["steps"]:
                st["status"] = "Done" if st["step"] in ("Allocate", "Boot") else ("Waiting" if st["step"] == "InstallExecutor" else "Pending")
            o["wait_seq"] += 1
            o["wait"] = {"request_id": f"{sid}:{o['wait_seq']}", "step": "InstallExecutor", "kind": "PhoneApproval", "expires_at": expires_at, "ttl_secs": 60}
        self.s.patch_setup(sid, fn)

    def test_the_screenshot_gate_is_not_claimed_here(self):
        self.skipTest("BLOCKED: raster secrecy needs withheld capture or inspected render/OCR evidence from bare; grepping PNG bytes proves nothing.")


class ProtectedSecret(Base):
    def test_wrong_authentication_is_refused_and_nothing_is_delivered(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.assertEqual(self.s.secret(sid, v, rid, user=None)[0], 401)
        self.assertEqual(self.s.secret(sid, v, rid, user="intruder")[0], 403)
        self.assert_no_delivery(sid)
        self.assert_open_credential_wait(sid, rid)
        self.assert_clean()

    def test_cross_bot_and_cross_request_identities_fail(self):
        a, b = self.s.new_waiting_setup(), self.s.new_waiting_setup()
        self.need_secret(a)
        va, ra = self.req(a)
        vb, rb = self.req(b)
        self.assertNotEqual(ra, rb)
        for st in (self.s.secret(a, va, rb)[0], self.s.secret(b, vb, ra)[0], self.s.secret(a, va, "nonexistent")[0],
                   self.s.secret("no-such-setup", 1, ra)[0]):
            self.assertGreaterEqual(st, 400)
        for sid in (a, b):
            self.assert_no_delivery(sid)
        self.assert_open_credential_wait(a, ra)
        self.assert_open_credential_wait(b, rb)
        self.assert_clean()

    def test_the_label_comes_from_the_pending_request_not_the_post(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        st, _ = self.s.call("POST", f"/api/setups/{sid}/secret", {"version": v, "request_id": rid, "value": self.s.sentinel.value, "label": "OTHER_LABEL"})
        if st == 204:
            self.assertEqual([r["label"] for r in self.s.receipts(sid)], ["FIXTURE_PASS"])
        else:
            self.assertGreaterEqual(st, 400)
            self.assert_no_delivery(sid)
        self.assert_clean()

    def test_stale_version_fails_without_delivery(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.assertGreaterEqual(self.s.secret(sid, v - 1, rid)[0], 400)
        self.assertGreaterEqual(self.s.secret(sid, v + 5, rid)[0], 400)
        self.assert_no_delivery(sid)
        self.assert_open_credential_wait(sid, rid)
        self.assert_clean()

    def test_delivery_completes_the_wait_once_and_a_duplicate_is_refused(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        st, body = self.s.secret(sid, v, rid)
        self.assertEqual(st, 204)
        self.assertFalse(body)
        self.assertEqual(len(self.s.calls(sid, "deliver-secret")), 1)
        self.assertEqual(self.s.receipts(sid), [{"bot_id": sid, "request_id": rid, "label": "FIXTURE_PASS"}])
        sink = self.s.rt(sid) / "guest" / "secrets.env"
        self.assertEqual(stat.S_IMODE(os.stat(sink).st_mode), 0o600)
        self.assertEqual(self.s.sentinel.count_file(sink), 1, "the private sink holds the value once")
        for argv in (c["argv"] for c in self.s.journal(sid)):
            self.assertNotIn(self.s.sentinel.value, argv)
        cur = self.s.setup(sid)
        self.assertEqual(cur["setup"]["wait"]["kind"], {"Credential": {"label": "HOLD_OPEN"}})
        # the same request, even with the new version, must not deliver again
        self.assertGreaterEqual(self.s.secret(sid, cur["version"], rid)[0], 400)
        self.assertEqual(len(self.s.calls(sid, "deliver-secret")), 1)
        self.assertEqual(self.s.model.requests, 0)
        self.assert_clean()

    def test_failed_delivery_keeps_the_wait_and_allows_a_retry(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.s.fault(sid, "fail")
        self.assertGreaterEqual(self.s.secret(sid, v, rid)[0], 400)
        self.assertEqual(self.s.guest_secrets(sid), [])
        self.assertEqual(self.s.receipts(sid), [])
        self.assert_open_credential_wait(sid)
        v, rid2 = self.req(sid)
        st = self.s.secret(sid, v, rid2)[0]
        self.assertEqual(st, 204)
        self.assertEqual(len(self.s.receipts(sid)), 1)
        self.assert_clean()

    def test_partial_delivery_without_a_receipt_never_completes_the_wait(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.s.fault(sid, "partial")
        self.assertGreaterEqual(self.s.secret(sid, v, rid)[0], 400)
        self.assertEqual(self.s.receipts(sid), [])
        self.assert_open_credential_wait(sid)
        self.assert_clean()

    def test_crash_after_delivery_before_the_receipt_reaches_the_host(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.s.fault(sid, "crash_after_deliver")
        st = self.s.secret(sid, v, rid)[0]
        self.assertNotEqual(st, 204, "the host cannot claim verified delivery without the guest answer")
        self.s.kill9()
        self.s.start()
        cur = self.s.setup(sid)
        done = cur["setup"]["wait"] is None or cur["setup"]["wait"]["request_id"] != rid
        if done:
            self.assertEqual(len(self.s.receipts(sid)), 1, "a cleared wait needs a guest receipt for this request and bot")
        # a receipt alone must never clear a different request
        self.assertEqual(len(self.s.calls(sid, "deliver-secret")), 1)
        self.assertTrue(all(r["request_id"] == rid and r["bot_id"] == sid for r in self.s.receipts(sid)))
        self.assert_clean()

    def test_restart_while_waiting_keeps_the_same_request(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.s.restart()
        self.assert_open_credential_wait(sid, rid)
        self.assertEqual(self.s.secret(sid, self.s.setup(sid)["version"], rid)[0], 204)
        self.assert_clean()

    def test_an_expired_request_cannot_deliver(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)

        def expire(o):
            o["wait"]["expires_at"] = 1
            o["wait"]["ttl_secs"] = 60
        self.s.patch_setup(sid, expire)
        v, rid = self.req(sid)
        self.assertGreaterEqual(self.s.secret(sid, v, rid)[0], 400)
        self.assert_no_delivery(sid)
        self.assert_clean()

    def test_unreadable_bodies_and_url_values_are_rejected(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, rid = self.req(sid)
        for raw in (b"{", b"[]", b'{"version":%d,"request_id":"%s"}' % (v, rid.encode()),
                    b'{"version":%d,"request_id":"%s","value":""}' % (v, rid.encode())):
            self.assertGreaterEqual(self.s.call("POST", f"/api/setups/{sid}/secret", raw=raw)[0], 400)
        st, _ = self.s.call("POST", f"/api/setups/{sid}/secret?value={self.s.sentinel.value}", {"version": v, "request_id": rid})
        self.assertGreaterEqual(st, 400)
        self.assert_no_delivery(sid)
        # the URL was logged by the harness; the server must not echo it
        self.assertEqual(self.s.sentinel.count_file(self.s.log), 0)


class Holds(Base):
    """Independent holds survive a secret delivery. The secret never dispatches work."""

    def delivered(self, sid):
        self.need_secret(sid)
        v, rid = self.req(sid)
        self.assertEqual(self.s.secret(sid, v, rid)[0], 204)

    def test_pause_budget_and_unknown_outcome_holds_stay_in_force(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)
        v, _ = self.req(sid)
        self.assertEqual(self.s.call("POST", f"/api/setups/{sid}/control", {"version": v, "command": {"action": "pause", "value": True}})[0], 204)
        self.s.sql("SELECT 1")
        import sqlite3
        db = sqlite3.connect(self.s.data / "deck.db", timeout=10)
        for kind in ("ceiling", "unknown_outcome", "investigation"):
            db.execute("INSERT INTO bot_holds(bot_id,kind,reason,created_at) VALUES (?,?,?,datetime('now'))", (sid, kind, "fixture"))
        db.commit()
        db.close()
        before = self.s.sql("SELECT paused FROM bot_state WHERE bot_id=?", (sid,))
        self.delivered(sid)
        self.assertEqual(self.s.sql("SELECT paused FROM bot_state WHERE bot_id=?", (sid,)), before)
        self.assertEqual(sorted(k for (k,) in self.s.sql("SELECT kind FROM bot_holds WHERE bot_id=?", (sid,))),
                         ["ceiling", "investigation", "unknown_outcome"])
        self.assertEqual(self.s.sql("SELECT count(*) FROM tasks WHERE bot_id=?", (sid,)), [(0,)])
        self.assertEqual(self.s.model.requests, 0)
        self.assert_clean()

    def test_a_step_with_an_unknown_outcome_is_not_resolved_by_delivery(self):
        sid = self.s.new_waiting_setup()
        self.need_secret(sid)

        def fn(o):
            o["steps"][0]["status"] = "Unknown"
        self.s.patch_setup(sid, fn)
        v, rid = self.req(sid)
        st, _ = self.s.secret(sid, v, rid)
        steps = {x["step"] if isinstance(x["step"], str) else "cred": x["status"] for x in self.s.setup(sid)["setup"]["steps"]}
        self.assertEqual(steps["Allocate"], "Unknown")
        self.assertEqual(len(self.s.calls(sid, "start")), 0 if st != 204 else len(self.s.calls(sid, "start")))
        self.assert_clean()


class Challenges(Base):
    seed = Harness.seed_phone_wait

    def test_owner_completion_requires_the_current_request_and_version(self):
        sid = self.s.new_waiting_setup()
        self.need_respond(sid)
        self.seed(sid)
        v, rid = self.req(sid)
        self.assertEqual(self.s.respond(sid, v - 1, rid, "completed")[0] // 100, 4)
        self.assertEqual(self.s.respond(sid, v, rid + "x", "completed")[0] // 100, 4)
        self.assertEqual(self.s.respond(sid, v, rid, "maybe")[0] // 100, 4)
        self.assertEqual(self.s.respond(sid, v, rid, "completed", value="x")[0] // 100, 4, "metadata only")
        self.assertEqual(self.s.setup(sid)["setup"]["wait"]["request_id"], rid)
        self.assertEqual(self.s.respond(sid, v, rid, "completed")[0], 204)
        self.assertGreaterEqual(self.s.respond(sid, self.s.setup(sid)["version"], rid, "completed")[0], 400, "duplicate")
        self.assert_clean()

    def test_wrong_owner_and_cross_bot_responses_fail(self):
        a, b = self.s.new_waiting_setup(), self.s.new_waiting_setup()
        self.need_respond(a)
        self.seed(a)
        self.seed(b)
        va, ra = self.req(a)
        vb, rb = self.req(b)
        self.assertEqual(self.s.call("POST", f"/api/setups/{a}/challenge/respond", {"version": va, "request_id": ra, "decision": "completed"}, user=None)[0], 401)
        self.assertEqual(self.s.call("POST", f"/api/setups/{a}/challenge/respond", {"version": va, "request_id": ra, "decision": "completed"}, user="intruder")[0], 403)
        self.assertGreaterEqual(self.s.respond(a, va, rb, "completed")[0], 400)
        self.assertEqual(self.s.setup(a)["setup"]["wait"]["request_id"], ra)
        self.assertEqual(self.s.setup(b)["setup"]["wait"]["request_id"], rb)

    def test_an_expired_challenge_gets_a_replacement_and_cannot_complete(self):
        sid = self.s.new_waiting_setup()
        self.need_respond(sid)
        self.seed(sid, expires_at=1)
        v, rid = self.req(sid)
        self.assertGreaterEqual(self.s.respond(sid, v, rid, "completed")[0], 400)
        w = self.s.setup(sid)["setup"]["wait"]
        self.assertIsNotNone(w, "an expired answer must not resume work")
        self.assertEqual(len(self.s.calls(sid, "install-executor")), 1, "no resume after expiry")

    def test_completion_resumes_the_driver_but_is_not_service_success(self):
        sid = self.s.new_waiting_setup()
        self.need_respond(sid)
        self.seed(sid)
        v, rid = self.req(sid)
        self.assertEqual(self.s.respond(sid, v, rid, "completed")[0], 204)
        w = self.s.wait_for(lambda: (lambda c: c if c["setup"]["wait"] and "Credential" in c["setup"]["wait"]["kind"] else None)(self.s.setup(sid)), 20)
        self.assertTrue(w, "the driver must continue to the next step after the owner answer")
        self.assertNotEqual(w["setup"]["phase"] if "phase" in w["setup"] else w["phase"], "Ready")

    def test_cancel_does_not_resume_work(self):
        sid = self.s.new_waiting_setup()
        self.need_respond(sid)
        self.seed(sid)
        v, rid = self.req(sid)
        before = len(self.s.calls(sid, "install-executor"))
        st = self.s.respond(sid, v, rid, "cancel")[0]
        self.assertIn(st, (200, 204, 409))
        steps = {x["step"] if isinstance(x["step"], str) else "cred": x["status"] for x in self.s.setup(sid)["setup"]["steps"]}
        self.assertNotEqual(steps["InstallExecutor"], "Done")
        self.assertEqual(len(self.s.calls(sid, "install-executor")), before)


if __name__ == "__main__":
    unittest.main()
