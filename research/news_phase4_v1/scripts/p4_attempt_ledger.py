"""Offline testable attempt-reservation and recovery ledger for Phase 4.

Takes a caller-owned EXISTING sqlite3 connection; never opens a file or invokes
MultiTool. Default model_calls_enabled=False rejects reservations. No retry or
reset of unknown attempts; a prompt_attempted marker is committed BEFORE any
external prompt send by the future adapter. The adapter MUST persist gateway
identity and independently verify every returned model before finalization.
"""
import hashlib
import re
import sqlite3

from p4_collector_schema import ARMS

HEX = re.compile(r'[0-9a-f]{64}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9_.-]{2,100}\Z')
SESSION = re.compile(r'[A-Za-z0-9_-]{5,160}\Z')
DAY_MS = 86_400_000
ALLOWED = {
    'reserved': frozenset(('session_created', 'failed', 'unknown')),
    'session_created': frozenset(('prompt_attempted', 'failed', 'unknown')),
    'prompt_attempted': frozenset(('completed', 'failed', 'unknown')),
}
MAX_CAP = 30


class LedgerError(ValueError):
    pass


def _ok(condition, code):
    if not condition:
        raise LedgerError(code)


def _stamp(v):
    return type(v) is int and v > 0


def _hex(v):
    return isinstance(v, str) and HEX.fullmatch(v) is not None


def _atomic(db, func):
    _ok(isinstance(db, sqlite3.Connection), 'connection_required')
    _ok(not db.in_transaction, 'caller_transaction_active')
    db.execute('BEGIN IMMEDIATE')
    try:
        value = func()
        db.commit()
        return value
    except BaseException:
        db.rollback()
        raise


def status(db, attempt_id):
    """Current append-only status; None for non-existent attempt."""
    _ok(isinstance(db, sqlite3.Connection), 'connection_required')
    row = db.execute(
        'SELECT s.status,s.ts_ms FROM attempt_status_events s '
        'WHERE s.attempt_id=? ORDER BY s.ordinal DESC LIMIT 1',
        (attempt_id,)).fetchone()
    return row


def _advance(db, attempt_id, next_status, now_ms):
    last = status(db, attempt_id)
    _ok(last is not None, 'attempt_missing')
    _ok(next_status in ALLOWED.get(last[0], ()), 'terminal_or_illegal_transition')
    _ok(_stamp(now_ms) and now_ms >= last[1], 'non_monotone_attempt_time')
    db.execute('INSERT INTO attempt_status_events(attempt_id,status,ts_ms) VALUES(?,?,?)',
               (attempt_id, next_status, now_ms))


def reserve(db, *, event_id, arm, prompt_hash, now_ms,
            model_calls_enabled=False, daily_call_cap=0):
    """Atomically reserve one irreversible call slot, no model call performed.

    prompt_hash must refer to exact final rendered prompt bytes, NOT merely
    frozen feature bytes. No implicit daily allowance from Phase 3.
    """
    _ok(model_calls_enabled is True, 'model_calls_disabled')
    _ok(type(daily_call_cap) is int and 1 <= daily_call_cap <= MAX_CAP,
        'invalid_daily_budget')
    _ok(_hex(event_id) and _hex(prompt_hash) and arm in ARMS and _stamp(now_ms),
        'invalid_reservation')
    aid = hashlib.sha256((event_id + ':' + arm).encode('ascii')).hexdigest()
    def write():
        begin = (now_ms // DAY_MS) * DAY_MS
        count = db.execute('SELECT COUNT(*) FROM attempt_reservations '
                           'WHERE reserved_ms>=? AND reserved_ms<?',
                           (begin, begin + DAY_MS)).fetchone()[0]
        _ok(count < daily_call_cap, 'daily_budget_exhausted')
        row = db.execute('SELECT cutoff_ms FROM events WHERE event_id=?',
                         (event_id,)).fetchone()
        _ok(row is not None and now_ms >= row[0], 'event_not_frozen')
        db.execute('INSERT INTO attempt_reservations VALUES (?,?,?,?,?)',
                   (aid, event_id, arm, now_ms, prompt_hash))
        db.execute('INSERT INTO attempt_status_events(attempt_id,status,ts_ms) '
                   'VALUES(?,?,?)', (aid, 'reserved', now_ms))
        return aid
    return _atomic(db, write)


def bind_session(db, attempt_id, session_id, now_ms):
    """Durably bind session_id before allowing any prompt send."""
    _ok(_hex(attempt_id) and isinstance(session_id, str) and
        SESSION.fullmatch(session_id) is not None and _stamp(now_ms),
        'invalid_session_binding')
    def write():
        last = status(db, attempt_id)
        _ok(last is not None and last[0] == 'reserved', 'session_not_reservable')
        _ok(now_ms >= last[1], 'session_before_reservation')
        db.execute('INSERT INTO attempt_sessions VALUES (?,?,?)',
                   (attempt_id, session_id, now_ms))
        _advance(db, attempt_id, 'session_created', now_ms)
    return _atomic(db, write)


def mark_prompt_attempted(db, attempt_id, now_ms):
    """Call BEFORE invoking session.prompt; never repeat the unknown call."""
    _ok(_hex(attempt_id), 'invalid_attempt_id')
    return _atomic(db, lambda: _advance(db, attempt_id, 'prompt_attempted', now_ms))


def terminal(db, attempt_id, state, now_ms):
    """Record 'failed' or 'unknown'. Both terminal, no automatic retries."""
    _ok(_hex(attempt_id) and state in ('failed', 'unknown'), 'invalid_terminal')
    return _atomic(db, lambda: _advance(db, attempt_id, state, now_ms))


def complete(db, *, attempt_id, now_ms, direction, confidence,
             provider_id, model_id, response_hash):
    """Commit verified decision and 'completed' status atomically.

    Provider/model MUST be read from the actual gateway assistant-message
    metadata, not hardcoded or inferred from request settings.
    """
    import math
    _ok(_hex(attempt_id) and _stamp(now_ms) and
        direction in ('UP', 'DOWN', 'NEUTRAL') and
        type(confidence) in (int, float) and math.isfinite(confidence) and
        0 <= confidence <= 1 and
        isinstance(provider_id, str) and IDENTITY.fullmatch(provider_id) and
        isinstance(model_id, str) and IDENTITY.fullmatch(model_id) and
        _hex(response_hash), 'invalid_verified_decision')
    def write():
        row = db.execute('SELECT event_id,arm FROM attempt_reservations '
                         'WHERE attempt_id=?', (attempt_id,)).fetchone()
        _ok(row is not None, 'attempt_missing')
        last = status(db, attempt_id)
        _ok(last is not None and last[0] == 'prompt_attempted' and now_ms >= last[1],
            'decision_without_confirmed_prompt')
        db.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)',
                   (row[0], row[1], direction, confidence,
                    provider_id, model_id, now_ms, response_hash))
        _advance(db, attempt_id, 'completed', now_ms)
    return _atomic(db, write)
