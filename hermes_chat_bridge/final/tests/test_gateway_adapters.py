import io
import json
import threading

import pytest

from git_dialog import DialogError
from git_dialog.append_store import AppendStore
from git_dialog.authority import CommonAuthority
from git_dialog.gateway_adapters import GatewayClient, MCPAdapter, main, register_mcp_tools
from git_dialog.gateway_host import GatewayApplication
from git_dialog.ingress import AccessAuthority
from test_confirmation import setup, cleanup
from test_github import TASK, writes

ORIGIN = 'http://127.0.0.1:18791'


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    api, registry, box, confirmations, controller = setup(tmp_path)
    store = AppendStore(tmp_path / 'append.sqlite')
    common = CommonAuthority(controller.coordinator.dialog, registry, store, TASK)
    access = AccessAuthority({'cg': {'role': 'agent', 'actor': 'chatgpt'},
                              'mt': {'role': 'agent', 'actor': 'mt'},
                              'owner': {'role': 'owner', 'actor': None}})
    dispatcher = common.dispatcher(controller, access, origin=ORIGIN, mutations=True)
    app = GatewayApplication(dispatcher)
    calls = []

    class Response:
        def __init__(self, env):
            captured = []
            def start(status, headers): captured.append((status, dict(headers)))
            self._raw = io.BytesIO(b''.join(app(env, start)))
            self.status = int(captured[0][0].split()[0])
            self._headers = captured[0][1]
        def getheader(self, name, default=None): return self._headers.get(name, default)
        def read1(self, size): return self._raw.read(size)

    class Connection:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1' and port == 18791 and timeout == 2
            self.sock, self.env = self, {'REMOTE_ADDR': host, 'QUERY_STRING': '', 'SCRIPT_NAME': ''}
        def connect(self): calls.append('connect')
        def settimeout(self, value): assert 0 < value <= 2
        def putrequest(self, method, path, **kwargs):
            assert kwargs == {'skip_accept_encoding': True}
            self.env.update(REQUEST_METHOD=method, PATH_INFO=path, HTTP_HOST='127.0.0.1:18791')
        def putheader(self, key, value):
            key = key.upper().replace('-', '_')
            self.env[key if key in ('CONTENT_LENGTH', 'CONTENT_TYPE') else 'HTTP_' + key] = value
        def endheaders(self, raw): self.env['wsgi.input'] = io.BytesIO(raw)
        def getresponse(self): return Response(self.env)
        def close(self): calls.append('close')

    monkeypatch.setattr('git_dialog.gateway_adapters.HTTPConnection', Connection)
    def client(principal):
        grant = access.issue(principal)
        return GatewayClient(ORIGIN, grant['session'], grant['csrf']), grant
    yield api, registry, store, common, app, access, client, calls, Connection
    store.close(); cleanup(registry, box, confirmations)


def append(head, mid='one', kind='task', body='ordinary report data'):
    return {'op': 'append', 'head': head, 'message_id': mid, 'kind': kind, 'body': body}


