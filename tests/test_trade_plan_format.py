import unittest

from app.notifications import format_alert, format_trade_plan
from app.analysis_plans import fvg_near_plan, snr_break_plan
from decimal import Decimal


class TradePlanFormatTests(unittest.TestCase):
    def test_user_examples_keep_exact_levels(self):
        self.assertEqual(format_trade_plan({
            'side': 'long', 'entry': [4279, 4281], 'stop': 4272,
            'targets': [4289, 4296, 4306],
            'cancel_rule': 'возврат и закрепление M5 ниже 4272',
        }), 'Лонг 4279–4281\nСтоп 4272\nТП 4289, 4296, 4306\n'
            'Отмена: возврат и закрепление M5 ниже 4272')
        self.assertEqual(format_trade_plan({
            'side': 'short', 'entry': [4275, 4277], 'stop': 4284,
            'targets': [4267, 4257, 4248], 'retest': True,
            'cancel_rule': 'закрепление M15 выше 4284',
        }), 'Шорт 4275–4277 на ретесте\nСтоп 4284\nТП 4267, 4257, 4248\n'
            'Отмена: закрепление M15 выше 4284')

    def test_inconsistent_levels_cannot_be_presented_as_plan(self):
        invalid = {'side': 'long', 'entry': [4279, 4281], 'stop': 4284,
                   'targets': [4289, 4296, 4306], 'cancel_rule': 'M5 ниже 4272'}
        with self.assertRaises(ValueError):
            format_trade_plan(invalid)
        invalid['stop'] = 4272
        invalid['targets'] = [4289, 4306, 4296]
        with self.assertRaises(ValueError):
            format_trade_plan(invalid)

    def test_generated_xauusd_prices_respect_broker_tick(self):
        plans = [snr_break_plan({'low_band':4154.983857,'high_band':4156.016143,'atr':5.16},
                                'sell', 100, 0.01),
                 fvg_near_plan({'type':'near','direction':'buy','child_low':4279.003,
                                'child_high':4281.007,'low':4272.005,'high':4285.0,
                                'time':100},4278,4278.2,0.01)]
        for plan in plans:
            self.assertIsNotNone(plan)
            for price in [*plan['entry'],plan['stop'],*plan['targets']]:
                self.assertEqual(Decimal(str(price)) % Decimal('0.01'), 0)
            self.assertIn('Отмена:',format_trade_plan(plan))
        self.assertIn('выше 4156.02',plans[0]['cancel_rule'])
        self.assertIn('ниже 4279',plans[1]['cancel_rule'])

    def test_fvg_confirmation_uses_why_and_scenario_id(self):
        text=format_alert({'type':'fvg_confirmed','symbol':'XAUUSD','time':100,
                           'zone_id':'zone-42','direction':'buy','low':4272,
                           'high':4285,'child_low':4279,'child_high':4281,
                           'stop':4271,'reason':'M5 закрылась над зоной'})
        self.assertIn('Сценарий: FVG-zone-42',text)
        self.assertIn('Почему: M5 закрылась над зоной',text)

    def test_preliminary_plan_explains_action_without_promising_safe_entry(self):
        plan={'side':'long','entry':[4279,4281],'stop':4272,
              'targets':[4289,4296,4306],'cancel_rule':'закрытие M5 ниже 4279'}
        text=format_alert({'type':'rbs_buy_plan','symbol':'XAUUSD','time':100,
                           'level_id':'level-42','analysis_plan':plan})
        self.assertIn('Проверьте возможную покупку',text)
        self.assertIn('Лонг 4279–4281',text)
        self.assertIn('расчётные ориентиры',text)
        self.assertIn('заявки нет',text)

    def test_snr_calculated_targets_do_not_pose_as_structural_target(self):
        plan={'side':'short','entry':[4136.98,4138.32],'stop':4138.99,
              'targets':[4136.31,4134.97,4133.63],
              'cancel_rule':'закрытие M5 выше 4138.32',
              'target_method':'midpoint_r_1_2_3'}
        text=format_alert({'type':'sbr_sell_plan','symbol':'XAUUSD','time':100,
                           'level_id':'level-42','analysis_plan':plan})
        self.assertIn('ТП рассчитаны как 1R/2R/3R',text)
        self.assertIn('структурную цель M15 проверим после ретеста',text)

    def test_telegram_uses_the_same_plan_deadline_as_the_card(self):
        plan={'side':'short','entry':[4136.98,4138.32],'stop':4138.99,
              'targets':[4136.31,4134.97,4133.63],
              'cancel_rule':'закрытие M5 выше 4138.32'}
        text=format_alert({'type':'sbr_sell_plan','symbol':'XAUUSD','time':160,
                           'level_id':'level-42','analysis_plan':plan,
                           'scenario_lifecycle':{'version':1,'stage':'waiting',
                                                 'valid_until':3760}})
        self.assertIn('Актуален до 01.01.1970 01:02 UTC, если не отменён раньше.',text)



if __name__ == '__main__':
    unittest.main()
