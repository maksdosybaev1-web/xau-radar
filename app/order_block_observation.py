"""Historical H1 order-block candidates from closed bid candles; no trading path."""

import hashlib
from collections import Counter, deque

from .engine import Aggregator, atr


VERSION = 'ob-h1-observation-1'
IMPULSE_ATR = 1.5


class OrderBlockObserver:
    def __init__(self, spec_hash, source_hash):
        self.spec_hash = spec_hash
        self.source_hash = source_hash
        self.aggregator = Aggregator(60)
        self.history = deque(maxlen=15)
        self.zones = []
        self.active = {}
        self.events = []
        self.last_m1 = None

    def event(self, kind, when, zone=None, **details):
        record = {'type': kind, 'time': when, 'version': VERSION,
                  'spec_hash': self.spec_hash, 'source_hash': self.source_hash,
                  'symbol': 'XAUUSD', 'source': 'Dukascopy', **details}
        if zone is not None:
            record['zone_id'] = zone['id']
        self.events.append(record)

    def gap(self, when):
        for zone in self.active.values():
            zone['state'] = 'unknown_after_gap'
            self.event('ob_unknown', when, zone, reason='m1_gap')
        self.active.clear()
        self.history.clear()
        self.aggregator = Aggregator(60)

    def step(self, row):
        when = row['time']
        if self.last_m1 is not None:
            if when <= self.last_m1:
                raise ValueError('M1 must be strictly increasing')
            if when - self.last_m1 != 60:
                self.gap(when)
        self.last_m1 = when
        bar = self.aggregator.add(row)
        if bar is not None:
            self.on_h1(bar)

    def on_h1(self, bar):
        when = bar['end']
        for zone in list(self.active.values()):
            invalid = (bar['close'] < zone['low'] if zone['direction'] == 'buy'
                       else bar['close'] > zone['high'])
            if invalid:
                zone['state'] = 'invalidated'
                zone['invalidated_at'] = when
                self.event('ob_invalidated', when, zone, close=bar['close'])
                del self.active[zone['id']]
            elif bar['low'] <= zone['high'] and bar['high'] >= zone['low']:
                zone['state'] = 'first_touch'
                zone['first_touch_at'] = when
                self.event('ob_first_touch', when, zone, bar_time=bar['time'])
                del self.active[zone['id']]

        origin = self.history[-1] if self.history else None
        baseline = atr(self.history)
        if origin is not None and baseline is not None and origin['end'] == bar['time']:
            buy = (origin['close'] < origin['open'] and bar['close'] > bar['open']
                   and bar['close'] > origin['high'])
            sell = (origin['close'] > origin['open'] and bar['close'] < bar['open']
                    and bar['close'] < origin['low'])
            if (buy or sell) and abs(bar['close'] - bar['open']) >= IMPULSE_ATR * baseline:
                direction = 'buy' if buy else 'sell'
                zone_id = hashlib.sha256(
                    f'{VERSION}|{self.source_hash}|{origin["time"]}|{direction}'.encode()
                ).hexdigest()[:24]
                zone = {'id': zone_id, 'direction': direction,
                        'low': origin['low'], 'high': origin['high'],
                        'origin_time': origin['time'], 'known_at': when,
                        'state': 'untested'}
                self.zones.append(zone)
                self.active[zone_id] = zone
                self.event('ob_known', when, zone, origin=origin.copy(),
                           impulse=bar.copy(), atr14=baseline,
                           impulse_atr=abs(bar['close'] - bar['open']) / baseline)
        self.history.append(bar)

    def finish(self):
        return {'version': VERSION, 'spec_hash': self.spec_hash,
                'source_hash': self.source_hash, 'zones': self.zones,
                'events': self.events,
                'summary': {'zones': len(self.zones),
                            'states': dict(Counter(z['state'] for z in self.zones)),
                            'event_types': dict(Counter(e['type'] for e in self.events)),
                            'paper_trades': 0}}


def run(rows, spec_hash, source_hash):
    observer = OrderBlockObserver(spec_hash, source_hash)
    for row in rows:
        observer.step(row)
    return observer.finish()
