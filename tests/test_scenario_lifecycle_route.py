"""One forward scenario across the journal, delivery, workspace and restart."""
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from app.fvg_forward import FVGForward
from app.notifications import AlertStore
from app.scenario_workspace import ScenarioWorkspace
from app.snr_sbr_live import SBRForward
from app.snr_rbs_live import RBSForward


class ScenarioLifecycleRouteTests(unittest.TestCase):
    def test_fvg_plan_cancellation_and_restart_agree_across_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            (root/'results').mkdir()
            journal=root/'results'/'forward_events.jsonl'
            alerts=AlertStore(root/'alerts.sqlite3')
            near={'id':'near','time':160,'type':'near','zone_id':'zone-1',
                  'direction':'buy','low':3300.0,'high':3305.0,
                  'child_low':3301.0,'child_high':3302.0,
                  'stop':3299.0,'reason':'Цена приблизилась к младшей зоне'}
            cancelled={'id':'cancel','time':220,'type':'cancelled','zone_id':'zone-1',
                       'direction':'buy','low':3300.0,'high':3305.0,
                       'reason':'Свеча закрылась за дальней границей старшей зоны'}
            forward=lambda: FVGForward('XAUUSD','MT5|demo|login','rules',100,journal,alerts)
            live=root/'results'/'live.json'
            def quote(when):
                live.write_text(json.dumps({'quote_time':when,'quote':{
                    'bid':3301.5,'ask':3301.7,'spread':0.2}}),encoding='utf-8')
            sent=[]
            with patch('app.fvg_forward.public_settings',return_value={'enabled':True}), \
                 patch('app.scenario_workspace.ROOT',root):
                forward().persist([near],170,169,3302.5,3302.7)
                first=json.loads(journal.read_text(encoding='utf-8').splitlines()[0])
                self.assertEqual(first['observation_quality'],'timely')
                self.assertEqual(first['delivery_alert']['scenario_lifecycle']['stage'],'near')
                quote(170)
                workspace=ScenarioWorkspace(alerts.path)
                plan=workspace.view(171)['scenarios'][0]
                self.assertEqual(plan['id'],first['delivery_alert']['id'])
                self.assertEqual(plan['stage'],'near')
                self.assertEqual(plan['valid_until'],first['delivery_alert']['scenario_lifecycle']['valid_until'])
                self.assertEqual(plan['plan'],first['delivery_alert']['analysis_plan'])
                alerts.drain(171,{'enabled':True},lambda body,_:sent.append(body))
                self.assertEqual(len(sent),1)
                self.assertIn('FVG-zone-1',sent[0])
                self.assertIn('Лонг 3301–3302',sent[0])
                self.assertIn('актуален до',sent[0].lower())
                self.assertEqual(workspace.view(172)['scenarios'][0]['plan_delivery'],'sent')

                forward().persist([cancelled],230,229)
                second=json.loads(journal.read_text(encoding='utf-8').splitlines()[1])
                self.assertEqual(second['delivery_alert']['scenario_lifecycle']['stage'],'ended')
                quote(230)
                ended=workspace.view(231)['scenarios'][0]
                self.assertEqual(ended['stage'],'ended')
                self.assertEqual(ended['stage_at'],second['time'])
                self.assertEqual(ended['stage_reason'],second['reason'])
                self.assertIsNone(ended['valid_until'])
                alerts.drain(231,{'enabled':True},lambda body,_:sent.append(body))
                self.assertEqual(len(sent),2)
                self.assertIn('FVG-zone-1',sent[1])
                self.assertIn(second['reason'],sent[1])
                self.assertIn('Сценарий отменён',sent[1])

                forward().persist([near,cancelled],240,239)
                self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()),2)
                self.assertEqual(len(alerts.recent()),2)
                self.assertEqual(workspace.view(231)['scenarios'][0]['stage'],'ended')

    def test_snr_plan_terminal_and_restart_agree_across_surfaces(self):
        for model,forward_type,setting_path,break_reason in (
            ('SBR',SBRForward,'app.snr_sbr_live.public_settings','close_below_support'),
            ('RBS',RBSForward,'app.snr_rbs_live.public_settings','close_above_resistance'),
        ):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp)
                (root/'results').mkdir()
                journal=root/'results'/'forward_events.jsonl'
                alerts=AlertStore(root/'alerts.sqlite3')
                forward=forward_type('XAUUSD','MT5|demo|login',100,journal,alerts)
                forward.finish_bootstrap()
                forward.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
                events=[{'id':'break','time':160,'state':'broken','level_id':'level-1',
                         'reason':break_reason},
                        {'id':'end','time':220,'state':'retest_unconfirmed',
                         'level_id':'level-1','reason':'first_retest_failed'}]
                forward.model.step=lambda _:forward.model.events.append(events.pop(0))
                sent=[]
                with patch(setting_path,return_value={'enabled':True}), \
                     patch('app.scenario_workspace.ROOT',root):
                    forward.ingest({},170,169)
                    first=json.loads(journal.read_text(encoding='utf-8').splitlines()[0])
                    self.assertEqual(first['delivery_alert']['scenario_lifecycle']['stage'],'waiting')
                    workspace=ScenarioWorkspace(alerts.path)
                    plan=workspace.view(171)['scenarios'][0]
                    self.assertEqual(plan['stage'],'waiting')
                    self.assertEqual(plan['valid_until'],first['delivery_alert']['scenario_lifecycle']['valid_until'])
                    self.assertEqual(plan['plan'],first['delivery_alert']['analysis_plan'])
                    alerts.drain(171,{'enabled':True},lambda body,_:sent.append(body))
                    self.assertEqual(len(sent),1)
                    self.assertIn(model+'-level-1',sent[0])
                    self.assertIn('ТП ',sent[0])
                    self.assertIn('Актуален до',sent[0])

                    forward.ingest({},230,229)
                    second=json.loads(journal.read_text(encoding='utf-8').splitlines()[1])
                    self.assertEqual(second['delivery_alert']['scenario_lifecycle']['stage'],'ended')
                    ended=workspace.view(231)['scenarios'][0]
                    self.assertEqual(ended['stage'],'ended')
                    self.assertEqual(ended['stage_at'],second['time'])
                    self.assertEqual(ended['stage_reason'],second['delivery_alert']['reason'])
                    self.assertIsNone(ended['valid_until'])
                    alerts.drain(231,{'enabled':True},lambda body,_:sent.append(body))
                    self.assertEqual(len(sent),2)
                    self.assertIn(model+'-level-1',sent[1])
                    self.assertIn(ended['stage_reason'],sent[1])

                    resumed=forward_type('XAUUSD','MT5|demo|login',240,journal,alerts)
                    resumed.finish_bootstrap()
                    resumed.state(240,239)
                    self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()),2)
                    self.assertEqual(len(alerts.recent()),2)
                    self.assertEqual(workspace.view(231)['scenarios'][0]['stage'],'ended')


if __name__=='__main__':
    unittest.main()
