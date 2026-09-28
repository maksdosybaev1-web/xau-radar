import json, math, mimetypes, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from .datafeed import ROOT, load_csv
from .engine import Aggregator, run, summarize
from .fvg_checkpoint import summarize_curve
from . import ai_review
from . import market_radar, notifications
from . import operations, scenario_workspace
from .quote_archive import QuoteArchive

def load_result():
    path=ROOT/'results'/'run.json'
    if not path.exists():return None
    return json.loads(path.read_text(encoding='utf-8'))

RESULT=load_result()
MANUAL_RESULT=None
MANUAL_LOCK=threading.Lock()
TECH_PATH=ROOT/'results'/'technical_history.json'
TECH_HISTORY=json.loads(TECH_PATH.read_text(encoding='utf-8')) if TECH_PATH.exists() else None
SBR_LOCK=threading.Lock()
SBR_CACHE=None


def sbr_data():
    global SBR_CACHE
    with SBR_LOCK:
        path=ROOT/'results'/'snr_sbr_run.json'
        if not path.exists():return None
        source_path=ROOT/'data'/'xauusd_m1.csv'
        signature=(path.stat().st_mtime_ns,source_path.stat().st_mtime_ns)
        if SBR_CACHE is None or SBR_CACHE[2]!=signature:
            result=json.loads(path.read_text(encoding='utf-8'))
            if SBR_CACHE is not None and SBR_CACHE[2][1]==signature[1]:
                bars=SBR_CACHE[1]
            else:
                agg=Aggregator(5);bars=[]
                for row in load_csv():
                    candle=agg.add(row)
                    if candle:bars.append(candle)
            SBR_CACHE=(result,bars,signature)
    return SBR_CACHE[:2]


def sbr_state(query):
    data=sbr_data()
    if data is None:return {'ready':False,'message':'Сначала выполните python -m app.snr_sbr_research'}
    result,bars=data
    trades={t['level_id']:t for t in result['trades']}
    events_by_level={}
    for event in result['events']:
        events_by_level.setdefault(event['level_id'],[]).append(event)
    cases=[]
    for level in result['levels']:
        history=events_by_level[level['id']]
        if not any(e['state']=='confirmed' for e in history):continue
        cases.append({'id':level['id'],'pivot_time':level['pivot_bar']['time'],
                      'confirmation_time':level['confirmation_time'],'level':level['level'],
                      'state':level['state'],'reason':history[-1]['reason'],
                      'r':trades.get(level['id'],{}).get('r')})
    cases.sort(key=lambda x:x['confirmation_time'])
    identity=query.get('id',[None])[0]
    response={'ready':True,'version':result['version'],'summary':result['summary'],
              'cases':cases,'source':'Dukascopy XAUUSD bid/ask M1 · история'}
    if identity is None:return response
    level=next((x for x in result['levels'] if x['id']==identity),None)
    if level is None or identity not in {x['id'] for x in cases}:raise ValueError('Сценарий SBR не найден')
    center=level['confirmation_time']
    response['selected']={'level':level,'events':events_by_level[identity],
                          'trade':trades.get(identity),
                          'bars':[b for b in bars if level['pivot_bar']['time']-3600<=b['time']<=center+3600]}
    return response


def technical_state(query):
    source=query.get('source',['history'])[0];tf=query.get('tf',['M15'])[0]
    result=TECH_HISTORY
    if source=='live':
        path=ROOT/'results'/'technical_live.json'
        result=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if not result:return {'ready':False,'message':'Данные обзора ещё не подготовлены'}
    maximum=max((rows[-1]['end'] for rows in result['frames'].values() if rows),default=0)
    now=int(time.time()) if source=='live' else int(query.get('cursor',[maximum])[0])
    view=market_radar.view(result,now,tf)
    view.update(source=result.get('source','Dukascopy'),mode=source,
                stale=source=='live' and time.time()-result.get('heartbeat',0)>market_radar.CFG['max_quote_age_seconds'],
                quote=result.get('quote') if source=='live' else None,
                volume_kind=result.get('volume_kind','Активность поставщика Dukascopy; не общий биржевой объём золота'),
                telegram=notifications.public_settings())
    if source=='live':
        store=notifications.AlertStore()
        view['events']=store.recent()
        view['plan_events']=recent_plan_events(store,now)
    return view


def model_telegram_alerts(store, confirmed_kind, plan_pattern):
    """Show both early plans and confirmed alerts on a model's live page."""
    with store.connect() as db:
        rows=db.execute('SELECT payload,status,detail FROM alerts WHERE kind=? OR kind GLOB ? ORDER BY time DESC LIMIT 10',
                        (confirmed_kind,plan_pattern)).fetchall()
    return [dict(json.loads(payload),delivery=status,delivery_detail=detail)
            for payload,status,detail in rows]


