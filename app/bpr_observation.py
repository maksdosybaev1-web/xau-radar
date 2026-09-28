"""Historical geometry-only BPR observations; no entry direction or trading."""
import hashlib
from collections import Counter, deque
from .engine import Aggregator, fvg

VERSION = 'bpr-m15-observation-1'
WINDOW = 8*3600
FVG_RULES = {'min_impulse_atr': .5, 'min_gap_atr': .05}


class BPRObserver:
    def __init__(self, spec_hash, source_hash, detector_hash):
        self.spec_hash, self.source_hash, self.detector_hash = spec_hash, source_hash, detector_hash
        self.aggregator = Aggregator(15)
        self.bars = deque(maxlen=15)
        self.parents, self.zones, self.events = [], [], []
        self.active = {}
        self.parent_count = 0
        self.last = None

    def record(self, kind, when, zone, **extra):
        self.events.append(dict(type=kind, time=when, zone_id=zone['id'],
                                parent_ids=zone['parent_ids'], version=VERSION,
                                spec_hash=self.spec_hash, source_hash=self.source_hash,
                                detector_hash=self.detector_hash, symbol='XAUUSD',
                                source='Dukascopy', timeframe='M15', **extra))

    def step(self, row):
        t = row['time']
        if self.last is not None:
            if t <= self.last:
                raise ValueError('M1 must be strictly increasing')
            if t-self.last != 60:
                for zone in self.active.values():
                    zone.update(state='unknown_after_gap', updated_at=t)
                    self.record('bpr_unknown', t, zone, reason='m1_gap')
                self.active.clear()
                self.parents.clear()
                self.bars.clear()
                self.aggregator = Aggregator(15)
        self.last = t
        bar = self.aggregator.add(row)
        if bar is not None:
            self.on_m15(bar)

    def on_m15(self, bar):
        when = bar['end']
        for zone in list(self.active.values()):
            expired = when > zone['expires_at']
            touched = bar['high'] >= zone['low'] and bar['low'] <= zone['high']
            if expired or touched:
                state = 'expired' if expired else 'first_touch'
                zone.update(state=state, updated_at=when)
                self.record('bpr_'+state, when, zone, bar=bar.copy())
                del self.active[zone['id']]
        self.parents = [p for p in self.parents if when-p['created'] <= WINDOW]
        self.bars.append(bar)
        candidate = fvg(self.bars, FVG_RULES)
        if candidate:
            self.register(candidate)

    def register(self, candidate):
        parent = dict(candidate)
        identity = f'{VERSION}|{self.source_hash}|{self.spec_hash}|{self.detector_hash}|{parent["created"]}|{parent["direction"]}'
        parent['id'] = hashlib.sha256(identity.encode()).hexdigest()[:24]
        for older in self.parents:
            age = parent['created']-older['created']
            if older['direction'] == parent['direction'] or not 0 < age <= WINDOW:
                continue
            low, high = max(older['low'], parent['low']), min(older['high'], parent['high'])
            if low >= high:
                continue
            key = hashlib.sha256((older['id']+'|'+parent['id']).encode()).hexdigest()[:24]
            zone = dict(id=key, low=low, high=high, parent_ids=[older['id'], parent['id']],
                        parents=[older.copy(), parent.copy()], known_at=parent['created'],
                        expires_at=parent['created']+WINDOW, updated_at=parent['created'], state='awaiting_touch')
            self.zones.append(zone)
            self.active[key] = zone
            self.record('bpr_known', zone['known_at'], zone, low=low, high=high,
                        parents=zone['parents'])
        self.parents.append(parent)
        self.parent_count += 1

    def finish(self):
        return dict(version=VERSION, spec_hash=self.spec_hash, source_hash=self.source_hash,
                    detector_hash=self.detector_hash, parameters=dict(fvg=FVG_RULES.copy(), window_seconds=WINDOW),
                    zones=self.zones, events=self.events,
                    summary=dict(fvg_count=self.parent_count, bpr_count=len(self.zones),
                                 states=dict(Counter(z['state'] for z in self.zones)),
                                 event_types=dict(Counter(e['type'] for e in self.events)), paper_trades=0))


def run(rows, spec_hash, source_hash, detector_hash):
    observer = BPRObserver(spec_hash, source_hash, detector_hash)
    for row in rows:
        observer.step(row)
    return observer.finish()
