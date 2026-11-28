"""Single-owner shared gateway runtime; approved local-UID CLI binding.

Two private Unix endpoints, one CommonAuthority and serving thread. MCP peers
must match the explicitly configured bridge PID+UID; the reviewed MCP handler
checks OAuth owner first. The CLI endpoint accepts the configured local owner
UID, not the bridge PID. This is a cooperating/local-owner policy, NOT a sandbox
against compromised owner processes, PID reuse or external Git credentials.

No Queue/bridge DB, key reuse, env/credential discovery, remote bootstrap,
branch deletion, code execution or automatic retry/recovery. New-dialog creation
is a separate explicit catalogue capability, never a missing-ref fallback.
"""
import argparse
from collections import OrderedDict
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import struct
import sys
import threading
import time

from . import DialogError, validate_message_id, validate_task_id
from .authority import CommonAuthority, WRITERS
from .gateway_adapters import _wire
from .github import decode_json
from .ingress import AccessAuthority
from .p31_errors import public_code, is_public
from .socket_recovery import SocketStartupGuard

STATE = Path('/home/kali/agent-dialog-beta/state')
SOCKETS = {'chatgpt': STATE / 'authority-cg.sock', 'mt': STATE / 'authority-mt.sock'}
MAX_RESPONSE = 512 * 1024
# Connection/upload remain short; a validated Git history requires several
# sequential HTTP reads. This is a bounded IPC wait, NOT a retry or a promise
# that the remote operation stops when the client stops waiting.
CONNECT_SECONDS = 5
RESPONSE_SECONDS = 120
REQUEST_OPS = ('view', 'append', 'index', 'notice', 'canary_report', 'worker_step', 'writer_status',
               'create_dialog', 'creation_status', 'list_dialogs', 'health',
               'delete_prepare', 'delete_confirm', 'return_turn')
# Static diagnostic categories only: never reflect exception text, request data,
# identities, socket paths, Git responses or credentials into MCP errors.
SERVER_PHASES = frozenset(('peer_identity', 'peer_policy', 'request_frame', 'dispatch'))


