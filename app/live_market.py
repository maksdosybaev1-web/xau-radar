"""Read-only multi-timeframe MT5 observations and fresh alert ingestion."""
import time,hashlib
from collections import deque
from .datafeed import ROOT
from .market_radar import CFG, TimeframeRadar
from .notifications import AlertStore
from .research import save_json
from .quote_archive import QuoteArchive


class LiveMarket:
    def __init__(self,symbol):
        self.symbol=symbol;self.startup=int(time.time());self.models={tf:TimeframeRadar(tf) for tf in CFG['timeframes']}
        self.frames={tf:deque(maxlen=600) for tf in self.models};self.store=AlertStore()
        self.near_keys=None
        self.quote_archive=QuoteArchive()

    def quote_alerts(self,quote):
        active=set();events=[]
        for tf,observations in self.frames.items():
            if not observations:continue
            s=observations[-1]
            if not s['ready'] or not s['atr']:continue
            for side in ('support','resistance'):
                level=s[side]
                if not level or abs(quote['bid']-level['price'])>CFG['near_atr']*s['atr']:continue
                key=(tf,side,round(level['price'],3));active.add(key)
                if self.near_keys is None or key in self.near_keys:continue
                identity=f"{CFG['version']}|{tf}|quote|{quote['time']}|{side}|{level['price']}"
                label='Живая цена около '+('поддержки' if side=='support' else 'сопротивления')
                events.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:24],'time':quote['time'],'tf':tf,
                    'type':'quote_near','label':label,'direction':s['trend'],'price':quote['bid'],'price_kind':'bid',
                    'score':s['score'],'rsi':s['rsi'],'ema20':s['ema20'],'ema50':s['ema50'],'atr':s['atr'],
                    'support':s['support']['price'] if s['support'] else None,
                    'resistance':s['resistance']['price'] if s['resistance'] else None,
                    'reason':f"Bid приблизилась к уровню {level['price']:.2f} на расстояние не более 0,25 ATR. Индикаторы рассчитаны по закрытой {tf}.",
                    'version':CFG['version']})
        self.near_keys=active
        return events

    def update(self,mt5,tick,now):
        # Indicator observations belong to the dashboard, not trading-plan Telegram alerts.
        enabled=False
        for tf,model in self.models.items():
            seconds=CFG['timeframes'][tf]*60;primed=model.last is not None
            count=600 if not primed else min(600,max(5,(now-model.last)//seconds+3))
            rates=mt5.copy_rates_from_pos(self.symbol,getattr(mt5,'TIMEFRAME_'+tf),1,count)
            if rates is None or not len(rates):raise RuntimeError('MT5 не вернул свечи '+tf)
            for row in rates:
                start=int(row['time']);end=start+seconds
                if end>now or model.last is not None and end<=model.last:continue
                bar={key:float(row[key]) for key in ('open','high','low','close')}
                bar.update(time=start,end=end,volume=float(row['tick_volume']))
                snapshot,events=model.add(bar);self.frames[tf].append(snapshot)
                if primed:
                    for event in events:
                        if event['time']>=self.startup:self.store.add(event,now,int(tick.time),enabled)
        quote={'bid':float(tick.bid),'ask':float(tick.ask),'spread':float(tick.ask-tick.bid),'time':int(tick.time)}
        quote['time_msc']=int(getattr(tick,'time_msc',0) or int(tick.time)*1000)
        self.quote_archive.add(self.symbol,quote,now)
        for event in self.quote_alerts(quote):
            if event['time']>=self.startup:self.store.add(event,now,int(tick.time),enabled)
        out={'version':CFG['version'],'frames':{tf:list(rows) for tf,rows in self.frames.items()},
             'events':sorted(self.store.recent(100),key=lambda e:e['time']),
             'generated_at':now,'heartbeat':min(now,int(tick.time)),'forward_since':self.startup,
             'quote':quote,
             'source':'MT5 · '+self.symbol,'volume_kind':'Тиковая активность брокера, не биржевой объём'}
        save_json(ROOT/'results'/'technical_live.json',out)
        return out['quote']
