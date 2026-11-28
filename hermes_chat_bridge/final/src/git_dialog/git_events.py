"""Git-reference notifications, NOT a second conversation/Queue writer.

The append grant is the durable source, so a crash between Git confirmation
and callback scheduling is recoverable without another Git write. This private
outbox holds metadata and a hash of the authenticated owner only. Native
subscription/delivery calls execute on the original ASGI loop, never on the
gateway's SQLite-owner thread. Existing native (sub_id,event_id) uniqueness
deduplicates a replay after enqueue success but before local acknowledgement.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from . import DialogError, validate_message_id, validate_task_id
from .github import sha
from .journal_probe import _db_file

APPLICATION_ID = 0x47444556
EVENT = 'hermes.message.created'
CHECKPOINT_HEAD = 'dda52cff4167859d47d560dec7d7f0f080cb431a'
CHECKPOINT_SEQ = 12
KEYS = {'source', 'queue', 'task_id', 'head', 'message_id', 'seq', 'from', 'to'}


def owner_digest(principal):
    # The native OAuth guard, not tool/Git/event data, supplies this value.
    if (not isinstance(principal, str) or not 1 <= len(principal) <= 512 or
            any(ord(c) < 33 or ord(c) == 127 for c in principal)):
        raise DialogError('git_event_owner_binding_required')
    return hashlib.sha256(('git-dialog-owner-v1\0' + principal).encode('utf-8')).hexdigest()


def metadata(value, task_id):
    if (not isinstance(value, dict) or set(value) != KEYS or value['source'] != 'git-dialog' or
            value['queue'] != 'test' or value['task_id'] != task_id or
            value['from'] != 'mt' or value['to'] != 'chatgpt' or
            type(value['seq']) is not int or not 1 <= value['seq'] <= 2**31-1):
        raise DialogError('git_event_metadata_rejected')
    validate_task_id(task_id); validate_message_id(value['message_id']); sha(value['head'])
    return dict(value)


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def event_id(value):
    return 'git_' + hashlib.sha256(('git-dialog-event-v1\0' + wire(value)).encode()).hexdigest()


class EventJournal:
    """One dedicated gateway-thread connection; no conversation text/schema changes."""
    def __init__(self, path, task_id, checkpoint_head, checkpoint_seq):
        validate_task_id(task_id); sha(checkpoint_head)
        if type(checkpoint_seq) is not int or checkpoint_seq < 0:
            raise DialogError('git_event_checkpoint_rejected')
        self.task_id, self.checkpoint_seq = task_id, checkpoint_seq
        path = Path(path)
        existed = path.exists() or path.is_symlink()
        _db_file(path, create=not existed)
        if path.stat().st_nlink != 1:
            raise DialogError('git_event_journal_rejected')
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute('PRAGMA synchronous=FULL')
            with self.tx():
                app = self.conn.execute('PRAGMA application_id').fetchone()[0]
                tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if existed:
                    if app != APPLICATION_ID or tables != {'route', 'events'}:
                        raise DialogError('git_event_journal_rejected')
                else:
                    self.conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    self.conn.execute('CREATE TABLE route(task TEXT PRIMARY KEY, head TEXT NOT NULL, '
                        'seq INTEGER NOT NULL, owner_hash TEXT)')
                    self.conn.execute('CREATE TABLE events(event_id TEXT PRIMARY KEY, '
                        'payload TEXT NOT NULL, queued INTEGER NOT NULL DEFAULT 0 CHECK(queued IN(0,1)))')
                    self.conn.execute('INSERT INTO route VALUES(?,?,?,NULL)',
                        (task_id, checkpoint_head, checkpoint_seq))
                rows = self.conn.execute('SELECT * FROM route').fetchall()
                if (len(rows) != 1 or rows[0]['task'] != task_id or rows[0]['head'] != checkpoint_head or
                        rows[0]['seq'] != checkpoint_seq):
                    raise DialogError('git_event_checkpoint_rejected')
                self.owner_hash = rows[0]['owner_hash']
                if self.owner_hash is not None and not _digest(self.owner_hash):
                    raise DialogError('git_event_journal_rejected')
            for target, flags in ((path, os.O_RDONLY), (path.parent, os.O_RDONLY | os.O_DIRECTORY)):
                fd = os.open(target, flags | os.O_NOFOLLOW)
                try: os.fsync(fd)
                finally: os.close(fd)
        except BaseException:
            self.conn.close()
            raise

    @contextmanager
    def tx(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self): self.conn.close()

    def bind(self, digest):
        if not _digest(digest) or self.owner_hash not in (None, digest):
            raise DialogError('git_event_owner_mismatch')
        with self.tx():
            self.conn.execute('UPDATE route SET owner_hash=? WHERE owner_hash IS NULL', (digest,))
        self.owner_hash = digest

    def record(self, value):
        value = metadata(value, self.task_id)
        if value['seq'] <= self.checkpoint_seq:
            raise DialogError('git_event_before_checkpoint')
        eid, payload = event_id(value), wire(value)
        with self.tx():
            self.conn.execute('INSERT OR IGNORE INTO events(event_id,payload) VALUES(?,?)', (eid, payload))
            row = self.conn.execute('SELECT payload FROM events WHERE event_id=?', (eid,)).fetchone()
            if row['payload'] != payload:
                raise DialogError('git_event_conflict')

    def pending(self):
        row = self.conn.execute('SELECT event_id,payload FROM events WHERE queued=0 ORDER BY rowid LIMIT 1').fetchone()
        if row is None: return None
        value = metadata(json.loads(row['payload']), self.task_id)
        if event_id(value) != row['event_id'] or value['seq'] <= self.checkpoint_seq:
            raise DialogError('git_event_journal_rejected')
        return row['event_id'], value

    def acknowledge(self, eid):
        with self.tx():
            self.conn.execute('UPDATE events SET queued=1 WHERE event_id=?', (eid,))


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class NativeDelivery:
    """Only the verified native methods, always on the original asyncio loop.

    A successful return means queued in native deliveries, NOT received/read
    by ChatGPT. Native callback retry/signing/OAuth state stays unchanged.
    """
    def __init__(self, store, loop, task_id):
        self.store, self.loop, self.task_id = store, loop, task_id
        if not callable(getattr(store, 'active_subscriptions', None)) or not callable(getattr(store, 'enqueue_delivery', None)):
            raise DialogError('git_event_native_binding_required')
        self.thread = threading.get_ident()

    async def deliver(self, eid, value, digest):
        if threading.get_ident() != self.thread or not _digest(digest):
            raise DialogError('git_event_delivery_thread_rejected')
        # A None pin is reserved for MultiDialogNativeDelivery, where the
        # task_id has already been validated against the registered append row.
        task_id = value.get('task_id') if self.task_id is None and type(value) is dict else self.task_id
        value = metadata(value, task_id)
        if eid != event_id(value): raise DialogError('git_event_metadata_rejected')
        # active_subscriptions is intrinsically active/unexpired, but NOT owner
        # filtered. Never forward its URL/secret/arguments values to our journal.
        targets = []
        for sub in self.store.active_subscriptions():
            if not isinstance(sub, dict): raise DialogError('git_event_subscription_rejected')
            args = sub.get('arguments')
            if sub.get('event') != EVENT or not isinstance(args, dict) or args.get('queue') != 'test':
                continue
            try: owned = owner_digest(sub.get('owner')) == digest
            except DialogError: owned = False
            if owned:
                sid = sub.get('id')
                if not isinstance(sid, str) or not sid: raise DialogError('git_event_subscription_rejected')
                targets.append(sid)
        # No matching current subscription is NOT success: retain for next
        # reconciliation. No conversation Queue job or forwarder.offer call.
        if not targets: return False
        for sid in targets:
            result = self.store.enqueue_delivery(sid, eid, dict(value))
            if type(result) is not int or result < 0:
                raise DialogError('git_event_enqueue_unconfirmed')
        return True

    def submit(self, eid, value, digest):
        return asyncio.run_coroutine_threadsafe(self.deliver(eid, value, digest), self.loop)


class NotificationPump:
    """Gateway-thread scan and cross-loop enqueue; no blocking on the ASGI loop."""
    def __init__(self, journal, appends, dialog, delivery, *, clock=time.monotonic):
        self.journal, self.appends, self.dialog, self.delivery = journal, appends, dialog, delivery
        self.clock, self.next_scan, self.next_send, self.cursor = clock, 0, 0, 0
        self.inflight = None
        self.error = None
        self.revision = -1

    def scan(self):
        return self.appends.notification_page(
            self.dialog, self.journal.task_id,
            after_row=self.cursor, after_seq=self.journal.checkpoint_seq)

    def tick(self):
        now = self.clock()
        revision = getattr(getattr(self.appends, 'conn', None), 'total_changes', 0)
        if revision != self.revision:
            self.cursor, self.next_scan = 0, 0
        if self.inflight is not None:
            eid, future = self.inflight
            if future.done():
                try:
                    if future.result() is True: self.journal.acknowledge(eid)
                    self.error = None
                except Exception:
                    # Exact event may be replayed only into native's UNIQUE
                    # delivery transport. NEVER a Git publication retry.
                    self.error = 'git_event_enqueue_unconfirmed'
                self.inflight = None
                self.next_send = now + 30
        if now >= self.next_scan:
            try:
                self.cursor, values = self.scan()
                for value in values: self.journal.record(value)
                self.next_scan = now + 30 if self.cursor == 0 else now + 1
            except Exception:
                # Notification failure never changes the committed Git result
                # or kills the authority. Retain the durable source/outbox.
                self.error, self.next_scan = 'git_event_reconciliation_unconfirmed', now + 30
            self.revision = getattr(getattr(self.appends, 'conn', None), 'total_changes', 0)
        if self.inflight is None and self.journal.owner_hash and now >= self.next_send:
            try:
                pending = self.journal.pending()
                if pending:
                    eid, value = pending
                    self.inflight = eid, self.delivery.submit(eid, value, self.journal.owner_hash)
            except Exception:
                self.error, self.next_send = 'git_event_enqueue_unconfirmed', now + 30

    def close(self):
        # Cancelling a scheduling future does NOT undo a completed enqueue.
        # Journal remains unacknowledged and native uniqueness makes recovery
        # safe if enqueue won the race; do not claim remote cancellation.
        if self.inflight is not None: self.inflight[1].cancel()
