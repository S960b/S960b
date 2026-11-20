#!/usr/bin/env python3
"""NEWS v2.6 additive asset binding for the 100-event PAPER experiment.

An event's symbol must be fixed BEFORE any model call and cannot be changed.
This module does NOT activate the series or modify the live v2.5 runner.
Uses an extra table in a fresh/isolated series database; no existing table is
altered. Do not deploy until runner and label integration tests are complete.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "release" / "source"
LIVE = SRC / "v22_live"
RSS = HERE.parent / "rss_expansion"
for d in (SRC, LIVE, RSS):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import rss_noise_filter as NF
import rss_sidecar as SC
import v26_symbol_market as M
import v22_arms as A
import tier2 as T2

VERSION = "v26-series-asset-binding-v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS series_asset_bindings(
 event_id TEXT PRIMARY KEY REFERENCES series_claims(event_id),
 symbol TEXT NOT NULL CHECK(symbol IN ('BTCUSDT','ETHUSDT','SOLUSDT')),
 source_id TEXT NOT NULL,
 asset_scope TEXT NOT NULL,
 published_ms INTEGER NOT NULL,
 first_observed_ms INTEGER NOT NULL,
 raw_sha256 TEXT NOT NULL,
 filter_rule_version TEXT NOT NULL,
 decision_ms INTEGER NOT NULL,
 cutoff_ms INTEGER NOT NULL,
 market_sha256 TEXT NOT NULL,
 prompts_sha256 TEXT NOT NULL,
 system_sha256 TEXT NOT NULL,
 binding_version TEXT NOT NULL
);
-- Independent snapshot captured in the same transaction as the sealed triple.
CREATE TABLE IF NOT EXISTS series_seal_symbols(
 event_id TEXT PRIMARY KEY REFERENCES tier2_triples(event_id),
 symbol TEXT NOT NULL CHECK(symbol IN ('BTCUSDT','ETHUSDT','SOLUSDT')),
 prompts_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS v26_binding_immutable_update
BEFORE UPDATE ON series_asset_bindings
BEGIN SELECT RAISE(ABORT,'v26_binding_immutable'); END;
CREATE TRIGGER IF NOT EXISTS v26_binding_immutable_delete
BEFORE DELETE ON series_asset_bindings
BEGIN SELECT RAISE(ABORT,'v26_binding_immutable'); END;
CREATE TRIGGER IF NOT EXISTS v26_seal_symbol_immutable_update
BEFORE UPDATE ON series_seal_symbols
BEGIN SELECT RAISE(ABORT,'v26_seal_symbol_immutable'); END;
CREATE TRIGGER IF NOT EXISTS v26_seal_symbol_immutable_delete
BEFORE DELETE ON series_seal_symbols
BEGIN SELECT RAISE(ABORT,'v26_seal_symbol_immutable'); END;
CREATE TRIGGER IF NOT EXISTS v26_seal_requires_fixed_binding
BEFORE INSERT ON tier2_triples
BEGIN
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM series_asset_bindings b
  WHERE b.event_id=NEW.event_id AND b.prompts_sha256=NEW.prompts_sha256)
 THEN RAISE(ABORT,'v26_seal_binding_mismatch') END;
END;
CREATE TRIGGER IF NOT EXISTS v26_capture_sealed_symbol
AFTER INSERT ON tier2_triples
BEGIN
 INSERT INTO series_seal_symbols(event_id,symbol,prompts_sha256)
 SELECT NEW.event_id,b.symbol,NEW.prompts_sha256
 FROM series_asset_bindings b WHERE b.event_id=NEW.event_id;
END;
"""

class BindingError(ValueError):
    pass


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _valid_hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def install_schema(con):
    """DDL only; call on a COPY or new series DB before binding."""
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)


def _prompt_data(prompts, symbol, row, market_features, event_id):
    if type(prompts) is not dict or set(prompts) != set(A.ARM_NAMES):
        raise BindingError("missing_or_extra_arm")
    if len(set(prompts.values())) != 3:
        raise BindingError("duplicate_arm_prompt")
    for arm, txt in prompts.items():
        if type(txt) is not str:
            raise BindingError("prompt_not_text")
        header, sep, payload = txt.partition("FROZEN_INPUT_JSON:\n")
        if not sep:
            raise BindingError("missing_prompt_payload")
        try:
            obj = json.loads(payload)
        except ValueError as exc:
            raise BindingError("unparseable_prompt:" + arm) from exc
        if type(obj) is not dict or obj.get("symbol") != symbol:
            raise BindingError("prompt_cross_asset:" + arm)
        if symbol not in header or ("BTCUSDT" in header and symbol != "BTCUSDT"):
            raise BindingError("prompt_header_cross_asset:" + arm)
        if arm == A.ARM_MARKET:
            if (obj.get("pre_cutoff_market") != market_features
                    or obj.get("news") is not None
                    or obj.get("event_id") is not None):
                raise BindingError("market_arm_mixed_with_news")
        else:
            news = obj.get("news")
            if (obj.get("event_id") != event_id or not isinstance(news, dict)
                    or news.get("title") != row["title"][:400]
                    or news.get("summary") != row.get("summary", "")[:600]):
                raise BindingError("news_arm_wrong_event:" + arm)
            if arm == A.ARM_NEWS:
                if "pre_cutoff_market" in obj:
                    raise BindingError("news_only_market_leak")
            elif obj.get("pre_cutoff_market") != market_features:
                raise BindingError("news_market_wrong_features")
    meta = {arm: A.execution_metadata(arm, event_ms=market_features["candle_closes_ms"],
                                       model_id="deepseek-v4-flash")
            for arm in A.ARM_NAMES}
    event = {"event_id": event_id, "title": row["title"], "summary": row.get("summary", ""),
             "source": row["source_id"]}
    A.audit_market_arm_isolation(prompts, meta, event)


