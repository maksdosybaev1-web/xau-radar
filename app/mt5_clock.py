"""Explicit, source-bound MT5 clock conversion; never infer offset from tick age."""
import json
from types import SimpleNamespace


class UTCFeed:
    def __init__(self, terminal, offset_seconds=0):
        if type(offset_seconds) is not int or abs(offset_seconds)>14*3600 or offset_seconds%3600:
            raise ValueError('Некорректное смещение времени MT5')
        self.terminal=terminal
        self.offset_seconds=offset_seconds

    def __getattr__(self, name):
        return getattr(self.terminal,name)

    def symbol_info_tick(self, symbol):
        tick=self.terminal.symbol_info_tick(symbol)
        if tick is None:return None
        return SimpleNamespace(time=int(tick.time)-self.offset_seconds,
                               time_msc=int(tick.time_msc)-self.offset_seconds*1000,
                               bid=tick.bid,ask=tick.ask)

    def normalize(self, rows):
        if rows is None:return None
        rows=rows.copy()
        rows['time']-=self.offset_seconds
        if 'time_msc' in rows.dtype.names:rows['time_msc']-=self.offset_seconds*1000
        return rows

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return self.normalize(self.terminal.copy_rates_from_pos(symbol,timeframe,start,count))

    def copy_ticks_range(self, symbol, start, end, flags):
        # TickArchive passes UTC epoch seconds; the terminal expects its source clock.
        return self.normalize(self.terminal.copy_ticks_range(
            symbol,start+self.offset_seconds,end+self.offset_seconds,flags))


def configured_feed(terminal, path, symbol):
    if not path.exists():return UTCFeed(terminal)
    cfg=json.loads(path.read_text(encoding='utf-8'))
    account=terminal.account_info()
    if account is None or cfg['server']!=account.server or cfg['symbol']!=symbol:
        raise ValueError('Настройка времени MT5 относится к другому источнику')
    return UTCFeed(terminal,cfg['offset_seconds'])
