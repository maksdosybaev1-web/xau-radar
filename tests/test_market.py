import copy,json,math,pathlib,tempfile,unittest
from unittest.mock import patch
from app.indicators import Average,Indicators
from app.market_radar import TimeframeRadar,prepare_history,view
from app.notifications import AlertStore,protect,save_settings,public_settings,DeliveryError,send_telegram
import io,urllib.error


def bar(i,close=100,minutes=5,volume=10):
    return dict(time=i*minutes*60,end=(i+1)*minutes*60,open=close-.1,high=close+1,low=close-1,close=close,volume=volume)


class IndicatorTests(unittest.TestCase):
    def test_ema_sma_seed(self):
        a=Average(3);self.assertIsNone(a.add(1));self.assertIsNone(a.add(2))
        self.assertEqual(a.add(3),2);self.assertEqual(a.add(4),3)

    def test_wilder_rsi_known_sequence(self):
        sequence=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28]
        indicator=Indicators()
        for i,c in enumerate(sequence):v=indicator.add(bar(i,c))
        self.assertAlmostEqual(v['rsi'],70.464135,places=5)

    def test_flat_and_trending_edges(self):
        for slope,expected in [(0,50),(.1,100),(-.1,0)]:
            model=Indicators()
            for i in range(210):v=model.add(bar(i,100+slope*i))
            self.assertEqual(v['rsi'],expected);self.assertAlmostEqual(v['atr'],2)
            if slope==0:self.assertAlmostEqual(v['macd_hist'],0)

    def test_volume_uses_only_prior_twenty(self):
        model=Indicators()
        for i in range(20):model.add(bar(i,volume=10))
        self.assertEqual(model.add(bar(20,volume=30))['volume_ratio'],3)

    def test_prefix_invariance_and_score_bounds(self):
        rows=[bar(i,100+i*.03+math.sin(i/9)*2,volume=10+i%17) for i in range(360)]
        a=prepare_history(rows[:310]);b=prepare_history(rows)
        self.assertEqual(a['frames']['M5'],b['frames']['M5'][:310])
        self.assertEqual(a['events'],[e for e in b['events'] if e['time']<=310*300])
        for s in b['frames']['M5']:
            if s['ready']:
                self.assertTrue(0<=s['score']<=100);self.assertEqual(s['score'],sum(p['points'] for p in s['score_parts']))
                for key in ['support','resistance']:
                    if s[key]:self.assertLessEqual(s[key]['confirmed_at'],s['end'])
        snapshot=view(b,300*300,'M5');self.assertTrue(all(x['end']<=300*300 for x in snapshot['chart']))

    def test_missing_m5_cannot_form_m15(self):
        rows=[bar(i) for i in [0,2,3,4,5]]
        result=prepare_history(rows)
        self.assertEqual(len(result['frames']['M15']),1);self.assertEqual(result['frames']['M15'][0]['time'],900)

    def test_requires_200_bars_and_monotonic_time(self):
        model=TimeframeRadar('H1')
        for i in range(199):snap,_=model.add(bar(i,minutes=60))
        self.assertFalse(snap['ready']);self.assertIsNone(snap['score'])
        snap,_=model.add(bar(199,minutes=60));self.assertTrue(snap['ready'])
        with self.assertRaises(ValueError):model.add(bar(199,minutes=60))


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=pathlib.Path(self.temp.name);self.store=AlertStore(self.path/'alerts.db')
        self.event=dict(id='one',time=1000,tf='M15',type='watch',label='WATCH',score=80,reason='test',price=100,rsi=60,ema20=99,ema50=98,atr=2)

    def test_stale_and_future_do_not_queue(self):
        self.assertFalse(self.store.add(self.event,1100,1100,True));self.assertFalse(self.store.add(self.event,1000,800,True))
        self.assertFalse(self.store.add(self.event,999,999,True));self.assertFalse(self.store.recent())

    def test_dedup_survives_restart_and_disabled_never_sends(self):
        self.assertTrue(self.store.add(self.event,1000,1000,False))
        reopened=AlertStore(self.path/'alerts.db');self.assertFalse(reopened.add(self.event,1001,1000,True))
        calls=[];reopened.drain(1001,{'enabled':False},lambda *a:calls.append(a));self.assertFalse(calls)

    def test_cooldown_and_confirmed_delivery(self):
        self.store.add(self.event,1000,1000,True)
        self.store.add(dict(self.event,id='two',time=1060),1060,1060,True)
        calls=[];self.store.drain(1060,{'enabled':True},lambda *a:calls.append(a))
        self.assertEqual(len(calls),1);self.assertEqual({e['delivery'] for e in self.store.recent()},{'sent','suppressed'})
        self.assertIn('Совпали индикаторные условия · проверьте вручную',calls[0][0])
        self.assertNotIn('НАБЛЮДАТЬ',calls[0][0])

    def test_beginner_plan_survives_noisy_indicator_feed(self):
        from app.server import recent_plan_events
        plan=dict(id='plan',time=1000,tf='M5',type='sbr_sell_plan',
                  level_id='level-1',cooldown_key='sbr_sell_plan:level-1',
                  analysis_plan={'side':'short'})
        self.store.add(plan,1000,1000,False)
        for n in range(90):
            self.store.add(dict(id=f'noise-{n}',time=1001+n,tf='M5',
                                type='quote_near',cooldown_key=f'noise-{n}'),1001+n,1001+n,False)
        self.assertNotIn('plan',{e['id'] for e in self.store.recent()})
        self.assertEqual([e['id'] for e in recent_plan_events(self.store,1100)],['plan'])

    def test_expiration_and_uncertain_delivery_no_repeat(self):
        self.store.add(self.event,1000,1000,True);calls=[]
        self.store.drain(1300,{'enabled':True},lambda *a:calls.append(a));self.assertFalse(calls)
        self.store.add(dict(self.event,id='new',time=4000),4000,4000,True)
        def fail(*args):calls.append(args);raise DeliveryError('Нет подтверждения доставки')
        self.store.drain(4000,{'enabled':True},fail);self.store.drain(4001,{'enabled':True},fail)
        self.assertEqual(len(calls),1)

    def test_dpapi_roundtrip_and_no_plaintext_token(self):
        token='123456789:'+'x'*30
        self.assertEqual(protect(protect(token),True),token)
        with patch('app.notifications.SETTINGS',self.path/'settings.json'):
            result=save_settings({'token':token,'chat_id':'12345','enabled':False})
            self.assertTrue(result['configured']);self.assertNotIn(token,json.dumps(public_settings()))
            self.assertNotIn(token,(self.path/'settings.json').read_text())

    def test_live_level_alert_on_approach_not_boot_or_repeat(self):
        from app.live_market import LiveMarket
        with patch('app.live_market.AlertStore',return_value=self.store):model=LiveMarket('XAUUSD')
        s=dict(ready=True,atr=2,support={'price':100},resistance=None,trend='buy',score=75,rsi=60,ema20=101,ema50=99)
        model.frames['M15'].append(s)
        self.assertFalse(model.quote_alerts({'bid':100.2,'time':1000}))
        self.assertFalse(model.quote_alerts({'bid':102,'time':1001}))
        events=model.quote_alerts({'bid':100.3,'time':1002})
        self.assertEqual(len(events),1);self.assertEqual(events[0]['price_kind'],'bid')
        self.assertFalse(model.quote_alerts({'bid':100.2,'time':1003}))

    def test_telegram_payload_and_success(self):
        config={'token':'12345:'+'x'*30,'chat_id':'12345'}
        response=io.BytesIO(b'{"ok":true,"result":{"message_id":7}}')
        with patch('app.notifications.urllib.request.build_opener') as opener:
            request=opener.return_value.open
            request.return_value=response
            self.assertEqual(send_telegram('test',config),7)
            body=json.loads(request.call_args.args[0].data)
            self.assertEqual(body['chat_id'],'12345');self.assertEqual(body['text'],'test')
            self.assertTrue(body['disable_notification'])

    def test_telegram_response_without_message_id_is_not_confirmed(self):
        config={'token':'12345:'+'x'*30,'chat_id':'12345'}
        with patch('app.notifications.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=io.BytesIO(b'{"ok":true,"result":{}}')
            with self.assertRaisesRegex(DeliveryError,'без ID сообщения'):
                send_telegram('test',config)

    def test_early_plan_is_not_sent_silently(self):
        event=dict(id='plan',time=1000,tf='M5',type='sbr_sell_plan',symbol='XAUUSD',
                   analysis_plan={'side':'short','entry':[100,102],'stop':103,
                                  'targets':[99,97,95],'cancel_rule':'закрытие M5 выше 102'})
        self.store.add(event,1000,1000,True)
        calls=[]
        self.store.drain(1000,{'enabled':True},lambda message,config:calls.append((message,config)))
        self.assertEqual(len(calls),1)
        self.assertFalse(calls[0][1]['disable_notification'])
        self.assertIn('Шорт 100–102',calls[0][0])

    def test_telegram_rate_limit_and_no_secret_in_error(self):
        config={'token':'12345:'+'x'*30,'chat_id':'12345'}
        error=urllib.error.HTTPError('https://api.telegram.org/bot'+config['token'],429,'rate limit',{},io.BytesIO(b'{"parameters":{"retry_after":12}}'))
        with patch('app.notifications.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=error
            with self.assertRaises(DeliveryError) as raised:send_telegram('test',config)
            self.assertEqual(raised.exception.retry_after,12);self.assertNotIn(config['token'],str(raised.exception))

if __name__=='__main__':unittest.main()
