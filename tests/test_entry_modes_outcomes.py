import unittest

from app.entry_modes_outcomes import atr_at_closes, evaluate, exit_on_row, run


def minute(t, opening=101.2, close=101.2, low=100.8, high=101.4, spread=.1):
    return {'time': t, 'bid_open': opening, 'bid_high': high,
            'bid_low': low, 'bid_close': close,
            'ask_open': opening+spread, 'ask_high': high+spread,
            'ask_low': low+spread, 'ask_close': close+spread, 'volume': 1}


def chain():
    return {'id': 'chain', 'known_at': 300, 'direction': 'buy',
            'inner': [99, 101], 'state': 'touch_observed'}


def observation(mode='touch_limit', state='resting_limit_crossing'):
    item = {'id': 'candidate', 'chain_id': 'chain', 'mode': mode,
            'state': state, 'first_return_m1_time': 300, 'spec_hash': 'entry'}
    if mode == 'touch_limit':
        item['limit_price'] = 101
    else:
        item.update(entry_quote_time=360, entry_quote=101.3)
    return item


class EntryOutcomeTests(unittest.TestCase):
    def test_atr_requires_closed_contiguous_history(self):
        rows = [minute(i*60) for i in range(14)]
        self.assertEqual(atr_at_closes(rows), {})
        rows.append(minute(14*60))
        self.assertIn(15*60, atr_at_closes(rows))
        rows.append(minute(16*60))
        self.assertNotIn(17*60, atr_at_closes(rows))

    def test_limit_entry_bar_with_possible_exit_is_unknown(self):
        rows = [minute(300, opening=101.2, close=102, low=100.8, high=106)]
        result = evaluate(observation(), chain(), rows, {300: 0}, {300: .5}, 'spec', 'csv')
        self.assertEqual(result['state'], 'entry_bar_path_unknown')
        self.assertTrue(result['entry_bar_target_touched'])
        self.assertNotIn('r', result)

    def test_close_mode_uses_next_open_and_stop_wins_tie(self):
        rows = [minute(300), minute(360, opening=101.3, close=101, low=98, high=107)]
        result = evaluate(observation('after_close', 'next_open_candidate'), chain(),
                          rows, {300: 0, 360: 1}, {300: .5}, 'spec', 'csv')
        self.assertEqual(result['state'], 'closed')
        self.assertEqual(result['exit_reason'], 'stop_ambiguous')
        self.assertLess(result['r'], 0)
        self.assertAlmostEqual(result['normalized_risk_usd'], 25)

    def test_gap_after_limit_cannot_be_scored(self):
        rows = [minute(300), minute(420, opening=101.5, close=101.5,
                                    low=101.4, high=101.6)]
        result = evaluate(observation(), chain(), rows, {300: 0, 420: 1},
                          {300: .5}, 'spec', 'csv')
        self.assertEqual(result['state'], 'incomplete')
        self.assertEqual(result['reason'], 'm1_gap_during_experiment')

    def test_spread_guard_prevents_narrow_stop_and_provenance_checked(self):
        small = {'id': 'chain', 'known_at': 300, 'direction': 'buy',
                 'inner': [100.9, 101], 'state': 'touch_observed'}
        rows = [minute(300, spread=.5)]
        result = evaluate(observation(), small, rows, {300: 0}, {300: .1}, 'spec', 'csv')
        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(result['reason'], 'spread_guard')
        entry = {'source_hash': 'csv', 'nested_spec_hash': 'nested',
                 'spec_hash': 'entry', 'observations': []}
        nested = {'source_hash': 'csv', 'spec_hash': 'nested', 'chains': []}
        with self.assertRaises(ValueError):
            run(rows, entry, nested, 'spec', 'changed-csv')


if __name__ == '__main__':
    unittest.main()
