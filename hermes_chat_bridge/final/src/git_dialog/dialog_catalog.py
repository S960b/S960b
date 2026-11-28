"""Explicit, owner-scoped dialogues; no shared mutable 'current chat'.

Both already-authenticated peers can create an isolated root branch. An intent
is consumed durably BEFORE Git writes; uncertain creation is reconciled using
GET only. Request IDs are per chat, not per message. No chat ID is invented from
OAuth identity (one owner may have several concurrent ChatGPT tabs).
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import ACTORS, DialogError, branch_ref, validate_message_id, validate_task_id
from .authority import CommonAuthority
from .gateway import panel_view
from .github import sha
from .journal_probe import _db_file

APPLICATION_ID = 0x47444352
PAGE_BYTES = 256 * 1024


def creation_identity(actor, request_id, title):
    if type(actor) is not str or actor not in ACTORS:
        raise DialogError('creation_actor_denied')
    validate_message_id(request_id)
    if (type(title) is not str or not title.strip() or len(title) > 256 or
            any(ord(c) < 32 or ord(c) == 127 for c in title)):
        raise DialogError('invalid_dialog_title')
    try:
        if len(title.encode('utf-8')) > 1024:
            raise DialogError('invalid_dialog_title')
    except UnicodeError:
        raise DialogError('invalid_dialog_title') from None
    digest = hashlib.sha256(('git-dialog-create-v1\0' + actor + '\0' + request_id).encode()).hexdigest()
    return 'task_' + digest[:32]


class CreationStore:
    """Dedicated private intent metadata. Existing lifecycle/append DBs unchanged.

    Serialized on the native gateway's existing SQLite-owner thread. No raw
    prompts, credentials, chat sessions, Queue jobs or execution results here.
    """
    def __init__(self, path):
        path = Path(path)
        existed = path.exists() or path.is_symlink()
        _db_file(path, create=not existed)
        if path.lstat().st_nlink != 1:
            raise DialogError('creation_store_rejected')
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute('PRAGMA synchronous=FULL')
            with self.tx():
                app = self.conn.execute('PRAGMA application_id').fetchone()[0]
                tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if existed:
                    if app != APPLICATION_ID or tables != {'creations'}:
                        raise DialogError('creation_store_rejected')
                    columns = [r[1] for r in self.conn.execute('PRAGMA table_info(creations)')]
                    if columns != ['actor', 'request_id', 'task_id', 'title', 'attempted', 'candidate', 'confirmed']:
                        raise DialogError('creation_store_rejected')
                else:
                    self.conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    self.conn.execute('''CREATE TABLE creations (
                        actor TEXT NOT NULL CHECK(actor IN ('chatgpt','mt')),
                        request_id TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                        attempted INTEGER NOT NULL DEFAULT 0 CHECK(attempted IN(0,1)),
                        candidate TEXT, confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN(0,1)),
                        PRIMARY KEY(actor,request_id))''')
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

    def get(self, actor, request_id):
        if type(actor) is not str or actor not in ACTORS:
            raise DialogError('creation_actor_denied')
        validate_message_id(request_id)
        row = self.conn.execute('SELECT * FROM creations WHERE actor=? AND request_id=?',
                                (actor, request_id)).fetchone()
        if row is None:
            raise DialogError('creation_not_found')
        row = dict(row)
        if (row['task_id'] != creation_identity(actor, request_id, row['title']) or
                row['attempted'] not in (0, 1) or row['confirmed'] not in (0, 1) or
                ((row['candidate'] is not None or row['confirmed']) and not row['attempted']) or
                (row['confirmed'] and row['candidate'] is None)):
            raise DialogError('creation_store_corrupt')
        if row['candidate'] is not None: sha(row['candidate'])
        return row

    def reserve(self, actor, request_id, title):
        task_id = creation_identity(actor, request_id, title)
        with self.tx():
            inserted = self.conn.execute('INSERT OR IGNORE INTO creations(actor,request_id,task_id,title) '
                'VALUES(?,?,?,?)', (actor, request_id, task_id, title)).rowcount == 1
        row = self.get(actor, request_id)
        if row['title'] != title: raise DialogError('creation_request_conflict')
        return row, inserted

    def start(self, row):
        with self.tx():
            changed = self.conn.execute('UPDATE creations SET attempted=1 WHERE actor=? AND request_id=? '
                'AND attempted=0', (row['actor'], row['request_id'])).rowcount
        if changed != 1: raise DialogError('creation_unconfirmed')

    def candidate(self, row, head):
        sha(head)
        with self.tx():
            changed = self.conn.execute('UPDATE creations SET candidate=? WHERE actor=? AND request_id=? '
                'AND attempted=1 AND candidate IS NULL', (head, row['actor'], row['request_id'])).rowcount
        if changed != 1: raise DialogError('creation_unconfirmed')

    def confirm(self, row):
        with self.tx():
            self.conn.execute('UPDATE creations SET confirmed=1 WHERE actor=? AND request_id=? '
                'AND attempted=1 AND candidate IS NOT NULL', (row['actor'], row['request_id']))


class DialogCatalog:
    """One repository/owner, explicit registered task selection for every call.

    The caller supplies the peer actor ONLY after existing OAuth/Unix auth, not
    from tool arguments. Selecting a task is not selecting an identity, repo,
    filesystem path, credential or executor. No implicit pilot fallback.
    """
    def __init__(self, dialog, registry, appends, creations):
        if type(creations) is not CreationStore or dialog.repository != 'S960b/agent-dialogs':
            raise DialogError('catalog_binding_rejected')
        self.dialog, self.registry, self.appends, self.creations = dialog, registry, appends, creations

    def _common(self, task_id, *, writing=False):
        validate_task_id(task_id)
        # Only explicit creation can register new tasks. Missing/retired ref is
        # never an invitation to search, create, adopt or resurrect a branch.
        if writing and self.registry.get(task_id)['state'] != 'active':
            raise DialogError('dialog_not_writable')
        return CommonAuthority(self.dialog, self.registry, self.appends, task_id)

    @staticmethod
    def _receipt(row, snapshot=None, *, created=False):
        return {'request_id': row['request_id'], 'task_id': row['task_id'],
            'branch': branch_ref(row['task_id']), 'initiator': row['actor'],
            'status': 'confirmed' if snapshot is not None else 'unconfirmed',
            'head': snapshot.head if snapshot is not None else None,
            'waiting_for': snapshot.state.next_actor if snapshot is not None else None,
            'created': created, 'retry_authorized': False}

    def create(self, actor, request_id, title):
        row, inserted = self.creations.reserve(actor, request_id, title)
        if not inserted:
            # Even a call repeated after restart is reconciliation ONLY.
            return self.creation_status(actor, request_id)
        try:
            self.registry.get(row['task_id'])
        except DialogError as exc:
            if exc.code != 'unregistered_dialog': raise
        else:
            raise DialogError('task_id_retired_or_registered')
        self.creations.start(row)
        candidate = self.dialog.prepare_create(row['task_id'], title, actor, request_id)
        self.creations.candidate(row, candidate)
        snapshot = self.dialog.publish_create(row['task_id'], candidate, title, actor, request_id)
        # Local activation only after independent exact remote verification.
        self.registry.register(row['task_id'], candidate)
        row = self.creations.get(actor, request_id)
        self.creations.confirm(row)
        return self._receipt(row, snapshot, created=True)

    def creation_status(self, actor, request_id):
        row = self.creations.get(actor, request_id)
        if row['candidate'] is None: return self._receipt(row)
        snapshot = self.dialog.observe_creation(row['task_id'], row['candidate'],
            row['title'], actor, request_id)
        self.registry.register(row['task_id'], row['candidate'])
        self.creations.confirm(row)
        return self._receipt(row, snapshot)

    def list(self, *, offset=0, limit=20):
        rows, next_offset = self.registry.page(offset=offset, limit=limit)
        result = []
        for row in rows:
            saved = self.creations.conn.execute('SELECT title,actor FROM creations '
                'WHERE task_id=? AND confirmed=1', (row['task_id'],)).fetchone()
            result.append({'task_id': row['task_id'], 'branch': branch_ref(row['task_id']),
                'lifecycle': row['state'], 'title': saved['title'] if saved else None,
                'initiator': saved['actor'] if saved else None})
        return {'dialogs': result, 'next_offset': next_offset, 'order': 'newest'}

    def view(self, task_id, *, offset=0, limit=20, order='newest'):
        if (type(offset) is not int or offset < 0 or type(limit) is not int or
                not 1 <= limit <= 100 or type(order) is not str or order not in ('newest', 'oldest')):
            raise DialogError('invalid_dialog_page')
        snapshot = self._common(task_id).gateway.load(task_id)
        result = panel_view(snapshot, offset=0, limit=1)
        source = snapshot.messages if order == 'oldest' else list(reversed(snapshot.messages))
        page, size = [], 0
        for message in source[offset:offset + limit]:
            value = message.to_dict()
            # Count the actual ASCII-escaped IPC representation, not only raw
            # UTF-8, so large Cyrillic/emoji pages remain below MAX_RESPONSE.
            size += len(json.dumps(value, ensure_ascii=True).encode('ascii'))
            if size > PAGE_BYTES: break
            page.append(value)
        end = offset + len(page)
        withdrawn = {m.body[len('OWNER_RETURN_LAST:'):] for m in snapshot.messages
                     if m.kind == 'return'}
        for item in page:
            item['returned'] = item['message_id'] in withdrawn
        result.update(history=page, next_offset=end if end < len(source) else None,
                      order=order, branch=branch_ref(task_id), lifecycle=self.registry.get(task_id)['state'],
                      active_message_id=(snapshot.last.message_id if snapshot.last
                                         and snapshot.last.kind != 'return' else None),
                      awaiting_replacement=bool(snapshot.last and snapshot.last.kind == 'return'))
        return result

    def health(self, task_id, *, last_error_code=None):
        """Bounded read-only state for one registered dialogue.

        last_error_code is a sanitized process-local diagnostic supplied only
        by the authenticated authority. No message bodies, credentials, or
        arbitrary exception details are included.
        """
        if last_error_code is not None:
            from .p31_errors import is_public
            if not is_public(last_error_code):
                raise DialogError('catalog_request_rejected')
        snapshot = self._common(task_id).gateway.load(task_id)
        panel = panel_view(snapshot, offset=0, limit=1)
        return {
            'task_id': task_id,
            'head': snapshot.head,
            'waiting_for': panel['waiting_for'],
            'status': panel['status'],
            'stalled': panel['stalled'],
            'seconds_since_message': panel['seconds_since_message'],
            'last_message_at': panel['last_message_at'],
            'last_error_code': last_error_code,
        }

    def index(self, task_id, *, offset=0, limit=20, order="newest"):
        """P36 metadata-only index. No message bodies cross the MCP boundary."""
        if (type(offset) is not int or offset < 0 or type(limit) is not int or
                not 1 <= limit <= 100 or order not in ("newest", "oldest")):
            raise DialogError("invalid_dialog_page")
        snap = self._common(task_id).gateway.load(task_id)
        source = snap.messages if order == "oldest" else list(reversed(snap.messages))
        page = [{"seq": m.seq, "message_id": m.message_id, "kind": m.kind,
                 "from": m.sender, "to": m.recipient}
                for m in source[offset:offset + limit]]
        end = offset + len(page)
        return {"task_id": task_id, "head": snap.head, "seq": snap.state.seq,
                "status": snap.state.status, "waiting_for": snap.state.next_actor,
                "messages": page, "order": order,
                "next_offset": end if end < len(source) else None}

    def notice(self, actor, task_id, head, message_id, seq):
        """Verify an independently published Git commit; NEVER write a body."""
        from .github import sha
        validate_task_id(task_id)
        validate_message_id(message_id)
        sha(head)
        if type(seq) is not int or seq < 1:
            raise DialogError("catalog_request_rejected")
        if self.registry.get(task_id)["state"] != "active":
            raise DialogError("dialog_not_writable")
        current = self.dialog.load(task_id)
        if seq > current.state.seq:
            raise DialogError("direct_notice_unconfirmed")
        candidate = self.dialog._snapshot(task_id, head)[0]
        if (candidate.state.seq != seq or candidate.last is None or
                candidate.last.message_id != message_id or
                candidate.last.sender != actor or candidate.last.kind == "return" or
                current.messages[seq - 1] != candidate.last):
            raise DialogError("direct_notice_unconfirmed")
        # Ensure the claimed commit remains an ancestor of the live ref.
        commit = current.head
        for _ in range(current.state.seq - seq + 1):
            if commit == head:
                break
            parents = self.dialog._commit(commit).get("parents")
            if (type(parents) is not list or len(parents) != 1):
                raise DialogError("direct_notice_unconfirmed")
            commit = sha(parents[0].get("sha"))
        if commit != head:
            raise DialogError("direct_notice_unconfirmed")
        return {"task_id": task_id, "head": head,
                "message_id": message_id, "seq": seq, "observed": True}

    def append(self, actor, task_id, *, head, message_id, kind, body):
        receipt = self._common(task_id, writing=True).port(actor).append(task_id, head,
            message_id=message_id, actor=actor, kind=kind, body=body)
        return {'message_id': receipt.message.message_id, 'commit': receipt.commit_sha,
                'created': receipt.created}

    def return_turn(self, task_id, expected_head, last_message_id):
        """Return the last active peer turn. Called ONLY after owner auth."""
        sha(expected_head)
        validate_message_id(last_message_id)
        common = self._common(task_id, writing=True)
        snapshot = common.gateway.load(task_id)
        if snapshot.head != expected_head:
            raise DialogError('stale_head')
        last = snapshot.last
        if (last is None or last.message_id != last_message_id or
                last.kind in ('return', 'close') or
                snapshot.state.status in ('done', 'closing')):
            raise DialogError('return_unavailable')
        seq = snapshot.state.seq + 1
        result = common.gateway._append(
            task_id, expected_head, message_id=f'owner_return_{seq}',
            actor=snapshot.state.next_actor, kind='return',
            body='OWNER_RETURN_LAST:' + last_message_id)
        return {'task_id': task_id, 'head': result.commit_sha,
                'returned_message_id': last_message_id,
                'waiting_for': last.sender, 'created': result.created}

    def delete_prepare(self, task_id, expected_head):
        """Start authenticated, HEAD-pinned Git branch removal (short nonce)."""
        from .github import sha
        validate_task_id(task_id)
        sha(expected_head)
        if self.registry.get(task_id)['state'] not in ('active', 'cancelled'):
            raise DialogError('dialog_not_writable')
        self.dialog._private()
        if self.dialog._head(task_id) != expected_head:
            raise DialogError('stale_head')
        return self.registry.request_delete(task_id, expected_head)

    def delete_confirm(self, task_id, expected_head, confirmation):
        """Tombstone before remote delete. Unknown result is NEVER retried."""
        from .github import sha
        validate_task_id(task_id)
        sha(expected_head)
        self.dialog._private()
        if self.dialog._head(task_id) != expected_head:
            raise DialogError('delete_head_changed')
        self.registry.confirm_delete(task_id, confirmation, expected_head)
        if self.dialog.delete_branch(task_id, expected_head) is not True:
            raise DialogError('delete_unconfirmed')
        self.registry.record_remote_absent(task_id)
        return {'task_id': task_id, 'head': expected_head,
                'deleted': True, 'remote_absent': True}

    def call(self, actor, obj):
        if type(actor) is not str or actor not in ACTORS or type(obj) is not dict:
            raise DialogError('catalog_request_rejected')
        fields = {
            'create_dialog': {'op', 'request_id', 'title'},
            'creation_status': {'op', 'request_id'},
            'list_dialogs': {'op', 'offset', 'limit'},
            'view': {'op', 'task_id', 'offset', 'limit', 'order'},
            'index': {'op', 'task_id', 'offset', 'limit', 'order'},
            'notice': {'op', 'task_id', 'head', 'message_id', 'seq'},
            'health': {'op', 'task_id'},
            'delete_prepare': {'op', 'task_id', 'head'},
            'delete_confirm': {'op', 'task_id', 'head', 'confirmation'},
            'append': {'op', 'task_id', 'head', 'message_id', 'kind', 'body'},
            'writer_status': {'op', 'task_id', 'message_id'},
        }
        op = obj.get('op')
        if type(op) is not str or op not in fields or set(obj) != fields[op]:
            raise DialogError('catalog_request_rejected')
        if op == 'create_dialog': return self.create(actor, obj['request_id'], obj['title'])
        if op == 'creation_status': return self.creation_status(actor, obj['request_id'])
        if op == 'list_dialogs': return self.list(offset=obj['offset'], limit=obj['limit'])
        if op == 'writer_status':
            if actor != 'mt': raise DialogError('catalog_request_rejected')
            self._common(obj['task_id'])
            return self.appends.status(obj['task_id'], obj['message_id'])
        if op in ('delete_prepare', 'delete_confirm'):
            if actor != 'mt':
                raise DialogError('catalog_request_rejected')
            if op == 'delete_prepare':
                return self.delete_prepare(obj['task_id'], obj['head'])
            return self.delete_confirm(obj['task_id'], obj['head'], obj['confirmation'])
        if op == 'health':
            return self.health(obj['task_id'])
        if op == 'view':
            return self.view(obj['task_id'], offset=obj['offset'], limit=obj['limit'], order=obj['order'])
        if op == 'index':
            return self.index(obj['task_id'], offset=obj['offset'], limit=obj['limit'], order=obj['order'])
        if op == 'notice':
            return self.notice(actor, obj['task_id'], obj['head'], obj['message_id'], obj['seq'])
        return self.append(actor, obj['task_id'], head=obj['head'], message_id=obj['message_id'],
                           kind=obj['kind'], body=obj['body'])
