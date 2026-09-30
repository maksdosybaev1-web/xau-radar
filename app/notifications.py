"""Local alert journal and optional Telegram outbox; never places orders."""
import base64, ctypes, datetime as dt, json, math, os, re, sqlite3, threading, time
from decimal import Decimal
import urllib.request, urllib.error
from contextlib import contextmanager
from .datafeed import ROOT
from .market_radar import CFG

SETTINGS=ROOT/'runtime'/'telegram-settings.json'
DATABASE=ROOT/'runtime'/'alerts.sqlite3'
LOCK=threading.Lock()


def protect(value,decode=False):
    """Windows DPAPI, bound to the current Windows user."""
    if os.name!='nt':raise ValueError('Для хранения токена требуется Windows DPAPI')
    class Blob(ctypes.Structure):
        _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value) if decode else value.encode('utf-8')
    buffer=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    fn=crypt.CryptUnprotectData if decode else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(Blob)]
    fn.restype=ctypes.c_int
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ValueError('Не удалось обработать локально защищённые настройки')
    try:result=ctypes.string_at(target.data,target.size)
    finally:kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))
    return result.decode('utf-8') if decode else base64.b64encode(result).decode('ascii')


def settings():
    if not SETTINGS.exists():return {'enabled':False,'token':'','chat_id':''}
    raw=json.loads(SETTINGS.read_text(encoding='utf-8'))
    secret=json.loads(protect(raw['protected'],True)) if raw.get('protected') else {'token':'','chat_id':''}
    return dict(secret,enabled=bool(raw.get('enabled')))


def public_settings():
    try:
        s=settings();return {'enabled':s['enabled'],'configured':bool(s.get('token') and s.get('chat_id')),
                             'chat_id':s.get('chat_id',''),'storage':'Windows DPAPI',
                             'message':'Доставка включена для новых событий' if s['enabled'] else 'Telegram выключен. Локальная лента работает.'}
    except (OSError,ValueError,KeyError):return {'enabled':False,'configured':False,'message':'Не удалось прочитать настройки Telegram'}


def save_settings(payload):
    with LOCK:
        old=settings();token=str(payload.get('token','')).strip() or old.get('token','')
        chat=str(payload.get('chat_id','')).strip() or old.get('chat_id','')
        enabled=payload.get('enabled') is True
        if token and not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{20,}',token):raise ValueError('Проверьте формат токена бота')
        if chat and not re.fullmatch(r'-?\d{1,20}',chat):raise ValueError('Укажите числовой chat_id вашего чата')
        if enabled and not (token and chat):raise ValueError('Для включения нужны токен и chat_id')
        encoded=protect(json.dumps({'token':token,'chat_id':chat})) if token or chat else ''
        SETTINGS.parent.mkdir(exist_ok=True)
        temp=SETTINGS.with_suffix('.tmp');temp.write_text(json.dumps({'enabled':enabled,'protected':encoded}),encoding='utf-8');temp.replace(SETTINGS)
    return public_settings()


class DeliveryError(Exception):
    def __init__(self,message,retry_after=None):super().__init__(message);self.retry_after=retry_after


