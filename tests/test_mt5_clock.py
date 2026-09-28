import json
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
import numpy as np
from app.mt5_clock import UTCFeed, configured_feed
from app.notifications import AlertStore


class ClockTests(unittest.TestCase):
    def test_rates_and_tick_windows_share_utc_without_mutating_source(self):
        class Terminal:
            rows=np.array([(11000,11000500)],dtype=[('time','i8'),('time_msc','i8')])
            def copy_rates_from_pos(self,*args):return self.rows
            def copy_ticks_range(self,*args):self.args=args;return self.rows
            def symbol_info_tick(self,symbol):
                return SimpleNamespace(time=11000,time_msc=11000500,bid=1,ask=2)
        terminal=Terminal();feed=UTCFeed(terminal,10800)
        self.assertEqual(feed.symbol_info_tick('X').time,200)
        self.assertEqual(feed.symbol_info_tick('X').time_msc,200500)
        self.assertEqual(int(feed.copy_rates_from_pos('X',1,1,1)[0]['time']),200)
        rows=feed.copy_ticks_range('X',190,201,2)
        self.assertEqual(terminal.args,('X',10990,11001,2))
        self.assertEqual(int(rows[0]['time_msc']),200500)
        self.assertEqual(int(terminal.rows[0]['time']),11000)

    def test_zero_offset_and_invalid_settings(self):
        self.assertEqual(UTCFeed(None).offset_seconds,0)
        for value in [True,1,5400,60000,'10800']:
            with self.assertRaises(ValueError):UTCFeed(None,value)

    def test_configuration_cannot_be_applied_to_another_source(self):
        terminal=SimpleNamespace(account_info=lambda:SimpleNamespace(server='server-A'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'clock.json'
            self.assertEqual(configured_feed(terminal,path,'X').offset_seconds,0)
            path.write_text(json.dumps({'server':'server-A','symbol':'X','offset_seconds':10800}))
            self.assertEqual(configured_feed(terminal,path,'X').offset_seconds,10800)
            with self.assertRaises(ValueError):configured_feed(terminal,path,'Y')
            terminal.account_info=lambda:SimpleNamespace(server='server-B')
            with self.assertRaises(ValueError):configured_feed(terminal,path,'X')

    def test_future_or_stale_quote_cannot_enqueue(self):
        with tempfile.TemporaryDirectory() as folder:
            store=AlertStore(Path(folder)/'alerts.db')
            event={'id':'a','time':1000,'tf':'M1','type':'test'}
            self.assertFalse(store.add(event,1000,11800,True))
            self.assertFalse(store.add(event,1000,800,True))
            self.assertTrue(store.add(event,1000,1000,True))

    def test_closed_minute_is_available_after_conversion(self):
        class Terminal:
            def copy_rates_from_pos(self,*args):
                return np.array([(11820,),(11880,)],dtype=[('time','i8')])
        rates=UTCFeed(Terminal(),10800).copy_rates_from_pos('X',1,1,2)
        self.assertEqual([int(r['time']) for r in rates if r['time']+60<=1080],[1020])
