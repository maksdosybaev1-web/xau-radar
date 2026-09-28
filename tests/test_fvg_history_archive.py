import json
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest

from app.datafeed import ROOT
from app.engine import Radar
from app.fvg_checkpoint import FVGCheckpoint
from app.fvg_history_archive import export, restore, restore_bundle


class HistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'checkpoint.json'
        self.archive = Path(self.tmp.name)/'archive.sqlite3'
        self.cfg = json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        self.radar = Radar(self.cfg)
        self.checkpoint = FVGCheckpoint(self.path, 'test', self.cfg)
        self.radar.events = [{'id': 2, 'reason': 'Проверка'}, {'id': 1}, {'id': 1}]
        self.radar.trades = [{'id': 't1', 'exit_time': None}]
        self.radar.positions = [self.radar.trades[0]]
        self.radar.pending = {'zone_id': 'z1', 'time': 100}
        self.radar.curve = [{'time': 100, 'equity': 9999}]
        self.checkpoint.save(self.radar, 100, 12)

    def test_lossless_idempotent_archive_and_position_links(self):
        original = self.path.read_bytes()
        report = export(self.path, self.archive)
        self.assertEqual(export(self.path, self.archive), report)
        recovered = restore(self.archive, report['sha256'])
        self.assertEqual(recovered, json.loads(original))
        self.assertEqual(self.path.read_bytes(), original)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 1)
        copy_path = self.path.with_name('restored.json')
        copy_path.write_text(json.dumps(recovered), encoding='utf-8')
        loaded, since, count = FVGCheckpoint(copy_path, 'test', self.cfg).load(self.cfg)
        self.assertIs(loaded.positions[0], loaded.trades[0])
        self.assertEqual(loaded.pending, self.radar.pending)
        self.assertEqual((since, count), (100, 12))

    def test_corrupt_source_rejected_before_archive_creation(self):
        value = json.loads(self.path.read_text(encoding='utf-8'))
        value['payload']['state']['balance'] = 1
        self.path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            export(self.path, self.archive)
        self.assertFalse(self.archive.exists())

    def test_archive_corruption_is_not_silently_repaired(self):
        report = export(self.path, self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("DELETE FROM history WHERE kind='events' AND ordinal=1")
        with self.assertRaises(ValueError):
            restore(self.archive, report['sha256'])
        with self.assertRaises(ValueError):
            export(self.path, self.archive)

    def test_new_snapshot_keeps_previous_version(self):
        first = export(self.path, self.archive)
        self.radar.trades[0]['exit_time'] = 200
        self.radar.positions.clear()
        self.checkpoint.save(self.radar, 100, 13)
        second = export(self.path, self.archive)
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertIsNone(restore(self.archive, first['sha256'])['payload']['state']['trades'][0]['exit_time'])
        self.assertEqual(restore(self.archive, second['sha256'])['payload']['state']['trades'][0]['exit_time'], 200)

    def test_compacted_curve_exports_a_resumable_bundle(self):
        self.radar.curve=[{'time':i*60,'equity':self.cfg['initial_equity']-i%100,
                           'balance':self.cfg['initial_equity'],'open_risk':0} for i in range(2401)]
        self.checkpoint.archive_curve(self.radar,100)
        self.checkpoint.save(self.radar,100,2401)
        report=export(self.path,self.archive)
        self.assertEqual(report['curve_archived_count'],1)
        restored_dir=Path(self.tmp.name)/'restored'
        files=restore_bundle(self.archive,report['sha256'],restored_dir)
        self.assertTrue(Path(files['curve']).exists())
        resumed,since,count=FVGCheckpoint(files['checkpoint'],'test',self.cfg).load(self.cfg)
        self.assertEqual((since,count,len(resumed.curve)),(100,2401,2400))
        self.assertEqual(export(self.path,self.archive),report)

    def test_missing_companion_rejects_export_and_restore(self):
        self.radar.curve=[{'time':i*60,'equity':self.cfg['initial_equity'],
                           'balance':self.cfg['initial_equity'],'open_risk':0} for i in range(2401)]
        self.checkpoint.archive_curve(self.radar,100)
        self.checkpoint.save(self.radar,100,2401)
        report=export(self.path,self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute('DELETE FROM companions WHERE digest=?',(report['sha256'],))
        with self.assertRaisesRegex(ValueError,'companion'):
            restore_bundle(self.archive,report['sha256'],Path(self.tmp.name)/'restored')
        self.path.with_name('fvg-curve.sqlite3').unlink()
        with self.assertRaisesRegex(ValueError,'missing'):
            export(self.path,Path(self.tmp.name)/'new-archive.sqlite3')

    def test_export_uses_checkpoint_boundary_when_live_curve_archive_advanced(self):
        self.radar.curve=[{'time':i*60,'equity':self.cfg['initial_equity']-i%100,
                           'balance':self.cfg['initial_equity'],'open_risk':0} for i in range(3000)]
        self.checkpoint.archive_curve(self.radar,0)
        self.checkpoint.save(self.radar,0,3000)
        self.radar.curve.extend({'time':i*60,'equity':self.cfg['initial_equity'],
                                 'balance':self.cfg['initial_equity'],'open_risk':0} for i in range(3000,3100))
        self.checkpoint.archive_curve(self.radar,0)
        report=export(self.path,self.archive)
        self.assertEqual(report['curve_archived_count'],600)
        files=restore_bundle(self.archive,report['sha256'],Path(self.tmp.name)/'older-snapshot')
        with closing(sqlite3.connect(files['curve'])) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM points').fetchone()[0],600)
        resumed,_,_=FVGCheckpoint(files['checkpoint'],'test',self.cfg).load(self.cfg)
        self.assertEqual(len(resumed.curve),2400)
