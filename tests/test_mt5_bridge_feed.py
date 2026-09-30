import types
import unittest

from app.mt5_bridge import RatesUnavailable, ClosedM1Stale, closed_m1_rates, m1_row, require_recent_m1, account_risk
from app.operations import summarize_health


class FakeMT5:
    TIMEFRAME_M1=1

    def __init__(self,rows,maxbars=100000):
        self.rows=rows
        self.maxbars=maxbars
        self.calls=[]

    def terminal_info(self):
        return types.SimpleNamespace(maxbars=self.maxbars)

    def copy_rates_from_pos(self,symbol,timeframe,start,count):
        self.calls.append((symbol,timeframe,start,count))
        return self.rows

    def last_error(self):
        return (-1,'Terminal: Call failed')


class MT5BridgeFeedTests(unittest.TestCase):
    def test_position_link_fields_are_read_only_and_account_scoped(self):
        p=types.SimpleNamespace(ticket=123,identifier=120,comment='plan-1',type=1,
            symbol='XAUUSD',volume=.02,price_open=4276,sl=4284,price_current=4275)
        terminal=types.SimpleNamespace(account_info=lambda:types.SimpleNamespace(
            server='demo',login=7,currency='USD',equity=10000),positions_get=lambda:[p],
            order_calc_profit=lambda *args:-18)
        result=account_risk(terminal)
        self.assertEqual(result['positions'][0]['comment'],'plan-1')
        self.assertEqual(result['positions'][0]['position_id'],120)
        self.assertEqual(result['known_risk'],18)
        terminal.account_info=lambda:types.SimpleNamespace(server='demo',login=8,currency='USD',equity=10000)
        self.assertNotEqual(account_risk(terminal)['account_id'],result['account_id'])

    def test_live_and_replay_use_same_bid_spread_conversion(self):
        rate={'time':1000,'tick_volume':8,'spread':20,'open':3300.0,
              'high':3302.0,'low':3299.0,'close':3301.0}
        row=m1_row(rate,0.01)
        self.assertEqual((row['bid_high'],row['ask_high'],row['ask_low'],row['volume']),
                         (3302.0,3302.2,3299.2,8.0))

    def test_recovers_more_than_old_100_bar_window(self):
        mt5=FakeMT5([{'time':160}])
        rows=closed_m1_rates(mt5,'XAUUSD',1000,1000+181*60,True)
        self.assertIs(rows,mt5.rows)
        self.assertEqual(mt5.calls,[('XAUUSD',1,1,183)])

    def test_bootstrap_keeps_warmup_and_catches_up(self):
        mt5=FakeMT5([{'time':160}])
        closed_m1_rates(mt5,'XAUUSD',1000,1000+60,False)
        self.assertEqual(mt5.calls[0][3],30000)

    def test_incomplete_history_fails_before_partial_replay(self):
        mt5=FakeMT5([{'time':160}],maxbars=120)
        with self.assertRaisesRegex(RuntimeError,'Разрыв наблюдения'):
            closed_m1_rates(mt5,'XAUUSD',1000,1000+181*60,True)
        self.assertEqual(mt5.calls,[])

    def test_terminal_error_code_reaches_operational_status(self):
        mt5=FakeMT5(None)
        with self.assertRaises(RatesUnavailable) as caught:
            closed_m1_rates(mt5,'XAUUSD',1000,1060,True)
        self.assertEqual(caught.exception.code,-1)
        health=summarize_health({'state':'retrying','updated_at':100,'error_type':'RatesUnavailable',
                                 'error_code':caught.exception.code},{'state':'running','updated_at':100},
                                {'enabled':True,'configured':True},100)
        self.assertEqual(health['bridge']['error_code'],-1)
        self.assertFalse(health['ready_for_new_alerts'])

    def test_fresh_tick_does_not_authorize_scenarios_with_stale_closed_m1(self):
        require_recent_m1(940,1000)
        require_recent_m1(820,1000)
        with self.assertRaises(ClosedM1Stale):require_recent_m1(819,1000)
        with self.assertRaises(ClosedM1Stale):require_recent_m1(None,1000)


if __name__=='__main__':unittest.main()
