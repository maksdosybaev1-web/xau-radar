import unittest
from unittest.mock import patch

from app.snr_classic import ClassicObservation, pivots


def bar(t, close):
    return {'time': t, 'end': t+300, 'open': close, 'high': close+.2,
            'low': close-.2, 'close': close, 'volume': 1}


def minute(t, bid=100, spread=.1, high=None, low=None):
    high = bid+.05 if high is None else high
    low = bid-.05 if low is None else low
    return {'time': t, 'bid_open': bid, 'bid_high': high, 'bid_low': low,
            'bid_close': bid, 'ask_open': bid+spread, 'ask_high': high+spread,
            'ask_low': low+spread, 'ask_close': bid+spread, 'volume': 1}


def level(model='A', state='armed'):
    return {'id': 'x', 'model': model, 'direction': 'sell' if model == 'A' else 'buy',
            'state': state, 'known_at': 300, 'armed_at': 300,
            'low_band': 99.9, 'high_band': 100.1}


class ClassicTests(unittest.TestCase):
    def test_pivot_needs_two_closed_right_bars_and_strict_close(self):
        four = [bar(i*300, c) for i, c in enumerate((100, 101, 103, 102))]
        self.assertEqual(pivots(four), [])
        self.assertEqual(pivots(four+[bar(1200, 101)])[0][:2], ('A', 'sell'))
        four[0]['high'] = 110
        self.assertEqual(pivots(four+[bar(1200, 101)])[0][0], 'A')
        self.assertEqual(pivots(four+[bar(1200, 103)]), [])
        lows = [bar(i*300, c) for i, c in enumerate((103, 102, 100, 101, 102))]
        self.assertEqual(pivots(lows)[0][:2], ('V', 'buy'))

    def test_no_touch_before_arming_and_ask_side_for_buy(self):
        model = ClassicObservation('spec', 'csv')
        item = level('V', 'level_known')
        model.levels[item['id']] = item
        model.active[item['id']] = item
        model.step(minute(300, bid=99.85, spread=.3))
        self.assertEqual(item['state'], 'level_known')
        item['state'] = 'armed'
        item['armed_at'] = 360
        model.bars[60].extend(bar(i*3600, 100) for i in range(240))
        with patch('app.snr_classic.trend', return_value='buy'):
            model.step(minute(360, bid=99.85, spread=.4))
            self.assertEqual(item['state'], 'armed')  # bid touched, ask did not
            model.step(minute(420, bid=99.85, spread=.1))
        self.assertEqual(item['state'], 'touch_observed')
        self.assertEqual(model.events[-1]['price_side'], 'ask')
        self.assertTrue(model.events[-1]['intraminute_order_unknown'])

    def test_first_touch_with_wrong_h1_is_terminal(self):
        model = ClassicObservation('spec', 'csv')
        item = level()
        model.levels[item['id']] = item
        model.active[item['id']] = item
        model.bars[60].extend(bar(i*3600, 100) for i in range(240))
        with patch('app.snr_classic.trend', return_value='buy'):
            model.step(minute(300))
            model.step(minute(360))
        self.assertEqual(item['state'], 'context_rejected')
        self.assertEqual(len(model.events), 1)

    def test_gap_marks_level_incomplete_and_resets_m5(self):
        model = ClassicObservation('spec', 'csv')
        item = level()
        model.levels[item['id']] = item
        model.active[item['id']] = item
        model.bars[60].extend(bar(i*3600, 100) for i in range(240))
        model.step(minute(300, bid=90))
        model.step(minute(420, bid=90))
        self.assertEqual(item['state'], 'incomplete')
        self.assertEqual(len(model.bars[60]), 240)
        self.assertEqual(len(model.bars[5]), 0)
        self.assertEqual(model.events[-1]['reason'], 'm1_gap')

    def test_expiry_and_m5_close_invalidation(self):
        model = ClassicObservation('spec', 'csv')
        item = level()
        model.levels[item['id']] = item
        model.active[item['id']] = item
        model.on_m5(bar(600, 100.2))
        self.assertEqual(item['state'], 'invalidated')
        other = level('V')
        other['id'] = 'y'
        model.levels['y'] = other
        model.active['y'] = other
        model.on_m5(bar(300+25*300, 100))
        self.assertEqual(other['state'], 'expired')


if __name__ == '__main__':
    unittest.main()
