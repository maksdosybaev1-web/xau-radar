"""Closed-bar market observations, distinct from the nested-FVG strategy."""
import bisect, hashlib, json
from collections import deque
from .datafeed import ROOT
from .indicators import Indicators

CFG=json.loads((ROOT/'radar_config.json').read_text(encoding='utf-8'))
LABELS={'ema_cross':'Пересечение EMA20/50','rsi_extreme':'RSI вышел за 30/70',
        'breakout':'Закрытие за уровнем','near_level':'Цена около уровня',
        'volume_spike':'Повышенная активность','volatility_high':'Высокая волатильность',
        'watch':'Совпали условия наблюдения'}


class TimeframeRadar:
    def __init__(self,tf,cfg=None):
        self.tf=tf;self.cfg=cfg or CFG;self.indicators=Indicators()
        self.bars=deque(maxlen=self.cfg['level_lookback']+5)
        self.pivots=[];self.last=None;self.count=0;self.previous=None

    def add(self,bar):
        if self.last is not None and bar['end']<=self.last:raise ValueError('Повторная или старая свеча индикаторов')
        if not (0<bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high']):raise ValueError('Некорректная свеча индикаторов')
        if bar['end']-bar['time']!=CFG['timeframes'][self.tf]*60:raise ValueError('Неверная длительность свечи')
        self.bars.append(bar);self.count+=1;self.last=bar['end'];v=self.indicators.add(bar)
        recent=list(self.bars)
        if len(recent)>=5:
            w=recent[-5:];middle=w[2]
            if all(a['end']==b['time'] for a,b in zip(w,w[1:])):
                for field,kind in [('high','resistance'),('low','support')]:
                    if all(middle[field]>x[field] if field=='high' else middle[field]<x[field] for x in w[:2]+w[3:]):
                        self.pivots.append({'price':middle[field],'kind':kind,'confirmed_at':bar['end'],'origin':middle['time']})
        oldest=recent[max(0,len(recent)-self.cfg['level_lookback'])]['time']
        self.pivots=[p for p in self.pivots if p['origin']>=oldest]
        price=bar['close'];below=[p for p in self.pivots if p['price']<price];above=[p for p in self.pivots if p['price']>price]
        support=max(below,key=lambda p:p['price']) if below else None
        resistance=min(above,key=lambda p:p['price']) if above else None
        previous=self.previous;cross=None;breakout=None
        if previous:
            a,b=previous['ema20'],previous['ema50']
            if a is not None and b is not None and v['ema50'] is not None:
                if a<=b and v['ema20']>v['ema50']:cross='buy'
                if a>=b and v['ema20']<v['ema50']:cross='sell'
            for key,direction in [('resistance','buy'),('support','sell')]:
                level=previous[key]
                if level and (previous['close']<=level['price']<price if direction=='buy' else previous['close']>=level['price']>price):
                    breakout={'direction':direction,'level':level['price']}
        trend='neutral'
        if v['ema50'] is not None:
            if price>v['ema20']>v['ema50']:trend='buy'
            elif price<v['ema20']<v['ema50']:trend='sell'
        rsi=v['rsi'];atr=v['atr'];ar=v['atr_ratio'];vr=v['volume_ratio'];near=[]
        if atr and atr>0:
            for key,level in [('support',support),('resistance',resistance)]:
                if level and abs(price-level['price'])<=self.cfg['near_atr']*atr:near.append(key)
        ready=v['ema200'] is not None and ar is not None and v['macd_hist'] is not None
        buy=trend=='buy';directional=trend!='neutral'
        criteria=[
            ('ema','Цена и EMA20/50 направлены одинаково',25,directional),
            ('ema200','Цена по нужную сторону EMA200',15,directional and v['ema200'] is not None and (price>v['ema200'] if buy else price<v['ema200'])),
            ('rsi','RSI поддерживает импульс без крайности',15,directional and rsi is not None and (55<=rsi<=70 if buy else 30<=rsi<=45)),
            ('macd','MACD и гистограмма подтверждают направление',15,directional and v['macd_hist'] is not None and (v['macd']>0 and v['macd_hist']>0 if buy else v['macd']<0 and v['macd_hist']<0)),
            ('volume','Объём / активность выше среднего в 1,5 раза',10,vr is not None and vr>=self.cfg['volume_ratio']),
            ('atr','ATR в диапазоне 0,75–2 медиан',10,ar is not None and .75<=ar<=2),
            ('level','Пробой по направлению или возврат к уровню',10,directional and (bool(breakout and breakout['direction']==trend) or ('support' if buy else 'resistance') in near))]
        parts=[{'id':k,'label':label,'weight':weight,'met':bool(met),'points':weight if met else 0} for k,label,weight,met in criteria]
        score=sum(p['points'] for p in parts) if ready else None
        watch=ready and directional and score>=self.cfg['watch_score']
        conditions={'ema_cross':bool(cross),'rsi_extreme':rsi is not None and (rsi>70 or rsi<30),
                    'breakout':bool(breakout),'near_level':bool(near),'volume_spike':vr is not None and vr>=self.cfg['volume_ratio'],
                    'volatility_high':ar is not None and ar>=self.cfg['volatility_high'],'watch':watch}
        plan=None
        if ready and directional and atr and atr>0:
            distance=self.cfg['stop_atr']*atr;sign=1 if buy else -1
            plan={'direction':trend,'reference':price,'stop':price-sign*distance,'target':price+sign*distance*self.cfg['reward_risk'],
                  'distance':distance,'reward_risk':self.cfg['reward_risk'],
                  'basis':'Сценарий от закрытия свечи: 1,5 ATR до стопа, цель 2R; спред и комиссии не включены.'}
        out={**bar,**v,'tf':self.tf,'bars_seen':self.count,'trend':trend,'ready':ready,'score':score,'score_parts':parts,
             'status':'WATCH' if watch else 'OBSERVE' if ready else 'WARMUP',
             'volatility':'high' if ar is not None and ar>=self.cfg['volatility_high'] else 'low' if ar is not None and ar<self.cfg['volatility_low'] else 'medium' if ar is not None else 'unknown',
             'support':support,'resistance':resistance,'near':near,'ema_cross':cross,'breakout':breakout,'conditions':conditions,'plan':plan}
        events=[]
        if ready and previous and previous['ready']:
            for kind,active in conditions.items():
                # Crossings and breakouts are events; other conditions alert on their rising edge.
                if active and (kind in ('ema_cross','breakout') or not previous['conditions'].get(kind)):
                    direction=cross if kind=='ema_cross' else breakout['direction'] if kind=='breakout' else trend
                    identity=f"{self.cfg['version']}|{self.tf}|{bar['end']}|{kind}"
                    events.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:24],
                                   'time':bar['end'],'tf':self.tf,'type':kind,'label':LABELS[kind],'direction':direction,
                                   'price':price,'score':score,'rsi':rsi,'ema20':v['ema20'],'ema50':v['ema50'],'atr':atr,
                                   'support':support['price'] if support else None,'resistance':resistance['price'] if resistance else None,
                                   'reason':f"{LABELS[kind]} на закрытой {self.tf}. Оценка условий {score}/100; решение о сделке не принимается.",
                                   'version':self.cfg['version']})
        self.previous=out
        return out,events