def bind_claim(con, *, event_id, rss_row, symbol, decision_ms,
               market_snapshot, prompts):
    """Bind exactly once to a prior series CLAIM, before any model reservation.

    Re-using an event_id with a new symbol fails. The registry is a second
    symbol check; the future runner MUST also use this same symbol for the
    live quote, frozen system prompt, and +4h candle.
    """
    M.checked_symbol(symbol)
    if not _valid_hex(event_id) or type(rss_row) is not dict:
        raise BindingError("invalid_event_identity")
    required = ("source_id", "asset_scope", "title", "published_ms",
                "first_observed_ms", "raw_sha256")
    if any(k not in rss_row for k in required):
        raise BindingError("missing_rss_metadata")
    if not _valid_hex(rss_row["raw_sha256"]):
        raise BindingError("invalid_raw_sha")
    if (type(decision_ms) is not int or type(market_snapshot) is not dict
            or market_snapshot.get("symbol") != symbol):
        raise BindingError("market_symbol_mismatch")
    canonical = SC.identity({
        "source_id": rss_row["source_id"], "title": rss_row["title"],
        "published_ms": rss_row["published_ms"]
    })
    if event_id != canonical:
        raise BindingError("event_occurrence_identity_mismatch")
    d = NF.decide(rss_row, now_ms=decision_ms)
    if not d.accepted or d.symbol != symbol:
        raise BindingError("filter_reject_or_symbol_mismatch:" + d.reason)
    cutoff = market_snapshot.get("cutoff_ms")
    features = market_snapshot.get("features")
    if (type(cutoff) is not int or cutoff <= 0
            or cutoff % M.HOUR_MS != 0 or cutoff > decision_ms
            or type(features) is not dict):
        raise BindingError("market_cutoff_invalid")
    A.audit_market_payload(features, cutoff_ms=cutoff, now_ms=decision_ms)
    if features.get("candle_count") != 26:
        raise BindingError("market_features_incomplete")
    _prompt_data(prompts, symbol, rss_row, features, event_id)
    prompt_hash = _digest(prompts)
    market_hash = _digest(market_snapshot)
    system_hash = hashlib.sha256(M.system_prompt(symbol).encode()).hexdigest()

    con.execute("BEGIN IMMEDIATE")
    try:
        state = con.execute("SELECT state FROM series_claims WHERE event_id=?",
                            (event_id,)).fetchone()
        if state != ("claimed",):
            raise BindingError("event_not_claimed")
        if con.execute("SELECT 1 FROM series_invocations WHERE event_id=? LIMIT 1",
                       (event_id,)).fetchone() is not None:
            raise BindingError("model_slot_already_reserved")
        con.execute(
            "INSERT INTO series_asset_bindings "
            "(event_id,symbol,source_id,asset_scope,published_ms,first_observed_ms,"
            "raw_sha256,filter_rule_version,decision_ms,cutoff_ms,market_sha256,"
            "prompts_sha256,system_sha256,binding_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, symbol, rss_row["source_id"], rss_row["asset_scope"],
             rss_row["published_ms"], rss_row["first_observed_ms"],
             rss_row["raw_sha256"], d.rule_version, decision_ms, cutoff,
             market_hash, prompt_hash, system_hash, VERSION))
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise BindingError("binding_already_exists_or_invalid_claim") from exc
    except BaseException:
        con.rollback()
        raise
    return {"event_id": event_id, "symbol": symbol, "market_sha256": market_hash,
            "prompts_sha256": prompt_hash, "system_sha256": system_hash}


def bound_symbol(con, event_id):
    if not _valid_hex(event_id):
        raise BindingError("invalid_event_id")
    found = con.execute(
        "SELECT symbol FROM series_asset_bindings WHERE event_id=?",
        (event_id,)).fetchone()
    if found is None:
        raise BindingError("unbound_event_symbol")
    symbol = M.checked_symbol(found[0])
    sealed = con.execute(
        "SELECT prompts_sha256 FROM tier2_triples WHERE event_id=?",
        (event_id,)).fetchone()
    if sealed is not None:
        snap = con.execute(
            "SELECT symbol,prompts_sha256 FROM series_seal_symbols "
            "WHERE event_id=?", (event_id,)).fetchone()
        if (snap is None or snap != (symbol,sealed[0])
                or con.execute(
                    "SELECT prompts_sha256 FROM series_asset_bindings "
                    "WHERE event_id=?", (event_id,)).fetchone() != sealed):
            raise BindingError("binding_symbol_mismatch")
    return symbol


