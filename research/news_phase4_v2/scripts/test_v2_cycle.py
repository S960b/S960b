"""All Phase4 v2 lifecycle tests use synthetic sealed Phase4 evidence and temp DB."""
import gzip
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from v2_core import V2Error, canonical
from v2_source import candidates, market_features
from v2_cycle import (locked_db, reserve, bind_session, mark_prompt, finish,
                      quote, label_once, study_evaluation, readonly_status)

EID='e'*64
HOUR=3_600_000
T=(1_800_000_000_000//HOUR)*HOUR+1_200_000
CUTOFF=T-10_000
START=T-20_000


def fake_phase4(root):
    root.mkdir(mode=0o700,parents=True)
    evidence=root/'evidence'
    evidence.mkdir(mode=0o700)
    end_hour=CUTOFF//HOUR*HOUR
    candles=[]
    for n in range(26):
        opened=end_hour-(26-n)*HOUR
        candles.append([opened,opened+HOUR-1,
                        100_000+n*10,100+n])
    content=canonical({'version':'p4-market-1h-v1',
                       'symbol':'BTCUSDT','interval':'1h',
                       'closed_candles':candles}).encode()
    market=hashlib.sha256(content).hexdigest()
    (evidence/(market+'.json.gz')).write_bytes(gzip.compress(content))
    news={'source':'coindesk','title':'BTC ETF forward event',
          'summary':'Bitcoin market announcement','url':'https://news.example',
          'published_ms':T-30_000,
          'first_http_seen_ms':T-15_000,
          'rss_body_sha256':'a'*64}
    raw=canonical(news)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    db=sqlite3.connect(root/'news.sqlite')
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE events(event_id TEXT PRIMARY KEY,cutoff_ms INTEGER);
        CREATE TABLE claimed_events(fingerprint TEXT,event_id TEXT);
        CREATE TABLE news_observations(
            fingerprint TEXT,first_seen_ms INTEGER,news_json TEXT,
            news_digest TEXT);
        CREATE TABLE features(event_id TEXT,source TEXT,digest TEXT,
                              available_ms INTEGER,observed_ms INTEGER);
    """)
    db.execute('INSERT INTO meta VALUES(?,?)',('schema_version','4'))
    db.execute('INSERT INTO events VALUES(?,?)',(EID,CUTOFF))
    db.execute('INSERT INTO claimed_events VALUES(?,?)',('f'*64,EID))
    db.execute('INSERT INTO news_observations VALUES(?,?,?,?)',
               ('f'*64,news['first_http_seen_ms'],raw,digest))
    db.execute('INSERT INTO features VALUES(?,?,?,?,?)',
               (EID,'market',market,CUTOFF-1000,CUTOFF-500))
    db.commit()
    db.close()
    return market


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=Path(self.tmp.name)
        self.source=p/'phase4'
        self.root=p/'phase4-v2'
        self.market=fake_phase4(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def init_store(self,start=START):
        with locked_db(self.root,create=True,now_ms=start):
            pass

    def test_status_missing_does_not_initialize(self):
        self.assertEqual(readonly_status(self.root,T),
                         {'status':'not_initialized'})
        self.assertFalse(self.root.exists())

    def test_sealed_evidence_and_precutoff(self):
        data=candidates(self.source,T,START)
        self.assertEqual(len(data),1)
        self.assertEqual(data[0]['event_id'],EID)
        self.assertTrue('return_4h' in data[0]['market'])
        self.assertEqual(len(market_features(self.source,self.market,CUTOFF)),5)

    def test_tampered_market_evidence_rejected(self):
        path=self.source/'evidence'/(self.market+'.json.gz')
        path.write_bytes(gzip.compress(b'{"tamper":true}'))
        with self.assertRaises(V2Error):
            candidates(self.source,T,START)

    def test_past_event_must_not_be_forecast(self):
        self.init_store(T+1000)
        with locked_db(self.root) as db:
            result=reserve(db,self.source,T+5000,enabled=True,daily_cap=4)
            self.assertEqual(result['reason'],'v2_no_new_forward_event')

    def test_model_disabled_and_bad_cap(self):
        self.init_store()
        with locked_db(self.root) as db:
            with self.assertRaises(V2Error):
                reserve(db,self.source,T)
            with self.assertRaises(V2Error):
                reserve(db,self.source,T,enabled=True,daily_cap=3)
            count=db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            self.assertEqual(count,0)

    def test_two_reservations_atomic_and_no_replay(self):
        self.init_store()
        with locked_db(self.root) as db:
            first=reserve(db,self.source,T,enabled=True,daily_cap=4)
            self.assertEqual(first['status'],'ready')
            self.assertEqual(len(first['arms']),2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts')
                             .fetchone()[0],2)
            second=reserve(db,self.source,T+10,enabled=True,daily_cap=4)
            self.assertEqual(second['reason'],
                             'v2_pending_pair_requires_reconcile')

    def test_full_forward_pair_and_delayed_four_hour_label(self):
        self.init_store()
        with locked_db(self.root) as db:
            batch=reserve(db,self.source,T,enabled=True,daily_cap=4)
            for n,arm in enumerate(batch['arms']):
                aid=arm['attempt_id']
                sid='session_v2_'+str(n)
                bind_session(db,aid,sid,T+n*1000)
                mark_prompt(db,aid,T+1000+n*1000)
                payload={
                    'session_id':sid,
                    'response':json.dumps({
                       'p_up':0.6 if n==0 else 0.55,
                       'expected_return_bps':60,'uncertainty':0.2,
                       'rationale':'synthetic'}),
                    'model':{'providerID':'deepseek',
                             'modelID':'deepseek-v4-flash'}}
                response=finish(db,aid,payload,T+5000+n*1000)
                self.assertEqual(response['status'],'completed')
            def fake_quote(url):
                return T+20_000,T+21_000,{
                    'symbol':'BTCUSDT','price':'100000.00'}
            self.assertEqual(quote(db,EID,fake_quote)['status'],
                             'entry_recorded')
            def fake_label(url):
                target=int(url.split('startTime=')[1].split('&')[0])
                h=1 if target<T+2*HOUR else 4
                started=T+21_000+h*HOUR+125_000
                return started,started+500,[[target,'1','1','1',
                                              '100400.00','1',
                                              target+59_999]]
            result=label_once(db,T+21_000+4*HOUR+300_000,fake_label)
            self.assertEqual(result['created'],2)
            scored=study_evaluation(db)
            self.assertEqual(scored['labeled_pairs'],1)
            self.assertGreater(scored['arms']['news']['brier'],0)
            self.assertEqual(scored['paired_delta']['mean_net_nm_minus_n_bps'],0)

    def test_session_duplicate_rejected(self):
        self.init_store()
        with locked_db(self.root) as db:
            batch=reserve(db,self.source,T,enabled=True,daily_cap=4)
            aid=batch['arms'][0]['attempt_id']
            bind_session(db,aid,'session123',T)
            with self.assertRaises(sqlite3.IntegrityError):
                bind_session(db,aid,'session456',T+1)

    def test_model_identity_mismatch_rejected(self):
        self.init_store()
        with locked_db(self.root) as db:
            batch=reserve(db,self.source,T,enabled=True,daily_cap=4)
            for n,arm in enumerate(batch['arms']):
                aid=arm['attempt_id']
                bind_session(db,aid,'session_v2_'+str(n),T)
                mark_prompt(db,aid,T+10)
                payload={'session_id':'session_v2_'+str(n),
                  'response':json.dumps({'p_up':0.5,'expected_return_bps':0,
                                         'uncertainty':1,'rationale':''}),
                  'model':{'providerID':'providerA' if n==0 else 'providerB',
                           'modelID':'fixed-model'}}
                if n==0:
                    finish(db,aid,payload,T+20)
                else:
                    with self.assertRaises(V2Error):
                        finish(db,aid,payload,T+30)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM decisions')
                             .fetchone()[0],1)

    def test_no_early_labels_or_fake_results(self):
        self.init_store()
        with locked_db(self.root) as db:
            self.assertEqual(label_once(db,T,lambda _:None)['created'],0)
            self.assertIsNone(study_evaluation(db)['paired_delta'])


if __name__=='__main__':
    unittest.main()
