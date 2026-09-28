import copy
import json
import pathlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from app.datafeed import ROOT,load_csv
from app.engine import Radar, summarize
from app.fvg_checkpoint import FVGCheckpoint, summarize_curve
from app.server import state


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=pathlib.Path(self.temp.name)/'state.json'
        self.cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        self.store=FVGCheckpoint(self.path,'test-source',self.cfg)

    def test_missing_returns_none_and_empty_roundtrip(self):
        self.assertIsNone(self.store.load(self.cfg))
        radar=Radar(self.cfg);radar.balance=9990;radar.day_start=10001;radar.sequence=42
        self.store.save(radar,1000,17)
        loaded,since,count=self.store.load(self.cfg)
        self.assertEqual((loaded.balance,loaded.day_start,loaded.sequence,since,count),(9990,10001,42,1000,17))

    def test_reject_corruption_source_config_and_engine_change(self):
        self.store.save(Radar(self.cfg),1000,0)
        for source,cfg in [('other',self.cfg),('test-source',dict(self.cfg,risk_fraction=.1))]:
            with self.assertRaises(ValueError):FVGCheckpoint(self.path,source,cfg).load(cfg)
        changed=FVGCheckpoint(self.path,'test-source',self.cfg);changed.identity['engine_sha256']='changed'
        with self.assertRaises(ValueError):changed.load(self.cfg)
        value=json.loads(self.path.read_text(encoding='utf-8'));value['payload']['state']['balance']=1
        self.path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.load(self.cfg)

    def test_failed_atomic_replace_preserves_previous_snapshot(self):
        radar=Radar(self.cfg);self.store.save(radar,1000,0);old=self.path.read_bytes();radar.balance=5
        with patch('pathlib.Path.replace',side_effect=PermissionError('test')),patch('app.fvg_checkpoint.time.sleep'):
            with self.assertRaises(PermissionError):self.store.save(radar,1000,0)
        self.assertEqual(self.path.read_bytes(),old)
        self.assertEqual(len(list(self.path.parent.glob('*.tmp'))),0)

    def test_real_pending_and_open_position_resume_equal_uninterrupted(self):
        result=json.loads((ROOT/'results/run.json').read_text(encoding='utf-8'))
        trade=result['trades'][0];rows=load_csv();finish=trade['exit_time']+60
        rows=[r for r in rows if r['time']<finish]
        for split in [trade['entry_time'],trade['entry_time']+420]:
            with self.subTest(split=split):
                radar=Radar(self.cfg)
                for row in rows:
                    if row['time']>=split:break
                    radar.on_bar(row)
                if split==trade['entry_time']:self.assertIsNotNone(radar.pending)
                else:self.assertTrue(radar.positions)
                self.store.save(radar,1234,len([r for r in rows if r['time']<split]))
                restored,since,_=self.store.load(self.cfg)
                for p in restored.positions:self.assertIs(p,next(t for t in restored.trades if t['id']==p['id']))
                self.assertEqual(since,1234)
                for row in rows:
                    if row['time']>=split:radar.on_bar(row);restored.on_bar(row)
                self.assertEqual(restored.result(),radar.result())
                self.assertEqual(restored.sequence,radar.sequence)
                self.assertFalse(restored.positions)

    def test_chart_archive_preserves_all_bars_and_bounded_restart(self):
        radar=Radar(self.cfg)
        original=[{'time':i*300,'end':(i+1)*300,'close':100+i} for i in range(300)]
        radar.chart=copy.deepcopy(original)
        self.assertEqual(self.store.archive_chart(radar),60)
        self.assertEqual(radar.chart,original[-240:])
        self.store.save(radar,1000,300)
        resumed,_,_=self.store.load(self.cfg)
        self.assertEqual(resumed.chart,original[-240:])
        # Replaying an unsaved candle after a crash must not duplicate its archive row.
        resumed.chart.insert(0,original[59])
        self.assertEqual(self.store.archive_chart(resumed),1)
        with closing(sqlite3.connect(self.path.with_name('fvg-chart.sqlite3'))) as db:
            rows=[json.loads(p) for (p,) in db.execute('SELECT payload FROM bars ORDER BY end')]
        self.assertEqual(rows,original[:60])
        self.assertEqual(resumed.chart,original[-240:])
        resumed.chart.insert(0,dict(original[59],close=-1))
        with self.assertRaises(ValueError):self.store.archive_chart(resumed)
        self.assertEqual(len(resumed.chart),241)

    def test_chart_archive_rejects_changed_source(self):
        radar=Radar(self.cfg)
        radar.chart=[{'end':i} for i in range(241)]
        self.store.archive_chart(radar)
        other=FVGCheckpoint(self.path,'another-source',self.cfg)
        other_radar=Radar(self.cfg)
        other_radar.chart=[{'end':i} for i in range(241)]
        with self.assertRaises(ValueError):other.archive_chart(other_radar)
        self.assertEqual(len(other_radar.chart),241)

    def test_curve_archive_preserves_drawdown_count_and_restart(self):
        radar=Radar(self.cfg)
        initial=self.cfg['initial_equity']
        curve=[{'time':i*60,'equity':initial+150 if i==80 else initial-75 if i==100 else initial+20+(i%20),
                'balance':initial,'open_risk':0} for i in range(3000)]
        radar.curve=copy.deepcopy(curve)
        expected=summarize([],initial,curve)
        self.assertEqual(self.store.archive_curve(radar,0),600)
        prefix,count=self.store.curve_prefix(0)
        self.assertEqual((prefix['count'],count,len(radar.curve)),(600,600,2400))
        self.assertAlmostEqual(summarize_curve([],initial,radar.curve,prefix)['max_marked_drawdown'],expected['max_marked_drawdown'])
        view=state({'mode':'live','forward_since':0,'config':self.cfg,'bars':[{'end':curve[-1]['time'],'context':'neutral'}],
                    'events':[],'trades':[],'curve':radar.curve,'curve_prefix':prefix})
        self.assertAlmostEqual(view['summary']['max_marked_drawdown'],expected['max_marked_drawdown'])
        self.store.save(radar,0,3000)
        restored,_,_=self.store.load(self.cfg)
        # A crash can replay already archived points from an older checkpoint.
        restored.curve=copy.deepcopy(curve[599:])
        self.store.archive_curve(restored,0)
        prefix,count=self.store.curve_prefix(0)
        self.assertEqual((prefix['count'],count), (600,600))
        self.assertAlmostEqual(summarize_curve([],initial,restored.curve,prefix)['max_marked_drawdown'],expected['max_marked_drawdown'])
        with self.assertRaises(ValueError):self.store.curve_prefix(1)
        restored.curve.insert(0,dict(curve[599],equity=-1))
        with self.assertRaises(ValueError):self.store.archive_curve(restored,0)

    def test_missing_curve_archive_rejects_compacted_snapshot(self):
        radar=Radar(self.cfg)
        radar.curve=[{'time':i*60,'equity':self.cfg['initial_equity'],'balance':self.cfg['initial_equity'],'open_risk':0}
                     for i in range(2401)]
        self.store.archive_curve(radar,0)
        self.store.save(radar,0,2401)
        self.path.with_name('fvg-curve.sqlite3').unlink()
        with self.assertRaisesRegex(ValueError,'обязательный архив'):
            self.store.curve_prefix(0)
        with self.assertRaisesRegex(ValueError,'Снимок FVG повреждён'):
            self.store.load(self.cfg)

    def test_crash_between_curve_archive_and_snapshot_restores_snapshot_boundary(self):
        radar=Radar(self.cfg)
        initial=self.cfg['initial_equity']
        radar.curve=[{'time':i*60,'equity':initial-100 if i==1200 else initial+50,
                      'balance':initial,'open_risk':0} for i in range(2400)]
        self.store.save(radar,0,2400)
        radar.curve.append({'time':2400*60,'equity':initial+50,'balance':initial,'open_risk':0})
        self.store.archive_curve(radar,0)
        # The SQLite transaction finished, but the newer JSON snapshot did not.
        resumed_store=FVGCheckpoint(self.path,'test-source',self.cfg)
        resumed,_,_=resumed_store.load(self.cfg)
        prefix,count=resumed_store.curve_prefix(0)
        self.assertEqual((prefix['count'] if prefix else 0,count),(0,0))
        self.assertEqual(len(resumed.curve),2400)
        self.assertEqual(resumed.curve[0]['time'],0)
        self.assertEqual(summarize_curve([],initial,resumed.curve,prefix)['max_marked_drawdown'],
                         summarize([],initial,resumed.curve)['max_marked_drawdown'])
        resumed.curve.append({'time':2400*60,'equity':initial+50,'balance':initial,'open_risk':0})
        resumed_store.archive_curve(resumed,0)
        resumed_store.save(resumed,0,2401)
        self.assertEqual(resumed_store.curve_prefix(0)[0]['count'],1)

    def test_crash_after_existing_curve_prefix_keeps_only_committed_points(self):
        radar=Radar(self.cfg)
        initial=self.cfg['initial_equity']
        points=[{'time':i*60,'equity':initial-i%100,'balance':initial,'open_risk':0}
                for i in range(3003)]
        radar.curve=copy.deepcopy(points[:3000])
        self.store.archive_curve(radar,0)
        self.store.save(radar,0,3000)
        radar.curve.extend(copy.deepcopy(points[3000:]))
        self.store.archive_curve(radar,0)
        self.assertEqual(self.store.curve_prefix(0)[0]['count'],603)
        resumed_store=FVGCheckpoint(self.path,'test-source',self.cfg)
        resumed,_,_=resumed_store.load(self.cfg)
        prefix,count=resumed_store.curve_prefix(0)
        self.assertEqual((prefix['count'],count,len(resumed.curve)),(600,600,2400))
        self.assertAlmostEqual(summarize_curve([],initial,resumed.curve,prefix)['max_marked_drawdown'],
                               summarize([],initial,points[:3000])['max_marked_drawdown'])
        resumed.curve.extend(copy.deepcopy(points[3000:]))
        resumed_store.archive_curve(resumed,0)
        resumed_store.save(resumed,0,3003)
        self.assertEqual(resumed_store.curve_prefix(0)[0]['count'],603)

if __name__=='__main__':unittest.main()
