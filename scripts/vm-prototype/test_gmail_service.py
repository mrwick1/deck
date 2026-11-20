"""Tests for gmail_service.py. They start no VM, no browser and no Deck server."""
import contextlib
import hashlib
import io
import json
import os
import stat
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

import gmail_service as s

NOW = 1_800_000_000_000
CFG = {"port": 8791, "data_dir": "/d", "runtime_dir": "/r", "app": "/app", "fixture_port": 9001, "account_selector": 'a[href*="x"]', "search_box": 'input[name="q"]'}


def acct(key, result="ok", success=30, attempt=30):
    return {"key": key, "result": result, "last_attempt_ms": NOW - attempt * 1000, "last_success_ms": NOW - success * 1000 if success is not None else None, "watermark_ms": NOW - 1000}


def reply(accounts=None, **kw):
    gm = {"mode": "poll_only", "running": False, "stage": None, "unprocessed_events": 0, "next_due_in_ms": 50, "overdue_ms": 0,
          "last_cycle": {"result": "complete", "started_ms": NOW - 90_000, "ended_ms": NOW - 60_000}, "accounts": accounts or [acct(k) for k in s.ACCOUNTS]}
    gm.update(kw.pop("gmail", {}))
    base = {"computer": "awake", "automation_paused": False, "holds": [], "unresolved": 0, "counters": {"model_calls": 0}, "gmail": gm}
    base.update(kw)
    return base


class Config(unittest.TestCase):
    def test_poll_only_no_model_and_three_accounts(self):
        t = s.render_config(CFG)
        self.assertIn("poll_only = true", t)
        self.assertIn('base_url = "http://127.0.0.1:9/v1"', t)
        self.assertEqual(t.count("[[gmail.accounts]]"), 3)
        self.assertIn('listen = "127.0.0.1:8791"', t)
        self.assertNotIn("[events]", t)

    def test_default_trusts_only_loopback(self):
        self.assertIn('trusted_proxies = ["127.0.0.1"]', s.render_config(CFG))

    def test_tailnet_bind_trusts_itself_and_the_named_proxy_only(self):
        t = s.render_config({**CFG, "bind": "192.0.2.1", "proxy_peers": ["192.0.2.2"]})
        self.assertIn('listen = "192.0.2.1:8791"', t)
        self.assertIn('trusted_proxies = ["127.0.0.1", "192.0.2.1", "192.0.2.2"]', t)

    def test_no_secret_or_address_value_is_rendered(self):
        t = s.render_config(CFG)
        self.assertNotIn("@", t)
        self.assertIn('user_secret = "GMAIL_A_USER"', t)

    def test_private_provisioning_text_is_appended_after_the_gmail_tables(self):
        self.assertNotIn("[provisioning]", s.render_config(CFG))
        t = s.render_config({**CFG, "provisioning_toml": "\n[provisioning]\nmax_bots = 1\n"})
        self.assertTrue(t.endswith("[provisioning]\nmax_bots = 1\n"))


class PersistentVm(unittest.TestCase):
    def test_the_vm_lifetime_is_unbounded_only_when_the_config_asks(self):
        self.assertNotIn("DECK_VM_LIFETIME_S", s.vm_env(CFG, {"PATH": "/bin"}))
        e = s.vm_env(dict(CFG, persistent_vm=True), {"PATH": "/bin"})
        self.assertEqual((e["DECK_VM_LIFETIME_S"], e["PATH"]), ("infinity", "/bin"))
        self.assertNotIn("DECK_VM_LIFETIME_S", s.vm_env(dict(CFG, persistent_vm=False), {"DECK_VM_LIFETIME_S": "infinity"}))


