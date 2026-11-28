import asyncio
import io
import json
import os
import socket
import stat
import struct
import threading
from types import SimpleNamespace

import pytest

from git_dialog import DialogError
from git_dialog.append_store import AppendStore
from git_dialog.authority import CommonAuthority
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.native_gateway import register_mcp
from git_dialog.unix_authority import Runtime, _frame, peer_identity
from test_github import FakeApi, TASK, writes


class Channel:
    def __init__(self, obj=None, *, pid=101, uid=None, raw=None):
        payload = json.dumps(obj).encode() if raw is None else raw
        self.input = io.BytesIO(struct.pack('!I', len(payload)) + payload)
        self.output = b''
        self.pid, self.uid = pid, os.getuid() if uid is None else uid
    def recv(self, count): return self.input.read(count)
    def sendall(self, data): self.output += data
    def settimeout(self, seconds): assert 0 < seconds <= 5
    def getsockopt(self, level, name, size):
        assert (level, name, size) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.pack('3i', self.pid, self.uid, 1000)
    def result(self):
        size = struct.unpack('!I', self.output[:4])[0]
        assert len(self.output[4:]) == size
        return json.loads(self.output[4:])


@pytest.fixture
def runtime(tmp_path):
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / 'lifecycle.sqlite')
    registry.register(TASK, api.initial)
    store = AppendStore(tmp_path / 'append.sqlite')
    common = CommonAuthority(GitHubDialog(api), registry, store, TASK)
    yield api, registry, store, Runtime(common, bridge_pid=101)
    store.close(); registry.close()


def append(head, mid='one', kind='task', body='data'):
    return {'op': 'append', 'head': head, 'message_id': mid, 'kind': kind, 'body': body}


def test_both_verified_peer_routes_share_store_and_no_grants_escape(runtime):
    api, _, store, host = runtime
    cg = Channel(append(api.initial))
    host.handle('chatgpt', cg)
    first = cg.result()['result']
    mt = Channel(append(first['commit'], 'two', 'report'), pid=102)
    host.handle('mt', mt)
    assert mt.result()['ok'] is True
    assert store.status(TASK, 'one')['confirmed_commit'] == first['commit']
    assert store.status(TASK, 'two')['confirmed_commit'] == mt.result()['result']['commit']
    assert host.access._grants == {}
    assert len([x for x in api.calls if x[0] == 'PATCH']) == 2


@pytest.mark.parametrize('channel,pid,uid', [('chatgpt', 102, None), ('mt', 101, None),
    ('chatgpt', 101, 1), ('mt', 102, 1), ('owner', 101, None)])
def test_peer_binding_denied_before_body_read_or_git(runtime, channel, pid, uid):
    api, _, _, host = runtime
    conn = Channel(append(api.initial), pid=pid, uid=uid)
    conn.recv = lambda _: (_ for _ in ()).throw(AssertionError('must authenticate before reading'))
    host.handle(channel, conn)
    assert conn.result()['ok'] is False and not api.calls


@pytest.mark.parametrize('extra', [{'writer': 'chatgpt'}, {'uid': 1000}, {'actor': 'chatgpt'},
                                  {'task_id': TASK}, {'principal': 'owner'}, {'op': 'confirm_close'}])
def test_body_cannot_select_identity_or_lifecycle(runtime, extra):
    api, _, _, host = runtime
    conn = Channel({**append(api.initial), **extra}, pid=102)
    host.handle('mt', conn)
    assert conn.result()['ok'] is False and not api.calls


@pytest.mark.parametrize('state', ['cancelled', 'closing', 'tombstoned'])
def test_lifecycle_blocks_both_peer_routes_before_network(runtime, state):
    api, registry, _, host = runtime
    if state == 'cancelled': registry.cancel(TASK)
    else:
        intent = registry.request_delete(TASK, api.initial)
        if state == 'tombstoned': registry.confirm_delete(TASK, intent['confirmation'], api.initial)
    for role, pid in [('chatgpt', 101), ('mt', 102)]:
        conn = Channel(append(api.initial), pid=pid)
        host.handle(role, conn)
        assert conn.result()['ok'] is False
    assert not api.calls


def test_unknown_after_effect_is_saved_and_no_resend_on_same_message(runtime):
    api, _, store, host = runtime
    original = api.request
    def lost(method, path, payload=None):
        value = original(method, path, payload)
        if method == 'PATCH': raise ApiError()
        return value
    api.request = lost
    first = Channel(append(api.initial))
    host.handle('chatgpt', first)
    assert first.result()['ok'] is False and store.status(TASK, 'one')['attempted']
    api.calls.clear()
    second = Channel(append(api.initial))
    host.handle('chatgpt', second)
    assert second.result()['ok'] is True and second.result()['result']['created'] is False
    assert not writes(api)


