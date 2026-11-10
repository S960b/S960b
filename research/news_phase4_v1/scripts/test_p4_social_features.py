import copy
import unittest
from datetime import datetime, timezone
from p4_social_features import build_social_features, SocialFeatureError

T=1_800_000_000_000

def iso(ms):
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat().replace('+00:00','Z')

def sample(n=6):
    return [{'uri':f'https://mastodon.social/users/x/statuses/{i}',
             'created_at':iso(T-10_000-i*1_000), 'edited_at':None,
             'content':f'<p>Bitcoin market bullish rally today number {i}</p>',
             'visibility':'public', 'language':'en','reblog':None,
             'account':{'bot':False}} for i in range(n)]

class SocialTests(unittest.TestCase):
    def test_valid(self):
        out=build_social_features(sample(),T,T+1)
        self.assertEqual(out['eligible_count'],6)
        self.assertEqual(out['positive_hits'],12)
        self.assertEqual(out['lexicon_balance'],1)
        self.assertNotIn('statuses/',str(out['excerpts']))
        self.assertEqual(out,build_social_features(sample(),T,T+1))
    def test_order_independent(self):
        self.assertEqual(build_social_features(sample(),T,T)['digest'],build_social_features(list(reversed(sample())),T,T)['digest'])
    def test_fetch_cannot_be_backdated(self):
        with self.assertRaises(SocialFeatureError): build_social_features(sample(),T,T-1)
    def test_expired_capture(self):
        with self.assertRaises(SocialFeatureError): build_social_features(sample(),T,T+120_001)
    def test_future_post(self):
        s=sample(); s[0]['created_at']=iso(T+1000)
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)
    def test_future_edit(self):
        s=sample(); s[0]['edited_at']=iso(T+1000)
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)
    def test_old_posts_no_backfill(self):
        s=sample(5); s[0]['created_at']=iso(T-3*3_600_000)
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)
    def test_private_excluded(self):
        s=sample(5); s[0]['visibility']='private'
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)
    def test_bot_excluded(self):
        s=sample(5); s[0]['account']['bot']=True
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)
    def test_duplicates_not_counted(self):
        s=sample(5); s.append(copy.deepcopy(s[-1]))
        self.assertEqual(build_social_features(s,T,T)['eligible_count'],5)
    def test_truncated_page_rejected(self):
        with self.assertRaises(SocialFeatureError): build_social_features(sample(40),T,T)
    def test_invalid_html_length(self):
        s=sample(); s[0]['content']='x'*4097
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)
    def test_no_model_network_or_file_io(self):
        import inspect, p4_social_features
        src=inspect.getsource(p4_social_features)
        for bad in ['urllib.request','requests.get','open(', 'sqlite3.connect', 'subprocess']:
            self.assertNotIn(bad,src)
    def test_sample_is_not_social_ready(self):
        with self.assertRaises(SocialFeatureError): build_social_features(sample(5)[:4],T,T)
    def test_bad_clocks(self):
        with self.assertRaises(SocialFeatureError): build_social_features(sample(),True,T)
        s=sample(); s[0]['created_at']='not_time'
        with self.assertRaises(SocialFeatureError): build_social_features(s,T,T)

if __name__=='__main__': unittest.main()