class Summary(unittest.TestCase):
    def test_ages_and_fields(self):
        v = s.summarize(reply(), NOW)
        self.assertEqual(v["accounts"][0]["last_success_age_s"], 30)
        self.assertEqual(v["last_cycle"], {"result": "complete", "started_age_s": 90, "ended_age_s": 60})
        self.assertEqual(v["mode"], "poll_only")
        self.assertEqual(v["cycles_complete_observed"], 1)

    def test_cycle_counter_counts_each_complete_cycle_once(self):
        v = s.summarize(reply(), NOW)
        v = s.summarize(reply(), NOW + 20_000, v)
        self.assertEqual(v["cycles_complete_observed"], 1)
        nxt = reply(gmail={"last_cycle": {"result": "complete", "started_ms": NOW + 30_000, "ended_ms": NOW + 55_000}})
        v = s.summarize(nxt, NOW + 60_000, v)
        self.assertEqual(v["cycles_complete_observed"], 2)

    def test_a_failed_cycle_is_not_counted(self):
        bad = reply(gmail={"last_cycle": {"result": "failed", "started_ms": NOW - 5000, "ended_ms": NOW - 1000}})
        self.assertEqual(s.summarize(bad, NOW)["cycles_complete_observed"], 0)

    def test_stage_and_current_account_show_while_running(self):
        v = s.summarize(reply(gmail={"running": True, "stage": "scan", "stage_account": "account_b"}), NOW)
        self.assertEqual((v["running"], v["stage"], v["stage_account"]), (True, "scan", "account_b"))

    def test_no_page_text_field_exists(self):
        self.assertEqual(sorted(s.summarize(reply(), NOW)["accounts"][0]), ["key", "last_attempt_age_s", "last_success_age_s", "result", "watermark_age_s"])


LABELS = {"chips_selector": "span.chip", "steps": {"needs_reply": 'click css="#a"', "action": 'click css="#b"', "waiting": 'click css="#c"', "fyi": 'click css="#d"'}}
SUM = dict(CFG, mode="summary", model_base_url="http://model.invalid/v1", model_name="the-model", summary_time="08:00")


class SummaryConfig(unittest.TestCase):
    def test_summary_mode_renders_the_model_and_the_summary_and_not_poll_only(self):
        t = s.render_config(SUM)
        self.assertNotIn("poll_only", t)
        self.assertIn('base_url = "http://model.invalid/v1"', t)
        self.assertIn('agent_model = "the-model"', t)
        self.assertIn('api_key_env = "DECK_MODEL_KEY"', t)
        self.assertIn('[gmail.summary]\ntime = "08:00"\ntimezone = "Asia/Kolkata"', t)
        self.assertIn("interval_secs = 120", t)
        self.assertEqual(t.count("[[gmail.accounts]]"), 3)

    def test_labels_render_only_when_configured(self):
        self.assertNotIn("gmail.summary.labels", s.render_config(SUM))
        t = s.render_config(dict(SUM, labels=LABELS))
        self.assertIn('chips_selector = "span.chip"', t)
        self.assertIn('needs_reply = "click css=\\"#a\\""', t)

    def test_the_default_mode_is_poll_only_and_an_unknown_mode_or_missing_model_is_refused(self):
        self.assertIn("poll_only = true", s.render_config(CFG))
        with self.assertRaises(SystemExit):
            s.render_config(dict(CFG, mode="other"))
        with self.assertRaises(SystemExit):
            s.render_config(dict(CFG, mode="summary"))

    def test_no_key_value_is_ever_rendered(self):
        self.assertNotIn("dummy", s.render_config(dict(SUM, model_key_item="/k")))
        self.assertNotIn("/k", s.render_config(dict(SUM, model_key_item="/k")))


class JournalGate(unittest.TestCase):
    def test_a_command_of_a_pruned_poll_task_is_settled_but_an_open_command_is_not(self):
        db = {"commands": [{"task_id": "gone", "state": "succeeded"}, {"task_id": "p1", "state": "succeeded"}], "polls": [{"id": "p1"}]}
        self.assertTrue(s.journal_settled(db, 0))
        self.assertFalse(s.journal_settled(dict(db, commands=db["commands"] + [{"task_id": "p1", "state": "running"}]), 0))
        self.assertFalse(s.journal_settled(db, 1))
        self.assertFalse(s.journal_settled(dict(db, commands=[]), 0))


