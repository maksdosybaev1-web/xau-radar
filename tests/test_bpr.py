import unittest
from app.bpr_observation import BPRObserver, WINDOW, run


def parent(direction, low, high, when):
    return dict(direction=direction, low=low, high=high, created=when, origin=when-2700, touched=False)


def bar(i, o=100, h=101, l=99, c=100):
    return dict(time=i*900, end=(i+1)*900, open=o, high=h, low=l, close=c, volume=1)


def pair():
    model = BPRObserver('spec', 'csv', 'engine')
    model.register(parent('buy', 100, 104, 900))
    model.register(parent('sell', 102, 106, 1800))
    return model


class BPRTests(unittest.TestCase):
    def test_intersection_provenance_and_only_later_touch(self):
        model = pair()
        zone = model.zones[0]
        self.assertEqual((zone['low'], zone['high'], zone['known_at']), (102, 104, 1800))
        self.assertEqual([p['created'] for p in zone['parents']], [900, 1800])
        self.assertEqual(zone['state'], 'awaiting_touch')
        self.assertEqual([e['type'] for e in model.events], ['bpr_known'])
        model.on_m15(bar(2, 101, 102, 100, 101))
        self.assertEqual(zone['state'], 'first_touch')
        self.assertEqual(model.events[-1]['time'], 2700)

    def test_same_direction_zero_width_and_too_old_do_not_form_zone(self):
        for direction, low, high, when in [('buy',102,106,1800), ('sell',104,106,1800),
                                          ('sell',102,106,900+WINDOW+900), ('sell',102,106,900)]:
            with self.subTest(direction=direction, low=low, when=when):
                model = BPRObserver('spec', 'csv', 'engine')
                model.register(parent('buy',100,104,900))
                model.register(parent(direction,low,high,when))
                self.assertEqual(model.zones, [])

    def test_expiry_boundary_and_priority_over_touch(self):
        for extra, expected in [(0, 'first_touch'), (900, 'expired')]:
            model = pair()
            end = 1800+WINDOW+extra
            model.on_m15(dict(bar(0,102,104,101,103),time=end-900,end=end))
            self.assertEqual(model.zones[0]['state'], expected)

    def test_gap_and_partial_candle_do_not_produce_touch(self):
        model = pair()
        row = dict(time=1800,bid_open=103,bid_high=104,bid_low=102,bid_close=103,volume=1)
        model.step(row)
        self.assertEqual(model.zones[0]['state'], 'awaiting_touch')
        model.step(dict(row,time=1920))
        self.assertEqual(model.zones[0]['state'], 'unknown_after_gap')
        self.assertFalse(model.parents)
        self.assertFalse(model.active)
        with self.assertRaises(ValueError):
            model.step(dict(row,time=1920))

    def test_distinct_pairs_keep_distinct_ids_for_identical_overlap(self):
        model = BPRObserver('spec','csv','engine')
        model.register(parent('buy',100,104,900))
        model.register(parent('buy',100,104,1800))
        model.register(parent('sell',102,106,2700))
        self.assertEqual(len(model.zones),2)
        self.assertNotEqual(model.zones[0]['id'],model.zones[1]['id'])

    def test_actual_fvg_detector_and_prefix_are_causal(self):
        bars=[bar(i) for i in range(14)]+[
            bar(14,100,107,100,106),bar(15,106,108,104,106),
            bar(16,106,106,97,98),bar(17,98,100,96,98),bar(18,99,102,98,100)]
        rows=[]
        for b in bars:
            for minute in range(15):
                rows.append(dict(time=b['time']+minute*60,bid_open=b['open'],
                                 bid_high=b['high'],bid_low=b['low'],bid_close=b['close'],volume=1))
        incomplete=run(rows[:18*15-1],'spec','csv','engine')
        prefix=run(rows[:18*15],'spec','csv','engine')
        full=run(rows,'spec','csv','engine')
        self.assertEqual(incomplete['summary']['bpr_count'],0)
        self.assertEqual(prefix['summary']['bpr_count'],1)
        self.assertEqual(prefix['zones'][0]['state'],'awaiting_touch')
        self.assertEqual(prefix['events'],[e for e in full['events'] if e['time']<=18*900])
        self.assertEqual(full['zones'][0]['state'],'first_touch')
