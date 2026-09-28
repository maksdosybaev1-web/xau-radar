import json
import pathlib
import tempfile
import unittest

from app.forward_assessment import snapshot


class ForwardAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        (self.root/'results').mkdir()

    def save(self,live,records):
        (self.root/'results'/'live.json').write_text(json.dumps(live),encoding='utf-8')
        (self.root/'results'/'forward_events.jsonl').write_text(
            ''.join(json.dumps(item)+'\n' for item in records),encoding='utf-8')

    def test_stale_quote_and_no_events_are_not_a_forward_result(self):
        live={'mode':'live','forward_since':100,'config_hash':'rules','quote_time':100,
              'source':{'last':99},'curve':[],'trades':[]}
        self.save(live,[])
        result=snapshot(self.root,now=300)
        self.assertEqual(result['status'],'waiting_for_fresh_quote')
        self.assertEqual(result['timely_confirmed'],0)
        self.assertIsNone(result['average_r'])

    def test_archived_curve_points_count_toward_forward_sample(self):
        live={'mode':'live','forward_since':100,'config_hash':'rules','quote_time':200,
              'curve_archived_forward_count':600,'curve':[{'time':150}], 'trades':[]}
        self.save(live,[])
        self.assertEqual(snapshot(self.root,now=205)['new_m1'],601)

    def test_only_timely_confirmed_with_complete_paper_path_counts(self):
        live={'mode':'live','forward_since':100,'config_hash':'rules','quote_time':200,
              'source':{'last':180},'curve':[{'time':90},{'time':150},{'time':180}],
              'trades':[
                  {'zone_id':'z1','signal_time':160,'exit_time':190,'r':1.5,'pnl':15},
                  {'zone_id':'z2','signal_time':170,'exit_time':190,'r':2,'pnl':20},
                  {'zone_id':'z3','signal_time':175,'exit_time':190,'r':-1,'pnl':-10,'data_gap':True}]}
        records=[{'time':160,'type':'confirmed','zone_id':'z1','config_hash':'rules','observation_quality':'timely'},
                 {'time':170,'type':'confirmed','zone_id':'z2','config_hash':'rules','observation_quality':'late_bar'},
                 {'time':175,'type':'confirmed','zone_id':'z3','config_hash':'rules','observation_quality':'timely'}]
        self.save(live,records)
        result=snapshot(self.root,now=205)
        self.assertEqual(result['status'],'collecting')
        self.assertEqual(result['new_m1'],2)
        self.assertEqual(result['timely_confirmed'],2)
        self.assertEqual(result['events']['late_bar'],1)
        self.assertEqual(result['paper_closed'],1)
        self.assertEqual(result['paper_data_gap'],1)
        self.assertEqual((result['net_pnl'],result['average_r']),(15,1.5))

    def test_changed_rules_are_reported_as_error_not_zero_events(self):
        live={'mode':'live','forward_since':100,'config_hash':'current','quote_time':200,
              'source':{'last':180},'curve':[],'trades':[]}
        self.save(live,[{'time':160,'type':'confirmed','config_hash':'old',
                         'observation_quality':'timely'}])
        with self.assertRaisesRegex(ValueError,'другой версии'):
            snapshot(self.root,now=205)

    def test_streamed_counts_preserve_duplicates_boundary_and_unknown_quality(self):
        live={'mode':'live','forward_since':100,'config_hash':'rules','quote_time':200,
              'curve':[],'trades':[{'zone_id':'z1','signal_time':160,'exit_time':None}]}
        near={'time':100,'type':'near','config_hash':'rules','observation_quality':'timely'}
        confirmed={'time':160,'type':'confirmed','zone_id':'z1',
                   'config_hash':'rules','observation_quality':'timely'}
        records=[{'time':99,'config_hash':'old'}]+[near]*1000+[confirmed]*2+[
            {'time':170,'type':'cancelled','config_hash':'rules'}]
        self.save(live,records)
        with (self.root/'results'/'forward_events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write('\n   \n')
        result=snapshot(self.root,now=205)
        self.assertEqual(result['events'],{'timely':1002,'unknown':1})
        self.assertEqual(result['timely_near'],1000)
        self.assertEqual(result['timely_confirmed'],1)
        self.assertEqual(result['paper_open'],1)
        self.assertEqual(result['paper_closed'],0)
        self.assertIsNone(result['net_pnl'])


if __name__=='__main__':unittest.main()
