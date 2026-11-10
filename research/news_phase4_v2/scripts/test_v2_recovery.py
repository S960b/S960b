"""In-memory/fake sealed RSS only; recovery must never prompt model or trade."""
import sqlite3
import tempfile
from pathlib import Path
import unittest

from test_v2_cycle import T, START, EID, fake_phase4
from v2_core import V2Error
from v2_cycle import (locked_db, reserve, bind_session, mark_prompt, finish,
                      _pending, readonly_status)
from v2_recovery import (present, count, create_recovery_schema, abandon,
                         is_abandoned)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.source=root/"source"
        self.store=root/"v2"
        fake_phase4(self.source)
        with locked_db(self.store,create=True,now_ms=START):
            pass

    def tearDown(self):
        self.tmp.cleanup()

    def make_partial(self,db):
        batch=reserve(db,self.source,T,enabled=True,daily_cap=4)
        self.assertEqual(batch["status"],"ready")
        for n,arm in enumerate(batch["arms"]):
            sid="fake_session_"+str(n)
            bind_session(db,arm["attempt_id"],sid,T)
            mark_prompt(db,arm["attempt_id"],T+1)
        first=batch["arms"][0]["attempt_id"]
        payload={"session_id":"fake_session_0",
                 "response":'{"p_up":0.51,"expected_return_bps":1,'
                            '"uncertainty":0.8,"rationale":"sample"}',
                 "model":{"providerID":"deepseek",
                          "modelID":"deepseek-v4-flash"}}
        finish(db,first,payload,T+2)
        return batch,payload

    def test_no_automatic_migration(self):
        with locked_db(self.store) as db:
            self.assertFalse(present(db))
            with self.assertRaises(V2Error):
                create_recovery_schema(db)
            self.assertFalse(present(db))
            self.assertEqual(count(db),0)

    def test_failed_one_arm_abandoned_append_only_and_unblocked(self):
        with locked_db(self.store) as db:
            batch,payload=self.make_partial(db)
            self.assertEqual(_pending(db),1)
            create_recovery_schema(db,enabled=True)
            self.assertTrue(present(db))
            with self.assertRaises(V2Error):
                abandon(db,EID,T+4)
            result=abandon(db,EID,T+4,enabled=True)
            self.assertEqual(result["status"],"abandoned_without_replay")
            self.assertEqual(count(db),1)
            self.assertTrue(is_abandoned(db,EID))
            self.assertEqual(_pending(db),0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts")
                             .fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM decisions")
                             .fetchone()[0],1)
            with self.assertRaises(V2Error):
                abandon(db,EID,T+5,enabled=True)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM abandoned_pairs WHERE event_id=?",(EID,))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE abandoned_pairs SET abandoned_ms=? WHERE event_id=?",
                           (T+5,EID))
            # second arm cannot silently finalize after the abandonment.
            missing=batch["arms"][1]["attempt_id"]
            payload["session_id"]="fake_session_1"
            with self.assertRaises(V2Error):
                finish(db,missing,payload,T+6)
            self.assertEqual(_pending(db),0)
            # Abandonment clears pending, but it must NOT bypass the
            # previously consumed 15-minute slot or the old event ID.
            self.assertEqual(reserve(db,self.source,T+7,
                        enabled=True,daily_cap=4)["reason"],
                             "v2_slot_already_claimed")
            self.assertEqual(reserve(db,self.source,T+900_000+7,
                        enabled=True,daily_cap=4)["reason"],
                             "v2_no_new_forward_event")
            self.assertEqual(readonly_status(self.store,T+10)["counts"]
                             ["abandoned"],1)

    def test_reject_not_fully_sent_pair(self):
        with locked_db(self.store) as db:
            reserve(db,self.source,T,enabled=True,daily_cap=4)
            create_recovery_schema(db,enabled=True)
            with self.assertRaises(V2Error):
                abandon(db,EID,T+10,enabled=True)

    def test_never_abandon_full_pair(self):
        with locked_db(self.store) as db:
            batch,payload=self.make_partial(db)
            create_recovery_schema(db,enabled=True)
            payload["session_id"]="fake_session_1"
            finish(db,batch["arms"][1]["attempt_id"],payload,T+10)
            with self.assertRaises(V2Error):
                abandon(db,EID,T+11,enabled=True)

    def test_recovery_schema_requires_single_install(self):
        with locked_db(self.store) as db:
            create_recovery_schema(db,enabled=True)
            with self.assertRaises(V2Error):
                create_recovery_schema(db,enabled=True)
            self.assertEqual(count(db),0)


if __name__=="__main__":
    unittest.main()
