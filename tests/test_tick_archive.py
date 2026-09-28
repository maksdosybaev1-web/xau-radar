import uuid
import unittest
from types import SimpleNamespace

from app.datafeed import ROOT
from app.tick_archive import TickArchive


class TickArchiveTests(unittest.TestCase):
    def make_store(self):
        path = ROOT/'runtime'/('ticks-test-'+uuid.uuid4().hex+'.sqlite3')
        self.addCleanup(lambda:path.unlink(missing_ok=True))
        return TickArchive(path, startup=1000)

    def test_overlap_dedup_receipt_and_window_gap(self):
        store = self.make_store()
        batches = [
            [dict(time_msc=1008000,bid=100,ask=100.2,flags=2),
             dict(time_msc=1009000,bid=101,ask=101.2,flags=2)],
            [dict(time_msc=1009000,bid=101,ask=101.2,flags=2),
             dict(time_msc=1019000,bid=102,ask=102.2,flags=2)],
            [dict(time_msc=1139000,bid=103,ask=103.2,flags=2)],
        ]
        calls = []
        mt5 = SimpleNamespace(COPY_TICKS_INFO=1,
            copy_ticks_range=lambda symbol,start,end,flags:(calls.append((start,end)),batches.pop(0))[1])
        self.assertEqual(store.collect(mt5,'XAUUSD',SimpleNamespace(time=1009),1010)['stored'],2)
        self.assertEqual(store.collect(mt5,'XAUUSD',SimpleNamespace(time=1019),1020)['stored'],1)
        self.assertEqual(calls[:2],[(1000,1010),(1009,1020)])
        self.assertEqual(store.summary(1020)['total'],3)
        with store.connect() as db:
            first = db.execute('SELECT first_observed_at FROM ticks WHERE time_msc=1009000').fetchone()[0]
        self.assertEqual(first,1010)
        self.assertEqual(store.assess_window('XAUUSD',1000,1020)['status'],'queried_unverified')
        store.collect(mt5,'XAUUSD',SimpleNamespace(time=1139),1140)
        self.assertEqual(calls[2],(1080,1140))
        self.assertEqual(store.assess_window('XAUUSD',1010,1140)['status'],'insufficient_data')

    def test_stale_error_and_partial_never_claim_coverage(self):
        store = self.make_store()
        mt5 = SimpleNamespace(COPY_TICKS_INFO=1,copy_ticks_range=lambda *_:None,
                              last_error=lambda:(-1,'unavailable'))
        self.assertEqual(store.collect(mt5,'XAUUSD',SimpleNamespace(time=1000),1100)['status'],'stale_quote')
        self.assertEqual(store.summary(1100)['pulls'],0)
        self.assertEqual(store.collect(mt5,'XAUUSD',SimpleNamespace(time=1009),1010)['status'],'error')
        mt5.copy_ticks_range = lambda *_:[dict(time_msc=1009000,bid=100,ask=100.2,flags=2),
                                          dict(time_msc=1009001,bid=0,ask=100.2,flags=2)]
        self.assertEqual(store.collect(mt5,'XAUUSD',SimpleNamespace(time=1009),1010)['status'],'partial')
        self.assertEqual(store.assess_window('XAUUSD',1000,1010)['status'],'insufficient_data')
        self.assertEqual(store.summary(1010)['non_ok_pulls'],2)

    def test_zone_quote_is_observed_without_claiming_fill(self):
        store = self.make_store()
        with store.connect() as db:
            db.execute('INSERT INTO ticks VALUES (?,?,?,?,?,?)',
                       ('XAUUSD',1005000,100.0,100.2,2,1010))
            db.execute('INSERT INTO tick_pulls(symbol,start_sec,end_sec,observed_at,status,returned,stored,error_code) VALUES (?,?,?,?,?,?,?,?)',
                       ('XAUUSD',1000,1060,1061,'ok',1,1,None))
        seen = store.inspect_zone('XAUUSD',1000,1060,'buy',100.1,100.3)
        self.assertEqual(seen['coverage']['status'],'queried_unverified')
        self.assertEqual(seen['zone_quote']['time_msc'],1005000)
        self.assertEqual(seen['zone_quote']['first_observed_at'],1010)
        self.assertEqual(seen['limit_threshold_quote']['price'],100.2)
        self.assertFalse(seen['broker_fill_verified'])
        self.assertIsNone(store.inspect_zone('XAUUSD',1000,1060,'sell',101,102)['zone_quote'])


if __name__=='__main__':
    unittest.main()
