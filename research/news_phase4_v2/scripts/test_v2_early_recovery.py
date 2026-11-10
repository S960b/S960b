"""No live SDK, public API or trading: synthetic, isolated recovery regression."""
import sqlite3
import tempfile
from pathlib import Path
import unittest

from test_v2_cycle import fake_phase4, EID, T, START
from v2_core import V2Error
from v2_cycle import (locked_db, reserve, bind_session, mark_prompt,
                      finish, quote, _pending, readonly_status)
from v2_recovery import create_recovery_schema, abandon as old_abandon
from v2_early_recovery import (
    present, count, is_early_abandoned, create_early_schema, abandon_early)


class EarlyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.source=root/"source"
        self.root=root/"v2"
        fake_phase4(self.source)
        with locked_db(self.root,create=True,now_ms=START) as db:
            create_recovery_schema(db,enabled=True)

    def tearDown(self):
        self.temp.cleanup()

    def early_pair(self,db):
        pair=reserve(db,self.source,T,enabled=True,daily_cap=4)
        self.assertEqual(pair["status"],"ready")
        first=next(x for x in pair["arms"] if x["arm"]=="news")
        bind_session(db,first["attempt_id"],"session_news_arm",T)
        mark_prompt(db,first["attempt_id"],T+1)
        return pair

    def test_no_implicit_migration_and_explicit_flags(self):
        with locked_db(self.root) as db:
            self.assertFalse(present(db))
            self.assertEqual(count(db),0)
            with self.assertRaises(V2Error):
                create_early_schema(db)
            with self.assertRaises(V2Error):
                abandon_early(db,EID,T+40*60_000)
            self.assertFalse(present(db))

    def test_early_failure_audited_and_old_attempts_preserved(self):
        with locked_db(self.root) as db:
            batch=self.early_pair(db)
            self.assertEqual(_pending(db),1)
            create_early_schema(db,enabled=True)
            with self.assertRaises(V2Error):
                abandon_early(db,EID,T+40*60_000)
            result=abandon_early(db,EID,T+40*60_000,enabled=True)
            self.assertEqual(result["status"],"early_pair_abandoned_without_replay")
            self.assertTrue(is_early_abandoned(db,EID))
            self.assertEqual(count(db),1)
            self.assertEqual(_pending(db),0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM prompts_sent").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entry_quotes").fetchone()[0],0)
            self.assertEqual(readonly_status(self.root,T+40*60_000)["counts"]
                             ["lifetime_calls"],2)
            self.assertEqual(readonly_status(self.root,T+40*60_000)["counts"]
                             ["abandoned_early"],1)
            # Does not return the old event in this or the next sampling slot.
            self.assertEqual(reserve(db,self.source,T+7,
                                    enabled=True,daily_cap=4)["reason"],
                             "v2_slot_already_claimed")
            self.assertEqual(reserve(db,self.source,T+900_007,
                                    enabled=True,daily_cap=4)["reason"],
                             "v2_no_new_forward_event")
            with self.assertRaises(V2Error):
                abandon_early(db,EID,T+50*60_000,enabled=True)

    def test_sql_append_only_and_no_replay_fences(self):
        with locked_db(self.root) as db:
            batch=self.early_pair(db)
            create_early_schema(db,enabled=True)
            abandon_early(db,EID,T+40*60_000,enabled=True)
            aid_news=next(x["attempt_id"] for x in batch["arms"]
                          if x["arm"]=="news")
            aid_nm=next(x["attempt_id"] for x in batch["arms"]
                          if x["arm"]=="news_market")
            for query,params in (
                ("DELETE FROM abandoned_early_pairs WHERE event_id=?",(EID,)),
                ("UPDATE abandoned_early_pairs SET abandoned_ms=? WHERE event_id=?",
                 (T+60*60_000,EID)),
                ("INSERT INTO sessions VALUES(?,?,?)",(aid_nm,"session_new",T+60)),
                ("INSERT INTO prompts_sent VALUES(?,?)",(aid_news,T+60)),
                ("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                 (aid_news,"providerA","modelA",T+60,"a"*64,0.5,0,0.9,"FLAT")),
                ("INSERT INTO entry_quotes VALUES(?,?,?,?)",
                 (EID,T+60,75000.0,"binance_spot_public"))):
                with self.subTest(query=query),self.assertRaises(sqlite3.DatabaseError):
                    db.execute(query,params)
            # The old completed forecast path is also prohibited.
            with self.assertRaises(V2Error):
                bind_session(db,aid_nm,"new-session",T+100)
            with self.assertRaises(V2Error):
                mark_prompt(db,aid_news,T+100)
            payload={"session_id":"session_news_arm","response":
                     '{"p_up":0.51,"expected_return_bps":10,'
                     '"uncertainty":0.9,"rationale":"test"}',
                     "model":{"providerID":"deepseek","modelID":"deepseek-v4-flash"}}
            with self.assertRaises(V2Error):
                finish(db,aid_news,payload,T+100)
            with self.assertRaises(V2Error):
                quote(db,EID,fetch=lambda _:None)

    def test_reject_too_young_and_unprompted(self):
        with locked_db(self.root) as db:
            pair=self.early_pair(db)
            create_early_schema(db,enabled=True)
            with self.assertRaises(V2Error):
                abandon_early(db,EID,T+1000,enabled=True)
        with tempfile.TemporaryDirectory() as temp2:
            root=Path(temp2)
            with locked_db(root,create=True,now_ms=START) as db:
                create_recovery_schema(db,enabled=True)
                create_early_schema(db,enabled=True)
                # Cannot abandon an event from the wrong (empty) study.
                with self.assertRaises(V2Error):
                    abandon_early(db,EID,T+40*60_000,enabled=True)

    def test_legacy_recovery_still_works_with_early_schema(self):
        with locked_db(self.root) as db:
            batch=reserve(db,self.source,T,enabled=True,daily_cap=4)
            create_early_schema(db,enabled=True)
            for n,arm in enumerate(batch["arms"]):
                sid="session_"+str(n)
                bind_session(db,arm["attempt_id"],sid,T)
                mark_prompt(db,arm["attempt_id"],T+1)
            first=batch["arms"][0]
            finish(db,first["attempt_id"],{"session_id":"session_0",
                "response":'{"p_up":0.5,"expected_return_bps":0,'
                           '"uncertainty":0.9,"rationale":""}',
                "model":{"providerID":"deepseek","modelID":"deepseek-v4-flash"}},
                T+3)
            with self.assertRaises(V2Error):
                abandon_early(db,EID,T+40*60_000,enabled=True)
            old_abandon(db,EID,T+40*60_000,enabled=True)
            self.assertEqual(_pending(db),0)
            self.assertEqual(count(db),0)

    def test_full_pair_cannot_be_early_abandoned(self):
        with locked_db(self.root) as db:
            batch=reserve(db,self.source,T,enabled=True,daily_cap=4)
            create_early_schema(db,enabled=True)
            for n,arm in enumerate(batch["arms"]):
                sid="session_"+str(n)
                bind_session(db,arm["attempt_id"],sid,T)
                mark_prompt(db,arm["attempt_id"],T+1)
                finish(db,arm["attempt_id"],{"session_id":sid,
                    "response":'{"p_up":0.5,"expected_return_bps":0,'
                               '"uncertainty":0.9,"rationale":""}',
                    "model":{"providerID":"deepseek","modelID":"deepseek-v4-flash"}},
                    T+3+n)
            with self.assertRaises(V2Error):
                abandon_early(db,EID,T+40*60_000,enabled=True)

    def test_second_migration_refused(self):
        with locked_db(self.root) as db:
            create_early_schema(db,enabled=True)
            with self.assertRaises(V2Error):
                create_early_schema(db,enabled=True)
            self.assertEqual(count(db),0)


if __name__=="__main__":
    unittest.main()