def invoke(app, grant, obj, **updates):
    raw = json.dumps(obj).encode()
    env = {'REMOTE_ADDR': '127.0.0.1', 'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/task',
           'SCRIPT_NAME': '', 'QUERY_STRING': '', 'HTTP_HOST': '127.0.0.1:18791',
           'HTTP_ORIGIN': ORIGIN, 'HTTP_AUTHORIZATION': 'Bearer ' + grant['session'],
           'HTTP_X_CSRF_TOKEN': grant['csrf'], 'CONTENT_TYPE': 'application/json',
           'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
    env.update(updates)
    out = []
    data = b''.join(app(env, lambda status, headers: out.append((status, dict(headers)))))
    assert out[0][1]['Cache-Control'] == 'no-store'
    assert out[0][1]['Content-Length'] == str(len(data))
    return int(out[0][0].split()[0]), json.loads(data)


def test_mcp_then_cli_use_one_real_wsgi_dispatcher_and_durable_store(runtime):
    api, _, store, _, _, _, client, calls, _ = runtime
    cg, _ = client('cg')
    mt, grant = client('mt')
    tools = MCPAdapter(cg)
    first = tools.dialog_append(api.initial, 'mcp_one', 'task', 'inert data; not shell')
    output = io.StringIO()
    payload = {'session': grant['session'], 'csrf': grant['csrf'],
               'request': append(first['commit'], 'cli_two', 'report')}
    assert main(['--origin', ORIGIN], stdin=io.StringIO(json.dumps(payload)), stdout=output) == 0
    second = json.loads(output.getvalue())
    assert store.status(TASK, 'mcp_one')['confirmed_commit'] == first['commit']
    assert store.status(TASK, 'cli_two')['confirmed_commit'] == second['commit']
    assert len([c for c in api.calls if c[0] == 'PATCH']) == 2
    assert tools.dialog_view()['waiting_for'] == 'chatgpt'
    assert calls == ['connect', 'close'] * 3


def test_delayed_mcp_retry_same_id_reconciles_without_new_write(runtime):
    api, _, _, _, _, _, client, _, _ = runtime
    cg, _ = client('cg'); tools = MCPAdapter(cg)
    first = tools.dialog_append(api.initial, 'one', 'task', 'data')
    api.calls.clear()
    assert tools.dialog_append(api.initial, 'one', 'task', 'data') == {**first, 'created': False}
    assert not writes(api)


@pytest.mark.parametrize('op', ['request_close', 'confirm_close', 'cancel_close', 'delete', 'create', 'execute'])
def test_host_exposes_no_lifecycle_or_execution_routes(runtime, op):
    api, _, _, _, app, _, client, _, _ = runtime
    _, grant = client('owner')
    status, body = invoke(app, grant, {'op': op})
    assert status == 403 and body == {'error': 'gateway_request_rejected'}
    assert not api.calls


@pytest.mark.parametrize('extra', [{'actor': 'mt'}, {'task_id': 'task_other'},
                                  {'principal': 'owner'}, {'session': 'data'}])
def test_identity_and_scope_cannot_be_selected_from_body(runtime, extra):
    api, _, _, _, app, _, client, _, _ = runtime
    _, grant = client('cg')
    status, _ = invoke(app, grant, {**append(api.initial), **extra})
    assert status == 403 and not api.calls


@pytest.mark.parametrize('change', [
    {'REMOTE_ADDR': '10.0.0.1'}, {'HTTP_HOST': 'evil.invalid'}, {'HTTP_ORIGIN': 'http://evil.invalid'},
    {'HTTP_X_FORWARDED_FOR': '127.0.0.1'}, {'HTTP_TRANSFER_ENCODING': 'chunked'},
    {'HTTP_EXPECT': '100-continue'}, {'QUERY_STRING': 'x=1'}, {'PATH_INFO': '/api/task/'},
    {'SCRIPT_NAME': '/prefix'}, {'CONTENT_LENGTH': '081'}, {'REQUEST_METHOD': 'GET'},
    {'HTTP_AUTHORIZATION': 'Basic ignored'}, {'HTTP_X_CSRF_TOKEN': 'invalid'},
])
def test_wsgi_request_guards_fail_before_git(runtime, change):
    api, _, _, _, app, _, client, _, _ = runtime
    _, grant = client('cg')
    status, _ = invoke(app, grant, append(api.initial), **change)
    assert status in (401, 403) and not api.calls


def test_revoked_grant_denied_before_reading_request_body(runtime):
    api, _, _, _, app, access, client, _, _ = runtime
    _, grant = client('cg'); access.revoke(grant['session'])
    class ForbiddenRead:
        def read(self, size): raise AssertionError('must not read unauthenticated input')
    assert invoke(app, grant, append(api.initial), **{'wsgi.input': ForbiddenRead()})[0] == 401
    assert not api.calls


def test_cancelled_lifecycle_blocks_both_adapters(runtime):
    api, registry, _, _, _, _, client, _, _ = runtime
    registry.cancel(TASK)
    cg, _ = client('cg'); mt, grant = client('mt')
    with pytest.raises(DialogError, match='gateway_result_unconfirmed'):
        MCPAdapter(cg).dialog_append(api.initial, 'mcp', 'task', 'data')
    out = io.StringIO()
    assert main(['--origin', ORIGIN], stdin=io.StringIO(json.dumps({
        'session': grant['session'], 'csrf': grant['csrf'], 'request': append(api.initial, 'cli', 'report')})),
        stdout=out) == 1
    assert json.loads(out.getvalue())['retry_authorized'] is False
    assert not api.calls


def test_cross_thread_fails_closed_not_sqlite_thread_repair(runtime):
    api, _, _, _, app, _, client, _, _ = runtime
    _, grant = client('cg'); results = []
    thread = threading.Thread(target=lambda: results.append(invoke(app, grant, append(api.initial))))
    thread.start(); thread.join(timeout=1)
    assert not thread.is_alive() and results[0][0] == 403 and not api.calls


@pytest.mark.parametrize('origin', ['https://127.0.0.1:18791', 'http://localhost:18791',
    'http://127.0.0.1:8765', 'http://127.0.0.1:8790', 'http://127.0.0.1:18791/path',
    'http://user:pass@127.0.0.1:18791', 'http://127.0.0.1:99999'])
def test_no_proxy_dns_redirect_credential_or_protected_service_target(runtime, origin):
    _, _, _, _, _, _, client, calls, _ = runtime
    _, grant = client('cg')
    with pytest.raises(DialogError): GatewayClient(origin, grant['session'], grant['csrf'])
    assert not calls


def test_lost_http_reply_after_effect_never_automatically_retries(runtime, monkeypatch):
    api, _, store, _, _, _, client, calls, connection = runtime
    cg, _ = client('cg')
    original = connection.getresponse
    def lost(self):
        original(self)
        raise OSError('PRIVATE_EXCEPTION_MUST_NOT_ESCAPE')
    monkeypatch.setattr(connection, 'getresponse', lost)
    with pytest.raises(DialogError, match='^gateway_result_unconfirmed$'):
        MCPAdapter(cg).dialog_append(api.initial, 'lost', 'task', 'data')
    assert store.status(TASK, 'lost')['confirmed_commit']
    assert len([c for c in api.calls if c[0] == 'PATCH']) == 1
    assert calls == ['connect', 'close']


def test_cli_does_not_echo_credentials_body_paths_or_exceptions(runtime):
    api, _, _, _, _, _, client, calls, _ = runtime
    _, grant = client('cg'); out = io.StringIO()
    assert main(['--origin', ORIGIN], stdin=io.StringIO(json.dumps({**grant, 'request':
        append(api.initial, body='PRIVATE_REPORT')})), stdout=out) == 1  # Extra expires field denied.
    assert out.getvalue() == '{"error": "gateway_result_unconfirmed", "retry_authorized": false}\n'
    assert not calls and not api.calls


def test_explicit_sdk_decorator_registration_exposes_only_bound_handlers(runtime):
    import inspect
    api, _, store, _, _, _, client, _, _ = runtime
    cg, _ = client('cg')
    class Server:
        def __init__(self): self.handlers = {}
        def tool(self):
            def register(handler):
                assert handler.__name__ not in self.handlers
                self.handlers[handler.__name__] = handler
                return handler
            return register
    server = Server()
    register_mcp_tools(server, cg)
    assert set(server.handlers) == {'dialog_view', 'dialog_append'}
    assert set(inspect.signature(server.handlers['dialog_append']).parameters) == {
        'head', 'message_id', 'kind', 'body'}
    result = server.handlers['dialog_append'](api.initial, 'registered', 'task', 'data')
    assert store.status(TASK, 'registered')['confirmed_commit'] == result['commit']


def test_owner_grant_and_default_read_only_dispatcher_cannot_append(runtime):
    api, _, _, _, app, _, client, _, _ = runtime
    owner, _ = client('owner')
    with pytest.raises(DialogError, match='gateway_result_unconfirmed'):
        owner.call(append(api.initial))
    assert not api.calls
    app.dispatcher.mutations = False
    cg, _ = client('cg')
    with pytest.raises(DialogError, match='gateway_result_unconfirmed'):
        cg.call(append(api.initial))
    assert not api.calls


def test_response_failure_and_redirect_do_not_trigger_second_attempt(runtime, monkeypatch):
    api, _, _, _, _, _, client, calls, connection = runtime
    cg, _ = client('cg')
    class Redirect:
        status = 302
        def getheader(self, name, default=None): return default
    monkeypatch.setattr(connection, 'getresponse', lambda self: Redirect())
    with pytest.raises(DialogError, match='gateway_result_unconfirmed'):
        cg.call(append(api.initial))
    assert calls == ['connect', 'close'] and not api.calls
