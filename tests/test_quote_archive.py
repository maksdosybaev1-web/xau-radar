import csv
import io
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.quote_archive import QuoteArchive
from app.notifications import AlertStore
from app.live_market import LiveMarket


class QuoteArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name)/'quotes.db'
        self.store = QuoteArchive(self.path)
        self.quote = dict(time=1000, time_msc=1000123, bid=100, ask=100.2)

    def test_duplicate_after_restart_keeps_first_receipt(self):
        self.assertTrue(self.store.add('XAUUSD', self.quote, 1001))
        reopened = QuoteArchive(self.path)
        self.assertFalse(reopened.add('XAUUSD', self.quote, 1010))
        s = reopened.summary(1010)
        self.assertEqual(s['total'], 1)
        self.assertEqual(s['last_received_at'], 1001)
        self.assertFalse(s['complete_tick_feed'])

    def test_stale_future_invalid_and_nonfinite_quotes_rejected(self):
        for patch, now in [({}, 1091), ({}, 999), ({'bid':0},1000),
                           ({'ask':99},1000), ({'bid':float('nan')},1000),
                           ({'ask':float('inf')},1000), ({'time_msc':-1},1000)]:
            with self.subTest(patch=patch, now=now):
                self.assertFalse(self.store.add('XAUUSD', dict(self.quote, **patch), now))
        self.assertEqual(self.store.summary(1100)['total'],0)

    def test_milliseconds_and_changed_price_are_preserved(self):
        for patch in [{}, {'time_msc':1000124}, {'time_msc':1000124,'ask':100.3}]:
            self.assertTrue(self.store.add('XAUUSD',dict(self.quote,**patch),1000))
        self.assertEqual(self.store.summary(1000)['total'],3)

    def test_gaps_partition_by_symbol_and_older_ticks_rejected(self):
        self.store.add('XAUUSD',self.quote,1000)
        self.store.add('OTHER',dict(self.quote,time_msc=1050000),1050)
        self.store.add('XAUUSD',dict(self.quote,time_msc=1100123),1100)
        self.assertFalse(self.store.add('XAUUSD',dict(self.quote,time_msc=1090000),1100))
        s=self.store.summary(1100)
        self.assertEqual(s['intervals_over_90s'],1)
        self.assertEqual(s['max_interval_seconds'],100)

    def test_csv_contains_bid_ask_and_both_timestamps(self):
        self.store.add('=XAUUSD',self.quote,1001)
        rows=list(csv.DictReader(io.StringIO(self.store.export_csv().decode('utf-8-sig'))))
        self.assertEqual(rows[0]['symbol'],"'=XAUUSD")
        self.assertEqual(rows[0]['time_msc'],'1000123')
        self.assertEqual(float(rows[0]['spread']),.2)
        self.assertNotEqual(rows[0]['quote_time_utc'],rows[0]['observed_at_utc'])

    def test_empty_archive_and_last_day_window(self):
        self.assertEqual(self.store.summary(1000)['total'],0)
        self.assertIsNone(self.store.summary(1000)['max_interval_seconds'])
        self.store.add('XAUUSD',self.quote,1000)
        self.assertEqual(self.store.summary(90000)['last_24h'],0)
        self.assertEqual(self.store.summary(1000)['last_24h'],1)

    def test_seconds_fallback_for_adapters_without_milliseconds(self):
        q=dict(self.quote);q.pop('time_msc')
        self.assertTrue(self.store.add('XAUUSD',q,1000))
        self.assertEqual(self.store.summary(1000)['last_time_msc'],1000000)

    def test_live_bridge_records_quote_without_warmup_alerts(self):
        alerts=AlertStore(self.path.parent/'alerts.db')
        row=dict(time=6000,open=100,high=101,low=99,close=100,tick_volume=10)
        mt5=SimpleNamespace(TIMEFRAME_M5=5,TIMEFRAME_M15=15,TIMEFRAME_H1=60,
                            copy_rates_from_pos=lambda *args:[row])
        tick=SimpleNamespace(time=10000,time_msc=10000123,bid=100,ask=100.2)
        with patch('app.live_market.AlertStore',return_value=alerts), \
             patch('app.live_market.QuoteArchive',return_value=self.store), \
             patch('app.live_market.save_json'):
            market=LiveMarket('XAUUSD');market.update(mt5,tick,10000)
        self.assertEqual(self.store.summary(10000)['total'],1)
        self.assertEqual(alerts.recent(),[])

    def test_signal_first_receipt_preserved_when_duplicate_reappears(self):
        alerts=AlertStore(self.path.parent/'alerts.db')
        event=dict(id='one',time=1000,tf='M15',type='watch')
        alerts.add(event,1010,1010,False)
        alerts.add(event,1020,1020,False)
        self.assertEqual(alerts.recent()[0]['observed_at'],1010)


if __name__=='__main__':unittest.main()