def test_runtime_rejects_other_serving_thread(runtime):
    api, _, _, host = runtime
    result = []
    def wrong_thread():
        try: host.call('chatgpt', json.dumps(append(api.initial)).encode())
        except DialogError as exc: result.append(exc.code)
    thread = threading.Thread(target=wrong_thread)
    thread.start(); thread.join(1)
    assert result == ['authority_runtime_binding_rejected'] and not api.calls


@pytest.mark.parametrize('raw', [b'', struct.pack('!I', 8193), struct.pack('!I', 3) + b'{}'])
def test_incomplete_or_oversize_frames_reject(raw):
    conn = Channel({}); conn.input = io.BytesIO(raw)
    with pytest.raises(DialogError): _frame(conn, 8192)


def test_registry_existing_mode_never_creates_missing_or_adopts_empty(tmp_path):
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(OSError): LifecycleRegistry(missing, existing=True)
    assert not missing.exists()
    empty = tmp_path / 'empty.sqlite'; empty.touch(mode=0o600)
    with pytest.raises(DialogError, match='wrong_lifecycle_database'):
        LifecycleRegistry(empty, existing=True)


def test_native_mcp_oauth_is_checked_before_any_client_call(monkeypatch):
    calls, handlers = [], {}
    class MCP:
        def tool(self, **kwargs):
            assert kwargs['meta'] == {'securitySchemes': [{'type': 'oauth2', 'scopes': ['bridge']}]}
            ann = kwargs['annotations']
            data = ann if isinstance(ann, dict) else ann.model_dump(by_alias=True, exclude_none=True)
            assert data['destructiveHint'] is False
            def register(fn): handlers[fn.__name__] = fn; return fn
            return register
    class Bridge:
        allowed = False
        def require_owner(self):
            calls.append('auth')
            if not self.allowed: raise PermissionError('denied')
            return 'configured_owner'
    class Client:
        def __init__(self, path): pass
        def call(self, obj): calls.append(obj); return {'ok': 'data'}
    monkeypatch.setattr('git_dialog.native_gateway.UnixClient', Client)
    bridge = Bridge()
    assert register_mcp(MCP(), bridge) == ('dialog_create', 'dialog_creation_status', 'dialog_list', 'dialog_index', 'dialog_health', 'dialog_notice')
    with pytest.raises(PermissionError): asyncio.run(handlers['dialog_index'](TASK))
    assert calls == ['auth']
    bridge.allowed = True
    assert asyncio.run(handlers['dialog_notice'](TASK, 'a'*40, 'm1', 1)) == {'ok': 'data'}
    assert calls[1:] == ['auth', {'op': 'notice', 'task_id': TASK, 'head': 'a'*40, 'message_id': 'm1', 'seq': 1}]
    with pytest.raises(DialogError, match='gateway_already_registered'): register_mcp(MCP(), bridge)


def test_unix_cli_has_no_credential_role_or_path_argument(monkeypatch, capsys):
    from git_dialog import unix_authority as module
    requests = []
    class Client:
        def __init__(self, path): assert path == module.SOCKETS['mt']
        def call(self, obj): requests.append(obj); return {'received': True}
    class Input:
        buffer = io.BytesIO(b'{"op":"view","offset":0,"limit":20}')
    monkeypatch.setattr(module, 'UnixClient', Client)
    monkeypatch.setattr(module.sys, 'stdin', Input())
    assert module.main(['cli']) == 0
    assert json.loads(capsys.readouterr().out) == {'received': True}
    assert requests == [{'op':'view', 'offset':0, 'limit':20}]


def test_native_notification_owner_is_guard_result_not_tool_or_git_body(monkeypatch):
    from concurrent.futures import Future
    calls, handlers = [], {}
    class MCP:
        def tool(self, **kw):
            def register(fn): handlers[fn.__name__] = fn; return fn
            return register
    class Host:
        notification_delivery = object()
        def request_notification_owner(self, principal):
            calls.append(('bind', principal))
            future = Future(); future.set_result(True); return future
    class Bridge:
        _shared_gateway_host = Host()
        def require_owner(self): calls.append('auth'); return 'oauth_principal'
    class Client:
        def __init__(self, *args): pass
        def call(self, obj): calls.append(('ipc', obj)); return {'head':'a'*40}
    monkeypatch.setattr('git_dialog.native_gateway.UnixClient', Client)
    register_mcp(MCP(), Bridge())
    asyncio.run(handlers['dialog_index'](TASK))
    assert calls[:2] == ['auth', ('bind','oauth_principal')]
    assert calls[2][0] == 'ipc'