def _directory(path):
    path = Path(path)
    info = path.lstat()
    if (not path.is_absolute() or not stat.S_ISDIR(info.st_mode) or
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise DialogError('authority_directory_rejected')
    # Reject symlink/group-writable ancestors, without listing or reading them.
    for ancestor in path.parents:
        item = ancestor.lstat()
        if (not stat.S_ISDIR(item.st_mode) or
                (stat.S_IMODE(item.st_mode) & 0o022 and not (
                    item.st_uid == 0 and stat.S_IMODE(item.st_mode) & stat.S_ISVTX))):
            raise DialogError('authority_directory_rejected')
    return path


def _file(path):
    path = Path(path)
    _directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise DialogError('authority_file_rejected')
    return path


def _frame(conn, maximum, seconds=2):
    deadline = time.monotonic() + seconds
    def exact(size):
        result = b''
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DialogError('authority_frame_rejected')
            conn.settimeout(remaining)
            part = conn.recv(size - len(result))
            if not part:
                raise DialogError('authority_frame_rejected')
            result += part
        return result
    size = struct.unpack('!I', exact(4))[0]
    if not 0 < size <= maximum:
        raise DialogError('authority_frame_rejected')
    return exact(size)


def _send(conn, raw):
    conn.sendall(struct.pack('!I', len(raw)) + raw)


def peer_identity(conn):
    if not hasattr(socket, 'SO_PEERCRED'):
        raise DialogError('authority_peer_unavailable')
    pid, uid, gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if pid <= 0 or uid < 0:
        raise DialogError('authority_peer_unavailable')
    return pid, uid


class UnixClient:
    def __init__(self, path):
        # Path is trusted host/CLI configuration, never request/body-selected.
        self.path = Path(path)

    def call(self, request):
        if type(request) is not dict or request.get('op') not in REQUEST_OPS:
            raise DialogError('authority_request_rejected')
        raw = _wire(request)
        conn = None
        phase = 'directory'
        remote_code = None
        try:
            _directory(self.path.parent)
            phase = 'socket_metadata'
            info = self.path.lstat()
            if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise DialogError('authority_socket_rejected')
            phase = 'socket_create'
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            phase = 'connect'
            conn.settimeout(CONNECT_SECONDS)
            conn.connect(str(self.path))
            phase = 'peer_identity'
            if peer_identity(conn)[1] != os.getuid():
                raise DialogError('authority_peer_denied')
            phase = 'send'
            _send(conn, raw)
            phase = 'receive'
            response = _frame(conn, MAX_RESPONSE, RESPONSE_SECONDS)
            phase = 'decode'
            value = decode_json(response.decode('utf-8'))
            phase = 'envelope'
            if type(value) is not dict or set(value) != {'ok', 'result'} or value['ok'] is not True:
                if (type(value) is dict and set(value) == {'ok', 'result'} and
                        value['ok'] is False and type(value['result']) is dict and
                        set(value['result']) in ({'error', 'retry_authorized', 'phase'},
                                                {'error', 'retry_authorized', 'phase', 'code'}) and
                        value['result'].get('error') == 'authority_result_unconfirmed' and
                        value['result'].get('retry_authorized') is False and
                        type(value['result'].get('phase')) is str and
                        value['result']['phase'] in SERVER_PHASES):
                    phase = 'remote_' + value['result']['phase']
                    if ('code' in value['result'] and phase == 'remote_dispatch' and
                            is_public(value['result']['code'])):
                        remote_code = value['result']['code']
                raise DialogError('authority_result_unconfirmed')
            phase = 'result'
            result = value['result']
            if type(result) is not dict or 'error' in result:
                raise DialogError('authority_result_unconfirmed')
            phase = 'receipt'
            if request['op'] in ('append', 'canary_report'):
                from .github import sha
                if (set(result) != {'message_id', 'commit', 'created'} or
                        result['message_id'] != request.get('message_id') or type(result['created']) is not bool):
                    raise DialogError('authority_result_unconfirmed')
                sha(result['commit'])
            elif request['op'] == 'worker_step':
                from .github import sha
                if (set(result) != {'task_id', 'request_id', 'report_id', 'status', 'confirmed_commit', 'result'} or
                        result['request_id'] != request.get('request_id') or
                        result['report_id'] != request.get('report_id') or result['status'] != 'published'):
                    raise DialogError('authority_result_unconfirmed')
                sha(result['confirmed_commit'])
            elif request['op'] in ('delete_prepare', 'delete_confirm'):
                from .github import sha
                if (result.get('task_id') != request.get('task_id') or
                        result.get('head', result.get('expected_head')) != request.get('head')):
                    raise DialogError('authority_result_unconfirmed')
                if request['op'] == 'delete_prepare':
                    if (set(result) != {'task_id', 'confirmation', 'expected_head', 'expires_at', 'state'} or
                            type(result['confirmation']) is not str or
                            not 20 <= len(result['confirmation']) <= 128 or
                            result['state'] != 'closing' or
                            type(result['expires_at']) is not int):
                        raise DialogError('authority_result_unconfirmed')
                    sha(result['expected_head'])
                else:
                    if (set(result) != {'task_id', 'head', 'deleted', 'remote_absent'} or
                            result['deleted'] is not True or result['remote_absent'] is not True):
                        raise DialogError('authority_result_unconfirmed')
                    sha(result['head'])
            elif request['op'] == 'return_turn':
                from .github import sha
                if (set(result) != {'task_id', 'head', 'returned_message_id',
                                    'waiting_for', 'created'} or
                        result['task_id'] != request.get('task_id') or
                        result['returned_message_id'] != request.get('last_message_id') or
                        result['waiting_for'] not in ('mt', 'chatgpt') or
                        type(result['created']) is not bool):
                    raise DialogError('authority_result_unconfirmed')
                sha(result['head'])
            elif request['op'] == 'notice':
                from .github import sha
                from . import validate_message_id
                if (set(result) != {'task_id', 'head', 'message_id', 'seq', 'observed'} or
                        result['task_id'] != request.get('task_id') or
                        result['head'] != request.get('head') or
                        result['message_id'] != request.get('message_id') or
                        result['seq'] != request.get('seq') or
                        result['observed'] is not True):
                    raise DialogError('authority_result_unconfirmed')
                sha(result['head'])
                validate_message_id(result['message_id'])
            elif request['op'] == 'index':
                from .github import sha
                from . import STATUSES, validate_message_id
                expected = {'task_id','head','seq','status','waiting_for','messages',
                            'order','next_offset'}
                if (set(result) != expected or result['task_id'] != request.get('task_id') or
                        result['status'] not in STATUSES or
                        result['order'] != request.get('order') or
                        type(result['seq']) is not int or result['seq'] < 0 or
                        result['waiting_for'] not in ('chatgpt', 'mt') or
                        type(result['messages']) is not list or
                        len(result['messages']) > request.get('limit', 20)):
                    raise DialogError('authority_result_unconfirmed')
                sha(result['head'])
                for item in result['messages']:
                    if (type(item) is not dict or
                            set(item) != {'seq','message_id','kind','from','to'} or
                            type(item['seq']) is not int or item['seq'] < 1 or
                            item['from'] not in ('mt','chatgpt') or
                            item['to'] not in ('mt','chatgpt')):
                        raise DialogError('authority_result_unconfirmed')
                    validate_message_id(item['message_id'])
            elif request['op'] == 'health':
                from . import STATUSES, validate_timestamp
                from .github import sha
                expected = {'task_id', 'head', 'waiting_for', 'status',
                            'stalled', 'seconds_since_message', 'last_message_at',
                            'last_error_code'}
                if (set(result) != expected or result['task_id'] != request.get('task_id') or
                        result['status'] not in STATUSES or type(result['stalled']) is not bool or
                        result['waiting_for'] not in ('chatgpt', 'mt', None) or
                        result['last_error_code'] is not None and not is_public(result['last_error_code'])):
                    raise DialogError('authority_result_unconfirmed')
                sha(result['head'])
                age, stamp = result['seconds_since_message'], result['last_message_at']
                if (age is None) != (stamp is None):
                    raise DialogError('authority_result_unconfirmed')
                if age is not None:
                    if type(age) is not int or age < 0:
                        raise DialogError('authority_result_unconfirmed')
                    validate_timestamp(stamp)
                if (result['waiting_for'] is None) != (result['status'] in ('closing', 'done')):
                    raise DialogError('authority_result_unconfirmed')
            elif request['op'] in ('create_dialog', 'creation_status'):
                from .dialog_catalog import creation_identity
                from . import branch_ref
                from .github import sha
                if (set(result) != {'request_id', 'task_id', 'branch', 'initiator', 'status',
                        'head', 'waiting_for', 'created', 'retry_authorized'} or
                        result['request_id'] != request.get('request_id') or
                        result['retry_authorized'] is not False or type(result['created']) is not bool or
                        result['status'] not in ('confirmed', 'unconfirmed') or
                        result['initiator'] not in ('chatgpt', 'mt') or
                        result['task_id'] != creation_identity(result['initiator'], request['request_id'], 'check') or
                        result['branch'] != branch_ref(result['task_id'])):
                    raise DialogError('authority_result_unconfirmed')
                if result['status'] == 'confirmed':
                    sha(result['head'])
                    if result['waiting_for'] not in ('chatgpt', 'mt'):
                        raise DialogError('authority_result_unconfirmed')
                elif result['head'] is not None or result['waiting_for'] is not None or result['created']:
                    raise DialogError('authority_result_unconfirmed')
            return result
        except Exception:
            if remote_code is not None:
                raise DialogError(remote_code) from None
            raise DialogError('authority_result_unconfirmed', phase) from None
        finally:
            if conn is not None:
                conn.close()


class Runtime:
    """Construct/call/close on the SQLite owner thread. No external grant API."""
    def __init__(self, common, *, bridge_pid, uid=None, journal_factory=None,
                 catalog=None, notice_callback=None):
        if type(common) is not CommonAuthority or type(bridge_pid) is not int or bridge_pid <= 0:
            raise DialogError('authority_runtime_binding_rejected')
        if uid is not None and (type(uid) is not int or uid != os.getuid()):
            raise DialogError('authority_runtime_binding_rejected')
        self.common, self.bridge_pid, self.uid = common, bridge_pid, os.getuid()
        self.thread = threading.current_thread()
        if journal_factory is not None and not callable(journal_factory):
            raise DialogError('authority_worker_binding_rejected')
        self.journal_factory, self.journal = journal_factory, None
        if catalog is not None:
            from .dialog_catalog import DialogCatalog
            if (type(catalog) is not DialogCatalog or catalog.registry is not common.registry or
                    catalog.appends is not common.store or catalog.dialog is not common.gateway._dialog.dialog):
                raise DialogError('catalog_binding_rejected')
        if notice_callback is not None and not callable(notice_callback):
            raise DialogError('authority_runtime_binding_rejected')
        self.catalog, self.notice_callback = catalog, notice_callback
        self._last_errors = OrderedDict()  # Process-local, bounded, static code only.
        self._owner_auth = None  # Persistent limiter, initialized on first owner action.
        self.access = AccessAuthority({writer: {'role': 'agent', 'actor': actor}
                                      for writer, actor in WRITERS.items()})
        self.dispatcher = common.dispatcher(None, self.access, origin='http://127.0.0.1:18791', mutations=True)

    def call(self, writer, raw):
        if threading.current_thread() is not self.thread or type(writer) is not str or writer not in WRITERS:
            raise DialogError('authority_runtime_binding_rejected')
        # Issued only AFTER the trusted caller / verified Unix peer binding. The
        # opaque grant never leaves this process and is revoked on every result.
        obj = decode_json(raw.decode('utf-8'))
        if type(obj) is not dict or obj.get('op') not in REQUEST_OPS:
            raise DialogError('authority_request_rejected')
        if obj['op'] == 'return_turn':
            # The ordinary MT socket is intentionally reused; unlike a
            # writer=mt check, this validates the actual taskboard owner
            # password already supplied by the authenticated browser.
            if writer != 'mt' or self.catalog is None or set(obj) != {
                    'op', 'task_id', 'head', 'last_message_id', 'authorization'}:
                raise DialogError('owner_return_denied')
            from .service import AuthAuthority
            from .taskboard import STATE as TASKBOARD_AUTH_STATE
            if self._owner_auth is None:
                self._owner_auth = AuthAuthority(TASKBOARD_AUTH_STATE)
            if not self._owner_auth.verify(obj['authorization']):
                raise DialogError('owner_return_denied')
            return self.catalog.return_turn(
                obj['task_id'], obj['head'], obj['last_message_id'])
        if obj['op'] in ('health', 'delete_prepare', 'delete_confirm') and self.catalog is None:
            raise DialogError('catalog_binding_required')
        if self.catalog is not None and (obj['op'] in (
                'view', 'append', 'index', 'notice', 'create_dialog', 'creation_status', 'list_dialogs', 'health',
                'delete_prepare', 'delete_confirm') or
                (obj['op'] == 'writer_status' and 'task_id' in obj)):
            if writer not in ('chatgpt', 'mt'):
                raise DialogError('authority_writer_denied')
            if obj['op'] in ('delete_prepare', 'delete_confirm') and writer != 'mt':
                raise DialogError('authority_writer_denied')
            if obj['op'] == 'health':
                if set(obj) != {'op', 'task_id'}:
                    raise DialogError('catalog_request_rejected')
                validate_task_id(obj['task_id'])
                return self.catalog.health(obj['task_id'],
                    last_error_code=self._last_errors.get(obj['task_id']))
            try:
                result = self.catalog.call(writer, obj)
                if obj['op'] == 'notice' and self.notice_callback is not None:
                    self.notice_callback(result, writer)
                return result
            except Exception as exc:
                task_id = obj.get('task_id')
                try:
                    validate_task_id(task_id)
                except DialogError:
                    pass
                else:
                    code = public_code(exc) or 'authority_unavailable'
                    self._last_errors[task_id] = code
                    self._last_errors.move_to_end(task_id)
                    if len(self._last_errors) > 128:
                        self._last_errors.popitem(last=False)
                raise
        if obj['op'] in ('worker_step', 'canary_report', 'writer_status'):
            # Operation names select fixed capabilities, never an actor/task,
            # credential, path, evaluator or code supplied in request data.
            if writer != 'mt':
                raise DialogError('authority_writer_denied')
            return self._writer_call(obj)
        grant = self.access.issue(writer)
        try:
            headers = [('host', self.dispatcher.host), ('origin', self.dispatcher.origin),
                ('authorization', 'Bearer ' + grant['session']), ('x-csrf-token', grant['csrf']),
                ('content-type', 'application/json'), ('content-length', str(len(raw)))]
            return self.dispatcher.dispatch('POST', '/api/task', headers, raw)
        finally:
            self.access.revoke(grant['session'])

    def _writer_call(self, obj):
        op = obj['op']
        if op == 'canary_report':
            if set(obj) != {'op', 'head', 'message_id', 'body'}:
                raise DialogError('authority_request_rejected')
            receipt = self.common.port('canary_mt').append(self.common.task_id, obj['head'],
                message_id=obj['message_id'], actor='mt', kind='report', body=obj['body'])
            return {'message_id': obj['message_id'], 'commit': receipt.commit_sha, 'created': receipt.created}
        if op == 'writer_status':
            if set(obj) != {'op', 'message_id'}:
                raise DialogError('authority_request_rejected')
            return self.common.store.status(self.common.task_id, obj['message_id'])
        if set(obj) != {'op', 'request_id', 'report_id', 'run_id'}:
            raise DialogError('authority_request_rejected')
        for key in ('request_id', 'report_id', 'run_id'):
            validate_message_id(obj[key])
        if self.journal_factory is None:
            raise DialogError('authority_worker_binding_required')
        # Refuse arbitrary prose instead of consuming its turn with an automatic
        # refusal report. The worker remains a pure, explicit one-step facility.
        from .worker import validate_action
        with self.common.registry.writing(self.common.task_id):
            snapshot = self.common.port('worker').load(self.common.task_id)
        incoming = next((m for m in snapshot.messages if m.message_id == obj['request_id']), None)
        if incoming is None or incoming.kind != 'task' or incoming.recipient != 'mt':
            raise DialogError('not_executable_task')
        validate_action(incoming.body, ('ping', 'sha256'))
        if self.journal is None:
            self.journal = self.journal_factory()
        return self.common.worker(self.journal, allowed_ops=('ping', 'sha256')).step(
            obj['request_id'], obj['report_id'], obj['run_id'])

    def close(self):
        if threading.current_thread() is not self.thread:
            raise DialogError('authority_runtime_binding_rejected')
        if self.journal is not None:
            self.journal.close()
            self.journal = None

    def handle(self, channel, conn):
        phase = 'peer_identity'
        try:
            pid, uid = peer_identity(conn)
            phase = 'peer_policy'
            if (uid != self.uid or channel not in ('chatgpt', 'mt') or
                    (channel == 'chatgpt') != (pid == self.bridge_pid)):
                raise DialogError('authority_peer_denied')
            phase = 'request_frame'
            raw = _frame(conn, 8192)
            phase = 'dispatch'
            result = {'ok': True, 'result': self.call(channel, raw)}
        except Exception as exc:
            # Only narrow, known protocol errors during authorized dispatch are
            # exposed as static codes; peer/frame failures stay opaque.
            result = {'ok': False, 'result': {'error': 'authority_result_unconfirmed',
                                             'retry_authorized': False, 'phase': phase}}
            code = public_code(exc) if phase == 'dispatch' else None
            if code is not None:
                result['result']['code'] = code
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_RESPONSE:
            raw = b'{"ok":false,"result":{"error":"authority_result_unconfirmed","retry_authorized":false}}'
        _send(conn, raw)


class UnixServer:
    """Serial two-channel Unix authority, with fail-closed P33 stale recovery.

    A permanent-inode flock is held for the entire server lifetime. Recovery
    is only allowed after verifying the socket is owner-owned and unlistened;
    an uncertain, foreign or live socket remains untouched.
    """
    def __init__(self, runtime, paths=None):
        if type(runtime) is not Runtime:
            raise DialogError('authority_runtime_binding_rejected')
        self.runtime, self.paths = runtime, dict(SOCKETS if paths is None else paths)
        if set(self.paths) != {'chatgpt', 'mt'} or len({str(p) for p in self.paths.values()}) != 2:
            raise DialogError('authority_socket_binding_rejected')
        self.selector, self.owned = selectors.DefaultSelector(), []
        self.start_guard = None
        try:
            # Check BOTH owner-only directories before taking the lifetime
            # lock. The guard never removes its lock inode, even after a crash.
            for path in self.paths.values():
                _directory(Path(path).parent)
            self.start_guard = SocketStartupGuard(self.paths.values())
            self.start_guard.acquire()
            self.start_guard.recover()
            for channel, path in self.paths.items():
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    previous = os.umask(0o177)
                    try: sock.bind(str(path))
                    finally: os.umask(previous)
                    info = Path(path).lstat()
                    self.owned.append((sock, Path(path), info.st_dev, info.st_ino))
                    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != runtime.uid or
                            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                        raise DialogError('authority_socket_rejected')
                    sock.listen(4)
                    self.selector.register(sock, selectors.EVENT_READ, channel)
                except BaseException:
                    sock.close()
                    raise
        except BaseException:
            self.close()
            raise

    def serve_once(self, timeout=0.2):
        if threading.current_thread() is not self.runtime.thread:
            raise DialogError('authority_runtime_binding_rejected')
        for key, _ in self.selector.select(timeout):
            conn, _ = key.fileobj.accept()
            with conn:
                conn.settimeout(2)
                try: self.runtime.handle(key.data, conn)
                except (OSError, DialogError): pass  # No raw exception/log/body.

    def close(self):
        try:
            self.selector.close()
            for sock, path, dev, ino in self.owned:
                sock.close()
                try: info = path.lstat()
                except FileNotFoundError: continue
                if (info.st_dev, info.st_ino) == (dev, ino) and stat.S_ISSOCK(info.st_mode):
                    path.unlink()
            self.owned = []
        finally:
            # Release only AFTER the endpoints are closed and their own
            # socket inodes have been removed; never unlink the lock file.
            if self.start_guard is not None:
                self.start_guard.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit shared-authority local runtime')
    parser.add_argument('action', choices=('cli', 'check', 'serve'))
    parser.add_argument('--registry')
    parser.add_argument('--append-store')
    parser.add_argument('--bridge-pid', type=int)
    args = parser.parse_args(argv)
    registry = store = server = None
    phase = 'options'
    try:
        if args.action == 'cli':
            phase = 'cli_request'
            if any(v is not None for v in (args.registry, args.append_store, args.bridge_pid)):
                raise DialogError('authority_cli_options_rejected')
            raw = sys.stdin.buffer.read(8193)
            if len(raw) > 8192:
                raise DialogError('authority_request_rejected')
            result = UnixClient(SOCKETS['mt']).call(decode_json(raw.decode('utf-8')))
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0
        if not args.registry or not args.append_store or args.bridge_pid is None or args.bridge_pid <= 0:
            raise DialogError('authority_serve_options_required')
        phase = 'registry_metadata'
        _file(args.registry)  # MUST already exist; never reset/register a task.
        phase = 'append_directory'
        _directory(Path(args.append_store).parent)
        if Path(args.registry).resolve() == Path(args.append_store).resolve():
            raise DialogError('authority_store_alias_rejected')
        if args.action == 'check':
            phase = 'registry_state'
            import sqlite3
            from .lifecycle import APPLICATION_ID
            from .preflight import TASK_ID
            # Read-only connection policy: no registry constructor/schema write.
            conn = sqlite3.connect(args.registry)
            try:
                conn.execute('PRAGMA query_only=ON')
                if conn.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                    raise DialogError('authority_registry_rejected')
                row = conn.execute('SELECT state FROM dialogs WHERE task_id=?', (TASK_ID,)).fetchone()
                if row is None or row[0] != 'active':
                    raise DialogError('authority_registry_not_active')
            finally:
                conn.close()
            phase = 'destination_absence'
            for path in (*SOCKETS.values(), Path(args.append_store)):
                if path.exists() or path.is_symlink():
                    raise DialogError('authority_destination_occupied')
            print('{"status":"authority_checked","existing_registry_active":true,"writes_performed":false}')
            return 0
        from .service import _interactive_secret
        from .preflight import read_configured_token, TASK_ID
        from .github import GitHubDialog, JsonHttp
        from .lifecycle import LifecycleRegistry
        from .append_store import AppendStore
        phase = 'registry_state'
        registry = LifecycleRegistry(args.registry, existing=True)
        registry.get(TASK_ID)  # Existing registration, including retired states.
        # Normal reviewed credential loader only, private terminal input; never
        # argv/env/output/files/config discovery or cross-service extraction.
        phase = 'credential_loader'
        path = _interactive_secret('Уже настроенный PAT-файл, локально и скрыто: ')
        token = read_configured_token(path)
        del path
        dialog = GitHubDialog(JsonHttp(token, timeout=5))
        del token
        phase = 'git_snapshot'
        dialog.load(TASK_ID)  # Read-only startup verification, not a canary.
        phase = 'append_store'
        store = AppendStore(args.append_store)
        runtime = Runtime(CommonAuthority(dialog, registry, store, TASK_ID), bridge_pid=args.bridge_pid)
        phase = 'socket_bind'
        server = UnixServer(runtime)
        def terminate(*unused): raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, terminate)
        print('{"status":"authority_ready","delete_enabled":false,"external_writers_fenced":false}', flush=True)
        phase = 'serve'
        while True: server.serve_once()
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({'error':'authority_unavailable', 'action':args.action,
                          'phase':phase, 'retry_authorized':False}))
        return 1
    finally:
        if server is not None: server.close()
        if store is not None: store.close()
        if registry is not None: registry.close()


if __name__ == '__main__':
    raise SystemExit(main())
