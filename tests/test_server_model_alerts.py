import pathlib
import tempfile
import unittest

from app.notifications import AlertStore
from app.server import model_telegram_alerts


class ModelAlertViewTests(unittest.TestCase):
    def test_early_plan_and_confirmation_are_visible_for_the_right_model(self):
        with tempfile.TemporaryDirectory() as folder:
            store=AlertStore(pathlib.Path(folder)/'alerts.sqlite3')
            events=[
                {'id':'sbr-plan','time':1000,'tf':'M5','type':'sbr_sell_plan',
                 'cooldown_key':'sbr_sell_plan:level-a'},
                {'id':'rbs-plan','time':1100,'tf':'M5','type':'rbs_buy_plan',
                 'cooldown_key':'rbs_buy_plan:level-b'},
                {'id':'sbr-confirmed','time':1200,'tf':'M5','type':'sbr_sell_confirmed'},
            ]
            for event in events:
                self.assertTrue(store.add(event,event['time'],event['time'],True))
            sbr=model_telegram_alerts(store,'sbr_sell_confirmed','sbr_sell_plan:*')
            rbs=model_telegram_alerts(store,'rbs_buy_confirmed','rbs_buy_plan:*')
            self.assertEqual([x['id'] for x in sbr],['sbr-confirmed','sbr-plan'])
            self.assertEqual([x['id'] for x in rbs],['rbs-plan'])
            self.assertTrue(all(x['delivery']=='pending' for x in sbr+rbs))


if __name__=='__main__':
    unittest.main()
