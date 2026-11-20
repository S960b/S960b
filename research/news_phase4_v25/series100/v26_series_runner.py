#!/usr/bin/env python3
"""NEWS v2.6 — separate 14-RSS / BTC,ETH,SOL paper series executor.

ADDITIVE, NOT ARMED. Never modify the live v2.5 release or its series ledger.
Default command is status. --poll collects public RSS only (zero model calls).
--execute requires separate owner-pinned v2.6 activation and absent-by-default
V26_TRIGGER_ENABLED gate before ANY model/provider interaction. No trades.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "release" / "source"
LIVE = SOURCE / "v22_live"
RSS = HERE.parent / "rss_expansion"
for directory in (SOURCE, LIVE, RSS, HERE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import series_guard as G
import v26_asset_binding as B
import v26_feed_pipeline as PIPE
import v26_story_guard as STORY
import v26_market_provenance as PROV
import v26_symbol_market as MARKET
import rss_sidecar as SIDECAR
import v24_collector as COL
import v24_adapters as AD
import tier2 as T2
import tier2_orchestrator as O
import tier2_forward_driver as F
import tier2_label_watch as LW
import v21_paper_prep as P
import v22_arms as ARMS
import tier2_integrity as INTEG

ROOT = Path.home() / ".local/share/news-phase4-v26-series100"
DB = ROOT / "series.sqlite"
FEED_DB = ROOT / "rss.sqlite"
META_DB = ROOT / "rss_meta.sqlite"
RAW_DIR = ROOT / "rss_raw"
MARKET_RAW = ROOT / "binance_raw"
ATTEST = ROOT / "attestation.jsonl"
ACTIVATION = ROOT / "TIER2_V26_ACTIVATION.json"
GATE = ROOT / "V26_TRIGGER_ENABLED"
PROTOCOL = HERE / "V26_SERIES_PROTOCOL.md"
VERSION = "news-v26-rss14-multisymbol-v1"
PROMPTS_VERSION = "v2.6-rss14-btc-eth-sol-arms3-v1"
PROTOCOL_ID = "prospective-v2.6-tier2-multiasset"
MODEL = "deepseek-v4-flash"
MARKET_LINKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS series_market_witnesses(
 event_id TEXT NOT NULL REFERENCES series_claims(event_id),
 operation TEXT NOT NULL CHECK(operation IN ('hourly','entry','exit')),
 symbol TEXT NOT NULL CHECK(symbol IN ('BTCUSDT','ETHUSDT','SOLUSDT')),
 request_url TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL,
 manifest_sha256 TEXT NOT NULL,
 result_sha256 TEXT NOT NULL,
 PRIMARY KEY(event_id,operation)
);
"""


class SeriesV26Error(RuntimeError):
    pass


def ms():
    return time.time_ns() // 1_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def protocol_sha():
    return digest(PROTOCOL.read_bytes())


def secure_root():
    if ROOT.is_symlink():
        raise SeriesV26Error("series_root_is_symlink")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ROOT.is_symlink():
        raise SeriesV26Error("series_root_is_symlink")


def approved():
    """Independent gate check, even if v2.5's old gate is enabled."""
    data = G.validate_activation(activation_path=ACTIVATION, gate_path=GATE)
    if (data.get("protocol_sha256") != protocol_sha()
            or data.get("model") != MODEL
            or data.get("prompts_version") != PROMPTS_VERSION
            or data.get("protocol_id") != PROTOCOL_ID
            or data.get("source_roster_size") != 14
            or data.get("symbols") != ["BTCUSDT","ETHUSDT","SOLUSDT"]):
        raise SeriesV26Error("v26_activation_not_pinned")
    return data


def store():
    secure_root()
    if DB.is_symlink():
        raise SeriesV26Error("series_db_symlink")
    with contextlib.closing(T2.open_tier2(DB)):
        pass
    con = G.open_db(DB)
    B.install_schema(con)
    con.executescript(MARKET_LINKS_SCHEMA)
    return con


def feed_readonly():
    if not FEED_DB.is_file() or not META_DB.is_file():
        return None
    if FEED_DB.is_symlink() or META_DB.is_symlink():
        raise SeriesV26Error("rss_database_symlink")
    return (sqlite3.connect(FEED_DB.resolve().as_uri()+"?mode=ro", uri=True),
            sqlite3.connect(META_DB.resolve().as_uri()+"?mode=ro", uri=True))


