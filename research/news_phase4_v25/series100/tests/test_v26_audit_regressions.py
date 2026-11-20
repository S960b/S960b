#!/usr/bin/env python3
"""NEWS v2.6 audit-finding regressions, ALL synthetic and isolated.

These tests demonstrate that a passing legacy suite alone was not sufficient:
asset binding, archived market evidence, terminal deadlines and article revisions
need their own adversarial assertions. No provider, Binance, activation or orders.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE=Path(__file__).resolve().parent.parent
ROOT=HERE.parent
for d in (HERE,ROOT/"release"/"source",ROOT/"release"/"source"/"v22_live",
          ROOT/"rss_expansion"):
    if str(d) not in sys.path:
        sys.path.insert(0,str(d))

import series_guard as G
import tier2 as T2
import v26_asset_binding as B
import v26_story_guard as STORY
import v26_label_policy as LABEL
import v26_series_runner as RUN
import v26_market_provenance as PROV


class Regression(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def _db(self):
        path=self.root/"series.sqlite"
        with T2.open_tier2(path):
            pass
        con=G.open_db(path)
        self.addCleanup(con.close)
        B.install_schema(con)
        con.executescript(RUN.MARKET_LINKS_SCHEMA)
        eid="e"*64
        G.claim(con,event_id=eid,semantic_cluster_id="d"*64,
                trial_id="evt-"+eid,at_ms=1000)
        con.execute(
            "INSERT INTO series_asset_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid,"ETHUSDT","decrypt","MULTI",100,200,
             "a"*64,"test-rule",300,0,"b"*64,"c"*64,"d"*64,"test-v"))
        return con,eid

    def _seal(self,con,eid,prompt_hash="c"*64):
        con.execute("INSERT INTO tier2_triples VALUES ("+
                    ",".join("?"*20)+")",
                    (eid,"v26","observational_tier2","v26-test",
                     prompt_hash,"a"*64,"b"*64,1,"test-model",
                     "d"*64,"e"*64,"crypto",100,0,2500.0,
                     300,290,14_400_000,"f"*64,300))

    def test_asset_sql_immutable_and_seal_snapshot(self):
        con,eid=self._db()
        for command in (
            "UPDATE series_asset_bindings SET symbol='BTCUSDT' WHERE event_id=?",
            "DELETE FROM series_asset_bindings WHERE event_id=?"):
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(command,(eid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self._seal(con,eid,prompt_hash="9"*64)
        self._seal(con,eid)
        self.assertEqual(con.execute(
            "SELECT symbol,prompts_sha256 FROM series_seal_symbols "
            "WHERE event_id=?", (eid,)).fetchone(),("ETHUSDT","c"*64))
        self.assertEqual(B.bound_symbol(con,eid),"ETHUSDT")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute(
                "UPDATE series_seal_symbols SET symbol='BTCUSDT' WHERE event_id=?",
                (eid,))
        # Deliberately remove one trigger in a DISPOSABLE test DB to simulate
        # corruption from an older release/import, then require read fail-closed.
        con.execute("DROP TRIGGER v26_binding_immutable_update")
        con.execute(
            "UPDATE series_asset_bindings SET symbol='BTCUSDT' WHERE event_id=?",
            (eid,))
        with self.assertRaisesRegex(B.BindingError,"binding_symbol_mismatch"):
            B.bound_symbol(con,eid)

    def test_market_evidence_revalidated_on_read(self):
        con,eid=self._db()
        operation="hourly"
        symbol="ETHUSDT"
        raw=b"[]"
        raw_sha=PROV.sha(raw)
        mkt=self.root/"binance_raw"
        mkt.mkdir()
        raw_path=mkt/(raw_sha+".bin")
        raw_path.write_bytes(raw)
        result_sha=PROV.sha(PROV.canonical({"symbol":symbol,"features":{}}))
        url="https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1h&limit=30"
        witness={
            "schema":PROV.SCHEMA,"operation":operation,"symbol":symbol,
            "request_url":url,"final_url":url,"status":200,
            "request_started_ms":100,"response_received_ms":200,
            "raw_sha256":raw_sha,"raw_bytes":len(raw),
            "result_sha256":result_sha,"raw_path":str(raw_path)
        }
        payload=PROV.canonical(witness)
        manifest_sha=PROV.sha(payload)
        (mkt/(manifest_sha+".json")).write_bytes(payload)
        con.execute(
            "INSERT INTO series_market_witnesses "
            "(event_id,operation,symbol,request_url,raw_sha256,"
            "manifest_sha256,result_sha256) VALUES(?,?,?,?,?,?,?)",
            (eid,operation,symbol,url,raw_sha,manifest_sha,result_sha))
        with mock.patch.object(RUN,"MARKET_RAW",mkt):
            self.assertTrue(RUN.verify_saved_witness(
                con,event_id=eid,operation=operation,symbol=symbol))
            raw_path.write_bytes(b"[altered]")
            with self.assertRaisesRegex(RUN.SeriesV26Error,"market_witness"):
                RUN.verify_saved_witness(con,event_id=eid,operation=operation,
                                         symbol=symbol)

    def _label_db(self):
        path=self.root/"label.sqlite"
        con=sqlite3.connect(path)
        con.execute("CREATE TABLE tier2_outcomes(event_id TEXT PRIMARY KEY,"
                    "due_ms INTEGER,price REAL,observed_ms INTEGER,source TEXT,"
                    "drift_ms INTEGER,label_state TEXT)")
        con.commit()
        con.close()
        return path

    def test_late_success_is_censored_without_market_call(self):
        p=self._label_db()
        row={"event_id":"e"*64,"due_ms":1000}
        with mock.patch.object(LABEL.LW,"sealed_rows",return_value=[row]):
            result=LABEL.settle(
                db_path=p,now_ms=1101,max_wait_ms=100,
                symbol_fn=lambda *a: self.fail("late symbol lookup"),
                verify_fn=lambda *a,**k:self.fail("late evidence lookup"),
                candle_fn=lambda **k:self.fail("late candle lookup"))
        self.assertEqual(result["outcomes"][0]["status"],"censored")
        with sqlite3.connect(p) as con:
            self.assertEqual(con.execute(
                "SELECT label_state,source FROM tier2_outcomes").fetchone(),
                ("censored","label_deadline_expired"))

    def test_bug_and_tamper_are_durable_terminal(self):
        p=self._label_db()
        row={"event_id":"e"*64,"due_ms":1000}
        with mock.patch.object(LABEL.LW,"sealed_rows",return_value=[row]):
            outcome=LABEL.settle(
                db_path=p,now_ms=70_000,
                symbol_fn=lambda *a:"ETHUSDT",
                verify_fn=lambda *a,**k:None,
                candle_fn=lambda **k:(_ for _ in ()).throw(RuntimeError("unhandled")))
            self.assertEqual(outcome["outcomes"][0]["status"],"failed")
            again=LABEL.settle(
                db_path=p,now_ms=70_100,
                symbol_fn=lambda *a:self.fail("already terminal"),
                verify_fn=lambda *a,**k:self.fail("already terminal"),
                candle_fn=lambda **k:self.fail("already terminal"))
            self.assertTrue(again["outcomes"][0]["already"])
        with sqlite3.connect(p) as con:
            self.assertEqual(con.execute(
                "SELECT label_state FROM tier2_outcomes").fetchone(),("failed",))
        # Independent disposable DB for a corrupted witness preflight.
        p2=self.root/"label2.sqlite"
        p2.write_bytes(p.read_bytes())
        with sqlite3.connect(p2) as con:
            con.execute("DELETE FROM tier2_outcomes")
            con.commit()
        with mock.patch.object(LABEL.LW,"sealed_rows",return_value=[row]):
            tampered=LABEL.settle(
                db_path=p2,now_ms=70_000,
                symbol_fn=lambda *a:"ETHUSDT",
                verify_fn=lambda *a,**k:(_ for _ in ()).throw(
                    RUN.SeriesV26Error("market_witness_raw_or_manifest_modified")),
                candle_fn=lambda **k:self.fail("tamper fetched a new candle"))
        self.assertEqual(tampered["outcomes"][0]["status"],"censored")

    def test_pubdate_revisions_keep_raw_occurrences_but_not_candidates(self):
        con=sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("CREATE TABLE v24_events(content_sha256 TEXT,"
                    "source_id TEXT,origin_url TEXT,observed_ms INTEGER,"
                    "first_seen_ms INTEGER)")
        url="https://example.org/article?story=7"
        con.executemany("INSERT INTO v24_events VALUES (?,?,?,?,?)",[
            ("old","coindesk",url,1000,1001),
            ("revised","coindesk",url+"&utm_source=feed",2000,2001),
            ("different","coindesk","https://example.org/article?story=8",
             2000,2001)])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM v24_events"
                                     ).fetchone()[0],3)
        self.assertTrue(STORY.is_first(
            con,event_id="old",source_id="coindesk",origin_url=url))
        self.assertFalse(STORY.is_first(
            con,event_id="revised",source_id="coindesk",origin_url=url))
        self.assertTrue(STORY.is_first(
            con,event_id="different",source_id="coindesk",
            origin_url="https://example.org/article?story=8"))
        self.assertEqual(STORY.story_key("coindesk",url),
                         STORY.story_key("coindesk",url+"&utm_source=feed"))


if __name__=="__main__":
    unittest.main(verbosity=2)
