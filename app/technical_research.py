"""Fixed-rule event study of WATCH transitions; not a portfolio recommendation."""
import bisect, datetime as dt, hashlib, json
from .datafeed import ROOT,load_csv
from .engine import exit_price,summarize
from .market_radar import CFG
from .research import save_json


def study(events,rows,tf,start,end):
    times=[r['time'] for r in rows];trades=[];occupied=0;missing=0;incomplete=0;eligible=0
    costs=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    costs.update(max_holding_minutes=CFG['timeframes'][tf]*6,flatten_hour_utc=24)
    for event in events:
        stamp=event['time']
        if event['tf']!=tf or event['type']!='watch' or not start<=stamp<end:continue
        eligible+=1
        if stamp<occupied:continue
        index=bisect.bisect_left(times,stamp)
        if index>=len(rows) or times[index]!=stamp:missing+=1;continue
        buy=event['direction']=='buy';sign=1 if buy else -1;row=rows[index]
        entry=row['ask_open']+costs['slippage_per_oz_side'] if buy else row['bid_open']-costs['slippage_per_oz_side']
        distance=event['atr']*CFG['stop_atr'];stop=event['price']-sign*distance;target=event['price']+sign*distance*CFG['reward_risk']
        actual_distance=sign*(entry-stop)
        if actual_distance<=0 or sign*(target-entry)<=0:missing+=1;continue
        unit_risk=actual_distance+costs['slippage_per_oz_side']+2*costs['commission_per_oz_side']
        trade={'direction':event['direction'],'entry_time':stamp,'entry':entry,'stop':stop,'target':target,'risk_usd':25.,'quantity_oz':25/unit_risk}
        last=stamp-60;found=False
        for bar in rows[index:index+costs['max_holding_minutes']+1]:
            if bar['time']>=end:break
            if bar['time']!=last+60:missing+=1;found=True;occupied=bar['time']+60;break
            last=bar['time'];out=exit_price(trade,bar,costs)
            if out:
                price,reason,closed=out;pnl=(price-entry)*sign*trade['quantity_oz']-2*costs['commission_per_oz_side']*trade['quantity_oz']
                trade.update(exit=price,exit_time=closed,exit_reason=reason,pnl=pnl,r=pnl/25)
                trades.append(trade);occupied=closed;found=True;break
        if not found:incomplete+=1;occupied=end
    return {'tf':tf,'eligible_watch':eligible,'gap_or_invalid_excluded':missing,'unclosed_excluded':incomplete,
            'summary':summarize(trades),'trades':trades}


def main():
    source=json.loads((ROOT/'results'/'technical_history.json').read_text(encoding='utf-8'));rows=load_csv()
    cutoff=int(dt.datetime(2025,9,1,tzinfo=dt.timezone.utc).timestamp());begin=rows[0]['time'];end=rows[-1]['time']+60
    runs=[]
    for label,a,b in [('Июль–август',begin,cutoff),('Сентябрь',cutoff,end)]:
        for tf in CFG['timeframes']:runs.append(dict(study(source['events'],rows,tf,a,b),period=label))
    counts={tf:sum(e['type']=='watch' and e['tf']==tf for e in source['events']) for tf in CFG['timeframes']}
    summary=f"Индикаторы рассчитаны на июле–сентябре 2025. Переходов в WATCH: M5 — {counts['M5']}, M15 — {counts['M15']}, H1 — {counts['H1']}. Это отдельная проверка условий с фиксированными правилами, не статистика старого FVG-сценария. Вероятность прибыли по оценке 0–100 не установлена."
    result={'ready':True,'version':CFG['version'],'config_sha256':hashlib.sha256((ROOT/'radar_config.json').read_bytes()).hexdigest(),
            'summary_text':summary,'watch_counts':counts,'runs':runs}
    save_json(ROOT/'results'/'technical_validation.json',result)
    lines=['# Проверка индикаторного радара v1','',summary,'',
           'Параметры зафиксированы до расчёта этого отчёта; подбора по результатам не было. Каждый период и таймфрейм рассматривается отдельно, без одновременных сделок внутри одного прогона. Индикаторы сентября используют только ранее известную историю для разогрева.','',
           'Условный опыт: переход в WATCH → открытие следующей M1 по ask/bid, SL 1,5 ATR и TP 3 ATR от сигнального закрытия, максимум 6 свечей выбранного периода. Условный риск 25 USD, комиссии 0,04 USD/унцию за сторону, проскальзывание 0,05 USD/унцию за сторону. Стоп выбирается при неоднозначном касании; разрыв M1 во время сделки исключает наблюдение. Эти расходы не являются тарифом вашего брокера.','',
           '| Период | TF | WATCH | Завершённых опытов | Средний R | Доля прибыльных | Исключено из-за разрыва / цены |','|---|---|---:|---:|---:|---:|---:|']
    for r in runs:
        s=r['summary'];avg='—' if s['average_r'] is None else f'{s["average_r"]:.3f}';win='—' if s['win_rate'] is None else f'{s["win_rate"]:.1%}'
        lines.append(f'| {r["period"]} | {r["tf"]} | {r["eligible_watch"]} | {s["closed"]} | {avg} | {win} | {r["gap_or_invalid_excluded"]} |')
    lines+=['','Показатели разных TF нельзя складывать: это коррелированные наблюдения одного инструмента. Перекрывающиеся сигналы пропущены; незакрытые опыты не превращены в сделки. Это предварительное исследование WATCH, а не проверенный торговый портфель. Нет моделирования маржи и свопов; оценка 0–100 не откалибрована как вероятность. Forward-наблюдения начнут накапливаться после появления новых котировок.','',
            'В полном JSON сохранены все включённые условные сделки и счётчики исключений. Исходный FVG-отчёт results/validation.md остаётся отдельным.']
    (ROOT/'results'/'technical_validation.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'watch':counts,'results':[{'period':r['period'],'tf':r['tf'],'trades':r['summary']['closed'],'average_r':r['summary']['average_r']} for r in runs]}))

if __name__=='__main__':main()