def poll_one_cycle():
    """Public RSS collection only, without touching the model/series gates."""
    secure_root()
    if FEED_DB.is_symlink() or META_DB.is_symlink() or RAW_DIR.is_symlink():
        raise SeriesV26Error("rss_path_is_symlink")
    limiter = AD.RateLimiter()
    with contextlib.closing(COL.open_db(FEED_DB)) as feed, \
         contextlib.closing(SIDECAR.connect(META_DB)) as meta:
        outcome = {}
        for source_id in PIPE.SOURCES:
            try:
                outcome[source_id] = PIPE.ingest_one(
                    events_db=feed, sidecar_db=meta, source_id=source_id,
                    limiter=limiter, raw_dir=RAW_DIR)
            except (AD.FetchError, PIPE.PipelineError) as exc:
                outcome[source_id] = {
                    "error": type(exc).__name__ + ":" + str(exc)[:120]}
    return {"status":"rss_polled", "sources":len(PIPE.SOURCES),
            "model_calls":0, "counts":outcome}


def choose(con, now):
    current = con.execute(
        "SELECT event_id,semantic_cluster_id FROM series_claims").fetchall()
    excluded = {v[0] for v in current}
    clusters = {v[1] for v in current}
    pair = feed_readonly()
    if pair is None:
        return None
    feed, meta = pair
    try:
        for _ in range(PIPE.MAX_CANDIDATES_SCANNED):
            result = PIPE.candidate(
                events_db=feed, sidecar_db=meta, now_ms=now,
                excluded=excluded)
            if result is None:
                return None
            event, rss_row, symbol = result
            # Durable unique claim across pubDate changes of the same URL.
            # The v24 archive still preserves all publisher revisions.
            semantic = STORY.story_key(event["source"],event["origin_url"])
            if semantic not in clusters:
                return event, rss_row, symbol, semantic
            excluded.add(event["event_id"])
    finally:
        feed.close()
        meta.close()
    raise SeriesV26Error("too_many_semantic_duplicates")


def write_witness(con, *, event_id, operation, symbol, witness):
    """Append-only link from one event/symbol/stage to immutable raw bytes."""
    MARKET.checked_symbol(symbol)
    if witness.get("symbol") != symbol or witness.get("operation") != operation:
        raise SeriesV26Error("market_witness_wrong_symbol_or_stage")
    path = Path(witness.get("manifest_path") or "")
    raw_path = Path(witness.get("raw_path") or "")
    if (not path.is_file() or path.is_symlink()
            or not raw_path.is_file() or raw_path.is_symlink()):
        raise SeriesV26Error("market_witness_files_missing")
    payload = path.read_bytes()
    original = raw_path.read_bytes()
    PROV.verify_manifest(witness, original)
    if witness.get("raw_sha256") != digest(original):
        raise SeriesV26Error("market_witness_raw_hash_invalid")
    if digest(payload) != path.stem:
        raise SeriesV26Error("market_witness_manifest_hash_invalid")
    stored = json.loads(payload)
    if (stored.get("symbol") != symbol or stored.get("operation") != operation
            or stored.get("raw_sha256") != witness["raw_sha256"]):
        raise SeriesV26Error("market_witness_manifest_mismatch")
    # Re-observing an unchanged historical candle is safe after a crash.
    # Keep the FIRST immutable manifest. A changed raw response must never
    # silently replace evidence or produce a different label for this event.
    current = con.execute(
        "SELECT symbol,request_url,raw_sha256,result_sha256 "
        "FROM series_market_witnesses WHERE event_id=? AND operation=?",
        (event_id,operation)).fetchone()
    expected = (symbol,witness["request_url"],witness["raw_sha256"],
                witness["result_sha256"])
    if current is not None:
        if tuple(current) != expected:
            raise SeriesV26Error("market_witness_conflict")
        return {"status":"already_recorded"}
    con.execute(
        "INSERT INTO series_market_witnesses"
        "(event_id,operation,symbol,request_url,raw_sha256,"
        "manifest_sha256,result_sha256) VALUES(?,?,?,?,?,?,?)",
        (event_id,operation,symbol,witness["request_url"],witness["raw_sha256"],
         digest(payload),witness["result_sha256"]))
    con.commit()
    return {"status":"recorded"}


