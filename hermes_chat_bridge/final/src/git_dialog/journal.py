"""Durable local execution ledger. No executor, network, or automatic retry.

A successful start is a one-time grant. A repeated start (even with the same
run_id) is NOT a grant. Call mark_unknown only after the owning worker is known
to have stopped. An uncertain external effect requires operator resolution.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from . import DialogError, Message, parse_message, validate_message_id, validate_task_id

APPLICATION_ID = 0x47444C47
SHA_RE = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")


def _wire(message):
    if not isinstance(message, Message):
        raise DialogError("invalid_message")
    parsed = parse_message(message.to_dict())
    return json.dumps(parsed.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class ExecutionJournal:
    """One connection per worker/thread; separate connections coordinate in SQLite."""

    def __init__(self, path, actor="mt"):
        if actor not in ("mt", "chatgpt"):
            raise DialogError("unknown_actor")
        path = Path(path)
        if path.is_symlink():
            raise DialogError("unsafe_journal_path")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Do not change permissions of arbitrary existing files.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self.actor = actor
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA synchronous=FULL")
        try:
            with self._tx():
                application_id = self.conn.execute("PRAGMA application_id").fetchone()[0]
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if application_id not in (0, APPLICATION_ID) or (application_id == 0 and tables):
                    raise DialogError("wrong_journal_database")
                if application_id == APPLICATION_ID and tables != {"executions"}:
                    raise DialogError("journal_schema_mismatch")
                if application_id == 0:
                    self.conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    self.conn.execute("""CREATE TABLE executions (
                    task_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    actor TEXT NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('received','started','result_ready','published','unknown')),
                    run_id TEXT, result TEXT, result_message_id TEXT, commit_sha TEXT,
                    PRIMARY KEY(task_id, message_id), UNIQUE(task_id, result_message_id)
                )""")
                columns = {r[1] for r in self.conn.execute("PRAGMA table_info(executions)")}
                if "publication_attempted" not in columns:
                    self.conn.execute("ALTER TABLE executions ADD COLUMN publication_attempted "
                                      "INTEGER NOT NULL DEFAULT 0 CHECK(publication_attempted IN (0,1))")
                    # Legacy rows cannot prove a publication was never tried.
                    # Allow observation/reconciliation, not a new automatic send.
                    self.conn.execute("UPDATE executions SET publication_attempted=1 "
                                      "WHERE status != 'received'")
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

    def _row(self, task_id, message_id):
        validate_task_id(task_id)
        validate_message_id(message_id)
        row = self.conn.execute(
            "SELECT * FROM executions WHERE task_id=? AND message_id=?",
            (task_id, message_id)).fetchone()
        if row is None:
            raise DialogError("execution_not_found")
        if row["actor"] != self.actor:
            raise DialogError("not_execution_owner")
        return row

    def get(self, task_id, message_id):
        """Returns a copy; callers must not log message bodies or credential-like data."""
        return dict(self._row(task_id, message_id))

    def receive(self, message):
        payload = _wire(message)
        if message.recipient != self.actor or message.kind != "task":
            raise DialogError("not_executable_task")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._tx():
            self.conn.execute(
                "INSERT OR IGNORE INTO executions "
                "(task_id,message_id,actor,payload_hash,payload,status) VALUES (?,?,?,?,?,'received')",
                (message.task_id, message.message_id, self.actor, digest, payload))
            row = self._row(message.task_id, message.message_id)
            if row["payload_hash"] != digest or row["payload"] != payload:
                raise DialogError("message_id_conflict")
            return dict(row)

    def start(self, task_id, message_id, run_id):
        """True exactly once for this record; False never authorizes execution."""
        validate_message_id(run_id)
        with self._tx():
            row = self._row(task_id, message_id)
            if row["status"] != "received":
                return False
            self.conn.execute(
                "UPDATE executions SET status='started',run_id=? WHERE task_id=? AND message_id=?",
                (run_id, task_id, message_id))
            return True

    def save_result(self, task_id, message_id, run_id, result):
        """Persist a validated immutable report before attempting publication."""
        validate_message_id(run_id)
        payload = _wire(result)
        with self._tx():
            row = self._row(task_id, message_id)
            incoming = parse_message(json.loads(row["payload"]))
            if row["run_id"] != run_id:
                raise DialogError("not_execution_owner")
            if (result.task_id != task_id or result.reply_to != message_id
                    or result.sender != self.actor or result.recipient != incoming.sender
                    or result.seq != incoming.seq + 1 or result.kind != "report"
                    or result.message_id == message_id):
                raise DialogError("invalid_execution_result")
            if row["status"] in ("result_ready", "published"):
                if row["result"] != payload:
                    raise DialogError("result_conflict")
                return dict(row)
            if row["status"] != "started":
                raise DialogError("invalid_execution_transition")
            try:
                self.conn.execute(
                    "UPDATE executions SET status='result_ready',result=?,result_message_id=? "
                    "WHERE task_id=? AND message_id=?",
                    (payload, result.message_id, task_id, message_id))
            except sqlite3.IntegrityError:
                raise DialogError("result_message_id_conflict") from None
            return dict(self._row(task_id, message_id))

    def mark_published(self, task_id, message_id, run_id, commit_sha):
        validate_message_id(run_id)
        if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
            raise DialogError("invalid_commit_sha")
        with self._tx():
            row = self._row(task_id, message_id)
            if row["run_id"] != run_id:
                raise DialogError("not_execution_owner")
            if row["status"] == "published":
                if row["commit_sha"] != commit_sha:
                    raise DialogError("publication_conflict")
                return dict(row)
            if row["status"] != "result_ready":
                raise DialogError("invalid_execution_transition")
            self.conn.execute(
                "UPDATE executions SET status='published',commit_sha=? WHERE task_id=? AND message_id=?",
                (commit_sha, task_id, message_id))
            return dict(self._row(task_id, message_id))

    def reserve_publication(self, task_id, message_id, run_id):
        """Durable one-time send grant. Crash after this grant NEVER regrants it.

        Recovery may only observe the exact saved result, not send it again.
        No reset method: a failed/unobserved attempt needs explicit resolution.
        """
        validate_message_id(run_id)
        with self._tx():
            row = self._row(task_id, message_id)
            if row["run_id"] != run_id:
                raise DialogError("not_execution_owner")
            if row["status"] not in ("result_ready", "published"):
                raise DialogError("result_not_ready")
            if row["status"] == "published" or row["publication_attempted"]:
                return False
            self.conn.execute("UPDATE executions SET publication_attempted=1 "
                              "WHERE task_id=? AND message_id=?", (task_id, message_id))
            return True

    def mark_unknown(self, task_id, message_id, run_id):
        """Explicit recovery only after confirming the worker stopped; no reset/retry."""
        validate_message_id(run_id)
        with self._tx():
            row = self._row(task_id, message_id)
            if row["run_id"] != run_id:
                raise DialogError("not_execution_owner")
            if row["status"] == "unknown":
                return dict(row)
            if row["status"] != "started":
                raise DialogError("invalid_execution_transition")
            self.conn.execute(
                "UPDATE executions SET status='unknown' WHERE task_id=? AND message_id=?",
                (task_id, message_id))
            return dict(self._row(task_id, message_id))