def send_telegram(text,config=None):
    config=config or settings()
    if not config.get('token') or not config.get('chat_id'):raise DeliveryError('Telegram не настроен')
    request=urllib.request.Request('https://api.telegram.org/bot'+config['token']+'/sendMessage',
        data=json.dumps({'chat_id':config['chat_id'],'text':text[:4000],'disable_web_page_preview':True,
                         'disable_notification':config.get('disable_notification',True)}).encode(),
        headers={'Content-Type':'application/json'})
    try:
        # Telegram is reachable directly on this host; the inherited system
        # proxy currently refuses connections. Keep this choice local to the bot.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=8) as response:answer=json.load(response)
        if not answer.get('ok'):raise DeliveryError('Telegram отклонил сообщение')
        message_id=answer.get('result',{}).get('message_id')
        if not isinstance(message_id,int) or message_id<=0:
            raise DeliveryError('Telegram ответил без ID сообщения; результат доставки неизвестен')
        return message_id
    except urllib.error.HTTPError as exc:
        if exc.code==429:
            try:delay=int(json.loads(exc.read()).get('parameters',{}).get('retry_after',30))
            except (ValueError,TypeError):delay=30
            exc.close()
            raise DeliveryError('Telegram ограничил частоту',max(1,delay)) from None
        exc.close()
        raise DeliveryError(f'Telegram: HTTP {exc.code}. Проверьте токен, chat_id и запуск бота командой /start.') from None
    except (OSError,ValueError,KeyError) as exc:
        # A timeout may follow successful delivery. Do not retry blindly and duplicate it.
        # Exception messages may contain the bot token URL; persist only a safe type.
        cause=type(getattr(exc,'reason',exc)).__name__
        raise DeliveryError('Нет подтверждения доставки ('+cause+'). Автоповтор отключён, чтобы не дублировать сообщение.') from None


def format_trade_plan(plan):
    """Render explicitly supplied levels; never calculate targets or cancellation."""
    side = plan.get('side')
    if side not in ('long', 'short'):
        raise ValueError('Укажите лонг или шорт')
    try:
        low, high = (float(value) for value in plan['entry'])
        stop = float(plan['stop'])
        targets = [float(value) for value in plan['targets']]
    except (KeyError, TypeError, ValueError):
        raise ValueError('Укажите числовые вход, стоп и три цели') from None
    if (not all(math.isfinite(value) and value > 0 for value in (low, high, stop, *targets))
            or low >= high or len(targets) != 3):
        raise ValueError('Ожидаются диапазон входа и три конечные цели')
    if side == 'long' and not (stop < low and high < targets[0] < targets[1] < targets[2]):
        raise ValueError('Для лонга стоп должен быть ниже входа, а цели — выше')
    if side == 'short' and not (targets[2] < targets[1] < targets[0] < low and high < stop):
        raise ValueError('Для шорта стоп должен быть выше входа, а цели — ниже')
    cancel = plan.get('cancel_rule')
    if not isinstance(cancel, str) or not cancel.strip() or len(cancel) > 160 or '\n' in cancel or '\r' in cancel:
        raise ValueError('Укажите одно короткое условие отмены')
    fmt = lambda value: format(Decimal(str(value)).normalize(), 'f')
    name = 'Лонг' if side == 'long' else 'Шорт'
    retest = ' на ретесте' if plan.get('retest') else ''
    return (f'{name} {fmt(low)}–{fmt(high)}{retest}\n'
            f'Стоп {fmt(stop)}\n'
            f'ТП {", ".join(map(fmt, targets))}\n'
            f'Отмена: {cancel.strip()}')


