import copy
import unittest
from app.breaker_block_observation import BreakerBlockObserver, run


def bar(i, o=100, h=101, l=99, c=100):
    return dict(time=i*3600, end=(i+1)*3600, open=o, high=h, low=l, close=c, volume=1)


def series():
    return [bar(i) for i in range(14)]+[
        bar(14, 100, 101, 98, 99), bar(15, 100, 105, 99, 104),
        bar(16, 103, 104, 100, 102), bar(17, 102, 103, 93, 94)]


def prepared():
    model = BreakerBlockObserver('bb', 'csv', 'ob')
    for b in series():
        model.on_h1(b)
    return model


class BreakerBlockTests(unittest.TestCase):
    def test_parent_survives_touch_and_break_cannot_retest_itself(self):
        model = prepared()
        block = model.breakers[0]
        self.assertEqual(model.parent.zones[0]['state'], 'first_touch')
        self.assertEqual(block['parent_ob_id'], model.parent.zones[0]['id'])
        self.assertEqual((block['direction'], block['low'], block['high']), ('sell', 98, 101))
        self.assertEqual(block['known_at'], 18*3600)
        self.assertEqual(block['state'], 'awaiting_retest')
        self.assertEqual([e['type'] for e in model.events], ['bb_known'])
        model.on_h1(bar(18, 96, 99, 95, 97))
        self.assertEqual(block['state'], 'first_retest')
        self.assertEqual(model.events[-1]['time'], 19*3600)

    def test_buy_symmetry(self):
        model = BreakerBlockObserver('bb', 'csv', 'ob')
        for b in series():
            mirrored = dict(b, open=200-b['open'], close=200-b['close'],
                            high=200-b['low'], low=200-b['high'])
            model.on_h1(mirrored)
        self.assertEqual(model.breakers[0]['direction'], 'buy')
        self.assertEqual((model.breakers[0]['low'], model.breakers[0]['high']), (99, 102))

    def test_wick_and_equality_are_not_break_then_weak_first_break_is_final(self):
        model = BreakerBlockObserver('bb', 'csv', 'ob')
        for b in series()[:16]:
            model.on_h1(b)
        parent_id = model.parent.zones[0]['id']
        model.on_h1(bar(16, 102, 103, 96, 98))
        self.assertEqual(model.breakers, [])
        self.assertIn(parent_id, model.parents)
        model.on_h1(bar(17, 98, 99, 97, 97.5))
        self.assertEqual(model.events[-1]['type'], 'bb_break_rejected')
        self.assertNotIn(parent_id, model.parents)
        model.on_h1(bar(18, 97, 98, 80, 81))
        self.assertFalse(any(b['parent_ob_id'] == parent_id for b in model.breakers))

    def test_invalidation_has_priority_over_retest(self):
        model = prepared()
        model.on_h1(bar(18, 96, 104, 95, 103))
        self.assertEqual(model.breakers[0]['state'], 'invalidated')
        self.assertEqual(model.events[-1]['type'], 'bb_invalidated')

    def test_gap_cancels_tracking_and_unclosed_h1_does_not_create_retest(self):
        model = prepared()
        row = dict(time=18*3600, bid_open=96, bid_high=99, bid_low=95, bid_close=97, volume=1)
        model.step(row)
        self.assertEqual(model.breakers[0]['state'], 'awaiting_retest')
        model.step(dict(row, time=row['time']+120))
        self.assertEqual(model.breakers[0]['state'], 'unknown_after_gap')
        self.assertFalse(model.active)
        self.assertFalse(model.parents)
        with self.assertRaises(ValueError):
            model.step(dict(row, time=row['time']+120))

    def test_future_bars_do_not_change_prior_events(self):
        rows=[]
        for b in series()+[bar(18, 96, 99, 95, 97)]:
            for minute in range(60):
                rows.append(dict(time=b['time']+minute*60, bid_open=b['open'],
                                 bid_high=b['high'], bid_low=b['low'], bid_close=b['close'], volume=1))
        prefix = run(rows[:-60], 'bb', 'csv', 'ob')
        original = copy.deepcopy(prefix['events'])
        full = run(rows, 'bb', 'csv', 'ob')
        self.assertEqual(original, [e for e in full['events'] if e['time'] <= 18*3600])
        self.assertEqual(prefix['summary']['breakers'], 1)
        self.assertEqual(full['summary']['states'], {'first_retest': 1})
