import sqlite3
import unittest
from p4_collector_schema import SCHEMA_SQL
from p4_attempt_ledger import (reserve, bind_session, mark_prompt_attempted,
                               complete, terminal, status, LedgerError)

E='a'*64
H='b'*64
T=1_800_000_000_000

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA_SQL)
        self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(E,T,T+1,H,'v1','v1'))
        self.db.execute('INSERT INTO features VALUES(?,?,?,?,?)',(E,'news',T-100,T-99,H))
        self.db.execute('INSERT INTO features VALUES(?,?,?,?,?)',(E,'market',T-100,T-99,H))
        self.db.commit()
    def tearDown(self): self.db.close()
    def reserve(self, arm='news', cap=3, t=T+1):
        return reserve(self.db,event_id=E,arm=arm,prompt_hash=H,now_ms=t,
                       model_calls_enabled=True,daily_call_cap=cap)
    def test_default_off(self):
        with self.assertRaises(LedgerError): reserve(
            self.db,event_id=E,arm='news',prompt_hash=H,now_ms=T+1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM attempt_reservations').fetchone()[0],0)
    def test_reserve_is_durable_and_one_per_arm(self):
        a=self.reserve()
        self.assertEqual(status(self.db,a)[0],'reserved')
        with self.assertRaises(sqlite3.IntegrityError): self.reserve()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM attempt_reservations').fetchone()[0],1)
    def test_budget(self):
        self.reserve('news',cap=1)
        with self.assertRaises(LedgerError): self.reserve('news_market',cap=1)
    def test_invalid_budget(self):
        with self.assertRaises(LedgerError): self.reserve(cap=True)
        with self.assertRaises(LedgerError): self.reserve(cap=31)
    def test_missing_source_before_reserve(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.reserve('news_market_social')
    def test_before_cutoff(self):
        with self.assertRaises(LedgerError): self.reserve(t=T-1)
    def test_durable_session_binding(self):
        a=self.reserve()
        bind_session(self.db,a,'session_abc123',T+2)
        self.assertEqual(status(self.db,a)[0],'session_created')
        self.assertEqual(self.db.execute('SELECT session_id FROM attempt_sessions').fetchone()[0],'session_abc123')
    def test_session_cannot_be_rebound(self):
        a=self.reserve()
        bind_session(self.db,a,'session_abc123',T+2)
        with self.assertRaises(LedgerError): bind_session(self.db,a,'session_abc124',T+3)
    def test_prompt_cannot_start_before_session(self):
        a=self.reserve()
        with self.assertRaises(LedgerError): mark_prompt_attempted(self.db,a,T+2)
    def test_prompt_marked_before_external_call(self):
        a=self.reserve()
        bind_session(self.db,a,'session_abc123',T+2)
        mark_prompt_attempted(self.db,a,T+3)
        self.assertEqual(status(self.db,a),('prompt_attempted',T+3))
    def test_unknown_is_terminal(self):
        a=self.reserve()
        bind_session(self.db,a,'session_abc123',T+2)
        mark_prompt_attempted(self.db,a,T+3)
        terminal(self.db,a,'unknown',T+4)
        with self.assertRaises(LedgerError): mark_prompt_attempted(self.db,a,T+5)
        with self.assertRaises(LedgerError): terminal(self.db,a,'failed',T+5)
    def test_confirmed_decision(self):
        a=self.reserve()
        bind_session(self.db,a,'session_abc123',T+2)
        mark_prompt_attempted(self.db,a,T+3)
        complete(self.db,attempt_id=a,now_ms=T+4,direction='UP',
                 confidence=.5,provider_id='gateway',model_id='deepseek',
                 response_hash=H)
        self.assertEqual(status(self.db,a)[0],'completed')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],1)
    def test_no_completion_before_prompt(self):
        a=self.reserve()
        with self.assertRaises(LedgerError):
            complete(self.db,attempt_id=a,now_ms=T+4,direction='UP',
                     confidence=.5,provider_id='gateway',model_id='deepseek',response_hash=H)
    def test_unverified_provider(self):
        a=self.reserve()
        bind_session(self.db,a,'session_abc123',T+2)
        mark_prompt_attempted(self.db,a,T+3)
        with self.assertRaises(LedgerError):
            complete(self.db,attempt_id=a,now_ms=T+4,direction='UP',
                     confidence=.5,provider_id='',model_id='deepseek',response_hash=H)
        self.assertEqual(status(self.db,a)[0],'prompt_attempted')
    def test_model_conflict_keeps_pending_attempt(self):
        a=self.reserve('news'); b=self.reserve('news_market')
        for attempt in (a,b):
            bind_session(self.db,attempt,'session_'+attempt[:8],T+2)
            mark_prompt_attempted(self.db,attempt,T+3)
        complete(self.db,attempt_id=a,now_ms=T+4,direction='UP',
                 confidence=.5,provider_id='gateway',model_id='deepseek',response_hash=H)
        with self.assertRaises(sqlite3.IntegrityError):
            complete(self.db,attempt_id=b,now_ms=T+4,direction='UP',
                     confidence=.5,provider_id='other',model_id='model',response_hash=H)
        self.assertEqual(status(self.db,b)[0],'prompt_attempted')
    def test_time_never_moves_backward(self):
        a=self.reserve()
        with self.assertRaises(LedgerError): bind_session(self.db,a,'session_abc123',T)
    def test_foreign_transaction(self):
        self.db.execute("INSERT INTO meta VALUES('unit','x')")
        with self.assertRaises(LedgerError): self.reserve()
        self.db.rollback()

if __name__=='__main__': unittest.main()