def test_actual_acceptance_memory_dialog_through_same_runtime(tmp_path):
    from git_dialog.authority_acceptance import _MemoryDialog
    dialog = _MemoryDialog()
    registry = LifecycleRegistry(tmp_path/'r.sqlite')
    registry.register(dialog.snapshot.state.task_id, dialog.snapshot.head)
    store = AppendStore(tmp_path/'a.sqlite')
    try:
        host = Runtime(CommonAuthority(dialog, registry, store, dialog.snapshot.state.task_id), bridge_pid=101)
        cg = Channel(append(dialog.snapshot.head))
        host.handle('chatgpt', cg)
        assert cg.result()['ok']
        mt = Channel(append(cg.result()['result']['commit'], 'two', 'report'), pid=102)
        host.handle('mt', mt)
        assert mt.result()['ok'] and len(dialog.snapshot.messages) == 4
    finally:
        store.close(); registry.close()


def test_read_only_check_does_not_initialize_or_touch_other_state(tmp_path, monkeypatch, capsys):
    from git_dialog import unix_authority as module
    from git_dialog.preflight import TASK_ID
    path = tmp_path/'registry.sqlite'
    registry = LifecycleRegistry(path); registry.register(TASK_ID, 'a'*40); registry.close()
    before = path.read_bytes()
    monkeypatch.setattr(module, 'SOCKETS', {'chatgpt':tmp_path/'cg.sock', 'mt':tmp_path/'mt.sock'})
    output = tmp_path/'new.sqlite'
    assert module.main(['check', '--registry', str(path), '--append-store', str(output), '--bridge-pid','101']) == 0
    assert json.loads(capsys.readouterr().out)['writes_performed'] is False
    assert before == path.read_bytes() and not output.exists()


def test_check_retired_registry_does_not_reset_it(tmp_path, monkeypatch, capsys):
    from git_dialog import unix_authority as module
    from git_dialog.preflight import TASK_ID
    path = tmp_path/'registry.sqlite'
    registry = LifecycleRegistry(path); registry.register(TASK_ID, 'a'*40); registry.cancel(TASK_ID); registry.close()
    before = path.read_bytes()
    monkeypatch.setattr(module, 'SOCKETS', {'chatgpt':tmp_path/'cg.sock', 'mt':tmp_path/'mt.sock'})
    assert module.main(['check','--registry',str(path),'--append-store',str(tmp_path/'new.sqlite'),
                        '--bridge-pid','101']) == 1
    out = json.loads(capsys.readouterr().out)
    assert out['phase'] == 'registry_state' and before == path.read_bytes()


@pytest.mark.parametrize('timeout', [False, True])
def test_client_has_separate_history_response_budget_and_never_retries(tmp_path, monkeypatch, timeout):
    from git_dialog import unix_authority as module
    path = tmp_path / 'mt.sock'
    monkeypatch.setattr(module, '_directory', lambda p: p)
    monkeypatch.setattr(type(path), 'lstat', lambda p: SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600, st_uid=os.getuid(), st_nlink=1))
    calls = []
    class Connection:
        def settimeout(self, seconds): calls.append(('connect_budget', seconds))
        def connect(self, value): calls.append(('connect', value))
        def sendall(self, value): calls.append(('send', value))
        def close(self): calls.append(('close',))
    conn = Connection()
    monkeypatch.setattr(module.socket, 'socket', lambda *args: conn)
    monkeypatch.setattr(module, 'peer_identity', lambda value: (201, os.getuid()))
    def frame(value, maximum, seconds):
        assert value is conn and maximum == module.MAX_RESPONSE
        calls.append(('response_budget', seconds))
        if timeout: raise TimeoutError()
        return b'{"ok":true,"result":{"message_id":"one","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","created":true}}'
    monkeypatch.setattr(module, '_frame', frame)
    client = module.UnixClient(path)
    if timeout:
        with pytest.raises(DialogError, match='authority_result_unconfirmed'):
            client.call(append('b' * 40))
    else:
        assert client.call(append('b' * 40))['created'] is True
    assert ('connect_budget', 5) in calls and ('response_budget', 120) in calls
    assert sum(c[0] == 'connect' for c in calls) == 1
    assert sum(c[0] == 'send' for c in calls) == 1
    assert calls[-1] == ('close',)


def test_response_deadline_is_total_not_renewed_per_fragment(monkeypatch):
    from git_dialog import unix_authority as module
    times = iter([0, 1, 119, 121])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(times))
    budgets = []
    class Connection:
        data = iter([struct.pack('!I', 2), b'{'])
        def settimeout(self, seconds): budgets.append(seconds)
        def recv(self, count): return next(self.data)
    with pytest.raises(DialogError, match='authority_frame_rejected'):
        module._frame(Connection(), 8192, 120)
    assert budgets == [119, 1]