class ModelKey(unittest.TestCase):
    def test_the_key_comes_from_the_vault_and_a_missing_or_empty_item_is_refused(self):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return types.SimpleNamespace(returncode=0, stdout="value\n")
        self.assertEqual(s.read_model_key("deck/x", run), "value")
        self.assertEqual(calls[0][0], ["rbw", "get", "deck/x"])
        for rc, out in ((1, ""), (0, "\n")):
            with self.assertRaises(SystemExit):
                s.read_model_key("deck/x", lambda *a, **k: types.SimpleNamespace(returncode=rc, stdout=out))

    def test_label_catchup_renders_only_when_set_and_before_the_label_tables(self):
        self.assertNotIn("label_catchup", s.render_config(dict(SUM, labels=LABELS)))
        t = s.render_config(dict(SUM, labels=LABELS, summary_label_catchup=True))
        self.assertLess(t.index("label_catchup = true"), t.index("[gmail.summary.labels]"))

    def test_topic_steps_render_in_the_labels_table_before_its_steps_and_only_when_set(self):
        self.assertNotIn("topic_step", s.render_config(dict(SUM, labels=LABELS)))
        topics = dict(LABELS, topic_step='click role=menuitemcheckbox name="{label}"', topic_create_step='click role=menuitem name="Create new"\ntype role=textbox name="Name" "{label}"')
        t = s.render_config(dict(SUM, labels=topics, cleanup={"trash_step": 'click role=button name="Delete"', "topics_new_per_pass": 1, "topics_registry_cap": 20}))
        head = t.index("[gmail.summary.labels]")
        self.assertLess(head, t.index("topic_step = "))
        self.assertLess(t.index("topic_create_step = "), t.index("[gmail.summary.labels.steps]"))
        self.assertIn('name=\\"{label}\\"', t)
        self.assertIn("topics_new_per_pass = 1\n", t)
        self.assertIn("topics_registry_cap = 20\n", t)

    def test_batch_limits_render_only_when_set(self):
        self.assertNotIn("batch_size", s.render_config(SUM))
        t = s.render_config(dict(SUM, summary_batch_size=3, summary_max_threads_per_run=6))
        self.assertIn("batch_size = 3\n", t)
        self.assertIn("max_threads_per_run = 6\n", t)

    def test_poll_only_never_reads_a_key(self):
        self.assertEqual(s.model_env(CFG), "unused")


class Configure(unittest.TestCase):
    def test_summary_then_rollback_keeps_the_rest_and_a_backup_of_the_poll_only_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.json"
            cfg.write_text(json.dumps(dict(CFG, state_dir=str(Path(d) / "st"))))
            os.chmod(cfg, 0o600)
            argv = ["configure", "--config", str(cfg), "--mode", "summary", "--model-base-url", "http://m.invalid/v1", "--model-name", "x", "--model-key-item", "deck/x"]
            with mock.patch.object(s, "read_model_key", return_value="v"):
                self.assertEqual(s.main(argv), 0)
            v = json.loads(cfg.read_text())
            self.assertEqual((v["mode"], v["model_name"], v["port"]), ("summary", "x", 8791))
            self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
            self.assertEqual(s.main(["configure", "--config", str(cfg), "--mode", "poll_only"]), 0)
            self.assertEqual(json.loads(cfg.read_text())["mode"], "poll_only")
            self.assertNotIn("mode", json.loads(Path(str(cfg) + ".poll-only").read_text()))


