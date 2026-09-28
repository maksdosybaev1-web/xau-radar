import unittest
import uuid
from unittest.mock import patch

from app.datafeed import ROOT
from app.notifications import AlertStore, format_alert
from app.snr_sbr_live import SBRForward
from app.snr_rbs_live import RBSForward


class SNRPlanLifecycleTests(unittest.TestCase):
    def test_sent_plan_receives_matching_terminal_reason_once(self):
        for label, forward_type, module, broken_reason in (
            ('SBR', SBRForward, 'app.snr_sbr_live.public_settings', 'close_below_support'),
            ('RBS', RBSForward, 'app.snr_rbs_live.public_settings', 'close_above_resistance'),
        ):
            with self.subTest(label=label):
                journal=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
                database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
                try:
                    alerts=AlertStore(database)
                    forward=forward_type('XAUUSD','test-server',100,journal,alerts)
                    forward.finish_bootstrap()
                    forward.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
                    events=[{'id':'break','time':160,'state':'broken','level_id':'level-1',
                             'reason':broken_reason},
                            {'id':'end','time':220,'state':'retest_unconfirmed',
                             'level_id':'level-1','reason':'first_retest_failed'}]
                    forward.model.step=lambda row:forward.model.events.append(events.pop(0))
                    with patch(module,return_value={'enabled':True}):
                        forward.ingest({},170,169)
                        self.assertEqual(alerts.recent()[0]['scenario_lifecycle']['valid_until'], 3760)
                        sent=[]
                        alerts.drain(170,{'enabled':True},lambda body,_:sent.append(body))
                        self.assertEqual(len(sent),1)
                        plan_kind=('sbr_sell_plan:' if label=='SBR' else 'rbs_buy_plan:')+'level-1'
                        self.assertTrue(alerts.sent_plan(plan_kind,forward.source_hash,'XAUUSD'))
                        self.assertFalse(alerts.sent_plan(plan_kind,'another-source','XAUUSD'))
                        forward.ingest({},230,229)
                    latest=alerts.recent()[0]
                    self.assertEqual(latest['type'],label.lower()+'_buy_closed' if label=='RBS'
                                     else 'sbr_sell_closed')
                    self.assertEqual(latest['scenario_lifecycle']['stage'], 'ended')
                    rendered=format_alert(latest)
                    self.assertIn('Первый ретест не подтвердил сценарий',rendered)
                    self.assertIn('Сценарий: '+label+'-level-1',rendered)
                    self.assertIn('Сценарий: '+label+'-level-1',sent[0])
                    alerts.drain(230,{'enabled':True},lambda body,_:sent.append(body))
                    self.assertEqual(len(sent),2)
                    resumed=forward_type('XAUUSD','test-server',240,journal,alerts)
                    resumed.finish_bootstrap()
                    self.assertEqual(len(alerts.recent()),2)
                finally:
                    journal.unlink(missing_ok=True)
                    database.unlink(missing_ok=True)

    def test_unsent_plan_has_no_misleading_closure(self):
        journal=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
        database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(database)
            forward=SBRForward('XAUUSD','test-server',100,journal,alerts)
            forward.finish_bootstrap()
            forward.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
            events=[{'id':'break','time':160,'state':'broken','level_id':'level-1',
                     'reason':'close_below_support'},
                    {'id':'end','time':220,'state':'entry_rejected','level_id':'level-1',
                     'reason':'no_known_target'}]
            forward.model.step=lambda row:forward.model.events.append(events.pop(0))
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                forward.ingest({},170,169)
                forward.ingest({},230,229)
            records=alerts.recent()
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]['type'],'sbr_sell_closed')
            self.assertEqual(records[0]['delivery'],'local_only')
            self.assertEqual(records[1]['type'],'sbr_sell_plan')
            self.assertEqual(records[1]['delivery'],'expired')
            sent=[]
            alerts.drain(231,{'enabled':True},lambda body,_:sent.append(body))
            self.assertEqual(sent,[])
        finally:
            journal.unlink(missing_ok=True)
            database.unlink(missing_ok=True)

    def test_confirmation_supersedes_undelivered_early_plan(self):
        journal=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
        database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(database)
            forward=SBRForward('XAUUSD','test-server',100,journal,alerts)
            forward.finish_bootstrap()
            forward.model.levels['level-1']={'low_band':100,'high_band':102,'level':101,'atr':4,
                                             'first_touch':{'close':99}}
            events=[{'id':'break','time':160,'state':'broken','level_id':'level-1',
                     'reason':'close_below_support'},
                    {'id':'ready','time':220,'state':'signal_ready','level_id':'level-1',
                     'reason':'closed_bar_observation_only','stop':103,'target':95}]
            forward.model.step=lambda row:forward.model.events.append(events.pop(0))
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                forward.ingest({},170,169)
                forward.ingest({},230,229)
            records=alerts.recent()
            self.assertEqual(records[0]['type'],'sbr_sell_confirmed')
            self.assertEqual(records[0]['delivery'],'pending')
            self.assertEqual(records[1]['type'],'sbr_sell_plan')
            self.assertEqual(records[1]['delivery'],'expired')
        finally:
            journal.unlink(missing_ok=True)
            database.unlink(missing_ok=True)

    def test_sent_plan_from_another_source_does_not_send_closure(self):
        journal_a=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
        journal_b=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
        database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(database)
            a=SBRForward('XAUUSD','source-A',100,journal_a,alerts)
            a.finish_bootstrap()
            a.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
            a.model.step=lambda row:a.model.events.append(
                {'id':'break-A','time':160,'state':'broken','level_id':'level-1',
                 'reason':'close_below_support'})
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                a.ingest({},170,169)
                alerts.drain(170,{'enabled':True},lambda *_:None)
                b=SBRForward('XAUUSD','source-B',100,journal_b,alerts)
                b.finish_bootstrap()
                b.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
                b.model.step=lambda row:b.model.events.append(
                    {'id':'end-B','time':220,'state':'retest_unconfirmed',
                     'level_id':'level-1','reason':'first_retest_failed'})
                b.ingest({},230,229)
            self.assertEqual(alerts.recent()[0]['type'],'sbr_sell_closed')
            self.assertEqual(alerts.recent()[0]['delivery'],'local_only')
        finally:
            journal_a.unlink(missing_ok=True)
            journal_b.unlink(missing_ok=True)
            database.unlink(missing_ok=True)

    def test_in_flight_plan_waits_for_delivery_result_before_closure(self):
        journal=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
        database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
        try:
            alerts=AlertStore(database)
            forward=SBRForward('XAUUSD','test-server',100,journal,alerts)
            forward.finish_bootstrap()
            forward.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
            events=[{'id':'break','time':160,'state':'broken','level_id':'level-1',
                     'reason':'close_below_support'},
                    {'id':'end','time':220,'state':'retest_unconfirmed','level_id':'level-1',
                     'reason':'first_retest_failed'}]
            forward.model.step=lambda row:forward.model.events.append(events.pop(0))
            with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
                forward.ingest({},170,169)
                with alerts.connect() as db:
                    db.execute('UPDATE alerts SET status="sending" WHERE kind=?',
                               ('sbr_sell_plan:level-1',))
                forward.ingest({},230,229)
            self.assertEqual(alerts.recent()[0]['delivery'],'pending')
            sent=[]
            alerts.drain(231,{'enabled':True},lambda text,_:sent.append(text))
            self.assertEqual(sent,[])
            with alerts.connect() as db:
                db.execute('UPDATE alerts SET status="sent" WHERE kind=?',
                           ('sbr_sell_plan:level-1',))
            alerts.drain(232,{'enabled':True},lambda text,_:sent.append(text))
            self.assertEqual(len(sent),1)
            self.assertIn('план завершён',sent[0])
        finally:
            journal.unlink(missing_ok=True)
            database.unlink(missing_ok=True)

    def test_stuck_in_flight_plan_does_not_keep_closure_pending_forever(self):
        for kind in ('sbr_sell_closed', 'rbs_buy_closed'):
            with self.subTest(kind=kind):
                database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
                try:
                    alerts=AlertStore(database)
                    prefix='sbr_sell_plan:' if kind.startswith('sbr') else 'rbs_buy_plan:'
                    source='source-A'
                    alerts.add({'id':'plan','time':160,'tf':'M5','type':prefix[:-1],
                                'cooldown_key':prefix+'level-1','symbol':'XAUUSD',
                                'source_hash':source,'level_id':'level-1'},170,169,True)
                    with alerts.connect() as db:
                        db.execute('UPDATE alerts SET status="sending" WHERE id="plan"')
                    alerts.add({'id':'end','time':220,'tf':'M5','type':kind,
                                'cooldown_key':kind+':level-1','symbol':'XAUUSD',
                                'source_hash':source,'level_id':'level-1'},230,229,True)
                    alerts.drain(401,{'enabled':True},lambda *_:self.fail('late delivery'))
                    self.assertEqual(alerts.recent()[0]['delivery'],'expired')
                finally:
                    database.unlink(missing_ok=True)

    def test_late_terminal_stays_local_and_ends_snr_scenario(self):
        for label, forward_type, module, broken_reason in (
            ('SBR', SBRForward, 'app.snr_sbr_live.public_settings', 'close_below_support'),
            ('RBS', RBSForward, 'app.snr_rbs_live.public_settings', 'close_above_resistance'),
        ):
            with self.subTest(label=label):
                journal=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.jsonl')
                database=ROOT/'runtime'/('plan-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
                try:
                    alerts=AlertStore(database)
                    forward=forward_type('XAUUSD','test-server',100,journal,alerts)
                    forward.finish_bootstrap()
                    forward.model.levels['level-1']={'low_band':100,'high_band':102,'atr':4}
                    events=[{'id':'break','time':160,'state':'broken','level_id':'level-1',
                             'reason':broken_reason},
                            {'id':'end','time':220,'state':'retest_unconfirmed',
                             'level_id':'level-1','reason':'first_retest_failed'}]
                    forward.model.step=lambda row:forward.model.events.append(events.pop(0))
                    with patch(module,return_value={'enabled':True}):
                        forward.ingest({},170,169)
                        forward.ingest({},400,399)
                    records=alerts.recent()
                    self.assertEqual(records[0]['delivery'],'local_only')
                    self.assertEqual(records[0]['scenario_lifecycle']['stage'],'ended')
                    self.assertEqual(records[0]['observed_at'],400)
                    self.assertEqual(records[1]['delivery'],'expired')
                    sent=[]
                    alerts.drain(401,{'enabled':True},lambda *args:sent.append(args))
                    self.assertFalse(sent)
                    self.assertEqual(len(alerts.recent()),2)
                finally:
                    journal.unlink(missing_ok=True)
                    database.unlink(missing_ok=True)


if __name__=='__main__': unittest.main()
