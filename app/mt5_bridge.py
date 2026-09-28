"""Read-only MT5 bridge. Imports optional MetaTrader5; no trade API calls."""
import argparse, datetime as dt, json, math, time, threading, sqlite3
from .datafeed import ROOT
from .engine import Radar
from .research import save_json
from .live_market import LiveMarket
from .notifications import delivery_worker
from .operations import heartbeat
from .snr_sbr_live import SBRForward
from .snr_rbs_live import RBSForward
from .nested_forward import NestedForward
from .tick_archive import TickArchive
from .fvg_checkpoint import FVGCheckpoint, summarize_curve
from .fvg_forward import FVGForward
from .mt5_clock import configured_feed

class RatesUnavailable(RuntimeError):
    def __init__(self, code):
        self.code=code
        super().__init__(f'Терминал не вернул свечи M1 (код {code})')


def closed_m1_rates(mt5,symbol,last,now,primed):
    """Request enough closed minutes to recover every bar since the checkpoint."""
    terminal=mt5.terminal_info()
    maxbars=int(getattr(terminal,'maxbars',0))
    if maxbars<1:raise RuntimeError('MT5 не сообщил доступный размер истории свечей')
    count=100 if primed else 30000
    if last is not None:
        count=max(count,(now-last)//60+2)
    if count>maxbars:
        raise RuntimeError('Разрыв наблюдения больше доступной истории MT5; автоматическое продолжение запрещено')
    rates=mt5.copy_rates_from_pos(symbol,mt5.TIMEFRAME_M1,1,count)
    if rates is None:
        error=mt5.last_error()
        raise RatesUnavailable(error[0] if error else None)
    if not primed and len(rates)==0:
        raise RatesUnavailable(None)
    return rates


def m1_row(rate,point):
    """Use the same MT5 bid/spread conversion in live and replay checks."""
    spread=int(rate['spread'])*point
    row={'time':int(rate['time']),'volume':float(rate['tick_volume'])}
    for name in ('open','high','low','close'):
        row['bid_'+name]=float(rate[name])
        row['ask_'+name]=float(rate[name])+spread
    return row

def account_risk(mt5):
    account=mt5.account_info();positions=mt5.positions_get()
    if positions is None:raise RuntimeError('MT5 не вернул позиции')
    known=0.;unknown=0;rows=[]
    for p in positions:
        # Remaining adverse P/L from the current price to the stop, not from initial entry.
        risk=None
        if p.sl:
            value=mt5.order_calc_profit(p.type,p.symbol,p.volume,p.price_current,p.sl)
            if value is not None:risk=max(0,-value);known+=risk
        if risk is None:unknown+=1
        rows.append({'ticket':p.ticket,'symbol':p.symbol,'direction':'buy' if p.type==0 else 'sell','lots':p.volume,
                     'entry':p.price_open,'stop':p.sl or None,'current':p.price_current,'remaining_risk':risk})
    return {'known_risk':known,'unknown_count':unknown,'positions':rows,
            'currency':account.currency if account else None,'equity':account.equity if account else None,
            'note':'Оставшийся риск по стопам всех позиций счёта; без взаимозачёта корреляций, гэпов, комиссии и проскальзывания. Без стопа общий риск неизвестен.'}

def begin_forward(radar):
    """Keep warmed-up zones and bars, but start simulated performance at zero."""
    radar.trades.clear();radar.positions.clear();radar.curve.clear();radar.pending=None
    radar.balance=radar.cfg['initial_equity'];radar.day_start=radar.balance

def main():
    p=argparse.ArgumentParser();p.add_argument('--symbol',default='XAUUSD');p.add_argument('--terminal');p.add_argument('--watch',action='store_true');args=p.parse_args()
    try:import MetaTrader5 as mt5
    except ImportError:raise SystemExit('Установите официальный пакет: python -m pip install MetaTrader5. Затем войдите в свой терминал MT5 вручную.')
    heartbeat('bridge','starting')
    if not (mt5.initialize(path=args.terminal) if args.terminal else mt5.initialize()):
        heartbeat('bridge','stopped');raise SystemExit(f'Не удалось подключиться к MT5: {mt5.last_error()}')
    mt5=configured_feed(mt5,ROOT/'mt5_clock.json',args.symbol)
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'));radar=Radar(cfg);startup=int(time.time());last=None;primed=False;processed_rows=0
    market=LiveMarket(args.symbol);stop_delivery=threading.Event()
    ticks=TickArchive(startup=startup)
    threading.Thread(target=delivery_worker,args=(stop_delivery,),daemon=True).start()
    folder=ROOT/'results';folder.mkdir(exist_ok=True)
    try:
        info=mt5.symbol_info(args.symbol)
        if info is None:raise RuntimeError('Инструмент не найден: проверьте суффикс символа брокера')
        price_step=float(info.trade_tick_size or info.point)
        if not math.isfinite(price_step) or price_step<=0:
            raise RuntimeError('У инструмента нет корректного шага цены')
        if not info.visible:mt5.symbol_select(args.symbol,True)
        account=mt5.account_info()
        source_identity='MT5|'+str(getattr(account,'server','unknown'))+'|'+args.symbol
        if mt5.offset_seconds:source_identity+='|UTC-offset='+str(mt5.offset_seconds)
        checkpoint=FVGCheckpoint(ROOT/'runtime'/'fvg-checkpoint.json',
                                 source_identity+'|'+str(getattr(account,'login','unknown')),cfg)
        restored=checkpoint.load(cfg)
        fvg_since=startup;checkpoint_last=None
        if restored:
            radar,fvg_since,processed_rows=restored;last=radar.last;checkpoint_last=last
            print('FVG: восстановлены состояние симуляции и последняя M1',flush=True)
            chart_archived=checkpoint.archive_chart(radar)
            curve_archived=checkpoint.archive_curve(radar,fvg_since)
            if chart_archived or curve_archived:
                checkpoint.save(radar,fvg_since,processed_rows)
        forward=FVGForward(args.symbol,source_identity+'|'+str(getattr(account,'login','unknown')),
                           radar.config_hash,fvg_since,price_step=price_step)
        forward.flush_pending(startup)
        forward_event_cursor=0
        sbr=SBRForward(args.symbol,source_identity,startup,price_step=price_step)
        rbs=RBSForward(args.symbol,source_identity,startup,price_step=price_step)
        nested=NestedForward(args.symbol,source_identity,startup)
        while True:
            try:
                tick=mt5.symbol_info_tick(args.symbol)
                if tick is None:raise RuntimeError('Нет котировки инструмента')
                now=int(time.time())
                if int(tick.time)>now:
                    raise RuntimeError('Котировка MT5 из будущего после преобразования UTC: проверьте mt5_clock.json и часы источника')
                rates=closed_m1_rates(mt5,args.symbol,last,now,primed)
                if last is not None and len(rates) and int(rates[0]['time'])+60<=now and int(rates[0]['time'])>last+60:
                    print('Обнаружен разрыв: сценарии будут сброшены',flush=True)
                for index,r in enumerate(rates):
                    t=int(r['time'])
                    if t+60>now:continue
                    # MT5 historical OHLC is bid; spread column supplies an APPROXIMATION for ask.
                    row=m1_row(r,info.point)
                    if sbr.model.last is None or t>sbr.model.last:
                        sbr.ingest(row,now,int(tick.time))
                    if rbs.model.last is None or t>rbs.model.last:
                        rbs.ingest(row,now,int(tick.time))
                    if nested.model.last is None or t>nested.model.last:
                        nested.ingest(row,now,int(tick.time))
                    if (last is None and index<len(rates)-10000) or (last is not None and t<=last):continue
                    radar.on_bar(row);last=t;processed_rows+=1
                if not primed:
                    if not restored:begin_forward(radar)
                    sbr.finish_bootstrap();rbs.finish_bootstrap();primed=True
                checkpoint.archive_chart(radar)
                checkpoint.archive_curve(radar,fvg_since)
                if checkpoint_last!=radar.last or not checkpoint.path.exists():
                    checkpoint.save(radar,fvg_since,processed_rows);checkpoint_last=radar.last
                forward.persist(radar.events[forward_event_cursor:],now,int(tick.time),
                                float(tick.bid),float(tick.ask))
                forward_event_cursor=len(radar.events)
                quote=market.update(mt5,tick,now)
                ticks.collect(mt5,args.symbol,tick,int(time.time()))
                nested.check_ticks(ticks,int(time.time()))
                result=radar.result();result.update(mode='live',heartbeat=now,quote_time=int(tick.time),forward_since=fvg_since,
                    checkpoint_restored=bool(restored),
                    quote=quote,
                    actual_positions=account_risk(mt5),source={'source':'MT5 · '+args.symbol,'symbol':args.symbol,
                    'timeframe':'M1','timezone':'UTC','rows':processed_rows,'first':radar.first,'last':last,
                    'ask_quality':'Оценка из bid OHLC + spread M1, не реальные ask OHLC'})
                result['symbol_details']={'symbol':args.symbol,
                    'contract_size':float(info.trade_contract_size),
                    'profit_currency':info.currency_profit,
                    'volume_min':float(info.volume_min),
                    'volume_step':float(info.volume_step)}
                prefix,archived_forward=checkpoint.curve_prefix(fvg_since)
                result['curve_prefix']=prefix
                result['curve_archived_forward_count']=archived_forward
                result['summary']=summarize_curve(radar.trades,cfg['initial_equity'],radar.curve,prefix)
                # Old market quote must remain visibly stale even if the bridge itself runs.
                result['heartbeat']=min(now,int(tick.time))
                result['source']['clock_offset_seconds']=mt5.offset_seconds
                result['source']['raw_quote_time']=int(tick.time)+mt5.offset_seconds
                save_json(folder/'live.json',result)
                save_json(folder/'snr_sbr_live.json',sbr.state(now,int(tick.time)))
                save_json(folder/'snr_rbs_live.json',rbs.state(now,int(tick.time)))
                save_json(folder/'nested_forward_live.json',nested.state(now,int(tick.time)))
                heartbeat('bridge','running',quote_time=int(tick.time),rows=processed_rows)
                print(f'Котировка {dt.datetime.fromtimestamp(tick.time,dt.timezone.utc).isoformat()}; загружено M1-свечей {processed_rows}; только чтение',flush=True)
                if not args.watch:break
                time.sleep(10)
            except (OSError,RuntimeError,ValueError,sqlite3.Error) as exc:
                heartbeat('bridge','retrying',error_type=type(exc).__name__,
                          error_code=exc.code if isinstance(exc,RatesUnavailable) else None)
                print(type(exc).__name__+': '+str(exc),flush=True)
                if not args.watch:raise
                mt5.shutdown()
                time.sleep(10)
                if args.terminal:mt5.initialize(path=args.terminal)
                else:mt5.initialize()
    finally:
        stop_delivery.set();mt5.shutdown();heartbeat('bridge','stopped')

if __name__=='__main__':main()
