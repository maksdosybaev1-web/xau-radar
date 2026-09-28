"""Frozen-parameter chronological evaluation and inspectable JSON/CSV journals."""
import csv, datetime as dt, hashlib, json
from .datafeed import ROOT, load_csv
from .engine import run, summarize, iso

def save_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8');tmp.replace(path)

def evaluate(rows,cfg):
    result=run(rows,cfg)
    cutoff=int(dt.datetime.fromisoformat(cfg['holdout_start']).replace(tzinfo=dt.timezone.utc).timestamp())
    test=[r for r in rows if r['time']>=cutoff]
    warm=[r for r in rows if cutoff-cfg['warmup_hours']*3600<=r['time']<cutoff]
    # Separate account and state; warmup is observation only, no positions carry over.
    out=run(warm+test,cfg) if test else None
    training=[t for t in result['trades'] if t['entry_time']<cutoff and t.get('exit_time') is not None and t['exit_time']<=cutoff]
    train_curve=[x for x in result['curve'] if x['time']<=cutoff]
    result['evaluation']={'cutoff':cutoff,'policy':'Параметры зафиксированы до просмотра результатов; отдельный прогон сентября с разогревом и новым счётом.',
        'development':summarize(training,cfg['initial_equity'],train_curve),
        'holdout':out['summary'] if out else None,
        'holdout_open_positions':len([t for t in out['trades'] if t['exit_time'] is None]) if out else 0,
        'forward_test':{'status':'not_started','reason':'Нужны новые котировки после фиксации версии; историческая перемотка не является forward-тестом.'},
        'ai_comparison':{'status':'not_run','reason':'Локальная модель используется только для объяснений. Заранее зафиксированного ИИ-фильтра нет; базовая стратегия не фильтруется ИИ.'}}
    return result,out

def main():
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'));rows=load_csv()
    outdir=ROOT/'results';outdir.mkdir(exist_ok=True)
    result,holdout=evaluate(rows,cfg)
    result['source']=json.loads((ROOT/'data'/'provenance.json').read_text(encoding='utf-8'))
    save_json(outdir/'run.json',result)
    if holdout:save_json(outdir/'holdout.json',holdout)
    with (outdir/'events.jsonl').open('w',encoding='utf-8') as f:
        for e in result['events']:f.write(json.dumps(e,ensure_ascii=False)+'\n')
    trades=result['trades'];columns=sorted({k for t in trades for k in t}) or ['id','entry_time','exit_time','pnl']
    with (outdir/'trades.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(trades)
    digest=hashlib.sha256((ROOT/'config.json').read_bytes()).hexdigest()
    lines=['# Проверка прототипа на истории','',f'Источник: Dukascopy XAUUSD, bid/ask M1. {iso(rows[0]["time"])} — {iso(rows[-1]["time"])}.',
           '',f'Отсутствующих дневных архивов bid/ask: {len(result["source"]["missing_archives"])}. Минут без пары bid/ask: {result["source"]["bid_ask_unmatched_minutes"]}. Пропуски не заполнялись. '+('Архив запрошенного периода собран полностью.' if not result['source']['missing_archives'] and not result['source']['bid_ask_unmatched_minutes'] else 'Результат предварительный при неполноте данных.'),
           '',f'Версия: {cfg["strategy_version"]}. SHA256 файла настроек: {digest}.','',
           'Это проверка нашей формализации nested-FVG, а не измерение прибыльности всей методики автора. Настройки не подбирались по результатам.','',
           '| Период | Закрытых сделок | Итог, USD | Средний R | Доля прибыльных | Просадка с открытыми позициями |',
           '|---|---:|---:|---:|---:|---:|']
    for name,s in [('Весь период',result['summary']),('До сентября',result['evaluation']['development']),('Сентябрь, отдельный прогон',result['evaluation']['holdout'])]:
        if not s:continue
        avg='—' if s['average_r'] is None else f'{s["average_r"]:.3f}'
        win='—' if s['win_rate'] is None else f'{s["win_rate"]:.1%}'
        lines.append(f'| {name} | {s["closed"]} | {s["net_pnl"]:.2f} | {avg} | {win} | {s["max_marked_drawdown"]:.2%} |')
    lines+=['','Условия исполнения: вход на открытии следующей M1 по ask для покупки и bid для продажи; выход по противоположной стороне. Комиссия 0,04 USD/унцию за сторону; проскальзывание 0,05 USD/унцию за сторону — сценарные допущения, не тариф брокера. При касании стопа и цели в одной минуте выбран стоп. Гэп через стоп исполняется по худшей цене открытия.','',
            f'Разрыв данных во время открытой сделки: {result["summary"]["trades_with_data_gap"]}. При таком разрыве результат исполнения недостоверен. Трёхсвечные FVG через пропуск свечей не создаются.','',
            'Объём выражен в условных унциях, не в брокерских лотах. Маржа, свопы и исполнение реального брокера не моделируются. Есть закрытие по времени и выбранной UTC-сессии; принудительное закрытие в конце файла не создаётся. Открытые позиции показаны отдельно.','',
            'Количество сигналов и сделок может быть мало: отсутствие или редкость сигналов не исправляется ослаблением правил ради красивого результата. Неположительный результат — основание не использовать эту версию для реальных сделок.','',
            'Forward-тест на новых данных не завершён. ИИ-фильтр не оценён. Перемотка исторических данных не заменяет ни тот, ни другой этап.']
    (outdir/'validation.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'events':len(result['events']),'zones':len(result['zones']),'all':result['summary'],'evaluation':result['evaluation']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
