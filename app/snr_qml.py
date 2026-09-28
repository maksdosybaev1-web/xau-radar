"""Observation-only QML break/reverse-break/return study; no orders."""
import hashlib
from collections import Counter, deque

from .engine import Aggregator, atr
from .snr_classic import pivots


VERSION = 'snr-qml-two-break-observation-1'
PHASE_LIMIT_M5 = 24
ACTIVE = {'level_known', 'break_1', 'break_2', 'return_1', 'departed_again'}


def beyond(level, close, phase):
    """Strict M5 close beyond the frozen zone in the required direction."""
    buy = level['direction'] == 'buy'
    if phase == 'first':
        return close < level['low_band'] if buy else close > level['high_band']
    return close > level['high_band'] if buy else close < level['low_band']


class QMLObservation:
    def __init__(self, spec_hash, source_hash, *, symbol='XAUUSD', source='Dukascopy'):
        self.spec_hash, self.source_hash = spec_hash, source_hash
        self.symbol, self.source = symbol, source
        self.aggregator = Aggregator(5)
        self.bars = deque(maxlen=30)
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
        if state not in ACTIVE:
            self.active.pop(level['id'], None)

    def step(self, row):
        t = row['time']
        if self.last is not None and t <= self.last:
            raise ValueError('M1 must be strictly increasing')
        if self.last is not None and t-self.last != 60:
            for level in list(self.active.values()):
                self.record(level, 'incomplete', t, 'm1_gap')
            self.aggregator = Aggregator(5)
            self.bars.clear()
        self.last = t

        for level in list(self.active.values()):
            if level['state'] not in ('break_2', 'departed_again') or t < level['updated_at']:
                continue
            side = 'ask' if level['direction'] == 'buy' else 'bid'
            if row[f'{side}_low'] > level['high_band'] or row[f'{side}_high'] < level['low_band']:
                continue
            number = 1 if level['state'] == 'break_2' else 2
            self.record(level, f'return_{number}', t+60, 'm1_band_intersection',
                        contact_number=number, m1_time=t, price_side=side,
                        low=row[f'{side}_low'], high=row[f'{side}_high'],
                        intraminute_order_unknown=True,
                        execution_mode='historical_m1_contact_observation')

        bar = self.aggregator.add(row)
        if bar:
            self.bars.append(bar)
            self.on_m5(bar)

    def on_m5(self, bar):
        when = bar['end']
        for level in list(self.active.values()):
            state = level['state']
            if (when-level['updated_at'])//300 > PHASE_LIMIT_M5:
                self.record(level, 'expired', when, f'{state}_deadline')
            elif state == 'level_known' and beyond(level, bar['close'], 'first'):
                self.record(level, 'break_1', when, 'first_m5_close_through_original_level')
            elif state == 'break_1' and beyond(level, bar['close'], 'reverse'):
                self.record(level, 'break_2', when, 'reverse_m5_close_through_level')
            elif state == 'return_1' and beyond(level, bar['close'], 'reverse'):
                self.record(level, 'departed_again', when, 'm5_close_away_after_first_return')

        a = atr(self.bars, 14)
        if not a or a <= 0:
            return
        for classic, direction, pivot in pivots(self.bars):
            model = 'QML_BUY' if classic == 'V' else 'QML_SELL'
            level_id = hashlib.sha256(
                f'{self.symbol}|{VERSION}|{model}|{pivot["time"]}|{when}'.encode()).hexdigest()[:24]
            if level_id in self.levels:
                continue
            width = .1*a
            level = {'id': level_id, 'model': model, 'direction': direction,
                     'pivot_time': pivot['time'], 'known_at': when,
                     'level': pivot['close'], 'atr_at_detection': a,
                     'low_band': pivot['close']-width, 'high_band': pivot['close']+width,
                     'state': 'level_known', 'updated_at': when}
            self.levels[level_id] = level
            self.active[level_id] = level
            self.record(level, 'level_known', when, 'strict_classic_close_pivot', pivot=pivot.copy())
            if beyond(level, bar['close'], 'first'):
                self.record(level, 'break_1', when, 'first_m5_close_through_original_level')

    def finish(self):
        if self.last is not None:
            for level in list(self.active.values()):
                self.record(level, 'incomplete', self.last+60, 'end_of_data')
        states = Counter(x['state'] for x in self.levels.values())
        contacts = Counter((e['model'], e['contact_number']) for e in self.events
                           if e['state'] in ('return_1', 'return_2'))
        return {'version': VERSION, 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                'levels': list(self.levels.values()), 'events': self.events,
                'summary': {'levels': len(self.levels), 'states': dict(states),
                            'contacts': {model: {'first': contacts[(model, 1)],
                                                 'second': contacts[(model, 2)]}
                                         for model in ('QML_BUY', 'QML_SELL')},
                            'paper_trades': 0}}


def run(rows, spec_hash, source_hash):
    model = QMLObservation(spec_hash, source_hash)
    for row in rows:
        model.step(row)
    return model.finish()
