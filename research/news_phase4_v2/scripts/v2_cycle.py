#!/usr/bin/env python3
"""Separate Phase4 v2 probabilistic paper pilot; SQLite and public BTC only.

Commands are one-shot. Inference happens ONLY in the external MultiTool SDK
client, never in Python. No trade/order/private-key endpoint exists.
All dangerous operations require explicit opt-in, OFF by default.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from urllib.request import Request, urlopen

from v2_core import (POLICY_VERSION, PROMPT_SYSTEM, ARMS, HORIZON_H, V2Error,
                     canonical, decode_prediction, decision, digest,
                     evaluate, prompt, require, ID, MODEL)
from v2_source import candidates
from v2_recovery import (count as abandoned_count, is_abandoned,
                         create_recovery_schema, abandon)
from v2_early_recovery import (present as early_present,
                               count as early_count, is_early_abandoned,
                               create_early_schema, abandon_early)

ROOT = Path.home() / ".local/share/news-phase4-v2"
SOURCE = Path.home() / ".local/share/news-phase4"
CONTRACT_SHA = hashlib.sha256((POLICY_VERSION + ':' + PROMPT_SYSTEM).encode()).hexdigest()
MAX_DAILY = 4
MAX_LIFETIME = 80
MAX_AGE_MS = 30 * 60_000
SLOT_MS = 15 * 60_000
HOUR_MS = 3_600_000
SID = re.compile(r"[A-Za-z0-9_-]{5,160}\Z")
TICKER = "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"
KLINE_BASE = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime="

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pairs(
 event_id TEXT PRIMARY KEY, cutoff_ms INTEGER NOT NULL,
 reserved_ms INTEGER NOT NULL, news_hash TEXT NOT NULL,
 market_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(
 attempt_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES pairs(event_id),
 arm TEXT NOT NULL CHECK(arm IN ('news','news_market')),
 reserved_ms INTEGER NOT NULL, prompt_hash TEXT NOT NULL,
 UNIQUE(event_id,arm));
CREATE TABLE IF NOT EXISTS sessions(
 attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
 session_id TEXT NOT NULL, bound_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS prompts_sent(
 attempt_id TEXT PRIMARY KEY REFERENCES sessions(attempt_id),
 sent_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(
 attempt_id TEXT PRIMARY KEY REFERENCES prompts_sent(attempt_id),
 provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
 completed_ms INTEGER NOT NULL, output_hash TEXT NOT NULL,
 p_up REAL NOT NULL CHECK(p_up>=0 AND p_up<=1),
 expected_return_bps REAL NOT NULL, uncertainty REAL NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('LONG_PAPER','SHORT_PAPER','FLAT')));
CREATE TABLE IF NOT EXISTS entry_quotes(
 event_id TEXT PRIMARY KEY REFERENCES pairs(event_id),
 entry_ms INTEGER NOT NULL, price REAL NOT NULL CHECK(price>0),
 source TEXT NOT NULL CHECK(source='binance_spot_public'));
CREATE TABLE IF NOT EXISTS outcomes(
 event_id TEXT NOT NULL REFERENCES entry_quotes(event_id),
 horizon_h INTEGER NOT NULL CHECK(horizon_h IN (1,4,24)),
 exit_ms INTEGER NOT NULL, settled_ms INTEGER NOT NULL,
 price REAL NOT NULL CHECK(price>0), source TEXT NOT NULL,
 PRIMARY KEY(event_id,horizon_h));
"""


def clock():
    return time.time_ns() // 1_000_000


