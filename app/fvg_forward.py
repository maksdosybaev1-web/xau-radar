"""Durable forward journal and optional alerts for fresh FVG zone events."""
import hashlib
import json
import os
from pathlib import Path

from .datafeed import ROOT
from .journal_outbox import ReplayPending, remember, reconcile
from .journal_keys import JournalKeys
from .market_radar import CFG
from .notifications import AlertStore, public_settings
from .analysis_plans import fvg_near_plan
from .scenario_lifecycle import plan as scenario_plan, confirmed as scenario_confirmed, ended as scenario_ended


class FVGForward:
    def __init__(self, symbol, source_identity, config_hash, forward_since, journal=None, alert_store=None,
                 price_step=0.01):
        self.symbol=symbol
        self.price_step=price_step
        self.source_hash=hashlib.sha256(source_identity.encode()).hexdigest()
        self.config_hash=config_hash
        self.forward_since=forward_since
        self.journal=Path(journal) if journal is not None else ROOT/'results'/'forward_events.jsonl'
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        self.alert_store=alert_store if alert_store is not None else AlertStore()
        if not self.journal.exists():self.journal.touch()
        self._index_dirty=False
        self._rebuild_index()
        self.pending_delivery=ReplayPending(self.journal)

    def key(self,event):
        fields=['fvg-zone-v2',self.symbol,self.config_hash,event.get('zone_id'),
                event['time'],event['type'],event.get('low'),event.get('high')]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()

    def legacy_key(self,event):
        fields=[self.symbol,self.config_hash,event['time'],event['type'],event.get('low'),event.get('high')]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()

    def _rebuild_index(self):
        # JSONL is authoritative. Restore aliases for genuine pre-v2 records only.
        with self.journal.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip():continue
                saved=json.loads(line)
                if saved.get('source_hash',self.source_hash)!=self.source_hash or saved.get('config_hash')!=self.config_hash:
                    raise ValueError('Forward-журнал FVG относится к другому источнику или версии правил')
        index=JournalKeys(self.journal)
        with self.journal.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip():continue
                saved=json.loads(line)
                if saved.get('key_version') is None and saved.get('zone_id') is not None and saved.get('dedup_key')==self.legacy_key(saved):
                    index.add(self.key(saved))
        self.seen=index
        self._index_dirty=False

    def flush_pending(self,now):
        if self.pending_delivery:
            reconcile(self.pending_delivery,self.alert_store,now,public_settings()['enabled'])

    def early_plan(self, event, bid, ask):
        return fvg_near_plan(event, bid, ask, self.price_step)

    def persist(self,events,received_at,quote_time,quote_bid=None,quote_ask=None):
        if self._index_dirty:self._rebuild_index()
        self.flush_pending(received_at)
        resolved={(e.get('zone_id'),e['time']) for e in events
                  if e['type'] in ('confirmed','cancelled')}
        for event in events:
            if event['time']<self.forward_since:continue
            key=self.key(event)
            if key in self.seen:continue
            max_age=CFG['max_quote_age_seconds']
            quality=('late_bar' if received_at-event['time']>max_age else
                     'stale_quote' if received_at-quote_time>max_age or quote_time<event['time'] else 'timely')
            saved=dict(event,dedup_key=key,received_at=received_at,quote_time=quote_time,
                       observation_quality=quality,source_hash=self.source_hash,config_hash=self.config_hash,
                       key_version=2)
            if event['type'] in ('near','confirmed','cancelled') and (quality=='timely' or event['type']=='cancelled'):
                kind='fvg_'+event['type']
                plan=(self.early_plan(event,quote_bid,quote_ask)
                      if kind=='fvg_near' and (event.get('zone_id'),event['time']) not in resolved
                      else None)
                labels={'near':'FVG · цена приближается к зоне',
                        'confirmed':'FVG · условия выполнены','cancelled':'FVG · сценарий отменён'}
                alert={'id':'fvg-'+key,'time':event['time'],'tf':'M5','type':kind,
                       'cooldown_key':kind+':'+str(event.get('zone_id','')),
                       'label':labels[event['type']],
                       'symbol':self.symbol,'source':'MT5 · '+self.symbol,'direction':event.get('direction'),
                       'source_hash':self.source_hash,
                       'zone_id':event.get('zone_id'),'low':event.get('low'),'high':event.get('high'),
                       'child_low':event.get('child_low'),'child_high':event.get('child_high'),
                       'reason':event.get('reason',''),'stop':event.get('stop'),
                       'analysis_plan':plan,
                       'scenario_lifecycle':(scenario_plan('FVG',event['time']) if kind=='fvg_near'
                                             else scenario_confirmed(event['time']) if kind=='fvg_confirmed'
                                             else scenario_ended(event['time']))}
                saved['delivery_alert']=alert
                saved['delivery_enabled']=quality=='timely' and public_settings()['enabled']
                if quality!='timely':saved['delivery_local_only']=True
            with self.journal.open('a',encoding='utf-8') as f:
                f.write(json.dumps(saved,ensure_ascii=False,allow_nan=False)+'\n')
                f.flush();os.fsync(f.fileno())
            remember(self.pending_delivery,saved)
            try:self.seen.add(key)
            except Exception:
                self._index_dirty=True
                raise
            self.flush_pending(received_at)
