"""Persist fresh SBR observations from the read-only MT5 bridge."""
import hashlib
import json
import os
from collections import deque
from .forward_memory import prune_finished_levels
from .journal_keys import JournalKeys
from .journal_outbox import ReplayPending, remember, reconcile

from .datafeed import ROOT
from .notifications import AlertStore, public_settings
from .analysis_plans import snr_break_plan, snr_terminal_reason
from .scenario_lifecycle import plan as scenario_plan, confirmed as scenario_confirmed, ended as scenario_ended
from .snr_sbr import SBRResearch, VERSION


class SBRForward:
    def __init__(self, symbol, source_identity, startup, journal=None, alert_store=None,
                 price_step=0.01):
        spec_hash=hashlib.sha256((ROOT/'docs'/'SNR_SBR_SPEC.md').read_bytes()).hexdigest()
        source_hash=hashlib.sha256(source_identity.encode()).hexdigest()
        self.model=SBRResearch(spec_hash,source_hash,symbol=symbol,
                               source='MT5 · '+symbol,signals_only=True)
        self.source_hash=source_hash
        self.identity={'source_hash':source_hash,'spec_hash':spec_hash,
                       'version':VERSION,'symbol':symbol,'source':'MT5 · '+symbol}
        self.symbol,self.startup=symbol,startup
        self.price_step=price_step
        self.journal=journal or ROOT/'results'/'snr_sbr_forward_events.jsonl'
        self.alert_store=alert_store if alert_store is not None else AlertStore()
        self.journal.parent.mkdir(exist_ok=True)
        self.recent_events = deque(maxlen=50)
        if self.journal.exists():
            with self.journal.open(encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        saved=json.loads(line)
                        if any(saved.get(name)!=value for name,value in self.identity.items()):
                            raise ValueError('Forward-журнал SBR относится к другому источнику, символу или версии правил')
                        self.recent_events.append(saved)
        else:
            self.journal.touch()
        self.seen=JournalKeys(self.journal)
        self.pending_delivery=ReplayPending(self.journal)
        self.primed=False
        self.new_events=0
        self._retry_observation=None
        self._index_dirty=False

    def ingest(self,row,received_at,quote_time):
        self._ingest(row,received_at,quote_time)
        prune_finished_levels(self.model)
        # The live journal owns persisted history; bootstrap events are transient.
        # Preserve the buffer if processing or durable delivery handoff fails.
        self.model.events.clear()

    def _ingest(self,row,received_at,quote_time):
        if self.pending_delivery:reconcile(self.pending_delivery,self.alert_store,received_at,public_settings()['enabled'])
        if self._retry_observation is not None:
            original_received,original_quote=self._retry_observation
            self._write_events(original_received,original_quote)
            self.model.events.clear()
            self._retry_observation=None
        self.model.step(row)
        if not self.primed:return
        try:
            self._write_events(received_at,quote_time)
        except Exception:
            self._retry_observation=(received_at,quote_time)
            raise

    def _write_events(self,received_at,quote_time):
        if self._index_dirty:
            self.seen=JournalKeys(self.journal)
            self._index_dirty=False
        fresh=[e for e in self.model.events if e['time']>=self.startup and self.dedup_key(e) not in self.seen]
        if not fresh:return
        with self.journal.open('a',encoding='utf-8') as f:
            for event in fresh:
                quality='late_bar' if received_at-event['time']>90 else 'stale_quote' if received_at-quote_time>90 or quote_time<event['time'] else 'timely'
                row=dict(event,**self.identity,received_at=received_at,quote_time=quote_time,observation_quality=quality,
                         execution_mode='live_signal_only',result_status='not_evaluated',
                         dedup_key=self.dedup_key(event))
                if event['state']=='broken' and quality=='timely':
                    level=self.model.levels[event['level_id']]
                    plan=snr_break_plan(level,'sell',event['time'],self.price_step)
                    if plan:
                        row['delivery_alert']={'id':'sbr-plan-'+row['dedup_key'],
                            'time':event['time'],'tf':'M5','type':'sbr_sell_plan',
                            'cooldown_key':'sbr_sell_plan:'+event['level_id'],
                            'label':'SBR SELL · план ретеста',
                            'direction':'sell','symbol':self.symbol,'source':row['source'],
                            'source_hash':self.source_hash,
                            'version':VERSION,'level_id':event['level_id'],
                            'analysis_plan':plan,
                            'scenario_lifecycle':scenario_plan('SBR',event['time']),
                            'reason':'Закрытая M5 пробила бывшую поддержку; возврат к полосе ещё не подтверждён.'}
                        row['delivery_enabled']=public_settings()['enabled']
                if event['state']=='signal_ready' and quality=='timely':
                    level=self.model.levels[event['level_id']]
                    alert={'id':'sbr-'+row['dedup_key'],'time':event['time'],'tf':'M5',
                           'type':'sbr_sell_confirmed','label':'SBR SELL · подтверждённый возврат',
                           'direction':'sell','symbol':self.symbol,'source':row['source'],
                           'source_hash':self.source_hash,
                           'version':VERSION,'level_id':event['level_id'],
                           'price':level['first_touch']['close'],'level':level['level'],
                           'stop':event['stop'],'target':event['target'],
                           'scenario_lifecycle':scenario_confirmed(event['time']),
                           'reason':'Закрытая M5 после первого возврата под бывшую поддержку. Правила численного отбора — исследовательские допущения.'}
                    row['delivery_alert']=alert
                    row['delivery_enabled']=public_settings()['enabled']
                terminal=snr_terminal_reason(event)
                if terminal:
                    row['delivery_alert']={'id':'sbr-end-'+row['dedup_key'],
                        'time':event['time'],'tf':'M5','type':'sbr_sell_closed',
                        'cooldown_key':'sbr_sell_closed:'+event['level_id'],
                        'symbol':self.symbol,'source':row['source'],'source_hash':self.source_hash,
                        'level_id':event['level_id'],
                        'reason':terminal,
                        'scenario_lifecycle':scenario_ended(event['time'])}
                    row['delivery_enabled']=(quality=='timely' and public_settings()['enabled'] and
                        self.alert_store.plan_delivery_status('sbr_sell_plan:'+event['level_id'],
                            self.source_hash,self.symbol) in ('sent','sending'))
                    if quality!='timely':row['delivery_local_only']=True
                f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())
                self.recent_events.append(row)
                remember(self.pending_delivery,row)
                self.new_events+=1
                try:self.seen.add(row['dedup_key'])
                except Exception:
                    self._index_dirty=True
                    raise
                if self.pending_delivery:reconcile(self.pending_delivery,self.alert_store,received_at,public_settings()['enabled'])

    def dedup_key(self,event):
        return hashlib.sha256((self.source_hash+'|'+event['id']).encode()).hexdigest()

    def finish_bootstrap(self):
        self.primed=True

    def state(self,now,quote_time):
        if self.pending_delivery:reconcile(self.pending_delivery,self.alert_store,now,public_settings()['enabled'])
        recent = list(self.recent_events)
        m=self.model
        return {'version':VERSION,'symbol':self.symbol,'source':'MT5 · '+self.symbol,
                'mode':'live_signal_only','generated_at':now,'quote_time':quote_time,
                'last_m1':m.last,'forward_since':self.startup,
                'h1_bars':len(m.bars[60]),'m15_bars':len(m.bars[15]),
                'ready':len(m.bars[60])>=240 and len(m.bars[15])>=120,
                'stale':now-quote_time>90,'forward_events_total':len(self.seen),
                'forward_events_this_run':self.new_events,'events':recent}