def _private_root(root):
    root = Path(root).expanduser().absolute()
    require(not root.is_symlink(), "unsafe_root_symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = root.stat()
    require(stat.S_ISDIR(mode.st_mode) and
            stat.S_IMODE(mode.st_mode) == 0o700 and
            mode.st_uid == os.geteuid(), "root_not_private")
    return root


@contextlib.contextmanager
def locked_db(root, *, create=False, now_ms=None):
    os.umask(0o077)
    root = _private_root(root) if create else Path(root).expanduser().absolute()
    require(root.is_dir() and not root.is_symlink(), "v2_root_absent")
    path = root / "v2.sqlite"
    require(not path.is_symlink(), "unsafe_v2_db")
    if not create:
        require(path.is_file(), "v2_not_initialized")
    lock = root / ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600 and
                os.fstat(fd).st_uid == os.geteuid(), "unsafe_lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.is_file():
            st = path.stat()
            require(st.st_uid == os.geteuid() and
                    stat.S_IMODE(st.st_mode) == 0o600, "unsafe_database")
        con = sqlite3.connect(path, timeout=5)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA synchronous=FULL")
            if create:
                con.executescript(SCHEMA)
                initial = con.execute("SELECT v FROM meta WHERE k='start_ms'").fetchone()
                if initial is None:
                    require(type(now_ms) is int and now_ms > 0,
                            "invalid_start")
                    con.executemany("INSERT INTO meta(k,v) VALUES(?,?)",
                                    (("start_ms", str(now_ms)),
                                     ("policy", POLICY_VERSION),
                                     ("contract_sha", CONTRACT_SHA),
                                     ("model_pair", "pinned_on_first_decision")))
                    con.commit()
            require(con.execute("SELECT v FROM meta WHERE k='policy'").fetchone()
                    == (POLICY_VERSION,) and
                    con.execute("SELECT v FROM meta WHERE k='contract_sha'").fetchone()
                    == (CONTRACT_SHA,), "v2_policy_mismatch")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _readmeta(db, key):
    row = db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    require(row is not None, "meta_missing")
    return row[0]


def _counts(db, now):
    day = now // 86_400_000 * 86_400_000
    return {
        "lifetime_calls": db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
        "today_calls": db.execute("SELECT COUNT(*) FROM attempts "
                                  "WHERE reserved_ms>=? AND reserved_ms<?",
                                  (day, day+86_400_000)).fetchone()[0],
        "pairs": db.execute("SELECT COUNT(*) FROM pairs").fetchone()[0],
        "abandoned": abandoned_count(db),
        "abandoned_early": early_count(db),
        "decisions": db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
        "entry_quotes": db.execute("SELECT COUNT(*) FROM entry_quotes").fetchone()[0],
        "labels": db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0],
    }


def _pending(db):
    # Before an explicitly authorized recovery migration, preserve old
    # conservative pending behavior. Never auto-initialize the recovery table.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='abandoned_pairs' "
                      "AND type='table'").fetchone():
        return db.execute(
            "SELECT COUNT(*) FROM pairs p WHERE NOT EXISTS "
            "(SELECT 1 FROM entry_quotes q WHERE q.event_id=p.event_id)"
        ).fetchone()[0]
    if early_present(db):
        return db.execute("""
            SELECT COUNT(*) FROM pairs p
            WHERE NOT EXISTS (SELECT 1 FROM entry_quotes q
                              WHERE q.event_id=p.event_id)
              AND NOT EXISTS (SELECT 1 FROM abandoned_pairs a
                              WHERE a.event_id=p.event_id)
              AND NOT EXISTS (SELECT 1 FROM abandoned_early_pairs x
                              WHERE x.event_id=p.event_id)
        """).fetchone()[0]
    return db.execute("""
        SELECT COUNT(*) FROM pairs p
        WHERE NOT EXISTS (SELECT 1 FROM entry_quotes q WHERE q.event_id=p.event_id)
          AND NOT EXISTS (SELECT 1 FROM abandoned_pairs a WHERE a.event_id=p.event_id)
    """).fetchone()[0]