class SummaryStatus(unittest.TestCase):
    def test_the_summary_block_carries_fixed_words_and_counts_only(self):
        sm = {"time": "08:00", "timezone": "Asia/Kolkata", "labels": "configured", "label_halted": False, "label_states": {"applied": 2}, "triage_backlog": {"account_a": [1, 1]},
              "triage_errors": {"model_timeout": 1}, "next_summary": "2026-11-16 08:00", "last_delivery": {"day": "2026-11-15", "age_ms": 5000}, "last_phase": {"end": "delivered", "age_ms": 3000},
              "text": "page text must not pass"}
        v = s.summarize(reply(gmail={"mode": "triage", "summary": sm}), NOW)
        self.assertNotIn("text", v["summary"])
        self.assertEqual(v["summary"]["last_delivery"], {"day": "2026-11-15", "age_ms": 5000})
        self.assertEqual(v["summary"]["next_summary"], "2026-11-16 08:00")
        self.assertIsNone(s.summarize(reply(), NOW)["summary"])


class ModeGates(unittest.TestCase):
    DB = {"model_tasks": 0, "events": [1], "bot": [{"epoch": 7}]}

    def test_poll_only_keeps_events_unprocessed_and_summary_hands_them_all_off(self):
        v = s.summarize(reply(gmail={"unprocessed_events": 1}), NOW)
        self.assertTrue(s.mode_gates("poll_only", "poll_only", v, self.DB)["events_as_the_mode_requires"])
        v = s.summarize(reply(gmail={"mode": "triage", "unprocessed_events": 0}), NOW)
        g = s.mode_gates("summary", "triage", v, self.DB)
        self.assertTrue(all(g.values()), g)
        v = s.summarize(reply(gmail={"mode": "triage", "unprocessed_events": 1}), NOW)
        self.assertFalse(s.mode_gates("summary", "triage", v, self.DB)["events_as_the_mode_requires"])

    def test_a_general_agent_task_fails_the_gate_in_both_modes(self):
        v = s.summarize(reply(gmail={"mode": "triage"}), NOW)
        self.assertFalse(s.mode_gates("summary", "triage", v, dict(self.DB, model_tasks=1))["no_general_agent_task"])

    def test_summary_mode_allows_model_calls_and_poll_only_does_not(self):
        v = s.summarize(reply(gmail={"mode": "triage"}, counters={"model_calls": 3}), NOW)
        self.assertTrue(s.mode_gates("summary", "triage", v, self.DB)["model_calls_allowed"])
        v = s.summarize(reply(counters={"model_calls": 3}), NOW)
        self.assertFalse(s.mode_gates("poll_only", "poll_only", v, self.DB)["model_calls_allowed"])


class CleanupConfig(unittest.TestCase):
    CLEAN = {"trash_step": 'click role=button name="Delete"', "trash_per_pass": 4, "backlog": False, "backlog_exclude": "-label:deck"}
    LAB = {"chips_selector": "span.chip", "steps": {"needs_reply": "a", "action": "b", "waiting": "c", "fyi": "d", "receipts": "e", "tech": "f"}}

    def test_cleanup_and_topic_steps_render_only_when_configured(self):
        base = dict(SUM, labels=self.LAB)
        t = s.render_config(base)
        self.assertNotIn("gmail.summary.cleanup", t)
        self.assertIn('receipts = "e"', t)
        self.assertNotIn("jobs =", t)
        t = s.render_config(dict(base, cleanup=self.CLEAN))
        self.assertIn("[gmail.summary.cleanup]", t)
        self.assertIn('trash_step = "click role=button name=\\"Delete\\""', t)
        self.assertIn("trash_per_pass = 4", t)
        self.assertIn("backlog = false", t)
        self.assertIn('backlog_exclude = "-label:deck"', t)

    def test_the_summary_run_cap_renders_so_a_label_catchup_cannot_starve_the_poll(self):
        t = s.render_config(dict(SUM, summary_max_run_secs=150, summary_max_threads_per_run=2))
        self.assertIn("max_run_secs = 150", t)
        self.assertIn("max_threads_per_run = 2", t)
        self.assertNotIn("max_run_secs", s.render_config(SUM))

    def test_the_read_limit_renders_so_a_long_promotion_is_read_in_full(self):
        self.assertIn("max_read_chars = 12000", s.render_config(dict(SUM, summary_max_read_chars=12000)))
        self.assertNotIn("max_read_chars", s.render_config(SUM))

    def test_cleanup_without_labels_renders_nothing(self):
        self.assertNotIn("cleanup", s.render_config(dict(SUM, cleanup=self.CLEAN)))
        self.assertNotIn("cleanup", s.render_config(dict(CFG, cleanup=self.CLEAN)))


