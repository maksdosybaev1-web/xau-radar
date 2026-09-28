"""Causal H1 structure and price-extremum observations; no order-book claims."""
import hashlib
from collections import Counter, deque

from .engine import Aggregator


VERSION = 'h1-structure-extrema-observation-1'
MAX_CONFIRM_HOURS = 12


def confirmed_turn(window):
    """Strict wick pivots known after two closed H1 bars to the right."""
    if len(window) < 5:
        return []
    bars = list(window)[-5:]
    middle = bars[2]
    others = bars[:2] + bars[3:]
    result = []
    if all(middle['high'] > bar['high'] for bar in others):
        result.append(('upper', middle['high'], middle))
    if all(middle['low'] < bar['low'] for bar in others):
        result.append(('lower', middle['low'], middle))
    return result


def direction(highs, lows):
    if len(highs) < 2 or len(lows) < 2:
        return 'neutral'
    if highs[-1]['price'] > highs[-2]['price'] and lows[-1]['price'] > lows[-2]['price']:
        return 'buy'
    if highs[-1]['price'] < highs[-2]['price'] and lows[-1]['price'] < lows[-2]['price']:
        return 'sell'
    return 'neutral'


class StructureObserver:
    def __init__(self, spec_hash, source_hash, *, symbol='XAUUSD', source='Dukascopy'):
        self.spec_hash, self.source_hash = spec_hash, source_hash
        self.symbol, self.source = symbol, source
        self.aggregator = Aggregator(60)
        self.window = deque(maxlen=5)
        self.highs, self.lows = deque(maxlen=2), deque(maxlen=2)
        self.pools, self.active_pools, self.events = {}, {}, []
        self.trend = 'neutral'
        self.pending = None
        self.last_m1 = None
        self.last_h1 = None
        self.epoch = 0

    def event(self, kind, when, **values):
        record = {'id': hashlib.sha256(f'{VERSION}|{len(self.events)+1}|{kind}|{when}'.encode()).hexdigest()[:24],
                  'time': when, 'type': kind, 'version': VERSION,
                  'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                  'symbol': self.symbol, 'source': self.source, 'epoch': self.epoch,
                  **values}
        self.events.append(record)
        return record

    def gap(self, when):
        if self.pending:
            self.event('sequence_incomplete', when, reason='m1_gap',
                       choch_id=self.pending['choch_id'])
            self.pending = None
        for pool in list(self.active_pools.values()):
            pool['state'] = 'unknown_after_gap'
            self.event('extremum_unknown', when, reason='m1_gap', extremum_id=pool['id'])
        self.active_pools.clear()
        self.aggregator = Aggregator(60)
        self.window.clear()
        self.highs.clear()
        self.lows.clear()
        self.trend = 'neutral'
        self.last_h1 = None
        self.epoch += 1

    def step(self, row):
        t = row['time']
        if self.last_m1 is not None and t <= self.last_m1:
            raise ValueError('M1 must be strictly increasing')
        if self.last_m1 is not None and t-self.last_m1 != 60:
            self.gap(t)
        self.last_m1 = t
        bar = self.aggregator.add(row)
        if bar:
            self.on_h1(bar)

    def on_h1(self, bar):
        when = bar['end']
        if self.last_h1 is not None and bar['time'] != self.last_h1['end']:
            self.gap(bar['time'])
        self.last_h1 = bar
        for pool in list(self.active_pools.values()):
            visited = (bar['high'] >= pool['price'] if pool['side'] == 'upper'
                       else bar['low'] <= pool['price'])
            if visited:
                pool['state'] = 'price_revisited'
                pool['revisited_at'] = when
                self.active_pools.pop(pool['id'])
                self.event('extremum_revisited', when, extremum_id=pool['id'],
                           side=pool['side'], price=pool['price'],
                           observation='wick_crossed_price_not_orders')

        if self.pending:
            p = self.pending
            crossed = (bar['close'] < p['threshold'] if p['direction'] == 'sell'
                       else bar['close'] > p['threshold'])
            if crossed and bar['time'] >= p['choch_time']:
                self.event('bos_confirmed', when, direction=p['direction'],
                           choch_id=p['choch_id'], threshold=p['threshold'],
                           close=bar['close'], bar=bar.copy())
                self.trend = p['direction']
                self.pending = None
                self.highs.clear()
                self.lows.clear()
            elif when-p['choch_time'] > MAX_CONFIRM_HOURS*3600:
                self.event('sequence_expired', when, choch_id=p['choch_id'],
                           reason='bos_deadline')
                self.pending = None
        elif self.trend != 'neutral':
            reference = (self.lows[-1] if self.trend == 'buy' and self.lows else
                         self.highs[-1] if self.trend == 'sell' and self.highs else None)
            if reference:
                changed = (bar['close'] < reference['price'] if self.trend == 'buy'
                           else bar['close'] > reference['price'])
                if changed:
                    new_direction = 'sell' if self.trend == 'buy' else 'buy'
                    threshold = bar['low'] if new_direction == 'sell' else bar['high']
                    event = self.event('choch_observed', when, previous_direction=self.trend,
                                       direction=new_direction, protected_extremum_id=reference['id'],
                                       protected_price=reference['price'],
                                       threshold_for_bos=threshold, close=bar['close'], bar=bar.copy())
                    self.pending = {'direction': new_direction, 'choch_id': event['id'],
                                    'choch_time': when, 'threshold': threshold}

        self.window.append(bar)
        for side, price, pivot in confirmed_turn(self.window):
            extremum_id = hashlib.sha256(
                f'{VERSION}|{self.symbol}|{self.epoch}|{side}|{pivot["time"]}'.encode()).hexdigest()[:24]
            if extremum_id in self.pools:
                continue
            role = ('protected_hypothesis' if (self.trend == 'buy' and side == 'lower')
                    or (self.trend == 'sell' and side == 'upper') else
                    'weak_hypothesis' if self.trend != 'neutral' else 'undetermined')
            pool = {'id': extremum_id, 'side': side, 'price': price,
                    'pivot_time': pivot['time'], 'known_at': when,
                    'state': 'price_not_revisited', 'role_at_detection': role,
                    'epoch': self.epoch}
            self.pools[extremum_id] = pool
            self.active_pools[extremum_id] = pool
            (self.highs if side == 'upper' else self.lows).append(pool)
            self.event('extremum_known', when, extremum_id=extremum_id,
                       side=side, price=price, pivot=pivot.copy(), role_hypothesis=role)
        if self.trend == 'neutral':
            inferred = direction(self.highs, self.lows)
            if inferred != 'neutral':
                self.trend = inferred
                self.event('trend_known', when, direction=inferred,
                           high_ids=[p['id'] for p in self.highs],
                           low_ids=[p['id'] for p in self.lows])

    def finish(self):
        if self.last_m1 is not None and self.pending:
            self.event('sequence_incomplete', self.last_m1+60,
                       reason='end_of_data', choch_id=self.pending['choch_id'])
            self.pending = None
        states = Counter(pool['state'] for pool in self.pools.values())
        kinds = Counter(event['type'] for event in self.events)
        return {'version': VERSION, 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                'extrema': list(self.pools.values()), 'events': self.events,
                'summary': {'extrema': len(self.pools), 'extremum_states': dict(states),
                            'event_types': dict(kinds), 'paper_trades': 0}}


def run(rows, spec_hash, source_hash):
    model = StructureObserver(spec_hash, source_hash)
    for row in rows:
        model.step(row)
    return model.finish()
