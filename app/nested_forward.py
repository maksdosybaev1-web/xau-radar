"""Read-only forward journal for the nested observation and two entry candidates."""
import hashlib
import json
import os
from collections import deque

from .datafeed import ROOT
from .entry_modes import VERSION as MODES_VERSION, classify_first_return
from .entry_modes_outcomes import evaluate
from .engine import atr
from .journal_keys import JournalKeys
from .nested_m15_m5_m1 import NestedObserver, VERSION as NESTED_VERSION


class NestedForward:
    def __init__(self, symbol, source_identity, startup, journal=None):
        self.symbol = symbol
        self.source_hash = hashlib.sha256(source_identity.encode()).hexdigest()
        self.nested_spec_hash = hashlib.sha256((ROOT/'docs'/'NESTED_M15_M5_M1_SPEC.md').read_bytes()).hexdigest()
        self.modes_spec_hash = hashlib.sha256((ROOT/'docs'/'ENTRY_MODES_SPEC.md').read_bytes()).hexdigest()
        self.model = NestedObserver(self.nested_spec_hash, self.source_hash,
                                    symbol=symbol, source='MT5 · '+symbol)
        self.journal = journal or ROOT/'results'/'nested_forward_events.jsonl'
        self.journal.parent.mkdir(exist_ok=True)
        self.outcomes_journal = self.journal.with_name(
            self.journal.stem.replace('_events', '')+'_outcomes.jsonl')
        self.tick_checks_journal = self.journal.with_name(
            self.journal.stem.replace('_events', '')+'_tick_checks.jsonl')
        self.outcome_spec_hash = hashlib.sha256((ROOT/'docs'/'ENTRY_MODES_OUTCOME_SPEC.md').read_bytes()).hexdigest()
        self.meta = self.journal.with_suffix('.meta.json')
        if self.meta.exists():
            metadata = json.loads(self.meta.read_text(encoding='utf-8'))
            if metadata['source_hash'] != self.source_hash:
                raise ValueError('Источник MT5 отличается от источника forward-журнала')
            self.forward_since = metadata['forward_since']
        else:
            self.forward_since = startup
            self.meta.write_text(json.dumps({'source_hash':self.source_hash,
                                             'forward_since':startup}), encoding='utf-8')
        self.recent = deque(maxlen=50)
        self.tick_pending = {}
        if self.journal.exists():
            with self.journal.open(encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        self.recent.append(record)
                        if record['kind']=='nested_event' and record['type']=='touch_observed':
                            self.tick_pending[record['dedup_key']] = record
        else:
            self.journal.touch()
        self.seen = JournalKeys(self.journal)
        self.pending = None
        self.new_events = 0
        self.rows, self.by_time, self.atr_by_end = [], {}, {}
        self.atr_history = deque(maxlen=15)
        self.pending_outcomes = {}
        self.outcomes_recent = deque(maxlen=50)
        if self.outcomes_journal.exists():
            with self.outcomes_journal.open(encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        result = json.loads(line)
                        self.outcomes_recent.append(result)
        else:
            self.outcomes_journal.touch()
        self.finalized = JournalKeys(self.outcomes_journal, 'candidate_id')
        self.new_outcomes = 0
        self.tick_checks_recent = deque(maxlen=50)
        if self.tick_checks_journal.exists():
            with self.tick_checks_journal.open(encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        check = json.loads(line)
                        self.tick_checks_recent.append(check)
                        self.tick_pending.pop(check['touch_key'], None)
        else:
            self.tick_checks_journal.touch()
        self.tick_checked = JournalKeys(self.tick_checks_journal, 'touch_key')
        self.new_tick_checks = 0
        self._unindexed_events = set()
        self._unindexed_outcomes = set()
        self._unindexed_ticks = set()

    def _ensure_indexes(self):
        if self._unindexed_events:
            self.seen = JournalKeys(self.journal)
            self._unindexed_events.clear()
        if self._unindexed_outcomes:
            self.finalized = JournalKeys(self.outcomes_journal, 'candidate_id')
            self._unindexed_outcomes.clear()
        if self._unindexed_ticks:
            self.tick_checked = JournalKeys(self.tick_checks_journal, 'touch_key')
            self._unindexed_ticks.clear()

    def key(self, kind, event):
        identity = event.get('chain_id') or event.get('zone_id') or event['id']
        return hashlib.sha256(f'{self.source_hash}|{kind}|{event["type"]}|{event["time"]}|{identity}'.encode()).hexdigest()

    def quality(self, event_time, received_at, quote_time):
        if received_at-event_time > 90:
            return 'late_bar'
        if received_at-quote_time > 90 or quote_time < event_time:
            return 'stale_quote'
        return 'timely'

    def save(self, kind, event, event_time, received_at, quote_time, **values):
        if event_time < self.forward_since:
            return
        key = self.key(kind, event)
        if key in self._unindexed_events or key in self.seen:
            return
        record = dict(event, kind=kind, received_at=received_at, quote_time=quote_time,
                      observation_quality=self.quality(event_time, received_at, quote_time),
                      result_status='not_evaluated', dedup_key=key, **values)
        if kind=='nested_event' and event['type']=='touch_observed':
            chain = next(c for c in self.model.chains.values() if c['id']==event['chain_id'])
            record['inner_zone'] = chain['inner']
        with self.journal.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
            f.flush();os.fsync(f.fileno())
        self.recent.append(record)
        self.new_events += 1
        if kind=='nested_event' and event['type']=='touch_observed' and key not in self.tick_checked:
            self.tick_pending[key] = record
        try:self.seen.add(key)
        except Exception:self._unindexed_events.add(key)

    def check_ticks(self, archive, now):
        self._ensure_indexes()
        for key, touch in list(self.tick_pending.items()):
            if key in self.tick_checked:
                del self.tick_pending[key]
                continue
            start = touch['m1_time']
            inspection = archive.inspect_zone(self.symbol, start, start+60,
                                              touch['direction'], *touch['inner_zone'])
            coverage = inspection['coverage']['status']
            zone_quote = inspection['zone_quote']
            threshold_quote = inspection['limit_threshold_quote']
            if not (zone_quote or threshold_quote or coverage=='queried_unverified'
                    or now-touch['time']>=90):
                continue
            status = ('zone_quote_observed' if zone_quote else
                      'limit_threshold_quote_observed' if threshold_quote else
                      'no_threshold_quote_in_queried_ticks' if coverage=='queried_unverified' else
                      'insufficient_tick_coverage')
            check = {'touch_key':key, 'chain_id':touch['chain_id'],
                     'symbol':self.symbol, 'm1_time':start, 'checked_at':now,
                     'touch_observation_quality':touch['observation_quality'],
                     'status':status, **inspection}
            with self.tick_checks_journal.open('a', encoding='utf-8') as f:
                f.write(json.dumps(check, ensure_ascii=False, allow_nan=False)+'\n')
                f.flush();os.fsync(f.fileno())
            self.tick_checks_recent.append(check)
            self.new_tick_checks += 1
            del self.tick_pending[key]
            try:self.tick_checked.add(key)
            except Exception:self._unindexed_ticks.add(key)

    def mode_event(self, touch, chain, mode, values, known_at, received_at, quote_time):
        candidate_id = hashlib.sha256(f'{MODES_VERSION}|{touch["id"]}|{mode}'.encode()).hexdigest()[:24]
        event = {'id':candidate_id, 'type':mode, 'time':touch['time'],
                 'chain_id':chain['id'], 'first_return_m1_time':touch['m1_time'],
                 'direction':chain['direction'], 'inner_zone':chain['inner'],
                 'symbol':self.symbol, 'source':'MT5 · '+self.symbol,
                 'version':MODES_VERSION, 'nested_version':NESTED_VERSION,
                 'spec_hash':self.modes_spec_hash, 'nested_spec_hash':self.nested_spec_hash,
                 'source_hash':self.source_hash, 'mode':mode,
                 'candidate_observation_quality':self.quality(known_at, received_at, quote_time),
                 **values}
        self.save('entry_candidate', event, known_at, received_at, quote_time,
                  execution_mode='forward_candidate_not_broker_fill',
                  quote_quality='estimated_ask_ohlc' if chain['direction']=='buy' else 'bid_ohlc',
                  intraminute_order_unknown=True)
        if candidate_id not in self.finalized:
            self.pending_outcomes[candidate_id] = (event, chain)

    def evaluate_pending(self, received_at, quote_time):
        for candidate_id, (observation, chain) in list(self.pending_outcomes.items()):
            if candidate_id in self._unindexed_outcomes or candidate_id in self.finalized:
                del self.pending_outcomes[candidate_id]
                continue
            result = evaluate(observation, chain, self.rows, self.by_time,
                              self.atr_by_end, self.outcome_spec_hash, self.source_hash)
            if result['state']=='incomplete' and result['reason']=='end_of_data':
                continue
            result.update(execution='forward_m1_paper_assumption_not_broker_fill',
                          evaluated_at=received_at, quote_time=quote_time,
                          candidate_observation_quality=observation['candidate_observation_quality'],
                          ask_ohlc_quality='estimated_from_bid_and_m1_spread',
                          broker_fill_verified=False)
            with self.outcomes_journal.open('a', encoding='utf-8') as f:
                f.write(json.dumps(result, ensure_ascii=False, allow_nan=False)+'\n')
                f.flush();os.fsync(f.fileno())
            self.outcomes_recent.append(result)
            self.new_outcomes += 1
            del self.pending_outcomes[candidate_id]
            try:self.finalized.add(candidate_id)
            except Exception:self._unindexed_outcomes.add(candidate_id)

    def ingest(self, row, received_at, quote_time):
        self._ensure_indexes()
        if self.rows and row['time']-self.rows[-1]['time'] != 60:
            self.atr_history.clear()
        self.by_time[row['time']] = len(self.rows)
        self.rows.append(row)
        self.atr_history.append({'time':row['time'], 'end':row['time']+60,
                                 'open':row['bid_open'], 'high':row['bid_high'],
                                 'low':row['bid_low'], 'close':row['bid_close']})
        value = atr(self.atr_history, 14)
        if value is not None:
            self.atr_by_end[row['time']+60] = value
        if self.pending is not None:
            touch, chain, touch_row = self.pending
            next_row = row if row['time'] == touch_row['time']+60 else None
            _, close = classify_first_return(chain, touch_row, next_row)
            self.mode_event(touch, chain, 'after_close', close,
                            row['time']+60 if next_row else row['time'], received_at, quote_time)
            self.pending = None
        before = len(self.model.events)
        self.model.step(row)
        for event in self.model.events[before:]:
            self.save('nested_event', event, event['time'], received_at, quote_time,
                      execution_mode='forward_observation_only',
                      quote_quality='estimated_ask_ohlc' if event.get('direction')=='buy' else 'bid_ohlc')
            if event['type'] != 'touch_observed' or event['time'] < self.forward_since:
                continue
            chain = next(c for c in self.model.chains.values() if c['id']==event['chain_id'])
            limit, close = classify_first_return(chain, row, None)
            self.mode_event(event, chain, 'touch_limit', limit, event['time'], received_at, quote_time)
            if close['state'] == 'first_return_not_confirmed':
                self.mode_event(event, chain, 'after_close', close, event['time'], received_at, quote_time)
            else:
                self.pending = (event, chain, row)
        self.evaluate_pending(received_at, quote_time)
        self.model.events.clear()
        live_inner_ids = {zone['id'] for zone in self.model.zones[1]}
        self.model.chains = {inner_id:chain for inner_id,chain in self.model.chains.items()
                             if inner_id in live_inner_ids or chain['id'] in self.model.active}
        if len(self.rows) > 600:
            self.rows = self.rows[-300:]
            self.by_time = {saved['time']:index for index,saved in enumerate(self.rows)}
            oldest = self.rows[0]['time']
            self.atr_by_end = {end:value for end,value in self.atr_by_end.items() if end >= oldest}

    def state(self, now, quote_time):
        self._ensure_indexes()
        return {'version':NESTED_VERSION, 'modes_version':MODES_VERSION,
                'symbol':self.symbol, 'source':'MT5 · '+self.symbol,
                'mode':'read_only_forward_observation', 'generated_at':now,
                'quote_time':quote_time, 'last_m1':self.model.last,
                'forward_since':self.forward_since,
                'm15_bars':len(self.model.bars[15]), 'm5_bars':len(self.model.bars[5]),
                'ready':len(self.model.bars[15])>=5 and len(self.model.bars[5])>=3,
                'stale':now-quote_time>90, 'forward_events_total':len(self.seen),
                'forward_events_this_run':self.new_events,
                'pending_next_m1':self.pending is not None,
                'ask_ohlc_quality':'estimated_from_bid_and_m1_spread',
                'broker_fill_verified':False, 'telegram_alerts_enabled':False,
                'paper_outcomes_total':len(self.finalized),
                'paper_outcomes_this_run':self.new_outcomes,
                'paper_outcomes_pending':len(self.pending_outcomes),
                'paper_outcomes_recent':list(self.outcomes_recent),
                'tick_checks_total':len(self.tick_checked),
                'tick_checks_this_run':self.new_tick_checks,
                'tick_checks_pending':len(self.tick_pending),
                'tick_checks_recent':list(self.tick_checks_recent),
                'events':list(self.recent)}
