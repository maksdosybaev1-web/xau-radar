"""Local operational status; no market forecasts or Telegram credentials in output."""
import csv,datetime as dt,io,json,time,uuid
from .datafeed import ROOT
from .quote_archive import QuoteArchive
from .tick_archive import TickArchive
from .forward_assessment import snapshot as fvg_forward_snapshot

def heartbeat(component,state,**extra):
    folder=ROOT/'runtime';folder.mkdir(exist_ok=True)
    target=folder/(component+'-status.json');temp=folder/(component+'-'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(dict(updated_at=int(time.time()),state=state,**extra),ensure_ascii=False),encoding='utf-8')
        for attempt in range(4):
            try:
                temp.replace(target)
                break
            except PermissionError:
                if attempt == 3:raise
                time.sleep(.05*(attempt+1))
    finally:
        if temp.exists():temp.unlink()

def read_status(component):
    try:return json.loads((ROOT/'runtime'/(component+'-status.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def summarize_health(bridge,delivery,telegram,now):
    bridge_alive=0<=now-bridge.get('updated_at',0)<=45
    delivery_alive=0<=now-delivery.get('updated_at',0)<=60
    quote_time=bridge.get('quote_time');age=now-quote_time if quote_time else None
    quote_fresh=age is not None and 0<=age<=90
    bridge_label='Работает' if bridge_alive and bridge.get('state')=='running' else 'Переподключается' if bridge_alive and bridge.get('state')=='retrying' else 'Запускается' if bridge_alive and bridge.get('state')=='starting' else 'Нет свежего статуса'
    telegram_label='Выключен' if not telegram.get('enabled') else 'Нет настроек' if not telegram.get('configured') else 'Ошибка очереди' if delivery_alive and delivery.get('state')=='error' else 'Очередь работает' if delivery_alive else 'Очередь не отвечает'
    return {'bridge':{'alive':bridge_alive,'state':bridge.get('state','unknown'),'label':bridge_label,
                      'updated_at':bridge.get('updated_at'),'error_type':bridge.get('error_type'),
                      'error_code':bridge.get('error_code')},
            'quote':{'fresh':quote_fresh,'time':quote_time,'age_seconds':age,'label':'Свежая' if quote_fresh else 'Устарела' if quote_time else 'Нет котировки'},
            'telegram':{'enabled':telegram.get('enabled',False),'configured':telegram.get('configured',False),'worker_alive':delivery_alive,'label':telegram_label},
            'ready_for_new_alerts':bridge_alive and bridge.get('state')=='running' and quote_fresh and delivery_alive and delivery.get('state')=='running' and telegram.get('enabled',False) and telegram.get('configured',False)}

def status(store=None,now=None):
    from .notifications import AlertStore,public_settings
    now=int(time.time() if now is None else now);store=store or AlertStore()
    result=summarize_health(read_status('bridge'),read_status('delivery'),public_settings(),now)
    with store.connect() as db:
        grouped=db.execute('SELECT status,COUNT(*) FROM alerts WHERE time>=? AND time<=? GROUP BY status',(now-86400,now)).fetchall()
        count,first,last=db.execute('SELECT COUNT(*),MIN(time),MAX(time) FROM alerts').fetchone()
        waiting=db.execute('SELECT status,COUNT(*) FROM alerts WHERE status IN ("pending","sending") GROUP BY status').fetchall()
    result.update(as_of=now,window_hours=24,journal={'total':count,'first_event':first,'last_event':last,'last_24h':dict(grouped),'unresolved':dict(waiting)})
    result['quote_archive']=QuoteArchive().summary(now)
    result['tick_archive']=TickArchive().summary(now)
    try:result['fvg_forward']=fvg_forward_snapshot(now=now)
    except (OSError,ValueError,KeyError,TypeError):
        result['fvg_forward']={'status':'error','message':'Не удалось прочитать FVG forward-журнал'}
    return result

def export_csv(store=None):
    from .notifications import AlertStore
    store=store or AlertStore();output=io.StringIO(newline='')
    fields=['id','time_utc','observed_at_utc','tf','type','direction','price','score','rsi','atr','support','resistance','delivery','attempts','detail']
    writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
    with store.connect() as db:
        for payload,delivery,attempts,detail in db.execute('SELECT payload,status,attempts,detail FROM alerts ORDER BY time,id'):
            e=json.loads(payload);row={k:e.get(k,'') for k in fields}
            row.update(time_utc=dt.datetime.fromtimestamp(e['time'],dt.timezone.utc).isoformat(),delivery=delivery,attempts=attempts,detail=detail)
            row['observed_at_utc']=dt.datetime.fromtimestamp(e['observed_at'],dt.timezone.utc).isoformat() if e.get('observed_at') is not None else ''
            # Keep text columns inert if the CSV is opened in a spreadsheet.
            writer.writerow({k:("'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@') else v) for k,v in row.items()})
    return ('\ufeff'+output.getvalue()).encode('utf-8')

def report_text(s):
    counts=s['journal']['last_24h'];stamp=dt.datetime.fromtimestamp(s['as_of'],dt.timezone.utc).isoformat()
    archive=s.get('quote_archive',{})
    ticks=s.get('tick_archive',{})
    return '\n'.join(['# Наблюдение за работой радара','',f'Снимок: {stamp}.',
        f'Мост MT5: {s["bridge"]["label"]}. Котировка: {s["quote"]["label"]}. Telegram: {s["telegram"]["label"]}.','',
        f'Всего записанных событий: {s["journal"]["total"]}. За последние 24 часа: {sum(counts.values())}.',
        f'Подтверждённых доставок рыночных событий за 24 часа: {counts.get("sent",0)}. Ошибок: {counts.get("failed",0)}. Подавленных повторов: {counts.get("suppressed",0)}.',
        f'Ожидают доставки: {s["journal"]["unresolved"].get("pending",0)}. Попыток без окончательного подтверждения: {s["journal"]["unresolved"].get("sending",0)}.','',
        f'Архив живых bid/ask: {archive.get("total",0)} записей; за 24 часа {archive.get("last_24h",0)}. Интервалов между сохранёнными котировками более 90 секунд: {archive.get("intervals_over_90s",0)}.',
        f'Архив тиков MT5: {ticks.get("total",0)} записей; запросов истории {ticks.get("pulls",0)}, без статуса ok {ticks.get("non_ok_pulls",0)}.',
        'Тики считываются из истории терминала в пределах последней минуты каждого опроса. Это более плотные bid/ask, но полнота истории брокера не доказана; поздно полученные тики не являются своевременной доставкой сигнала.',
        'Архив содержит выборки при опросе примерно раз в 10 секунд, а не все тики. Длинный интервал может означать закрытый рынок, отсутствие изменений или перерыв наблюдения. Касания уровней между выборками неизвестны.','',
        'Тестовое сообщение настройки не считается рыночным сигналом. Отсутствие событий не доказывает работоспособность на свежем рынке; проверка новых котировок продолжается после их появления. Здесь нет оценки прибыли или качества стратегии.'])

if __name__=='__main__':
    s=status();folder=ROOT/'results';folder.mkdir(exist_ok=True)
    (folder/'observation_report.md').write_text(report_text(s),encoding='utf-8')
    (folder/'observation_status.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(s,ensure_ascii=False))
