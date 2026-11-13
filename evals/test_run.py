import hashlib
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import run

VALUES = {"base": "http://127.0.0.1:9", "account": "ACC-1A2B", "password": "pw-secret", "upload_text": "abc"}


def mkrun(answer="", calls=(), status="done"):
    return {"status": status, "answer": answer, "calls": list(calls)}


def call(name, args=None, result=""):
    return {"name": name, "args": args or {}, "result": result, "ok": True}


class FillTest(unittest.TestCase):
    def test_plain_and_escaped(self):
        self.assertEqual(run.fill("{{base}}/x", VALUES), "http://127.0.0.1:9/x")
        self.assertEqual(run.fill("{{base}}/x", VALUES, escape=True), r"http://127\.0\.0\.1:9/x")

    def test_regex_braces_stay(self):
        self.assertEqual(run.fill(r"\d{3}", VALUES), r"\d{3}")

    def test_unknown_placeholder(self):
        with self.assertRaises(KeyError):
            run.fill("{{nope}}", VALUES)


class SseTest(unittest.TestCase):
    def test_parse(self):
        lines = [
            ": keep-alive\n",
            "id: 3\n", "event: text\n", 'data: {"text":"a"}\n', "\n",
            "event: run_finished\n", "data: line1\n", "data: line2\n", "\n",
            "data: tail",
        ]
        self.assertEqual(list(run.parse_sse(lines)), [
            ("text", '{"text":"a"}', "3"),
            ("run_finished", "line1\nline2", None),
            ("message", "tail", None),
        ])

    def test_no_space_after_colon(self):
        self.assertEqual(list(run.parse_sse(["event:x\n", "data:y\n", "\n"])), [("x", "y", None)])


class ErrorLinesTest(unittest.TestCase):
    def test_prefixes(self):
        text = "ok line\nError: stale_ref e7\n  not ready: loading\nscript error: x is undefined\nfine"
        self.assertEqual(len(run.error_lines(text)), 3)

    def test_failed_call_json(self):
        self.assertEqual(run.error_lines('{"ok":false,"error":"boom"}', ok=False), ["error: boom"])

    def test_failed_call_text(self):
        self.assertEqual(run.error_lines("weird", ok=False), ["weird"])


class ScoreTest(unittest.TestCase):
    def test_answer(self):
        check = {"answer": "{{account}}"}
        self.assertEqual(run.score(check, VALUES, mkrun("It is ACC-1A2B."), None), [])
        self.assertEqual(len(run.score(check, VALUES, mkrun("It is ACC-1A2C."), None)), 1)

    def test_status(self):
        self.assertIn("run timeout", run.score({}, VALUES, mkrun(status="timeout"), None))

    def test_password_leak(self):
        r = run.score({}, VALUES, mkrun("pw-secret"), None)
        self.assertTrue(any("leaked" in x for x in r))
        r = run.score({}, VALUES, mkrun("", [call("browser_act", {"text": "pw-secret"})]), None)
        self.assertTrue(any("leaked" in x for x in r))
        self.assertEqual(run.score({}, VALUES, mkrun("", [call("browser_act", {"text": "${EVAL_PASSWORD}"})]), None), [])

    def test_secret_file_values_must_not_leak(self):
        secrets = run.secret_values(["SITE_PASSWORD=hunter2x", "SITE_EMAIL=me@example.com"])
        self.assertEqual(secrets, ["hunter2x"])
        self.assertEqual(len(run.score({}, VALUES, mkrun("it is hunter2x"), None, secrets=secrets)), 1)
        self.assertEqual(run.score({}, VALUES, mkrun("from me@example.com"), None, secrets=secrets), [])

    def test_tools(self):
        calls = [call("browser_open"), call("browser_eval")]
        r = run.score({"forbid_tools": ["browser_eval"], "require_tools": ["browser_links"]}, VALUES, mkrun("", calls), None)
        self.assertEqual(len(r), 2)

    def test_trace(self):
        calls = [call("browser_open", {"url": "https://x/news?p=2"})]
        self.assertEqual(run.score({"trace": r"news\?p=2"}, VALUES, mkrun("", calls), None), [])
        self.assertEqual(len(run.score({"trace": "nothing"}, VALUES, mkrun("", calls), None)), 1)

    def test_posted(self):
        fx = {"logins_ok": 0, "posts": [
            {"path": "/f", "fields": {"a": ["1"], "b": ["x"]}, "files": []},
        ]}
        check = {"posted": {"path": "/f", "count": 1, "fields": {"a": "1", "b": "x"}}}
        self.assertEqual(run.score(check, VALUES, mkrun(), fx), [])
        check["posted"]["fields"]["b"] = "y"
        self.assertEqual(len(run.score(check, VALUES, mkrun(), fx)), 1)
        fx["posts"].append(fx["posts"][0])
        check["posted"]["fields"]["b"] = "x"
        self.assertEqual(len(run.score(check, VALUES, mkrun(), fx)), 1)

    def test_no_post(self):
        r = run.score({"posted": {"path": "/f"}}, VALUES, mkrun(), {"logins_ok": 0, "posts": []})
        self.assertEqual(r, ["no POST to /f"])

    def test_uploaded(self):
        fx = {"logins_ok": 0, "posts": [
            {"path": "/u", "fields": {}, "files": [{"field": "f", "filename": "report.txt", "text": "abc\n"}]},
        ]}
        check = {"uploaded": {"path": "/u", "filename": "report.txt", "text": "{{upload_text}}"}}
        self.assertEqual(run.score(check, VALUES, mkrun(), fx), [])
        check["uploaded"]["filename"] = "other.txt"
        self.assertEqual(len(run.score(check, VALUES, mkrun(), fx)), 1)

    def test_logins(self):
        self.assertEqual(len(run.score({"logins_ok": 1}, VALUES, mkrun(), {"logins_ok": 0, "posts": []})), 1)