class Alerts(unittest.TestCase):
    def test_routine_cleanup_progress_never_alerts(self):
        gm = {"summary": {"cleanup": {"open": {"triage": 40, "trash": 9, "topic": 3}, "done_total": {"trashed": 120}, "backlog": {"account_a": {"state": "running", "page": 7}}}}}
        v = s.summarize(reply(gmail=gm), NOW)
        self.assertEqual(v["summary"]["cleanup"]["done_total"]["trashed"], 120)
        self.assertEqual(s.alert_list(v), [])


    def test_healthy_has_none(self):
        self.assertEqual(s.alert_list(s.summarize(reply(), NOW)), [])

    def test_auth_and_identity_are_urgent_at_once(self):
        for r in s.IMMEDIATE:
            v = s.summarize(reply([acct("account_a"), acct("account_b", r, success=50), acct("account_c")]), NOW)
            a = s.alert_list(v)
            self.assertEqual([(x[0], x[1]) for x in a], [("auth-account_b", "urgent")])

    def test_stale_account_alerts_only_after_five_intervals(self):
        near = s.summarize(reply([acct("account_a", "error", success=s.STALE_S - 1), acct("account_b"), acct("account_c")]), NOW)
        self.assertEqual(s.alert_list(near), [])
        far = s.summarize(reply([acct("account_a", "error", success=s.STALE_S + 60), acct("account_b"), acct("account_c")]), NOW)
        self.assertEqual([x[0] for x in s.alert_list(far)], ["stale-account_a"])

    def test_a_pause_silences_the_stale_alert_but_a_hold_still_alerts(self):
        v = s.summarize(reply([acct("account_a", "error", success=2000), acct("account_b"), acct("account_c")], automation_paused=True, holds=[{}]), NOW)
        self.assertEqual([x[0] for x in s.alert_list(v)], ["hold"])

    def test_dedupe_within_the_hour(self):
        a = [("k", "alert", "t")]
        self.assertEqual(s.due_alerts(a, {"k": 1000}, 1000 + s.ALERT_EVERY_S - 1), [])
        self.assertEqual(s.due_alerts(a, {"k": 1000}, 1000 + s.ALERT_EVERY_S), a)
        self.assertEqual(s.due_alerts(a, {}, 5), a)


class Fresh(unittest.TestCase):
    def test_fresh_needs_all_three_within_the_limit(self):
        self.assertTrue(s.accounts_fresh(s.summarize(reply(), NOW)))
        old = reply([acct("account_a", "error", success=s.STALE_S + 1), acct("account_b"), acct("account_c")])
        self.assertFalse(s.accounts_fresh(s.summarize(old, NOW)))
        never = reply([acct("account_a", "error", success=None), acct("account_b"), acct("account_c")])
        self.assertFalse(s.accounts_fresh(s.summarize(never, NOW)))
        self.assertFalse(s.accounts_fresh(s.summarize(reply([acct("account_a")]), NOW)))

    def test_one_recent_failure_with_a_recent_success_is_still_fresh(self):
        v = s.summarize(reply([acct("account_a", "error", success=100, attempt=5), acct("account_b"), acct("account_c")]), NOW)
        self.assertTrue(s.accounts_fresh(v))


