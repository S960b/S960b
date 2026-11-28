"""Offline protocol tests; no NEWS baseline copy, model, RSS or Binance network."""
import json
import re
import sqlite3
import unittest
from types import SimpleNamespace

import news_mt_cycle as a


class FakeBase:
    SYSTEM = 'Synthetic BTC classifier'
    KEYWORDS = re.compile(r'bitcoin|btc|etf', re.I)

    def __init__(self):
        self.now = 1793966400000
        self.rss_next = []
        self.quoted = 0

    def init_db(self, c):
        c.executescript('''
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY,source TEXT,title TEXT,summary TEXT,
            url TEXT,published_ms INTEGER,first_seen_ms INTEGER,prediction_id TEXT);
          CREATE TABLE IF NOT EXISTS predictions(id TEXT PRIMARY KEY,entry_ms INTEGER NOT NULL,
            entry_price REAL NOT NULL,direction TEXT NOT NULL,confidence REAL NOT NULL,
            rationale TEXT,model TEXT NOT NULL,model_input_hash TEXT NOT NULL,
            model_output_hash TEXT NOT NULL,input_news TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outcomes(prediction_id TEXT,horizon_h INTEGER,exit_ms INTEGER,
            exit_price REAL,btc_return REAL,model_net REAL,btc_hodl_net REAL,
            PRIMARY KEY(prediction_id,horizon_h));
          CREATE TABLE IF NOT EXISTS events(ts_ms INTEGER,kind TEXT,status TEXT,detail TEXT);
        ''')

    def ms_now(self): return self.now
    def digest(self, s): return a.hashlib.sha256(s.encode()).hexdigest()
    def check_clock(self): return 5
    def label(self, c, now): return 0
    def quote(self):
        self.quoted += 1
        return 75000.0
    def collect(self, c, now):
        for row in self.rss_next:
            c.execute("INSERT OR IGNORE INTO stories VALUES(?,?,?,?,?,?,?,NULL)", row)
        n = len(self.rss_next)
        self.rss_next = []
        return {'feeds_ok': 4, 'new_headlines': n, 'errors': []}
    def meta_get(self, c, key):
        x = c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return x[0] if x else None
    def meta_put(self, c, key, val):
        c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(val)))
    def event(self, c, kind, status, detail=''):
        c.execute('INSERT INTO events VALUES(?,?,?,?)',(self.now,kind,status,detail))
    def can_forecast(self,c,now):
        if c.execute('SELECT COUNT(*) FROM predictions').fetchone()[0]>=60: return False,'max_60_predictions'
        if c.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt' AND ts_ms>=?",(now-24*3600000,)).fetchone()[0]>=6:
            return False,'max_6_model_calls_per_24h'
        return True,'eligible'
    def parse_prediction(self,raw):
        obj=json.loads(raw)
        if obj['direction'] not in ('UP','DOWN','NEUTRAL') or type(obj['confidence']) not in (float,int) or not 0<=obj['confidence']<=1 or not isinstance(obj['rationale'],str):
            raise ValueError('invalid output')
        return obj
    def report(self,c):
        return {'unique_predictions':c.execute('SELECT COUNT(*) FROM predictions').fetchone()[0]}


