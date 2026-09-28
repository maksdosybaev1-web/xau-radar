import unittest

from app.ai_journal import snapshot, validate_review


class AIJournalTests(unittest.TestCase):
    def test_summary_checks_actual_experiments(self):
        run={'version':'v','source_hash':'h',
             'experiments':[{'mode':'touch_limit','state':'rejected','reason':'spread_guard'},
                            {'candidate_id':'one','mode':'after_close','state':'closed','r':1}],
             'summary':{'by_mode':{'touch_limit':{'total':1,'closed':0,'wins':0,'average_r':None},
                                   'after_close':{'total':1,'closed':1,'wins':1,'average_r':1}},
                        'paired_closed':0}}
        forward={'forward_since':1,'quote_time':2,'stale':True,
                 'forward_events_total':0,'paper_outcomes_total':0,'tick_checks_total':0}
        facts=snapshot(run,forward)
        self.assertEqual(facts['modes']['touch_limit']['reasons']['spread_guard'],1)
        run['summary']['by_mode']['touch_limit']['total']=2
        with self.assertRaisesRegex(ValueError,'не совпадает'):
            snapshot(run,forward)

    def test_wrong_dominant_reason_is_rejected(self):
        correct={'touch_limit':'spread_guard','after_close':'first_return_not_confirmed'}
        observations=['Главная причина для лимита — фильтр спреда.',
                      'После закрытия чаще отсутствует подтверждение.']
        self.assertEqual(validate_review({'dominant_reasons':correct,
                                          'observations':observations},correct),observations)
        wrong=dict(correct,touch_limit='limit_crossed_at_open')
        with self.assertRaisesRegex(ValueError,'главные причины'):
            validate_review({'dominant_reasons':wrong,'observations':observations},correct)


if __name__=='__main__':
    unittest.main()
