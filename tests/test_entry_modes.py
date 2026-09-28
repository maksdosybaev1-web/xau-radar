import unittest

from app.entry_modes import classify_first_return, run


def minute(t, opening=100, close=100.2, low=99.8, high=100.3, spread=.1):
    return {'time': t, 'bid_open': opening, 'bid_high': high,
            'bid_low': low, 'bid_close': close,
            'ask_open': opening+spread, 'ask_high': high+spread,
            'ask_low': low+spread, 'ask_close': close+spread, 'volume': 1}


class EntryModeTests(unittest.TestCase):
    def test_buy_limit_and_next_open_are_separate_quotes(self):
        chain = {'direction': 'buy', 'inner': [99.9, 100.1]}
        first, second = classify_first_return(
            chain, minute(300, opening=100.2, close=100.3, low=99.9, high=100.4),
            minute(360, opening=100.4, close=100.5, low=100.3, high=100.6))
        self.assertEqual(first['state'], 'resting_limit_crossing')
        self.assertEqual(first['limit_price'], 100.1)
        self.assertFalse(first['fill_known'])
        self.assertEqual(second['state'], 'next_open_candidate')
        self.assertAlmostEqual(second['entry_quote'], 100.5)
        self.assertEqual(second['entry_quote_time'], 360)

    def test_open_already_crossed_is_not_resting_limit(self):
        chain = {'direction': 'buy', 'inner': [99.9, 100.1]}
        first, _ = classify_first_return(chain, minute(300, opening=100, low=99.8), None)
        self.assertEqual(first['state'], 'limit_crossed_at_open')

    def test_sell_close_must_confirm_first_touch(self):
        chain = {'direction': 'sell', 'inner': [99.9, 100.1]}
        first, second = classify_first_return(
            chain, minute(300, opening=100.2, close=99.8, low=99.7, high=100.3),
            minute(360, opening=99.7, close=99.6, low=99.5, high=99.9))
        self.assertEqual(first['state'], 'limit_crossed_at_open')
        self.assertEqual(first['price_side'], 'bid')
        self.assertEqual(second['state'], 'next_open_candidate')
        self.assertEqual(second['entry_quote'], 99.7)
        _, failed = classify_first_return(
            chain, minute(300, opening=100.2, close=100, low=99.7, high=100.3), None)
        self.assertEqual(failed['state'], 'first_return_not_confirmed')

    def test_exact_next_minute_required_after_confirmation(self):
        chain = {'direction': 'buy', 'inner': [99.9, 100.1]}
        _, second = classify_first_return(
            chain, minute(300, opening=100, close=100.3, low=99.8),
            minute(420, opening=100.4))
        self.assertEqual(second['state'], 'missing_next_m1')
        self.assertIsNone(second['entry_quote'])

    def test_join_emits_exactly_two_modes_and_validates_provenance(self):
        chain = {'id': 'chain', 'direction': 'buy', 'inner': [99.9, 100.1],
                 'known_at': 300, 'state': 'touch_observed'}
        touch = {'id': 'touch', 'type': 'touch_observed', 'chain_id': 'chain', 'm1_time': 300}
        nested = {'source_hash': 'csv', 'spec_hash': 'nested',
                  'chains': [chain], 'events': [touch]}
        result = run([minute(300, opening=100, close=100.3, low=99.8),
                      minute(360, opening=100.4)], nested, 'spec', 'csv')
        self.assertEqual(result['summary']['first_returns'], 1)
        self.assertEqual({x['mode'] for x in result['observations']},
                         {'touch_limit', 'after_close'})
        self.assertEqual(len({x['id'] for x in result['observations']}), 2)
        with self.assertRaises(ValueError):
            run([], nested, 'spec', 'other-csv')


if __name__ == '__main__':
    unittest.main()
