"""Durable LOCAL lifecycle gate; no network, branch creation or deletion.

All cooperating writers must use the SAME durable registry. SQLite's write
transaction spans append so close/cancel cannot overtake that local writer.
This is not a distributed lock, proof of user identity, or Git object erasure.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

from . import DialogError, branch_ref, validate_task_id
from .github import sha

APPLICATION_ID = 0x47444C43


def _now(value):
    value = int(time.time()) if value is None else value
    if type(value) is not int or value < 0:
        raise DialogError("invalid_lifecycle_time")
    return value


@dataclass(frozen=True)
class DeletionTicket:
    task_id: str
    ref: str
    expected_head: str


class LifecycleRegistry:
    """Separate database, not the bridge DB or ExecutionJournal DB.

    Cancelled/tombstoned IDs NEVER become active again. A tombstone means locally
    disabled before an optional future delete; it does not mean remote absent.
    """
    def __init__(self, path, *, existing=False):
        path = Path(path)
        self.conn = None
        if type(existing) is not bool:
            raise DialogError("unsafe_lifecycle_path")
        if not existing:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            except OSError:
                raise DialogError("unsafe_lifecycle_path") from None
            else:
                os.close(fd)
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise DialogError("unsafe_lifecycle_path")
            self.conn = sqlite3.connect(path, timeout=5)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA synchronous=FULL")
            with self._tx():
                app = self.conn.execute("PRAGMA application_id").fetchone()[0]
                if existing and app != APPLICATION_ID:
                    raise DialogError("wrong_lifecycle_database")
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if app not in (0, APPLICATION_ID) or (app == 0 and tables):
                    raise DialogError("wrong_lifecycle_database")
                if app == APPLICATION_ID and tables != {"dialogs"}:
                    raise DialogError("lifecycle_schema_mismatch")
                if app == 0:
                    self.conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    self.conn.execute("""CREATE TABLE dialogs (
                    task_id TEXT PRIMARY KEY, initial_head TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active','closing','cancelled','tombstoned')),
                    delete_head TEXT, nonce_hash TEXT, issued_at INTEGER, expires_at INTEGER,
                    before_close TEXT CHECK(before_close IN ('active','cancelled')),
                    remote_absent INTEGER NOT NULL DEFAULT 0 CHECK(remote_absent IN (0,1))
                )""")
        except BaseException:
            if self.conn is not None:
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
        row = self.conn.execute("SELECT * FROM dialogs WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise DialogError("unregistered_dialog")
        return row

    def get(self, task_id):
        row = dict(self._row(task_id))
        row.pop("nonce_hash")
        return row

    def register(self, task_id, initial_head):
        """Explicit local registration only, NEVER a remote create-ref call."""
        validate_task_id(task_id)
        sha(initial_head)
        with self._tx():
            existing = self.conn.execute("SELECT * FROM dialogs WHERE task_id=?", (task_id,)).fetchone()
            if existing is not None:
                if existing["state"] != "active":
                    raise DialogError("task_id_retired")
                if existing["initial_head"] != initial_head:
                    raise DialogError("dialog_registration_conflict")
            else:
                self.conn.execute("INSERT INTO dialogs(task_id,initial_head,state) VALUES (?,?,'active')",
                                  (task_id, initial_head))
            return self.get(task_id)

    def page(self, *, offset=0, limit=20):
        """Local metadata only, newest registrations first; no Git/body reads."""
        if (type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100):
            raise DialogError('invalid_dialog_page')
        rows = self.conn.execute('SELECT task_id,initial_head,state FROM dialogs '
            'ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
        return [dict(row) for row in rows[:limit]], (offset + limit if len(rows) > limit else None)

    @contextmanager
    def writing(self, task_id):
        # Do not release this transaction between authorization and append.
        with self._tx():
            if self._row(task_id)["state"] != "active":
                raise DialogError("dialog_not_writable")
            yield

    def cancel(self, task_id):
        """Stops FUTURE local publication, not an already-running external effect."""
        with self._tx():
            row = self._row(task_id)
            if row["state"] == "tombstoned":
                raise DialogError("task_id_retired")
            self.conn.execute("UPDATE dialogs SET state='cancelled',nonce_hash=NULL,"
                              "issued_at=NULL,expires_at=NULL WHERE task_id=?", (task_id,))
            return self.get(task_id)

    def request_delete(self, task_id, expected_head, *, now=None, ttl=300):
        """Explicit UI intent. Return nonce once; never put it in history/logs."""
        sha(expected_head)
        now = _now(now)
        if type(ttl) is not int or not 1 <= ttl <= 600:
            raise DialogError("invalid_confirmation_ttl")
        nonce = secrets.token_urlsafe(24)
        digest = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        with self._tx():
            row = self._row(task_id)
            if row["state"] == "tombstoned":
                raise DialogError("task_id_retired")
            previous = row["before_close"] if row["state"] == "closing" else row["state"]
            self.conn.execute("UPDATE dialogs SET state='closing',delete_head=?,nonce_hash=?,"
                              "issued_at=?,expires_at=?,before_close=? WHERE task_id=?",
                              (expected_head, digest, now, now + ttl, previous, task_id))
        return {"task_id": task_id, "confirmation": nonce, "expected_head": expected_head,
                "expires_at": now + ttl, "state": "closing"}

    def abort_delete(self, task_id):
        """Explicitly cancel an UNCONFIRMED UI intent; no ticket was issued.

        Does not resurrect cancelled/tombstoned tasks or create a remote ref.
        """
        with self._tx():
            row = self._row(task_id)
            if row["state"] != "closing" or row["before_close"] not in ("active", "cancelled"):
                raise DialogError("delete_not_pending")
            self.conn.execute("UPDATE dialogs SET state=?,delete_head=NULL,nonce_hash=NULL,"
                              "issued_at=NULL,expires_at=NULL,before_close=NULL WHERE task_id=?",
                              (row["before_close"], task_id))
            return self.get(task_id)

    def confirm_delete(self, task_id, confirmation, observed_head, *, now=None):
        """Persist tombstone BEFORE returning a ticket. Does not delete Git ref.

        Caller must authenticate a real user and independently read current ref.
        Ticket's expected_head is NOT server-side conditional DELETE protection.
        """
        sha(observed_head)
        now = _now(now)
        if not isinstance(confirmation, str) or not 1 <= len(confirmation) <= 128:
            raise DialogError("invalid_delete_confirmation")
        digest = hashlib.sha256(confirmation.encode("utf-8")).hexdigest()
        with self._tx():
            row = self._row(task_id)
            if row["state"] != "closing" or row["nonce_hash"] is None:
                raise DialogError("delete_not_pending")
            if not hmac.compare_digest(row["nonce_hash"], digest):
                raise DialogError("invalid_delete_confirmation")
            if not row["issued_at"] <= now < row["expires_at"]:
                raise DialogError("delete_confirmation_expired")
            if row["delete_head"] != observed_head:
                raise DialogError("delete_head_changed")
            self.conn.execute("UPDATE dialogs SET state='tombstoned',nonce_hash=NULL,"
                              "issued_at=NULL,expires_at=NULL,before_close=NULL WHERE task_id=?", (task_id,))
            return DeletionTicket(task_id, branch_ref(task_id), observed_head)

    def record_remote_absent(self, task_id):
        """Caller must have verified remote absence; this method makes no API call."""
        with self._tx():
            if self._row(task_id)["state"] != "tombstoned":
                raise DialogError("delete_not_confirmed")
            self.conn.execute("UPDATE dialogs SET remote_absent=1 WHERE task_id=?", (task_id,))
            return self.get(task_id)


class LifecycleDialog:
    """Gate cooperating appends on one local registry, not arbitrary external Git."""
    def __init__(self, dialog, registry):
        self.dialog = dialog
        self.registry = registry

    def load(self, task_id):
        self.registry.get(task_id)
        return self.dialog.load(task_id)

    def reconcile(self, task_id, message):
        self.registry.get(task_id)
        return self.dialog.reconcile(task_id, message)

    def append(self, task_id, expected_head, **kwargs):
        with self.registry.writing(task_id):
            return self.dialog.append(task_id, expected_head, **kwargs)

