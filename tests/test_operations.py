import json,pathlib,sqlite3,tempfile,threading,unittest
from contextlib import closing
from unittest.mock import patch
from app.notifications import AlertStore
from app.operations import summarize_health,export_csv,heartbeat,read_status

class OperationTests(unittest.TestCase):
    def test_working_bridge_with_old_quote_is_not_fresh_market(self):
        s=summarize_health({'updated_at':1000,'state':'running','quote_time':500},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertTrue(s['bridge']['alive']);self.assertFalse(s['quote']['fresh']);self.assertFalse(s['ready_for_new_alerts'])

    def test_dead_or_failed_delivery_not_ready(self):
        for worker in [{'updated_at':800,'state':'running'},{'updated_at':1000,'state':'error'}]:
            s=summarize_health({'updated_at':1000,'state':'running','quote_time':1000},worker,{'enabled':True,'configured':True},1001)
            self.assertFalse(s['ready_for_new_alerts'])

    def test_all_components_fresh_and_configured(self):
        s=summarize_health({'updated_at':1000,'state':'running','quote_time':1000,'last_m1':900},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertTrue(s['ready_for_new_alerts'])

    # --- New: overall status tests ---
    def test_overall_green_when_all_healthy(self):
        s=summarize_health({'updated_at':1000,'state':'running','quote_time':1000,'last_m1':900},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertEqual(s['overall']['label'],'Работает')
        self.assertEqual(s['overall']['color'],'green')

    def test_overall_yellow_when_quote_stale(self):
        s=summarize_health({'updated_at':1000,'state':'running','quote_time':500},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertEqual(s['overall']['label'],'Котировка устарела')
        self.assertEqual(s['overall']['color'],'yellow')

    def test_overall_yellow_when_retrying(self):
        s=summarize_health({'updated_at':1000,'state':'retrying'},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertEqual(s['overall']['label'],'Переподключается к MT5')
        self.assertEqual(s['overall']['color'],'yellow')

    def test_overall_yellow_when_starting(self):
        s=summarize_health({'updated_at':1000,'state':'starting'},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertEqual(s['overall']['label'],'Мост запускается')
        self.assertEqual(s['overall']['color'],'yellow')

    def test_overall_red_when_bridge_dead(self):
        s=summarize_health({'updated_at':800,'state':'running','quote_time':800},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertEqual(s['overall']['label'],'Нет свежего статуса MT5')
        self.assertEqual(s['overall']['color'],'red')
        self.assertEqual(s['bridge']['state'],'stale')
        self.assertEqual(s['bridge']['reported_state'],'running')

    def test_fresh_tick_and_heartbeat_do_not_hide_stale_or_missing_m1(self):
        bridge={'updated_at':1000,'state':'running','quote_time':1000}
        worker={'updated_at':1000,'state':'running'}
        telegram={'enabled':True,'configured':True}
        for last in (None,820,942,1100):
            with self.subTest(last_m1=last):
                s=summarize_health(dict(bridge,last_m1=last),worker,telegram,1001)
                self.assertTrue(s['quote']['fresh'])
                self.assertFalse(s['ready_for_new_alerts'])
                self.assertFalse(s['m1']['fresh'])
                self.assertEqual(s['overall']['label'],'Нет свежей закрытой M1')
        for last in (821,941):
            with self.subTest(last_m1=last):
                s=summarize_health(dict(bridge,last_m1=last),worker,telegram,1001)
                self.assertTrue(s['ready_for_new_alerts'])

    def test_overall_reflects_telegram_and_delivery_failure(self):
        bridge={'updated_at':1000,'state':'running','quote_time':1000,'last_m1':900}
        running={'updated_at':1000,'state':'running'}
        cases=[
            (running,{'enabled':False,'configured':True},'Telegram выключен','yellow'),
            (running,{'enabled':True,'configured':False},'Telegram не настроен','yellow'),
            ({'updated_at':800,'state':'running'},{'enabled':True,'configured':True},'Очередь не отвечает','red'),
            ({'updated_at':1000,'state':'error'},{'enabled':True,'configured':True},'Ошибка очереди','red'),
            ({'updated_at':1000,'state':'starting'},{'enabled':True,'configured':True},'Очередь запускается','yellow'),
        ]
        for delivery,telegram,label,color in cases:
            with self.subTest(label=label):
                s=summarize_health(bridge,delivery,telegram,1001)
                self.assertEqual(s['overall'],{'label':label,'color':color})
                self.assertFalse(s['ready_for_new_alerts'])
                if telegram['enabled'] and telegram['configured'] and (delivery['state']!='running' or delivery['updated_at']<940):
                    self.assertNotEqual(s['telegram']['label'],'Очередь работает')

    def test_overall_red_when_no_heartbeat(self):
        s=summarize_health({},{},{'enabled':False},1001)
        self.assertEqual(s['overall']['color'],'red')

    def test_bridge_age_seconds_present(self):
        s=summarize_health({'updated_at':990,'state':'running','quote_time':990},{'updated_at':990,'state':'running'},{'enabled':True,'configured':True},1000)
        self.assertEqual(s['bridge']['age_seconds'],10)

    def test_bridge_age_seconds_none_when_no_heartbeat(self):
        s=summarize_health({},{},{'enabled':False},1000)
        self.assertIsNone(s['bridge']['age_seconds'])

    def test_bridge_outage_records_duration_and_m1_boundary_after_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('app.operations.ROOT',pathlib.Path(temp)),patch('app.operations.time.time',side_effect=[1000,1010,1030]):
                heartbeat('bridge','running',quote_time=1000,last_m1=960)
                heartbeat('bridge','retrying',error_type='RatesUnavailable',error_code=-1)
                heartbeat('bridge','running',quote_time=1030,last_m1=1020)
                state=read_status('bridge')
            self.assertNotIn('outage_started_at',state)
            self.assertEqual(state['last_recovery'],{'started_at':1010,'ended_at':1030,
                'duration_seconds':20,'reason':'RatesUnavailable',
                'last_m1_before':960,'last_m1_after':1020})
            self.assertEqual(summarize_health(state,{}, {},1031)['bridge']['last_recovery'],state['last_recovery'])

    def test_m1_gap_remains_visible_after_next_poll(self):
        gap={'before':1000,'after':8200,'missing_minutes':119,
             'classification':'Причина отсутствия M1 у брокера не установлена'}
        with tempfile.TemporaryDirectory() as temp:
            with patch('app.operations.ROOT',pathlib.Path(temp)),patch('app.operations.time.time',side_effect=[8300,8310]):
                heartbeat('bridge','running',quote_time=8300,last_m1=8200,m1_gaps=[gap])
                heartbeat('bridge','running',quote_time=8310,last_m1=8260,m1_gaps=[])
                state=read_status('bridge')
        self.assertEqual(state['m1_gaps'],[gap])

    def test_queue_network_does_not_lock_journal_or_allow_double_send(self):
        with tempfile.TemporaryDirectory() as temp:
            store=AlertStore(pathlib.Path(temp)/'alerts.db')
            event=dict(id='one',time=1000,tf='M15',type='watch',label='WATCH',score=80,reason='test',price=100)
            store.add(event,1000,1000,True);entered=threading.Event();release=threading.Event();calls=[];errors=[]
            def sender(*args):calls.append(args);entered.set();release.wait(3)
            def worker():
                try:store.drain(1000,{'enabled':True},sender)
                except Exception as e:errors.append(e)
            thread=threading.Thread(target=worker);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                store.drain(1000,{'enabled':True},sender)
                self.assertTrue(store.add(dict(event,id='two',tf='H1'),1000,1000,False))
            finally:release.set();thread.join(5)
            self.assertFalse(errors);self.assertEqual(len(calls),1)
            data=export_csv(store).decode('utf-8-sig')
            self.assertIn('one',data);self.assertIn('two',data);self.assertNotIn('token',data)
            self.assertIn('sent',data)

    def test_existing_alert_database_migrates_and_records_api_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            path=pathlib.Path(temp)/'alerts.db'
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute('CREATE TABLE alerts (id TEXT PRIMARY KEY, time INTEGER, tf TEXT, kind TEXT, payload TEXT, status TEXT, attempts INTEGER DEFAULT 0, next_attempt INTEGER DEFAULT 0, detail TEXT DEFAULT "")')
            store=AlertStore(path)
            event=dict(id='plan-1',time=1000,tf='M5',type='watch',label='WATCH',score=80,reason='test',price=100)
            self.assertTrue(store.add(event,1000,1000,True))
            store.drain(1000,{'enabled':True},lambda *_: 58)
            with store.connect() as db:
                receipt=db.execute('SELECT status,sent_at,telegram_message_id FROM alerts WHERE id=?',('plan-1',)).fetchone()
            self.assertEqual(receipt,('sent',1000,58))
            exported=export_csv(store).decode('utf-8-sig')
            self.assertIn('telegram_message_id',exported)
            self.assertIn('58',exported)

    def test_two_process_like_starts_migrate_one_alert_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path=pathlib.Path(temp)/'alerts.db'
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute('CREATE TABLE alerts (id TEXT PRIMARY KEY, time INTEGER, tf TEXT, kind TEXT, payload TEXT, status TEXT, attempts INTEGER DEFAULT 0, next_attempt INTEGER DEFAULT 0, detail TEXT DEFAULT "")')
            barrier=threading.Barrier(3);errors=[]
            def start():
                barrier.wait()
                try:AlertStore(path)
                except Exception as exc:errors.append(exc)
            workers=[threading.Thread(target=start) for _ in range(2)]
            for worker in workers:worker.start()
            barrier.wait()
            for worker in workers:worker.join(5)
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertFalse(errors)
            with closing(sqlite3.connect(path)) as db:
                columns={row[1] for row in db.execute('PRAGMA table_info(alerts)')}
            self.assertTrue({'sent_at','telegram_message_id'}<=columns)

if __name__=='__main__':unittest.main()
