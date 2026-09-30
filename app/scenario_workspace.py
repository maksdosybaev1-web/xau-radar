"""User decisions about live research scenarios. No broker order operations."""
import csv
import io
import json
import math
import sqlite3
import time

from .datafeed import ROOT
from .notifications import AlertStore
from .operations import read_status, summarize_health, m1_is_recent
from .scenario_lifecycle import (VERSION as LIFECYCLE_VERSION, FVG_PLAN_SECONDS,
                                 SNR_RETEST_SECONDS, CONFIRMED_SECONDS)

DATABASE = ROOT / 'runtime' / 'alerts.sqlite3'
PLAN_TYPES = {'fvg_near': 'FVG', 'sbr_sell_plan': 'SBR', 'rbs_buy_plan': 'RBS'}
CONFIRMED = {'fvg_confirmed', 'sbr_sell_confirmed', 'rbs_buy_confirmed'}
ENDED = {'fvg_cancelled', 'sbr_sell_closed', 'rbs_buy_closed'}
def _identity(event):
    model = PLAN_TYPES.get(event.get('type')) or ('FVG' if event.get('type', '').startswith('fvg_')
        else 'SBR' if event.get('type', '').startswith('sbr_') else 'RBS')
    key = event.get('zone_id') if model == 'FVG' else event.get('level_id')
    source = event.get('source_hash') or event.get('source')
    return (model, source, key) if key else None

def beginner_guidance(row):
    """Explain the existing scenario checks; never invent a new trading signal."""
    c=row['checks'];buy=row['plan']['side']=='long';side='покупки' if buy else 'продажи'
    button='BUY' if buy else 'SELL';trade=row.get('trade');decision=row.get('decision') or {}
    if not c['data_ready']:
        return {'title':'Пауза: сначала восстановите данные','tone':'paused',
                'steps':[c['data_reason'],'Откройте MT5 и проверьте подключение и открытые позиции.']}
    if trade:
        if trade.get('exit_time'):
            return {'title':'Сделка записана: разберите результат','tone':'review',
                    'steps':['Сравните фактический вход с зоной сценария.',
                             'Проверьте, включены ли комиссия и прочие расходы в записанный итог.',
                             'Разберите причину результата в журнале перед следующим решением.']}
        return {'title':'Сопровождайте фактическую сделку в MT5','tone':'manage',
                'steps':['Проверьте реальный стоп, объём и цели в терминале.',
                         'Отмена сценария не закрывает вашу позицию: оцените её отдельно.',
                         'После фактического закрытия запишите цену выхода и итог брокера в форме ниже.']}
    if decision.get('action')=='skip':
        return {'title':'Вы пропустили этот сценарий','tone':'review',
                'steps':['Сохранённое решение не открывает сделку.','Следите за новым сценарием; этот разберите в журнале.']}
    if row['stage'] in ('ended','expired'):
        return {'title':'Этот план завершён: используйте его для разбора','tone':'review',
                'steps':[row['stage_reason'],'Уровни этого плана больше не действуют. Дождитесь нового сценария.']}
    if row['stage'] in ('waiting','near'):
        return {'title':f'Готовим разбор {side}: подтверждения ещё нет','tone':'waiting',
                'steps':['Возьмите сценарий в работу, если хотите следить за ним.',
                         'Дождитесь первого ретеста и подтверждения на закрытой M5.' if row['stage']=='waiting'
                         else 'Цена у зоны: дождитесь подтверждения на закрытой M5.',
                         'До подтверждения не считайте ранний план готовностью к входу.']}
    if not c['in_zone']:
        return {'title':f'Подтверждение есть: ждём цену для разбора {side}','tone':'waiting',
                'steps':['Цена сейчас вне зоны входа. Не переносите уровни вслед за ценой.',
                         'Следите за условием отмены и сроком плана.']}
    if not c['spread_ok']:
        return {'title':'Проверка входа приостановлена: спред не подходит','tone':'paused',
                'steps':['Спред слишком велик либо данных для его проверки нет.',
                         'Дождитесь повторной проверки спреда до истечения плана.']}
    if c['unknown_open_risk'] or c['max_lots_under_budget'] is None or c['max_lots_under_budget']<=0:
        return {'title':'Сначала уточните риск и объём','tone':'paused',
                'steps':['Проверьте стопы открытых позиций и параметры контракта в MT5.',
                         'При неизвестном риске или исчерпанном лимите новый объём не предлагается.']}
    return {'title':f'Условия модели выполнены: проверьте {button} вручную','tone':'ready',
            'steps':['Сверьте символ, направление, текущую цену и спред в MT5.',
                     'Укажите объём не выше расчётного ориентира; проверьте стоп и цели в окне заявки.',
                     f'Если принимаете риск, самостоятельно нажмите {button} в MT5.',
                     'После исполнения внесите фактическую цену, лоты и время в форму ниже.'],
            'note':'Совпадение условий модели не измеряет вероятность прибыли. Объём рассчитан по лимиту 0,25% капитала без гэпов, комиссии и проскальзывания.'}