def reserve(db, source_root, now, *, enabled=False, daily_cap=0):
    require(enabled is True, "v2_models_disabled")
    require(type(now) is int and now > 0, "invalid_clock")
    require(type(daily_cap) is int and 2 <= daily_cap <= MAX_DAILY
            and daily_cap % 2 == 0, "invalid_daily_cap")
    counters = _counts(db, now)
    if counters["lifetime_calls"] + 2 > MAX_LIFETIME or \
            counters["today_calls"] + 2 > daily_cap:
        return {"status": "skip", "reason": "v2_budget_exhausted"}
    if _pending(db):
        return {"status": "skip", "reason": "v2_pending_pair_requires_reconcile"}
    slot = now // SLOT_MS * SLOT_MS
    if db.execute("SELECT 1 FROM pairs WHERE reserved_ms>=? AND "
                  "reserved_ms<? LIMIT 1", (slot, slot+SLOT_MS)).fetchone():
        return {"status": "skip", "reason": "v2_slot_already_claimed"}
    start = int(_readmeta(db, "start_ms"))
    entries = candidates(source_root, now, start)
    done = {item[0] for item in db.execute("SELECT event_id FROM pairs")}
    entries = [e for e in entries if e["event_id"] not in done]
    if not entries:
        return {"status": "skip", "reason": "v2_no_new_forward_event"}
    entry = entries[0]
    eid = entry["event_id"]
    texts = {arm: prompt(eid, entry["cutoff_ms"], entry["news"],
                         entry["market"], arm) for arm in ARMS}
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("INSERT INTO pairs VALUES(?,?,?,?,?)",
                   (eid,entry["cutoff_ms"],now,entry["news_hash"],
                    entry["market_hash"]))
        arms = []
        for arm in ARMS:
            aid = hashlib.sha256(
                ("p4v2:"+eid+":"+arm).encode("ascii")).hexdigest()
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)",
                       (aid,eid,arm,now,hashlib.sha256(
                           texts[arm].encode()).hexdigest()))
            arms.append({"arm":arm,"attempt_id":aid,"prompt":texts[arm]})
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"status":"ready","event_id":eid,"arms":arms}


def bind_session(db, aid, sid, now):
    require(type(aid) is str and bool(ID.fullmatch(aid)) and
            type(sid) is str and bool(SID.fullmatch(sid)), "invalid_session")
    require(db.execute("SELECT 1 FROM attempts WHERE attempt_id=?",
                       (aid,)).fetchone(), "unreserved_attempt")
    require(not db.execute("""
        SELECT 1 FROM attempts a JOIN abandoned_early_pairs x USING(event_id)
        WHERE a.attempt_id=?""",(aid,)).fetchone()
        if early_present(db) else True, "early_pair_cannot_bind_session")
    db.execute("INSERT INTO sessions VALUES(?,?,?)", (aid,sid,now))
    db.commit()


def mark_prompt(db, aid, now):
    require(db.execute("SELECT 1 FROM sessions WHERE attempt_id=?",
                       (aid,)).fetchone(), "session_missing")
    require(not db.execute("""
        SELECT 1 FROM attempts a JOIN abandoned_early_pairs x USING(event_id)
        WHERE a.attempt_id=?""",(aid,)).fetchone()
        if early_present(db) else True, "early_pair_cannot_send_prompt")
    db.execute("INSERT INTO prompts_sent VALUES(?,?)", (aid,now))
    db.commit()


def finish(db, aid, payload, now):
    require(type(payload) is dict and
            set(payload) == {"session_id","response","model"},
            "invalid_model_envelope")
    row = db.execute("""SELECT s.session_id,a.event_id,a.arm,a.reserved_ms
        FROM sessions s JOIN attempts a ON a.attempt_id=s.attempt_id
        JOIN prompts_sent p ON p.attempt_id=s.attempt_id
        WHERE s.attempt_id=?""",(aid,)).fetchone()
    require(row is not None and row[0] == payload["session_id"],
            "session_binding_mismatch")
    require(not db.execute("SELECT 1 FROM decisions WHERE attempt_id=?",
                           (aid,)).fetchone(), "decision_already_completed")
    require(not is_abandoned(db,row[1]) and
            not is_early_abandoned(db,row[1]), "cannot_finalize_abandoned_pair")
    model = payload["model"]
    require(type(model) is dict and set(model) == {"providerID","modelID"}
            and all(type(model[x]) is str and bool(MODEL.fullmatch(model[x]))
                    for x in ("providerID","modelID")),
            "unverified_model_identity")
    prior = db.execute("SELECT DISTINCT provider_id,model_id FROM decisions").fetchall()
    require(not prior or all(x == (model["providerID"],model["modelID"])
                             for x in prior), "v2_cohort_model_conflict")
    pred = decode_prediction(payload["response"])
    db.execute("""INSERT INTO decisions
        VALUES(?,?,?,?,?,?,?,?,?)""",(
        aid,model["providerID"],model["modelID"],now,
        hashlib.sha256(payload["response"].encode()).hexdigest(),
        pred["p_up"],pred["expected_return_bps"],pred["uncertainty"],
        decision(pred["p_up"],pred["expected_return_bps"])))
    db.commit()
    return {"status":"completed","action":
            decision(pred["p_up"],pred["expected_return_bps"])}