class Files(unittest.TestCase):
    def test_private_write_is_0600_and_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x" / "status.json"
            s.private_write(p, "{}")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
            self.assertEqual([f.name for f in p.parent.iterdir()], ["status.json"])

    def test_adopt_pause_creates_a_private_state_directory_and_marker(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.json"
            cfg.write_text(json.dumps({"state_dir": str(Path(d) / "state")}))
            os.chmod(cfg, 0o600)
            self.assertEqual(s.main(["adopt-pause", "--config", str(cfg)]), 0)
            m = Path(d) / "state" / "paused-by-service"
            self.assertEqual((stat.S_IMODE(m.stat().st_mode), stat.S_IMODE(m.parent.stat().st_mode)), (0o600, 0o700))

    def test_a_group_readable_config_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text("{}")
            os.chmod(p, 0o640)
            with self.assertRaises(SystemExit):
                s.load(p)

    def test_install_writes_private_config_unit_and_a_runnable_copy(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d) / "deck"
            deck.write_bytes(b"binary")
            with contextlib.redirect_stdout(io.StringIO()):
                s.main(["install", "--deck", str(deck), "--data-dir", "/data", "--trial", "/trial", "--base", "/base", "--bare-mcp", str(deck),
                        "--account-selector", "a", "--search-box", "b", "--port", "8791", "--fixture-port", "9001", "--home", d])
            cfg = Path(d) / ".config/deck-gmail/service.json"
            self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
            self.assertEqual(json.loads(cfg.read_text())["runtime_dir"], "/trial/computers/shared-profile")
            self.assertIn("gmail_service.py run --config", (Path(d) / ".config/systemd/user/deck-gmail-poller.service").read_text())
            app = Path(d) / ".local/share/deck-gmail/app/scripts/vm-prototype"
            self.assertTrue((app / "vmproto.py").exists())
            self.assertTrue((app / "test_multi_account.py").exists())


class Preflight(unittest.TestCase):
    PLACEHOLDER = b"<p>The web app is not built. Run <code>npm run build</code> in <code>web/</code>, then rebuild deck.</p>"

    def bad_for(self, deck_bytes):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d) / "deck"
            deck.write_bytes(deck_bytes)
            cfg = {"app": d, "deck": str(deck), "deck_sha256": "", "state_dir": d, "data_dir": d, "runtime_dir": d,
                   "trial": d, "base": d, "port": 1, "min_avail_mib": 0}
            return s.Svc(cfg).preflight()

    def test_a_binary_with_the_placeholder_page_is_refused(self):
        self.assertIn("deck_binary_has_no_web_app", self.bad_for(b"\x7fELF" + self.PLACEHOLDER))

    def test_a_binary_with_a_built_web_app_is_not_refused_for_the_web_app(self):
        self.assertNotIn("deck_binary_has_no_web_app", self.bad_for(b"\x7fELF<!doctype html><title>deck</title><div id=root></div>"))