def format_alert(event):
    value=lambda k:'—' if event.get(k) is None else f'{event[k]:.2f}'
    stamp=dt.datetime.fromtimestamp(event['time'],dt.timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
    lifecycle=event.get('scenario_lifecycle') or {}
    deadline=lifecycle.get('valid_until')
    deadline_note=('\nАктуален до '+dt.datetime.fromtimestamp(deadline,dt.timezone.utc).strftime('%d.%m.%Y %H:%M UTC')+
                   ', если не отменён раньше.' if type(deadline) is int and deadline>event['time'] else '')
    scenario=(event.get('level_id') or event.get('zone_id') or '—')[:12]
    if event['type'] in ('sbr_sell_plan','rbs_buy_plan'):
        body=format_trade_plan(event['analysis_plan'])
        model='SBR' if event['type']=='sbr_sell_plan' else 'RBS'
        action='продажу' if model=='SBR' else 'покупку'
        why=event.get('reason') or ('Цена пробила поддержку вниз; ждём возврата к ней снизу.'
             if event['type']=='sbr_sell_plan' else
             'Цена пробила сопротивление вверх; ждём возврата к нему сверху.')
        target_note=(' ТП рассчитаны как 1R/2R/3R; структурную цель M15 проверим после ретеста.'
                     if event['analysis_plan'].get('target_method')=='midpoint_r_1_2_3' else '')
        return (f"{event.get('symbol','XAUUSD')} · {model} · предварительно · {stamp}\n"
                f"Сценарий: {model}-{scenario}\n"
                f"Проверьте возможную {action}: ждём возврата цены к зоне.\n"+body+
                '\nПочему: '+why+
                '\nСледующая проверка: первый ретест уровня и закрытие M5.'+
                '\nЗона входа, стоп и ТП — расчётные ориентиры.'+target_note+
                ' Ретест ещё не подтверждён; заявки нет.'+deadline_note)
    if event['type'] in ('sbr_sell_closed','rbs_buy_closed'):
        model='SBR' if event['type']=='sbr_sell_closed' else 'RBS'
        return (f"{event.get('symbol','XAUUSD')} · {model} · план завершён · {stamp}\n"
                f"Сценарий: {model}-{scenario}\nПочему: {event['reason']}\n"
                'Ранний план больше не действует. Это не закрытие сделки у брокера.')
    if event['type']=='fvg_cancelled':
        direction='BUY' if event.get('direction')=='buy' else 'SELL'
        return (f"{event['symbol']} · FVG {direction} · M5\nСценарий отменён · {stamp}\n"
                f"Сценарий: FVG-{scenario}\n"
                f"Зона {event.get('zone_id','—')}: {value('low')}–{value('high')}\n"
                f"Причина: {event['reason']}\n"
                "Наблюдение за этим сценарием завершено. Это не сообщение о закрытии позиции у брокера.")
    if event['type'] in ('fvg_near','fvg_confirmed'):
        direction='BUY' if event.get('direction')=='buy' else 'SELL'
        if event['type']=='fvg_near' and event.get('analysis_plan'):
            plan=event['analysis_plan']
            try:
                body=format_trade_plan(plan)
            except ValueError:
                body=None
            if body is not None:
                action='покупку' if direction=='BUY' else 'продажу'
                why=event.get('reason') or 'Цена приблизилась к зоне быстрого движения; ждём подтверждения на M5.'
                target_note=(' Цели 1R/2R/3R рассчитаны от середины зоны и стопа.'
                             if plan.get('target_method')=='midpoint_r_1_2_3' else '')
                return (f"{event['symbol']} · FVG · предварительно · {stamp}\n"
                        f"Сценарий: FVG-{scenario}\n"
                        f"Проверьте возможную {action}: цена подошла к зоне.\n"+body+
                        '\nПочему: '+why+
                        '\nСледующая проверка: подтверждение на закрытой M5.'+
                        '\nЗона входа, стоп и ТП — расчётные ориентиры.'+target_note+
                        ' Вход ещё не подтверждён; заявки нет.'+
                        deadline_note)
        state='Цена приблизилась' if event['type']=='fvg_near' else 'M5 подтвердила условия'
        same_plan=('\n'+format_trade_plan(event['analysis_plan']) if event['type']=='fvg_confirmed' and event.get('analysis_plan') else '')
        stop=f"\nРасчётный стоп: {value('stop')}" if event['type']=='fvg_confirmed' else ''
        return (f"{event['symbol']} · FVG {direction} · M5\n{state} · {stamp}\n"
                f"Сценарий: FVG-{scenario}\n"
                f"Старшая H1-зона: {value('low')}–{value('high')}\n"
                f"Вложенная M15-зона: {value('child_low')}–{value('child_high')}"
                f"{stop}{same_plan}\nПочему: {event['reason']}\n"
                "Исследовательское наблюдение. Это не вход и не заявка брокеру.")
    if event['type']=='sbr_sell_confirmed':
        same_plan=('\n'+format_trade_plan(event['analysis_plan']) if event.get('analysis_plan') else '')
        objective=(f"Дополнительная структурная цель: {value('target')}\n" if same_plan else '')
        return (f"{event.get('symbol','XAUUSD')} · SBR · {stamp}\n"
                f"Сценарий: SBR-{scenario}\nШорт · ретест подтверждён M5\nЦена закрытия {value('price')}\n"
                f"{same_plan or 'Стоп '+value('stop')+'; ТП '+value('target')}\n"
                f"{objective}"
                "Почему: "+(event.get('reason') or 'После возврата к пробитой поддержке свеча M5 закрылась под ней.')+"\n"
                "Следующая проверка: текущая цена в зоне входа, спред и риск открытых позиций в MT5.\n"
                "Подтверждение сценария, не исполнение сделки.")
    if event['type']=='rbs_buy_confirmed':
        same_plan=('\n'+format_trade_plan(event['analysis_plan']) if event.get('analysis_plan') else '')
        objective=(f"Дополнительная структурная цель: {value('target')}\n" if same_plan else '')
        return (f"{event.get('symbol','XAUUSD')} · RBS · {stamp}\n"
                f"Сценарий: RBS-{scenario}\nЛонг · ретест подтверждён M5\nЦена закрытия {value('price')}\n"
                f"{same_plan or 'Стоп '+value('stop')+'; ТП '+value('target')}\n"
                f"{objective}"
                "Почему: "+(event.get('reason') or 'После возврата к пробитому сопротивлению свеча M5 закрылась над ним.')+"\n"
                "Следующая проверка: текущая цена в зоне входа, спред и риск открытых позиций в MT5.\n"
                "Подтверждение сценария, не исполнение сделки.")
    price_label='Bid' if event.get('price_kind')=='bid' else 'Цена закрытия'
    indicator_label='Совпали индикаторные условия · проверьте вручную' if event['type']=='watch' else event['label']
    score_note=('Условия совпали; это не подтверждённый вход.' if event['type']=='watch'
                else 'Отдельное индикаторное событие; это не подтверждённый вход.')
    return (f"XAU/USD · ИНДИКАТОРЫ · {event['tf']}\n{indicator_label}\n{stamp}\n"
            f"{price_label}: {value('price')}\nRSI: {value('rsi')}\nEMA20 / EMA50: {value('ema20')} / {value('ema50')}\n"
            f"ATR: {value('atr')}\nПоддержка / сопротивление: {value('support')} / {value('resistance')}\n"
            f"Оценка условий: {event['score']}/100 · {score_note}\n{event['reason']}\n"
            'Оценка не является вероятностью прибыли. Заявка брокеру не отправлена.')


class AlertStore:
    def __init__(self,path=None):
        self.path=path or DATABASE;self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, time INTEGER, tf TEXT, kind TEXT, payload TEXT, status TEXT, attempts INTEGER DEFAULT 0, next_attempt INTEGER DEFAULT 0, detail TEXT DEFAULT "", sent_at INTEGER, telegram_message_id INTEGER)')
            columns={row[1] for row in db.execute('PRAGMA table_info(alerts)')}
            if not {'sent_at','telegram_message_id'}<=columns:
                db.execute('BEGIN IMMEDIATE')
                columns={row[1] for row in db.execute('PRAGMA table_info(alerts)')}
                if 'sent_at' not in columns:db.execute('ALTER TABLE alerts ADD COLUMN sent_at INTEGER')
                if 'telegram_message_id' not in columns:db.execute('ALTER TABLE alerts ADD COLUMN telegram_message_id INTEGER')
            db.execute('CREATE INDEX IF NOT EXISTS alert_time ON alerts(time)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()

    def add(self,event,now,quote_time,enabled=False):
        if event['time']>now or now-event['time']>CFG['max_quote_age_seconds'] or not 0<=now-quote_time<=CFG['max_quote_age_seconds']:return False
        kind=event.get('cooldown_key') or event['type']
        with self.connect() as db:
            self._expire_prior_plan(db,event)
            prior=db.execute('SELECT time FROM alerts WHERE tf=? AND kind=? AND status!="suppressed" ORDER BY time DESC LIMIT 1',(event['tf'],kind)).fetchone()
            suppressed=prior is not None and event['time']-prior[0]<CFG['alert_cooldown_seconds']
            status='suppressed' if suppressed else 'pending' if enabled else 'local_only'
            result=db.execute('INSERT OR IGNORE INTO alerts (id,time,tf,kind,payload,status) VALUES (?,?,?,?,?,?)',
                              (event['id'],event['time'],event['tf'],kind,json.dumps(dict(event,observed_at=now),ensure_ascii=False),status))
            return bool(result.rowcount)

    def add_local(self,event,observed_at):
        """Keep a late terminal observation in the scenario timeline without Telegram delivery."""
        kind=event.get('cooldown_key') or event['type']
        with self.connect() as db:
            self._expire_prior_plan(db,event)
            result=db.execute('INSERT OR IGNORE INTO alerts (id,time,tf,kind,payload,status) VALUES (?,?,?,?,?,?)',
                              (event['id'],event['time'],event['tf'],kind,
                               json.dumps(dict(event,observed_at=observed_at),ensure_ascii=False),'local_only'))
            return bool(result.rowcount)

    @staticmethod
    def _expire_prior_plan(db,event):
        earlier={'fvg_confirmed':('fvg_near:','zone_id'),
                 'fvg_cancelled':('fvg_near:','zone_id'),
                 'sbr_sell_confirmed':('sbr_sell_plan:','level_id'),
                 'sbr_sell_closed':('sbr_sell_plan:','level_id'),
                 'rbs_buy_confirmed':('rbs_buy_plan:','level_id'),
                 'rbs_buy_closed':('rbs_buy_plan:','level_id')}.get(event['type'])
        if earlier and event.get(earlier[1]):
            plan_kind=earlier[0]+event[earlier[1]]
            rows=db.execute('SELECT id,payload FROM alerts WHERE kind=? AND status="pending" AND time<=?',
                            (plan_kind,event['time'])).fetchall()
            for plan_id,payload in rows:
                previous=json.loads(payload)
                if (previous.get('symbol')==event.get('symbol')
                        and previous.get('source_hash')==event.get('source_hash')
                        and previous.get(earlier[1])==event[earlier[1]]):
                    db.execute('UPDATE alerts SET status="expired",detail=? WHERE id=? AND status="pending"',
                               ('План завершился до отправки',plan_id))

    def recent(self,limit=80):
        with self.connect() as db:
            rows=db.execute('SELECT payload,status,detail FROM alerts ORDER BY time DESC,id LIMIT ?',(limit,)).fetchall()
        return [dict(json.loads(p),delivery=s,delivery_detail=d) for p,s,d in rows]

    def plan_delivery_status(self, kind, source_hash, symbol):
        with self.connect() as db:
            rows=db.execute('SELECT payload,status FROM alerts WHERE kind=? AND status IN ("sent","sending")',
                            (kind,)).fetchall()
        matches=[status for payload,status in rows for saved in (json.loads(payload),)
                 if saved.get('source_hash') == source_hash and saved.get('symbol') == symbol]
        return 'sent' if 'sent' in matches else 'sending' if 'sending' in matches else None

    def sent_plan(self, kind, source_hash, symbol):
        return self.plan_delivery_status(kind, source_hash, symbol) == 'sent'

    def plan_for(self,event):
        """Reuse the exact early plan in a later confirmation message."""
        name=event.get('type','')
        pattern=('fvg_near:*' if name=='fvg_confirmed' else
                 'sbr_sell_plan:*' if name=='sbr_sell_confirmed' else
                 'rbs_buy_plan:*' if name=='rbs_buy_confirmed' else None)
        if pattern is None:return None
        key='zone_id' if name=='fvg_confirmed' else 'level_id'
        with self.connect() as db:
            rows=db.execute('SELECT payload FROM alerts WHERE kind GLOB ? AND time<=? ORDER BY time DESC LIMIT 100',
                            (pattern,event['time'])).fetchall()
        for (payload,) in rows:
            prior=json.loads(payload)
            if (prior.get(key)==event.get(key) and prior.get('symbol')==event.get('symbol')
                    and prior.get('source')==event.get('source')
                    and (not prior.get('source_hash') or not event.get('source_hash')
                         or prior['source_hash']==event['source_hash'])):
                return prior.get('analysis_plan')
        return None

    def drain(self,now=None,config=None,sender=None):
        now=int(now or time.time());config=config if config is not None else settings();sender=sender or send_telegram
        with self.connect() as db:
            rows=db.execute('SELECT id,time,payload,attempts FROM alerts WHERE status="pending" AND next_attempt<=? ORDER BY time LIMIT 3',(now,)).fetchall()
        for key,stamp,payload,attempts in rows:
            event=json.loads(payload)
            if now-stamp>180:
                with self.connect() as db:
                    db.execute('UPDATE alerts SET status="expired" WHERE id=? AND status="pending"',(key,))
                continue
            if event['type'] in ('sbr_sell_closed','rbs_buy_closed'):
                prefix='sbr_sell_plan:' if event['type']=='sbr_sell_closed' else 'rbs_buy_plan:'
                prior=self.plan_delivery_status(prefix+event['level_id'],
                                                event.get('source_hash'),event.get('symbol'))
                if prior=='sending':continue
                if prior!='sent':
                    with self.connect() as db:
                        db.execute('UPDATE alerts SET status="local_only",detail=? '
                                   'WHERE id=? AND status="pending"',
                                   ('Ранний план не был подтверждённо доставлен',key))
                    continue
            with self.connect() as db:
                if not config.get('enabled') or now-stamp>180:
                    db.execute('UPDATE alerts SET status=? WHERE id=? AND status="pending"',('expired' if now-stamp>180 else 'local_only',key));continue
                # Persist the attempt before network I/O; a crash will not resend an uncertain delivery.
                claimed=db.execute('UPDATE alerts SET status="sending",attempts=attempts+1 WHERE id=? AND status="pending" AND next_attempt<=?',(key,now)).rowcount
            if not claimed:continue
            try:
                if event['type'] in ('fvg_confirmed','sbr_sell_confirmed','rbs_buy_confirmed'):
                    plan=self.plan_for(event)
                    if plan:event['analysis_plan']=plan
                early_plan=event['type'] in ('sbr_sell_plan','rbs_buy_plan') or (
                    event['type']=='fvg_near' and bool(event.get('analysis_plan')))
                delivery_config=dict(config,disable_notification=not early_plan)
                message_id=sender(format_alert(event),delivery_config)
                status,detail,next_try='sent','Доставка подтверждена',0
            except DeliveryError as exc:
                next_try=now+(exc.retry_after or 0)
                status='pending' if exc.retry_after and attempts<2 and next_try-stamp<=180 else 'failed'
                detail=str(exc)
            with self.connect() as db:
                db.execute('UPDATE alerts SET status=?,detail=?,next_attempt=?,sent_at=?,telegram_message_id=? WHERE id=?',
                           (status,detail,next_try,now if status=='sent' else None,
                            message_id if status=='sent' else None,key))


def delivery_worker(stop):
    from .operations import heartbeat
    store=None
    def report(state):
        try:heartbeat('delivery',state)
        except (OSError,ValueError):pass
    while not stop.is_set():
        try:
            if store is None:store=AlertStore()
            store.drain()
        except (OSError,ValueError,sqlite3.Error):report('error')
        else:report('running')
        stop.wait(3)
    report('stopped')
