import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.snr_sbr_live import SBRForward
from app.snr_rbs_live import RBSForward
from app.forward_memory import TERMINAL_STATES, prune_finished_levels


class ForwardTailTests(unittest.TestCase):
    def test_tail_survives_restart_without_reading_journal_on_poll(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / 'events.jsonl'
                seed = cls('XAUUSD', 'test', 100, journal, Mock())
                records = [dict(id=str(i), dedup_key=str(i), **seed.identity) for i in range(80)]
                journal.write_text('\n'.join(json.dumps(r) for r in records)+'\n', encoding='utf-8')
                forward = cls('XAUUSD', 'test', 100, journal, Mock())
                forward.state(160, 160)  # One-time streaming outbox recovery.
                with patch.object(Path, 'open', side_effect=AssertionError('unexpected file read')):
                    state = forward.state(160, 160)
                self.assertEqual(state['events'], records[-50:])
                self.assertEqual(state['forward_events_total'], 80)
                forward.finish_bootstrap()
                event = dict(id='new', time=160, state='armed')
                forward.model.step = lambda row: forward.model.events.append(event)
                forward.ingest({}, 170, 165)
                with patch.object(Path, 'open', side_effect=AssertionError('unexpected file read')):
                    state = forward.state(170, 165)
                self.assertEqual(len(state['events']), 50)
                self.assertEqual(state['events'][0]['id'], '31')
                self.assertEqual(state['events'][-1]['id'], 'new')
                self.assertEqual(state['forward_events_total'], 81)
                forward.ingest({}, 170, 165)
                self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()), 81)
                restored = cls('XAUUSD', 'test', 175, journal, Mock())
                self.assertEqual(restored.state(180, 180)['events'], state['events'])
                self.assertEqual(restored.state(180, 180)['forward_events_total'], 81)

    def test_durable_event_visible_when_queue_fails(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                store = Mock()
                store.add.side_effect = OSError('queue unavailable')
                forward = cls('XAUUSD', 'test', 100, Path(tmp)/'events.jsonl', store)
                forward.finish_bootstrap()
                event = dict(id='new', time=160, state='signal_ready', level_id='one',
                             source='test', stop=101, target=95)
                forward.model.levels['one'] = {'first_touch': {'close': 99}, 'level': 100}
                forward.model.step = lambda row: forward.model.events.append(event)
                with patch(cls.__module__+'.public_settings', return_value={'enabled': False}):
                    with self.assertRaises(OSError):
                        forward.ingest({}, 170, 165)
                    self.assertEqual(forward.model.events, [event])
                    store.add.side_effect = None
                    self.assertEqual(forward.state(175, 175)['events'][0]['id'], 'new')
                self.assertEqual(len(forward.recent_events), 1)

    def test_live_event_cleanup_matches_untrimmed_model(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp)/'events.jsonl'
                forward = cls('XAUUSD', 'test', 300*60, journal, Mock())
                model = forward.model
                reference = type(model)(model.spec_hash, model.source_hash,
                                        symbol=model.symbol, source=model.source, signals_only=True)
                expected = []
                for i in range(3600):
                    if i == 300:
                        forward.finish_bootstrap()
                    price = 100 + 4*math.sin(i/20) + math.sin(i/7)
                    row = {'time': i*60, 'volume': 1}
                    for side, offset in [('bid', 0), ('ask', .1)]:
                        row.update({side+'_open': price+offset, side+'_close': price+offset,
                                    side+'_high': price+offset+.2, side+'_low': price+offset-.2})
                    before = len(reference.events)
                    reference.step(row)
                    if i >= 300:
                        expected.extend(reference.events[before:])
                    forward.ingest(row, i*60+60, i*60+60)
                    self.assertEqual(model.events, [])
                saved = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
                self.assertGreater(len(expected), 50)
                self.assertEqual([{key: record[key] for key in event}
                                  for record, event in zip(saved, expected)], expected)
                self.assertEqual(len(saved), len(expected))
                active = {key: level for key, level in reference.levels.items()
                          if level['state'] not in TERMINAL_STATES}
                self.assertEqual(model.levels, active)
                self.assertLess(len(model.levels), len(reference.levels))
                self.assertEqual(model.bars, reference.bars)
                self.assertEqual(model.trades, reference.trades)
                self.assertEqual(forward.state(3600*60, 3600*60)['events'], saved[-50:])

    def test_write_failure_keeps_unpersisted_event(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                forward = cls('XAUUSD', 'test', 100, Path(tmp)/'events.jsonl', Mock())
                forward.finish_bootstrap()
                event = dict(id='new', time=160, state='armed')
                forward.model.step = lambda row: forward.model.events.append(event)
                with patch.object(Path, 'open', side_effect=OSError('disk unavailable')):
                    with self.assertRaises(OSError):
                        forward.ingest({}, 170, 165)
                self.assertEqual(forward.model.events, [event])
                self.assertEqual(list(forward.recent_events), [])
                self.assertEqual(len(forward.seen), 0)

    def test_only_terminal_live_levels_are_removed(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                forward = cls('XAUUSD', 'test', 100, Path(tmp)/'events.jsonl', Mock())
                model = forward.model
                states = set(TERMINAL_STATES) | {'level_known', 'broken', 'pending_entry',
                                               'confirmed', 'observing', 'future_state'}
                model.levels = {state: {'state': state} for state in states}
                model.signals_only = False
                prune_finished_levels(model)
                self.assertEqual(set(model.levels), states)
                model.signals_only = True
                model.trades = [{'state': 'observing', 'level_id': 'closed'}]
                prune_finished_levels(model)
                self.assertEqual(set(model.levels), (states - TERMINAL_STATES) | {'closed'})

    def test_signal_level_removed_only_after_durable_handoff(self):
        for cls in (SBRForward, RBSForward):
            with self.subTest(model=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                store = Mock()
                forward = cls('XAUUSD', 'test', 100, Path(tmp)/'events.jsonl', store)
                forward.finish_bootstrap()
                event = dict(id='signal', time=160, state='signal_ready', level_id='one',
                             source='test', stop=101, target=95)
                forward.model.levels['one'] = {'state': 'signal_ready',
                                             'first_touch': {'close': 99}, 'level': 100}
                forward.model.step = lambda row: forward.model.events.append(event)
                store.add.side_effect = OSError('queue unavailable')
                with patch(cls.__module__+'.public_settings', return_value={'enabled': False}):
                    with self.assertRaises(OSError):
                        forward.ingest({}, 170, 165)
                    self.assertIn('one', forward.model.levels)
                    store.add.side_effect = None
                    forward.model.step = lambda row: None
                    forward.ingest({}, 175, 175)
                self.assertNotIn('one', forward.model.levels)
                saved = json.loads(forward.journal.read_text(encoding='utf-8'))
                self.assertEqual(saved['delivery_alert']['level'], 100)
                self.assertEqual(saved['delivery_alert']['price'], 99)
                self.assertEqual(saved['delivery_alert']['stop'], 101)
                self.assertEqual(saved['delivery_alert']['target'], 95)


if __name__ == '__main__':
    unittest.main()
