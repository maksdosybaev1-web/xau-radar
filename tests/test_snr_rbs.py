import unittest
from unittest.mock import patch

from app.snr_rbs import RBSResearch, long_exit, resistance, target


def bar(t, o=100, h=101, l=99, c=100):
    return {'time': t, 'end': t+300, 'open': o, 'high': h, 'low': l,
            'close': c, 'volume': 1}


def minute(t, bid=100, spread=.1):
    return {'time': t, 'bid_open': bid, 'bid_high': bid+.1,
            'bid_low': bid-.1, 'bid_close': bid,
            'ask_open': bid+spread, 'ask_high': bid+spread+.1,
            'ask_low': bid+spread-.1, 'ask_close': bid+spread, 'volume': 1}


class RBSTests(unittest.TestCase):
    def test_resistance_known_after_two_right_closes_not_wicks(self):
        rows = [bar(i*300, c=c) for i, c in enumerate((100, 101, 103, 102))]
        self.assertIsNone(resistance(rows))
        self.assertEqual(resistance(rows+[bar(1200, c=101)])['close'], 103)
        rows[0]['high'] = 110
        self.assertEqual(resistance(rows+[bar(1200, c=101)])['close'], 103)
        self.assertIsNone(resistance(rows+[bar(1200, c=103)]))

    def test_target_uses_confirmed_m15_wick_above_close(self):
        rows = [bar(i*900, h=101+i%2) for i in range(10)]
        rows[2]['high'] = 110
        rows[7]['high'] = 107
        self.assertEqual(target(rows, 103), 107)
        self.assertIsNone(target(rows, 110))

    def test_break_requires_strict_close_and_buy_context(self):
        model = RBSResearch('spec', 'data')
        level = {'id':'a','state':'level_known','known_at':300,
                 'low_band':99.8,'high_band':100.2,'level':100,'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=100.2))
        model.bars[5].append(bar(600, c=100.2))
        model.on_m5(bar(600, h=101, c=100.2))
        self.assertEqual(level['state'], 'level_known')
        model.bars[5].append(bar(900, c=100.3))
        model.on_m5(bar(900, h=101, c=100.3))
        self.assertEqual(level['state'], 'context_rejected')

    def test_invalidation_precedes_touch_and_failed_first_retest_is_final(self):
        model = RBSResearch('spec', 'data')
        level = {'id':'a','state':'broken','break_time':300,
                 'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['a'] = level
        model.bars[60].extend(bar(i*3600) for i in range(240))
        with patch('app.snr_rbs.trend', return_value='buy'):
            model.on_m5(bar(600, o=100.3, h=100.5, l=99.5, c=99.7))
        self.assertEqual(level['state'], 'invalidated')
        other = {'id':'b','state':'broken','break_time':300,
                 'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['b'] = other
        with patch('app.snr_rbs.trend', return_value='buy'):
            model.on_m5(bar(900, o=100.4, h=100.5, l=100, c=100.3))
            model.on_m5(bar(1200, o=100.2, h=100.5, l=100, c=100.4))
        self.assertEqual(other['state'], 'retest_unconfirmed')
        self.assertEqual(len([e for e in model.events if e['level_id']=='b']), 1)

    def test_confirmed_retest_freezes_stop_and_target(self):
        model = RBSResearch('spec', 'data')
        level = {'id':'a','state':'broken','break_time':300,
                 'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['a'] = level
        model.bars[60].extend(bar(i*3600) for i in range(240))
        model.bars[15].extend(bar(i*900) for i in range(120))
        with patch('app.snr_rbs.trend', return_value='buy'), patch('app.snr_rbs.target', return_value=102):
            model.on_m5(bar(600, o=100, h=100.6, l=99.7, c=100.4))
        self.assertEqual(level['state'], 'pending_entry')
        self.assertAlmostEqual(level['stop'], 99.5)
        self.assertEqual(level['target'], 102)
        self.assertEqual([e['state'] for e in model.events], ['confirmed', 'pending_entry'])

    def test_live_observation_emits_signal_without_paper_trade(self):
        model = RBSResearch('spec', 'data', signals_only=True)
        level = {'id':'a','state':'broken','break_time':300,
                 'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['a'] = level
        model.bars[60].extend(bar(i*3600) for i in range(240))
        model.bars[15].extend(bar(i*900) for i in range(120))
        with patch('app.snr_rbs.trend', return_value='buy'), patch('app.snr_rbs.target', return_value=102):
            model.on_m5(bar(600, o=100, h=100.6, l=99.7, c=100.4))
        self.assertEqual(level['state'], 'signal_ready')
        self.assertEqual([e['state'] for e in model.events], ['confirmed', 'signal_ready'])
        self.assertEqual(model.trades, [])

    def test_twelfth_retest_allowed_thirteenth_expires(self):
        model = RBSResearch('spec', 'data')
        model.bars[60].extend(bar(i*3600) for i in range(240))
        model.bars[15].extend(bar(i*900) for i in range(120))
        allowed = {'id':'a','state':'broken','break_time':300,
                   'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['a'] = allowed
        with patch('app.snr_rbs.trend', return_value='buy'), patch('app.snr_rbs.target', return_value=102):
            model.on_m5(bar(3600, o=100, h=100.6, l=99.7, c=100.4))
        self.assertEqual(allowed['state'], 'pending_entry')
        late = {'id':'c','state':'broken','break_time':300,
                'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['c'] = late
        with patch('app.snr_rbs.trend', return_value='buy'):
            model.on_m5(bar(3900, o=100, h=100.6, l=99.7, c=100.4))
        self.assertEqual(late['state'], 'expired')

    def test_no_known_target_rejects_before_entry(self):
        model = RBSResearch('spec', 'data')
        level = {'id':'a','state':'broken','break_time':300,
                 'low_band':99.8,'high_band':100.2,'atr':2}
        model.levels['a'] = level
        model.bars[60].extend(bar(i*3600) for i in range(240))
        with patch('app.snr_rbs.trend', return_value='buy'):
            model.on_m5(bar(600, o=100, h=100.6, l=99.7, c=100.4))
        self.assertEqual(level['state'], 'entry_rejected')
        self.assertEqual(model.events[-1]['reason'], 'no_known_target')

    def test_entry_uses_ask_and_rejects_bad_geometry(self):
        model = RBSResearch('spec', 'data')
        level = {'id':'a','state':'pending_entry','confirmation_time':60,
                 'stop':99,'target':102}
        model.levels['a'] = level
        model.enter(level, minute(60, bid=100, spread=.1))
        self.assertEqual(level['state'], 'observing')
        self.assertAlmostEqual(model.trades[0]['entry'], 100.15)
        self.assertAlmostEqual(model.trades[0]['quantity_oz'], 25/1.28)
        bad = {'id':'b','state':'pending_entry','confirmation_time':60,
               'stop':99,'target':100.1}
        model.levels['b'] = bad
        model.enter(bad, minute(60, bid=100, spread=.1))
        self.assertEqual(bad['state'], 'entry_rejected')
        self.assertEqual(model.events[-1]['reason'], 'invalid_entry_geometry')

    def test_entry_rejects_low_rr_and_large_spread(self):
        model = RBSResearch('spec', 'data')
        poor = {'id':'a','state':'pending_entry','confirmation_time':60,
                'stop':99,'target':100.8}
        wide = {'id':'b','state':'pending_entry','confirmation_time':60,
                'stop':100.9,'target':110}
        model.levels.update(a=poor,b=wide)
        model.enter(poor, minute(60, bid=100, spread=.1))
        self.assertEqual(model.events[-1]['reason'], 'rr_below_one')
        model.enter(wide, minute(60, bid=100, spread=1.1))
        self.assertEqual(model.events[-1]['reason'], 'spread_limit')

    def test_missing_exact_entry_minute_cannot_shift(self):
        model = RBSResearch('spec', 'data')
        level = {'id':'a','state':'pending_entry','confirmation_time':60}
        model.levels['a'] = level
        model.last = 0
        model.step(minute(120))
        self.assertEqual(level['state'], 'incomplete')
        self.assertFalse(model.trades)

    def test_stop_wins_ambiguous_bid_bar_and_gap_worsens_exit(self):
        trade = {'stop':99,'target':102,'entry_time':0}
        row = {'time':0,'bid_open':100,'bid_low':98.9,
               'bid_high':102.1,'bid_close':100}
        price, reason = long_exit(trade, row)
        self.assertEqual(reason, 'stop_ambiguous')
        self.assertAlmostEqual(price, 98.95)
        row['bid_open'] = 98.5
        self.assertAlmostEqual(long_exit(trade, row)[0], 98.45)


if __name__ == '__main__':
    unittest.main()
