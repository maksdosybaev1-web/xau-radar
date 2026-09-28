import unittest
from unittest.mock import patch

from app.snr_sbr import SBRResearch, short_exit, support, target


def bar(t, o=100, h=101, l=99, c=100):
    return {'time': t, 'end': t+300, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': 1}


def minute(t, bid=99.5, spread=.1):
    return {'time': t, 'bid_open': bid, 'bid_high': bid+.1, 'bid_low': bid-.1,
            'bid_close': bid, 'ask_open': bid+spread, 'ask_high': bid+spread+.1,
            'ask_low': bid+spread-.1, 'ask_close': bid+spread, 'volume': 1}


class SBRTests(unittest.TestCase):
    def test_pivot_needs_two_right_bars_and_strict_close(self):
        rows = [bar(i*300, c=c) for i, c in enumerate((103, 102, 100, 101))]
        self.assertIsNone(support(rows))
        self.assertEqual(support(rows+[bar(1200, l=95, c=102)])['close'], 100)
        rows[0]['low'] = 90
        self.assertEqual(support(rows+[bar(1200, c=102)])['close'], 100)
        rows[4-1]['close'] = 100
        self.assertIsNone(support(rows+[bar(1200, c=102)]))

    def test_target_only_known_strict_lower_wick(self):
        rows = [bar(i*900, l=90+i%3, c=100) for i in range(6)]
        rows[2]['low'] = 80
        self.assertEqual(target(rows, 99), 80)
        self.assertIsNone(target(rows, 80))

    def test_break_uses_strict_close_and_retest_is_next_bar(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'level_known', 'known_at':300, 'low_band':99.8,
                 'high_band':100.2, 'level':100, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(0, c=100))
        model.bars[5].append(bar(300, c=100))
        model.on_m5(bar(300, h=100.5, l=99.5, c=99.8))
        self.assertEqual(level['state'], 'level_known')
        model.bars[5].append(bar(600, c=99.7))
        # Context is deliberately neutral; a strict crossing is therefore rejected.
        model.on_m5(bar(600, h=100.1, l=99.4, c=99.7))
        self.assertEqual(level['state'], 'context_rejected')

    def test_first_retest_and_invalidation_priority(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'broken', 'break_time':300,
                 'low_band':99.8, 'high_band':100.2, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=99.7))
        model.bars[60].extend(bar(i*3600) for i in range(240))
        with patch('app.snr_sbr.trend', return_value='sell'):
            model.on_m5(bar(600, o=99.7, h=100.5, l=99.5, c=100.3))
        self.assertEqual(level['state'], 'invalidated')

    def test_confirmed_first_retest_freezes_stop_and_target(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'broken', 'break_time':300,
                 'low_band':99.8, 'high_band':100.2, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=99.7))
        model.bars[60].extend(bar(i*3600) for i in range(240))
        model.bars[15].extend(bar(i*900) for i in range(120))
        with patch('app.snr_sbr.trend', return_value='sell'), patch('app.snr_sbr.target', return_value=98.5):
            model.on_m5(bar(600, o=99.7, h=100.1, l=99.4, c=99.6))
        self.assertEqual(level['state'], 'pending_entry')
        self.assertAlmostEqual(level['stop'], 100.4)
        self.assertEqual(level['target'], 98.5)
        self.assertEqual([e['state'] for e in model.events], ['confirmed', 'pending_entry'])

    def test_live_observer_records_signal_without_backdated_trade(self):
        model = SBRResearch('spec', 'data', source='MT5 · XAUUSD', signals_only=True)
        level = {'id':'a', 'state':'broken', 'break_time':300,
                 'low_band':99.8, 'high_band':100.2, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=99.7))
        model.bars[60].extend(bar(i*3600) for i in range(240))
        model.bars[15].extend(bar(i*900) for i in range(120))
        with patch('app.snr_sbr.trend', return_value='sell'), patch('app.snr_sbr.target', return_value=98.5):
            model.on_m5(bar(600, o=99.7, h=100.1, l=99.4, c=99.6))
        self.assertEqual(level['state'], 'signal_ready')
        self.assertFalse(model.trades)
        self.assertEqual([e['state'] for e in model.events], ['confirmed', 'signal_ready'])

    def test_first_retest_failure_cannot_retry(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'broken', 'break_time':300,
                 'low_band':99.8, 'high_band':100.2, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=99.7))
        model.bars[60].extend(bar(i*3600) for i in range(240))
        with patch('app.snr_sbr.trend', return_value='sell'):
            model.on_m5(bar(600, o=99.6, h=100.1, l=99.5, c=99.7))
            model.on_m5(bar(900, o=99.7, h=100.1, l=99.4, c=99.6))
        self.assertEqual(level['state'], 'retest_unconfirmed')
        self.assertEqual(len(model.events), 1)

    def test_thirteenth_retest_expires(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'broken', 'break_time':300,
                 'low_band':99.8, 'high_band':100.2, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=99.7))
        model.bars[60].extend(bar(i*3600) for i in range(240))
        with patch('app.snr_sbr.trend', return_value='sell'):
            model.on_m5(bar(3600, o=99.7, h=99.7, l=99.4, c=99.6))
            self.assertEqual(level['state'], 'broken')
            model.on_m5(bar(3900, o=99.7, h=100.1, l=99.4, c=99.6))
        self.assertEqual(level['state'], 'expired')

    def test_missing_target_rejects_before_entry(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'broken', 'break_time':300,
                 'low_band':99.8, 'high_band':100.2, 'atr':2}
        model.levels['a'] = level
        model.bars[5].append(bar(300, c=99.7))
        model.bars[60].extend(bar(i*3600) for i in range(240))
        with patch('app.snr_sbr.trend', return_value='sell'):
            model.on_m5(bar(600, o=99.7, h=100.1, l=99.4, c=99.6))
        self.assertEqual(level['state'], 'entry_rejected')
        self.assertEqual(model.events[-1]['reason'], 'no_known_target')

    def test_entry_past_target_is_rejected(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'pending_entry', 'confirmation_time':60,
                 'stop':100.4, 'target':98.5}
        model.levels['a'] = level
        model.enter(level, minute(60, bid=98.45))
        self.assertEqual(level['state'], 'entry_rejected')
        self.assertEqual(model.events[-1]['reason'], 'invalid_entry_geometry')
        self.assertFalse(model.trades)

    def test_entry_rr_spread_and_quantity(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'pending_entry', 'confirmation_time':60,
                 'stop':100.4, 'target':98.5}
        model.levels['a'] = level
        model.enter(level, minute(60, bid=99.55, spread=.1))
        self.assertEqual(level['state'], 'observing')
        self.assertAlmostEqual(model.trades[0]['entry'], 99.5)
        self.assertAlmostEqual(model.trades[0]['quantity_oz'], 25/1.03)
        other = {'id':'b', 'state':'pending_entry', 'confirmation_time':60,
                 'stop':100.4, 'target':98.5}
        model.levels['b'] = other
        model.enter(other, minute(60, bid=99.05))
        self.assertEqual(model.events[-1]['reason'], 'rr_below_one')

    def test_missing_entry_not_shifted(self):
        model = SBRResearch('spec', 'data')
        level = {'id':'a', 'state':'pending_entry', 'confirmation_time':60}
        model.levels['a'] = level
        model.last = 0
        model.step(minute(120))
        self.assertEqual(level['state'], 'incomplete')
        self.assertEqual(model.events[-1]['reason'], 'missing_entry_bar')
        self.assertFalse(model.trades)

    def test_stop_wins_same_minute(self):
        trade = {'stop':100.4, 'target':98.5, 'entry_time':0}
        row = {'time':0, 'ask_open':99.5, 'ask_high':100.5,
               'ask_low':98.4, 'ask_close':99.5}
        price, reason = short_exit(trade, row)
        self.assertEqual(reason, 'stop_ambiguous')
        self.assertAlmostEqual(price, 100.45)
        row['ask_open'] = 100.7
        self.assertAlmostEqual(short_exit(trade, row)[0], 100.75)


if __name__ == '__main__':
    unittest.main()
