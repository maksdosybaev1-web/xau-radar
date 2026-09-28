"""Causal nested-FVG research engine. Closed candles only; simulated orders only."""
import copy, datetime as dt, hashlib, json, math, statistics
from collections import deque

def iso(t): return dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()

class Aggregator:
    def __init__(self, minutes): self.minutes=minutes;self.bucket=None;self.rows=[]
    def add(self, row):
        bucket=row['time']//(self.minutes*60)*(self.minutes*60)
        if bucket!=self.bucket: self.bucket=bucket;self.rows=[]
        self.rows.append(row)
        if row['time']+60 != bucket+self.minutes*60: return None
        if len(self.rows)!=self.minutes or self.rows[0]['time']!=bucket: return None
        r=self.rows
        return {'time':bucket,'end':bucket+self.minutes*60,'open':r[0]['bid_open'],
                'high':max(x['bid_high'] for x in r),'low':min(x['bid_low'] for x in r),
                'close':r[-1]['bid_close'],'volume':sum(x['volume'] for x in r)}

def atr(bars, n=14):
    if len(bars)<n+1:return None
    b=list(bars)[-n-1:]
    return statistics.mean(max(y['high']-y['low'],abs(y['high']-x['close']),abs(y['low']-x['close'])) for x,y in zip(b,b[1:]))

def fvg(bars, cfg):
    if len(bars)<15:return None
    a,b,c=list(bars)[-3:];v=atr(bars)
    if a['end']!=b['time'] or b['end']!=c['time']:return None
    if not v or abs(b['close']-b['open'])<cfg['min_impulse_atr']*v:return None
    if a['high']<c['low'] and b['close']>b['open']:
        direction,lo,hi='buy',a['high'],c['low']
    elif c['high']<a['low'] and b['close']<b['open']:
        direction,lo,hi='sell',c['high'],a['low']
    else:return None
    if hi-lo<cfg['min_gap_atr']*v:return None
    return {'direction':direction,'low':lo,'high':hi,'created':c['end'],'origin':a['time'],'touched':False}

def trend(bars):
    # A pivot is knowable only after TWO closed bars on its right.
    b=list(bars);highs=[];lows=[]
    for i in range(2,len(b)-2):
        window=b[i-2:i]+b[i+1:i+3]
        if all(b[i]['high']>x['high'] for x in window):highs.append(b[i]['high'])
        if all(b[i]['low']<x['low'] for x in window):lows.append(b[i]['low'])
    if len(highs)<2 or len(lows)<2:return 'neutral'
    if highs[-1]>highs[-2] and lows[-1]>lows[-2]:return 'buy'
    if highs[-1]<highs[-2] and lows[-1]<lows[-2]:return 'sell'
    return 'neutral'

def exit_price(trade, bar, cfg):
    """Executable side OHLC; stops win ties; adverse gaps filled at open."""
    side='bid' if trade['direction']=='buy' else 'ask'
    o,h,l,c=(bar[side+'_'+x] for x in ('open','high','low','close'))
    stop,target=trade['stop'],trade['target'];buy=trade['direction']=='buy'
    hit_stop=l<=stop if buy else h>=stop
    hit_target=h>=target if buy else l<=target
    slip=cfg['slippage_per_oz_side']
    if hit_stop:
        p=min(o,stop) if buy else max(o,stop)
        return p-slip if buy else p+slip, 'stop_ambiguous' if hit_target else 'stop',bar['time']+60
    if hit_target:
        return target-slip if buy else target+slip,'target',bar['time']+60
    if bar['time']+60-trade['entry_time']>=cfg['max_holding_minutes']*60:
        return c-slip if buy else c+slip,'timeout',bar['time']+60
    if (bar['time']+60)%86400>=cfg['flatten_hour_utc']*3600:
        return c-slip if buy else c+slip,'session_end',bar['time']+60
    return None

