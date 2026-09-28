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
        (self.root / 'results' / 'live.json').write_text(json.dumps({
            'quote_time': when, 'quote': {'bid': bid, 'ask': ask, 'spread': ask-bid},
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
        self.workspace.record_trade('plan-1', {'entry': 4278, 'lots': 0.1, 'entry_time': 1062}, 1062)
        self.workspace.close_trade('plan-1', {'exit_price': 4267, 'net_pnl': 110,
                                              'exit_time': 1200}, 1200)
        restored = ScenarioWorkspace(self.root / 'alerts.sqlite3').view(1201)['scenarios'][0]
        self.assertEqual(restored['decision']['action'], 'watch')
        self.assertEqual(restored['trade']['entry'], 4278)
        self.assertEqual(restored['trade']['net_pnl'], 110)
        self.assertIn('plan-1', self.workspace.export_csv(1201).decode('utf-8-sig'))

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
