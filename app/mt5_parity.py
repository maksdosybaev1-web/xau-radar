"""Compare one frozen MT5 history through continuous and checkpointed FVG replay."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from .datafeed import ROOT
from .engine import Radar, run
from .fvg_checkpoint import FVGCheckpoint
from .mt5_bridge import m1_row


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--symbol',default='XAUUSD')
    parser.add_argument('--bars',type=int,default=30000)
    parser.add_argument('--terminal')
    args=parser.parse_args()
    if args.bars<2:parser.error('--bars must be at least 2')
    import MetaTrader5 as mt5
    if not (mt5.initialize(path=args.terminal) if args.terminal else mt5.initialize()):
        raise SystemExit(f'MT5 не подключился: {mt5.last_error()}')
    try:
        info=mt5.symbol_info(args.symbol)
        if info is None:raise SystemExit('Символ недоступен в MT5')
        rates=mt5.copy_rates_from_pos(args.symbol,mt5.TIMEFRAME_M1,1,args.bars)
        if rates is None:raise SystemExit(f'MT5 не вернул M1: {mt5.last_error()}')
        now=int(time.time())
        rows=[m1_row(rate,info.point) for rate in rates if int(rate['time'])+60<=now]
    finally:
        mt5.shutdown()
    if len(rows)<2:raise SystemExit('Недостаточно закрытых M1-свечей для сравнения')
    if any(b['time']<=a['time'] for a,b in zip(rows,rows[1:])):
        raise SystemExit('MT5 вернул M1 не по возрастанию времени')
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    continuous=run(rows,cfg)
    split=len(rows)//2
    radar=Radar(cfg)
    for row in rows[:split]:radar.on_bar(row)
    with tempfile.TemporaryDirectory(prefix='mt5-parity-',dir=ROOT/'runtime') as tmp:
        checkpoint=FVGCheckpoint(Path(tmp)/'checkpoint.json','MT5-parity|'+args.symbol,cfg)
        checkpoint.save(radar,rows[0]['time'],split)
        radar,_,_=checkpoint.load(cfg)
        for row in rows[split:]:radar.on_bar(row)
        resumed=radar.result()
    different=[key for key in continuous if continuous[key]!=resumed[key]]
    print(json.dumps({'source':'MT5 '+args.symbol,'bars':len(rows),'first':rows[0]['time'],
                      'last':rows[-1]['time'],'continuous_events':len(continuous['events']),
                      'resumed_events':len(resumed['events']),'continuous_trades':len(continuous['trades']),
                      'resumed_trades':len(resumed['trades']),'equal':not different,
                      'different_fields':different,'scope':'Исторический повтор одного снимка MT5; не forward-тест'},
                     ensure_ascii=False))
    if different:raise SystemExit(1)


if __name__=='__main__':main()
