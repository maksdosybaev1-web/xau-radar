"""Independent historical paper outcomes for two predeclared M1 entry modes."""
import datetime as dt
import hashlib
import statistics
from collections import Counter, deque

from .engine import atr


VERSION = 'nested-entry-outcomes-1'
FEE = .04
SLIP = .05
RISK_USD = 25.0
BUFFER_ATR = .1
REWARD_RISK = 2.0
MAX_SPREAD = 1.0
MAX_SPREAD_TO_STOP = .2
HOLD_SECONDS = 3600


def atr_at_closes(rows):
    history = deque(maxlen=15)
    values = {}
    previous = None
    for row in rows:
        if previous is not None and row['time']-previous != 60:
            history.clear()
        previous = row['time']
        history.append({'time': row['time'], 'end': row['time']+60,
                        'open': row['bid_open'], 'high': row['bid_high'],
                        'low': row['bid_low'], 'close': row['bid_close']})
        value = atr(history, 14)
        if value is not None:
            values[row['time']+60] = value
    return values


def boundaries_hit(direction, stop, target, row):
    buy = direction == 'buy'
    side = 'bid' if buy else 'ask'
    stop_hit = row[f'{side}_low'] <= stop if buy else row[f'{side}_high'] >= stop
    target_hit = row[f'{side}_high'] >= target if buy else row[f'{side}_low'] <= target
    return stop_hit, target_hit


def exit_on_row(direction, stop, target, row, entry_time):
    buy = direction == 'buy'
    side = 'bid' if buy else 'ask'
    stop_hit, target_hit = boundaries_hit(direction, stop, target, row)
    if stop_hit:
        price = (min(row[f'{side}_open'], stop)-SLIP if buy
                 else max(row[f'{side}_open'], stop)+SLIP)
        return price, 'stop_ambiguous' if target_hit else 'stop'
    if target_hit:
        return target, 'target'
    if row['time']+60-entry_time >= HOLD_SECONDS:
        price = row[f'{side}_close']-SLIP if buy else row[f'{side}_close']+SLIP
        return price, 'timeout'
    return None


def summarize(experiments):
    states = Counter(x['state'] for x in experiments)
    closed = [x for x in experiments if x['state'] == 'closed']
    return {'total': len(experiments), 'states': dict(states), 'closed': len(closed),
            'wins': sum(x['r'] > 0 for x in closed),
            'average_r': statistics.mean(x['r'] for x in closed) if closed else None,
            'stop_exits': sum(x['exit_reason'].startswith('stop') for x in closed),
            'target_exits': sum(x['exit_reason'] == 'target' for x in closed),
            'timeout_exits': sum(x['exit_reason'] == 'timeout' for x in closed)}