def summarize(trades, initial=10000, curve=None):
    closed=[x for x in trades if x.get('exit_time') is not None]
    wins=[x for x in closed if x['pnl']>0];losses=[x for x in closed if x['pnl']<0]
    gross_win=sum(x['pnl'] for x in wins);gross_loss=-sum(x['pnl'] for x in losses)
    eq=initial;peak=initial;dd=0
    for t in sorted(closed,key=lambda x:x['exit_time']):
        eq+=t['pnl'];peak=max(peak,eq);dd=max(dd,(peak-eq)/peak if peak>0 else 0)
    mdd=dd
    if curve:
        peak=initial;mdd=0
        for point in curve:
            peak=max(peak,point['equity']);mdd=max(mdd,(peak-point['equity'])/peak if peak>0 else 0)
    n=len(closed);p=len(wins)/n if n else None
    interval=None
    if n:
        z=1.96;den=1+z*z/n;center=(p+z*z/(2*n))/den
        half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den;interval=[max(0,center-half),min(1,center+half)]
    return {'closed':n,'wins':len(wins),'losses':len(losses),'net_pnl':sum(t['pnl'] for t in closed),
            'win_rate':p,'win_rate_wilson95':interval,'average_pnl':statistics.mean(t['pnl'] for t in closed) if n else None,
            'average_r':statistics.mean(t['r'] for t in closed) if n else None,
            'profit_factor':gross_win/gross_loss if gross_loss else None,
            'max_closed_drawdown':dd,'max_marked_drawdown':mdd,
            'stop_fraction':sum(t['exit_reason'].startswith('stop') for t in closed)/n if n else None,
            'ambiguous_bars':sum(t['exit_reason']=='stop_ambiguous' for t in closed),
            'trades_with_data_gap':sum(bool(t.get('data_gap')) for t in closed)}

