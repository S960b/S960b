"""Explicit, append-only forensic abandonment of incomplete Phase4 v2 pairs.

Only a human-authorized recovery command may abandon a pair. Never replay
an ambiguous external model effect; never overwrite, delete or backfill
existing predictions. New migration does not change pinned prompt/policy.
"""
import sqlite3

from v2_core import V2Error, require, ID

REASON = "unconfirmed_second_arm_finalize"
RECOVERY_DDL = (
    """CREATE TABLE IF NOT EXISTS abandoned_pairs(
    event_id TEXT PRIMARY KEY REFERENCES pairs(event_id),
    abandoned_ms INTEGER NOT NULL CHECK(abandoned_ms>0),
    reason TEXT NOT NULL CHECK(reason='unconfirmed_second_arm_finalize'),
    attempts_at_abandon INTEGER NOT NULL CHECK(attempts_at_abandon=2),
    completed_at_abandon INTEGER NOT NULL CHECK(completed_at_abandon BETWEEN 0 AND 1)
    )""",
    """CREATE TRIGGER IF NOT EXISTS v2_no_update_abandoned
    BEFORE UPDATE ON abandoned_pairs BEGIN
    SELECT RAISE(ABORT,'append_only'); END""",
    """CREATE TRIGGER IF NOT EXISTS v2_no_delete_abandoned
    BEFORE DELETE ON abandoned_pairs BEGIN
    SELECT RAISE(ABORT,'append_only'); END""",
    """CREATE TRIGGER IF NOT EXISTS v2_no_finalize_abandoned
    BEFORE INSERT ON decisions BEGIN
    SELECT CASE WHEN EXISTS(
      SELECT 1 FROM attempts a JOIN abandoned_pairs p USING(event_id)
      WHERE a.attempt_id=NEW.attempt_id
    ) THEN RAISE(ABORT,'abandoned_pair') END; END""",
    """CREATE TRIGGER IF NOT EXISTS v2_no_quote_abandoned
    BEFORE INSERT ON entry_quotes BEGIN
    SELECT CASE WHEN EXISTS(
      SELECT 1 FROM abandoned_pairs p WHERE p.event_id=NEW.event_id
    ) THEN RAISE(ABORT,'abandoned_pair') END; END""",
    """CREATE TRIGGER IF NOT EXISTS v2_abandon_only_incomplete
    BEFORE INSERT ON abandoned_pairs BEGIN
    SELECT CASE WHEN
      (SELECT COUNT(*) FROM attempts WHERE event_id=NEW.event_id)!=2 OR
      (SELECT COUNT(*) FROM attempts a JOIN prompts_sent p USING(attempt_id)
       WHERE a.event_id=NEW.event_id)!=2 OR
      (SELECT COUNT(*) FROM attempts a JOIN decisions d USING(attempt_id)
       WHERE a.event_id=NEW.event_id)>=2 OR
      EXISTS(SELECT 1 FROM entry_quotes WHERE event_id=NEW.event_id)
    THEN RAISE(ABORT,'cannot_abandon_completed_or_unprompted_pair') END;
    END"""
)


def present(db):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='abandoned_pairs'").fetchone() is not None


def count(db):
    return db.execute("SELECT COUNT(*) FROM abandoned_pairs").fetchone()[0] \
        if present(db) else 0


def is_abandoned(db, event_id):
    if not present(db):
        return False
    return db.execute(
        "SELECT 1 FROM abandoned_pairs WHERE event_id=?",
        (event_id,)).fetchone() is not None


def create_recovery_schema(db, *, enabled=False):
    require(enabled is True, "recovery_schema_opt_in_required")
    require(db.execute("PRAGMA foreign_keys").fetchone()[0]==1,
            "recovery_requires_foreign_keys")
    require(db.execute("SELECT v FROM meta WHERE k='policy'").fetchone()
            is not None, "recovery_wrong_database")
    require(not present(db), "recovery_schema_already_present")
    db.execute("BEGIN IMMEDIATE")
    try:
        for sql in RECOVERY_DDL:
            db.execute(sql)
        db.commit()
        return {"status":"recovery_schema_installed","abandoned_pairs":0}
    except BaseException:
        db.rollback()
        raise


def abandon(db, event_id, now_ms, *, enabled=False):
    require(enabled is True, "abandon_opt_in_required")
    require(type(event_id) is str and bool(ID.fullmatch(event_id))
            and type(now_ms) is int and now_ms>0,
            "invalid_abandon_request")
    require(present(db), "recovery_schema_not_initialized")
    require(not is_abandoned(db,event_id), "already_abandoned")
    row=db.execute("""
        SELECT p.reserved_ms,
        (SELECT COUNT(*) FROM attempts a WHERE a.event_id=p.event_id),
        (SELECT COUNT(*) FROM attempts a JOIN sessions s USING(attempt_id)
          WHERE a.event_id=p.event_id),
        (SELECT COUNT(*) FROM attempts a JOIN prompts_sent s USING(attempt_id)
          WHERE a.event_id=p.event_id),
        (SELECT COUNT(*) FROM attempts a JOIN decisions d USING(attempt_id)
          WHERE a.event_id=p.event_id),
        (SELECT COUNT(*) FROM entry_quotes q WHERE q.event_id=p.event_id)
        FROM pairs p WHERE p.event_id=?""",(event_id,)).fetchone()
    require(row is not None,"pair_not_found")
    reserved,attempts,sessions,sent,completed,quotes=row
    require(0<reserved<=now_ms and attempts==2 and
            sessions==2 and sent==2 and completed==1 and quotes==0,
            "pair_not_confirmed_incomplete")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("INSERT INTO abandoned_pairs VALUES(?,?,?,?,?)",
                   (event_id,now_ms,REASON,attempts,completed))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"status":"abandoned_without_replay",
            "event_id_prefix":event_id[:12],
            "attempts_remain":attempts,
            "completed_decisions_remain":completed}