def verify_saved_witness(con, *, event_id, operation, symbol):
    """Re-verify DB row -> exact manifest -> exact Binance response bytes.

    Unlike write_witness(), this operates on durable historical evidence and
    MUST be invoked again before computing a +4h label. HTTP proof here means
    local request/response integrity, not Binance-signed asset authentication.
    """
    MARKET.checked_symbol(symbol)
    if operation not in ("hourly", "entry", "exit"):
        raise SeriesV26Error("invalid_market_witness_operation")
    record=con.execute(
        "SELECT symbol,request_url,raw_sha256,manifest_sha256,result_sha256 "
        "FROM series_market_witnesses WHERE event_id=? AND operation=?",
        (event_id,operation)).fetchone()
    if record is None:
        raise SeriesV26Error("market_witness_missing:"+operation)
    db_symbol,url,raw_sha,manifest_sha,result_sha=record
    if db_symbol!=symbol:
        raise SeriesV26Error("market_witness_symbol_mismatch")
    if any(type(h) is not str or len(h)!=64
           or any(c not in "0123456789abcdef" for c in h)
           for h in (raw_sha,manifest_sha,result_sha)):
        raise SeriesV26Error("market_witness_bad_digest")
    if MARKET_RAW.is_symlink():
        raise SeriesV26Error("market_witness_dir_symlink")
    manifest_path=MARKET_RAW/(manifest_sha+".json")
    raw_path=MARKET_RAW/(raw_sha+".bin")
    if any(p.is_symlink() or not p.is_file() for p in (manifest_path,raw_path)):
        raise SeriesV26Error("market_witness_evidence_missing")
    payload=manifest_path.read_bytes()
    raw=raw_path.read_bytes()
    if digest(payload)!=manifest_sha or digest(raw)!=raw_sha:
        raise SeriesV26Error("market_witness_raw_or_manifest_modified")
    try:
        witness=json.loads(payload)
        PROV.verify_manifest(witness,raw)
    except (KeyError,TypeError,ValueError,PROV.WitnessError) as exc:
        raise SeriesV26Error("market_witness_manifest_invalid") from exc
    if (witness.get("operation")!=operation
            or witness.get("symbol")!=symbol
            or witness.get("request_url")!=url
            or witness.get("raw_sha256")!=raw_sha
            or witness.get("result_sha256")!=result_sha
            or witness.get("raw_path")!=str(raw_path)):
        raise SeriesV26Error("market_witness_db_manifest_conflict")
    return True


def verify_sealed_market(con, *, event_id, symbol):
    """A sealed v2.6 event must retain both its entry and hourly evidence."""
    B.bound_symbol(con,event_id)
    verify_saved_witness(con,event_id=event_id,operation="hourly",symbol=symbol)
    verify_saved_witness(con,event_id=event_id,operation="entry",symbol=symbol)
    # An existing exit observation must also remain immutable on retries.
    if con.execute(
        "SELECT 1 FROM series_market_witnesses WHERE event_id=? "
        "AND operation='exit'", (event_id,)).fetchone() is not None:
        verify_saved_witness(con,event_id=event_id,operation="exit",symbol=symbol)
    return True


