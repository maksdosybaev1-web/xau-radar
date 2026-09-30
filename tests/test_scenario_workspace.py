import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from app.notifications import AlertStore, format_alert
from app.scenario_workspace import ScenarioWorkspace


class ScenarioWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / 'results').mkdir()
        self.patcher = patch('app.scenario_workspace.ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.store = AlertStore(self.root / 'alerts.sqlite3')
        self.workspace = ScenarioWorkspace(self.root / 'alerts.sqlite3')
        self.plan = {'id': 'plan-1', 'time': 1000, 'tf': 'M5', 'type': 'sbr_sell_plan',
                     'cooldown_key': 'sbr_sell_plan:level-1', 'symbol': 'XAUUSD',
                     'source': 'MT5 · XAUUSD', 'level_id': 'level-1',
                     'reason': 'Поддержка пробита; нужен ретест',
                     'analysis_plan': {'side': 'short', 'entry': [4275, 4277], 'stop': 4284,
                                       'targets': [4267, 4257, 4248],
                                       'cancel_rule': 'закрытие M5 выше 4284'}}
        self.store.add(self.plan, 1000, 1000, False)
        self.live(4276, 4276.2, 1000)

    def live(self, bid, ask, when, unknown=0):
        (self.root/'runtime').mkdir(exist_ok=True)
        (self.root/'runtime'/'bridge-status.json').write_text(json.dumps(
            {'updated_at':when,'state':'running','quote_time':when,'last_m1':when-60}),encoding='utf-8')
        (self.root / 'results' / 'live.json').write_text(json.dumps({
            'quote_time': when, 'quote': {'bid': bid, 'ask': ask, 'spread': ask-bid},
            'source': {'last':when-60},
            'actual_positions': {'known_risk': 10, 'unknown_count': unknown,
                                 'equity': 10000, 'currency': 'USD'},
            'symbol_details': {'symbol': 'XAUUSD', 'contract_size': 100,
                               'profit_currency': 'USD', 'volume_min': .01, 'volume_step': .01}
        }), encoding='utf-8')

    def test_user_path_persists_and_compares_actual_fill(self):
        view = self.workspace.view(1001)
        row = view['scenarios'][0]
        self.assertEqual(view['active_id'], 'plan-1')
        self.assertEqual(row['stage'], 'waiting')
        self.assertFalse(row['checks']['ready_to_review_entry'])
        self.assertEqual(row['guidance']['tone'],'waiting')
        self.assertEqual(row['checks']['remaining_budget'], 15)
        self.workspace.decide('plan-1', 'watch', 1002)
        with self.assertRaises(ValueError):
            self.workspace.decide('plan-1', 'watch', 1003)
        self.store.add({'id': 'confirmed-1', 'time': 1060, 'tf': 'M5',
                        'type': 'sbr_sell_confirmed', 'symbol': 'XAUUSD',
                        'source': 'MT5 · XAUUSD', 'level_id': 'level-1',
                        'reason': 'M5 закрылась под уровнем', 'price': 4276}, 1060, 1060, False)
        self.live(4276, 4276.2, 1060)
        confirmed = ScenarioWorkspace(self.root / 'alerts.sqlite3').view(1061)['scenarios'][0]
        self.assertEqual(confirmed['stage'], 'confirmed')
        self.assertTrue(confirmed['checks']['ready_to_review_entry'])
        self.assertEqual(confirmed['guidance']['tone'],'ready')
        self.assertIn('SELL',confirmed['guidance']['title'])
        self.workspace.record_trade('plan-1', {'entry': 4278, 'lots': 0.1, 'entry_time': 1062}, 1062)
        self.workspace.close_trade('plan-1', {'exit_price': 4267, 'net_pnl': 110,
                                              'exit_time': 1200}, 1200)
        restored = ScenarioWorkspace(self.root / 'alerts.sqlite3').view(1201)['scenarios'][0]
        self.assertEqual(restored['decision']['action'], 'watch')
        self.assertEqual(restored['trade']['entry'], 4278)
        self.assertEqual(restored['trade']['net_pnl'], 110)
        self.assertIn('plan-1', self.workspace.export_csv(1201).decode('utf-8-sig'))

    def test_assistant_distinguishes_open_trade_risk_and_completed_review(self):
        self.workspace.decide('plan-1','watch',1001)
        self.workspace.record_trade('plan-1',{'entry':4276,'lots':.01,'entry_time':1002},1002)
        self.live(4276,4276.2,1003)
        row=self.workspace.view(1003)['scenarios'][0]
        self.assertEqual(row['guidance']['tone'],'manage')
        self.assertNotIn('нажмите SELL',' '.join(row['guidance']['steps']))
        self.workspace.close_trade('plan-1',{'exit_price':4275,'net_pnl':1,'exit_time':1004},1004)
        self.live(4275,4275.2,1005)
        self.assertEqual(self.workspace.view(1005)['scenarios'][0]['guidance']['tone'],'review')
        self.live(4276,4276.2,1100,unknown=1)
        account=self.workspace.view(1101)['account_review']
        self.assertEqual(account['unknown_risk'],1)
        self.assertIsNone(account['headroom'])

    def position_snapshot(self, positions, when=1100):
        self.live(4276,4276.2,when)
        path=self.root/'results'/'live.json'
        live=json.loads(path.read_text(encoding='utf-8'))
        live['actual_positions'].update(account_id='account-A',positions=positions)
        path.write_text(json.dumps(live),encoding='utf-8')

    def test_mt5_import_uses_server_values_persists_ticket_and_rejects_repeat(self):
        self.workspace.decide('plan-1','watch',1001)
        position={'ticket':123,'position_id':120,'comment':'plan-1','symbol':'XAUUSD',
                  'direction':'sell','entry':4276.5,'lots':.02,'stop':4284}
        self.position_snapshot([position])
        self.assertEqual(self.workspace.view(1100)['scenarios'][0]['mt5_position']['ticket'],123)
        result=self.workspace.import_position('plan-1',{'ticket':123,'entry_time':1050,
             'entry':9999,'lots':99},1101)
        trade=result['scenarios'][0]['trade']
        self.assertEqual((trade['entry'],trade['lots'],trade['entry_time'],trade['mt5_ticket']),
                         (4276.5,.02,1050,123))
        restored=ScenarioWorkspace(self.store.path).view(1102)['scenarios'][0]
        self.assertEqual(restored['trade']['mt5_position_id'],120)
        self.assertEqual(restored['guidance']['tone'],'manage')
        with self.assertRaisesRegex(ValueError,'уже сохранён'):
            self.workspace.import_position('plan-1',{'ticket':123,'entry_time':1050},1102)
        csv=self.workspace.export_csv(1102).decode('utf-8-sig')
        self.assertIn('mt5_ticket,mt5_position_id',csv)
        self.assertIn('123,120',csv)
        other=dict(self.plan,id='plan-2',time=1103)
        self.store.add(other,1103,1103,False)
        self.position_snapshot([dict(position,comment='plan-2')],1104)
        self.workspace.decide('plan-2','watch',1104)
        with self.assertRaisesRegex(ValueError,'уже привязана'):
            self.workspace.import_position('plan-2',{'ticket':123,'entry_time':1104},1104)

    def test_mt5_import_rejects_stale_ambiguous_wrong_side_symbol_and_ticket(self):
        self.workspace.decide('plan-1','watch',1001)
        p={'ticket':123,'position_id':120,'comment':'plan-1','symbol':'XAUUSD',
           'direction':'sell','entry':4276.5,'lots':.02}
        for positions in ([dict(p,direction='buy')],[dict(p,symbol='EURUSD')],
                          [dict(p,comment='plan-1 extra')],[p,dict(p,ticket=124)],[]):
            self.position_snapshot(positions)
            with self.assertRaises(ValueError):
                self.workspace.import_position('plan-1',{'ticket':123,'entry_time':1050},1101)
        self.position_snapshot([p])
        with self.assertRaises(ValueError):
            self.workspace.import_position('plan-1',{'ticket':999,'entry_time':1050},1101)
        with self.assertRaises(ValueError):
            self.workspace.import_position('plan-1',{'ticket':123,'entry_time':1050},1250)
        self.assertIsNone(self.workspace.view(1250)['scenarios'][0]['trade'])

    def test_stale_and_terminal_plan_cannot_be_taken_to_work(self):
        self.live(4276, 4276.2, 800)
        self.assertFalse(self.workspace.view(1001)['quote_fresh'])
        with self.assertRaisesRegex(ValueError, 'актуальны'):
            self.workspace.decide('plan-1', 'watch', 1001)
        self.live(4276, 4276.2, 1100, unknown=1)
        self.assertIsNone(self.workspace.view(1101)['scenarios'][0]['checks']['remaining_budget'])
        self.store.add({'id': 'ended-1', 'time': 1120, 'tf': 'M5', 'type': 'sbr_sell_closed',
                        'cooldown_key': 'sbr_sell_closed:level-1', 'symbol': 'XAUUSD',
                        'level_id': 'level-1', 'reason': 'Ретест отменён'}, 1120, 1120, False)
        self.live(4276, 4276.2, 1120)
        ended = self.workspace.view(1121)['scenarios'][0]
        self.assertEqual(ended['stage'], 'ended')
        self.assertIsNone(ended['valid_until'])
        with self.assertRaises(ValueError):
            self.workspace.decide('plan-1', 'watch', 1121)
        with self.assertRaises(ValueError):
            self.workspace.decide('plan-1', 'skip', 1121)

    def test_confirmed_card_pauses_for_stale_m1_or_bridge_and_recovers_without_new_plan(self):
        self.store.add({'id':'confirmed-1','time':1060,'tf':'M5',
            'type':'sbr_sell_confirmed','symbol':'XAUUSD','source':'MT5 · XAUUSD',
            'level_id':'level-1','reason':'M5 подтвердила','price':4276},1060,1060,False)
        for failure in ('m1','missing_m1','bridge','retrying','missing_status'):
            with self.subTest(failure=failure):
                self.live(4276,4276.2,1060)
                if failure in ('m1','missing_m1'):
                    path=self.root/'results'/'live.json'
                    live=json.loads(path.read_text(encoding='utf-8'))
                    live['source']['last']=800 if failure=='m1' else None
                    path.write_text(json.dumps(live),encoding='utf-8')
                else:
                    path=self.root/'runtime'/'bridge-status.json'
                    if failure=='missing_status':path.unlink()
                    else:path.write_text(json.dumps({'updated_at':800 if failure=='bridge' else 1060,
                        'state':'retrying' if failure=='retrying' else 'running'}),encoding='utf-8')
                paused=self.workspace.view(1061)
                row=paused['scenarios'][0]
                self.assertTrue(paused['quote_fresh'])
                self.assertFalse(paused['data_ready'])
                self.assertEqual(paused['active_id'],'plan-1')
                self.assertEqual(row['stage'],'confirmed')
                self.assertFalse(row['checks']['ready_to_review_entry'])
                self.assertFalse(row['checks']['current'])
                self.assertTrue(row['checks']['data_reason'])
                self.assertEqual(row['guidance']['tone'],'paused')
                with self.assertRaises(ValueError):self.workspace.decide('plan-1','watch',1061)
        self.live(4276,4276.2,1070)
        resumed=self.workspace.view(1071)
        self.assertEqual(resumed['active_id'],'plan-1')
        self.assertTrue(resumed['scenarios'][0]['checks']['ready_to_review_entry'])
        self.assertEqual(len(self.store.recent()),2)

    def test_snr_plan_waits_twelve_m5_bars_before_expiring(self):
        buy = {'id': 'buy-1', 'time': 1000, 'tf': 'M5', 'type': 'rbs_buy_plan',
               'cooldown_key': 'rbs_buy_plan:level-2', 'symbol': 'XAUUSD',
               'source': 'MT5 · XAUUSD', 'level_id': 'level-2',
               'analysis_plan': {'side': 'long', 'entry': [4279, 4281], 'stop': 4272,
                                 'targets': [4289, 4296, 4306],
                                 'cancel_rule': 'закрытие M5 ниже 4279'}}
        self.store.add(buy, 1000, 1000, False)
        fvg = dict(buy, id='fvg-1', type='fvg_near', zone_id='z1',
                   cooldown_key='fvg_near:z1')
        self.store.add(fvg, 1000, 1000, False)
        self.live(4276, 4276.2, 1901)
        rows = {row['id']: row for row in self.workspace.view(1901)['scenarios']}
        self.assertEqual(rows['plan-1']['stage'], 'waiting')
        self.assertEqual(rows['buy-1']['stage'], 'waiting')
        self.assertEqual(rows['fvg-1']['stage'], 'expired')
        self.live(4276, 4276.2, 4600)
        rows = {row['id']: row for row in self.workspace.view(4600)['scenarios']}
        self.assertEqual(rows['plan-1']['stage'], 'waiting')
        self.assertEqual(rows['buy-1']['stage'], 'waiting')
        self.live(4276, 4276.2, 4601)
        row = {r['id']: r for r in self.workspace.view(4601)['scenarios']}['plan-1']
        self.assertEqual(row['stage'], 'expired')
        self.assertIn('60 минут', row['stage_reason'])

    def test_new_events_use_producer_deadline_and_keep_legacy_rows(self):
        with self.store.connect() as db:
            db.execute("UPDATE alerts SET payload=? WHERE id='plan-1'", (json.dumps(
                dict(self.plan, scenario_lifecycle={
                    'version': 1, 'stage': 'waiting', 'valid_until': 1300,
                    'expiry_reason': 'Срок из события модели'})),))
        self.live(4276, 4276.2, 1300)
        self.assertEqual(self.workspace.view(1300)['scenarios'][0]['stage'], 'waiting')
        self.live(4276, 4276.2, 1301)
        row = self.workspace.view(1301)['scenarios'][0]
        self.assertEqual(row['stage'], 'expired')
        self.assertEqual(row['valid_until'], 1300)
        self.assertEqual(row['stage_reason'], 'Срок из события модели')

    def test_telegram_confirmation_reuses_original_three_targets(self):
        confirmation = {'time': 1060, 'type': 'sbr_sell_confirmed', 'symbol': 'XAUUSD',
                        'source': 'MT5 · XAUUSD', 'level_id': 'level-1',
                        'price': 4276, 'stop': 4284, 'target': 4267,
                        'reason': 'M5 закрылась под уровнем'}
        plan = self.store.plan_for(confirmation)
        self.assertEqual(plan, self.plan['analysis_plan'])
        text = format_alert(dict(confirmation, analysis_plan=plan))
        self.assertIn('Шорт 4275–4277', text)
        self.assertIn('ТП 4267, 4257, 4248', text)

    def test_same_level_from_another_source_does_not_confirm_first_plan(self):
        with self.store.connect() as db:
            db.execute("UPDATE alerts SET payload=? WHERE id='plan-1'", (json.dumps(
                dict(self.plan, source_hash='account-A')),))
        other = dict(self.plan, id='plan-2', time=1001, source_hash='account-B')
        self.store.add(other, 1001, 1001, False)
        self.store.add({'id': 'confirmed-B', 'time': 1060, 'tf': 'M5',
                        'type': 'sbr_sell_confirmed', 'symbol': 'XAUUSD',
                        'source': 'MT5 · XAUUSD', 'source_hash': 'account-B',
                        'level_id': 'level-1', 'reason': 'M5 подтвердила'}, 1060, 1060, False)
        rows = {r['id']: r for r in self.workspace.view(1061)['scenarios']}
        self.assertEqual(rows['plan-1']['stage'], 'waiting')
        self.assertEqual(rows['plan-2']['stage'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
