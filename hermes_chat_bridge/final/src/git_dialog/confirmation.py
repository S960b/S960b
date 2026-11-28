"""Offline confirmation state machine; NOT authentication or a web endpoint.

The future ingress MUST authenticate a real user, check CSRF/origin, and obtain
principal from that session, never from Git/message/request-body fields.
This module consumes a nonce once, then ONLY prepares a terminal intent. No
remote writes/deletion, automatic replay or UI/service installation.
"""
from contextlib import contextmanager
import hashlib
import hmac
import secrets
import sqlite3
import time

from . import DialogError, validate_task_id
from .github import sha
from .journal_probe import _db_file

APPLICATION_ID = 0x47444346


def _clock(now):
    now = int(time.time()) if now is None else now
    if type(now) is not int or now < 0:
        raise DialogError("invalid_confirmation_time")
    return now


def _digest(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 128 or any(ord(c) < 33 for c in text):
        raise DialogError("invalid_confirmation_value")
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise DialogError("invalid_confirmation_value") from None


class ConfirmationStore:
    """Own NEW DB, not registry/outbox/journal. Pending does not stop workers."""
    def __init__(self, path):
        from pathlib import Path
        path = Path(path)
        path = _db_file(path, create=not (path.exists() or path.is_symlink()))
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA synchronous=FULL")
            with self._tx():
                app = self.conn.execute("PRAGMA application_id").fetchone()[0]
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if app not in (0, APPLICATION_ID) or (app == 0 and tables):
                    raise DialogError("wrong_confirmation_database")
                if app == APPLICATION_ID and tables != {"confirmations"}:
                    raise DialogError("confirmation_schema_mismatch")
                self.conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                self.conn.execute("""CREATE TABLE IF NOT EXISTS confirmations (
                    task_id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, nonce_hash TEXT NOT NULL,
                    expected_head TEXT NOT NULL, actor TEXT NOT NULL, message_id TEXT NOT NULL,
                    issued INTEGER NOT NULL, expires INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','cancelled','consumed'))
                )""")
        except BaseException:
            self.conn.close()
            raise

    @contextmanager
    def _tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    def _row(self, task_id):
        validate_task_id(task_id)
        row = self.conn.execute("SELECT * FROM confirmations WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise DialogError("confirmation_not_pending")
        return dict(row)

    def _check(self, task_id, principal, nonce, now):
        row = self._row(task_id)
        if (row["state"] != "pending" or not hmac.compare_digest(row["owner_hash"], _digest(principal)) or
                not hmac.compare_digest(row["nonce_hash"], _digest(nonce))):
            raise DialogError("confirmation_denied")
        if not row["issued"] <= now < row["expires"]:
            raise DialogError("confirmation_expired")
        return row

    def issue(self, task_id, principal, expected_head, actor, *, now=None, ttl=300):
        validate_task_id(task_id); sha(expected_head)
        now = _clock(now)
        if type(ttl) is not int or not 1 <= ttl <= 600 or actor not in ("chatgpt", "mt"):
            raise DialogError("invalid_confirmation_request")
        nonce = secrets.token_urlsafe(32)
        owner, digest = _digest(principal), _digest(nonce)
        with self._tx():
            existing = self.conn.execute("SELECT state,owner_hash FROM confirmations WHERE task_id=?", (task_id,)).fetchone()
            if existing is not None and existing["state"] == "consumed":
                raise DialogError("confirmation_already_consumed")
            if existing is not None and not hmac.compare_digest(existing["owner_hash"], owner):
                raise DialogError("confirmation_owner_denied")
            self.conn.execute("INSERT OR REPLACE INTO confirmations VALUES (?,?,?,?,?,?,?,?,?)",
                (task_id, owner, digest, expected_head, actor, "close_" + digest[:24], now, now + ttl, "pending"))
        return {"task_id": task_id, "confirmation": nonce, "expected_head": expected_head,
                "expires_at": now + ttl, "warning": "terminal_close_not_undoable_not_git_object_erasure"}

    def consume(self, task_id, principal, nonce, observed_head, *, now=None):
        sha(observed_head)
        with self._tx():
            row = self._check(task_id, principal, nonce, _clock(now))
            if row["expected_head"] != observed_head:
                raise DialogError("confirmation_head_changed")
            self.conn.execute("UPDATE confirmations SET state='consumed' WHERE task_id=?", (task_id,))
        # Durable consume BEFORE crossing into the separate closing outbox.
        return {**row, "state": "consumed"}

    def cancel(self, task_id, principal, nonce, *, now=None):
        with self._tx():
            self._check(task_id, principal, nonce, _clock(now))
            self.conn.execute("UPDATE confirmations SET state='cancelled' WHERE task_id=?", (task_id,))

    def status(self, task_id):
        row = self._row(task_id)
        return {k: row[k] for k in ("task_id", "expected_head", "expires", "state")}


class ConfirmationController:
    """Bound owner from trusted ingress config, NOT proof of user identity.

    A crash after consume requires manual read-only diagnosis; never replay
    confirm automatically, even if there was no visible effect. No publish API.
    """
    def __init__(self, coordinator, store, *, owner):
        self._owner = _digest(owner)
        self.coordinator, self.store = coordinator, store
        self.task_id = coordinator.task_id

    def _owner_check(self, principal):
        if not hmac.compare_digest(self._owner, _digest(principal)):
            raise DialogError("confirmation_owner_denied")

    def request(self, principal, expected_head, *, now=None, ttl=300):
        self._owner_check(principal)
        sha(expected_head)
        if self.coordinator.registry.get(self.task_id)["state"] != "active":
            raise DialogError("dialog_not_writable")
        snapshot = self.coordinator.dialog.load(self.task_id)
        if snapshot.head != expected_head or snapshot.state.status in ("closing", "done"):
            raise DialogError("confirmation_head_changed")
        return self.store.issue(self.task_id, principal, expected_head, snapshot.state.next_actor,
                                now=now, ttl=ttl)

    def cancel(self, principal, nonce, *, now=None):
        self._owner_check(principal)
        self.store.cancel(self.task_id, principal, nonce, now=now)

    def confirm(self, principal, nonce, *, now=None):
        self._owner_check(principal)
        # Current head is read independently, never accepted from body/nonce.
        snapshot = self.coordinator.dialog.load(self.task_id)
        row = self.store.consume(self.task_id, principal, nonce, snapshot.head, now=now)
        # No exception handler replays this operation after durable consumption.
        self.coordinator.prepare(row["expected_head"], message_id=row["message_id"], actor=row["actor"])
        return {"task_id": self.task_id, "status": "terminal_intent_prepared_not_published"}
