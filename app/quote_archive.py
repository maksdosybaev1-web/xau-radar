"""Durable samples of live bid/ask; never presented as a complete tick feed."""
import csv
import datetime as dt
import io
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .datafeed import ROOT


class QuoteArchive:
    MAX_AGE = 90

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else ROOT/'runtime'/'quotes.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS quotes (
                symbol TEXT NOT NULL, time_msc INTEGER NOT NULL,
                observed_at INTEGER NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
                PRIMARY KEY (symbol,time_msc,bid,ask))''')
            db.execute('CREATE INDEX IF NOT EXISTS quotes_observed ON quotes(observed_at)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, symbol, quote, now):
        """Keep the first receipt, rejecting stale, future and invalid quotes."""
        try:
            stamp = int(quote.get('time_msc') or int(quote['time'])*1000)
            bid, ask = float(quote['bid']), float(quote['ask'])
            observed = int(now)
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if not symbol or not all(math.isfinite(v) for v in (bid, ask)) or not 0 < bid <= ask:
            return False
        # `now` is whole seconds; permit milliseconds inside that same second.
        if stamp <= 0 or not 0 <= observed-stamp//1000 <= self.MAX_AGE:
            return False
        with self.connect() as db:
            latest = db.execute('SELECT MAX(time_msc) FROM quotes WHERE symbol=?', (symbol,)).fetchone()[0]
            if latest is not None and stamp < latest:
                return False
            return bool(db.execute('INSERT OR IGNORE INTO quotes VALUES (?,?,?,?,?)',
                                   (symbol, stamp, observed, bid, ask)).rowcount)

    def summary(self, now=None):
        now = int(time.time() if now is None else now)
        with self.connect() as db:
            count, first, last, received = db.execute(
                'SELECT COUNT(*),MIN(time_msc),MAX(time_msc),MAX(observed_at) FROM quotes').fetchone()
            recent = db.execute('SELECT COUNT(*) FROM quotes WHERE observed_at BETWEEN ? AND ?',
                                (now-86400, now)).fetchone()[0]
            gaps, maximum = db.execute('''SELECT COALESCE(SUM(gap>?),0),MAX(gap) FROM (
                SELECT (time_msc-LAG(time_msc) OVER (PARTITION BY symbol ORDER BY time_msc))/1000.0 AS gap
                FROM quotes)''', (self.MAX_AGE,)).fetchone()
        return dict(total=count, first_time_msc=first, last_time_msc=last, last_received_at=received,
                    last_24h=recent, intervals_over_90s=gaps, max_interval_seconds=maximum,
                    source='MT5 · выборочные bid/ask', complete_tick_feed=False)

    def export_csv(self):
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['symbol', 'quote_time_utc', 'time_msc', 'observed_at_utc', 'bid', 'ask', 'spread'])
        utc = lambda stamp: dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat()
        with self.connect() as db:
            for symbol, stamp, observed, bid, ask in db.execute('SELECT * FROM quotes ORDER BY time_msc,symbol,bid,ask'):
                safe_symbol = "'"+symbol if symbol[:1] in ('=', '+', '-', '@') else symbol
                writer.writerow([safe_symbol, utc(stamp/1000), stamp, utc(observed),
                                 bid, ask, round(ask-bid, 10)])
        return ('\ufeff'+output.getvalue()).encode('utf-8')
