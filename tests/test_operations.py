import json,pathlib,tempfile,threading,unittest
from app.notifications import AlertStore
from app.operations import summarize_health,export_csv

class OperationTests(unittest.TestCase):
    def test_working_bridge_with_old_quote_is_not_fresh_market(self):
        s=summarize_health({'updated_at':1000,'state':'running','quote_time':500},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertTrue(s['bridge']['alive']);self.assertFalse(s['quote']['fresh']);self.assertFalse(s['ready_for_new_alerts'])

    def test_dead_or_failed_delivery_not_ready(self):
        for worker in [{'updated_at':800,'state':'running'},{'updated_at':1000,'state':'error'}]:
            s=summarize_health({'updated_at':1000,'state':'running','quote_time':1000},worker,{'enabled':True,'configured':True},1001)
            self.assertFalse(s['ready_for_new_alerts'])

    def test_all_components_fresh_and_configured(self):
        s=summarize_health({'updated_at':1000,'state':'running','quote_time':1000},{'updated_at':1000,'state':'running'},{'enabled':True,'configured':True},1001)
        self.assertTrue(s['ready_for_new_alerts'])

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

if __name__=='__main__':unittest.main()
