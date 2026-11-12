import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import benchmark as b


class EvidenceTests(unittest.TestCase):
    def test_failed_scenario_retains_journal_log_and_failure_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            (source / 'deck.toml').write_text('synthetic')
            (source / 'server.log').write_text('failure evidence')
            (source / 'data').mkdir()
            (source / 'data/journal.db').write_bytes(b'synthetic receipt')
            def scenario():
                child = subprocess.Popen(['true'], cwd=source)
                child.wait()
                runtime = root / 'evidence/run-1'
                runtime.mkdir()
                (runtime / 'results.json').write_text('{"ok":false}')
                raise RuntimeError('scripted failure')
            args = SimpleNamespace(output=str(root / 'evidence'), deck=__file__, base=__file__, rounds=1)
            with patch.object(b.stage3, 'main', scenario):
                with self.assertRaisesRegex(RuntimeError, 'scripted failure'):
                    b.run(args)
            saved = root / 'evidence/run-1'
            self.assertEqual((saved / 'server.log').read_text(), 'failure evidence')
            self.assertEqual((saved / 'host-data/journal.db').read_bytes(), b'synthetic receipt')
            self.assertFalse(json.loads((root / 'evidence/summary.json').read_text())[0]['ok'])

    def test_sampler_failure_is_reported_to_the_main_run(self):
        errors = []
        def fail():
            raise OSError('sampler failed')
        thread = threading.Thread(target=b.observe, args=(threading.Event(), fail, errors, 0.001))
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIn('sampler failed', errors[0])

    def test_main_run_rejects_failed_observer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def scenario():
                runtime = root / 'evidence/run-1'
                runtime.mkdir()
                (runtime / 'results.json').write_text('{"ok":true}')
            def broken(stop, collect, errors):
                errors.append('injected sampler error')
            args = SimpleNamespace(output=str(root / 'evidence'), deck=__file__, base=__file__, rounds=1)
            with patch.object(b.stage3, 'main', scenario), patch.object(b, 'observe', broken):
                with self.assertRaisesRegex(RuntimeError, 'observer failed|invalid evidence'):
                    b.run(args)
            status = json.loads((root / 'evidence/evidence-status.json').read_text())
            self.assertEqual(status['observer_errors'], ['injected sampler error'])