class ScenarioWorkspace:
    def __init__(self, path=DATABASE):
        self.store = AlertStore(path)
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS scenario_decisions
                (scenario_id TEXT PRIMARY KEY, action TEXT NOT NULL, decided_at INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS scenario_trades
                (scenario_id TEXT PRIMARY KEY, entry_time INTEGER NOT NULL, entry REAL NOT NULL,
                 lots REAL NOT NULL, exit_time INTEGER, exit_price REAL, net_pnl REAL,
                 recorded_at INTEGER NOT NULL)''')
            columns={r[1] for r in db.execute('PRAGMA table_info(scenario_trades)')}
            for name,kind in [('mt5_ticket','INTEGER'),('mt5_position_id','INTEGER'),('mt5_account_id','TEXT')]:
                if name not in columns:db.execute(f'ALTER TABLE scenario_trades ADD COLUMN {name} {kind}')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS scenario_mt5_position
                ON scenario_trades(mt5_account_id,mt5_position_id) WHERE mt5_position_id IS NOT NULL''')

    def _events(self, now):
        with self.store.connect() as db:
            rows = db.execute('''SELECT payload,status FROM alerts WHERE time BETWEEN ? AND ?
                AND (kind GLOB 'fvg_near:*' OR kind GLOB 'fvg_confirmed:*'
                  OR kind GLOB 'fvg_cancelled:*' OR kind GLOB 'sbr_sell_plan:*'
                  OR kind GLOB 'sbr_sell_closed:*' OR kind='sbr_sell_confirmed'
                  OR kind GLOB 'rbs_buy_plan:*' OR kind GLOB 'rbs_buy_closed:*'
                  OR kind='rbs_buy_confirmed') ORDER BY time,id''',
                (now - 90 * 86400, now)).fetchall()
        return [dict(json.loads(payload), delivery=status) for payload, status in rows]

    def _rows(self, now):
        scenarios = []
        latest = {}
        for event in self._events(now):
            key = _identity(event)
            if not key:
                continue
            kind = event.get('type')
            if kind in PLAN_TYPES and isinstance(event.get('analysis_plan'), dict):
                row = {'id': event['id'], 'model': key[0], 'key': key[2],
                       'symbol': event.get('symbol', 'XAUUSD'), 'source': event.get('source'),
                       'created_at': event['time'],
                       'plan': event['analysis_plan'], 'reason': event.get('reason', ''),
                       'scenario_lifecycle': event.get('scenario_lifecycle'),
                       'plan_type': kind, 'plan_delivery': event['delivery'],
                       'terminal': None, 'confirmed': None}
                scenarios.append(row)
                latest[key] = row
            else:
                # Older alerts lacked the source hash. Attach only if exactly
                # one scenario has this model, level ID and visible source.
                if key not in latest and kind in CONFIRMED | ENDED:
                    matches = [k for k, candidate in latest.items()
                               if k[0] == key[0] and k[2] == key[2]
                               and (not event.get('source') or candidate['source'] == event['source'])]
                    if len(matches) == 1:
                        key = matches[0]
                if key not in latest or event['time'] < latest[key]['created_at']:
                    continue
                if kind in CONFIRMED:
                    latest[key]['confirmed'] = event
                elif kind in ENDED:
                    latest[key]['terminal'] = event
        with self.store.connect() as db:
            decisions = {r[0]: {'action': r[1], 'time': r[2]} for r in db.execute(
                'SELECT scenario_id,action,decided_at FROM scenario_decisions')}
            trades = {r[0]: {'entry_time': r[1], 'entry': r[2], 'lots': r[3],
                              'exit_time': r[4], 'exit_price': r[5], 'net_pnl': r[6],
                              'mt5_ticket':r[7],'mt5_position_id':r[8]}
                      for r in db.execute('SELECT scenario_id,entry_time,entry,lots,exit_time,exit_price,net_pnl,mt5_ticket,mt5_position_id FROM scenario_trades')}
        for row in scenarios:
            terminal, confirmed = row.pop('terminal'), row.pop('confirmed')
            last_time = confirmed['time'] if confirmed else row['created_at']
            lifetime = (CONFIRMED_SECONDS if confirmed else FVG_PLAN_SECONDS if row['model'] == 'FVG'
                        else SNR_RETEST_SECONDS)
            latest_event = terminal or confirmed or row
            lifecycle = latest_event.get('scenario_lifecycle')
            expected_stage = 'ended' if terminal else 'confirmed' if confirmed else (
                'near' if row['model'] == 'FVG' else 'waiting')
            valid_until = (lifecycle.get('valid_until') if isinstance(lifecycle, dict)
                           and lifecycle.get('version') == LIFECYCLE_VERSION
                           and lifecycle.get('stage') == expected_stage else None)
            expiry_at = (valid_until if type(valid_until) is int and valid_until >= last_time
                         else last_time + lifetime)
            row['valid_until'] = None if terminal else expiry_at
            expiry_reason = (lifecycle.get('expiry_reason') if expiry_at == valid_until
                             and isinstance(lifecycle.get('expiry_reason'), str) else None)
            row.pop('scenario_lifecycle', None)
            if terminal:
                row.update(stage='ended', stage_at=terminal['time'], stage_reason=terminal.get('reason', 'Сценарий завершён'))
            elif now > expiry_at:
                row.update(stage='expired', stage_at=expiry_at,
                           stage_reason=expiry_reason or ('Истёк срок ожидания первого ретеста (60 минут); нужен новый план'
                                         if row['model'] in ('SBR', 'RBS') and not confirmed else
                                         'Плану больше 15 минут; актуальность требует нового анализа'))
            elif confirmed:
                row.update(stage='confirmed', stage_at=confirmed['time'], stage_reason=confirmed.get('reason', 'M5 подтвердила условия'))
            else:
                row.update(stage='near' if row['model'] == 'FVG' else 'waiting',
                           stage_at=row['created_at'], stage_reason=row['reason'])
            row['decision'] = decisions.get(row['id'])
            row['trade'] = trades.get(row['id'])
            row['confirmation'] = ({'time': confirmed['time'], 'price': confirmed.get('price'),
                                    'stop': confirmed.get('stop'), 'target': confirmed.get('target'),
                                    'delivery': confirmed.get('delivery')}
                                   if confirmed else None)
        return sorted(scenarios, key=lambda r: (r['created_at'], r['id']), reverse=True)

    def view(self, now=None, *, live=None):
        now = int(time.time()) if now is None else int(now)
        rows = self._rows(now)
        live_path = ROOT / 'results' / 'live.json'
        if live is not None:
            pass
        elif live_path.exists():
            try:
                live = json.loads(live_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                live = {}
        else:
            live = {}
        quote = live.get('quote') or {}
        quote_time = int(live.get('quote_time') or 0)
        fresh = 0 <= now - quote_time <= 90 and bool(quote)
        health = summarize_health(read_status('bridge',root=ROOT), {}, {}, now)
        bridge_running = health['bridge']['alive'] and health['bridge']['state']=='running'
        last_m1 = (live.get('source') or {}).get('last')
        m1_fresh = m1_is_recent(last_m1,now)
        data_ready = bool(fresh and bridge_running and m1_fresh)
        data_reason = ('Нет свежего статуса моста MT5; проверка сценария приостановлена.'
                       if not health['bridge']['alive'] else
                       'Мост MT5 не получает новые данные; проверка сценария приостановлена.'
                       if not bridge_running else
                       'Ждём свежую котировку MT5; проверка входа приостановлена.'
                       if not fresh else
                       'Нет свежей закрытой M1; проверка сценария приостановлена.'
                       if not m1_fresh else '')
        account = live.get('actual_positions') or {}
        symbol = live.get('symbol_details') or {}
        equity, known = account.get('equity'), account.get('known_risk')
        unknown = int(account.get('unknown_count') or 0)
        budget = equity * 0.0025 if isinstance(equity, (float, int)) and equity > 0 else None
        headroom = max(0, budget - known) if budget is not None and isinstance(known, (float, int)) and not unknown else None
        for row in rows:
            candidates=[p for p in account.get('positions',[]) if p.get('comment')==row['id']
                        and p.get('symbol')==row['symbol']
                        and p.get('direction')==('buy' if row['plan']['side']=='long' else 'sell')
                        and type(p.get('ticket')) is int and p['ticket']>0
                        and type(p.get('position_id')) is int and p['position_id']>0]
            row['mt5_position']=None
            row['mt5_link_reason']='Укажите точный ID сценария в комментарии заявки MT5: '+row['id']
            if not data_ready:
                row['mt5_link_reason']='Подтягивание позиции приостановлено до восстановления данных MT5.'
            elif len(candidates)>1:
                row['mt5_link_reason']='Несколько позиций с этим комментарием: используйте ручную запись.'
            elif len(candidates)==1 and account.get('account_id'):
                p=candidates[0]
                row['mt5_position']={k:p.get(k) for k in ('ticket','position_id','entry','lots','stop')}
                row['mt5_link_reason']='Найдена позиция MT5 по точному комментарию. Сверьте время и данные перед записью.'
            plan = row['plan']
            low, high = plan['entry']
            side = plan['side']
            price = quote.get('ask' if side == 'long' else 'bid')
            spread = quote.get('spread')
            distance = abs((low + high) / 2 - plan['stop'])
            in_zone = bool(fresh and isinstance(price, (float, int)) and low <= price <= high)
            spread_ok = bool(fresh and isinstance(spread, (float, int)) and distance > 0 and spread <= distance * .25)
            current = row['stage'] in ('waiting', 'near', 'confirmed') and data_ready
            contract = symbol.get('contract_size')
            loss_per_lot = None
            if (symbol.get('symbol') == row['symbol'] and symbol.get('profit_currency') == account.get('currency')
                    and isinstance(contract, (int, float)) and math.isfinite(contract) and contract > 0):
                worst_entry = high if side == 'long' else low
                loss_per_lot = abs(worst_entry - plan['stop']) * contract
            step = symbol.get('volume_step')
            max_lots = (math.floor(headroom / loss_per_lot / step) * step
                        if headroom is not None and loss_per_lot and isinstance(step, (int, float)) and step > 0 else None)
            if max_lots is not None and max_lots < (symbol.get('volume_min') or 0):
                max_lots = 0
            row['checks'] = {'fresh': fresh, 'in_zone': in_zone, 'spread_ok': spread_ok,
                             'data_ready': data_ready, 'data_reason': data_reason,
                             'm1_fresh': m1_fresh, 'bridge_running': bridge_running,
                             'spread': spread, 'quote_price': price if fresh else None,
                             'current': current, 'open_risk': known,
                             'unknown_open_risk': unknown, 'account_equity': equity,
                             'risk_budget_025_pct': budget, 'remaining_budget': headroom,
                             'loss_per_lot_at_stop': loss_per_lot, 'max_lots_under_budget': max_lots,
                             'volume_min': symbol.get('volume_min'), 'volume_step': step,
                             'account_currency': account.get('currency'),
                             'ready_to_review_entry': bool(current and row['stage'] == 'confirmed' and in_zone
                                 and spread_ok and max_lots is not None and max_lots > 0)}
            if row['trade'] and loss_per_lot is not None:
                entry = row['trade']['entry']
                adverse = entry - plan['stop'] if side == 'long' else plan['stop'] - entry
                row['trade']['estimated_stop_loss'] = (adverse * contract * row['trade']['lots']
                    if adverse > 0 else None)
            row['guidance']=beginner_guidance(row)
        active_id = next((r['id'] for r in rows if r['stage'] in ('waiting','near','confirmed')), None)
        visible = [r for index, r in enumerate(rows) if index < 40 or r['id'] == active_id
                   or r['decision'] is not None or r['trade'] is not None]
        return {'as_of': now, 'quote_time': quote_time or None, 'quote_fresh': fresh,
                'data_ready': data_ready, 'data_reason': data_reason,
                'account_review': {'positions':len(account.get('positions') or []),
                                   'unknown_risk':unknown,'known_risk':known,
                                   'budget':budget,'headroom':headroom,'currency':account.get('currency'),
                                   'fresh':data_ready},
                'scenarios': visible, 'active_id': active_id}

    def decide(self, scenario_id, action, now=None):
        now = int(time.time()) if now is None else int(now)
        if action not in ('watch', 'skip'):
            raise ValueError('Выберите «Взять в работу» или «Пропустить»')
        view = self.view(now)
        row = next((r for r in view['scenarios'] if r['id'] == scenario_id), None)
        if not row:
            raise ValueError('Сценарий не найден в живом журнале')
        if not row['checks']['current']:
            raise ValueError('Решение можно сохранить только пока сценарий и данные MT5 актуальны')
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM scenario_decisions WHERE scenario_id=?', (scenario_id,)).fetchone():
                raise ValueError('Решение уже сохранено для этого сценария')
            db.execute('INSERT INTO scenario_decisions VALUES (?,?,?)', (scenario_id, action, now))
        return self.view(now)

    def import_position(self, scenario_id, data, now=None):
        now=int(time.time()) if now is None else int(now)
        try:live=json.loads((ROOT/'results'/'live.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):raise ValueError('Нет свежего снимка MT5') from None
        view=self.view(now,live=live)
        row=next((r for r in view['scenarios'] if r['id']==scenario_id),None)
        p=row.get('mt5_position') if row else None
        if not view['data_ready'] or not p or p['ticket']!=data.get('ticket'):
            raise ValueError('Свежая однозначная позиция не найдена; обновите карточку')
        # Account identity is taken from the same live snapshot, never from the client.
        account=live.get('actual_positions') or {}
        return self.record_trade(scenario_id,{'entry':p['entry'],'lots':p['lots'],
            'entry_time':data.get('entry_time')},now,
            position={'ticket':p['ticket'],'position_id':p['position_id'],'account_id':account['account_id']})

    def record_trade(self, scenario_id, data, now=None, *, position=None):
        now = int(time.time()) if now is None else int(now)
        row = next((r for r in self._rows(now) if r['id'] == scenario_id), None)
        if not row or not row['decision'] or row['decision']['action'] != 'watch':
            raise ValueError('Сначала возьмите этот сценарий в работу')
        try:
            entry = float(data['entry']); lots = float(data['lots']); entry_time = int(data['entry_time'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Укажите фактическую цену, объём в лотах и время входа') from None
        if not (math.isfinite(entry) and entry > 0 and math.isfinite(lots) and 0 < lots <= 100
                and row['created_at'] <= entry_time <= now + 60):
            raise ValueError('Проверьте цену, объём и время фактического входа')
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM scenario_trades WHERE scenario_id=?', (scenario_id,)).fetchone():
                raise ValueError('Фактический вход уже сохранён для сценария')
            position=position or {}
            try:
                db.execute('''INSERT INTO scenario_trades
                    (scenario_id,entry_time,entry,lots,recorded_at,mt5_ticket,mt5_position_id,mt5_account_id)
                    VALUES (?,?,?,?,?,?,?,?)''',
                    (scenario_id,entry_time,entry,lots,now,position.get('ticket'),
                     position.get('position_id'),position.get('account_id')))
            except sqlite3.IntegrityError:
                raise ValueError('Эта позиция MT5 уже привязана к сценарию') from None
        return self.view(now)

    def close_trade(self, scenario_id, data, now=None):
        now = int(time.time()) if now is None else int(now)
        try:
            exit_price = float(data['exit_price']); net_pnl = float(data['net_pnl']); exit_time = int(data['exit_time'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Укажите фактическую цену выхода, итог брокера и время') from None
        if not (math.isfinite(exit_price) and exit_price > 0 and math.isfinite(net_pnl) and abs(net_pnl) <= 1e9):
            raise ValueError('Некорректная цена выхода или итог')
        with self.store.connect() as db:
            trade = db.execute('SELECT entry_time,exit_time FROM scenario_trades WHERE scenario_id=?', (scenario_id,)).fetchone()
            if not trade or trade[1] is not None or not trade[0] <= exit_time <= now + 60:
                raise ValueError('Сделка не найдена, уже закрыта или время выхода некорректно')
            db.execute('UPDATE scenario_trades SET exit_time=?,exit_price=?,net_pnl=? WHERE scenario_id=?',
                       (exit_time, exit_price, net_pnl, scenario_id))
        return self.view(now)

    def export_csv(self, now=None):
        rows = self._rows(int(time.time()) if now is None else int(now))
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['scenario_id', 'model', 'created_utc', 'stage', 'decision', 'decision_utc',
                         'entry_utc', 'entry_price', 'lots', 'exit_utc', 'exit_price', 'net_pnl_broker',
                         'mt5_ticket','mt5_position_id'])
        for row in rows:
            decision, trade = row['decision'] or {}, row['trade'] or {}
            writer.writerow([row['id'], row['model'], row['created_at'], row['stage'],
                             decision.get('action'), decision.get('time'), trade.get('entry_time'),
                             trade.get('entry'), trade.get('lots'), trade.get('exit_time'),
                             trade.get('exit_price'), trade.get('net_pnl'),
                             trade.get('mt5_ticket'),trade.get('mt5_position_id')])
        return output.getvalue().encode('utf-8-sig')