class BareMcpFile(unittest.TestCase):
    """The deploy builds bare-mcp. The service installs that file in the guest and expects its digest."""

    def svc(self, d, **kw):
        deck = Path(d) / "deck"
        deck.write_bytes(b"\x7fELF<div id=root></div>")
        cfg = {"app": d, "deck": str(deck), "deck_sha256": s.sha256(deck), "state_dir": d, "data_dir": d, "runtime_dir": d,
               "trial": d, "base": "/base", "port": 1, "min_avail_mib": 0, **kw}
        return s.Svc(cfg)

    def test_the_resume_passes_the_file_and_its_digest_and_ignores_an_old_expected_hash(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "bare-mcp"
            f.write_bytes(b"newer bare")
            svc = self.svc(d, bare_mcp=str(f), expect_bare_sha256="0" * 64)
            self.assertEqual(svc.resume_args(), ["resume", "--expect-bare-sha256", hashlib.sha256(b"newer bare").hexdigest(), "--bare-candidate", str(f)])
            f.write_bytes(b"newest bare")  # the next start follows the file
            self.assertEqual(svc.resume_args()[2], hashlib.sha256(b"newest bare").hexdigest())

    def test_the_deck_and_the_harness_get_the_file_path_so_each_started_computer_is_synced(self):
        self.assertEqual(s.vm_env({"bare_mcp": "/x/bare-mcp"}, {"A": "1"}), {"A": "1", "DECK_VM_BARE_MCP": "/x/bare-mcp"})
        self.assertNotIn("DECK_VM_BARE_MCP", s.vm_env({}, {"A": "1"}))

    def test_a_missing_file_or_key_stops_the_preflight_with_a_clear_code(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("bare_mcp_missing", self.svc(d, bare_mcp=str(Path(d) / "none")).preflight())
            self.assertIn("bare_mcp_missing", self.svc(d).preflight())
            (Path(d) / "bare-mcp").write_bytes(b"x")
            self.assertNotIn("bare_mcp_missing", self.svc(d, bare_mcp=str(Path(d) / "bare-mcp")).preflight())

    def test_install_keeps_the_deploy_built_file_next_to_deck_and_no_hand_kept_hash(self):
        with tempfile.TemporaryDirectory() as d:
            deck, bare = Path(d) / "deck", Path(d) / "bare-mcp"
            deck.write_bytes(b"binary")
            bare.write_bytes(b"bare")
            bare.chmod(0o755)
            with contextlib.redirect_stdout(io.StringIO()):
                s.main(["install", "--deck", str(deck), "--bare-mcp", str(bare), "--data-dir", "/data", "--trial", "/trial", "--base", "/base",
                        "--account-selector", "a", "--search-box", "b", "--port", "8791", "--fixture-port", "9001", "--home", d])
            cfg = json.loads((Path(d) / ".config/deck-gmail/service.json").read_text())
            kept = Path(d) / ".local/share/deck-gmail/bin/bare-mcp"
            self.assertEqual((cfg["bare_mcp"], kept.read_bytes(), os.access(kept, os.X_OK)), (str(kept), b"bare", True))
            self.assertNotIn("expect_bare_sha256", cfg)


class Boost(unittest.TestCase):
    NORMAL = {"trash_step": "click", "triage_per_pass": 3, "trash_per_pass": 1, "topics_per_pass": 3, "max_run_secs": 240, "backlog_queue_cap": 20, "backlog_pages_per_pass": 1}
    BOOST = {"active": True, "started_s": 1000, "base_labels": {"applied": 80, "uncertain": 4},
             "cleanup": {"triage_per_pass": 12, "max_run_secs": 300}, "summary": {"max_threads_per_run": 6}}
    LAB = CleanupConfig.LAB

    def cfg(self, **kw):
        return dict(SUM, labels=self.LAB, cleanup=dict(self.NORMAL), summary_max_threads_per_run=2, **kw)

    def view(self, walk="complete", open_=None, pending=0, **kw):
        sm = {"cleanup": {"backlog": {k: {"state": walk} for k in s.ACCOUNTS}, "open": open_ or {"triage": 0, "topic": 0, "trash": 0}},
              "triage_backlog": {k: [pending, 0] for k in s.ACCOUNTS}, "label_halted": False, "label_states": {"applied": 90, "uncertain": 5}}
        sm.update(kw.pop("sm", {}))
        v = s.summarize(reply(), NOW)
        v["summary"] = sm
        v.update(kw)
        return v

    def test_the_overlay_raises_only_the_named_caps_and_the_normal_caps_stay_in_the_file(self):
        c = dict(self.cfg(), boost=self.BOOST)
        t = s.render_config(c)
        self.assertIn("triage_per_pass = 12", t)
        self.assertIn("max_threads_per_run = 6", t)
        self.assertIn("trash_per_pass = 1", t)
        self.assertEqual(c["cleanup"], self.NORMAL)
        self.assertEqual(c["summary_max_threads_per_run"], 2)

    def test_an_inactive_or_absent_boost_renders_the_normal_caps(self):
        base = s.render_config(self.cfg())
        self.assertEqual(s.render_config(dict(self.cfg(), boost=dict(self.BOOST, active=False))), base)
        self.assertIn("triage_per_pass = 3", base)

    def test_ending_the_boost_restores_the_normal_render_and_keeps_every_other_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "service.json"
            s.private_write(p, json.dumps(dict(self.cfg(), boost=self.BOOST, deck_sha256="abc")))
            before = s.render_config(self.cfg())
            cfg = s.set_boost(p, False, 2000, "drained")
            self.assertEqual(s.render_config(cfg), before)
            on_disk = json.loads(p.read_text())
            self.assertEqual((on_disk["deck_sha256"], on_disk["cleanup"], on_disk["boost"]["ended_reason"]), ("abc", self.NORMAL, "drained"))
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_a_restart_in_the_middle_keeps_the_boost_on_and_the_end_is_written_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "service.json"
            s.private_write(p, json.dumps(self.cfg()))
            s.set_boost(p, True, 1000, caps={"cleanup": {"triage_per_pass": 12}, "summary": {}}, base_labels={"applied": 1})
            self.assertIn("triage_per_pass = 12", s.render_config(s.load(p)))
            self.assertEqual(s.load(p)["cleanup"], self.NORMAL)

    def test_it_ends_as_done_only_when_every_walk_is_complete_and_no_work_is_open(self):
        b = self.BOOST
        self.assertEqual(s.boost_decision(b, self.view(), 1100), ("done", "drained"))
        self.assertIsNone(s.boost_decision(b, self.view(walk="running"), 1100))
        self.assertIsNone(s.boost_decision(b, self.view(open_={"triage": 0, "topic": 2, "trash": 0}), 1100))
        self.assertIsNone(s.boost_decision(b, self.view(pending=3), 1100))
        self.assertIsNone(s.boost_decision(b, self.view(sm={"cleanup": {"backlog": {}, "open": {}}}), 1100))
        self.assertIsNone(s.boost_decision(dict(b, active=False), self.view(), 1100))

    def test_it_backs_down_on_a_hold_a_halt_a_stale_scan_the_time_limit_or_a_rising_uncertain_share(self):
        b = self.BOOST
        self.assertEqual(s.boost_decision(b, self.view(holds=1), 1100), ("back_down", "hold"))
        self.assertEqual(s.boost_decision(b, self.view(sm={"label_halted": True}), 1100), ("back_down", "label_halted"))
        self.assertEqual(s.boost_decision(b, self.view(walk="running"), 1000 + s.BOOST_MAX_S + 1), ("back_down", "time_limit"))
        stale = self.view(walk="running", accounts=[dict(a, last_success_age_s=9999) for a in s.summarize(reply(), NOW)["accounts"]])
        self.assertEqual(s.boost_decision(b, stale, 1000 + s.STALE_S + 1), ("back_down", "stale_scan"))
        fresh_start = self.view(walk="running", accounts=[dict(a, last_success_age_s=None) for a in s.summarize(reply(), NOW)["accounts"]])
        self.assertIsNone(s.boost_decision(b, fresh_start, 1000 + s.STALE_S))
        self.assertIsNone(s.boost_decision(b, stale, 1000 + s.STALE_S))  # old ages right after a restart are not judged yet
        self.assertEqual(s.boost_decision(b, fresh_start, 1000 + s.STALE_S + 1), ("back_down", "stale_scan"))
        worse = self.view(walk="running", sm={"label_states": {"applied": 90, "uncertain": 14}})  # 10 new, all uncertain
        self.assertEqual(s.boost_decision(b, worse, 1100), ("back_down", "uncertain_share"))
        few = self.view(walk="running", sm={"label_states": {"applied": 80, "uncertain": 8}})  # 4 new writes: too few to judge
        self.assertIsNone(s.boost_decision(b, few, 1100))

    def test_the_status_shows_the_boost_with_fixed_words_only(self):
        self.assertEqual(s.boost_view(None, 5), {"active": False, "age_s": None, "ended_reason": None})
        self.assertEqual(s.boost_view(self.BOOST, 1500), {"active": True, "age_s": 500, "ended_reason": None})


if __name__ == "__main__":
    unittest.main()
