"""Read-only MT5 bid/ask tick history with explicit observation windows."""
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .datafeed import ROOT


class TickArchive:
    def __init__(self, path=None, startup=None):
        self.path = Path(path) if path is not None else ROOT/'runtime'/'ticks.sqlite3'
        self.startup = int(time.time() if startup is None else startup)
        self.last_end = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ticks (
                symbol TEXT NOT NULL, time_msc INTEGER NOT NULL, bid REAL NOT NULL,
                ask REAL NOT NULL, flags INTEGER NOT NULL, first_observed_at INTEGER NOT NULL,
                PRIMARY KEY (symbol,time_msc,bid,ask,flags))''')
            db.execute('CREATE INDEX IF NOT EXISTS ticks_time ON ticks(symbol,time_msc)')
            db.execute('''CREATE TABLE IF NOT EXISTS tick_pulls (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, start_sec INTEGER NOT NULL,
                end_sec INTEGER NOT NULL, observed_at INTEGER NOT NULL, status TEXT NOT NULL,
                returned INTEGER NOT NULL, stored INTEGER NOT NULL, error_code INTEGER)''')
            db.execute('CREATE INDEX IF NOT EXISTS tick_pulls_window ON tick_pulls(symbol,start_sec,end_sec)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def collect(self, mt5, symbol, tick, now):
        """Pull at most the latest 60 seconds; never label old terminal history live."""
        now = int(now)
        quote_time = int(tick.time)
        if not 0 <= now-quote_time <= 90:
            return {'status':'stale_quote', 'returned':0, 'stored':0}
        start = max(self.startup, now-60, (self.last_end-1) if self.last_end is not None else 0)
        end = quote_time+1
        if end <= start:
            return {'status':'no_new_window', 'returned':0, 'stored':0}
        try:
            rows = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_INFO)
        except Exception:
            rows = None
        error_code = None
        if rows is None:
            last_error = mt5.last_error()
            error_code = int(last_error[0]) if last_error else None
            status, count, accepted = 'error', 0, []
        else:
            count, accepted = len(rows), []
            for row in rows:
                try:
                    stamp = int(row['time_msc'])
                    bid, ask = float(row['bid']), float(row['ask'])
                    flags = int(row['flags'])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if (start*1000 <= stamp <= end*1000 and math.isfinite(bid) and
                        math.isfinite(ask) and 0 < bid <= ask):
                    accepted.append((symbol, stamp, bid, ask, flags, now))
            status = 'ok' if count and len(accepted) == count else 'empty' if not count else 'partial'
        with self.connect() as db:
            before = db.total_changes
            db.executemany('INSERT OR IGNORE INTO ticks VALUES (?,?,?,?,?,?)', accepted)
            stored = db.total_changes-before
            db.execute('''INSERT INTO tick_pulls
                (symbol,start_sec,end_sec,observed_at,status,returned,stored,error_code)
                VALUES (?,?,?,?,?,?,?,?)''',
                (symbol,start,end,now,status,count,stored,error_code))
        if status == 'ok':
            self.last_end = end
        return {'status':status, 'returned':count, 'stored':stored,
                'start_sec':start, 'end_sec':end}

    def assess_window(self, symbol, start_sec, end_sec):
        """Report query coverage, not a guarantee of complete broker ticks."""
        if end_sec <= start_sec:
            raise ValueError('end_sec must be after start_sec')
        with self.connect() as db:
            windows = db.execute('''SELECT start_sec,end_sec FROM tick_pulls
                WHERE symbol=? AND status='ok' AND end_sec>? AND start_sec<?
                ORDER BY start_sec,end_sec''', (symbol,start_sec,end_sec)).fetchall()
            count = db.execute('''SELECT COUNT(*) FROM ticks
                WHERE symbol=? AND time_msc>=? AND time_msc<?''',
                (symbol,start_sec*1000,end_sec*1000)).fetchone()[0]
        reached = start_sec
        for first,last in windows:
            if first > reached:
                break
            reached = max(reached,last)
            if reached >= end_sec:
                break
        status = 'queried_unverified' if reached >= end_sec and count else 'insufficient_data'
        return {'status':status, 'ticks':count, 'covered_until':min(reached,end_sec),
                'note':'Успешный запрос истории MT5 не доказывает полноту всех тиков или реальное исполнение.'}

    def inspect_zone(self, symbol, start_sec, end_sec, direction, low, high):
        """Describe archived quotes in a minute; this cannot verify an order fill."""
        if direction not in ('buy', 'sell') or not low < high:
            raise ValueError('Invalid direction or zone')
        coverage = self.assess_window(symbol, start_sec, end_sec)
        side = 'ask' if direction == 'buy' else 'bid'
        threshold = high if direction == 'buy' else low
        comparator = '<=' if direction == 'buy' else '>='
        with self.connect() as db:
            first_zone = db.execute(f'''SELECT time_msc,{side},ask-bid,first_observed_at FROM ticks
                WHERE symbol=? AND time_msc>=? AND time_msc<? AND {side} BETWEEN ? AND ?
                ORDER BY time_msc LIMIT 1''',
                (symbol,start_sec*1000,end_sec*1000,low,high)).fetchone()
            first_threshold = db.execute(f'''SELECT time_msc,{side},ask-bid,first_observed_at FROM ticks
                WHERE symbol=? AND time_msc>=? AND time_msc<? AND {side}{comparator}?
                ORDER BY time_msc LIMIT 1''',
                (symbol,start_sec*1000,end_sec*1000,threshold)).fetchone()
        return {'coverage':coverage, 'price_side':side,
                'zone_quote':dict(zip(('time_msc','price','spread','first_observed_at'),first_zone)) if first_zone else None,
                'limit_threshold_quote':dict(zip(('time_msc','price','spread','first_observed_at'),first_threshold)) if first_threshold else None,
                'broker_fill_verified':False}

    def summary(self, now=None):
        now = int(time.time() if now is None else now)
        with self.connect() as db:
            total, first, last = db.execute(
                'SELECT COUNT(*),MIN(time_msc),MAX(time_msc) FROM ticks').fetchone()
            recent = db.execute('''SELECT COUNT(*) FROM ticks
                WHERE first_observed_at BETWEEN ? AND ?''', (now-86400,now)).fetchone()[0]
            pulls, errors = db.execute('''SELECT COUNT(*),COALESCE(SUM(status!='ok'),0)
                FROM tick_pulls''').fetchone()
            last_pull = db.execute('''SELECT status,observed_at,start_sec,end_sec,returned
                FROM tick_pulls ORDER BY id DESC LIMIT 1''').fetchone()
        return {'total':total, 'first_time_msc':first, 'last_time_msc':last,
                'last_24h':recent, 'pulls':pulls, 'non_ok_pulls':errors,
                'last_pull':dict(zip(('status','observed_at','start_sec','end_sec','returned'),last_pull)) if last_pull else None,
                'source':'MT5 copy_ticks_range · bid/ask', 'complete_tick_feed':False}
