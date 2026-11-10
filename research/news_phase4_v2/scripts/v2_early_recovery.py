"""Strict, explicitly authorized early-arm abandonment for Phase4-v2.

Extends, never rewrites, the existing abandoned_pairs ledger. This module
must NOT be called automatically on a timeout or an SDK exception.
Only a human-reviewed stalled pair with EXACT (2 attempts, 1 news session,
1 news prompt, 0 decisions, no quote/outcome) is eligible. No model replay.
"""
from v2_core import require, ID
from v2_recovery import present as old_present, is_abandoned

EARLY_REASON = "first_arm_unconfirmed_after_prompt_attempted"
MIN_AGE_MS = 30 * 60_000

EARLY_DDL = (
    """CREATE TABLE abandoned_early_pairs(
      event_id TEXT PRIMARY KEY REFERENCES pairs(event_id),
      abandoned_ms INTEGER NOT NULL CHECK(abandoned_ms>0),
      reason TEXT NOT NULL CHECK(reason='first_arm_unconfirmed_after_prompt_attempted'),
      attempts_at_abandon INTEGER NOT NULL CHECK(attempts_at_abandon=2),
      sessions_at_abandon INTEGER NOT NULL CHECK(sessions_at_abandon=1),
      prompts_at_abandon INTEGER NOT NULL CHECK(prompts_at_abandon=1),
      decisions_at_abandon INTEGER NOT NULL CHECK(decisions_at_abandon=0)
    )""",
    """CREATE TRIGGER v2_early_append_only_update
       BEFORE UPDATE ON abandoned_early_pairs
       BEGIN SELECT RAISE(ABORT,'append_only'); END""",
    """CREATE TRIGGER v2_early_append_only_delete
       BEFORE DELETE ON abandoned_early_pairs
       BEGIN SELECT RAISE(ABORT,'append_only'); END""",
    """CREATE TRIGGER v2_early_block_sessions
       BEFORE INSERT ON sessions
       WHEN EXISTS (SELECT 1 FROM attempts a
         JOIN abandoned_early_pairs x ON x.event_id=a.event_id
         WHERE a.attempt_id=NEW.attempt_id)
       BEGIN SELECT RAISE(ABORT,'abandoned_early_pair'); END""",
    """CREATE TRIGGER v2_early_block_prompt
       BEFORE INSERT ON prompts_sent
       WHEN EXISTS (SELECT 1 FROM attempts a
         JOIN abandoned_early_pairs x ON x.event_id=a.event_id
         WHERE a.attempt_id=NEW.attempt_id)
       BEGIN SELECT RAISE(ABORT,'abandoned_early_pair'); END""",
    """CREATE TRIGGER v2_early_block_decision
       BEFORE INSERT ON decisions
       WHEN EXISTS (SELECT 1 FROM attempts a
         JOIN abandoned_early_pairs x ON x.event_id=a.event_id
         WHERE a.attempt_id=NEW.attempt_id)
       BEGIN SELECT RAISE(ABORT,'abandoned_early_pair'); END""",
    """CREATE TRIGGER v2_early_block_quote
       BEFORE INSERT ON entry_quotes
       WHEN EXISTS (SELECT 1 FROM abandoned_early_pairs x
         WHERE x.event_id=NEW.event_id)
       BEGIN SELECT RAISE(ABORT,'abandoned_early_pair'); END""",
    """CREATE TRIGGER v2_early_block_attempt
       BEFORE INSERT ON attempts
       WHEN EXISTS (SELECT 1 FROM abandoned_early_pairs x
         WHERE x.event_id=NEW.event_id)
       BEGIN SELECT RAISE(ABORT,'abandoned_early_pair'); END""",
    """CREATE TRIGGER v2_early_require_exact_shape
       BEFORE INSERT ON abandoned_early_pairs BEGIN
       SELECT CASE WHEN
        NOT EXISTS (SELECT 1 FROM pairs p
            WHERE p.event_id=NEW.event_id
              AND p.reserved_ms <= NEW.abandoned_ms
              AND NEW.abandoned_ms-p.reserved_ms >= 1800000)
        OR EXISTS (SELECT 1 FROM abandoned_pairs old
            WHERE old.event_id=NEW.event_id)
        OR (SELECT COUNT(*) FROM attempts a
            WHERE a.event_id=NEW.event_id)!=2
        OR (SELECT COUNT(*) FROM attempts a JOIN sessions s USING(attempt_id)
            WHERE a.event_id=NEW.event_id)!=1
        OR (SELECT COUNT(*) FROM attempts a JOIN prompts_sent s USING(attempt_id)
            WHERE a.event_id=NEW.event_id)!=1
        OR (SELECT COUNT(*) FROM attempts a JOIN sessions s USING(attempt_id)
            WHERE a.event_id=NEW.event_id AND a.arm='news')!=1
        OR (SELECT COUNT(*) FROM attempts a JOIN prompts_sent s USING(attempt_id)
            WHERE a.event_id=NEW.event_id AND a.arm='news')!=1
        OR (SELECT COUNT(*) FROM attempts a JOIN decisions d USING(attempt_id)
            WHERE a.event_id=NEW.event_id)!=0
        OR EXISTS (SELECT 1 FROM entry_quotes q
            WHERE q.event_id=NEW.event_id)
        OR EXISTS (SELECT 1 FROM outcomes o
            WHERE o.event_id=NEW.event_id)
       THEN RAISE(ABORT,'early_abandon_shape_mismatch') END;
       END"""
)


