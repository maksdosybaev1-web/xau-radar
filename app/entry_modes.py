"""Compare touch-limit and closed-M1 candidate rules on the same first return."""
import hashlib
from collections import Counter


VERSION = 'nested-first-return-entry-modes-1'


def classify_first_return(chain, row, next_row):
    buy = chain['direction'] == 'buy'
    low, high = chain['inner']
    side = 'ask' if buy else 'bid'
    limit = high if buy else low
    if row[f'{side}_low'] > high or row[f'{side}_high'] < low:
        raise ValueError('Событие первого касания не совпадает с M1 OHLC')
    resting = row[f'{side}_open'] > limit if buy else row[f'{side}_open'] < limit
    limit_mode = {'state': 'resting_limit_crossing' if resting else 'limit_crossed_at_open',
                  'limit_price': limit, 'price_side': side,
                  'first_return_spread': row['ask_open']-row['bid_open'],
                  'quote_time': row['time'],
                  'fill_known': False}
    confirmed = (row['bid_close'] > high and row['bid_close'] > row['bid_open'] if buy
                 else row['bid_close'] < low and row['bid_close'] < row['bid_open'])
    if not confirmed:
        close_mode = {'state': 'first_return_not_confirmed',
                      'confirmation_time': row['time']+60, 'entry_quote': None}
    elif next_row is None or next_row['time'] != row['time']+60:
        close_mode = {'state': 'missing_next_m1',
                      'confirmation_time': row['time']+60, 'entry_quote': None}
    else:
        close_mode = {'state': 'next_open_candidate',
                      'confirmation_time': row['time']+60,
                      'entry_quote_time': next_row['time'],
                      'entry_quote_side': side,
                      'entry_quote': next_row[f'{side}_open'],
                      'entry_quote_spread': next_row['ask_open']-next_row['bid_open'],
                      'fill_known': False}
    return limit_mode, close_mode


def run(rows, nested_run, spec_hash, source_hash):
    if nested_run['source_hash'] != source_hash:
        raise ValueError('Исходный CSV не совпадает с журналом цепочек')
    by_time = {row['time']: row for row in rows}
    chains = {chain['id']: chain for chain in nested_run['chains']}
    observations = []
    for event in nested_run['events']:
        if event['type'] != 'touch_observed':
            continue
        chain = chains[event['chain_id']]
        t = event['m1_time']
        if chain['known_at'] > t or chain['state'] != 'touch_observed':
            raise ValueError('Касание произошло до знания цепочки или цепочка не завершена касанием')
        row = by_time.get(t)
        if row is None:
            raise ValueError('Отсутствует M1 первого касания')
        first, second = classify_first_return(chain, row, by_time.get(t+60))
        for mode, values in (('touch_limit', first), ('after_close', second)):
            observations.append({
                'id': hashlib.sha256(f'{VERSION}|{event["id"]}|{mode}'.encode()).hexdigest()[:24],
                'chain_id': chain['id'], 'first_return_event_id': event['id'],
                'first_return_m1_time': t, 'mode': mode, 'direction': chain['direction'],
                'inner_zone': chain['inner'], 'version': VERSION,
                'spec_hash': spec_hash, 'source_hash': source_hash,
                'nested_spec_hash': nested_run['spec_hash'],
                'execution_mode': 'historical_candidate_not_broker_fill',
                'intraminute_order_unknown': True, **values})
    counts = Counter((o['mode'], o['state']) for o in observations)
    return {'version': VERSION, 'spec_hash': spec_hash, 'source_hash': source_hash,
            'nested_spec_hash': nested_run['spec_hash'], 'observations': observations,
            'summary': {'first_returns': len(observations)//2,
                        'touch_limit': {state: n for (mode, state), n in counts.items()
                                        if mode == 'touch_limit'},
                        'after_close': {state: n for (mode, state), n in counts.items()
                                        if mode == 'after_close'},
                        'paper_trades': 0}}