def rec(i, ok, t, n=3):
    return {"id": i, "pass": ok, "wall_s": t, "n_tool_calls": n, "reasons": [] if ok else ["x"]}


class CompareTest(unittest.TestCase):
    def test_changes(self):
        old = [rec("a", True, 10), rec("b", False, 20), rec("c", True, 5)]
        new = [rec("a", False, 12), rec("b", True, 15), rec("d", True, 1)]
        text = "\n".join(run.compare(old, new))
        self.assertIn("now FAILING   a", text)
        self.assertIn("now passing   b", text)
        self.assertIn("new task      d", text)
        self.assertIn("not run       c", text)
        self.assertIn("passed 2/3 -> 2/3", text)
        self.assertIn("30.0s -> 27.0s (-3.0s)", text)


class RunFilesTest(unittest.TestCase):
    def test_three_valid_files(self):
        files = run.run_files("a1b2c3")
        self.assertEqual(files["file_txt"][0], "eval-a1b2c3.txt")
        self.assertEqual(files["file_pdf"][0], "eval-a1b2c3.pdf")
        self.assertEqual(files["file_png"][0], "eval-a1b2c3.png")
        self.assertIn(b"a1b2c3", files["file_txt"][1])
        self.assertTrue(files["file_pdf"][1].startswith(b"%PDF-"))
        self.assertIn(b"a1b2c3", files["file_pdf"][1])
        self.assertTrue(files["file_png"][1].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(run.run_files("a1b2c3"), files)
        self.assertNotEqual(run.run_files("ffffff")["file_txt"], files["file_txt"])


class DownloadedTest(unittest.TestCase):
    def setUp(self):
        self.files = run.run_files("abc123")
        self.values = dict(VALUES, run_id="abc123", **{k: n for k, (n, _) in self.files.items()})
        self.made = {n: hashlib.sha256(d).hexdigest() for n, d in self.files.values()}

    def test_all_three_downloaded(self):
        downloads = {hashlib.sha256(d).hexdigest() for _, d in self.files.values()}
        self.assertEqual(run.score({"downloaded": True}, self.values, mkrun(), None, made=self.made, downloads=downloads), [])

    def test_missing_and_changed(self):
        downloads = {self.made["eval-abc123.txt"], self.made["eval-abc123.png"], "0" * 64}
        r = run.score({"downloaded": True}, self.values, mkrun(), None, made=self.made, downloads=downloads)
        self.assertEqual(r, ["no download matches eval-abc123.pdf"])

    def test_download_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run.download_hashes(Path(d) / "downloads"), set())
            (Path(d) / "downloads" / "sub").mkdir(parents=True)
            (Path(d) / "downloads" / "sub" / "any name.bin").write_bytes(b"x")
            self.assertEqual(run.download_hashes(Path(d) / "downloads"), {hashlib.sha256(b"x").hexdigest()})

    def test_needs_make_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.toml"
            path.write_text('[[task]]\nid = "x"\nprompt = "p"\ntimeout_s = 5\n[task.check]\ndownloaded = true\n')
            with self.assertRaises(ValueError):
                run.load_tasks(path)


class SecretsAndConfigTest(unittest.TestCase):
    def test_secret_file_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "in.env"
            path.write_text("# private\n\nMAIL_USER=alice\nMAIL_PASS=p w=1\n")
            self.assertEqual(run.read_secret_file(path), ["MAIL_USER=alice", "MAIL_PASS=p w=1"])
            path.write_text("MAIL_USER alice\n")
            with self.assertRaises(ValueError) as cm:
                run.read_secret_file(path)
            self.assertNotIn("alice", str(cm.exception))

    def test_write_private_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "secrets.env"
            run.write_private(path, "EVAL_PASSWORD=pw\nA=1\n")
            self.assertEqual(path.read_text(), "EVAL_PASSWORD=pw\nA=1\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_state_dir_only_when_given(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(
                run.os.environ, {"DECK_EVAL_BASE_URL": "http://127.0.0.1:1/v1"}):
            path, _ = run.write_config(Path(d), 1234, "/s.env", 5)
            self.assertNotIn("state_dir", run.tomllib.loads(path.read_text())["bare"])
            path, _ = run.write_config(Path(d), 1234, "/s.env", 5, state_dir="/x/state")
            self.assertEqual(run.tomllib.loads(path.read_text())["bare"]["state_dir"], "/x/state")


class TasksTest(unittest.TestCase):
    def test_tasks_file_loads(self):
        tasks = run.load_tasks(Path(run.HERE) / "tasks.toml")
        self.assertGreaterEqual(len(tasks), 15)
        self.assertTrue(any(t.get("fixture") for t in tasks))
        values = dict(VALUES, tracking="t", welcome="w", sku="s")
        for t in tasks:
            run.fill(t["prompt"], values)
            if "answer" in t["check"]:
                run.fill(t["check"]["answer"], values, escape=True)


if __name__ == "__main__":
    unittest.main()
