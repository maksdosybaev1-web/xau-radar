"""Auxiliary OCL seam observations attached to existing SBR/RBS break events."""
import hashlib
from collections import Counter

from .engine import Aggregator


VERSION = 'snr-ocl-parent-seam-observation-1'
PARENTS = {'sbr': ('OCL_SELL', 'sell'), 'rbs': ('OCL_BUY', 'buy')}


def classify(parent, first, second, direction):
    """Use only two closed adjacent M5 bodies and a previously known parent band."""
    if first is None or second is None or first['end'] != second['time']:
        return 'missing_adjacent_m5', None
    bearish = direction == 'sell'
    if not all(b['close'] < b['open'] if bearish else b['close'] > b['open']
               for b in (first, second)):
        return 'body_direction_mismatch', None
    low, high = sorted((first['close'], second['open']))
    seam = {'low': low, 'high': high}
    if high < parent['low_band'] or low > parent['high_band']:
        return 'seam_outside_parent_band', seam
    return 'candidate', seam


def run(rows, parent_runs, spec_hash, source_hash):
    for slug in PARENTS:
        if slug not in parent_runs or parent_runs[slug]['source_hash'] != source_hash:
            raise ValueError(f'Отсутствует или устарел родительский журнал {slug}')
    aggregate = Aggregator(5)
    bars = {}
    for row in rows:
        bar = aggregate.add(row)
        if bar:
            bars[bar['time']] = bar

    events = []
    for slug, (model, direction) in PARENTS.items():
        parent_run = parent_runs[slug]
        levels = {x['id']: x for x in parent_run['levels']}
        for event in parent_run['events']:
            if event['state'] != 'broken':
                continue
            parent = levels[event['level_id']]
            if parent['known_at'] > event['time']:
                raise ValueError('Родительский уровень стал известен после пробоя')
            second = bars.get(event['bar']['time'])
            if second is None or second['end'] != event['time'] or second['close'] != event['bar']['close']:
                raise ValueError('Родительский пробой не совпадает с архивом M5')
            first = bars.get(second['time']-300)
            state, seam = classify(parent, first, second, direction)
            record = {'id': hashlib.sha256(f"{VERSION}|{event['id']}".encode()).hexdigest()[:24],
                      'time': event['time'], 'model': model, 'direction': direction,
                      'state': state, 'parent_model': slug.upper(),
                      'parent_level_id': parent['id'], 'parent_break_event_id': event['id'],
                      'parent_spec_hash': parent_run['spec_hash'],
                      'parent_band': [parent['low_band'], parent['high_band']],
                      'first_m5': first, 'second_m5': second, 'body_seam': seam,
                      'version': VERSION, 'spec_hash': spec_hash, 'source_hash': source_hash,
                      'execution_mode': 'historical_auxiliary_observation',
                      'source_supported': 'between_two_candles_with_parent_zone',
                      'research_assumption': 'adjacent_same_direction_m5_body_seam'}
            events.append(record)
    states = Counter(x['state'] for x in events)
    by_model = Counter(x['model'] for x in events if x['state'] == 'candidate')
    return {'version': VERSION, 'spec_hash': spec_hash, 'source_hash': source_hash,
            'parent_spec_hashes': {slug: parent_runs[slug]['spec_hash'] for slug in PARENTS},
            'events': events,
            'summary': {'parent_breaks': len(events), 'states': dict(states),
                        'candidates_by_model': dict(by_model), 'paper_trades': 0}}
