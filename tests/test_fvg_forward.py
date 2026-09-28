import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.fvg_forward import FVGForward
from app.notifications import AlertStore, format_alert


def event(kind, stamp, zone='z1'):
    return {'id':stamp, 'time':stamp, 'type':kind, 'zone_id':zone,
            'direction':'buy', 'low':3300.0, 'high':3305.0,
            'child_low':3301.0, 'child_high':3302.0,
            'stop':3299.0, 'reason':'Условия выполнены' if kind=='confirmed' else 'Цена приблизилась'}


class FVGForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=pathlib.Path(self.tmp.name)
        self.store=AlertStore(self.folder/'alerts.sqlite3')
        self.journal=self.folder/'events.jsonl'

    def forward(self, since=100):
        return FVGForward('XAUUSD','MT5|demo|login','rules',since,self.journal,self.store)

    def records(self):
        return [json.loads(line) for line in self.journal.read_text(encoding='utf-8').splitlines()]

    def test_fresh_zone_events_are_delivered_once_after_restart(self):
        alerts=[event('near',160),event('confirmed',170)]
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist(alerts,175,174)
            self.assertEqual(len(self.records()),2)
            self.assertEqual([row['delivery'] for row in self.store.recent()],['pending','expired'])
            messages=[]
            self.store.drain(176,{'enabled':True},lambda message,_:messages.append(message))
            self.assertEqual(len(messages),1)
            self.assertIn('Расчётный стоп:',messages[0])
            self.assertIn('Это не вход',messages[0])
            self.forward().persist(alerts,180,180)
            self.store.drain(180,{'enabled':True},lambda message,_:messages.append(message))
            self.assertEqual(len(messages),1)
            self.assertEqual(len(self.records()),2)

    def test_early_plan_reaches_queue_only_for_timely_near_event(self):
        early=event('near',160)
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist([early],170,169,3302.5,3302.7)
            messages=[]
            self.store.drain(171,{'enabled':True},lambda message,_:messages.append(message))
        self.assertEqual(len(messages),1)
        self.assertIn('Лонг 3301–3302\nСтоп 3299.9\nТП 3303.1, 3304.7, 3306.3',messages[0])
        self.assertIn('Отмена: закрытие M5 ниже 3301',messages[0])
        self.assertIn('Вход ещё не подтверждён',messages[0])
        self.assertEqual(self.records()[0]['delivery_alert']['analysis_plan']['known_at'],160)
        self.assertEqual(self.records()[0]['delivery_alert']['scenario_lifecycle']['valid_until'],1060)

    def test_early_plan_not_claimed_if_first_target_passed_or_same_bar_cancelled(self):
        self.assertIsNone(self.forward().early_plan(event('near',160),3303.2,3303.4))
        cancelled=event('cancelled',160)
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist([event('near',160),cancelled],170,169,3302.5,3302.7)
        self.assertIsNone(self.records()[0]['delivery_alert']['analysis_plan'])

    def test_archive_late_bar_and_stale_quote_never_queue(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward(150).persist([event('near',100),event('near',160),event('confirmed',170)],300,300)
            self.forward(150).persist([event('near',400,'z2')],410,300)
        rows=self.records()
        self.assertEqual([row['observation_quality'] for row in rows],['late_bar','late_bar','stale_quote'])
        self.assertTrue(all('delivery_alert' not in row for row in rows))
        self.assertEqual(self.store.recent(),[])

    def test_disabled_at_observation_stays_local_after_enable(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            self.forward().persist([event('near',160)],170,169)
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().flush_pending(175)
        self.assertEqual(self.store.recent()[0]['delivery'],'local_only')

    def test_different_zones_are_not_suppressed_by_cooldown(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist([event('near',160),event('near',170,'z2')],175,174)
        self.assertEqual([row['delivery'] for row in self.store.recent()],['pending','pending'])

    def test_journal_recovers_after_queue_failure(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            forward=self.forward()
            with patch.object(self.store,'add',side_effect=sqlite3.OperationalError('locked')):
                with self.assertRaises(sqlite3.OperationalError):
                    forward.persist([event('near',160)],170,169)
            self.assertEqual(len(self.records()),1)
            self.forward().flush_pending(175)
            self.assertEqual(self.store.recent()[0]['delivery'],'pending')

    def test_cancellation_after_near_is_delivered_once_with_real_reason(self):
        cancelled=event('cancelled',170)
        cancelled['reason']='Старшая структура больше не подтверждает направление'
        cancelled.pop('stop');cancelled.pop('child_low');cancelled.pop('child_high')
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist([event('near',160),cancelled],175,174)
            messages=[]
            self.store.drain(176,{'enabled':True},lambda text,_:messages.append(text))
            self.assertEqual(len(messages),1)
            self.assertIn('Сценарий отменён',messages[0])
            self.assertIn(cancelled['reason'],messages[0])
            self.assertIn('завершено',messages[0])
            self.assertNotIn('Расчётный стоп',messages[0])
            self.forward().persist([cancelled],180,180)
            self.store.drain(180,{'enabled':True},lambda text,_:messages.append(text))
            self.assertEqual(len(messages),1)

    def test_cancellation_stale_and_disabled_stay_unsent(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist([event('cancelled',160)],300,300)
            self.forward().persist([event('cancelled',400)],410,300)
        self.assertEqual(len(self.store.recent()),2)
        self.assertTrue(all(row['delivery']=='local_only' for row in self.store.recent()))
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            self.forward().persist([event('cancelled',500)],510,509)
        sent=[]
        self.store.drain(520,{'enabled':True},lambda *args:sent.append(args))
        self.assertFalse(sent)
        self.assertIn(self.store.recent()[0]['delivery'],('local_only','suppressed'))

    def test_late_cancellation_ends_card_and_replays_without_telegram(self):
        from app.scenario_workspace import ScenarioWorkspace
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            self.forward().persist([event('near',160)],170,169,3302.5,3302.7)
            self.forward().persist([event('cancelled',220)],400,399)
            self.forward().flush_pending(401)
        records=self.records()
        self.assertEqual(records[1]['observation_quality'],'late_bar')
        self.assertTrue(records[1]['delivery_local_only'])
        self.assertEqual(self.store.recent()[0]['delivery'],'local_only')
        self.assertEqual(self.store.recent()[1]['delivery'],'expired')
        with patch('app.scenario_workspace.ROOT',self.folder):
            card=ScenarioWorkspace(self.store.path).view(401)['scenarios'][0]
        self.assertEqual(card['stage'],'ended')
        sent=[]
        self.store.drain(401,{'enabled':True},lambda *args:sent.append(args))
        self.assertFalse(sent)
        self.assertEqual(len(self.store.recent()),2)

    def test_cancellation_queue_recovers_after_error(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            with patch.object(self.store,'add',side_effect=sqlite3.OperationalError('locked')):
                with self.assertRaises(sqlite3.OperationalError):
                    self.forward().persist([event('cancelled',160)],170,169)
            self.forward().flush_pending(175)
        self.assertEqual(self.store.recent()[0]['type'],'fvg_cancelled')
        self.assertEqual(self.store.recent()[0]['delivery'],'pending')

    def test_index_failure_recovers_without_duplicate_after_restart(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            forward=self.forward()
            with patch.object(forward.seen,'add',side_effect=sqlite3.OperationalError('locked')):
                with self.assertRaises(sqlite3.OperationalError):
                    forward.persist([event('near',160)],170,169)
            self.assertEqual(len(self.records()),1)
            resumed=self.forward()
            resumed.persist([event('near',160)],175,174)
        self.assertEqual(len(self.records()),1)
        self.assertEqual(len(resumed.seen),1)
        self.assertEqual(self.store.recent()[0]['delivery'],'local_only')

    def test_index_failure_recovers_without_duplicate_in_same_process(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':True}):
            forward=self.forward()
            with patch.object(forward.seen,'add',side_effect=sqlite3.OperationalError('locked')):
                with self.assertRaises(sqlite3.OperationalError):
                    forward.persist([event('near',160)],170,169)
            forward.persist([event('near',160)],175,174)
        self.assertEqual(len(self.records()),1)
        self.assertEqual(len(self.store.recent()),1)
        self.assertEqual(self.store.recent()[0]['delivery'],'pending')

    def test_same_time_and_bounds_in_distinct_zones_have_distinct_keys(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            forward=self.forward()
            first=event('near',160,'one')
            second=event('near',160,'two')
            self.assertNotEqual(forward.key(first),forward.key(second))
            forward.persist([first,second],170,169)
        self.assertEqual(len(self.records()),2)
        self.assertNotEqual(self.records()[0]['dedup_key'],self.records()[1]['dedup_key'])

    def test_legacy_zone_key_is_migrated_without_suppressing_other_zone(self):
        seed=self.forward()
        first=event('near',160,'one')
        second=event('near',160,'two')
        legacy=dict(first,dedup_key=seed.legacy_key(first),
                    source_hash=seed.source_hash,config_hash='rules',
                    quote_time=169,received_at=170,observation_quality='timely')
        self.journal.write_text(json.dumps(legacy)+'\n',encoding='utf-8')
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            resumed=self.forward()
            resumed.persist([first,second],175,174)
            self.forward().persist([first,second],180,179)
        self.assertEqual(len(self.records()),2)
        self.assertEqual([row['zone_id'] for row in self.records()],['one','two'])

    def test_wrong_identity_rejected_before_index_rebuild_or_replay(self):
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            self.forward().persist([event('near',160)],170,169)
        original=self.journal.read_bytes()
        for source,config in [('other','rules'),('MT5|demo|login','other')]:
            with self.subTest(source=source,config=config), \
                 patch('app.fvg_forward.JournalKeys') as index, \
                 patch.object(self.store,'add') as add:
                with self.assertRaises(ValueError):
                    FVGForward('XAUUSD',source,config,100,self.journal,self.store)
                index.assert_not_called()
                add.assert_not_called()
                self.assertEqual(self.journal.read_bytes(),original)

    def test_large_journal_recovery_does_not_preload_alerts(self):
        seed=self.forward()
        records=[]
        for i in range(1000):
            records.append(dict(event('near',160+i),dedup_key=str(i),
                                source_hash=seed.source_hash,config_hash='rules',quote_time=1160,
                                delivery_enabled=False,delivery_alert={'id':str(i)}))
        self.journal.write_text('\n'.join(json.dumps(r) for r in records)+'\n',encoding='utf-8')
        store=Mock()
        resumed=FVGForward('XAUUSD','MT5|demo|login','rules',100,self.journal,store)
        self.assertEqual(len(resumed.pending_delivery),0)
        self.assertTrue(resumed.pending_delivery)
        self.assertEqual(len(resumed.seen),1000)
        with patch('app.fvg_forward.public_settings',return_value={'enabled':False}):
            resumed.flush_pending(1160)
        self.assertEqual(store.add.call_count,1000)
        self.assertFalse(resumed.pending_delivery)


if __name__=='__main__':unittest.main()
