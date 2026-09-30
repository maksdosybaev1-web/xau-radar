"""Local operational status; no market forecasts or Telegram credentials in output."""
import csv,datetime as dt,io,json,time,uuid
from .datafeed import ROOT
from .quote_archive import QuoteArchive
from .tick_archive import TickArchive
from .forward_assessment import snapshot as fvg_forward_snapshot

def m1_is_recent(last,now):
    return last is not None and 60<=now-last<=180

def heartbeat(component,state,**extra):
    folder=ROOT/'runtime';folder.mkdir(exist_ok=True)
    target=folder/(component+'-status.json');temp=folder/(component+'-'+uuid.uuid4().hex+'.tmp')
    try:
        now=int(time.time());data=dict(updated_at=now,state=state,**extra)
        if component=='bridge':
            previous=read_status(component)
            if 'last_m1' not in data and previous.get('last_m1') is not None:data['last_m1']=previous['last_m1']
            if previous.get('last_recovery'):data['last_recovery']=previous['last_recovery']
            gaps={item['after']:item for item in previous.get('m1_gaps',[])}
            gaps.update({item['after']:item for item in extra.get('m1_gaps',[])})
            data['m1_gaps']=sorted(gaps.values(),key=lambda item:item['after'])[-20:]
            outage_start=previous.get('outage_started_at')
            if state=='retrying':
                data['outage_started_at']=outage_start or now
                data['outage_reason']=previous.get('outage_reason') if outage_start else extra.get('error_type','MT5')
            elif state=='starting' and (outage_start or now-previous.get('updated_at',now)>90):
                data['outage_started_at']=outage_start or previous['updated_at']
                data['outage_reason']=previous.get('outage_reason') or 'Нет свежего статуса моста'
            elif state=='running' and outage_start:
                data['last_recovery']={'started_at':outage_start,'ended_at':now,
                    'duration_seconds':max(0,now-outage_start),
                    'reason':previous.get('outage_reason','MT5'),
                    'last_m1_before':previous.get('last_m1'),
                    'last_m1_after':extra.get('last_m1')}
        temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        for attempt in range(4):
            try:
                temp.replace(target)
                break
            except PermissionError:
                if attempt == 3:raise
                time.sleep(.05*(attempt+1))
    finally:
        if temp.exists():temp.unlink()