def evaluate(observation, chain, rows, by_time, atr_by_end, spec_hash, source_hash):
    mode = observation['mode']
    result = {'id': hashlib.sha256(f'{VERSION}|{observation["id"]}'.encode()).hexdigest()[:24],
              'candidate_id': observation['id'], 'chain_id': chain['id'],
              'mode': mode, 'direction': chain['direction'],
              'first_return_time': observation['first_return_m1_time'],
              'version': VERSION, 'spec_hash': spec_hash, 'source_hash': source_hash,
              'entry_spec_hash': observation['spec_hash'],
              'execution': 'independent_historical_paper_assumption'}
    required = ('resting_limit_crossing' if mode == 'touch_limit' else 'next_open_candidate')
    if observation['state'] != required:
        result.update(state='not_eligible', reason=observation['state'])
        return result
    value = atr_by_end.get(chain['known_at'])
    if value is None or value <= 0:
        result.update(state='rejected', reason='no_frozen_m1_atr')
        return result
    buy = chain['direction'] == 'buy'
    stop = (chain['inner'][0]-BUFFER_ATR*value if buy
            else chain['inner'][1]+BUFFER_ATR*value)
    entry_time = (observation['first_return_m1_time'] if mode == 'touch_limit'
                  else observation['entry_quote_time'])
    index = by_time.get(entry_time)
    if index is None:
        result.update(state='incomplete', reason='missing_entry_m1')
        return result
    row = rows[index]
    entry = (observation['limit_price'] if mode == 'touch_limit' else
             observation['entry_quote']+(SLIP if buy else -SLIP))
    spread = row['ask_open']-row['bid_open']
    distance = entry-stop if buy else stop-entry
    result.update(entry_time=entry_time, entry=entry, stop=stop,
                  frozen_m1_atr=value, spread_proxy_at_entry=spread,
                  spread_at_limit_fill_unknown=(mode == 'touch_limit'))
    if distance <= 0:
        result.update(state='rejected', reason='invalid_stop_geometry')
        return result
    if spread > MAX_SPREAD or spread/distance > MAX_SPREAD_TO_STOP:
        result.update(state='rejected', reason='spread_guard')
        return result
    target = entry+(REWARD_RISK*distance if buy else -REWARD_RISK*distance)
    quantity = RISK_USD/(distance+SLIP+2*FEE)
    result.update(target=target, quantity_oz=quantity, normalized_risk_usd=RISK_USD,
                  commission_per_oz_side=FEE, exit_slippage_per_oz=SLIP,
                  entry_slippage_per_oz=0 if mode == 'touch_limit' else SLIP)
    if mode == 'touch_limit':
        stop_hit, target_hit = boundaries_hit(chain['direction'], stop, target, row)
        if stop_hit or target_hit:
            result.update(state='entry_bar_path_unknown', reason='entry_and_exit_order_unknown',
                          entry_bar_stop_touched=stop_hit, entry_bar_target_touched=target_hit)
            return result
        start = index+1
        previous_time = entry_time
    else:
        start = index
        previous_time = entry_time-60
    for i in range(start, len(rows)):
        bar = rows[i]
        if bar['time']-previous_time != 60:
            result.update(state='incomplete', reason='m1_gap_during_experiment')
            return result
        outcome = exit_on_row(chain['direction'], stop, target, bar, entry_time)
        if outcome:
            exit_price, reason = outcome
            pnl = ((exit_price-entry) if buy else (entry-exit_price))*quantity-2*FEE*quantity
            result.update(state='closed', exit_time=bar['time']+60, exit=exit_price,
                          exit_reason=reason, pnl_independent_usd=pnl, r=pnl/RISK_USD)
            return result
        previous_time = bar['time']
    result.update(state='incomplete', reason='end_of_data')
    return result


def run(rows, entry_run, nested_run, spec_hash, source_hash):
    if entry_run['source_hash'] != source_hash or nested_run['source_hash'] != source_hash:
        raise ValueError('Журналы и исходный CSV не совпадают')
    if entry_run['nested_spec_hash'] != nested_run['spec_hash']:
        raise ValueError('Журнал режимов создан для другой версии цепочек')
    by_time = {row['time']: i for i, row in enumerate(rows)}
    atr_by_end = atr_at_closes(rows)
    chains = {x['id']: x for x in nested_run['chains']}
    experiments = [evaluate(o, chains[o['chain_id']], rows, by_time, atr_by_end,
                            spec_hash, source_hash) for o in entry_run['observations']]
    by_mode = {mode: [x for x in experiments if x['mode'] == mode]
               for mode in ('touch_limit', 'after_close')}
    paired = {}
    for item in experiments:
        paired.setdefault(item['chain_id'], {})[item['mode']] = item
    both = [pair for pair in paired.values()
            if all(mode in pair and pair[mode]['state'] == 'closed'
                   for mode in ('touch_limit', 'after_close'))]
    months = {}
    for mode, items in by_mode.items():
        month_groups = {}
        for item in items:
            if item['state'] == 'closed':
                month = dt.datetime.fromtimestamp(item['first_return_time'], dt.timezone.utc).strftime('%Y-%m')
                month_groups.setdefault(month, []).append(item)
        months[mode] = {month: summarize(group) for month, group in sorted(month_groups.items())}
    return {'version': VERSION, 'spec_hash': spec_hash, 'source_hash': source_hash,
            'entry_spec_hash': entry_run['spec_hash'], 'nested_spec_hash': nested_run['spec_hash'],
            'experiments': experiments,
            'summary': {'by_mode': {mode: summarize(items) for mode, items in by_mode.items()},
                        'paired_closed': len(both),
                        'paired_average_r_difference_after_close_minus_limit':
                            statistics.mean(pair['after_close']['r']-pair['touch_limit']['r']
                                            for pair in both) if both else None,
                        'closed_by_month': months,
                        'portfolio_simulated': False}}
