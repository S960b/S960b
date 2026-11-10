import copy
import unittest
from p4_forward_evidence import seal_evidence, arm_input_hash, EvidenceError

T = 1_800_000_000_000
H = 'a'*64
N = {'source_id':'rss-allowlist-v1','available_ms':T-5000,
     'observed_ms':T-4000, 'digest':H, 'size_bytes':100}
M = {'source_id':'binance-spot-btcusdt-1h-v1','available_ms':T-6000,
     'observed_ms':T-3000, 'digest':'b'*64, 'size_bytes':300}
S = {'source_id':'mastodon-social-bitcoin-v1','available_ms':T-2500,
     'observed_ms':T-2500, 'digest':'c'*64, 'size_bytes':400}

def make(**overrides):
    d = dict(news=N,market=M,news_first_seen_ms=T-5000,cutoff_ms=T,
             created_ms=T+1, social_missing_reason='incomplete',
             social_checked_ms=T-1000)
    d.update(overrides)
    return seal_evidence(**d)

class EvidenceTests(unittest.TestCase):
    def test_two_arm_event(self):
        e=make()
        self.assertEqual(len(e['event'][0]),64)
        self.assertEqual(len(e['features']),2)
        self.assertEqual(len(e['source_missing']),1)
        self.assertEqual(e['available_arms'],('news','news_market'))
        self.assertEqual(e,make())
    def test_three_arm_event(self):
        e=make(social=S,social_missing_reason=None,social_checked_ms=None)
        self.assertEqual(len(e['features']),3)
        self.assertEqual(len(e['source_missing']),0)
        self.assertEqual(len(e['available_arms']),3)
        self.assertEqual(len(arm_input_hash(e,'news_market_social')),64)
    def test_same_news_first_seen_same_event(self):
        self.assertEqual(make()['event'][0],make(cutoff_ms=T+10,created_ms=T+11)['event'][0])
    def test_changed_news_first_seen_new_event(self):
        self.assertNotEqual(make()['event'][0],
                            make(news_first_seen_ms=T-4999)['event'][0])
    def test_late_observation(self):
        m=dict(M,observed_ms=T+1)
        with self.assertRaises(EvidenceError): make(market=m)
    def test_future_available(self):
        m=dict(M,available_ms=T+1)
        with self.assertRaises(EvidenceError): make(market=m)
    def test_negative_and_bool_clock(self):
        with self.assertRaises(EvidenceError): make(cutoff_ms=True)
        with self.assertRaises(EvidenceError): make(news_first_seen_ms=-1)
    def test_late_missing_check(self):
        with self.assertRaises(EvidenceError): make(social_checked_ms=T+1)
    def test_must_record_missing(self):
        with self.assertRaises(EvidenceError):
            make(social_checked_ms=None)
    def test_no_present_and_missing(self):
        with self.assertRaises(EvidenceError): make(social=S)
    def test_malformed_hash(self):
        with self.assertRaises(EvidenceError): make(news=dict(N,digest='fake'))
    def test_large_response(self):
        with self.assertRaises(EvidenceError): make(news=dict(N,size_bytes=750_001))
    def test_bogus_source(self):
        with self.assertRaises(EvidenceError): make(news=dict(N,source_id='reddit'))
    def test_freeze_delay(self):
        with self.assertRaises(EvidenceError): make(created_ms=T+120_001)
    def test_first_seen_after_observation(self):
        with self.assertRaises(EvidenceError): make(news_first_seen_ms=T-3999)
    def test_missing_social_hash_disallowed(self):
        with self.assertRaises(EvidenceError):
            arm_input_hash(make(),'news_market_social')
    def test_pair_uses_same_news(self):
        e=make(social=S,social_missing_reason=None,social_checked_ms=None)
        self.assertEqual(e['frozen']['sources']['news'],N)
        self.assertNotEqual(arm_input_hash(e,'news'),arm_input_hash(e,'news_market'))
    def test_incorrect_input_types(self):
        with self.assertRaises(EvidenceError): make(news=[])
        with self.assertRaises(EvidenceError): make(market=dict(M,size_bytes=True))

if __name__=='__main__': unittest.main()
