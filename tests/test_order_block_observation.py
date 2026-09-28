import unittest

from app.order_block_observation import OrderBlockObserver


def h1(hour, opening=100, high=101, low=99, close=100):
    return {'time': hour * 3600, 'end': (hour + 1) * 3600,
            'open': opening, 'high': high, 'low': low, 'close': close, 'volume': 1}


def prepared():
    observer = OrderBlockObserver('spec', 'csv')
    for hour in range(14):
        observer.on_h1(h1(hour))
    observer.on_h1(h1(14, opening=100, high=101, low=98, close=99))
    return observer


class OrderBlockObservationTests(unittest.TestCase):
    def test_zone_is_known_only_after_closed_impulse_and_later_touch(self):
        observer = prepared()
        self.assertEqual(observer.zones, [])
        observer.on_h1(h1(15, opening=100, high=105, low=99, close=104))
        zone = observer.zones[0]
        self.assertEqual(zone['known_at'], 16 * 3600)
        self.assertEqual((zone['low'], zone['high']), (98, 101))
        self.assertEqual([event['type'] for event in observer.events], ['ob_known'])
        observer.on_h1(h1(16, opening=103, high=104, low=100, close=102))
        self.assertEqual(zone['state'], 'first_touch')
        self.assertEqual(zone['first_touch_at'], 17 * 3600)
        self.assertEqual([event['type'] for event in observer.events],
                         ['ob_known', 'ob_first_touch'])

    def test_invalidation_wins_when_same_h1_also_touches(self):
        observer = prepared()
        observer.on_h1(h1(15, opening=100, high=105, low=99, close=104))
        observer.on_h1(h1(16, opening=102, high=103, low=97, close=97.5))
        self.assertEqual(observer.zones[0]['state'], 'invalidated')
        self.assertEqual([event['type'] for event in observer.events[:2]],
                         ['ob_known', 'ob_invalidated'])
        self.assertEqual(observer.events[1]['zone_id'], observer.zones[0]['id'])

    def test_weak_or_unclosed_impulse_does_not_create_zone(self):
        observer = prepared()
        observer.on_h1(h1(15, opening=100, high=102, low=99, close=101.5))
        self.assertEqual(observer.zones, [])

        observer = prepared()
        observer.step({'time': 15 * 3600, 'bid_open': 100, 'bid_high': 105,
                       'bid_low': 99, 'bid_close': 104, 'volume': 1})
        self.assertEqual(observer.zones, [])

    def test_gap_makes_untested_zone_unknown_and_resets_history(self):
        observer = prepared()
        observer.on_h1(h1(15, opening=100, high=105, low=99, close=104))
        observer.last_m1 = 16 * 3600
        observer.step({'time': 16 * 3600 + 120, 'bid_open': 100,
                       'bid_high': 101, 'bid_low': 99, 'bid_close': 100,
                       'volume': 1})
        self.assertEqual(observer.zones[0]['state'], 'unknown_after_gap')
        self.assertEqual(len(observer.history), 0)
        self.assertEqual(observer.events[-1]['type'], 'ob_unknown')


if __name__ == '__main__':
    unittest.main()
