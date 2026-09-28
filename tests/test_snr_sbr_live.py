import json
import pathlib
import unittest
import uuid
from unittest.mock import patch

from app.datafeed import ROOT
from app.notifications import AlertStore, format_alert
from app.snr_sbr_live import SBRForward


class ForwardJournalTests(unittest.TestCase):
    def test_broken_support_sends_preliminary_retest_plan(self):
        path=ROOT/'runtime'/('sbr-test-'+uuid.uuid4().hex+'.jsonl')
        db=ROOT/'runtime'/('sbr-test-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(db)
            forward=SBRForward('XAUUSD','test-server',100,path,alerts)
            forward.finish_bootstrap()
            forward.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
            broken={'id':'break','time':160,'state':'broken','level_id':'level-1',
                    'source':'MT5 · XAUUSD','reason':'close_below_support'}
            forward.model.step=lambda row:forward.model.events.append(broken)
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                forward.ingest({},170,169)
            alert=alerts.recent()[0]
            self.assertEqual(alert['type'],'sbr_sell_plan')
            self.assertIn('Шорт 100–102 на ретесте',format_alert(alert))
            self.assertIn('ТП 99.6, 98.2, 96.8',format_alert(alert))
            self.assertIn('Ретест ещё не подтверждён',format_alert(alert))
        finally:
            path.unlink(missing_ok=True)
            db.unlink(missing_ok=True)

    def test_bootstrap_suppression_and_restart_dedup(self):
        path=ROOT/'runtime'/('sbr-test-'+uuid.uuid4().hex+'.jsonl')
        db=ROOT/'runtime'/('sbr-test-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(db)
            forward=SBRForward('XAUUSD','test-server',100,path,alerts)
            old={'id':'old','time':90,'state':'confirmed','reason':'test'}
            new={'id':'new','time':160,'state':'signal_ready','reason':'test',
                 'level_id':'level-1','source':'MT5 · XAUUSD','stop':101,'target':95}
            forward.model.levels['level-1']={'first_touch':{'close':99},'level':100}
            forward.model.step=lambda row:forward.model.events.append(old)
            forward.ingest({},100,100)
            self.assertEqual(path.read_text(encoding='utf-8'),'')
            forward.finish_bootstrap()
            forward.model.step=lambda row:forward.model.events.append(new)
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                forward.ingest({},170,165)
            self.assertEqual(forward.state(170,165)['forward_events_total'],1)
            row=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(row['observation_quality'],'timely')
            self.assertEqual(row['result_status'],'not_evaluated')
            queued=alerts.recent()
            self.assertEqual(len(queued),1)
            self.assertEqual(queued[0]['delivery'],'pending')
            self.assertIn('Шорт · ретест подтверждён M5',format_alert(queued[0]))
            sent=[];alerts.drain(170,{'enabled':True},lambda message,_:sent.append(message))
            self.assertEqual(len(sent),1)
            self.assertEqual(alerts.recent()[0]['delivery'],'sent')
            resumed=SBRForward('XAUUSD','test-server',180,path,alerts)
            resumed.finish_bootstrap()
            resumed.model.step=lambda row:resumed.model.events.append(new)
            resumed.ingest({},190,185)
            self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()),1)
            self.assertEqual(len(alerts.recent()),1)
        finally:
            path.unlink(missing_ok=True)
            db.unlink(missing_ok=True)

    def test_late_bar_and_stale_quote_are_journaled_without_telegram_alert(self):
        path=ROOT/'runtime'/('sbr-test-'+uuid.uuid4().hex+'.jsonl')
        db=ROOT/'runtime'/('sbr-test-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(db)
            forward=SBRForward('XAUUSD','test-server',100,path,alerts)
            forward.finish_bootstrap()
            events=[{'id':'late','time':160,'state':'signal_ready','level_id':'level-1',
                     'source':'MT5 · XAUUSD','stop':101,'target':95},
                    {'id':'stale','time':270,'state':'signal_ready','level_id':'level-1',
                     'source':'MT5 · XAUUSD','stop':101,'target':95}]
            forward.model.step=lambda row:forward.model.events.append(events.pop(0))
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                forward.ingest({},260,255)
                forward.ingest({},280,150)
            quality=[json.loads(line)['observation_quality'] for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(quality,['late_bar','stale_quote'])
            self.assertEqual(alerts.recent(),[])
        finally:
            path.unlink(missing_ok=True)
            db.unlink(missing_ok=True)


if __name__=='__main__':unittest.main()
