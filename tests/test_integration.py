import copy, datetime as dt, json, lzma, struct, unittest
from types import SimpleNamespace
from app.datafeed import ROOT, load_csv, parse_day
from app.engine import run, fvg
from app.mt5_bridge import account_risk, begin_forward
from test_engine import CFG

class IntegrationTests(unittest.TestCase):
    def test_archive_binary_order_and_scale(self):
        raw=struct.pack('>5If',60,3300125,3300500,3299750,3301000,12.5)
        result=parse_day(lzma.compress(raw),dt.date(2025,7,1))
        self.assertEqual(next(iter(result.values())),[3300.125,3301,3299.75,3300.5,12.5])

    def test_fvg_does_not_bridge_missing_candles(self):
        b=[dict(time=i*60,end=(i+1)*60,open=100,high=102,low=99,close=101) for i in range(15)]
        b[-2].update(open=101,high=106,low=100,close=105)
        b[-1].update(open=105,high=108,low=104,close=107)
        self.assertIsNotNone(fvg(b,CFG))
        b[-1]['time']+=60;b[-1]['end']+=60
        self.assertIsNone(fvg(b,CFG))

    def test_all_account_positions_and_unknown_stop(self):
        positions=[SimpleNamespace(ticket=i,symbol=s,type=0,volume=1,price_open=100,price_current=110,sl=sl)
                   for i,s,sl in [(1,'XAUUSD',100),(2,'EURUSD',100),(3,'USDJPY',0)]]
        calls=[]
        def calc(kind,symbol,volume,current,stop):calls.append(symbol);return -25
        mt5=SimpleNamespace(account_info=lambda:SimpleNamespace(currency='USD',equity=10000),
                            positions_get=lambda:positions,order_calc_profit=calc)
        result=account_risk(mt5)
        self.assertEqual(result['known_risk'],50);self.assertEqual(result['unknown_count'],1)
        self.assertEqual(calls,['XAUUSD','EURUSD'])

    def test_forward_simulation_starts_after_warmup(self):
        from app.engine import Radar
        radar=Radar(CFG)
        radar.chart=[{'time':300,'end':600}]
        radar.zones=[{'id':'z1'}]
        radar.trades=[{'entry_time':300,'pnl':-50}]
        radar.positions=[{'entry_time':300}]
        radar.curve=[{'time':300,'equity':9950}]
        radar.pending={'time':600}
        radar.balance=9950;radar.day_start=9950
        begin_forward(radar)
        self.assertEqual(radar.balance,CFG['initial_equity'])
        self.assertEqual(radar.day_start,CFG['initial_equity'])
        self.assertFalse(radar.trades or radar.positions or radar.curve or radar.pending)
        self.assertEqual(radar.chart[0]['end'],600)
        self.assertEqual(radar.zones[0]['id'],'z1')

    def test_live_view_excludes_startup_history_from_results(self):
        from app.server import state
        result={'mode':'live','forward_since':600,'heartbeat':600,'quote_time':540,'config':CFG,
                'bars':[{'time':0,'end':300,'context':'neutral'},{'time':300,'end':600,'context':'neutral'}],
                'events':[{'id':1,'time':300,'type':'near'},{'id':2,'time':600,'type':'near'}],
                'trades':[{'entry_time':300,'exit_time':540,'pnl':-10,'r':-1,'exit_reason':'stop'}],
                'curve':[{'time':300,'equity':9990,'balance':9990,'open_risk':0}], 'source':{}}
        view=state(result)
        self.assertEqual([e['id'] for e in view['events']],[2])
        self.assertEqual(view['summary']['closed'],0)
        self.assertFalse(view['trades'])
        self.assertFalse(view['curve'])
        self.assertEqual(view['quote_time'],540)

    @unittest.skipUnless((ROOT/'results'/'run.json').exists(),'Требуется подготовленная история')
    def test_real_signal_prefix_and_replay_do_not_reveal_future(self):
        from app.server import state
        full=json.loads((ROOT/'results'/'run.json').read_text(encoding='utf-8'))
        trade=full['trades'][0];cutoff=trade['entry_time']+420
        prefix=run([r for r in load_csv() if r['time']<cutoff],CFG)
        self.assertGreater(len(prefix['events']),0)
        self.assertEqual([e for e in prefix['events'] if e['time']<cutoff],
                         [e for e in full['events'] if e['time']<cutoff])
        self.assertEqual(prefix['bars'],[b for b in full['bars'] if b['end']<=cutoff])
        self.assertIsNone(prefix['trades'][0]['exit_time'])
        view=state(full,cutoff)
        self.assertTrue(view['positions']);self.assertFalse(view['trades'])
        for p in view['positions']:
            self.assertFalse(set(('exit','exit_reason','pnl','r'))&set(p))
        self.assertTrue(all(b['end']<=cutoff for b in view['bars']))

if __name__=='__main__':unittest.main()
