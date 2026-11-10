import json
import unittest

from v2_core import (ARMS, V2Error, COST_GATE_BPS, POLICY_VERSION,
    decode_prediction, decision, evaluate, paper_net_bps, prompt)

EID='a'*64
NEWS={'source':'coindesk','title':'Bitcoin ETF institutional flows',
      'summary':'BTC-related headline with source disclosure',
      'published_ms':1_800_000_000_000,
      'first_http_seen_ms':1_800_000_005_000}
MARKET={'return_1h':0.001,'return_4h':-0.005,
        'realized_vol_24h':0.011}
T=1_800_000_010_000


class CoreTests(unittest.TestCase):
    def test_strict_probability_forecast(self):
        r=decode_prediction(json.dumps({
            'p_up':0.65,'expected_return_bps':80,
            'uncertainty':0.3,'rationale':'test'}))
        self.assertEqual(decision(r['p_up'],r['expected_return_bps']),
                         'LONG_PAPER')

    def test_rationale_length_is_non_signal_with_bounded_tolerance(self):
        # Reproduces the first real NM model contract violation (271 chars).
        # The financial forecast must remain exact; explanation is ignored.
        model={"p_up":0.34,"expected_return_bps":-80,
               "uncertainty":0.62,"rationale":"x"*271}
        result=decode_prediction(json.dumps(model))
        self.assertEqual(result,{"p_up":0.34,
                                 "expected_return_bps":-80.0,
                                 "uncertainty":0.62})
        model["rationale"]="x"*1024
        self.assertEqual(decode_prediction(json.dumps(model)),result)
        model["rationale"]="x"*1025
        with self.assertRaises(V2Error):
            decode_prediction(json.dumps(model))

    def test_no_forced_trade_below_fees(self):
        self.assertEqual(COST_GATE_BPS,35)
        self.assertEqual(decision(0.9,34.9),'FLAT')
        self.assertEqual(decision(0.65,35),'FLAT')
        self.assertEqual(decision(0.51,200),'FLAT')
        self.assertEqual(decision(0.4,-100),'SHORT_PAPER')
        self.assertEqual(decision(0.65,-100),'FLAT')

    def test_reject_extra_and_duplicate_keys_and_nan(self):
        bad=['{"p_up":0.8,"p_up":0.1,"expected_return_bps":10,'
             '"uncertainty":0.2,"rationale":""}',
             '{"p_up":NaN,"expected_return_bps":10,"uncertainty":0.2,"rationale":""}',
             '{"p_up":0.5,"expected_return_bps":10,"uncertainty":0.2,'
             '"rationale":"","direction":"UP"}']
        for text in bad:
            with self.subTest(text=text), self.assertRaises(V2Error):
                decode_prediction(text)

    def test_reject_boolean_and_out_of_range_numeric(self):
        for probability in (True,-0.1,1.1):
            with self.subTest(probability=probability),self.assertRaises(V2Error):
                decode_prediction(json.dumps({
                    'p_up':probability,'expected_return_bps':0,
                    'uncertainty':0.1,'rationale':'none'}))

    def test_identical_news_in_both_arms_and_only_nm_has_market(self):
        n=prompt(EID,T,NEWS,MARKET,'news')
        nm=prompt(EID,T,NEWS,MARKET,'news_market')
        self.assertIn(POLICY_VERSION,n)
        self.assertNotIn('pre_cutoff_market',n)
        self.assertIn('pre_cutoff_market',nm)
        payloads=[json.loads(x.split('FROZEN_INPUT_JSON:\n')[1]) for x in (n,nm)]
        self.assertEqual(payloads[0]['news'],payloads[1]['news'])
        self.assertEqual(payloads[0]['cutoff_ms'],payloads[1]['cutoff_ms'])

    def test_forbid_news_future_observation(self):
        stale={**NEWS,'first_http_seen_ms':T+1}
        with self.assertRaises(V2Error):
            prompt(EID,T,stale,MARKET,'news')

    def test_paper_fee_and_cash(self):
        self.assertAlmostEqual(paper_net_bps('LONG_PAPER',100),75)
        self.assertAlmostEqual(paper_net_bps('SHORT_PAPER',-100),75)
        self.assertEqual(paper_net_bps('FLAT',100),0)

    def test_brier_and_paired_net(self):
        entry=100_000
        row={'event_id':EID,'entry_ms':T,
             'entry_price':entry,'exit_ms':T+4*3_600_000,
             'exit_price':100_800,
             'arms':{
              'news':{'p_up':0.6,'expected_return_bps':80,
                      'uncertainty':0.3,'action':'LONG_PAPER'},
              'news_market':{'p_up':0.55,'expected_return_bps':50,
                             'uncertainty':0.6,'action':'LONG_PAPER'}}}
        report=evaluate([row])
        self.assertEqual(report['labeled_pairs'],1)
        self.assertAlmostEqual(report['arms']['news']['brier'],0.16)
        self.assertAlmostEqual(report['arms']['news_market']['brier'],0.2025)
        self.assertAlmostEqual(report['paired_delta']['mean_net_nm_minus_n_bps'],0)

    def test_overlap_and_duplicate_rejected(self):
        row={'event_id':EID,'entry_ms':T,'entry_price':100000,
             'exit_ms':T+4*3_600_000,'exit_price':100000,
             'arms':{arm:dict(p_up=0.5,expected_return_bps=0,
                               uncertainty=0.9,action='FLAT') for arm in ARMS}}
        with self.assertRaises(V2Error):evaluate([row,row])
        delayed={**row,'exit_ms':T+3*3_600_000}
        with self.assertRaises(V2Error):evaluate([delayed])

    def test_empty_evaluation(self):
        r=evaluate([])
        self.assertEqual(r['labeled_pairs'],0)
        self.assertIsNone(r['arms']['news']['brier'])
        self.assertIsNone(r['paired_delta'])


if __name__=='__main__':
    unittest.main()