class NewsMTTests(unittest.TestCase):
    def setUp(self):
        self.base = FakeBase()
        self.db = sqlite3.connect(':memory:')
        a.setup(self.db,self.base)

    def tearDown(self): self.db.close()

    def add_story(self, tag='s1'):
        t=self.base.now
        self.base.rss_next.append((tag,'coindesk','Bitcoin ETF approval','BTC surge?',
                                   'https://example.test/x',t-1000,t))

    def prepare_ready(self):
        warm=a.prepare(self.db,self.base)
        self.assertEqual(warm['reason'],'first_collection_warmup')
        self.base.now+=3000
        self.add_story()
        ready=a.prepare(self.db,self.base)
        self.assertEqual(ready['status'],'ready')
        return ready

    def mark(self,batch,session='ses_test123'):
        self.assertEqual(a.transition(self.db,self.base,batch,'session',session)['status'],'session_created')
        self.assertEqual(a.transition(self.db,self.base,batch,'prompt',session)['status'],'prompt_attempted')
        return session

    def test_warmup_prepare_budget_and_untrusted_prompt(self):
        ready=self.prepare_ready()
        self.assertIn('NEWS_DATA_JSON_START',ready['prompt'])
        self.assertIn('untrusted data',ready['prompt'])
        self.assertEqual(self.base.quoted,0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0],1)
        self.assertEqual(a.prepare(self.db,self.base)['status'],'blocked_pending')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],0)

    def test_finalize_once_after_session_with_post_model_quote(self):
        ready=self.prepare_ready(); ident=ready['batch_id']; sid=self.mark(ident)
        payload=json.dumps({'session_id':sid,'response':'{"direction":"UP","confidence":0.8,"rationale":"ETF"}'}).encode()
        self.assertEqual(self.base.quoted,0)
        result=a.finalize(self.db,self.base,ident,payload)
        self.assertEqual(result['status'],'created')
        self.assertEqual(self.base.quoted,1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM stories WHERE prediction_id IS NOT NULL').fetchone()[0],1)
        again=a.finalize(self.db,self.base,ident,payload)
        self.assertEqual(again['status'],'already_completed')
        self.assertEqual(self.base.quoted,1)
        self.assertEqual(a.status(self.db,self.base)['pending'],None)

    def test_fail_closed_without_session_binding(self):
        ready=self.prepare_ready()
        with self.assertRaisesRegex(ValueError,'BATCH_NOT_READY_FOR_FINALIZE'):
            a.finalize(self.db,self.base,ready['batch_id'],json.dumps({'session_id':'ses_test123','response':'{}'}).encode())
        self.assertEqual(self.base.quoted,0)

    def test_invalid_response_rejected_no_quote_and_reserved_call(self):
        ready=self.prepare_ready(); sid=self.mark(ready['batch_id'])
        for reply in ('```json\n{}\n```','{"direction":"UP","confidence":0.5,"rationale":"x","extra":1}',
                      '{"direction":"UP","direction":"DOWN","confidence":0.5,"rationale":"x"}',
                      '{"direction":"UP","confidence":NaN,"rationale":"x"}'):
            with self.subTest(reply=reply):
                # Strict parser itself must reject each form; no need to re-finalize failed batch.
                with self.assertRaises(ValueError): a.strict_prediction(self.base,reply)
        result=a.finalize(self.db,self.base,ready['batch_id'],
             json.dumps({'session_id':sid,'response':'not JSON'}).encode())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['reason'],'MODEL_JSON_INVALID')
        self.assertEqual(self.base.quoted,0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0],1)

    def test_age_limit_and_manual_abandon(self):
        ready=self.prepare_ready(); ident=ready['batch_id']; sid=self.mark(ident)
        self.base.now+=a.MAX_PENDING_AGE_MS+1
        result=a.finalize(self.db,self.base,ident,
             json.dumps({'session_id':sid,'response':'{"direction":"UP","confidence":0.7,"rationale":"ETF"}'}).encode())
        self.assertEqual(result['reason'],'MODEL_RESULT_TOO_LATE')
        self.assertEqual(self.base.quoted,0)
        # A separate pending batch can be abandoned only explicitly after reconciliation.
        self.base.now+=1000
        self.add_story('s2'); ready=a.prepare(self.db,self.base)
        self.assertEqual(ready['status'],'ready')
        self.assertEqual(a.abandon(self.db,self.base,ready['batch_id'])['status'],'abandoned')

    def test_clock_failure_before_model_attempt(self):
        def fail(): raise RuntimeError('CLOCK_SKEW_GT_3S')
        self.base.check_clock=fail
        with self.assertRaises(RuntimeError):a.prepare(self.db,self.base)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0],0)


    # --- v1.2 automation ---------------------------------------------------
    def test_auto_stop_is_derived_from_db_and_survives_reload(self):
        st = a.auto_state(self.db)
        self.assertEqual(st["stopped"], False)
        self.db.execute("INSERT INTO predictions VALUES('p1',1,75000.,'UP',0.6,'r','m','a','b','[]')")
        self.db.commit()
        st2 = a.auto_state(self.db)
        self.assertEqual(st2["stopped"], True)
        self.assertEqual(st2["predictions"], 1)
        out = a.auto(self.db, self.base, 1793966400000)
        self.assertEqual(out["status"], "skip")
        self.assertEqual(out["reason"], "auto_stopped_after_first_prediction")

    def test_auto_blocks_on_pending_batch_without_side_effects(self):
        ready = self.prepare_ready()
        self.assertIn("NEWS_DATA_JSON_START", ready["prompt"])
        budget_before = self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0]
        out = a.auto(self.db, self.base, 1793966400000 + 900000)
        self.assertEqual(out["status"], "skip")
        self.assertEqual(out["reason"], "blocked_pending_batch")
        budget_after = self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0]
        self.assertEqual(budget_before, budget_after)

    def test_auto_slot_claim_is_idempotent(self):
        self.db.execute("INSERT INTO meta VALUES('warmup_at','1793966000000')")
        self.db.commit()
        slot = 1793966400000
        out = a.auto(self.db, self.base, slot)
        self.assertEqual(out["status"], "skip")
        self.assertEqual(out["reason"], "no_recent_new_headlines")
        again = a.auto(self.db, self.base, slot)
        self.assertEqual(again["reason"], "slot_already_claimed")
        other = a.auto(self.db, self.base, slot + 900000)
        self.assertNotEqual(other["reason"], "slot_already_claimed")

    def test_auto_claims_no_slot_when_stopped(self):
        self.db.execute("INSERT INTO predictions VALUES('p1',1,75000.,'UP',0.6,'r','m','a','b','[]')")
        self.db.commit()
        a.auto(self.db, self.base, 1793966400000)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM auto_slots").fetchone()[0], 0)

    def test_auto_ready_then_exactly_one_prediction_stops_forever(self):
        self.base.rss_next = [("s1", "feed", "Bitcoin ETF news headlines", "", "url", 1793966399000, 1793966399000)]
        self.db.execute("INSERT INTO meta VALUES('warmup_at','1793966000000')")
        self.db.commit()
        slot = 1793966400000
        ready = a.auto(self.db, self.base, slot)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(self.base.quoted, 0)
        self.mark(ready["batch_id"])
        fin = a.finalize(self.db, self.base, ready["batch_id"],
                         json.dumps({"session_id": "ses_test123",
                                     "response": '{"direction":"UP","confidence":0.6,"rationale":"etf"}'}).encode())
        self.assertEqual(fin["status"], "created")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 1)
        stopped = a.auto(self.db, self.base, slot + 3600000)
        self.assertEqual(stopped["reason"], "auto_stopped_after_first_prediction")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 1)

    def test_auto_respects_model_budget_cap(self):
        for _ in range(6):
            self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (self.base.now, "model_attempt", "started", "x"))
        self.db.commit()
        out = a.auto(self.db, self.base, 1793966400000)
        self.assertEqual(out["status"], "skip")
        self.assertEqual(out["reason"], "max_6_model_calls_per_24h")

    def test_auto_and_labels_are_separate_paths(self):
        # labels mode never creates predictions and never consumes model budget
        self.db.execute("INSERT INTO predictions VALUES('p9',1793966000000,75000.,'UP',0.6,'r','m','a','b','[]')")
        self.db.commit()
        before = self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0]
        a.base_module = lambda: self.base
        try:
            a.main(["labels", "--db", ":memory:"])
        finally:
            a.base_module = __import__("news_mt_cycle").base_module
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0], before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 1)

    # --- v1.3 label slots --------------------------------------------------
    LABEL_SLOT = 1793988900000

    def test_labels_slot_claim_is_atomic_across_instances(self):
        first = a.labels(self.db, self.base, self.LABEL_SLOT)
        self.assertEqual(first["status"], "ok")
        second = a.labels(self.db, self.base, self.LABEL_SLOT)
        self.assertEqual(second["status"], "skip")
        self.assertEqual(second["reason"], "label_slot_already_claimed")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM label_slots").fetchone()[0], 1)

    def test_labels_slot_advances_to_next_window(self):
        a.labels(self.db, self.base, self.LABEL_SLOT)
        nxt = a.labels(self.db, self.base, self.LABEL_SLOT + a.LABEL_SLOT_MS)
        self.assertEqual(nxt["status"], "ok")

    def test_labels_slot_rejects_unaligned_epoch(self):
        with self.assertRaises(ValueError):
            a.labels(self.db, self.base, self.LABEL_SLOT + 1000)

    def test_labels_slot_survives_restart(self):
        import os, tempfile, sqlite3 as sq
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "news.sqlite")
            with a.database(db, self.base) as c:
                self.assertEqual(a.labels(c, self.base, self.LABEL_SLOT)["status"], "ok")
            # brand-new connection = simulated process restart on the same DB file
            with a.database(db, self.base) as c2:
                again = a.labels(c2, self.base, self.LABEL_SLOT)
            self.assertEqual(again["reason"], "label_slot_already_claimed")
            with a.database(db, self.base) as c3:
                nxt = a.labels(c3, self.base, self.LABEL_SLOT + a.LABEL_SLOT_MS)
            self.assertEqual(nxt["status"], "ok")

    def test_labels_never_increments_model_attempt(self):
        self.db.execute("INSERT INTO predictions VALUES('p1',1,75000.,'UP',0.6,'r','m','a','b','[]')")
        self.db.commit()
        before = self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0]
        a.labels(self.db, self.base, self.LABEL_SLOT)
        after = self.db.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0]
        self.assertEqual(before, after)

    def test_clock_error_does_not_burn_the_slot(self):
        class ClockFail(FakeBase):
            def check_clock(self):
                raise RuntimeError("CLOCK_SKEW_GT_3S")
        bad = ClockFail()
        with self.assertRaises(RuntimeError):
            a.labels(self.db, bad, self.LABEL_SLOT)
        # slot not claimed -> next attempt in the same window still works
        self.assertEqual(a.labels(self.db, self.base, self.LABEL_SLOT)["status"], "ok")

    def test_manual_labels_without_slot_still_work(self):
        out = a.labels(self.db, self.base)
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["slot_ms"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM label_slots").fetchone()[0], 0)

    def test_label_slots_pruned_but_predictions_untouched(self):
        self.db.execute("INSERT INTO predictions VALUES('p1',1,75000.,'UP',0.6,'r','m','a','b','[]')")
        self.db.execute("INSERT INTO label_slots VALUES(?,?)", (self.LABEL_SLOT, 1))
        self.db.commit()
        a.labels(self.db, self.base, self.LABEL_SLOT + a.LABEL_SLOT_MS)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM label_slots").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 1)

    # --- v1.3.1: persistent 1h/4h/24h completion ---------------------------
    def insert_pilot_prediction(self, ident="pilot1", entry=None):
        if entry is None:
            entry = self.base.now
        self.db.execute("INSERT INTO predictions VALUES(?,?,75000.,'DOWN',0.6,'r','m','a','b','[]')",
                        (ident, entry))
        self.db.commit()
        return entry

    def add_pilot_outcome(self, ident, h, entry):
        self.db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?,?)",
                        (ident, h, entry+h*3600000, 75000., 0., 0., 0.))
        self.db.commit()

    def test_no_predictions_is_not_fully_labeled(self):
        self.assertFalse(a.label_progress(self.db)["pilot_complete"])
        self.assertEqual(a.label_progress(self.db)["pending_horizons"], 0)

    def test_completion_requires_all_horizons(self):
        entry = self.insert_pilot_prediction()
        for h in (1, 4):
            self.add_pilot_outcome("pilot1", h, entry)
        self.assertEqual(a.label_progress(self.db)["pending_horizons"], 1)
        self.assertFalse(a.label_progress(self.db)["pilot_complete"])
        self.add_pilot_outcome("pilot1", 24, entry)
        self.assertEqual(a.label_progress(self.db)["completed_horizons"], 3)
        self.assertTrue(a.label_progress(self.db)["pilot_complete"])

    def test_complete_skips_clock_network_and_claim_even_after_restart(self):
        entry = self.insert_pilot_prediction()
        for h in (1,4,24):
            self.add_pilot_outcome("pilot1", h, entry)
        self.base.check_clock = lambda: (_ for _ in ()).throw(AssertionError("no clock"))
        self.base.label = lambda c,now: (_ for _ in ()).throw(AssertionError("no labels"))
        result = a.labels(self.db,self.base,self.LABEL_SLOT)
        self.assertEqual(result["reason"], "pilot_labels_complete")
        self.assertTrue(result["pilot_complete"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM label_slots").fetchone()[0],0)
        self.assertTrue(a.status(self.db,self.base)["labels"]["pilot_complete"])

    def test_last_due_horizon_sets_complete_in_same_tick(self):
        entry = self.insert_pilot_prediction()
        def due_labels(c, now):
            added = 0
            for h in (1,4,24):
                if now >= entry+h*3600000:
                    added += c.execute("INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?,?,?,?)",
                        ("pilot1",h,entry+h*3600000,75000.,0.,0.,0.)).rowcount
            return added
        self.base.label = due_labels
        for h in (1,4):
            self.base.now = entry+h*3600000
            slot = (self.base.now//a.LABEL_SLOT_MS)*a.LABEL_SLOT_MS
            result = a.labels(self.db,self.base,slot)
            self.assertEqual(result["labels_created"],1)
            self.assertFalse(result["pilot_complete"])
        self.base.now = entry+24*3600000-1000
        result = a.labels(self.db,self.base,(self.base.now//a.LABEL_SLOT_MS)*a.LABEL_SLOT_MS)
        self.assertEqual(result["labels_created"],0)
        self.assertFalse(result["pilot_complete"])
        self.base.now = entry+24*3600000
        result = a.labels(self.db,self.base,(self.base.now//a.LABEL_SLOT_MS)*a.LABEL_SLOT_MS)
        self.assertEqual(result["labels_created"],1)
        self.assertTrue(result["pilot_complete"])

    def test_second_prediction_prevents_premature_label_shutdown(self):
        entry=self.insert_pilot_prediction()
        for h in (1,4,24):
            self.add_pilot_outcome("pilot1",h,entry)
        self.insert_pilot_prediction("pilot2",entry)
        progress=a.label_progress(self.db)
        self.assertEqual(progress["completed_horizons"],3)
        self.assertEqual(progress["pending_horizons"],3)
        self.assertFalse(progress["pilot_complete"])


if __name__ == '__main__': unittest.main()