def model_executor(*, event_id, series_id, system_prompt):
    """Existing auditable raw wire client, with v2.6 instrument system text."""
    import tier2_raw_store as RAW
    import v22_toolfree_client as CLIENT
    import v22_attestation as ATT

    def execute(prompts, *, now_ms, con=None, trial_id=None):
        if con is None or not trial_id:
            raise SeriesV26Error("missing_model_context")
        results=[]
        for arm in ARMS.ARM_NAMES:
            check=approved()
            if check["series_id"] != series_id:
                raise SeriesV26Error("activation_switched")
            T2.mark_attempt(con, trial_id=trial_id, arm=arm, state="started",
                            now_ms=ms())
            started=ms()
            # Durable, global budget is reserved before the call; no retry.
            G.reserve_model_call(con, event_id=event_id, arm=arm, at_ms=started)
            T2.record_invocation(con,trial_id=trial_id,arm=arm,now_ms=started,
                                 session_id=None,limit=3)
            capture=RAW.raw_capture(attestation_path=ATTEST,
                                     trial_id=trial_id,arm=arm)
            response=CLIENT.call(model=MODEL, system=system_prompt,
                                 user=prompts[arm],raw_capture=capture)
            rid=response.get("provider_request_id")
            G.record_response(con,event_id=event_id,arm=arm,response_id=rid)
            T2.record_call_response(con,trial_id=trial_id,arm=arm,
                                    provider_response_id=rid)
            RAW.verify_wire(attestation_path=ATTEST,trial_id=trial_id,arm=arm,
                            request_sha256=response["request_sha256"],
                            response_sha256=response["response_sha256"])
            forwarded=ATT.forwarded_body(
                dict(response["request_body"],max_output_tokens=32000))
            record=ATT.attest(
                chat_body=response["request_body"],upstream_body=forwarded,
                parsed=json.loads(response["response_raw"]),
                latency_ms=ms()-started,model_expected=MODEL,log=ATTEST,
                raw_ingress=response["request_raw"],
                raw_response=response["response_raw"],
                upstream_capture="reconstructed_from_client_mapping",
                extra={"trial_id":trial_id,"arm":arm})
            item={
                "arm":arm,"text":response["content"],"completed_ms":ms(),
                "tool_evidence":T2.TIER,
                "attestation_record_sha256":record["record_sha256"],
                "model_observed":response.get("model"),"model_expected":MODEL,
                "response_sha256":record["response"]["sha256"],
                "request_sha256":record["ingress"]["sha256"],
                "terminal_response":response.get("finish_reason")=="stop",
                "retried_ambiguously":False,
                "tool_artifacts":record["response"].get("tool_items") or []
            }
            if item["tool_artifacts"]:
                raise SeriesV26Error("unexpected_model_tool_artifact")
            results.append(item)
            T2.mark_attempt(con,trial_id=trial_id,arm=arm,state="completed",
                            now_ms=item["completed_ms"],session_id=rid)
        return results
    return execute


