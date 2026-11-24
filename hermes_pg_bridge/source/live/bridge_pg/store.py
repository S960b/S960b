"""Thin, bounded wrapper over bridge-owned SECURITY DEFINER SQL functions.

Only call these methods from an authenticated MCP / local worker process.
The database role `bridge_app` is a shared application capability, not a
replacement for verifying remote-user identity and actor role at the edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import os
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar

import psycopg
from psycopg.rows import dict_row


KNOWN_ERRORS = {
    'invalid_request', 'request_id_conflict', 'message_id_conflict',
    'dialog_not_found', 'dialog_not_active', 'wrong_turn',
    'version_conflict', 'return_not_allowed', 'delivery_already_started',
    'outbox_not_found',
}


@dataclass
class BridgeError(Exception):
    code: str

    def __str__(self) -> str:
        return self.code


class Store:
    """One short connection per operation; no persistent idle connections.

    For 1 GiB servers this deliberately avoids a default Python connection
    pool. Libpq environment variables, including PGPASSFILE, are supported.
    """
    def __init__(self, conninfo: str | None = None):
        config = Path.home()/'.config/bridge-pg-v2/conninfo'
        configured = None
        if conninfo is None and not os.environ.get('BRIDGE_PG_DSN') and config.exists():
            stat = config.lstat()
            if not config.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_nlink != 1:
                raise BridgeError('connection_config_unsafe')
            configured = config.read_text().strip()
            if '\n' in configured or '\r' in configured or len(configured) > 1024:
                raise BridgeError('connection_config_unsafe')
        self.conninfo = conninfo or os.environ.get('BRIDGE_PG_DSN') or configured or (
            'host=127.0.0.1 port=15432 dbname=bridge user=bridge_app '
            'connect_timeout=5 application_name=bridge_pg_v2')

        self._taskboard_scope = ContextVar("bridge_pg_taskboard_scope", default=None)

    def _connect(self):
        return psycopg.connect(self.conninfo, row_factory=dict_row)

    def _run_one_on_connection(self, cn, sql: str, args: tuple[Any, ...]) -> Any:
        with cn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '10000ms'")
            cur.execute(sql, args)
            row = cur.fetchone()
            return row['result'] if row is not None else None

    @contextmanager
    def taskboard_read_batch(self):
        """One private DB connection per HTTP request, never shared across threads.

        The contextvar is reset even on errors. Per-operation transactions below
        make expected per-dialog errors fail independently rather than poisoning
        all subsequent reads in a PostgreSQL transaction.
        """
        if self._taskboard_scope.get() is not None:
            raise BridgeError('request_rejected')
        try:
            with self._connect() as cn:
                token = self._taskboard_scope.set(cn)
                try:
                    yield
                finally:
                    self._taskboard_scope.reset(token)
        except psycopg.Error as e:
            raise BridgeError('database_unavailable') from e

    def _one(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        try:
            scoped = self._taskboard_scope.get()
            if scoped is None:
                # All non-Taskboard users retain the existing short connection.
                with self._connect() as cn:
                    return self._run_one_on_connection(cn, sql, args)
            # Committed independently: a recoverable error in one row must not
            # abort the next dialog's metadata/last-message lookup.
            with scoped.transaction():
                return self._run_one_on_connection(scoped, sql, args)
        except psycopg.Error as e:
            code = (getattr(getattr(e, 'diag', None), 'message_primary', None) or '').strip()
            if e.sqlstate == 'P0001' and code in KNOWN_ERRORS:
                raise BridgeError(code) from e
            # Never leak DSN, passwords, query parameters, or server paths.
            raise BridgeError('database_unavailable') from e

    def create_dialog(self, request_id: str, title: str, initiator: str = 'chatgpt') -> dict:
        return self._one('SELECT bridge.create_dialog(%s,%s,%s) AS result',
                         (request_id, title, initiator))

    def send_message(self, task_id: str, message_id: str, sender: str,
                     kind: str, body: str, expected_version: int | None = None) -> dict:
        return self._one('SELECT bridge.send_message(%s,%s,%s,%s,%s,%s) AS result',
                         (task_id, message_id, sender, kind, body, expected_version))

    def send_status(self, task_id: str, message_id: str) -> dict:
        return self._one('SELECT bridge.send_status(%s,%s) AS result', (task_id, message_id))

    def list_dialogs(self, offset: int = 0, limit: int = 30) -> list[dict]:
        return self._one('SELECT bridge.list_dialogs(%s,%s) AS result', (offset, limit))

    def view_dialog(self, task_id: str, offset: int = 0, limit: int = 30) -> dict:
        return self._one('SELECT bridge.view_dialog(%s,%s,%s) AS result', (task_id, offset, limit))

    def return_turn(self, task_id: str, message_id: str, sender: str,
                    expected_version: int | None = None) -> dict:
        return self._one('SELECT bridge.return_turn(%s,%s,%s,%s) AS result',
                         (task_id, message_id, sender, expected_version))

    def health(self) -> dict:
        return self._one('SELECT bridge.health() AS result')

    def claim_outbox(self, recipient: str, consumer: str,
                     limit: int = 10, lease_seconds: int = 60) -> list[dict]:
        try:
            with self._connect() as cn:
                with cn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '10000ms'")
                    cur.execute('SELECT * FROM bridge.claim_outbox(%s,%s,%s,%s)',
                                (recipient, consumer, limit, lease_seconds))
                    return list(cur.fetchall())
        except psycopg.Error as e:
            code = (getattr(getattr(e, 'diag', None), 'message_primary', None) or '').strip()
            if e.sqlstate == 'P0001' and code in KNOWN_ERRORS:
                raise BridgeError(code) from e
            raise BridgeError('database_unavailable') from e

    def ack_outbox(self, delivery_id: int, lease_token: str, consumer: str) -> bool:
        return self._one('SELECT bridge.ack_outbox(%s,%s::uuid,%s) AS result',
                         (delivery_id, lease_token, consumer))

    def release_outbox(self, delivery_id: int, lease_token: str, consumer: str,
                       retry_seconds: int = 10, error: str | None = None) -> bool:
        return self._one('SELECT bridge.release_outbox(%s,%s::uuid,%s,%s,%s) AS result',
                         (delivery_id, lease_token, consumer, retry_seconds, error))
