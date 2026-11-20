#!/usr/bin/env python3
"""Offline, no-provider E2E regression for NEWS v2.6's full 3-symbol chain.

Synthetic timestamps and responses are explicitly TEST FIXTURES and never
prospective observations. No HTTP, no model calls, no production DB writes.
Checks raw RSS -> sidecar -> candidate -> fixed symbol -> three prompts ->
market URL witnesses -> mock Tier-2 seal -> signed-symbol label -> monitor.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import TestCase, main, mock

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/"release"/"source"
LIVE=SOURCE/"v22_live"
RSS=HERE.parent/"rss_expansion"
for p in (SOURCE,LIVE,RSS,HERE):
    if str(p) not in sys.path:
        sys.path.insert(0,str(p))

import v26_feed_pipeline as PIPE
import v26_asset_binding as B
import v26_symbol_market as MARKET
import v26_market_provenance as PROV
import v26_series_runner as RUN
import v26_monitor as MON
import v26_rss_collector as RSSCOL
import rss_sidecar as SC
import v24_collector as COL
import series_guard as G
import tier2 as T2
import tier2_orchestrator as O
import v22_arms as A

CUTOFF=1_800_000_000_000
SYMBOL_HEADLINES={
    "BTCUSDT":("bitcoin_core","Bitcoin security advisory CVE-2025-62859","BTC"),
    "ETHUSDT":("decrypt","Ethereum suffers major exploit","MULTI"),
    "SOLUSDT":("solana_status","Solana mainnet halted","SOL"),
}


class Fake:
    def __init__(self, req, payload):
        self.status=200
        self.url=req.full_url
        self.data=json.dumps(payload,sort_keys=True).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def geturl(self):
        return self.url
    def read(self,lim=-1):
        return self.data if lim<0 else self.data[:lim]


def candles_1h(symbol):
    base={"BTCUSDT":60000,"ETHUSDT":2500,"SOLUSDT":150}[symbol]
    rows=[]
    for i in range(30):
        start=CUTOFF-(30-i)*MARKET.HOUR_MS
        rows.append([start,"1","1","1",str(base+i),"1",
                     start+MARKET.HOUR_MS-1])
    return rows


class MultiAssetE2E(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=RUN.store.__globals__["DB"] if False else self.root/"series.sqlite"
        self.feed=COL.open_db(self.root/"rss.sqlite")
        self.addCleanup(self.feed.close)
        self.side=SC.connect(self.root/"rss_meta.sqlite")
        self.addCleanup(self.side.close)
        c=T2.open_tier2(self.db)
        c.close()
        self.con=G.open_db(self.db)
        self.addCleanup(self.con.close)
        B.install_schema(self.con)
        self.con.executescript(RUN.MARKET_LINKS_SCHEMA)

    def case(self,symbol):
        sid,title,scope=SYMBOL_HEADLINES[symbol]
        pub=CUTOFF+1000
        obs=pub+1000
        date=format_datetime(datetime.fromtimestamp(pub/1000,timezone.utc),usegmt=True)
        raw=(f"<rss><channel><item><title>{title}</title>"
             f"<link>https://example.org/{sid}/security</link>"
             f"<pubDate>{date}</pubDate>"
             f"<description>Confirmed security incident or interruption</description>"
             f"</item></channel></rss>").encode()
        parsed=RSSCOL.RSS.parse_feed(raw,source_id=sid,observed_ms=obs)
        self.assertEqual(len(parsed["records"]),1)
        got={
            "source_id":sid,
            "raw_body":{"body":raw,"observed_ms":obs,"status":200},
            "fetched":{"status":200,"content_type":"application/rss+xml","attempts":1},
            "records":[dict(r,terms_decision="metadata",dated=True)
                       for r in parsed["records"]],
            "parse_error":None
        }
        PIPE.ingest_one(events_db=self.feed,sidecar_db=self.side,
                        source_id=sid,limiter=object(),raw_dir=self.root/"raw",
                        harvested=got)
        now=obs+1000
        selected=PIPE.candidate(events_db=self.feed,sidecar_db=self.side,
                                 now_ms=now)
        self.assertIsNotNone(selected)
        event,row,chosen=selected
        self.assertEqual(chosen,symbol)
        self.assertEqual(scope,got["records"][0]["asset_scope"])
        self.assertEqual(event["summary"],got["records"][0]["summary"])

        def opener(req, timeout):
            self.assertEqual(req.full_url.split("symbol=")[1].split("&")[0],symbol)
            self.assertIn("interval=1h",req.full_url)
            return Fake(req,candles_1h(symbol))

        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("live HTTP forbidden")):
            market,proof=PROV.observe("hourly",symbol,cutoff_ms=CUTOFF,
                                     opener=opener,evidence_dir=self.root/"market",
                                     clock=lambda:now)
        self.assertEqual(market["features"]["candle_count"],26)
        prompts=MARKET.symbol_prompts(symbol,event,market["features"])
        eid=event["event_id"]
        trial="evt-"+eid
        G.claim(self.con,event_id=eid,semantic_cluster_id=
                COL.semantic_cluster_id(event["title"],event["published_ms"]),
                trial_id=trial,at_ms=now)
        bind=B.bind_claim(self.con,event_id=eid,rss_row=row,symbol=symbol,
                          decision_ms=now,market_snapshot=market,prompts=prompts)
        self.assertEqual(bind["symbol"],symbol)
        RUN.write_witness(self.con,event_id=eid,operation="hourly",
                          symbol=symbol,witness=proof)

        # An explicit mock attestation is tagged as an offline fixture.
        # It is never counted as a real DeepSeek result.
        def fake_executor(prompts,*,now_ms,con,trial_id):
            out=[]
            for idx,arm in enumerate(A.ARM_NAMES):
                G.reserve_model_call(con,event_id=eid,arm=arm,at_ms=now_ms+idx)
                G.record_response(con,event_id=eid,arm=arm,
                                  response_id="synthetic-test-"+arm)
                text=json.dumps({"p_up":0.50,"expected_return_bps":0.0,
                                 "uncertainty":0.5,"rationale":"offline_fixture"})
                hashval=hashlib.sha256((arm+text).encode()).hexdigest()
                att=T2._write_attestation(self.root/"attestation.jsonl",
                                           response_sha=hashval,
                                           model="deepseek-v4-flash")
                out.append({
                    "arm":arm,"text":text,"completed_ms":now_ms+1000+idx*100,
                    "tool_evidence":T2.TIER,
                    "attestation_record_sha256":att["record_sha256"],
                    "model_observed":"deepseek-v4-flash",
                    "model_expected":"deepseek-v4-flash",
                    "response_sha256":hashval,
                    "request_sha256":hashlib.sha256(arm.encode()).hexdigest(),
                    "terminal_response":True,
                    "retried_ambiguously":False,"tool_artifacts":[]
                })
            return out

        last_arm=now+1200
        stamp=last_arm+1000
        price={"BTCUSDT":60100,"ETHUSDT":2530,"SOLUSDT":152}[symbol]
        def quote_fn(*,after_ms):
            self.assertEqual(after_ms,last_arm)
            q={"symbol":symbol,"price":price,"observed_ms":stamp,
               "source":"binance_public_ticker"}
            self.assertEqual(B.check_entry(self.con,eid,q,last_arm_ms=after_ms,
                                           now_ms=stamp),symbol)
            return q

        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("live HTTP forbidden")):
            out=O.run_trial(
                con=self.con,event=event,prompts=prompts,
                executor=fake_executor,quote_fn=quote_fn,
                now_ms=now,trial_id=trial,
                attestation_path=self.root/"attestation.jsonl",
                gate_check=lambda:{"trial_id":trial},
                clock=lambda:stamp+1000,
                published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]]*1000,
                symbol=symbol,protocol_id=RUN.PROTOCOL_ID,
                prompts_version=RUN.PROMPTS_VERSION,
                protocol_file_sha256=RUN.protocol_sha(),
                cohort_extra="")
        self.assertEqual(out["status"],"sealed")
        self.assertEqual(out["due_ms"],stamp+4*T2.HORIZON_MS//4)
        G.finalize(self.con,event_id=eid,state="sealed")
        state=self.con.execute(
            "SELECT protocol_id,prompts_version FROM tier2_triples "
            "WHERE event_id=?",(eid,)).fetchone()
        self.assertEqual(state,(RUN.PROTOCOL_ID,RUN.PROMPTS_VERSION))
        binding_state=self.con.execute(
            "SELECT mode FROM tier2_bindings WHERE event_id=?",(eid,)).fetchone()
        self.assertEqual(binding_state,("offline_fixture",))

        due=out["due_ms"]
        minute=(due//MARKET.MIN_MS)*MARKET.MIN_MS
        candle={
            "symbol":symbol,"open_ms":minute,"close_ms":minute+59999,
            "source":"binance_public_1m_kline","close":float(price+1),
            "closed":True
        }
        self.assertEqual(B.check_exit(self.con,eid,candle,due_ms=due),symbol)
        with self.assertRaises(B.BindingError):
            B.check_exit(self.con,eid,dict(candle,symbol="BTCUSDT"
                         if symbol!="BTCUSDT" else "SOLUSDT"),due_ms=due)
        label=O.write_label(con=self.con,event_id=eid,due_ms=due,
                            candle=candle,now_ms=due+62000,symbol=symbol)
        self.assertEqual(label["label_state"],"labelled")
        G.record_label(self.con,event_id=eid,state="labelled",
                       at_ms=candle["close_ms"])
        snap=MON.report(series_path=self.db,feeds_path=self.root/"rss.sqlite",
                        now_ms=due+62000)
        self.assertEqual(snap["series"]["assets"][symbol]["sealed"],1)
        self.assertEqual(snap["series"]["assets"][symbol]["labelled"],1)
        self.assertEqual(snap["series"]["assets"][symbol]["reserved_calls"],3)
        self.assertEqual(snap["series"]["reserved_calls"],3)
        self.assertFalse(snap["errors"],snap)

    def test_btc(self):
        self.case("BTCUSDT")

    def test_eth(self):
        self.case("ETHUSDT")

    def test_sol(self):
        self.case("SOLUSDT")


if __name__=="__main__":
    main(verbosity=2)
