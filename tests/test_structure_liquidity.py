import unittest

from app.structure_liquidity import StructureObserver, confirmed_turn, direction


def bar(t, opening=100, high=101, low=99, close=100):
    return {'time': t, 'end': t+3600, 'open': opening,
            'high': high, 'low': low, 'close': close, 'volume': 1}


def swing(side, price, ident):
    return {'id': ident, 'side': side, 'price': price, 'state': 'price_not_revisited'}


class StructureLiquidityTests(unittest.TestCase):
    def test_pivot_is_known_only_after_two_right_h1_and_strict_wicks(self):
        four = [bar(i*3600, high=h) for i, h in enumerate((101, 102, 105, 103))]
        self.assertEqual(confirmed_turn(four), [])
        self.assertEqual(confirmed_turn(four+[bar(14400, high=102)])[0][0], 'upper')
        self.assertEqual(confirmed_turn(four+[bar(14400, high=105)]), [])

    def test_direction_requires_both_higher_or_both_lower_swings(self):
        self.assertEqual(direction([swing('upper', 101, 'h1'), swing('upper', 103, 'h2')],
                                   [swing('lower', 99, 'l1'), swing('lower', 100, 'l2')]), 'buy')
        self.assertEqual(direction([swing('upper', 103, 'h1'), swing('upper', 101, 'h2')],
                                   [swing('lower', 100, 'l1'), swing('lower', 99, 'l2')]), 'sell')
        self.assertEqual(direction([swing('upper', 101, 'h1'), swing('upper', 103, 'h2')],
                                   [swing('lower', 100, 'l1'), swing('lower', 99, 'l2')]), 'neutral')

    def test_bearish_choch_needs_later_close_below_choch_low_for_bos(self):
        model = StructureObserver('spec', 'csv')
        model.trend = 'buy'
        model.lows.append(swing('lower', 100, 'protected'))
        model.on_h1(bar(0, opening=101, high=102, low=98, close=99))
        self.assertEqual(model.events[-1]['type'], 'choch_observed')
        self.assertEqual(model.pending['threshold'], 98)
        model.on_h1(bar(3600, opening=99, high=100, low=97, close=98))
        self.assertEqual([x['type'] for x in model.events], ['choch_observed'])
        model.on_h1(bar(7200, opening=98, high=99, low=96, close=97))
        self.assertEqual(model.events[-1]['type'], 'bos_confirmed')
        self.assertEqual(model.trend, 'sell')

    def test_bullish_choch_is_mirror_image(self):
        model = StructureObserver('spec', 'csv')
        model.trend = 'sell'
        model.highs.append(swing('upper', 100, 'protected'))
        model.on_h1(bar(0, opening=99, high=102, low=98, close=101))
        self.assertEqual(model.events[-1]['type'], 'choch_observed')
        model.on_h1(bar(3600, opening=101, high=104, low=100, close=103))
        self.assertEqual(model.events[-1]['type'], 'bos_confirmed')
        self.assertEqual(model.trend, 'buy')

    def test_gap_marks_pending_and_unrevisited_extremum_unknown(self):
        model = StructureObserver('spec', 'csv')
        model.pending = {'choch_id': 'c', 'direction': 'sell', 'choch_time': 3600, 'threshold': 98}
        pool = swing('upper', 105, 'u')
        model.pools['u'] = pool
        model.active_pools['u'] = pool
        model.gap(7200)
        self.assertEqual([x['type'] for x in model.events],
                         ['sequence_incomplete', 'extremum_unknown'])
        self.assertEqual(pool['state'], 'unknown_after_gap')
        self.assertIsNone(model.pending)

    def test_revisit_is_price_only_and_happens_once(self):
        model = StructureObserver('spec', 'csv')
        pool = swing('upper', 105, 'u')
        model.pools['u'] = pool
        model.active_pools['u'] = pool
        model.on_h1(bar(0, high=105, low=99))
        model.on_h1(bar(3600, high=106, low=99))
        revisits = [x for x in model.events if x['type'] == 'extremum_revisited']
        self.assertEqual(len(revisits), 1)
        self.assertEqual(revisits[0]['observation'], 'wick_crossed_price_not_orders')


if __name__ == '__main__':
    unittest.main()
