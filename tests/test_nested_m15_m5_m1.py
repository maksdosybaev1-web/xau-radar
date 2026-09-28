import unittest

from app.nested_m15_m5_m1 import NestedObserver, inside, parent_pivots


def bar(t, high=101, low=99):
    return {'time': t, 'end': t+900, 'open': 100, 'high': high,
            'low': low, 'close': 100, 'volume': 1}


def minute(t, bid=100, spread=.1):
    return {'time': t, 'bid_open': bid, 'bid_high': bid+.1,
            'bid_low': bid-.1, 'bid_close': bid,
            'ask_open': bid+spread, 'ask_high': bid+spread+.1,
            'ask_low': bid+spread-.1, 'ask_close': bid+spread, 'volume': 1}


def add_chain_zones(model, *, inner_touched=None):
    model.zone(15, {'origin': 0, 'created': 2700, 'direction': 'buy',
                    'low': 90, 'high': 110}, 'pivot_candle_range')
    model.zone(5, {'origin': 900, 'created': 1800, 'direction': 'buy',
                   'low': 95, 'high': 105}, 'fvg')
    model.zone(1, {'origin': 1200, 'created': 2100, 'direction': 'buy',
                   'low': 98, 'high': 102}, 'fvg')
    model.zones[1][0]['touched_at'] = inner_touched


class NestedTests(unittest.TestCase):
    def test_event_id_does_not_depend_on_retained_event_list(self):
        model = NestedObserver('spec', 'source')
        first = model.event('probe', 60)['id']
        model.events.clear()
        second = model.event('probe', 60)['id']
        self.assertNotEqual(first, second)
        self.assertEqual(model.event_sequence, 2)

    def test_m15_pivot_needs_two_closed_right_bars(self):
        four = [bar(i*900, low=low) for i, low in enumerate((99, 98, 95, 97))]
        self.assertEqual(parent_pivots(four), [])
        self.assertEqual(parent_pivots(four+[bar(3600, low=96)])[0][0], 'buy')
        self.assertEqual(parent_pivots(four+[bar(3600, low=95)]), [])

    def test_full_containment_and_same_direction(self):
        outer = {'direction': 'buy', 'low': 90, 'high': 110}
        self.assertTrue(inside({'direction': 'buy', 'low': 90, 'high': 110}, outer))
        self.assertFalse(inside({'direction': 'sell', 'low': 95, 'high': 100}, outer))
        self.assertFalse(inside({'direction': 'buy', 'low': 89, 'high': 100}, outer))

    def test_link_is_known_after_last_zone_and_touch_is_later(self):
        model = NestedObserver('spec', 'csv')
        add_chain_zones(model)
        model.link(2700)
        chain = next(iter(model.chains.values()))
        self.assertEqual(chain['known_at'], 2700)
        self.assertEqual(chain['state'], 'chain_known')
        self.assertEqual(chain['outer'], [90, 110])
        self.assertEqual(chain['middle'], [95, 105])
        self.assertEqual(chain['inner'], [98, 102])
        model.step(minute(2700, bid=99))
        self.assertEqual(chain['state'], 'touch_observed')
        self.assertEqual(model.events[-1]['price_side'], 'ask')
        self.assertEqual(model.events[-1]['time'], 2760)

    def test_prior_touch_is_retained_without_new_signal(self):
        model = NestedObserver('spec', 'csv')
        add_chain_zones(model, inner_touched=2400)
        model.link(2700)
        chain = next(iter(model.chains.values()))
        self.assertEqual(chain['state'], 'previously_touched')
        model.step(minute(2700, bid=99))
        self.assertFalse(any(e['type'] == 'touch_observed' for e in model.events))

    def test_gap_makes_active_chain_incomplete(self):
        model = NestedObserver('spec', 'csv')
        add_chain_zones(model)
        model.link(2700)
        model.step(minute(2700, bid=120))
        model.step(minute(2820, bid=120))
        chain = next(iter(model.chains.values()))
        self.assertEqual(chain['state'], 'incomplete')
        self.assertEqual(model.events[-1]['type'], 'chain_incomplete')
        self.assertEqual(model.events[-1]['reason'], 'm1_gap')


if __name__ == '__main__':
    unittest.main()
