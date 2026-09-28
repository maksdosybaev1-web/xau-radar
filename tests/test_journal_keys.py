import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.journal_keys import JournalKeys
from app.snr_sbr_live import SBRForward
from app.snr_rbs_live import RBSForward


class JournalKeysTests(unittest.TestCase):
    def test_rebuild_matches_journal_and_discards_stale_index_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp)/'events.jsonl'
            journal.write_text('\n'.join(json.dumps({'dedup_key': str(i % 500)})
                                         for i in range(1000))+'\n', encoding='utf-8')
            keys = JournalKeys(journal)
            self.assertEqual(len(keys), 500)
            self.assertIn('499', keys)
            self.assertNotIn('missing', keys)
            keys.add('stale')
            with journal.open('a', encoding='utf-8') as output:
                output.write(json.dumps({'dedup_key': 'journal_only'})+'\n')
            rebuilt = JournalKeys(journal)
            self.assertEqual(len(rebuilt), 501)
            self.assertNotIn('stale', rebuilt)
            self.assertIn('journal_only', rebuilt)
            rebuilt.path.unlink()
            self.assertEqual(len(JournalKeys(journal)), 501)

    def test_durable_event_deduplicated_after_index_write_failure_and_restart(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp)/'events.jsonl'
                forward = cls('XAUUSD', 'test', 100, journal, Mock())
                forward.finish_bootstrap()
                event = dict(id='one', time=160, state='armed')
                forward.model.step = lambda row: forward.model.events.append(event)
                with patch.object(forward.seen, 'add', side_effect=OSError('index unavailable')):
                    with self.assertRaises(OSError):
                        forward.ingest({}, 170, 165)
                resumed = cls('XAUUSD', 'test', 100, journal, Mock())
                resumed.finish_bootstrap()
                resumed.model.step = lambda row: resumed.model.events.append(event)
                resumed.ingest({}, 175, 170)
                self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()), 1)
                self.assertEqual(resumed.state(175, 170)['forward_events_total'], 1)


if __name__ == '__main__':
    unittest.main()