@contextlib.contextmanager
def singleton():
    secure_root()
    fd=os.open(str(ROOT/"SERIES_V26_RUN.lock"),
               os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SeriesV26Error("already_running") from None
        yield
    finally:
        os.close(fd)


def execute_one():
    activation=approved()
    with contextlib.closing(store()) as con:
        counters=G.counters(con)
        if counters["sealed"]>=G.SERIES_LIMIT or counters["calls_reserved"]>=G.CALL_LIMIT:
            return {"status":"budget_exhausted","counts":counters}
        result=choose(con,ms())
        if result is None:
            return {"status":"no_eligible","counts":counters}
        event,row,symbol,semantic=result
        if len(event["event_id"])!=64:
            raise SeriesV26Error("bad_event_id")
        trial_id="evt-"+event["event_id"]
        timestamp=ms()
        cutoff=P.decision_cutoff(timestamp)
        market,market_proof=PROV.observe(
            "hourly",symbol,cutoff_ms=cutoff,evidence_dir=MARKET_RAW)
        ARMS.audit_market_payload(
            market["features"],cutoff_ms=cutoff,now_ms=timestamp)
        prompts=MARKET.symbol_prompts(
            symbol,event,market["features"],
            max_abs_bps=int(max(abs(T2.BPS_RANGE[0]),abs(T2.BPS_RANGE[1]))))
        decision=ms()
        T2.validate_event(
            event,decision,
            published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]]*1000)
        # Candidate must still pass the original deterministic policy.
        import rss_noise_filter as NF
        d=NF.decide(row,now_ms=decision)
        if not d.accepted or d.symbol!=symbol:
            raise SeriesV26Error("candidate_no_longer_eligible")
        if approved()["series_id"]!=activation["series_id"]:
            raise SeriesV26Error("activation_changed")
        G.claim(con,event_id=event["event_id"],semantic_cluster_id=semantic,
                trial_id=trial_id,at_ms=decision)
        try:
            B.bind_claim(con,event_id=event["event_id"],rss_row=row,
                         symbol=symbol,decision_ms=decision,
                         market_snapshot=market,prompts=prompts)
            write_witness(con,event_id=event["event_id"],operation="hourly",
                          symbol=symbol,witness=market_proof)
            sys_prompt=MARKET.system_prompt(symbol)
            frozen={
                "trial_id":trial_id,"series_id":activation["series_id"],
                "event":event,"decision_ms":decision,"cutoff_ms":cutoff,
                "symbol":symbol,"market":market,"market_witness":market_proof,
                "prompts":prompts,"system_prompt":sys_prompt,
                "system_prompt_sha256":digest(sys_prompt.encode("utf-8")),
                "prompt_version":PROMPTS_VERSION,"protocol_sha256":protocol_sha(),
                "protocol_id":PROTOCOL_ID
            }
            freeze_sha=F._write_new_json(
                ROOT/("frozen-"+trial_id+".json"),frozen)

            def gate_check():
                act=approved()
                if act["series_id"]!=activation["series_id"]:
                    raise SeriesV26Error("activation_switched")
                return {"trial_id":trial_id}

            def quote_fn(*,after_ms):
                quote,proof=PROV.observe(
                    "entry",symbol,after_ms=after_ms,evidence_dir=MARKET_RAW)
                B.check_entry(con,event["event_id"],quote,
                              last_arm_ms=after_ms,now_ms=ms())
                write_witness(con,event_id=event["event_id"],operation="entry",
                              symbol=symbol,witness=proof)
                return quote

            out=O.run_trial(
                con=con,event=event,prompts=prompts,
                executor=model_executor(event_id=event["event_id"],
                                        series_id=activation["series_id"],
                                        system_prompt=sys_prompt),
                quote_fn=quote_fn,now_ms=decision,trial_id=trial_id,
                attestation_path=ATTEST,gate_check=gate_check,
                published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]]*1000,
                frozen_sha256=freeze_sha,cohort_extra="",
                protocol_file_sha256=protocol_sha(),
                symbol=symbol,protocol_id=PROTOCOL_ID,
                prompts_version=PROMPTS_VERSION)
            if out["status"]!="sealed":
                raise SeriesV26Error("not_sealed")
            B.bound_symbol(con,event["event_id"])
            INTEG.verify_binding(con,event["event_id"],
                                 attestation_path=ATTEST,require_live=True)
            G.finalize(con,event_id=event["event_id"],state="sealed")
            return {"status":"sealed","event_id":event["event_id"],
                    "symbol":symbol,"due_ms":out["due_ms"],
                    "counts":G.counters(con)}
        except BaseException as exc:
            try:
                used=con.execute(
                    "SELECT COUNT(*) FROM series_invocations WHERE event_id=?",
                    (event["event_id"],)).fetchone()[0]
                G.finalize(con,event_id=event["event_id"],
                           state="aborted" if used==0 else "unknown",
                           reason=(type(exc).__name__+":"+str(exc))[:180])
            except Exception as final_exc:
                print("finalize_failed:"+str(final_exc)[:180],
                      file=sys.stderr,flush=True)
            raise


def settle_labels():
    if not DB.is_file() or DB.is_symlink():
        return {"status":"no_series"}
    now=ms()
    # Use the same DB connection provided by the label worker: evidence link
    # is committed before its labelled outcome and never re-synthesized.
    context={}

    def symbol_fn(con,event_id):
        symbol=B.bound_symbol(con,event_id)
        context.clear()
        context.update({"con":con,"event_id":event_id,"symbol":symbol})
        return symbol

    def candle_fn(*,due_ms,symbol):
        if symbol!=context.get("symbol") or context.get("con") is None:
            raise SeriesV26Error("label_symbol_context_missing")
        con=context["con"]
        eid=context["event_id"]
        candle,proof=PROV.observe("exit",symbol,due_ms=due_ms,now_ms=now,
                                  evidence_dir=MARKET_RAW)
        B.check_exit(con,eid,candle,due_ms=due_ms)
        write_witness(con,event_id=eid,operation="exit",symbol=symbol,
                      witness=proof)
        return candle

    out=LW.settle(db_path=DB,now_ms=now,
                  symbol_fn=symbol_fn,candle_fn=candle_fn,dry=False)
    with contextlib.closing(G.open_db(DB)) as con:
        rows=con.execute(
            "SELECT event_id,label_state,observed_ms FROM tier2_outcomes "
            "WHERE label_state IN ('labelled','censored')").fetchall()
        for event_id,state,observed in rows:
            if con.execute(
                "SELECT 1 FROM series_labels WHERE event_id=?",
                (event_id,)).fetchone() is None:
                G.record_label(con,event_id=event_id,state=state,at_ms=observed)
        return {"status":out["status"],"outcomes":out["outcomes"],
                "counts":G.counters(con)}


