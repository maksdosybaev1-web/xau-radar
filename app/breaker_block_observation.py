"""Causal historical breaker candidates with explicit parent OB provenance."""
import hashlib
from collections import Counter
from .engine import Aggregator, atr
from .order_block_observation import OrderBlockObserver, VERSION as OB_VERSION

VERSION = 'bb-h1-observation-1'
BREAK_ATR = 1.5


class BreakerBlockObserver:
    def __init__(self, spec_hash, source_hash, parent_spec_hash):
        self.spec_hash, self.source_hash = spec_hash, source_hash
        self.parent_spec_hash = parent_spec_hash
        self.parent = OrderBlockObserver(parent_spec_hash, source_hash)
        self.aggregator = Aggregator(60)
        self.last = None
        self.parents = {}
        self.parent_count = 0
        self.breakers, self.active, self.events = [], {}, []

    def record(self, kind, when, parent_id, **details):
        self.events.append(dict(type=kind, time=when, parent_ob_id=parent_id,
                                version=VERSION, spec_hash=self.spec_hash,
                                parent_version=OB_VERSION, parent_spec_hash=self.parent_spec_hash,
                                source_hash=self.source_hash, symbol='XAUUSD', source='Dukascopy',
                                **details))

    def gap(self, when):
        for key in self.parents:
            self.record('bb_parent_unknown', when, key, reason='m1_gap')
        for block in self.active.values():
            block.update(state='unknown_after_gap', updated_at=when)
            self.record('bb_unknown', when, block['parent_ob_id'], breaker_id=block['id'], reason='m1_gap')
        self.parents.clear()
        self.active.clear()
        self.parent.gap(when)
        self.aggregator = Aggregator(60)

    def step(self, row):
        t = row['time']
        if self.last is not None:
            if t <= self.last:
                raise ValueError('M1 must be strictly increasing')
            if t-self.last != 60:
                self.gap(t)
        self.last = t
        bar = self.aggregator.add(row)
        if bar is not None:
            self.on_h1(bar)

    def on_h1(self, bar):
        when = bar['end']
        baseline = atr(self.parent.history)
        # Existing blocks only: a formation candle cannot also be its own retest.
        for block in list(self.active.values()):
            invalid = (bar['close'] < block['low'] if block['direction'] == 'buy'
                       else bar['close'] > block['high'])
            touch = bar['low'] <= block['high'] and bar['high'] >= block['low']
            if invalid or touch:
                state = 'invalidated' if invalid else 'first_retest'
                block.update(state=state, updated_at=when)
                self.record('bb_'+state, when, block['parent_ob_id'],
                            breaker_id=block['id'], bar=bar.copy())
                del self.active[block['id']]

        for key, parent in list(self.parents.items()):
            sell = parent['direction'] == 'buy'
            crossed = bar['close'] < parent['low'] if sell else bar['close'] > parent['high']
            if not crossed:
                continue
            body = bar['open']-bar['close'] if sell else bar['close']-bar['open']
            if baseline is None or baseline <= 0 or body < BREAK_ATR*baseline:
                self.record('bb_break_rejected', when, key, reason='insufficient_directed_impulse',
                            bar=bar.copy(), atr14=baseline)
            else:
                identifier = hashlib.sha256(f'{VERSION}|{self.spec_hash}|{key}|{when}'.encode()).hexdigest()[:24]
                block = dict(id=identifier, parent_ob_id=key, parent_direction=parent['direction'],
                             parent_known_at=parent['known_at'], known_at=when, updated_at=when,
                             direction='sell' if sell else 'buy', low=parent['low'], high=parent['high'],
                             state='awaiting_retest', break_bar=bar.copy(), atr14=baseline)
                self.breakers.append(block)
                self.active[identifier] = block
                self.record('bb_known', when, key, breaker_id=identifier, direction=block['direction'],
                            low=block['low'], high=block['high'], parent_known_at=parent['known_at'],
                            bar=bar.copy(), atr14=baseline)
            del self.parents[key]

        before = len(self.parent.zones)
        self.parent.on_h1(bar)
        for zone in self.parent.zones[before:]:
            self.parents[zone['id']] = zone.copy()
            self.parent_count += 1

    def finish(self):
        return dict(version=VERSION, spec_hash=self.spec_hash, source_hash=self.source_hash,
                    parent_version=OB_VERSION, parent_spec_hash=self.parent_spec_hash,
                    breakers=self.breakers, events=self.events,
                    summary=dict(parent_ob_count=self.parent_count, pending_parents=len(self.parents),
                                 breakers=len(self.breakers),
                                 states=dict(Counter(b['state'] for b in self.breakers)),
                                 event_types=dict(Counter(e['type'] for e in self.events)), paper_trades=0))


def run(rows, spec_hash, source_hash, parent_spec_hash):
    observer = BreakerBlockObserver(spec_hash, source_hash, parent_spec_hash)
    for row in rows:
        observer.step(row)
    return observer.finish()