def read_status(component,root=None):
    try:return json.loads(((ROOT if root is None else root)/'runtime'/(component+'-status.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def summarize_health(bridge,delivery,telegram,now):
    bridge_alive=0<=now-bridge.get('updated_at',0)<=45
    delivery_alive=0<=now-delivery.get('updated_at',0)<=60
    quote_time=bridge.get('quote_time');age=now-quote_time if quote_time else None
    quote_fresh=age is not None and 0<=age<=90
    last_m1=bridge.get('last_m1')
    m1_age=now-last_m1 if last_m1 is not None else None
    m1_fresh=m1_is_recent(last_m1,now)
    bridge_age=now-bridge.get('updated_at',0) if bridge.get('updated_at') else None
    bridge_label='Работает' if bridge_alive and bridge.get('state')=='running' else 'Переподключается' if bridge_alive and bridge.get('state')=='retrying' else 'Запускается' if bridge_alive and bridge.get('state')=='starting' else 'Мост не работает' if bridge_alive else 'Нет свежего статуса'
    telegram_label='Выключен' if not telegram.get('enabled') else 'Нет настроек' if not telegram.get('configured') else 'Очередь не отвечает' if not delivery_alive else 'Очередь работает' if delivery.get('state')=='running' else 'Очередь запускается' if delivery.get('state')=='starting' else 'Ошибка очереди'
    ready=bridge_alive and bridge.get('state')=='running' and quote_fresh and m1_fresh and delivery_alive and delivery.get('state')=='running' and telegram.get('enabled',False) and telegram.get('configured',False)
    if not bridge_alive:
        overall_label='Нет свежего статуса MT5';overall_color='red'
    elif bridge.get('state')=='retrying':
        overall_label='Переподключается к MT5';overall_color='yellow'
    elif bridge.get('state')=='starting':
        overall_label='Мост запускается';overall_color='yellow'
    elif bridge.get('state')!='running':
        overall_label='Мост MT5 не работает';overall_color='red'
    elif not quote_fresh:
        overall_label='Котировка устарела';overall_color='yellow'
    elif not m1_fresh:
        overall_label='Нет свежей закрытой M1';overall_color='yellow'
    elif not telegram.get('enabled'):
        overall_label='Telegram выключен';overall_color='yellow'
    elif not telegram.get('configured'):
        overall_label='Telegram не настроен';overall_color='yellow'
    elif not delivery_alive:
        overall_label='Очередь не отвечает';overall_color='red'
    elif delivery.get('state')=='starting':
        overall_label='Очередь запускается';overall_color='yellow'
    elif delivery.get('state')!='running':
        overall_label='Ошибка очереди';overall_color='red'
    else:
        overall_label='Работает';overall_color='green'
    return {'bridge':{'alive':bridge_alive,'state':bridge.get('state','unknown') if bridge_alive else 'stale',
                      'reported_state':bridge.get('state','unknown'),'label':bridge_label,
                      'updated_at':bridge.get('updated_at'),'age_seconds':bridge_age,
                      'error_type':bridge.get('error_type'),
                      'error_code':bridge.get('error_code'),
                      'outage_started_at':bridge.get('outage_started_at') or (bridge.get('updated_at') if not bridge_alive else None),
                      'last_recovery':bridge.get('last_recovery'),
                      'm1_gaps':bridge.get('m1_gaps',[])},
            'quote':{'fresh':quote_fresh,'time':quote_time,'age_seconds':age,'label':'Свежая' if quote_fresh else 'Устарела' if quote_time else 'Нет котировки'},
            'm1':{'fresh':m1_fresh,'time':last_m1,'age_seconds':m1_age,
                  'label':'Свежая закрытая M1' if m1_fresh else 'Нет свежей закрытой M1'},
            'telegram':{'enabled':telegram.get('enabled',False),'configured':telegram.get('configured',False),'worker_alive':delivery_alive,'worker_state':delivery.get('state','unknown'),'label':telegram_label},
            'overall':{'label':overall_label,'color':overall_color},
            'ready_for_new_alerts':ready}

def status(store=None,now=None):
    from .notifications import AlertStore,public_settings
    now=int(time.time() if now is None else now);store=store or AlertStore()
    result=summarize_health(read_status('bridge'),read_status('delivery'),public_settings(),now)
    with store.connect() as db:
        grouped=db.execute('SELECT status,COUNT(*) FROM alerts WHERE time>=? AND time<=? GROUP BY status',(now-86400,now)).fetchall()
        all_statuses=db.execute('SELECT status,COUNT(*) FROM alerts GROUP BY status').fetchall()
        count,first,last=db.execute('SELECT COUNT(*),MIN(time),MAX(time) FROM alerts').fetchone()
        waiting=db.execute('SELECT status,COUNT(*) FROM alerts WHERE status IN ("pending","sending") GROUP BY status').fetchall()
        latest_sent=db.execute('SELECT sent_at,telegram_message_id,kind FROM alerts WHERE status="sent" AND sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 1').fetchone()
    result.update(as_of=now,window_hours=24,journal={'total':count,'first_event':first,'last_event':last,
        'last_24h':dict(grouped),'all_statuses':dict(all_statuses),'unresolved':dict(waiting),
        'last_confirmed_send':dict(zip(('sent_at','telegram_message_id','kind'),latest_sent)) if latest_sent else None})
    result['quote_archive']=QuoteArchive().summary(now)
    result['tick_archive']=TickArchive().summary(now)
    try:result['fvg_forward']=fvg_forward_snapshot(now=now)
    except (OSError,ValueError,KeyError,TypeError):
        result['fvg_forward']={'status':'error','message':'Не удалось прочитать FVG forward-журнал'}
    return result

def export_csv(store=None):
    from .notifications import AlertStore
    store=store or AlertStore();output=io.StringIO(newline='')
    fields=['id','time_utc','observed_at_utc','tf','type','direction','price','score','rsi','atr','support','resistance','delivery','attempts','detail','sent_at_utc','telegram_message_id']
    writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
    with store.connect() as db:
        for payload,delivery,attempts,detail,sent_at,message_id in db.execute('SELECT payload,status,attempts,detail,sent_at,telegram_message_id FROM alerts ORDER BY time,id'):
            e=json.loads(payload);row={k:e.get(k,'') for k in fields}
            row.update(time_utc=dt.datetime.fromtimestamp(e['time'],dt.timezone.utc).isoformat(),delivery=delivery,attempts=attempts,detail=detail)
            row['observed_at_utc']=dt.datetime.fromtimestamp(e['observed_at'],dt.timezone.utc).isoformat() if e.get('observed_at') is not None else ''
            row['sent_at_utc']=dt.datetime.fromtimestamp(sent_at,dt.timezone.utc).isoformat() if sent_at is not None else ''
            row['telegram_message_id']=message_id if message_id is not None else ''
            # Keep text columns inert if the CSV is opened in a spreadsheet.
            writer.writerow({k:("'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@') else v) for k,v in row.items()})
    return ('\ufeff'+output.getvalue()).encode('utf-8')

def report_text(s):
    counts=s['journal']['last_24h'];stamp=dt.datetime.fromtimestamp(s['as_of'],dt.timezone.utc).isoformat()
    archive=s.get('quote_archive',{})
    ticks=s.get('tick_archive',{})
    bridge=s.get('bridge',{});recovery=bridge.get('last_recovery');gaps=bridge.get('m1_gaps',[])
    when=lambda value:dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat() if value is not None else 'неизвестно'
    incident=(f'Текущий сбой получения данных начался {when(bridge["outage_started_at"])}.'
              if bridge.get('outage_started_at') else
              f'Последний восстановленный сбой: {when(recovery["started_at"])}–{when(recovery["ended_at"])}; '
              f'длительность {recovery["duration_seconds"]} секунд.' if recovery else 'Сохранённого периода сбоя после обновления ещё нет.')
    m1=(f'Промежутков без M1 в истории MT5: {len(gaps)}; последний {when(gaps[-1]["before"])}–'
        f'{when(gaps[-1]["after"])} ({gaps[-1]["missing_minutes"]} минут). Причина не подтверждена.'
        if gaps else 'Промежутков без M1 в текущем статусе нет.')
    delivery=s['journal'].get('last_confirmed_send')
    return '\n'.join(['# Наблюдение за работой радара','',f'Снимок: {stamp}.',
        f'Общий статус: {s["overall"]["label"]}.',
        f'Мост MT5: {s["bridge"]["label"]}. Котировка: {s["quote"]["label"]}. Telegram: {s["telegram"]["label"]}.',incident,m1,'',
        f'Всего записанных событий: {s["journal"]["total"]}. За последние 24 часа: {sum(counts.values())}.',
        f'Подтверждённых доставок рыночных событий за 24 часа: {counts.get("sent",0)}. Ошибок: {counts.get("failed",0)}. Подавленных повторов: {counts.get("suppressed",0)}.',
        f'Старых ошибок доставки за всё время: {s["journal"].get("all_statuses",{}).get("failed",0)}.',
        f'Последняя отправка с подтверждением Telegram API: {when(delivery["sent_at"])}; message_id={delivery["telegram_message_id"]}.' if delivery else 'Отправок с сохранённым message_id после обновления ещё нет.',
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
