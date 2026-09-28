import copy
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.ai_journal import snapshot, validate_review, verified_observations
from app.notifications import AlertStore, delivery_worker
from app.snr_sbr_live import SBRForward
from app.snr_rbs_live import RBSForward
from app.snr_classic import ClassicObservation


class AuditFixTests(unittest.TestCase):
    def test_worker_survives_status_failure_and_drain_failure(self):
        stop=Mock();stop.is_set.side_effect=[False,False,True]
        store=Mock();store.drain.side_effect=[sqlite3.OperationalError('locked'),None]
        with patch('app.notifications.AlertStore',return_value=store), \
             patch('app.operations.heartbeat',side_effect=PermissionError('locked')):
            delivery_worker(stop)
        self.assertEqual(store.drain.call_count,2)
        self.assertEqual(stop.wait.call_count,2)

    def test_outbox_recovers_after_restart_and_never_resends(self):
        for cls,module in [(SBRForward,'snr_sbr_live'),(RBSForward,'snr_rbs_live')]:
            with self.subTest(module=module),tempfile.TemporaryDirectory() as tmp:
                folder=pathlib.Path(tmp);store=AlertStore(folder/'alerts.db');journal=folder/'events.jsonl'
                f=cls('XAUUSD','audit',100,journal,store);f.finish_bootstrap()
                event=dict(id='one',time=160,state='signal_ready',level_id='one',source='audit',stop=101,target=95)
                f.model.levels['one']={'first_touch':{'close':99},'level':100}
                f.model.step=lambda row:f.model.events.append(event)
                with patch('app.'+module+'.public_settings',return_value={'enabled':True}):
                    with patch.object(store,'add',side_effect=sqlite3.OperationalError('locked')):
                        with self.assertRaises(sqlite3.OperationalError):f.ingest({},170,165)
                    restored=cls('XAUUSD','audit',175,journal,store)
                    restored.state(175,175)
                    self.assertEqual(len(store.recent()),1)
                    sent=[];store.drain(175,{'enabled':True},lambda *args:sent.append(args))
                    cls('XAUUSD','audit',180,journal,store).state(180,180)
                    store.drain(180,{'enabled':True},lambda *args:sent.append(args))
                    self.assertEqual(len(sent),1)
                self.assertEqual(len(journal.read_text(encoding='utf-8').splitlines()),1)

    def test_recovery_does_not_queue_expired_or_previously_disabled_alert(self):
        from app.journal_outbox import reconcile
        with tempfile.TemporaryDirectory() as tmp:
            store=AlertStore(pathlib.Path(tmp)/'alerts.db')
            event=dict(id='one',time=100,tf='M5',type='watch')
            pending={'one':{'delivery_alert':event,'quote_time':100,'delivery_enabled':True}}
            reconcile(pending,store,300,True);self.assertEqual(store.recent(),[])
            pending={'one':{'delivery_alert':event,'quote_time':100,'delivery_enabled':False}}
            reconcile(pending,store,110,True)
            self.assertEqual(store.recent()[0]['delivery'],'local_only')

    def test_classic_expires_before_m1_touch(self):
        m=ClassicObservation('test','test');known=892500;t=known+24*300+60
        level=dict(id='one',model='A',direction='sell',state='armed',known_at=known,armed_at=known,low_band=99.9,high_band=100.1)
        m.levels['one']=level;m.active['one']=level;m.last=t-60
        row={'time':t,'volume':1}
        for side in ['bid','ask']:
            row.update({side+'_open':100,side+'_close':100,side+'_low':99.95,side+'_high':100.05})
        m.step(row)
        self.assertEqual(level['state'],'expired')

    def test_ai_recounts_wins_average_and_paired(self):
        run={'version':'test','source_hash':'test','experiments':[
            {'candidate_id':'one','mode':mode,'state':'closed','r':1} for mode in ['touch_limit','after_close']],
            'summary':{'paired_closed':1,'by_mode':{mode:{'total':1,'closed':1,'wins':1,'average_r':1}
                       for mode in ['touch_limit','after_close']}}}
        forward=dict(forward_since=1,quote_time=2,stale=False,forward_events_total=1,paper_outcomes_total=1,tick_checks_total=1)
        self.assertEqual(snapshot(run,forward)['paired_closed'],1)
        for key in ['wins','average_r','paired_closed']:
            bad=copy.deepcopy(run)
            if key=='paired_closed':bad['summary'][key]=999
            else:bad['summary']['by_mode']['touch_limit'][key]=999
            with self.subTest(key=key),self.assertRaises(ValueError):snapshot(bad,forward)

    def test_ai_dynamic_reasons_empty_ties_and_nonzero_outcomes(self):
        facts={'modes':{'touch_limit':{'reasons':{'spread_guard':1,'limit_crossed_at_open':99}},
                        'after_close':{'reasons':{}}},'paired_closed':5,'forward':{'paper_outcomes':10}}
        text=' '.join(verified_observations(facts))
        self.assertIn('открытие за уровнем (по 99)',text)
        self.assertIn('опытов: 5',text);self.assertIn('исходов: 10',text)
        facts['modes']['touch_limit']['reasons']={'a':2,'b':2}
        self.assertIn('a, b (по 2)',verified_observations(facts)[0])

    def test_ai_malformed_top_level_rejected(self):
        for answer in [[],None,42,'text']:
            with self.subTest(answer=answer),self.assertRaises(ValueError):validate_review(answer,{})

if __name__=='__main__':unittest.main()
