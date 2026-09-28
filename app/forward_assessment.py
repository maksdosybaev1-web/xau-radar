"""FVG forward evidence from the live snapshot and durable event journal."""
import json
import time
from collections import Counter
from pathlib import Path

from .datafeed import ROOT
from .market_radar import CFG


def snapshot(root=ROOT,now=None):
    now=int(time.time() if now is None else now)
    root=Path(root)
    path=root/'results'/'live.json'
    if not path.exists():
        return {'status':'not_started','forward_since':None,'new_m1':0,
                'events':{},'timely_confirmed':0,'paper_closed':0,
                'paper_open':0,'paper_data_gap':0,'net_pnl':None,'average_r':None}
    live=json.loads(path.read_text(encoding='utf-8'))
    if live.get('mode')!='live' or live.get('forward_since') is None:
        raise ValueError('Живой снимок FVG не содержит границу forward-наблюдения')
    since=int(live['forward_since'])
    journal=root/'results'/'forward_events.jsonl'
    counts=Counter()
    confirmed=set()
    timely_near=0
    if journal.exists():
        with journal.open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():continue
                record=json.loads(line)
                if record['time']<since:continue
                if record.get('config_hash')!=live['config_hash']:
                    raise ValueError('Forward-журнал FVG относится к другой версии правил')
                counts[record.get('observation_quality','unknown')]+=1
                if record.get('observation_quality')=='timely':
                    if record['type']=='near':timely_near+=1
                    elif record['type']=='confirmed':confirmed.add((record.get('zone_id'),record['time']))
    paper=[trade for trade in live.get('trades',[]) if
           (trade.get('zone_id'),trade.get('signal_time')) in confirmed]
    closed=[trade for trade in paper if trade.get('exit_time') is not None and not trade.get('data_gap')]
    quote_time=live.get('quote_time')
    quote_fresh=quote_time is not None and 0<=now-quote_time<=CFG['max_quote_age_seconds']
    return {'status':'collecting' if quote_fresh else 'waiting_for_fresh_quote',
            'forward_since':since,'last_m1':live.get('source',{}).get('last'),
            'quote_time':quote_time,'quote_fresh':quote_fresh,
            'new_m1':int(live.get('curve_archived_forward_count',0))+sum(item['time']>=since for item in live.get('curve',[])),
            'events':dict(counts),'timely_near':timely_near,
            'timely_confirmed':len(confirmed),'paper_closed':len(closed),
            'paper_open':sum(trade.get('exit_time') is None for trade in paper),
            'paper_data_gap':sum(bool(trade.get('data_gap')) for trade in paper),
            'net_pnl':sum(trade['pnl'] for trade in closed) if closed else None,
            'average_r':sum(trade['r'] for trade in closed)/len(closed) if closed else None,
            'note':'Условные исходы по MT5 M1 с оценённым ask; это не исполнение брокера и не доказательство преимущества.'}
