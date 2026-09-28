"""Causal SBR SELL detector; historical execution and read-only live observation."""
import hashlib
from collections import Counter, deque

from .engine import Aggregator, atr, trend
from .scenario_lifecycle import SNR_RETEST_BARS


VERSION = 'snr-sbr-close-0.2-research'
SPEC = 'docs/SNR_SBR_SPEC.md'
SLIP = 0.05
FEE = 0.04


def support(bars):
    """Return a strict close pivot once two right M5 bars have closed."""
    if len(bars) < 5:
        return None
    b = list(bars)[-5:]
    return b[2] if all(b[2]['close'] < b[i]['close'] for i in (0, 1, 3, 4)) else None


def target(bars, ceiling):
    """Nearest confirmed M15 wick pivot below the confirmation close."""
    b = list(bars)[-120:]
    prices = [b[i]['low'] for i in range(2, len(b)-2)
              if b[i]['low'] < ceiling and all(b[i]['low'] < b[j]['low'] for j in (i-2, i-1, i+1, i+2))]
    return max(prices) if prices else None


def short_exit(trade, row):
    stop_hit = row['ask_high'] >= trade['stop']
    target_hit = row['ask_low'] <= trade['target']
    if stop_hit:
        return max(row['ask_open'], trade['stop']) + SLIP, 'stop_ambiguous' if target_hit else 'stop'
    if target_hit:
        return trade['target'] + SLIP, 'target'
    if row['time'] + 60 - trade['entry_time'] >= 3600:
        return row['ask_close'] + SLIP, 'timeout'
    return None


