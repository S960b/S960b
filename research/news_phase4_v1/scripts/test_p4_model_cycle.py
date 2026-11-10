import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from p4_evidence_store import EvidenceStore
from p4_live_collector import collect_once
from p4_model_cycle import (prepare_pair, finalize, quote_after_pair, ModelCycleError,
                            _read_market, TICKER)
from p4_attempt_ledger import bind_session,mark_prompt_attempted,status,LedgerError
from p4_public_sources import Captured
from test_p4_live_collector import fake_fetch,T,H

RESULT=json.dumps({'direction':'UP','confidence':0.5,'rationale':'Test only'})
IDENT={'providerID':'gateway','modelID':'deepseek'}


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ctx=EvidenceStore(Path(self.tmp.name)/'p4')
        self.store=self.ctx.__enter__()
        result=collect_once(self.store,fake_fetch,lambda:T)
        self.assertEqual(result['events_created'],1)
    def tearDown(self):
        self.ctx.__exit__(None,None,None)
        self.tmp.cleanup()
    def prep(self):
        return prepare_pair(self.store,T+60_000,enabled=True,daily_cap=2)
    def test_disabled_by_default(self):
        with self.assertRaises(ModelCycleError):
            prepare_pair(self.store,T+60_000)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM attempt_reservations').fetchone()[0],0)
    def test_reserve_two_prompts_atomically(self):
        p=self.prep()
        self.assertEqual(p['status'],'ready')
        self.assertEqual(len(p['arms']),2)
        self.assertEqual({a['arm'] for a in p['arms']},{'news','news_market'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM attempt_reservations').fetchone()[0],2)
    def test_market_not_in_news_prompt(self):
        p=self.prep()
        for item in p['arms']:
            if item['arm']=='news':
                self.assertNotIn('pre_cutoff_market',item['prompt'])
            else:
                self.assertIn('pre_cutoff_market',item['prompt'])
    def test_bad_budget(self):
        with self.assertRaises(ModelCycleError):
            prepare_pair(self.store,T+60_000,enabled=True,daily_cap=1)
        with self.assertRaises(ModelCycleError):
            prepare_pair(self.store,T+60_000,enabled=True,daily_cap=5)
    def test_stale_event_not_retro_prediction(self):
        self.assertEqual(prepare_pair(self.store,T+20*60_000,enabled=True,daily_cap=2)['status'],'skip')
    def test_repeated_prepare_does_not_replay(self):
        self.prep()
        self.assertEqual(self.prep()['status'],'skip')
    def test_same_fifteen_minute_slot_claimed_once_across_instances(self):
        first=self.prep()
        self.assertEqual(first['status'],'ready')
        # Separate timer instances enter in the same wall-clock sampling slot.
        # The second must not reserve even another news event in this slot.
        second=prepare_pair(self.store,T+61_000,enabled=True,daily_cap=4)
        self.assertEqual(second,{'status':'skip',
                                 'reason':'phase4_slot_already_claimed'})
        self.assertEqual(self.store.db.execute(
            'SELECT COUNT(*) FROM attempt_reservations').fetchone()[0],2)

    def test_session_and_prompt_must_be_durable(self):
        a=self.prep()['arms'][0]['attempt_id']
        with self.assertRaises(LedgerError): mark_prompt_attempted(self.store.db,a,T+60_010)
        bind_session(self.store.db,a,'session_12345',T+60_010)
        mark_prompt_attempted(self.store.db,a,T+60_020)
        self.assertEqual(status(self.store.db,a)[0],'prompt_attempted')
    def test_finalize_exact_model(self):
        a=self.prep()['arms'][0]['attempt_id']
        bind_session(self.store.db,a,'session_12345',T+60_010)
        mark_prompt_attempted(self.store.db,a,T+60_020)
        out=finalize(self.store,a,{'session_id':'session_12345',
                      'response':RESULT,'model':IDENT},T+60_030)
        self.assertEqual(out['status'],'completed')
        self.assertEqual(status(self.store.db,a)[0],'completed')
    def test_wrong_session_rejected(self):
        a=self.prep()['arms'][0]['attempt_id']
        bind_session(self.store.db,a,'session_12345',T+60_010)
        mark_prompt_attempted(self.store.db,a,T+60_020)
        with self.assertRaises(ModelCycleError):
            finalize(self.store,a,{'session_id':'wrong_12345',
                      'response':RESULT,'model':IDENT},T+60_030)
        self.assertEqual(status(self.store.db,a)[0],'prompt_attempted')
    def test_non_json_response_rejected(self):
        a=self.prep()['arms'][0]['attempt_id']
        bind_session(self.store.db,a,'session_12345',T+60_010)
        mark_prompt_attempted(self.store.db,a,T+60_020)
        with self.assertRaises(ModelCycleError):
            finalize(self.store,a,{'session_id':'session_12345',
                      'response':'not json','model':IDENT},T+60_030)
    def test_invalid_identity_rejected(self):
        a=self.prep()['arms'][0]['attempt_id']
        bind_session(self.store.db,a,'session_12345',T+60_010)
        mark_prompt_attempted(self.store.db,a,T+60_020)
        with self.assertRaises(ModelCycleError):
            finalize(self.store,a,{'session_id':'session_12345',
                      'response':RESULT,'model':{'providerID':'','modelID':'deepseek'}},T+60_030)
    def test_quote_requires_both_completed_no_network(self):
        event=self.prep()
        def no_fetch(*a,**kw): raise AssertionError('quote_not_allowed')
        with self.assertRaises(ModelCycleError):
            quote_after_pair(self.store,event['event_id'],no_fetch)
    def test_shared_quote_after_both(self):
        event=self.prep()
        for i,item in enumerate(event['arms']):
            sid='session_'+str(i)+'_12345'
            bind_session(self.store.db,item['attempt_id'],sid,T+60_010)
            mark_prompt_attempted(self.store.db,item['attempt_id'],T+60_020)
            finalize(self.store,item['attempt_id'],{
                'session_id':sid,'response':RESULT,'model':IDENT},T+60_030)
        def get_quote(url,*,max_bytes):
            self.assertEqual(url,TICKER)
            return Captured(url,b'{"symbol":"BTCUSDT","price":"35000.0"}',
                            T+60_100,T+60_200,200)
        value=quote_after_pair(self.store,event['event_id'],get_quote)
        self.assertEqual(value['status'],'entry_recorded')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM entry_quotes').fetchone()[0],1)
        with self.assertRaises(ModelCycleError):
            quote_after_pair(self.store,event['event_id'],get_quote)
    def test_global_cohort_model_identity_pinned(self):
        event=self.prep()
        a=event['arms'][0]['attempt_id']
        bind_session(self.store.db,a,'session_abc000',T+60_010)
        mark_prompt_attempted(self.store.db,a,T+60_020)
        finalize(self.store,a,{
            'session_id':'session_abc000','response':RESULT,'model':IDENT},T+60_030)
        other='e'*64
        aid='f'*64
        self.store.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                              (other,T,T+1,H,'v1','v1'))
        self.store.db.execute('INSERT INTO features VALUES (?,?,?,?,?)',
                              (other,'news',T-5000,T-4000,H))
        self.store.db.execute('INSERT INTO attempt_reservations VALUES(?,?,?,?,?)',
                              (aid,other,'news',T+100_000,H))
        self.store.db.execute('INSERT INTO attempt_status_events(attempt_id,status,ts_ms) VALUES (?,?,?)',
                              (aid,'reserved',T+100_000))
        self.store.db.commit()
        bind_session(self.store.db,aid,'session_other0',T+100_010)
        mark_prompt_attempted(self.store.db,aid,T+100_020)
        with self.assertRaisesRegex(ModelCycleError,'cohort_model_identity_changed'):
            finalize(self.store,aid,{'session_id':'session_other0',
                'response':RESULT,'model':{'providerID':'other','modelID':'changed'}},T+100_030)
        self.assertEqual(status(self.store.db,aid)[0],'prompt_attempted')

    def test_cross_arm_model_conflict_blocked(self):
        event=self.prep()
        for i,item in enumerate(event['arms']):
            sid='session_'+str(i)+'_12345'
            bind_session(self.store.db,item['attempt_id'],sid,T+60_010)
            mark_prompt_attempted(self.store.db,item['attempt_id'],T+60_020)
            identity=IDENT if i==0 else {'providerID':'other','modelID':'different'}
            if i:
                with self.assertRaisesRegex(ModelCycleError,'cohort_model_identity_changed'):
                    finalize(self.store,item['attempt_id'],{
                        'session_id':sid,'response':RESULT,'model':identity},T+60_030)
                self.assertEqual(status(self.store.db,item['attempt_id'])[0],'prompt_attempted')
            else:
                finalize(self.store,item['attempt_id'],{
                    'session_id':sid,'response':RESULT,'model':identity},T+60_030)


if __name__=='__main__': unittest.main()
