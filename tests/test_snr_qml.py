import unittest

from app.snr_qml import QMLObservation, beyond


def bar(t, close):
    return {'time': t, 'end': t+300, 'open': close, 'high': close+.2,
            'low': close-.2, 'close': close, 'volume': 1}


def minute(t, bid=100, spread=.1, high=None, low=None):
    high = bid+.05 if high is None else high
    low = bid-.05 if low is None else low
    return {'time': t, 'bid_open': bid, 'bid_high': high, 'bid_low': low,
            'bid_close': bid, 'ask_open': bid+spread, 'ask_high': high+spread,
            'ask_low': low+spread, 'ask_close': bid+spread, 'volume': 1}


def level(direction='buy', state='level_known'):
    return {'id': 'q', 'model': 'QML_BUY' if direction == 'buy' else 'QML_SELL',
            'direction': direction, 'state': state, 'known_at': 300,
            'updated_at': 300, 'low_band': 99.9, 'high_band': 100.1}


class QMLTests(unittest.TestCase):
    def model_with(self, item):
        model = QMLObservation('spec', 'csv')
        model.levels[item['id']] = item
        model.active[item['id']] = item
        return model

    def test_opposite_strict_breaks_for_both_directions(self):
        for direction, first, reverse in (('buy', 99.8, 100.2),
                                          ('sell', 100.2, 99.8)):
            item = level(direction)
            self.assertFalse(beyond(item, 99.9 if direction == 'buy' else 100.1, 'first'))
            model = self.model_with(item)
            model.on_m5(bar(300, first))
            self.assertEqual(item['state'], 'break_1')
            model.on_m5(bar(600, reverse))
            self.assertEqual(item['state'], 'break_2')
            self.assertEqual([e['state'] for e in model.events], ['break_1', 'break_2'])

    def test_touch_only_after_reverse_break_and_uses_executable_side(self):
        item = level('buy', 'break_1')
        model = self.model_with(item)
        model.step(minute(300, bid=99.85, spread=.1))
        self.assertEqual(item['state'], 'break_1')
        model.on_m5(bar(300, 100.2))
        self.assertEqual(item['state'], 'break_2')
        for t in (360, 420, 480, 540):
            model.step(minute(t, bid=90))
        model.step(minute(600, bid=99.85, spread=.4))
        self.assertEqual(item['state'], 'break_2')  # bid intersects; ask does not
        model.step(minute(660, bid=99.85, spread=.1))
        self.assertEqual(item['state'], 'return_1')
        self.assertEqual(model.events[-1]['contact_number'], 1)
        self.assertEqual(model.events[-1]['price_side'], 'ask')

    def test_second_contact_requires_closed_m5_departure(self):
        item = level('sell', 'return_1')
        item['updated_at'] = 600
        model = self.model_with(item)
        model.step(minute(600))
        self.assertEqual(item['state'], 'return_1')
        model.on_m5(bar(600, 99.8))
        self.assertEqual(item['state'], 'departed_again')
        for t in (660, 720, 780, 840):
            model.step(minute(t, bid=90))
        model.step(minute(900))
        self.assertEqual(item['state'], 'return_2')
        self.assertEqual(model.events[-1]['contact_number'], 2)
        model.step(minute(960))
        self.assertEqual(len([e for e in model.events if e['state'] == 'return_2']), 1)

    def test_gap_and_expiry_are_not_returns(self):
        item = level('buy', 'break_2')
        model = self.model_with(item)
        model.step(minute(300, bid=90))
        model.step(minute(420, bid=90))
        self.assertEqual(item['state'], 'incomplete')
        self.assertEqual(model.events[-1]['reason'], 'm1_gap')
        other = level('sell', 'break_1')
        another = self.model_with(other)
        another.on_m5(bar(300+25*300, 100))
        self.assertEqual(other['state'], 'expired')


if __name__ == '__main__':
    unittest.main()
