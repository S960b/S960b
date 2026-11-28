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
    'outbox_not_found', 'dialog_has_active_delivery',
}



def _bridge_diag58(exc, component):
    """Emit only fixed categories; never stringify an exception or log its payload."""
    try:
        import os
        import stat
        import time
        import fcntl
        from pathlib import Path
        state = getattr(exc, "sqlstate", None)
        if type(state) is not str:
            state = ""
        # AdapterError deliberately strips SQLSTATE. Its own known safe code is
        # compared literally, never written verbatim or taken from server data.
        if state == "28P01" or state.startswith("28"):
            category = "db_authentication"
        elif state == "42501":
            category = "db_permission_denied"
        elif state == "57014":
            category = "db_query_cancelled_or_timeout"
        elif state == "55P03":
            category = "db_lock_unavailable"
        elif state == "40P01":
            category = "db_deadlock"
        elif state == "40001":
            category = "db_serialization_conflict"
        elif state == "53300":
            category = "db_connection_capacity"
        elif state == "57P01":
            category = "db_shutdown"
        elif state.startswith("08"):
            category = "db_connection_failure"
        elif state.startswith("23"):
            category = "db_constraint_failure"
        elif state == "P0001":
            category = "db_application_rejection"
        elif state:
            category = "db_other_sqlstate"
        elif isinstance(exc, TimeoutError):
            category = "local_timeout"
        elif isinstance(exc, ConnectionError):
            category = "local_connection_failure"
        elif isinstance(exc, OSError):
            category = "local_os_error"
        elif type(exc).__module__.startswith("psycopg") and type(exc).__name__ in ("OperationalError", "ConnectionTimeout"):
            category = "db_transport_or_connect_failure"
        elif type(exc).__name__ == "AdapterError" and getattr(exc,"args",()) == ("actor_database_unavailable",):
            category = "actor_database_failure_see_adapter"
        elif type(exc).__name__ == "AdapterError" and getattr(exc,"args",()) == ("actor_identity_mismatch",):
            category = "actor_identity_mismatch"
        elif type(exc).__name__ == "AdapterError":
            category = "actor_adapter_rejection"
        else:
            category = "internal_failure"
        if component not in ("store","actor_adapter","managed_agent_cli"):
            return
        # Local same-user private append log. No symlink following, no new dirs
        # at runtime. Bounded to 1 MiB; rotate only under exclusive file lock by
        # truncating oldest accumulated log in place. Never affects primary path.
        root = Path.home()/".local/state/bridge-pg-v2"
        st = root.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            return
        fd = os.open(root/"diagnostics58.log", os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
                return
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            if os.fstat(fd).st_size>=1048576:
                os.ftruncate(fd,0)
            stamp=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
            os.write(fd,(stamp+" "+component+" "+category+"\n").encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        pass  # Diagnostics must never change delivery/error behavior.


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
            _bridge_diag58(e, 'store')
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
            _bridge_diag58(e, 'store')
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

    def hand_back_turn(self, task_id: str, expected_version: int | None = None) -> dict:
        return self._one('SELECT bridge.hand_back_turn(%s,%s) AS result',
                         (task_id, expected_version))

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
            _bridge_diag58(e, 'store')
            raise BridgeError('database_unavailable') from e

    def ack_outbox(self, delivery_id: int, lease_token: str, consumer: str) -> bool:
        return self._one('SELECT bridge.ack_outbox(%s,%s::uuid,%s) AS result',
                         (delivery_id, lease_token, consumer))

    def release_outbox(self, delivery_id: int, lease_token: str, consumer: str,
                       retry_seconds: int = 10, error: str | None = None) -> bool:
        return self._one('SELECT bridge.release_outbox(%s,%s::uuid,%s,%s,%s) AS result',
                         (delivery_id, lease_token, consumer, retry_seconds, error))