def _get_public(url, maxbytes=5000, timeout=12):
    require(url == TICKER or
            (url.startswith(KLINE_BASE) and
             re.fullmatch(r"\d{13}&limit=1",url[len(KLINE_BASE):])),
            "unapproved_public_endpoint")
    started = clock()
    with urlopen(Request(url,headers={"User-Agent":"NEWS-v2-paper-research/0.1"}),
                 timeout=timeout) as response:
        require(response.status == 200 and response.geturl() == url,
                "public_redirect_rejected")
        raw = response.read(maxbytes+1)
    observed = clock()
    require(0 < len(raw) <= maxbytes and
            started <= observed <= started + 20_000, "public_capture_invalid")
    return started, observed, json.loads(raw)


def quote(db, eid, fetch=_get_public):
    require(type(eid) is str and bool(ID.fullmatch(eid)), "invalid_event")
    require(not is_abandoned(db,eid) and not is_early_abandoned(db,eid),
            "cannot_quote_abandoned_pair")
    count,last = db.execute("""
        SELECT COUNT(*),MAX(d.completed_ms) FROM decisions d
        JOIN attempts a USING(attempt_id) WHERE a.event_id=?""",(eid,)).fetchone()
    require(count == 2, "quote_requires_paired_decisions")
    require(not db.execute("SELECT 1 FROM entry_quotes WHERE event_id=?",
                           (eid,)).fetchone(), "quote_already_recorded")
    started,observed,price = fetch(TICKER)
    require(started >= last and type(price) is dict and
            price.get("symbol") == "BTCUSDT" and
            type(price.get("price")) is str, "quote_not_post_inference")
    val = float(price["price"])
    require(math.isfinite(val) and 1000 < val < 10_000_000,
            "invalid_quote_price")
    db.execute("INSERT INTO entry_quotes VALUES(?,?,?,?)",
               (eid,observed,val,"binance_spot_public"))
    db.commit()
    return {"status":"entry_recorded","event_id_prefix":eid[:12]}


def label_once(db, now, fetch=_get_public):
    require(type(now) is int and now > 0, "invalid_clock")
    due = db.execute("""
        SELECT q.event_id,q.entry_ms,h.h FROM entry_quotes q
        CROSS JOIN (SELECT 1 AS h UNION ALL SELECT 4 UNION ALL SELECT 24) h
        LEFT JOIN outcomes o ON o.event_id=q.event_id AND o.horizon_h=h.h
        WHERE o.event_id IS NULL AND q.entry_ms+h.h*3600000+120000<=?
        ORDER BY (q.entry_ms+h.h*3600000) ASC LIMIT 3
    """,(now,)).fetchall()
    completed = 0
    for eid,entry,h in due:
        target = ((entry+h*HOUR_MS+59_999)//60_000)*60_000
        url = KLINE_BASE + str(target) + "&limit=1"
        started,observed,rows = fetch(url)
        require(started >= entry+h*HOUR_MS+120_000,
                "label_before_maturity")
        require(type(rows) is list and len(rows) == 1 and
                type(rows[0]) is list and len(rows[0]) >= 7,
                "invalid_label_payload")
        k = rows[0]
        require(type(k[0]) is int and type(k[6]) is int and
                k[0] == target and k[6] == target+59_999 and
                k[6] <= observed, "label_not_closed")
        price = float(k[4])
        require(math.isfinite(price) and 1000 < price < 10_000_000,
                "label_price_invalid")
        db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",
                   (eid,h,k[6],observed,price,"binance_public_closed_1m"))
        db.commit()
        completed += 1
    return {"status":"ok","created":completed}


def study_evaluation(db):
    data = db.execute("""
        SELECT q.event_id,q.entry_ms,q.price,o.exit_ms,o.price
        FROM entry_quotes q JOIN outcomes o USING(event_id)
        WHERE o.horizon_h=? ORDER BY q.entry_ms""",(HORIZON_H,)).fetchall()
    rows = []
    for eid,entry,price,exit_ms,exit_price in data:
        arms = {}
        q = db.execute("""
            SELECT a.arm,d.p_up,d.expected_return_bps,d.uncertainty,d.action
            FROM attempts a JOIN decisions d USING(attempt_id)
            WHERE a.event_id=?""",(eid,)).fetchall()
        for arm,p,ret,unc,action in q:
            arms[arm] = {"p_up":p,"expected_return_bps":ret,
                         "uncertainty":unc,"action":action}
        if len(arms) == 2:
            rows.append({"event_id":eid,"entry_ms":entry,
                         "entry_price":price,"exit_ms":exit_ms,
                         "exit_price":exit_price,"arms":arms})
    return evaluate(rows)


