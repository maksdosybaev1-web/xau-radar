import json
import unittest
import uuid
from unittest.mock import patch

from app.datafeed import ROOT
from app.nested_forward import NestedForward
from app.tick_archive import TickArchive
from tests.test_nested_m15_m5_m1 import add_chain_zones, minute


class NestedForwardTests(unittest.TestCase):
    def test_event_index_failure_rebuilds_from_journal_without_duplicate(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        try:
            forward = NestedForward('XAUUSD', 'MT5|test|XAUUSD', 100, path)
            for journal in (forward.journal, forward.outcomes_journal, forward.tick_checks_journal):
                self.addCleanup(journal.with_suffix(journal.suffix + '.keys.sqlite3').unlink, missing_ok=True)
            event = dict(id='one', type='probe', time=160)
            with patch.object(forward.seen, 'add', side_effect=OSError('index unavailable')):
                forward.save('nested_event', event, 160, 170, 169)
            forward.save('nested_event', event, 160, 175, 174)
            self.assertEqual(forward.state(175, 174)['forward_events_total'], 1)
            self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()), 1)
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)

    def make_forward(self, path, startup=2700):
        forward = NestedForward('XAUUSD', 'MT5|test|XAUUSD', startup, path)
        for journal in (forward.journal, forward.outcomes_journal, forward.tick_checks_journal):
            self.addCleanup(journal.with_suffix(journal.suffix + '.keys.sqlite3').unlink, missing_ok=True)
        add_chain_zones(forward.model)
        forward.model.link(2700)
        return forward

    def test_long_stream_keeps_working_window_and_outcome_after_trim(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        try:
            forward = NestedForward('XAUUSD', 'MT5|test|XAUUSD', 0, path)
            for journal in (forward.journal, forward.outcomes_journal, forward.tick_checks_journal):
                self.addCleanup(journal.with_suffix(journal.suffix + '.keys.sqlite3').unlink, missing_ok=True)
            for t in range(0, 1300*60, 60):
                forward.ingest(minute(t, bid=120), t+62, t+61)
            self.assertLessEqual(len(forward.rows), 600)
            self.assertLessEqual(len(forward.by_time), 600)
            self.assertLessEqual(len(forward.atr_by_end), 600)
            self.assertEqual(forward.model.events, [])
            touch_time = 1300*60
            model = forward.model
            model.zone(15, {'origin':touch_time-2700, 'created':touch_time,
                            'direction':'buy', 'low':90, 'high':110}, 'pivot_candle_range')
            model.zone(5, {'origin':touch_time-1800, 'created':touch_time-900,
                           'direction':'buy', 'low':95, 'high':105}, 'fvg')
            model.zone(1, {'origin':touch_time-1500, 'created':touch_time-600,
                           'direction':'buy', 'low':98, 'high':102}, 'fvg')
            model.link(touch_time)
            first = minute(touch_time, bid=103)
            first.update(bid_low=101.5, bid_high=103.2, bid_close=102.8,
                         ask_low=101.6, ask_high=103.3, ask_close=102.9)
            forward.ingest(first, touch_time+62, touch_time+61)
            forward.ingest(minute(touch_time+60, bid=111), touch_time+122, touch_time+121)
            self.assertEqual(forward.state(touch_time+122, touch_time+121)['paper_outcomes_total'], 2)
            self.assertEqual(forward.model.events, [])
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)

    def test_forward_boundary_and_restart_do_not_relabel_history(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        try:
            forward = self.make_forward(path)
            forward.ingest(minute(2700, bid=99), 2762, 2761)
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['type'] for r in rows],
                             ['touch_observed', 'touch_limit', 'after_close'])
            self.assertEqual(rows[1]['state'], 'limit_crossed_at_open')
            self.assertNotEqual(rows[1]['id'], rows[2]['id'])
            self.assertEqual(rows[1]['quote_quality'], 'estimated_ask_ohlc')
            self.assertTrue(all(r['result_status']=='not_evaluated' for r in rows))
            restarted = self.make_forward(path, 9999)
            restarted.ingest(minute(2700, bid=99), 10000, 10000)
            self.assertEqual(restarted.forward_since, 2700)
            self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()), 3)
            self.assertEqual(restarted.state(10000, 10000)['forward_events_this_run'], 0)
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)

    def test_confirmation_waits_for_exact_next_m1(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        try:
            forward = self.make_forward(path)
            first = minute(2700, bid=101.9)
            first.update(bid_high=103.5, bid_low=101.8, bid_close=103.0,
                         ask_high=103.6, ask_low=101.9, ask_close=103.1)
            forward.ingest(first, 2762, 2761)
            self.assertTrue(forward.state(2762, 2761)['pending_next_m1'])
            self.assertEqual([json.loads(x)['type'] for x in path.read_text(encoding='utf-8').splitlines()],
                             ['touch_observed', 'touch_limit'])
            forward.ingest(minute(2760, bid=103.2), 2822, 2821)
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            after = [r for r in rows if r['type']=='after_close']
            self.assertEqual(len(after), 1)
            self.assertEqual(after[0]['state'], 'next_open_candidate')
            self.assertEqual(after[0]['entry_quote_time'], 2760)
            self.assertFalse(after[0]['fill_known'])
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)

    def test_paper_outcome_waits_for_future_m1_and_is_deduplicated(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        try:
            forward = self.make_forward(path)
            for t in range(1800, 2700, 60):
                forward.ingest(minute(t, bid=120), t+62, t+61)
            first = minute(2700, bid=103)
            first.update(bid_low=101.5, bid_high=103.2, bid_close=102.8,
                         ask_low=101.6, ask_high=103.3, ask_close=102.9)
            forward.ingest(first, 2762, 2761)
            state = forward.state(2762, 2761)
            self.assertEqual(state['paper_outcomes_pending'], 1)
            self.assertEqual(state['paper_outcomes_total'], 1)
            self.assertEqual(state['paper_outcomes_recent'][0]['state'], 'not_eligible')
            forward.ingest(minute(2760, bid=111), 2822, 2821)
            states = [r['state'] for r in forward.state(2822, 2821)['paper_outcomes_recent']]
            self.assertEqual(states, ['not_eligible', 'closed'])
            closed = forward.state(2822, 2821)['paper_outcomes_recent'][-1]
            self.assertEqual(closed['mode'], 'touch_limit')
            self.assertFalse(closed['broker_fill_verified'])
            self.assertEqual(closed['candidate_observation_quality'], 'timely')
            resumed = self.make_forward(path, 9999)
            for t in range(1800, 2700, 60):
                resumed.ingest(minute(t, bid=120), 10000, 10000)
            resumed.ingest(first, 10000, 10000)
            resumed.ingest(minute(2760, bid=111), 10000, 10000)
            self.assertEqual(resumed.state(10000, 10000)['paper_outcomes_total'], 2)
            self.assertEqual(resumed.state(10000, 10000)['paper_outcomes_this_run'], 0)
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)

    def test_tick_check_records_quote_evidence_once(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        db_path = ROOT/'runtime'/('nested-ticks-'+uuid.uuid4().hex+'.sqlite3')
        try:
            forward = self.make_forward(path)
            forward.ingest(minute(2700, bid=99), 2762, 2761)
            archive = TickArchive(db_path, startup=2700)
            with archive.connect() as db:
                db.execute('INSERT INTO ticks VALUES (?,?,?,?,?,?)',
                           ('XAUUSD',2700500,99,99.1,2,2761))
                db.execute('INSERT INTO tick_pulls(symbol,start_sec,end_sec,observed_at,status,returned,stored,error_code) VALUES (?,?,?,?,?,?,?,?)',
                           ('XAUUSD',2700,2760,2761,'ok',1,1,None))
            forward.check_ticks(archive, 2762)
            check = forward.state(2762, 2761)['tick_checks_recent'][0]
            self.assertEqual(check['status'], 'zone_quote_observed')
            self.assertEqual(check['coverage']['status'], 'queried_unverified')
            self.assertFalse(check['broker_fill_verified'])
            resumed = self.make_forward(path, 9999)
            resumed.check_ticks(archive, 10000)
            self.assertEqual(resumed.state(10000, 10000)['tick_checks_total'], 1)
            self.assertEqual(resumed.state(10000, 10000)['tick_checks_this_run'], 0)
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)
            db_path.unlink(missing_ok=True)

    def test_missing_ticks_stay_unknown_until_window_expires(self):
        path = ROOT/'runtime'/('nested-test-'+uuid.uuid4().hex+'.jsonl')
        db_path = ROOT/'runtime'/('nested-ticks-'+uuid.uuid4().hex+'.sqlite3')
        try:
            forward = self.make_forward(path)
            forward.ingest(minute(2700, bid=99), 2762, 2761)
            archive = TickArchive(db_path, startup=2700)
            forward.check_ticks(archive, 2762)
            self.assertEqual(forward.state(2762, 2761)['tick_checks_pending'], 1)
            forward.check_ticks(archive, 2851)
            check = forward.state(2851, 2761)['tick_checks_recent'][0]
            self.assertEqual(check['status'], 'insufficient_tick_coverage')
            self.assertIsNone(check['zone_quote'])
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix('.meta.json').unlink(missing_ok=True)
            path.with_name(path.stem+'_outcomes.jsonl').unlink(missing_ok=True)
            path.with_name(path.stem+'_tick_checks.jsonl').unlink(missing_ok=True)
            db_path.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
