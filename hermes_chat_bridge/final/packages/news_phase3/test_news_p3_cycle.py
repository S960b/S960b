"""Offline regression tests. Only temporary SQLite files and mocked public APIs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import news_p3_base as base
import news_p3_cycle as p3
from news_p3_eval import evaluate


class Phase3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="news-p3-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "paper.sqlite")

    def with_db(self, fn):
        with p3.database(self.path, base) as c:
            return fn(c)

    def add_prediction(self, c, i, entry_ms=None, direction="UP"):
        t = base.ms_now() if entry_ms is None else entry_ms
        c.execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (f"p{i:03d}", t, 70000.0, direction, 0.6, "fixture",
                   "deepseek/deepseek-v4.1-flash", "x"*64, "y"*64, "[]"))
        return f"p{i:03d}"

    def add_attempts(self, c, n):
        for i in range(n):
            c.execute("INSERT INTO events VALUES(?,?,?,?)",
                      (base.ms_now() - i * 1000, "model_attempt", "started", "fixture"))

    def test_new_isolated_db_starts_with_no_attempts_and_cap(self):
        def check(c):
            self.assertEqual(base.meta_get(c, "experiment"), "news-phase3")
            self.assertEqual(p3.auto_state(c)["predictions"], 0)
            self.assertEqual(p3.auto_state(c)["model_attempts"], 0)
            self.assertFalse(p3.auto_state(c)["stopped"])
        self.with_db(check)

    def test_30_predictions_stop_before_any_external_calls(self):
        def check(c):
            for i in range(30):
                self.add_prediction(c, i)
            s = p3.auto_state(c)
            self.assertEqual(s["stop_reason"], "max_30_predictions")
            with patch.object(base, "check_clock", side_effect=AssertionError("network")):
                self.assertEqual(p3.prepare(c, base)["reason"], "max_30_predictions")
                self.assertTrue(p3.auto(c, base, 1800000000000)["status"] == "skip")
        self.with_db(check)

    def test_36_attempts_stop_including_failed_attempts(self):
        def check(c):
            self.add_attempts(c, 36)
            state = p3.auto_state(c)
            self.assertEqual(state["model_attempts"], 36)
            self.assertEqual(state["stop_reason"], "max_36_model_attempts")
            with patch.object(base, "collect", side_effect=AssertionError("network")):
                self.assertEqual(p3.prepare(c, base)["status"], "skip")
            self.assertTrue(p3.label_progress(c)["study_complete"])
        self.with_db(check)

    def test_pending_blocks_automatic_dispatch_without_model_replay(self):
        def check(c):
            c.execute("""INSERT INTO mt_batches(id,prepared_ms,status,input_hash,input_news,story_ids)
                         VALUES(?,?,'prompt_attempted',?,?,?)""",
                      ("a"*24,base.ms_now(),"b"*64,"[]","[]"))
            self.assertTrue(p3.auto_state(c)["blocked"])
            with patch.object(base, "check_clock", side_effect=AssertionError("network")):
                self.assertEqual(p3.auto(c,base,1800000000000)["reason"],"blocked_pending_batch")
        self.with_db(check)

    def test_unknown_model_identity_fails_closed_before_quote(self):
        def check(c):
            now = base.ms_now()
            c.execute("""INSERT INTO mt_batches
                         (id,prepared_ms,status,input_hash,input_news,story_ids,session_id)
                         VALUES(?,?,'prompt_attempted',?,?,?,?)""",
                      ("a"*24,now,"b"*64,"[]","[]","ses_testing"))
            payload = json.dumps({"session_id":"ses_testing",
                "response":'{"direction":"UP","confidence":0.8,"rationale":"test"}',
                "model":None}).encode()
            with patch.object(base,"quote",side_effect=AssertionError("quoted")):
                with self.assertRaisesRegex(ValueError,"MODEL_IDENTITY_UNVERIFIED"):
                    p3.finalize(c,base,"a"*24,payload)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],0)
            self.assertEqual(p3.pending(c)[1],"prompt_attempted")
        self.with_db(check)

    def test_confirmed_model_metadata_is_recorded_not_hardcoded(self):
        def check(c):
            now = base.ms_now()
            c.execute("""INSERT INTO stories VALUES(?,?,?,?,?,?,?,NULL)""",
                      ("story_1","fixture","BTC news","summary","https://example.invalid",now,now))
            c.execute("""INSERT INTO mt_batches
                         (id,prepared_ms,status,input_hash,input_news,story_ids,session_id)
                         VALUES(?,?,'prompt_attempted',?,?,?,?)""",
                      ("a"*24,now,"b"*64,"[]",'["story_1"]',"ses_testing"))
            payload=json.dumps({"session_id":"ses_testing",
                "response":'{"direction":"UP","confidence":0.8,"rationale":"test"}',
                "model":{"providerID":"deepseek","modelID":"deepseek-v4.1-flash"}}).encode()
            with patch.object(base,"check_clock",return_value=0),patch.object(base,"quote",return_value=70000):
                out=p3.finalize(c,base,"a"*24,payload)
            self.assertEqual(out["status"],"created")
            self.assertFalse(out["sampling_complete"])
            self.assertEqual(c.execute("SELECT model FROM predictions").fetchone()[0],
                             "deepseek/deepseek-v4.1-flash")
        self.with_db(check)

    def test_final_label_schedule_stops_only_after_study_cap_and_all_labels(self):
        def check(c):
            self.add_prediction(c,1)
            for h in (1,4,24):
                c.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?,?)",
                          ("p001",h,base.ms_now(),70001,0.001,0,0))
            self.assertFalse(p3.label_progress(c)["study_complete"])
            for i in range(2,31):
                self.add_prediction(c,i)
                for h in (1,4,24):
                    c.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?,?)",
                              (f"p{i:03d}",h,base.ms_now(),70001,0.001,0,0))
            self.assertTrue(p3.label_progress(c)["study_complete"])
        self.with_db(check)

    def test_autoslot_claim_blocks_duplicate_instances(self):
        def check(c):
            with patch.object(base,"check_clock",return_value=0),\
                 patch.object(base,"collect",return_value={"feeds_ok":1,"new_headlines":0}),\
                 patch.object(base,"label",return_value=0):
                first=p3.auto(c,base,1800000000000)
                second=p3.auto(c,base,1800000000000)
            self.assertEqual(first["reason"],"first_collection_warmup")
            self.assertEqual(second["reason"],"slot_already_claimed")
        self.with_db(check)

    def test_export_readonly_no_creating_missing_file(self):
        with self.assertRaisesRegex(RuntimeError,"PHASE3_DATABASE_ABSENT"):
            p3.read_only_snapshot(self.path,base)
        self.assertFalse(Path(self.path).exists())
        self.with_db(lambda c: None)
        p=Path(self.path)
        before=p.stat().st_mtime_ns
        result=p3.read_only_snapshot(self.path,base,export=True)
        self.assertEqual(result["experiment"],"news-phase3")
        self.assertEqual(result["predictions"],[])
        self.assertIn("evaluation",result)
        self.assertEqual(before,p.stat().st_mtime_ns)

    def test_fees_gross_net_and_naive_short(self):
        def check(c):
            self.add_prediction(c,1,entry_ms=1800000000000,direction="UP")
            c.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?,?)",
                      ("p001",1,1800003600000,70700,0.01,0,0))
            metrics=evaluate(c)["horizons"][0]
            self.assertAlmostEqual(metrics["model_gross"]["mean_pct"],1.0)
            self.assertAlmostEqual(metrics["cost_scenarios"]["0.003"]["model"]["mean_pct"],0.7)
            self.assertAlmostEqual(metrics["cost_scenarios"]["0.003"]["always_short"]["mean_pct"],-1.3)
            self.assertAlmostEqual(metrics["cost_scenarios"]["0.003"]["btc_hodl"]["mean_pct"],0.7)
        self.with_db(check)

    def test_phase2_database_path_is_always_refused(self):
        with self.assertRaisesRegex(RuntimeError,"PHASE2_DATABASE_PATH_FORBIDDEN"):
            p3.read_only_snapshot("/home/kali/.local/share/news-phase2/news.sqlite",base)

    def test_baseline_has_no_direct_model_api_or_trading_methods(self):
        content=Path(base.__file__).read_text()
        self.assertNotIn("api.deepseek.com",content)
        self.assertNotIn("DEEPSEEK_API_KEY",content)
        self.assertNotIn("Authorization",content)
        self.assertNotIn("POST",content)


if __name__=="__main__":
    unittest.main()