def readonly_status(root, now):
    path = Path(root).expanduser().absolute()/"v2.sqlite"
    if path.is_symlink() or not path.is_file():
        return {"status":"not_initialized"}
    db = sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        require(_readmeta(db,"policy")==POLICY_VERSION and
                _readmeta(db,"contract_sha")==CONTRACT_SHA,"policy_mismatch")
        return {"status":"ok","policy":POLICY_VERSION,
                "counts":_counts(db,now),
                "unquoted_pairs":_pending(db),
                "evaluation":study_evaluation(db)}
    finally:
        db.close()


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("command",choices=("init","prepare","session","prompt",
                  "finish","quote","labels","status","evaluate",
                  "recovery-schema","abandon",
                  "early-recovery-schema","abandon-early"))
    a.add_argument("--root",type=Path,default=ROOT)
    a.add_argument("--source-root",type=Path,default=SOURCE)
    a.add_argument("--enable-init",action="store_true")
    a.add_argument("--enable-models",action="store_true")
    a.add_argument("--enable-public-quote",action="store_true")
    a.add_argument("--enable-public-labels",action="store_true")
    a.add_argument("--enable-recovery-migration",action="store_true")
    a.add_argument("--enable-abandon",action="store_true")
    a.add_argument("--enable-early-recovery-migration",action="store_true")
    a.add_argument("--enable-abandon-early",action="store_true")
    a.add_argument("--daily-cap",type=int,default=0)
    a.add_argument("--attempt-id")
    a.add_argument("--session-id")
    a.add_argument("--event-id")
    arg = a.parse_args(argv)
    try:
        now = clock()
        if arg.command in ("status","evaluate"):
            result = readonly_status(arg.root,now)
            if arg.command == "evaluate":
                result = result.get("evaluation") if result["status"]=="ok" \
                         else result
        else:
            require(arg.command != "init" or arg.enable_init,
                    "init_requires_opt_in")
            with locked_db(arg.root,create=(arg.command=="init"),
                           now_ms=now) as db:
                if arg.command=="init":
                    result={"status":"initialized","policy":POLICY_VERSION}
                elif arg.command=="recovery-schema":
                    result=create_recovery_schema(
                        db,enabled=arg.enable_recovery_migration)
                elif arg.command=="abandon":
                    result=abandon(db,arg.event_id,now,
                                   enabled=arg.enable_abandon)
                elif arg.command=="early-recovery-schema":
                    result=create_early_schema(
                        db,enabled=arg.enable_early_recovery_migration)
                elif arg.command=="abandon-early":
                    result=abandon_early(db,arg.event_id,now,
                                         enabled=arg.enable_abandon_early)
                elif arg.command=="prepare":
                    result=reserve(db,arg.source_root,now,
                                   enabled=arg.enable_models,
                                   daily_cap=arg.daily_cap)
                elif arg.command=="session":
                    bind_session(db,arg.attempt_id,arg.session_id,now)
                    result={"status":"session_bound"}
                elif arg.command=="prompt":
                    mark_prompt(db,arg.attempt_id,now)
                    result={"status":"prompt_marked"}
                elif arg.command=="finish":
                    raw=sys.stdin.buffer.read(8193)
                    require(len(raw)<=8192,"model_envelope_too_big")
                    result=finish(db,arg.attempt_id,json.loads(raw),now)
                elif arg.command=="quote":
                    require(arg.enable_public_quote,"public_quote_disabled")
                    result=quote(db,arg.event_id)
                else:
                    require(arg.enable_public_labels,"labels_disabled")
                    result=label_once(db,now)
        print(canonical(result))
        return 0
    except (V2Error,OSError,sqlite3.Error,ValueError,TypeError,
            OverflowError,TimeoutError,BlockingIOError):
        # No raw prompt, URL, news, response or credential in error output.
        print(canonical({"status":"error","code":"v2_fail_closed"}),
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
