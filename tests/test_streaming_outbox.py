import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.journal_outbox import ReplayPending, reconcile, remember


class StreamingOutboxTests(unittest.TestCase):
    def test_streaming_retry_and_new_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp)/'events.jsonl'
            records = [dict(dedup_key=str(i), delivery_alert={'id': str(i)},
                            quote_time=100, delivery_enabled=i % 2 == 0) for i in range(1000)]
            journal.write_text('\n'.join(json.dumps(r) for r in records)+'\n', encoding='utf-8')
            pending = ReplayPending(journal)
            self.assertEqual(len(pending), 0)
            self.assertTrue(pending)
            accepted = []
            def add(alert, now, quote, *, enabled):
                if alert['id'] == '7':
                    raise OSError('queue unavailable')
                accepted.append((alert['id'], enabled))
            store = Mock()
            store.add.side_effect = add
            with self.assertRaises(OSError):
                reconcile(pending, store, 100, True)
            self.assertEqual([item[0] for item in accepted], [str(i) for i in range(7)])
            self.assertEqual(len(pending), 0)
            store.add.side_effect = lambda alert, now, quote, *, enabled: accepted.append((alert['id'], enabled))
            reconcile(pending, store, 100, True)
            self.assertEqual(accepted, [(str(i), i % 2 == 0) for i in range(1000)])
            self.assertFalse(pending)
            remember(pending, dict(records[0], dedup_key='new', delivery_alert={'id': 'new'}))
            reconcile(pending, store, 100, False)
            self.assertEqual(accepted[-1], ('new', False))
            self.assertFalse(pending)

    def test_truncated_journal_does_not_silently_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp)/'events.jsonl'
            journal.write_text('{}\n', encoding='utf-8')
            pending = ReplayPending(journal)
            journal.write_text('', encoding='utf-8')
            with self.assertRaises(ValueError):
                reconcile(pending, Mock(), 100, False)
            self.assertTrue(pending)

    def test_late_terminal_replays_as_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/'events.jsonl'
            record={'dedup_key':'terminal','received_at':400,'quote_time':399,
                    'delivery_local_only':True,'delivery_alert':{'id':'terminal-alert'}}
            journal.write_text(json.dumps(record)+'\n',encoding='utf-8')
            pending=ReplayPending(journal)
            store=Mock()
            reconcile(pending,store,500,True)
            store.add_local.assert_called_once_with(record['delivery_alert'],400)
            store.add.assert_not_called()
            self.assertFalse(pending)
