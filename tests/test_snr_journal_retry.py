"""A failed SNR journal append must not discard the observation on the next bar."""
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import Mock, patch

from app.datafeed import ROOT
from app.snr_rbs_live import RBSForward
from app.snr_sbr_live import SBRForward


class SNRJournalRetryTests(unittest.TestCase):
    def test_index_failure_does_not_duplicate_same_process(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__):
                journal = ROOT / 'runtime' / ('snr-index-' + uuid.uuid4().hex + '.jsonl')
                try:
                    store = Mock()
                    forward = cls('XAUUSD', 'source-A', 100, journal, store)
                    forward.finish_bootstrap()
                    forward.model.step = lambda _row: forward.model.events.append(
                        dict(id='one', time=160, state='armed'))
                    with patch.object(forward.seen, 'add', side_effect=OSError('index unavailable')):
                        with self.assertRaises(OSError):
                            forward.ingest({}, 170, 169)
                    forward.ingest({}, 180, 179)
                    self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()), 1)
                    self.assertEqual(forward.state(180, 179)['forward_events_total'], 1)
                finally:
                    journal.unlink(missing_ok=True)
                    journal.with_suffix(journal.suffix + '.keys.sqlite3').unlink(missing_ok=True)

    def test_rejects_foreign_journal_before_index_and_delivery_recovery(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__):
                journal = ROOT / 'runtime' / ('snr-identity-' + uuid.uuid4().hex + '.jsonl')
                try:
                    forward = cls('XAUUSD', 'source-A', 100, journal, Mock())
                    forward.finish_bootstrap()
                    forward.model.step = lambda _row: forward.model.events.append(
                        dict(id='one', time=160, state='armed'))
                    forward.ingest({}, 170, 169)
                    original = journal.read_bytes()
                    for symbol, source in [('XAUUSD', 'source-B'), ('EURUSD', 'source-A')]:
                        with self.subTest(symbol=symbol, source=source), \
                             patch(cls.__module__ + '.JournalKeys') as index:
                            store = Mock()
                            with self.assertRaises(ValueError):
                                cls(symbol, source, 100, journal, store)
                            index.assert_not_called()
                            store.add.assert_not_called()
                            self.assertEqual(journal.read_bytes(), original)
                    saved = json.loads(original)
                    saved['version'] = 'old-rules'
                    journal.write_text(json.dumps(saved) + '\n', encoding='utf-8')
                    with patch(cls.__module__ + '.JournalKeys') as index:
                        with self.assertRaises(ValueError):
                            cls('XAUUSD', 'source-A', 100, journal, Mock())
                        index.assert_not_called()
                finally:
                    journal.unlink(missing_ok=True)
                    journal.with_suffix(journal.suffix + '.keys.sqlite3').unlink(missing_ok=True)

    def test_retries_failed_event_before_processing_next_bar(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__):
                journal = ROOT / 'runtime' / ('snr-retry-' + uuid.uuid4().hex + '.jsonl')
                try:
                    self.check_retry(cls, journal)
                finally:
                    journal.unlink(missing_ok=True)
                    journal.with_suffix(journal.suffix + '.keys.sqlite3').unlink(missing_ok=True)

    def check_retry(self, cls, journal):
        store = Mock()
        forward = cls('XAUUSD', 'source-A', 100, journal, store)
        forward.finish_bootstrap()
        forward.model.levels['one'] = {'first_touch': {'close': 100}, 'level': 100}
        event = dict(id='one', time=160, state='signal_ready', level_id='one',
                     source='MT5 · XAUUSD', stop=102, target=96)
        calls = 0

        def step(_row):
            nonlocal calls
            calls += 1
            if calls == 1:
                forward.model.events.append(event)
            else:
                self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()), 1)

        forward.model.step = step
        original_open = Path.open
        failures = 0

        def fail_initial_appends(path, *args, **kwargs):
            nonlocal failures
            if path == journal and args and args[0] == 'a' and failures < 2:
                failures += 1
                raise OSError('disk unavailable')
            return original_open(path, *args, **kwargs)

        with patch.object(Path, 'open', fail_initial_appends), \
             patch(cls.__module__ + '.public_settings', return_value={'enabled': True}):
            with self.assertRaises(OSError):
                forward.ingest({}, 170, 169)
            self.assertEqual(len(forward.model.events), 1)
            with self.assertRaises(OSError):
                forward.ingest({}, 200, 199)
            self.assertEqual(calls, 1)
            forward.ingest({}, 280, 279)

        rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['received_at'], 170)
        self.assertEqual(rows[0]['observation_quality'], 'timely')
        self.assertEqual(store.add.call_count, 1)
        self.assertEqual(forward.model.events, [])


if __name__ == '__main__':
    unittest.main()