class Radar:
    def __init__(self,cfg,manual=None):
        self.cfg=copy.deepcopy(cfg);self.manual=manual or [];self.manual_added=set()
        self.agg={n:Aggregator(n) for n in (5,15,60)}
        self.bars={n:deque(maxlen=240) for n in (5,15,60)}
        self.chart=[];self.zones=[];self.children=[];self.events=[];self.trades=[];self.positions=[]
        self.pending=None;self.balance=cfg['initial_equity'];self.curve=[];self.context='neutral'
        self.first=None;self.last=None;self.day=None;self.day_start=self.balance;self.sequence=0
        self.config_hash=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()

    def event(self,t,kind,zone=None,**extra):
        self.sequence+=1
        e={'id':self.sequence,'time':t,'type':kind,**extra}
        if zone:e.update(zone_id=zone['id'],direction=zone['direction'],low=zone['low'],high=zone['high'])
        self.events.append(e);return e

    def zone(self,z,t,source='auto'):
        z=copy.deepcopy(z);z.update(id=f'z{len(self.zones)+1}',state='waiting',child=None,near=False,source=source)
        self.zones.append(z);self.event(t,'zone_created',z,reason='Старшая зона H1 зарегистрирована',context=self.context)

    def cancel(self,z,t,reason):
        z['state']='cancelled';self.event(t,'cancelled',z,reason=reason)

    def close_trade(self,p,price,t,reason):
        sign=1 if p['direction']=='buy' else -1
        fee=self.cfg['commission_per_oz_side']*p['quantity_oz']*2
        pnl=(price-p['entry'])*sign*p['quantity_oz']-fee
        p.update(exit=price,exit_time=t,exit_reason=reason,pnl=pnl,r=pnl/p['risk_usd'],fees=fee)
        self.balance+=pnl;self.positions.remove(p)
        self.event(t,'position_closed',trade_id=p['id'],reason=reason,pnl=pnl)

    def fill_pending(self,row):
        if not self.pending:return
        signal=self.pending;self.pending=None;cfg=self.cfg;t=row['time']
        z=next(z for z in self.zones if z['id']==signal['zone_id'])
        spread=row['ask_open']-row['bid_open']
        reason=None
        if t!=signal['time']:reason='Пропуск данных перед входом'
        elif len(self.positions)>=cfg['max_positions']:reason='Достигнут лимит одновременных позиций'
        elif self.balance<=self.day_start*(1-cfg['daily_loss_fraction']):reason='Достигнут дневной лимит потерь'
        elif spread>cfg['max_spread']:reason='Спред выше предела'
        buy=signal['direction']=='buy';slip=cfg['slippage_per_oz_side']
        entry=row['ask_open']+slip if buy else row['bid_open']-slip
        distance=entry-signal['stop'] if buy else signal['stop']-entry
        if distance<=0:reason='Цена открытия за стопом'
        elif spread/distance>cfg['max_spread_to_stop']:reason='Спред слишком велик относительно стопа'
        if reason:
            self.event(t,'entry_rejected',z,reason=reason);return
        per_oz=distance+slip+2*cfg['commission_per_oz_side']
        budget=self.balance*cfg['risk_fraction'];step=cfg['min_quantity_oz']
        qty=math.floor(budget/per_oz/step)*step;risk=qty*per_oz
        if qty<=0 or sum(p['risk_usd'] for p in self.positions)+risk>self.balance*cfg['max_open_risk_fraction']:
            self.event(t,'entry_rejected',z,reason='Лимит общего риска или недостаточный объём');return
        p={'id':f't{len(self.trades)+1}','zone_id':z['id'],'direction':signal['direction'],
           'signal_time':signal['time'],'entry_time':t,'entry':entry,'stop':signal['stop'],
           'target':entry+(1 if buy else -1)*distance*cfg['reward_risk'],'quantity_oz':qty,
           'risk_usd':risk,'spread_at_entry':spread,'exit_time':None,'config_hash':self.config_hash,
           'child_low':signal['child_low'],'child_high':signal['child_high'],
           'explanation':signal['explanation']}
        self.positions.append(p);self.trades.append(p)
        self.event(t,'paper_entry',z,trade_id=p['id'],entry=entry,stop=p['stop'],target=p['target'],risk_usd=risk,reason='Симуляция: вход по открытию следующей M1')

    def on_bar(self,row):
        cfg=self.cfg;t=row['time'];end=t+60
        if self.last is not None and t<=self.last:raise ValueError('Повторная или старая свеча')
        if self.first is None:self.first=t
        day=t//86400
        if day!=self.day:self.day=day;self.day_start=self.balance
        # After a missing minute, live price path is unknown: invalidate waiting setups.
        if self.last is not None and t-self.last>60:
            for p in self.positions:p['data_gap']=True
            for z in self.zones:
                if z['state'] in ('waiting','ready'):self.cancel(z,t,'Разрыв котировок: сценарий сброшен')
            self.children=[]
        self.fill_pending(row)
        for p in list(self.positions):
            result=exit_price(p,row,cfg)
            if result:self.close_trade(p,result[0],result[2],result[1])
        closed={}
        for n,a in self.agg.items():
            bar=a.add(row)
            if bar:self.bars[n].append(bar);closed[n]=bar
        if 60 in closed:
            self.context=trend(self.bars[60])
            parent=fvg(self.bars[60],cfg)
            if not self.manual and parent and parent['direction']==self.context and end-self.first>=cfg['warmup_hours']*3600:
                self.zone(parent,end)
        for index,z in enumerate(self.manual):
            if index not in self.manual_added and z['created']<=end:
                self.zone(z,end,'manual');self.manual_added.add(index)
        if 15 in closed:
            child=fvg(self.bars[15],cfg)
            if child:self.children.append(child)
        self.children=[x for x in self.children if end-x['created']<=cfg['child_lifetime_hours']*3600]
        if 5 in closed:
            b=closed[5];v=atr(self.bars[5]);self.chart.append(dict(b,context=self.context))
            if v:
                for z in self.zones:
                    if z['state'] not in ('waiting','ready'):continue
                    buy=z['direction']=='buy'
                    if end-z['created']>cfg['parent_lifetime_hours']*3600:
                        self.cancel(z,end,'Истёк срок старшей зоны');continue
                    if (buy and b['close']<z['low']) or (not buy and b['close']>z['high']):
                        self.cancel(z,end,'Свеча закрылась за дальней границей старшей зоны');continue
                    if self.context!=z['direction']:
                        self.cancel(z,end,'Старшая структура больше не подтверждает направление');continue
                    if not z['child']:
                        candidates=[x for x in self.children if x['direction']==z['direction'] and not x['touched'] and x['low']>=z['low'] and x['high']<=z['high'] and x['created']>=z['origin']]
                        if candidates:
                            z['child']=copy.deepcopy(min(candidates,key=lambda x:abs(b['close']-(x['low']+x['high'])/2)))
                            z['state']='ready';self.event(end,'child_found',z,child_low=z['child']['low'],child_high=z['child']['high'],reason='Нетронутая M15-зона внутри H1')
                    c=z['child']
                    if not c:continue
                    if end-c['created']>cfg['child_lifetime_hours']*3600:
                        self.cancel(z,end,'Истёк срок младшей зоны');continue
                    distance=max(c['low']-b['close'],b['close']-c['high'],0)
                    if distance<=cfg['near_atr']*v and not z['near']:
                        z['near']=True;self.event(end,'near',z,child_low=c['low'],child_high=c['high'],reason='Цена приблизилась к младшей зоне')
                    # Confirmation cannot use the candle that created either zone.
                    if b['time']<max(z['created'],c['created']):continue
                    if (buy and b['close']<c['low']) or (not buy and b['close']>c['high']):
                        self.cancel(z,end,'Свеча закрылась за младшей зоной');continue
                    touched=b['low']<=c['high'] and b['high']>=c['low']
                    confirmed=touched and (b['close']>c['high'] and b['close']>b['open'] if buy else b['close']<c['low'] and b['close']<b['open'])
                    if confirmed:
                        z['state']='triggered'
                        stop=c['low']-cfg['stop_buffer_atr']*v if buy else c['high']+cfg['stop_buffer_atr']*v
                        explanation=f"H1: {'рост' if buy else 'снижение'}; M15-зона {c['low']:.3f}–{c['high']:.3f} внутри H1; закрытая M5 коснулась зоны и вернулась в направлении сделки."
                        e=self.event(end,'confirmed',z,child_low=c['low'],child_high=c['high'],stop=stop,explanation=explanation,reason='Условия выполнены')
                        if end%86400>=cfg['flatten_hour_utc']*3600:
                            self.event(end,'entry_rejected',z,reason='Окончание выбранной сессии')
                        elif self.pending:
                            self.event(end,'entry_rejected',z,reason='Другой сигнал уже ожидает исполнения')
                        else:self.pending=e
        # Freshness updated after evaluation; future touches cannot affect previous decisions.
        for c in self.children:
            if t>=c['created'] and row['bid_low']<=c['high'] and row['bid_high']>=c['low']:c['touched']=True
        floating=0
        for p in self.positions:
            price=row['bid_close'] if p['direction']=='buy' else row['ask_close']
            floating+=(price-p['entry'])*(1 if p['direction']=='buy' else -1)*p['quantity_oz']-2*cfg['commission_per_oz_side']*p['quantity_oz']
        self.curve.append({'time':end,'equity':self.balance+floating,'balance':self.balance,'open_risk':sum(p['risk_usd'] for p in self.positions)})
        self.last=t

    def result(self):
        return {'config':self.cfg,'config_hash':self.config_hash,'events':self.events,'trades':self.trades,
                'zones':self.zones,'bars':self.chart,'curve':self.curve,'pending':self.pending,
                'summary':summarize(self.trades,self.cfg['initial_equity'],self.curve)}

def run(rows,cfg,manual=None):
    radar=Radar(cfg,manual)
    for row in rows:radar.on_bar(row)
    return radar.result()