def check_entry(con, event_id, quote, *, last_arm_ms, now_ms):
    symbol = bound_symbol(con, event_id)
    if not isinstance(quote, dict) or quote.get("symbol") != symbol:
        raise BindingError("entry_symbol_missing_or_mismatch")
    T2.validate_quote(quote, last_arm_ms, now_ms=now_ms, symbol=symbol)
    return symbol


def check_exit(con, event_id, candle, *, due_ms):
    """Verify exit ticker and due using BOTH the binding and sealed triple."""
    symbol = bound_symbol(con, event_id)
    triple = con.execute(
        "SELECT entry_observed_ms,horizon_ms FROM tier2_triples "
        "WHERE event_id=?", (event_id,)).fetchone()
    if triple is None:
        raise BindingError("missing_sealed_triple")
    sealed_due = int(triple[0]) + int(triple[1])
    if type(due_ms) is not int or sealed_due != due_ms:
        raise BindingError("exit_due_not_sealed")
    if not isinstance(candle, dict) or candle.get("symbol") != symbol:
        raise BindingError("exit_symbol_missing_or_mismatch")
    T2.validate_candle(candle, due_ms, symbol=symbol)
    return symbol


def selftest():
    import tempfile
    import series_guard as G
    with tempfile.TemporaryDirectory() as d:
        con = G.open_db(Path(d) / "series.sqlite")
        # Real production series store() applies the Tier-2 schema separately.
        # Reproduce that schema in this disposable test database only.
        con.executescript(T2.SCHEMA)
        install_schema(con)
        cutoff = 1_800_000_000_000
        pub = cutoff+1_000
        row = {
            "source_id":"decrypt", "asset_scope":"MULTI",
            "title":"Ethereum suffers major exploit", "summary":"Protocol breach reported",
            "published_ms":pub, "first_observed_ms":pub+1_000,
            "raw_sha256":"a"*64
        }
        eid = SC.identity(row)
        G.claim(con, event_id=eid, semantic_cluster_id="b"*64,
                trial_id="evt-"+eid, at_ms=cutoff+3_000)
        features = {"return_1h": 0.01, "return_4h": 0.02, "return_24h": -0.05,
                    "realized_vol_24h":0.04, "candle_opens_ms":cutoff-26*M.HOUR_MS,
                    "candle_closes_ms":cutoff-1, "candle_count":26}
        market = {"symbol":"ETHUSDT", "cutoff_ms":cutoff, "features":features}
        event = {"event_id":eid, "cohort":"crypto", "title":row["title"],
                 "summary":row["summary"], "source":row["source_id"],
                 "source_kind":"media", "published_ms":pub,
                 "observed_ms":pub+1_000}
        prompts = M.symbol_prompts("ETHUSDT", event, features)
        binding = bind_claim(con, event_id=eid, rss_row=row,
                             symbol="ETHUSDT", decision_ms=cutoff+3_000,
                             market_snapshot=market, prompts=prompts)
        assert binding["symbol"] == bound_symbol(con, eid) == "ETHUSDT"
        # Even direct SQL writes cannot silently rewrite a claimed asset.
        for sql in (
            "UPDATE series_asset_bindings SET symbol='BTCUSDT' WHERE event_id=?",
            "DELETE FROM series_asset_bindings WHERE event_id=?",
        ):
            try:
                con.execute(sql,(eid,))
                raise AssertionError("mutable asset binding accepted")
            except sqlite3.IntegrityError as exc:
                assert "v26_binding_immutable" in str(exc),exc
        assert bound_symbol(con,eid)=="ETHUSDT"
        try:
            bind_claim(con, event_id=eid, rss_row=row,
                       symbol="ETHUSDT", decision_ms=cutoff+3_000,
                       market_snapshot=market, prompts=prompts)
            raise AssertionError("duplicate binding accepted")
        except BindingError:
            pass
        try:
            check_entry(con, eid, {"symbol":"BTCUSDT", "price": 65000.0,
                "source":"binance_public_ticker", "observed_ms":cutoff+6_000},
                last_arm_ms=cutoff+5_000, now_ms=cutoff+6_000)
            raise AssertionError("wrong quote symbol accepted")
        except BindingError:
            pass
        assert check_entry(con, eid, {"symbol":"ETHUSDT", "price": 2500.,
                "source":"binance_public_ticker", "observed_ms":cutoff+6_000},
                last_arm_ms=cutoff+5_000, now_ms=cutoff+6_000) == "ETHUSDT"
        try:
            check_exit(con, eid, {}, due_ms=cutoff+4*M.HOUR_MS+6_000)
            raise AssertionError("unsealed label accepted")
        except BindingError:
            pass
        assert con.execute("SELECT COUNT(*) FROM series_asset_bindings").fetchone()[0] == 1
        assert G.counters(con)["calls_reserved"] == 0
        con.close()
    print("NEWS V26 ASSET BINDING SELFTEST OK: fixed symbol, atomic claim, quote, exit")

if __name__ == "__main__":
    selftest()