def recent_plan_events(store, now):
    """Read the current card's plan lifecycle independently of the noisy alert feed."""
    with store.connect() as db:
        rows=db.execute('''SELECT payload,status FROM alerts WHERE time>=? AND time<=?
            AND (kind GLOB 'fvg_near:*' OR kind GLOB 'fvg_confirmed:*'
              OR kind GLOB 'fvg_cancelled:*' OR kind GLOB 'sbr_sell_plan:*'
              OR kind GLOB 'sbr_sell_closed:*' OR kind='sbr_sell_confirmed'
              OR kind GLOB 'rbs_buy_plan:*' OR kind GLOB 'rbs_buy_closed:*'
              OR kind='rbs_buy_confirmed')
            ORDER BY time DESC,id DESC LIMIT 100''',(now-15*60,now)).fetchall()
    return [dict(json.loads(payload),delivery=status) for payload,status in rows]

def manual_replay(payload):
    if RESULT is None:raise ValueError('Сначала подготовьте исторические данные')
    try:
        created=int(payload['created']);low=float(payload['low']);high=float(payload['high'])
        direction=payload['direction']
    except (KeyError,TypeError,ValueError) as exc:raise ValueError('Укажите время, направление и границы зоны') from exc
    if direction not in ('buy','sell') or not math.isfinite(low) or not math.isfinite(high) or not 0<low<high:
        raise ValueError('Нужны направление BUY/SELL и положительные границы: нижняя меньше верхней')
    if created not in {b['end'] for b in RESULT['bars']}:
        raise ValueError('Зарегистрируйте зону на закрытии доступной M5-свечи')
    zone={'direction':direction,'low':low,'high':high,'created':created,'origin':created,'touched':False}
    result=run(load_csv(),RESULT['config'],manual=[zone])
    result.update(mode='manual',evaluation=None,source=dict(RESULT['source'],source='Ручная зона · архив Dukascopy'),
                  manual_zone=zone)
    return result

