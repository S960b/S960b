import json
import os
import stat
from types import SimpleNamespace

import pytest

from git_dialog import DialogError
from git_dialog import unix_authority as module
from test_unix_authority import Channel, runtime


@pytest.mark.parametrize('phase', [
    'directory', 'socket_metadata', 'socket_create', 'connect', 'peer_identity',
    'send', 'receive', 'decode', 'envelope', 'result', 'receipt',
    'remote_peer_identity', 'remote_peer_policy', 'remote_request_frame', 'remote_dispatch',
])
def test_client_reports_static_stage_only_and_never_retries(tmp_path, monkeypatch, phase):
    calls = []
    def fail():
        raise RuntimeError('SENSITIVE_EXCEPTION_TEXT')
    def directory(path):
        if phase == 'directory': fail()
        return path
    monkeypatch.setattr(module, '_directory', directory)
    path = tmp_path / 'private.sock'
    monkeypatch.setattr(type(path), 'lstat', lambda p: SimpleNamespace(
        st_mode=stat.S_IFSOCK | (0o644 if phase == 'socket_metadata' else 0o600),
        st_uid=os.getuid(), st_nlink=1))
    class Connection:
        def settimeout(self, seconds): assert seconds == module.CONNECT_SECONDS
        def connect(self, value):
            calls.append('connect')
            if phase == 'connect': fail()
        def sendall(self, value):
            calls.append('send')
            if phase == 'send': fail()
        def close(self): calls.append('close')
    conn = Connection()
    def make(*args):
        if phase == 'socket_create': fail()
        return conn
    monkeypatch.setattr(module.socket, 'socket', make)
    def peer(value):
        if phase == 'peer_identity': fail()
        return 201, os.getuid()
    monkeypatch.setattr(module, 'peer_identity', peer)
    def frame(*args):
        calls.append('receive')
        if phase == 'receive': fail()
        if phase == 'decode': return b'\xff'
        if phase == 'envelope': return b'{"error":"SENSITIVE_WIRE_TEXT"}'
        if phase.startswith('remote_'):
            return json.dumps({'ok': False, 'result': {
                'error': 'authority_result_unconfirmed', 'retry_authorized': False,
                'phase': phase[len('remote_'):]}}).encode()
        return json.dumps({'ok': True, 'result': (
            {'error': 'SENSITIVE_WIRE_TEXT'} if phase == 'result' else
            {'body': 'SENSITIVE_WIRE_TEXT'})}).encode()
    monkeypatch.setattr(module, '_frame', frame)
    with pytest.raises(DialogError) as exc:
        module.UnixClient(path).call({'op': 'append', 'head': 'a'*40,
                                     'message_id': 'sample', 'kind': 'task', 'body': 'inert'})
    assert exc.value.code == 'authority_result_unconfirmed'
    assert exc.value.detail == phase
    assert str(exc.value) == 'authority_result_unconfirmed: ' + phase
    assert calls.count('connect') <= 1 and calls.count('send') <= 1
    assert calls.count('receive') <= 1
    if phase not in ('directory', 'socket_metadata', 'socket_create'):
        assert calls[-1] == 'close'


@pytest.mark.parametrize('remote_phase', ['SENSITIVE_WIRE_TEXT', {'private': 'data'}, None])
def test_client_never_reflects_unknown_remote_diagnostic(tmp_path, monkeypatch, remote_phase):
    path = tmp_path / 'socket'
    monkeypatch.setattr(module, '_directory', lambda p: p)
    monkeypatch.setattr(type(path), 'lstat', lambda p: SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600, st_uid=os.getuid(), st_nlink=1))
    class Connection:
        def settimeout(self, value): pass
        def connect(self, value): pass
        def sendall(self, value): pass
        def close(self): pass
    monkeypatch.setattr(module.socket, 'socket', lambda *args: Connection())
    monkeypatch.setattr(module, 'peer_identity', lambda c: (201, os.getuid()))
    monkeypatch.setattr(module, '_frame', lambda *args: json.dumps({
        'ok': False, 'result': {'error': 'authority_result_unconfirmed',
                              'retry_authorized': False, 'phase': remote_phase}}).encode())
    with pytest.raises(DialogError) as exc:
        module.UnixClient(path).call({'op': 'view', 'offset': 19, 'limit': 1})
    assert str(exc.value) == 'authority_result_unconfirmed: envelope'


@pytest.mark.parametrize('phase', ['peer_identity', 'peer_policy', 'request_frame', 'dispatch'])
def test_server_stage_does_not_expose_exception_identity_or_body(runtime, monkeypatch, phase):
    api, _, _, host = runtime
    conn = Channel({'op': 'view', 'offset': 19, 'limit': 1}, pid=102)
    def fail(*args): raise RuntimeError('SENSITIVE_EXCEPTION_TEXT')
    if phase == 'peer_identity': monkeypatch.setattr(module, 'peer_identity', fail)
    elif phase == 'peer_policy': conn.pid = 101
    elif phase == 'request_frame': monkeypatch.setattr(module, '_frame', fail)
    else: monkeypatch.setattr(host, 'call', fail)
    host.handle('mt', conn)
    assert conn.result() == {'ok': False, 'result': {
        'error': 'authority_result_unconfirmed', 'retry_authorized': False, 'phase': phase}}
    assert not api.calls
