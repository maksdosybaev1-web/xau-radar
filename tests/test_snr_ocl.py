import unittest

from app.engine import Aggregator
from app.snr_ocl import classify, run


def bar(t, opening, close):
    return {'time': t, 'end': t+300, 'open': opening,
            'high': max(opening, close)+.1, 'low': min(opening, close)-.1,
            'close': close, 'volume': 1}


def minute(t, price):
    return {'time': t, 'bid_open': price, 'bid_high': price+.1,
            'bid_low': price-.1, 'bid_close': price,
            'ask_open': price+.1, 'ask_high': price+.2,
            'ask_low': price, 'ask_close': price+.1, 'volume': 1}


class OCLTests(unittest.TestCase):
    def test_sell_and_buy_seam_require_parent_overlap(self):
        first, second = bar(0, 101, 100), bar(300, 100, 99)
        parent = {'low_band': 99.9, 'high_band': 100.1}
        self.assertEqual(classify(parent, first, second, 'sell'),
                         ('candidate', {'low': 100, 'high': 100}))
        up1, up2 = bar(0, 99, 100), bar(300, 100, 101)
        self.assertEqual(classify(parent, up1, up2, 'buy')[0], 'candidate')
        self.assertEqual(classify(parent, first, second, 'buy')[0], 'body_direction_mismatch')

    def test_rejects_unrelated_or_unknown_pair(self):
        parent = {'low_band': 99.9, 'high_band': 100.1}
        first, second = bar(0, 103, 102), bar(300, 102, 101)
        self.assertEqual(classify(parent, first, second, 'sell')[0], 'seam_outside_parent_band')
        self.assertEqual(classify(parent, None, second, 'sell')[0], 'missing_adjacent_m5')
        self.assertEqual(classify(parent, first, bar(600, 102, 101), 'sell')[0],
                         'missing_adjacent_m5')

    def test_join_uses_only_break_and_previous_closed_m5(self):
        rows = [minute(i*60, p) for i, p in enumerate(
            (101, 100.8, 100.5, 100.2, 100, 100, 99.8, 99.6, 99.4, 99))]
        agg = Aggregator(5)
        bars = [x for row in rows if (x := agg.add(row)) is not None]
        parent = {'id': 'level', 'known_at': 300, 'low_band': 99.9, 'high_band': 100.1}
        event = {'id': 'break', 'state': 'broken', 'level_id': 'level', 'time': 600,
                 'bar': bars[1]}
        sbr = {'source_hash': 'csv', 'spec_hash': 'sbr-spec',
               'levels': [parent], 'events': [event]}
        rbs = {'source_hash': 'csv', 'spec_hash': 'rbs-spec',
               'levels': [], 'events': []}
        result = run(rows, {'sbr': sbr, 'rbs': rbs}, 'ocl-spec', 'csv')
        self.assertEqual(result['summary']['states'], {'candidate': 1})
        self.assertEqual(result['events'][0]['first_m5'], bars[0])
        self.assertEqual(result['events'][0]['second_m5'], bars[1])
        self.assertEqual(result['events'][0]['parent_break_event_id'], 'break')
        self.assertEqual(result['summary']['paper_trades'], 0)
        with self.assertRaises(ValueError):
            run(rows, {'sbr': sbr, 'rbs': rbs}, 'ocl-spec', 'changed-csv')


if __name__ == '__main__':
    unittest.main()