def state(result,cursor=None):
    if not result or not result['bars']:return {'ready':False,'message':'История ещё не подготовлена. Из корня проекта выполните python -X utf8 -m app.datafeed, затем python -X utf8 -m app.research.'}
    forward_since=result.get('forward_since') if result.get('mode')=='live' else None
    bars=result['bars'];maximum=bars[-1]['end'];minimum=bars[0]['end']
    now=max(minimum,min(maximum,int(cursor or maximum)))
    visible=[b for b in bars if b['end']<=now]
    events=[e for e in result['events'] if e['time']<=now]
    active={}
    for e in events:
        zid=e.get('zone_id')
        if e['type']=='zone_created':active[zid]=dict(e,state='waiting')
        elif zid in active:
            if e['type']=='child_found':active[zid].update(child_low=e['child_low'],child_high=e['child_high'],state='ready')
            if e['type'] in ('cancelled','confirmed','entry_rejected'):active[zid]['state']=e['type']
    closed=[];positions=[]
    for t in result['trades']:
        if t['entry_time']>now:continue
        if forward_since is not None and t['entry_time']<forward_since:continue
        if t.get('exit_time') is not None and t['exit_time']<=now:closed.append(t)
        else:
            p={k:v for k,v in t.items() if k not in ('exit','exit_time','exit_reason','pnl','r','fees')};positions.append(p)
    curve=[x for x in result['curve'] if x['time']<=now and (forward_since is None or x['time']>=forward_since)]
    summary=summarize_curve(closed,result['config']['initial_equity'],curve,result.get('curve_prefix') if forward_since is not None else None)
    next_event=next((e['time'] for e in result['events'] if e['time']>now and e['type'] in ('near','confirmed')),None)
    point=dict(curve[-1]) if curve else {'equity':result['config']['initial_equity'],'balance':result['config']['initial_equity'],'open_risk':0}
    point['open_risk']=sum(p['risk_usd'] for p in positions)
    return {'ready':True,'time':now,'min':minimum,'max':maximum,'next_event':next_event,'forward_since':forward_since,
            'quote_time':result.get('quote_time') if result.get('mode')=='live' else None,
            'quote':result.get('quote') if result.get('mode')=='live' else None,
            'mode':result.get('mode','history'),'stale':result.get('mode')=='live' and time.time()-result.get('heartbeat',0)>90,
            'context':visible[-1]['context'] if visible else 'neutral','bars':visible[-160:],
            'zones':[z for z in active.values() if z['state'] in ('waiting','ready')][-20:],
            'events':[e for e in events if forward_since is None or e['time']>=forward_since][-100:][::-1],
            'positions':positions,'trades':closed[-100:][::-1],
            'summary':summary,'account':point,'evaluation':result.get('evaluation'),
            'source':{k:v for k,v in result.get('source',{}).items() if k in ('source','symbol','timeframe','timezone','first','last','rows','missing_archives','bid_ask_unmatched_minutes','ask_quality')},
            'ai':ai_review.status(),'config':result['config'],'actual_positions':result.get('actual_positions'),
            'curve':curve[::max(1,len(curve)//240)]}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):pass
    def send_data(self,value,status=200):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def get_result(self,query):
        if query.get('source',['history'])[0]=='manual':return MANUAL_RESULT
        if query.get('source',['history'])[0]=='live':
            path=ROOT/'results'/'live.json'
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        return RESULT
    def do_GET(self):
        if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):return self.send_data({'error':'Только локальный доступ'},403)
        parsed=urlparse(self.path);q=parse_qs(parsed.query)
        try:
            if parsed.path=='/api/state':return self.send_data(state(self.get_result(q),q.get('cursor',[None])[0]))
            if parsed.path=='/api/technical':return self.send_data(technical_state(q))
            if parsed.path=='/api/sbr':return self.send_data(sbr_state(q))
            if parsed.path=='/api/sbr-live':
                path=ROOT/'results'/'snr_sbr_live.json'
                if not path.exists():return self.send_data({'ready':False,'message':'Мост MT5 ещё не подготовил наблюдение SBR'})
                live=json.loads(path.read_text(encoding='utf-8'))
                live['bridge_stale']=time.time()-live['generated_at']>90
                live['stale']=time.time()-live['quote_time']>90
                telegram=notifications.public_settings()
                live['telegram']={'enabled':telegram['enabled'],'configured':telegram['configured']}
                live['telegram_alerts']=model_telegram_alerts(
                    notifications.AlertStore(),'sbr_sell_confirmed','sbr_sell_plan:*')
                return self.send_data(live)
            if parsed.path=='/api/rbs-live':
                path=ROOT/'results'/'snr_rbs_live.json'
                if not path.exists():return self.send_data({'ready':False,'message':'Мост MT5 ещё не подготовил наблюдение RBS'})
                live=json.loads(path.read_text(encoding='utf-8'))
                live['bridge_stale']=time.time()-live['generated_at']>90
                live['stale']=time.time()-live['quote_time']>90
                telegram=notifications.public_settings()
                live['telegram']={'enabled':telegram['enabled'],'configured':telegram['configured']}
                live['telegram_alerts']=model_telegram_alerts(
                    notifications.AlertStore(),'rbs_buy_confirmed','rbs_buy_plan:*')
                return self.send_data(live)
            if parsed.path=='/api/nested-live':
                path=ROOT/'results'/'nested_forward_live.json'
                if not path.exists():return self.send_data({'ready':False,'message':'Мост MT5 ещё не подготовил вложенное наблюдение'})
                live=json.loads(path.read_text(encoding='utf-8'))
                live['bridge_stale']=time.time()-live['generated_at']>90
                live['stale']=time.time()-live['quote_time']>90
                return self.send_data(live)
            if parsed.path=='/api/telegram':return self.send_data(notifications.public_settings())
            if parsed.path=='/api/operations':return self.send_data(operations.status())
            if parsed.path=='/api/scenarios':return self.send_data(scenario_workspace.ScenarioWorkspace().view())
            if parsed.path=='/download/scenarios.csv':
                data=scenario_workspace.ScenarioWorkspace().export_csv()
                self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="scenarios.csv"')
                self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(data);return
            if parsed.path in ('/download/live_alerts.csv','/download/live_quotes.csv','/download/observation_report.md'):
                data=QuoteArchive().export_csv() if parsed.path.endswith('live_quotes.csv') else operations.export_csv() if parsed.path.endswith('.csv') else operations.report_text(operations.status()).encode('utf-8')
                self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8' if parsed.path.endswith('.csv') else 'text/markdown; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="'+parsed.path.rsplit('/',1)[-1]+'"')
                self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data);return
            if parsed.path=='/api/technical-validation':
                path=ROOT/'results'/'technical_validation.json'
                return self.send_data(json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'ready':False})
            if parsed.path=='/api/health':return self.send_data({'ok':True,'history_ready':RESULT is not None,'ai':ai_review.status()})
            if parsed.path=='/api/rules':return self.send_data({'text':(ROOT/'docs'/'SPEC.md').read_text(encoding='utf-8')})
            downloads={'/download/trades.csv':ROOT/'results'/'trades.csv','/download/events.jsonl':ROOT/'results'/'events.jsonl','/download/validation.md':ROOT/'results'/'validation.md'}
            downloads['/download/fvg_forward_events.jsonl']=ROOT/'results'/'forward_events.jsonl'
            downloads['/download/technical_validation.md']=ROOT/'results'/'technical_validation.md'
            downloads.update({'/download/sbr_validation.md':ROOT/'results'/'snr_sbr_validation.md',
                              '/download/sbr_events.jsonl':ROOT/'results'/'snr_sbr_events.jsonl',
                              '/download/sbr_forward_events.jsonl':ROOT/'results'/'snr_sbr_forward_events.jsonl',
                              '/download/sbr_spec.md':ROOT/'docs'/'SNR_SBR_SPEC.md'})
            downloads.update({'/download/rbs_validation.md':ROOT/'results'/'snr_rbs_validation.md',
                              '/download/rbs_events.jsonl':ROOT/'results'/'snr_rbs_events.jsonl',
                              '/download/rbs_forward_events.jsonl':ROOT/'results'/'snr_rbs_forward_events.jsonl',
                              '/download/rbs_spec.md':ROOT/'docs'/'SNR_RBS_SPEC.md'})
            downloads.update({'/download/nested_forward_events.jsonl':ROOT/'results'/'nested_forward_events.jsonl',
                              '/download/nested_forward_outcomes.jsonl':ROOT/'results'/'nested_forward_outcomes.jsonl',
                              '/download/nested_forward_tick_checks.jsonl':ROOT/'results'/'nested_forward_tick_checks.jsonl',
                              '/download/nested_spec.md':ROOT/'docs'/'NESTED_M15_M5_M1_SPEC.md',
                              '/download/entry_modes_spec.md':ROOT/'docs'/'ENTRY_MODES_SPEC.md'})
            file=downloads.get(parsed.path)
            if not file:
                names={'/':'index.html','/app.js':'app.js','/market.js':'market.js','/scenarios.js':'scenarios.js','/operations.js':'operations.js','/style.css':'style.css','/sbr.html':'sbr.html','/sbr.js':'sbr.js','/rbs.html':'rbs.html','/rbs.js':'rbs.js'}
                if parsed.path not in names:return self.send_data({'error':'Не найдено'},404)
                file=ROOT/'web'/names[parsed.path]
            if not file.exists():return self.send_data({'error':'Файл ещё не подготовлен'},404)
            data=file.read_bytes();self.send_response(200);self.send_header('Content-Type',(mimetypes.guess_type(str(file))[0] or 'text/plain')+'; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        except (ValueError,KeyError) as exc:self.send_data({'error':str(exc)},400)
    def do_POST(self):
        try:
            host=self.headers.get('Host','');origin=self.headers.get('Origin')
            if host.split(':')[0] not in ('127.0.0.1','localhost'):return self.send_data({'error':'Только локальный доступ'},403)
            if origin and origin!=f'http://{host}':return self.send_data({'error':'Недопустимый источник запроса'},403)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_data({'error':'Ожидается JSON'},415)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:return self.send_data({'error':'Некорректный размер запроса'},400)
            payload=json.loads(self.rfile.read(size))
            if self.path in ('/api/scenario-decision','/api/scenario-entry','/api/scenario-exit'):
                workspace=scenario_workspace.ScenarioWorkspace()
                identity=payload.get('scenario_id')
                if not isinstance(identity,str) or len(identity)>160 or not identity:
                    raise ValueError('Укажите идентификатор сценария')
                if self.path=='/api/scenario-decision':return self.send_data(workspace.decide(identity,payload.get('action')))
                if self.path=='/api/scenario-entry':return self.send_data(workspace.record_trade(identity,payload))
                return self.send_data(workspace.close_trade(identity,payload))
            if self.path=='/api/telegram':return self.send_data(notifications.save_settings(payload))
            if self.path=='/api/telegram-test':
                try:notifications.send_telegram('XAU/USD RADAR: проверка доставки. Это тестовое сообщение, не рыночный сигнал.')
                except notifications.DeliveryError as exc:raise ValueError(str(exc)) from None
                return self.send_data({'ok':True,'message':'Тестовое сообщение доставлено'})
            if self.path=='/api/explain':
                result=self.get_result({'source':[payload.get('source','history')]})
                if result is None:raise ValueError('Данных нет')
                return self.send_data(ai_review.explain(result,int(payload['event_id'])))
            if self.path=='/api/manual-zone':
                global MANUAL_RESULT
                with MANUAL_LOCK:
                    MANUAL_RESULT=manual_replay(payload)
                return self.send_data({'ok':True,'registered':MANUAL_RESULT['manual_zone']['created']})
            self.send_data({'error':'Не найдено'},404)
        except (ValueError,KeyError,OSError) as exc:self.send_data({'error':str(exc)},400)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8767);args=p.parse_args()
    print(f'Радар: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
