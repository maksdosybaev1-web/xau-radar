"""Causal, observation-only Classic A/V research over closed bid/ask M1 bars."""
import hashlib
from collections import Counter, deque

from .engine import Aggregator, atr, trend


VERSION = 'snr-classic-av-observation-2'
MAX_AGE_M5 = 24


def pivots(bars):
    """Return strict closing-price turns only after two M5 bars to the right."""
    if len(bars) < 5:
        return []
    b = list(bars)[-5:]
    middle = b[2]
    neighbors = b[:2] + b[3:]
    out = []
    if all(middle['close'] > x['close'] for x in neighbors):
        out.append(('A', 'sell', middle))
    if all(middle['close'] < x['close'] for x in neighbors):
        out.append(('V', 'buy', middle))
    return out


class ClassicObservation:
    def __init__(self, spec_hash, source_hash, *, symbol='XAUUSD', source='Dukascopy'):
        self.spec_hash, self.source_hash = spec_hash, source_hash
        self.symbol, self.source = symbol, source
        self.aggregators = {n: Aggregator(n) for n in (5, 60)}
        self.bars = {5: deque(maxlen=30), 60: deque(maxlen=240)}
        self.levels, self.active, self.events = {}, {}, []
        self.last = None

    def record(self, level, state, when, reason, **extra):
        level['state'] = state
        level['updated_at'] = when
        event = {'id': hashlib.sha256(f"{level['id']}|{state}|{when}|{reason}".encode()).hexdigest()[:24],
                 'level_id': level['id'], 'model': level['model'], 'direction': level['direction'],
                 'state': state, 'time': when, 'reason': reason, 'version': VERSION,
                 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                 'symbol': self.symbol, 'source': self.source, **extra}
        self.events.append(event)
        if state not in ('level_known', 'armed'):
            self.active.pop(level['id'], None)

    def step(self, row):
        t = row['time']
        if self.last is not None and t <= self.last:
            raise ValueError('M1 must be strictly increasing')
        if self.last is not None and t-self.last != 60:
            for level in list(self.active.values()):
                self.record(level, 'incomplete', t, 'm1_gap')
            self.aggregators = {n: Aggregator(n) for n in (5, 60)}
            self.bars[5].clear()
            # Preserve confirmed H1 structure; require a fresh H1 close at touch.
        self.last = t

        # These levels were armed at the end of an earlier closed M5 bar.
        for level in list(self.active.values()):
            if t+60 > level['known_at']+MAX_AGE_M5*300:
                self.record(level, 'expired', t+60, 'observation_deadline')
                continue
            if level['state'] != 'armed' or t < level['armed_at']:
                continue
            side = 'ask' if level['direction'] == 'buy' else 'bid'
            if row[f'{side}_low'] > level['high_band'] or row[f'{side}_high'] < level['low_band']:
                continue
            context = (trend(self.bars[60]) if len(self.bars[60]) >= 240
                       and t+60-self.bars[60][-1]['end'] <= 3600 else 'neutral')
            values = {'m1_time': t, 'price_side': side, 'low': row[f'{side}_low'],
                      'high': row[f'{side}_high'], 'h1_context': context,
                      'execution_mode': 'historical_m1_touch_observation',
                      'intraminute_order_unknown': True}
            if context == level['direction']:
                self.record(level, 'touch_observed', t+60, 'first_return_to_band', **values)
            else:
                self.record(level, 'context_rejected', t+60, 'h1_not_aligned_on_first_touch', **values)

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
        for level in list(self.active.values()):
            age = (when-level['known_at'])//300
            if age > MAX_AGE_M5:
                self.record(level, 'expired', when, 'observation_deadline')
            elif level['state'] == 'level_known':
                departed = (bar['close'] < level['low_band'] if level['direction'] == 'sell'
                            else bar['close'] > level['high_band'])
                if departed:
                    level['armed_at'] = when
                    self.record(level, 'armed', when, 'close_departed_from_band')
            elif level['state'] == 'armed':
                broken = (bar['close'] > level['high_band'] if level['direction'] == 'sell'
                          else bar['close'] < level['low_band'])
                if broken:
                    self.record(level, 'invalidated', when, 'm5_close_through_band')

        a = atr(self.bars[5], 14)
        if not a or a <= 0:
            return
        for model, direction, pivot in pivots(self.bars[5]):
            level_id = hashlib.sha256(
                f'{self.symbol}|{VERSION}|{model}|{pivot["time"]}|{when}'.encode()).hexdigest()[:24]
            if level_id in self.levels:
                continue
            width = .1*a
            level = {'id': level_id, 'model': model, 'direction': direction,
                     'pivot_time': pivot['time'], 'known_at': when,
                     'level': pivot['close'], 'atr_at_detection': a,
                     'low_band': pivot['close']-width, 'high_band': pivot['close']+width,
                     'state': 'level_known'}
            self.levels[level_id] = level
            self.active[level_id] = level
            self.record(level, 'level_known', when, 'strict_m5_close_pivot', pivot=pivot.copy())
            departed = (bar['close'] < level['low_band'] if direction == 'sell'
                        else bar['close'] > level['high_band'])
            if departed:
                level['armed_at'] = when
                self.record(level, 'armed', when, 'close_departed_from_band')

    def finish(self):
        if self.last is not None:
            for level in list(self.active.values()):
                self.record(level, 'incomplete', self.last+60, 'end_of_data')
        states = Counter(x['state'] for x in self.levels.values())
        return {'version': VERSION, 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                'levels': list(self.levels.values()), 'events': self.events,
                'summary': {'levels': len(self.levels), 'states': dict(states),
                            'touches_by_model': {m: sum(x['state'] == 'touch_observed' and x['model'] == m
                                                        for x in self.levels.values()) for m in ('A', 'V')},
                            'paper_trades': 0}}


def run(rows, spec_hash, source_hash):
    model = ClassicObservation(spec_hash, source_hash)
    for row in rows:
        model.step(row)
    return model.finish()