class SBRResearch:
    def __init__(self, spec_hash, source_hash, *, symbol='XAUUSD', source='Dukascopy', signals_only=False):
        self.spec_hash, self.source_hash = spec_hash, source_hash
        self.symbol, self.source, self.signals_only = symbol, source, signals_only
        self.aggregators = {n: Aggregator(n) for n in (5, 15, 60)}
        self.bars = {5: deque(maxlen=30), 15: deque(maxlen=120), 60: deque(maxlen=240)}
        self.levels, self.events, self.trades = {}, [], []
        self.last = None

    def record(self, level, state, when, reason, **values):
        level['state'] = state
        level['updated_at'] = when
        event = {'id': hashlib.sha256(f"{level['id']}|{state}|{when}|{reason}".encode()).hexdigest()[:24],
                 'level_id': level['id'], 'state': state, 'time': when, 'reason': reason,
                 'version': VERSION, 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                 'symbol': self.symbol, 'source': self.source, 'source_supported': 'support_break_retest',
                 'research_assumption': 'numeric_rules', **values}
        self.events.append(event)

    def gap(self, when):
        for level in self.levels.values():
            if level['state'] in ('level_known', 'broken', 'pending_entry'):
                self.record(level, 'incomplete', when,
                            'missing_entry_bar' if level['state'] == 'pending_entry' else 'm1_gap')
        for trade in self.trades:
            if trade['state'] == 'observing':
                trade.update(state='incomplete', reason='m1_gap', updated_at=when)
                self.record(self.levels[trade['level_id']], 'incomplete', when, 'm1_gap_during_trade')
        self.aggregators = {n: Aggregator(n) for n in (5, 15, 60)}
        self.bars[5].clear()

    def enter(self, level, row):
        t = row['time']
        if t != level['confirmation_time']:
            self.record(level, 'incomplete', t, 'missing_entry_bar')
            return
        e = row['bid_open'] - SLIP
        spread = row['ask_open'] - row['bid_open']
        s, target_price = level['stop'], level['target']
        reason = None
        if not target_price < e < s:
            reason = 'invalid_entry_geometry'
        elif (e-target_price)/(s-e) < 1:
            reason = 'rr_below_one'
        elif spread > 1 or spread > .2*(s-e):
            reason = 'spread_limit'
        if reason:
            self.record(level, 'entry_rejected', t, reason, entry=e, spread=spread)
            return
        quantity = 25/(s-e+SLIP+2*FEE)
        trade = {'id': level['id'], 'level_id': level['id'], 'state': 'observing',
                 'entry_time': t, 'entry': e, 'stop': s, 'target': target_price,
                 'spread': spread, 'quantity_oz': quantity, 'risk_usd': 25,
                 'execution_mode': 'historical_m1_open', 'slippage_per_oz_side': SLIP,
                 'commission_per_oz_side': FEE, 'exit_time': None, 'pnl': None, 'r': None}
        self.trades.append(trade)
        self.record(level, 'observing', t, 'paper_entry', entry=e, spread=spread, quantity_oz=quantity)

    def step(self, row):
        t = row['time']
        if self.last is not None and t <= self.last:
            raise ValueError('M1 must be strictly increasing')
        if self.last is not None and t-self.last != 60:
            self.gap(t)
        self.last = t
        for level in self.levels.values():
            if level['state'] == 'pending_entry' and not self.signals_only:
                self.enter(level, row)
        for trade in self.trades:
            if trade['state'] != 'observing':
                continue
            result = short_exit(trade, row)
            if result:
                price, reason = result
                pnl = (trade['entry']-price-2*FEE)*trade['quantity_oz']
                trade.update(state='closed', exit_time=t+60, exit=price, exit_reason=reason,
                             pnl=pnl, r=pnl/25)
                self.record(self.levels[trade['level_id']], 'closed', t+60, reason,
                            exit=price, pnl=pnl, r=pnl/25)
        closed = {}
        for n, agg in self.aggregators.items():
            bar = agg.add(row)
            if bar:
                self.bars[n].append(bar)
                closed[n] = bar
        if 5 in closed:
            self.on_m5(closed[5])

    def on_m5(self, bar):
        when = bar['end']
        context = trend(self.bars[60]) if len(self.bars[60]) >= 240 else 'neutral'
        for level in list(self.levels.values()):
            state = level['state']
            if state == 'level_known':
                age = (when-level['known_at'])//300
                if age > 24:
                    self.record(level, 'expired', when, 'break_deadline')
                elif age and bar['time'] > level['known_at']-300 and self.bars[5][-2]['close'] >= level['low_band'] and bar['close'] < level['low_band']:
                    if context != 'sell':
                        self.record(level, 'context_rejected', when, 'h1_not_sell', h1_context=context)
                    else:
                        level.update(break_time=when, break_bar=bar.copy(), h1_context_break=context)
                        self.record(level, 'broken', when, 'close_below_support', bar=bar.copy(), h1_context=context)
            elif state == 'broken':
                age = (when-level['break_time'])//300
                if age > SNR_RETEST_BARS:
                    self.record(level, 'expired', when, 'retest_deadline')
                elif context != 'sell':
                    self.record(level, 'context_rejected', when, 'h1_context_changed', h1_context=context)
                elif bar['close'] > level['high_band']:
                    self.record(level, 'invalidated', when, 'close_above_band', bar=bar.copy())
                elif bar['high'] >= level['low_band'] and bar['low'] <= level['high_band']:
                    level.update(first_touch=bar.copy(), h1_context_confirmation=context)
                    if bar['close'] < level['low_band'] and bar['close'] < bar['open']:
                        stop = max(bar['high'], level['high_band']) + .1*level['atr']
                        objective = target(self.bars[15], bar['close']) if len(self.bars[15]) >= 120 else None
                        level.update(confirmation_time=when, stop=stop, target=objective)
                        self.record(level, 'confirmed', when, 'first_retest_confirmed', bar=bar.copy(),
                                    stop=stop, target=objective, h1_context=context)
                        if objective is None:
                            self.record(level, 'entry_rejected', when, 'no_known_target')
                        elif self.signals_only:
                            self.record(level, 'signal_ready', when, 'closed_bar_observation_only',
                                        stop=stop, target=objective)
                        else:
                            self.record(level, 'pending_entry', when, 'await_exact_next_m1')
                    else:
                        self.record(level, 'retest_unconfirmed', when, 'first_retest_failed', bar=bar.copy())
        pivot = support(self.bars[5])
        if pivot:
            a = atr(self.bars[5], 14)
            if a and a > 0:
                level_id = hashlib.sha256(f"{self.symbol}|{VERSION}|{pivot['time']}|{when}".encode()).hexdigest()[:24]
                if level_id not in self.levels:
                    low_band, high_band = pivot['close']-.1*a, pivot['close']+.1*a
                    level = {'id': level_id, 'version': VERSION, 'spec_hash': self.spec_hash,
                             'source_hash': self.source_hash, 'symbol': self.symbol, 'source': self.source,
                             'pivot_bar': pivot.copy(), 'known_at': when, 'level': pivot['close'],
                             'atr': a, 'tolerance': .1*a, 'low_band': low_band, 'high_band': high_band,
                             'state': 'level_known'}
                    self.levels[level_id] = level
                    self.record(level, 'level_known', when, 'strict_close_pivot', pivot=pivot.copy(), atr=a)

    def finish(self):
        for level in self.levels.values():
            if level['state'] in ('level_known', 'broken', 'pending_entry'):
                self.record(level, 'incomplete', self.last+60, 'end_of_data')
        for trade in self.trades:
            if trade['state'] == 'observing':
                trade.update(state='incomplete', reason='end_of_data')
                self.record(self.levels[trade['level_id']], 'incomplete', self.last+60, 'end_of_data_during_trade')
        closed = [x for x in self.trades if x['state'] == 'closed']
        states = Counter(x['state'] for x in self.levels.values())
        return {'version': VERSION, 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                'levels': list(self.levels.values()), 'events': self.events, 'trades': self.trades,
                'summary': {'levels': len(self.levels), 'states': dict(states), 'closed': len(closed),
                            'average_r': sum(x['r'] for x in closed)/len(closed) if closed else None,
                            'net_pnl_independent_usd': sum(x['pnl'] for x in closed),
                            'wins': sum(x['pnl'] > 0 for x in closed),
                            'incomplete_trades': sum(x['state'] == 'incomplete' for x in self.trades)}}


def run(rows, spec_hash, source_hash):
    model = SBRResearch(spec_hash, source_hash)
    for row in rows:
        model.step(row)
    return model.finish()