def present(db):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='abandoned_early_pairs'").fetchone() is not None


def count(db):
    return (db.execute("SELECT COUNT(*) FROM abandoned_early_pairs")
            .fetchone()[0]) if present(db) else 0


def is_early_abandoned(db, event_id):
    return (present(db) and db.execute(
        "SELECT 1 FROM abandoned_early_pairs WHERE event_id=?",
        (event_id,)).fetchone() is not None)


def create_early_schema(db, *, enabled=False):
    require(enabled is True, "early_schema_opt_in_required")
    require(db.execute("PRAGMA foreign_keys").fetchone() == (1,),
            "early_schema_requires_fk")
    require(old_present(db), "legacy_recovery_schema_missing")
    require(not present(db), "early_schema_already_installed")
    db.execute("BEGIN IMMEDIATE")
    try:
        for sql in EARLY_DDL:
            db.execute(sql)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"status":"early_recovery_schema_installed",
            "abandoned_early_pairs":0}


def abandon_early(db, event_id, now_ms, *, enabled=False):
    require(enabled is True, "abandon_early_opt_in_required")
    require(type(event_id) is str and bool(ID.fullmatch(event_id)) and
            type(now_ms) is int and now_ms > 0, "invalid_early_abandon_request")
    require(present(db), "early_schema_not_installed")
    require(not is_abandoned(db,event_id) and
            not is_early_abandoned(db,event_id), "already_abandoned")
    row=db.execute("""
      SELECT p.reserved_ms,
       (SELECT COUNT(*) FROM attempts a WHERE a.event_id=p.event_id),
       (SELECT COUNT(*) FROM attempts a JOIN sessions s USING(attempt_id)
        WHERE a.event_id=p.event_id),
       (SELECT COUNT(*) FROM attempts a JOIN prompts_sent s USING(attempt_id)
        WHERE a.event_id=p.event_id),
       (SELECT COUNT(*) FROM attempts a JOIN decisions d USING(attempt_id)
        WHERE a.event_id=p.event_id),
       (SELECT COUNT(*) FROM attempts a JOIN sessions s USING(attempt_id)
        WHERE a.event_id=p.event_id AND a.arm='news'),
       (SELECT COUNT(*) FROM attempts a JOIN prompts_sent s USING(attempt_id)
        WHERE a.event_id=p.event_id AND a.arm='news'),
       (SELECT COUNT(*) FROM entry_quotes q WHERE q.event_id=p.event_id),
       (SELECT COUNT(*) FROM outcomes o WHERE o.event_id=p.event_id)
      FROM pairs p WHERE p.event_id=?""",(event_id,)).fetchone()
    require(row is not None,"pair_not_found")
    reserved,attempts,sessions,prompts,decisions,n_s,n_p,quotes,outcomes=row
    require(0<reserved<=now_ms-MIN_AGE_MS and
            (attempts,sessions,prompts,decisions,n_s,n_p,quotes,outcomes)
            == (2,1,1,0,1,1,0,0), "pair_not_confirmed_early_failure")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("INSERT INTO abandoned_early_pairs VALUES(?,?,?,?,?,?,?)",
                   (event_id,now_ms,EARLY_REASON,2,1,1,0))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"status":"early_pair_abandoned_without_replay",
            "event_id_prefix":event_id[:12],"attempts_retained":2,
            "sessions_retained":1,"prompts_retained":1,
            "decisions_retained":0}
