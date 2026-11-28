"""Shared durable append grants for BOTH actors; never execute conversation data.

One SQLite connection per owner thread. All cooperating writer instances must
use the same durable file and lifecycle registry. This cannot fence a separate
Git credential holder or make GitHub's update-ref a universal CAS.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

from . import DialogError, parse_message, prepare_append, validate_message_id, validate_task_id
from .github import sha
from .journal import _wire
from .journal_probe import _db_file

APPLICATION_ID = 0x47444150


class AppendStore:
    def __init__(self, path):
        path = Path(path)
        path = _db_file(path, create=not (path.exists() or path.is_symlink()))
        if path.lstat().st_nlink != 1:
            raise DialogError('unsafe_append_store')
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute('PRAGMA synchronous=FULL')
            with self._tx():
                app = self.conn.execute('PRAGMA application_id').fetchone()[0]
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if app not in (0, APPLICATION_ID) or (app == 0 and tables):
                    raise DialogError('wrong_append_database')
                if app == APPLICATION_ID and tables != {'appends'}:
                    raise DialogError('append_schema_mismatch')
                if app == 0:
                    self.conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    self.conn.execute('''CREATE TABLE appends (
                    task_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    expected_head TEXT NOT NULL, message TEXT NOT NULL,
                    attempted INTEGER NOT NULL DEFAULT 0 CHECK(attempted IN (0,1)),
                    confirmed_commit TEXT, PRIMARY KEY(task_id,message_id)
                )''')
        except BaseException:
            self.conn.close()
            raise

    @contextmanager
    def _tx(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    def _get(self, task_id, message_id):
        validate_task_id(task_id); validate_message_id(message_id)
        row = self.conn.execute('SELECT * FROM appends WHERE task_id=? AND message_id=?',
                                (task_id, message_id)).fetchone()
        if row is None:
            return None
        row = dict(row)
        sha(row['expected_head'])
        message = parse_message(json.loads(row['message']))
        if (message.task_id != task_id or message.message_id != message_id or
                _wire(message) != row['message'] or row['attempted'] not in (0, 1) or
                (row['confirmed_commit'] is not None and not row['attempted'])):
            raise DialogError('append_record_corrupt')
        if row['confirmed_commit'] is not None:
            sha(row['confirmed_commit'])
        return row, message

    def status(self, task_id, message_id):
        saved = self._get(task_id, message_id)
        if saved is None:
            raise DialogError('append_not_found')
        row, message = saved
        return {'task_id': task_id, 'message_id': message_id, 'actor': message.sender,
                'attempted': bool(row['attempted']), 'confirmed_commit': row['confirmed_commit']}

    def notification_page(self, dialog, task_id, *, after_row=0, after_seq=0, limit=32):
        """GET-only recovery of MT publications; NEVER republish an intent.

        The cursor is a local scan cursor, not a publication grant. Incomplete
        attempts stay eligible on the next scan. Return metadata only, not text.
        Must run on this connection's existing owner thread.
        """
        validate_task_id(task_id)
        if (type(after_row) is not int or after_row < 0 or type(after_seq) is not int or
                after_seq < 0 or type(limit) is not int or not 1 <= limit <= 32):
            raise DialogError('notification_scan_rejected')
        rows = self.conn.execute('SELECT rowid,message_id FROM appends WHERE task_id=? '
            'AND rowid>? ORDER BY rowid LIMIT ?', (task_id, after_row, limit)).fetchall()
        result = []
        for item in rows:
            row, message = self._get(task_id, item['message_id'])
            if (not row['attempted'] or message.sender != 'mt' or
                    message.recipient != 'chatgpt' or message.kind == 'return' or message.seq <= after_seq):
                continue
            commit = row['confirmed_commit']
            if commit is None:
                # Lost response after a remote effect: exact saved message GET
                # reconciliation only. No new ID/recompute/object/ref mutation.
                try:
                    commit = self._observe(dialog, row, message).commit_sha
                except DialogError:
                    continue
            result.append({'source': 'git-dialog', 'queue': 'test', 'task_id': task_id,
                'head': commit, 'message_id': message.message_id, 'seq': message.seq,
                'from': 'mt', 'to': 'chatgpt'})
        return (rows[-1]['rowid'] if rows else 0), result

    def notification_page_all(self, dialog, registry, *, after_row=0,
                              checkpoint_row=0, exclude_task_id=None, limit=32):
        """Read-only scan of durable MT publications across registered dialogs.

        The checkpoint is retained by the separate P31 event journal. The cursor
        never grants a Git mutation; uncertain receipts use existing GET-only
        reconciliation. A completed sweep returns cursor=0 for rescan, allowing
        pending publications to resolve after an earlier network failure.
        """
        if (type(after_row) is not int or after_row < 0 or
                type(checkpoint_row) is not int or checkpoint_row < 0 or
                type(limit) is not int or not 1 <= limit <= 32):
            raise DialogError('notification_scan_rejected')
        if exclude_task_id is not None:
            validate_task_id(exclude_task_id)
        cursor = max(after_row, checkpoint_row)
        rows = self.conn.execute(
            'SELECT rowid,task_id,message_id FROM appends WHERE rowid>? '
            'ORDER BY rowid LIMIT ?', (cursor, limit)).fetchall()
        result = []
        for item in rows:
            task_id = item['task_id']
            if task_id == exclude_task_id:
                continue
            # Never adopt a task merely because a row in SQLite references it.
            # The local registry is the only authority for registered dialogues.
            try:
                if registry.get(task_id)['state'] != 'active':
                    continue
            except DialogError as exc:
                if exc.code == 'unregistered_dialog':
                    continue
                raise
            row, message = self._get(task_id, item['message_id'])
            if (not row['attempted'] or message.sender != 'mt' or
                    message.recipient != 'chatgpt' or message.kind == 'return'):
                continue
            commit = row['confirmed_commit']
            if commit is None:
                try:
                    commit = self._observe(dialog, row, message).commit_sha
                except DialogError:
                    continue
            result.append({'source': 'git-dialog', 'queue': 'test', 'task_id': task_id,
                           'head': commit, 'message_id': message.message_id,
                           'seq': message.seq, 'from': 'mt', 'to': 'chatgpt'})
        # Return zero on exhaustion to rescan unresolved records on a later tick.
        next_row = rows[-1]['rowid'] if len(rows) == limit else 0
        return next_row, result

    def notification_checkpoint(self):
        """Last durable row at first P31 activation; no historical blast."""
        return self.conn.execute('SELECT COALESCE(MAX(rowid),0) FROM appends').fetchone()[0]

    def _observe(self, dialog, row, message):
        try:
            receipt = dialog.reconcile(message.task_id, message)
        except DialogError as exc:
            if exc.code == 'publication_not_observed':
                raise DialogError('gateway_publication_unresolved') from None
            raise
        with self._tx():
            self.conn.execute('UPDATE appends SET confirmed_commit=COALESCE(confirmed_commit,?) '
                              'WHERE task_id=? AND message_id=? AND attempted=1',
                              (receipt.commit_sha, message.task_id, message.message_id))
        return receipt

    def publish(self, dialog, task_id, expected_head, *, message_id, actor, kind, body,
                created_at_utc=None):
        """Called ONLY while the gateway holds its lifecycle writing transaction.

        An attempted record can ONLY reconcile. Absence, changed history or a
        lost response NEVER grants a resend, even after reopening this database.
        """
        sha(expected_head)
        saved = self._get(task_id, message_id)
        if saved is not None:
            row, message = saved
            if (message.sender != actor or message.kind != kind or message.body != body or
                    (created_at_utc is not None and message.created_at_utc != created_at_utc)):
                raise DialogError('message_id_conflict')
            if row['attempted']:
                return self._observe(dialog, row, message)
        else:
            snapshot = dialog.load(task_id)
            prepared = prepare_append(snapshot, expected_head, task_id=task_id,
                message_id=message_id, actor=actor, kind=kind, body=body,
                created_at_utc=created_at_utc)
            message = prepared.message
            if not prepared.created:
                return dialog.reconcile(task_id, message)
            with self._tx():
                self.conn.execute('INSERT INTO appends(task_id,message_id,expected_head,message) '
                                  'VALUES (?,?,?,?)', (task_id, message_id, expected_head, _wire(message)))
            row, message = self._get(task_id, message_id)
        # Durable grant BEFORE any remote mutation, including object creation.
        with self._tx():
            granted = self.conn.execute('UPDATE appends SET attempted=1 WHERE task_id=? '
                                       'AND message_id=? AND attempted=0', (task_id, message_id)).rowcount
        if granted != 1:
            row, message = self._get(task_id, message_id)
            return self._observe(dialog, row, message)
        sent = dialog.append(task_id, row['expected_head'], message_id=message.message_id,
                             actor=message.sender, kind=message.kind, body=message.body,
                             created_at_utc=message.created_at_utc)
        return replace(self._observe(dialog, row, message), created=sent.created)
