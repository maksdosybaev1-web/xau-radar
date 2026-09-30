import json
import pathlib
import sqlite3
import tempfile
import unittest
from contextlib import closing

from app.v2_forward_report import collect, freeze, markdown


class V2ForwardReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'results').mkdir()
        (self.root / 'runtime').mkdir()
        self.write('live.json', {'forward_since':100,'config_hash':'f','quote_time':200,
                                 'config':{'initial_equity':100},'source':{'last':180},
                                 'trades':[{'id':'t1','zone_id':'z','signal_time':130,
                                            'exit_time':150,'pnl':5,'r':1}]})
        self.write('snr_sbr_live.json', {'forward_since':100,'version':'s','quote_time':200})
        self.write('snr_rbs_live.json', {'forward_since':100,'version':'r','quote_time':200})
        self.lines('forward_events.jsonl', [
            {'time':120,'type':'near','zone_id':'z','source_hash':'src','config_hash':'f',
             'observation_quality':'timely','delivery_alert':{'id':'f-plan','type':'fvg_near','analysis_plan':{}}},
            {'time':130,'type':'confirmed','zone_id':'z','source_hash':'src','config_hash':'f',
             'observation_quality':'timely'}])
        self.lines('snr_sbr_forward_events.jsonl', [
            {'time':120,'state':'broken','level_id':'l','source_hash':'src','version':'s',
             'spec_hash':'spec','observation_quality':'timely',
             'delivery_alert':{'id':'s-plan','type':'sbr_sell_plan','analysis_plan':{}}},
            {'time':130,'state':'signal_ready','level_id':'l','source_hash':'src','version':'s',
             'spec_hash':'spec','observation_quality':'late_bar'},
            {'time':140,'state':'expired','level_id':'l','source_hash':'src','version':'s',
             'spec_hash':'spec','observation_quality':'timely'}])
        self.lines('snr_rbs_forward_events.jsonl', [])
        with closing(sqlite3.connect(self.root / 'runtime' / 'alerts.sqlite3')) as db:
            db.execute('CREATE TABLE alerts (id TEXT, status TEXT)')
            db.executemany('INSERT INTO alerts VALUES (?,?)', [('f-plan','sent'),('s-plan','failed')])
            db.commit()

    def write(self, name, value):
        (self.root / 'results' / name).write_text(json.dumps(value), encoding='utf-8')

    def lines(self, name, values):
        (self.root / 'results' / name).write_text(
            ''.join(json.dumps(value) + '\n' for value in values), encoding='utf-8')

    def test_cohorts_delivery_and_paper_outcome_are_separate_and_repeatable(self):
        first = collect(self.root)
        self.assertEqual(first, collect(self.root))
        fvg, sbr, rbs = (first['models'][name] for name in ('FVG','SBR','RBS'))
        self.assertEqual((fvg['plans'],fvg['timely_confirmed_plans'],fvg['paper']['closed']), (1,1,1))
        self.assertEqual((fvg['paper']['average_r'],fvg['paper']['net_pnl_usd']), (1,5))
        self.assertEqual(fvg['paper']['not_timely_confirmed'],0)
        self.assertEqual(fvg['delivery'], {'sent':1})
        self.assertEqual((sbr['plans_without_confirmation_so_far'],sbr['ended_without_confirmation']), (1,1))
        self.assertEqual(sbr['delivery'], {'failed':1})
        self.assertIsNone(sbr['paper'])
        self.assertIsNone(rbs['paper'])
        self.assertIn('не измеряются', markdown(first))

    def test_mixed_model_versions_refuse_a_combined_cohort(self):
        path = self.root / 'results' / 'snr_sbr_forward_events.jsonl'
        with path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'time':150,'state':'level_known','level_id':'other',
                                     'source_hash':'src','version':'old','spec_hash':'spec'}) + '\n')
        with self.assertRaisesRegex(ValueError, 'другую версию'):
            collect(self.root)

    def test_frozen_inputs_are_unaffected_by_later_live_changes(self):
        expected = collect(self.root)
        frozen = freeze(self.root)
        self.lines('forward_events.jsonl', [])
        with closing(sqlite3.connect(self.root / 'runtime' / 'alerts.sqlite3')) as db:
            db.execute("UPDATE alerts SET status='failed' WHERE id='f-plan'")
            db.commit()
        self.assertNotEqual(collect(self.root), expected)
        self.assertEqual(collect(frozen), expected)


if __name__ == '__main__':
    unittest.main()
