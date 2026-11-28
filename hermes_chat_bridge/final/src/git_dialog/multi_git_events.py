"""P31 multi-dialogue event outbox: isolated from the pinned P26 pilot.

Only signed-in owner's hash and bounded Git metadata persist. No Git writes,
no chat content, no credential lookup, and no independent subscription API.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import DialogError, validate_task_id
from .git_events import (NotificationPump, NativeDelivery, _digest,
                         event_id, metadata, wire)
from .journal_probe import _db_file

APPLICATION_ID = 0x47445031  # GDP1


class MultiEventJournal:
    """One durable outbox shared by all *new* dialogues (never the pilot)."""
    def __init__(self, path, pilot_task_id, checkpoint_row):
        validate_task_id(pilot_task_id)
        if type(checkpoint_row) is not int or checkpoint_row < 0:
            raise DialogError('multi_event_checkpoint_rejected')
        path = Path(path)
        existed = path.exists() or path.is_symlink()
        _db_file(path, create=not existed)
        if path.lstat().st_nlink != 1:
            raise DialogError('multi_event_journal_rejected')
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute('PRAGMA synchronous=FULL')
            with self.conn:
                app = self.conn.execute('PRAGMA application_id').fetchone()[0]
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'")}
                if existed:
                    if app != APPLICATION_ID or tables != {'route', 'events'}:
                        raise DialogError('multi_event_journal_rejected')
                else:
                    self.conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    self.conn.execute(
                        'CREATE TABLE route (id INTEGER PRIMARY KEY CHECK(id=1), '
                        'pilot TEXT NOT NULL, checkpoint INTEGER NOT NULL, owner_hash TEXT)')
                    self.conn.execute(
                        'CREATE TABLE events (event_id TEXT PRIMARY KEY, '
                        'payload TEXT NOT NULL, queued INTEGER NOT NULL DEFAULT 0 '
                        'CHECK(queued IN(0,1)))')
                    self.conn.execute('INSERT INTO route VALUES(1,?,?,NULL)',
                                      (pilot_task_id, checkpoint_row))
                row = self.conn.execute('SELECT * FROM route').fetchall()
                if (len(row) != 1 or row[0]['id'] != 1 or
                        row[0]['pilot'] != pilot_task_id or
                        type(row[0]['checkpoint']) is not int or row[0]['checkpoint'] < 0):
                    raise DialogError('multi_event_journal_rejected')
                self.checkpoint_row = row[0]['checkpoint']
                self.pilot_task_id = pilot_task_id
                self.owner_hash = row[0]['owner_hash']
                if self.owner_hash is not None and not _digest(self.owner_hash):
                    raise DialogError('multi_event_journal_rejected')
            for target, flags in ((path, os.O_RDONLY),
                                  (path.parent, os.O_RDONLY | os.O_DIRECTORY)):
                fd = os.open(target, flags | os.O_NOFOLLOW)
                try: os.fsync(fd)
                finally: os.close(fd)
        except BaseException:
            self.conn.close()
            raise

    def close(self):
        self.conn.close()

    def bind(self, digest):
        if not _digest(digest) or self.owner_hash not in (None, digest):
            raise DialogError('git_event_owner_mismatch')
        with self.conn:
            self.conn.execute('UPDATE route SET owner_hash=? '
                              'WHERE id=1 AND owner_hash IS NULL', (digest,))
        self.owner_hash = digest

    def record(self, value):
        if type(value) is not dict:
            raise DialogError('git_event_metadata_rejected')
        task_id = value.get('task_id')
        value = metadata(value, task_id)
        if task_id == self.pilot_task_id:
            raise DialogError('multi_event_pilot_rejected')
        eid, payload = event_id(value), wire(value)
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO events(event_id,payload) '
                              'VALUES(?,?)', (eid, payload))
            saved = self.conn.execute('SELECT payload FROM events WHERE event_id=?',
                                      (eid,)).fetchone()
            if saved['payload'] != payload:
                raise DialogError('git_event_conflict')

    def pending(self):
        row = self.conn.execute('SELECT event_id,payload FROM events '
                                'WHERE queued=0 ORDER BY rowid LIMIT 1').fetchone()
        if row is None:
            return None
        obj = json.loads(row['payload'])
        if type(obj) is not dict:
            raise DialogError('multi_event_journal_rejected')
        value = metadata(obj, obj.get('task_id'))
        if value['task_id'] == self.pilot_task_id or event_id(value) != row['event_id']:
            raise DialogError('multi_event_journal_rejected')
        return row['event_id'], value

    def acknowledge(self, eid):
        if type(eid) is not str or not eid.startswith('git_'):
            raise DialogError('git_event_metadata_rejected')
        with self.conn:
            self.conn.execute('UPDATE events SET queued=1 WHERE event_id=?', (eid,))


class MultiDialogNativeDelivery(NativeDelivery):
    """Accept validated per-event task IDs; no model-provided task binding."""
    def __init__(self, store, loop):
        super().__init__(store, loop, None)


class MultiDialogNotificationPump(NotificationPump):
    """Reuse P26's bounded scan, durable outbox and native de-duplication."""
    def __init__(self, journal, appends, dialog, delivery, registry, *,
                 clock=None):
        from . import validate_task_id
        if not isinstance(journal, MultiEventJournal):
            raise DialogError('multi_event_journal_rejected')
        # Pass the inherited clock only when supplied by a deterministic test.
        if clock is None:
            super().__init__(journal, appends, dialog, delivery)
        else:
            super().__init__(journal, appends, dialog, delivery, clock=clock)
        self.registry = registry

    def scan(self):
        return self.appends.notification_page_all(
            self.dialog, self.registry, after_row=self.cursor,
            checkpoint_row=self.journal.checkpoint_row,
            exclude_task_id=self.journal.pilot_task_id)