def status():
    if not DB.is_file():
        return {"status":"not_initialized","armed":False,
                "max_events":G.SERIES_LIMIT,"max_calls":G.CALL_LIMIT}
    with contextlib.closing(sqlite3.connect(
            DB.resolve().as_uri()+"?mode=ro",uri=True)) as con:
        counts=G.counters(con)
        try:
            assets=con.execute(
                "SELECT symbol,COUNT(*) FROM series_asset_bindings "
                "GROUP BY symbol ORDER BY symbol").fetchall()
        except sqlite3.OperationalError:
            assets=[]
    try:
        act=approved()
        armed=True
        state=act["series_id"]
    except (G.Refused,SeriesV26Error,OSError,ValueError):
        armed=False
        state="not_armed"
    return {"status":state,"armed":armed,"counts":counts,
            "assets":dict(assets),"max_events":G.SERIES_LIMIT,
            "max_calls":G.CALL_LIMIT}


def selftest():
    import tempfile
    import unittest.mock as mock
    with tempfile.TemporaryDirectory() as tmp:
        global ROOT, DB, FEED_DB, META_DB, RAW_DIR, MARKET_RAW, ATTEST, ACTIVATION, GATE
        old=(ROOT, DB, FEED_DB, META_DB, RAW_DIR, MARKET_RAW,
             ATTEST, ACTIVATION, GATE)
        ROOT=Path(tmp)
        DB=ROOT/"series.sqlite"
        FEED_DB=ROOT/"rss.sqlite"
        META_DB=ROOT/"rss_meta.sqlite"
        RAW_DIR=ROOT/"rss_raw"
        MARKET_RAW=ROOT/"binance_raw"
        ATTEST=ROOT/"attestation.jsonl"
        ACTIVATION=ROOT/"TIER2_V26_ACTIVATION.json"
        GATE=ROOT/"V26_TRIGGER_ENABLED"
        try:
            with contextlib.closing(store()) as con:
                assert con.execute(
                    "SELECT COUNT(*) FROM series_asset_bindings").fetchone()[0]==0
                assert con.execute(
                    "SELECT COUNT(*) FROM series_market_witnesses").fetchone()[0]==0
                assert G.counters(con)["calls_reserved"]==0
            assert status()["armed"] is False
            try:
                execute_one()
                raise AssertionError("unarmed model execution allowed")
            except G.Refused:
                pass
            with mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("unexpected HTTP")):
                assert status()["armed"] is False
        finally:
            (ROOT, DB, FEED_DB, META_DB, RAW_DIR, MARKET_RAW,
             ATTEST, ACTIVATION, GATE)=old
    print("NEWS V26 SERIES SELFTEST OK: isolated schema, no gate, 0 model calls")


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument("--poll",action="store_true",
                      help="one RSS collection cycle; no model calls")
    mode.add_argument("--execute",action="store_true",
                      help="one paper event, REQUIRES separate owner gate")
    mode.add_argument("--label",action="store_true",
                      help="settle due paper labels; no model calls")
    mode.add_argument("--selftest",action="store_true")
    args=ap.parse_args(argv)
    try:
        if args.selftest:
            selftest()
            return 0
        if args.poll:
            output=poll_one_cycle()
        elif args.execute:
            # First gate check before any network or DB schema operation.
            approved()
            with singleton():
                output=execute_one()
        elif args.label:
            # No lock, directories or side effects when no series exists.
            if not DB.is_file() or DB.is_symlink():
                output={"status":"no_series"}
            else:
                with singleton():
                    output=settle_labels()
        else:
            output=status()
        print(json.dumps(output,sort_keys=True,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status":"refused","error":type(exc).__name__,
                          "reason":str(exc)[:220]},sort_keys=True))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
