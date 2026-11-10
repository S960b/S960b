"""No network; read-only milestone tests with fresh private temp SQLite."""
from pathlib import Path
import tempfile
import unittest

from v2_cycle import locked_db
from v2_milestones import snapshot
from v2_recovery import create_recovery_schema, abandon
from v2_early_recovery import create_early_schema, abandon_early

T=1_800_000_000_000
EID="a"*64


class MilestoneTests(unittest.TestCase):
    def test_absent_source_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"no_database"
            self.assertEqual(snapshot(root,T)["status"],"not_initialized")
            self.assertFalse(root.exists())

    def test_forecast_then_mature_four_hour_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"cohort"
            with locked_db(root,create=True,now_ms=T) as db:
                pass
            idle=snapshot(root,T+1000)
            self.assertEqual(idle["milestone"],"waiting_first_forward_event")
            with locked_db(root) as db:
                db.execute("INSERT INTO pairs VALUES(?,?,?,?,?)",
                           (EID,T,T+1000,"b"*64,"c"*64))
                db.commit()
            partial=snapshot(root,T+2000)
            self.assertEqual(partial["milestone"],
                             "incomplete_pair_requires_monitoring")
            with locked_db(root) as db:
                for index,arm in enumerate(("news","news_market")):
                    aid=chr(ord("d")+index)*64
                    db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)",
                               (aid,EID,arm,T+1000,"f"*64))
                    db.execute("INSERT INTO sessions VALUES(?,?,?)",
                               (aid,"session_"+str(index),T+2000))
                    db.execute("INSERT INTO prompts_sent VALUES(?,?)",
                               (aid,T+3000))
                    db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                               (aid,"deepseek","deepseek-v4-flash",T+4000,
                                "e"*64,0.5,0.0,0.9,"FLAT"))
                db.execute("INSERT INTO entry_quotes VALUES(?,?,?,?)",
                           (EID,T+5000,100_000.0,"binance_spot_public"))
                db.commit()
            ready=snapshot(root,T+6000)
            self.assertEqual(ready["milestone"],"first_forecast_pair_recorded")
            self.assertEqual(ready["counts"]["complete_pairs"],1)
            due=T+5000+4*3_600_000+120_000
            self.assertEqual(ready["first_pair"]["four_hour_label_due_ms"],due)
            self.assertEqual(ready["counts"]["mature_4h_labels"],0)
            with locked_db(root) as db:
                db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",
                           (EID,4,due-120_000,due+1000,100_200.0,
                            "binance_public_closed_1m"))
                db.commit()
            scored=snapshot(root,due+2000)
            self.assertEqual(scored["milestone"],"first_4h_result_recorded")
            self.assertEqual(scored["counts"]["mature_4h_labels"],1)
            self.assertEqual(scored["evaluation"]["labeled_pairs"],1)
            self.assertEqual(scored["evaluation"]["arms"]["news"]
                             ["paper_active_fraction"],0)

    def test_abandoned_failure_remains_visible_but_does_not_block_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"cohort"
            with locked_db(root,create=True,now_ms=T) as db:
                db.execute("INSERT INTO pairs VALUES(?,?,?,?,?)",
                           (EID,T,T+1000,"b"*64,"c"*64))
                for index,arm in enumerate(("news","news_market")):
                    aid=chr(ord("d")+index)*64
                    db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)",
                               (aid,EID,arm,T+1000,"f"*64))
                    db.execute("INSERT INTO sessions VALUES(?,?,?)",
                               (aid,"session_"+str(index),T+2000))
                    db.execute("INSERT INTO prompts_sent VALUES(?,?)",
                               (aid,T+3000))
                db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                           ("d"*64,"deepseek","deepseek-v4-flash",T+4000,
                            "e"*64,0.5,0.0,0.9,"FLAT"))
                db.commit()
                create_recovery_schema(db,enabled=True)
                abandon(db,EID,T+40_000,enabled=True)
            result=snapshot(root,T+50_000)
            self.assertEqual(result["milestone"],
                             "waiting_next_forward_event_after_abandonment")
            self.assertEqual(result["counts"]["abandoned_pairs"],1)
            self.assertEqual(result["counts"]["decisions"],1)

    def test_early_failed_pair_counted_and_excluded_without_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"cohort"
            with locked_db(root,create=True,now_ms=T) as db:
                db.execute("INSERT INTO pairs VALUES(?,?,?,?,?)",
                           (EID,T,T+1000,"b"*64,"c"*64))
                for arm,aid in (("news","d"*64),("news_market","f"*64)):
                    db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)",
                               (aid,EID,arm,T+1000,"a"*64))
                db.execute("INSERT INTO sessions VALUES(?,?,?)",
                           ("d"*64,"session_news",T+2000))
                db.execute("INSERT INTO prompts_sent VALUES(?,?)",
                           ("d"*64,T+3000))
                db.commit()
                create_recovery_schema(db,enabled=True)
                create_early_schema(db,enabled=True)
                abandon_early(db,EID,T+40*60_000,enabled=True)
            status=snapshot(root,T+50*60_000)
            self.assertEqual(status["counts"]["abandoned_pairs"],0)
            self.assertEqual(status["counts"]["abandoned_early_pairs"],1)
            self.assertEqual(status["counts"]["abandoned_total"],1)
            self.assertEqual(status["counts"]["pending_pairs"],0)
            self.assertEqual(status["milestone"],
                             "waiting_next_forward_event_after_abandonment")
            self.assertEqual(status["evaluation"]["labeled_pairs"],0)

    def test_incomplete_old_pair_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"cohort"
            with locked_db(root,create=True,now_ms=T) as db:
                db.execute("INSERT INTO pairs VALUES(?,?,?,?,?)",
                           (EID,T,T+1000,"b"*64,"c"*64))
            late=snapshot(root,T+40*60_000)
            self.assertEqual(
                late["milestone"],
                "incomplete_pair_requires_manual_reconciliation")


if __name__=="__main__":
    unittest.main()
