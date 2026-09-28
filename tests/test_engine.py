import copy, json, pathlib, sys, unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from app.engine import Aggregator, Radar, exit_price, trend, run, summarize
from app.datafeed import ROOT
from app.ai_review import snapshot, explain, verified_explanation

CFG=json.loads((ROOT/'config.json').read_text())
def bar(t=0,o=100,h=102,l=99,c=101,spread=.2):
    return dict(time=t,bid_open=o,bid_high=h,bid_low=l,bid_close=c,ask_open=o+spread,ask_high=h+spread,ask_low=l+spread,ask_close=c+spread,volume=1)

class ExecutionTests(unittest.TestCase):
    def test_long_same_minute_stop_target_is_stop(self):
        p={'direction':'buy','stop':99,'target':102,'entry_time':0}
        price,reason,t=exit_price(p,bar(h=103,l=98),CFG)
        self.assertEqual(reason,'stop_ambiguous');self.assertAlmostEqual(price,98.95);self.assertEqual(t,60)
    def test_short_stop_uses_ask_not_bid(self):
        p={'direction':'sell','stop':102,'target':96,'entry_time':0}
        result=exit_price(p,bar(h=101.9,l=99,spread=.2),CFG)
        self.assertEqual(result[1],'stop');self.assertAlmostEqual(result[0],102.05)
    def test_gap_is_worse_than_stop(self):
        p={'direction':'buy','stop':99,'target':110,'entry_time':0}
        price,reason,_=exit_price(p,bar(o=95,h=97,l=94,c=96),CFG)
        self.assertAlmostEqual(price,94.95)
    def prepare(self):
        r=Radar(CFG);r.zone({'created':0,'origin':0,'low':98,'high':99,'direction':'buy'},0)
        r.pending={'time':300,'zone_id':'z1','direction':'buy','stop':98,'child_low':98.5,'child_high':99,'explanation':'test'}
        return r
    def test_fill_next_open_ask_and_risk_costs(self):
        r=self.prepare();r.fill_pending(bar(t=300,o=100,spread=.2))
        p=r.positions[0];self.assertAlmostEqual(p['entry'],100.25)
        self.assertLessEqual(p['risk_usd'],25)
        r.close_trade(p,97.95,360,'stop')
        self.assertAlmostEqual(-p['pnl'],p['risk_usd'])
        self.assertEqual(p['exit_time'],360);self.assertEqual(p['exit_reason'],'stop')
    def test_gap_before_entry_rejected(self):
        r=self.prepare();r.fill_pending(bar(t=360));self.assertFalse(r.positions)
        self.assertEqual(r.events[-1]['type'],'entry_rejected')
    def test_spread_reject(self):
        r=self.prepare();r.fill_pending(bar(t=300,spread=2));self.assertFalse(r.positions)

class CausalityTests(unittest.TestCase):
    def test_aggregate_waits_for_closed_complete_candle(self):
        a=Aggregator(5)
        for i in range(4):self.assertIsNone(a.add(bar(i*60)))
        self.assertEqual(a.add(bar(240))['end'],300)
        b=Aggregator(5)
        for i in [0,1,3,4]:self.assertIsNone(b.add(bar(i*60)))
    def test_pivot_requires_two_right_bars(self):
        data=[{'high':v+1,'low':v-1} for v in [100,102,105,103,101,103,107,105,104,106,109]]
        before=trend(data[:-1]);data[-1]={'high':10000,'low':-10000}
        self.assertEqual(trend(data[:-1]),before)
    def test_append_future_cannot_rewrite_events(self):
        rows=[]
        for i in range(600):
            value=100+i*.01
            rows.append(bar(i*60,o=value,h=value+1,l=value-1,c=value+.2))
        first=run(rows[:400],CFG);full=run(rows,CFG)
        self.assertEqual(first['events'],[e for e in full['events'] if e['time']<=400*60])
        self.assertEqual(first['bars'],[b for b in full['bars'] if b['end']<=400*60])
    def test_ai_snapshot_has_no_future_or_trade_outcome(self):
        r={'config':{},'events':[{'id':1,'time':300,'type':'confirmed'}],
           'bars':[{'end':300},{'end':600}], 'trades':[{'pnl':999999}]}
        s=snapshot(r,1);self.assertEqual(len(s['closed_m5']),1);self.assertNotIn('trades',s)
        self.assertNotIn('999999',json.dumps(s))
    def test_ai_does_not_invent_conditions_for_near_event(self):
        result={'config':{},'events':[{'id':1,'time':300,'type':'near','reason':'Цена приблизилась к зоне'}],
                'bars':[{'end':300}], 'trades':[]}
        with patch.dict('os.environ',{'RADAR_OLLAMA_MODEL':'qwen2.5:7b'}):
            with self.assertRaisesRegex(ValueError,'только для подтверждённых'):
                explain(result,1)
    def test_ai_panel_keeps_source_conditions_even_if_model_omits_them(self):
        event={'type':'confirmed','direction':'sell','stop':101.234,
               'explanation':'H1: снижение; M15-зона внутри H1; закрытая M5 коснулась зоны и вернулась вниз.'}
        facts=verified_explanation(event)
        for required in ('продажа','H1: снижение','M15-зона внутри H1','закрытая M5','Стоп: 101.23'):
            self.assertIn(required,facts)
        self.assertIn('не исполненная сделка',facts)
    def test_zero_trades_no_fake_win_rate(self):
        s=summarize([]);self.assertIsNone(s['win_rate']);self.assertIsNone(s['average_r']);self.assertEqual(s['closed'],0)

if __name__=='__main__':unittest.main()
