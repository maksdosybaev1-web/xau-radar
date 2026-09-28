"""Causal M15 pivot-candle -> M5 FVG -> M1 FVG observation chain."""
import hashlib
from collections import Counter, deque

from .engine import Aggregator, fvg


VERSION = 'nested-m15-m5-m1-observation-1'
FVG_CONFIG = {'min_impulse_atr': .5, 'min_gap_atr': .05}
LIFE = {15: 24*3600, 5: 8*3600, 1: 2*3600}


def parent_pivots(bars):
    if len(bars) < 5:
        return []
    b = list(bars)[-5:]
    middle = b[2]
    others = b[:2] + b[3:]
    result = []
    if all(middle['low'] < x['low'] for x in others):
        result.append(('buy', middle))
    if all(middle['high'] > x['high'] for x in others):
        result.append(('sell', middle))
    return result


def inside(child, parent):
    return child['direction'] == parent['direction'] and parent['low'] <= child['low'] <= child['high'] <= parent['high']


class NestedObserver:
    def __init__(self, spec_hash, source_hash, *, symbol='XAUUSD', source='Dukascopy'):
        self.spec_hash, self.source_hash = spec_hash, source_hash
        self.symbol, self.source = symbol, source
        self.aggregators = {n: Aggregator(n) for n in (5, 15)}
        self.bars = {n: deque(maxlen=20) for n in (1, 5, 15)}
        self.zones = {n: [] for n in (1, 5, 15)}
        self.chains, self.active, self.events = {}, {}, []
        self.event_sequence = 0
        self.last = None

    def event(self, event_type, when, **values):
        self.event_sequence += 1
        record = {'id': hashlib.sha256(f'{VERSION}|{self.event_sequence}|{event_type}|{when}'.encode()).hexdigest()[:24],
                  'time': when, 'type': event_type, 'version': VERSION,
                  'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                  'symbol': self.symbol, 'source': self.source, **values}
        self.events.append(record)
        return record

    def reset_for_gap(self, when):
        for chain in list(self.active.values()):
            chain['state'] = 'incomplete'
            self.event('chain_incomplete', when, chain_id=chain['id'], reason='m1_gap')
        self.active.clear()
        self.aggregators = {n: Aggregator(n) for n in (5, 15)}
        for n in self.bars:
            self.bars[n].clear()
            self.zones[n].clear()

    def prune(self, when):
        for n in self.zones:
            self.zones[n] = [z for z in self.zones[n] if when-z['created'] <= LIFE[n]]
        for chain in list(self.active.values()):
            if when-chain['known_at'] > LIFE[1]:
                chain['state'] = 'expired'
                self.event('chain_expired', when, chain_id=chain['id'], reason='m1_return_deadline')
                self.active.pop(chain['id'])

    def zone(self, n, raw, kind):
        zone = dict(raw)
        zone.update(id=hashlib.sha256(
            f'{VERSION}|{self.symbol}|{n}|{kind}|{raw["origin"]}|{raw["created"]}|{raw["direction"]}'.encode()
        ).hexdigest()[:24], timeframe=f'M{n}', kind=kind, touched_at=None)
        self.zones[n].append(zone)
        self.event('zone_known', zone['created'], zone_id=zone['id'], timeframe=zone['timeframe'],
                   kind=kind, direction=zone['direction'], low=zone['low'], high=zone['high'],
                   origin=zone['origin'])

    def link(self, when):
        for inner in self.zones[1]:
            if inner['id'] in self.chains:
                continue
            choices = []
            for middle in self.zones[5]:
                if not inside(inner, middle) or inner['created'] < middle['origin']:
                    continue
                for outer in self.zones[15]:
                    if (inside(middle, outer) and middle['created'] >= outer['origin']
                            and inner['created'] >= outer['origin']):
                        choices.append((middle['high']-middle['low'], outer['high']-outer['low'],
                                        middle['id'], outer['id'], middle, outer))
            if not choices:
                continue
            _, _, _, _, middle, outer = min(choices)
            known = max(inner['created'], middle['created'], outer['created'])
            chain_id = hashlib.sha256(
                f'{VERSION}|{outer["id"]}|{middle["id"]}|{inner["id"]}'.encode()).hexdigest()[:24]
            prelinked_touch = inner['touched_at'] is not None and inner['touched_at'] <= known
            chain = {'id': chain_id, 'direction': inner['direction'], 'known_at': known,
                     'outer_id': outer['id'], 'middle_id': middle['id'], 'inner_id': inner['id'],
                     'outer': [outer['low'], outer['high']], 'middle': [middle['low'], middle['high']],
                     'inner': [inner['low'], inner['high']],
                     'inner_created': inner['created'], 'middle_created': middle['created'],
                     'outer_created': outer['created'],
                     'state': 'previously_touched' if prelinked_touch else 'chain_known'}
            self.chains[inner['id']] = chain
            self.event('chain_known', when, **chain)
            if not prelinked_touch:
                self.active[chain_id] = chain

    def step(self, row):
        t = row['time']
        if self.last is not None and t <= self.last:
            raise ValueError('M1 must be strictly increasing')
        if self.last is not None and t-self.last != 60:
            self.reset_for_gap(t)
        self.last = t
        self.prune(t+60)

        for inner in self.zones[1]:
            if t < inner['created'] or inner['touched_at'] is not None:
                continue
            side = 'ask' if inner['direction'] == 'buy' else 'bid'
            if row[f'{side}_low'] <= inner['high'] and row[f'{side}_high'] >= inner['low']:
                inner['touched_at'] = t+60
        for chain in list(self.active.values()):
            if t < chain['known_at']:
                continue
            side = 'ask' if chain['direction'] == 'buy' else 'bid'
            if row[f'{side}_low'] <= chain['inner'][1] and row[f'{side}_high'] >= chain['inner'][0]:
                chain['state'] = 'touch_observed'
                self.event('touch_observed', t+60, chain_id=chain['id'], direction=chain['direction'],
                           price_side=side, m1_time=t, low=row[f'{side}_low'],
                           high=row[f'{side}_high'], intraminute_order_unknown=True)
                self.active.pop(chain['id'])

        closed = {1: {'time': t, 'end': t+60, 'open': row['bid_open'],
                      'high': row['bid_high'], 'low': row['bid_low'],
                      'close': row['bid_close'], 'volume': row['volume']}}
        for n, aggregate in self.aggregators.items():
            bar = aggregate.add(row)
            if bar:
                closed[n] = bar
        new_zone = False
        for n, bar in closed.items():
            self.bars[n].append(bar)
            if n == 15:
                for direction, pivot in parent_pivots(self.bars[15]):
                    self.zone(15, {'direction': direction, 'low': pivot['low'],
                                   'high': pivot['high'], 'origin': pivot['time'],
                                   'created': bar['end']}, 'pivot_candle_range')
                    new_zone = True
            else:
                candidate = fvg(self.bars[n], FVG_CONFIG)
                if candidate:
                    self.zone(n, candidate, 'fvg')
                    new_zone = True
        if new_zone:
            self.link(t+60)

    def finish(self):
        if self.last is not None:
            for chain in list(self.active.values()):
                chain['state'] = 'incomplete'
                self.event('chain_incomplete', self.last+60, chain_id=chain['id'], reason='end_of_data')
            self.active.clear()
        states = Counter(x['state'] for x in self.chains.values())
        kinds = Counter(x['type'] for x in self.events)
        return {'version': VERSION, 'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                'chains': list(self.chains.values()), 'events': self.events,
                'summary': {'chains': len(self.chains), 'states': dict(states),
                            'event_types': dict(kinds), 'paper_trades': 0}}


def run(rows, spec_hash, source_hash):
    model = NestedObserver(spec_hash, source_hash)
    for row in rows:
        model.step(row)
    return model.finish()