def prepare_history(m5_bars):
    """Aggregate only complete M5 buckets; each observation exists at its close."""
    result={'version':CFG['version'],'frames':{},'events':[]}
    for tf,minutes in CFG['timeframes'].items():
        model=TimeframeRadar(tf);observations=[];bucket=None;group=[]
        for bar in m5_bars:
            start=bar['time']//(minutes*60)*(minutes*60)
            if start!=bucket:bucket=start;group=[]
            group.append(bar)
            if len(group)!=minutes//5 or group[0]['time']!=start or bar['end']!=start+minutes*60:continue
            if any(a['end']!=b['time'] for a,b in zip(group,group[1:])):continue
            merged={'time':start,'end':bar['end'],'open':group[0]['open'],'high':max(b['high'] for b in group),
                    'low':min(b['low'] for b in group),'close':bar['close'],'volume':sum(b['volume'] for b in group)}
            snapshot,events=model.add(merged);observations.append(snapshot);result['events'].extend(events)
        result['frames'][tf]=observations
    result['events'].sort(key=lambda e:(e['time'],e['tf'],e['type']))
    return result


def view(result,now,tf='M15'):
    if tf not in CFG['timeframes']:raise ValueError('Таймфрейм должен быть M5, M15 или H1')
    frames={};chart=[]
    for key,observations in result.get('frames',{}).items():
        index=bisect.bisect_right([x['end'] for x in observations],now)
        frames[key]=observations[index-1] if index else None
        if key==tf:chart=[{k:x[k] for k in ('time','end','open','high','low','close','ema20','ema50','ema200')} for x in observations[max(0,index-150):index]]
    return {'ready':bool(frames.get(tf)),'tf':tf,'frames':frames,'chart':chart,
            'events':[e for e in result.get('events',[]) if e['time']<=now][-60:][::-1],
            'version':CFG['version'],'config':CFG}


def main():
    from .research import save_json
    source=json.loads((ROOT/'results'/'run.json').read_text(encoding='utf-8'))
    result=prepare_history(source['bars'])
    result['source']='Dukascopy · bid/ask M1 → закрытые свечи';save_json(ROOT/'results'/'technical_history.json',result)
    print(json.dumps({'frames':{k:len(v) for k,v in result['frames'].items()},'events':len(result['events'])}))

if __name__=='__main__':main()
