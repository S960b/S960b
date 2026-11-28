import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from git_dialog import native_notify as notify, wakeup_key
from git_dialog.preflight import TASK_ID

KEY = b'n' * 32
NOW = 1793200000


def signed(**changes):
    from datetime import datetime, timezone
    value = {'job_id': 'test_fixture.job', 'queue': 'test', 'task_id': TASK_ID,
             'timestamp': datetime.fromtimestamp(NOW, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    value.update(changes)
    raw = json.dumps(value, separators=(',', ':')).encode()
    return raw, 'sha256=' + hmac.new(KEY, notify.DOMAIN + raw, hashlib.sha256).hexdigest()


def binding(**changes):
    metadata = {'job_id': 'test_fixture.job', 'queue': 'test', 'task_id': TASK_ID,
                'direction': 'to_chatgpt', 'status': 'pending', 'text': 'never used as authority'}
    metadata.update(changes)
    calls = []
    bridge = SimpleNamespace(queue=SimpleNamespace(get_message=lambda job: metadata),
                             emit_test_event=lambda job, **kw: calls.append((job, kw)),
                             _beta_forwarder=SimpleNamespace(_key=KEY))
    return bridge, calls


def test_existing_pending_report_notified_once_no_content_writer():
    bridge, calls = binding()
    gate = notify.NotifyGate(KEY, bridge)
    assert gate.accept(*signed(), now=NOW, monotonic=0) == (202, 'notified')
    assert gate.accept(*signed(), now=NOW, monotonic=1) == (200, 'duplicate')
    assert calls == [('test_fixture.job', {'queue': 'test'})]


@pytest.mark.parametrize('change', [{'status': 'claimed'}, {'status': 'replied'},
                                  {'queue': 'ctf_A'}, {'task_id': 'other'},
                                  {'direction': 'to_hermes'}, {'job_id': 'other.job'}])
def test_metadata_scope_and_pending_only(change):
    bridge, calls = binding(**change)
    assert notify.NotifyGate(KEY, bridge).accept(*signed(), now=NOW, monotonic=0) == (200, 'ignored')
    assert calls == []


@pytest.mark.parametrize('change', [{'queue': 'ctf_A'}, {'task_id': 'other'},
                                  {'job_id': '../../file'}, {'timestamp': 'bad'}, {'text': 'execute me'}])
def test_authenticated_body_cannot_broaden_scope(change):
    bridge, calls = binding()
    assert notify.NotifyGate(KEY, bridge).accept(*signed(**change), now=NOW, monotonic=0)[0] == 400
    assert calls == []


def test_hmac_domain_auth_before_parse_and_freshness():
    bridge, calls = binding()
    gate = notify.NotifyGate(KEY, bridge)
    raw, sig = signed()
    wrong_domain = 'sha256=' + hmac.new(KEY, raw, hashlib.sha256).hexdigest()
    assert gate.accept(raw, wrong_domain, now=NOW)[0] == 401
    assert gate.accept(b'not json', sig, now=NOW)[0] == 401
    assert gate.accept(raw, sig, now=NOW + 121)[0] == 400
    assert gate.accept(raw, sig, now=NOW - 31)[0] == 400
    assert not calls


def test_duplicate_json_and_unknown_callback_fail_closed_no_retry():
    bridge, calls = binding()
    raw = b'{"job_id":"x.job","job_id":"y.job"}'
    sig = 'sha256=' + hmac.new(KEY, notify.DOMAIN + raw, hashlib.sha256).hexdigest()
    assert notify.NotifyGate(KEY, bridge).accept(raw, sig, now=NOW)[0] == 400
    def fail(*a, **kw): raise RuntimeError('secret traceback must not escape')
    bridge.emit_test_event = fail
    gate = notify.NotifyGate(KEY, bridge)
    assert gate.accept(*signed(), now=NOW, monotonic=0) == (503, 'unknown')
    assert gate.accept(*signed(), now=NOW, monotonic=1) == (200, 'duplicate')


def test_rate_bound():
    bridge, calls = binding()
    gate = notify.NotifyGate(KEY, bridge)
    assert gate.accept(*signed(), now=NOW, monotonic=0)[0] == 202
    assert gate.accept(*signed(job_id='test_second.job'), now=NOW, monotonic=1) == (429, 'limited')


def request_fixture(headers=None, **scope_changes):
    raw, sig = signed()
    scope = {'client': ('127.0.0.1', 1234), 'raw_path': notify.PATH.encode(), 'query_string': b'',
             'method': 'POST', 'headers': [(b'host', b'127.0.0.1:8765'),
             (b'content-type', b'application/json'), (b'content-length', str(len(raw)).encode()),
             (b'x-hermes-notify', sig.encode())]}
    if headers:
        scope['headers'] += headers
    scope.update(scope_changes)
    async def stream(): yield raw
    return SimpleNamespace(scope=scope, stream=stream)


def application():
    class Router:
        def __init__(self): self.routes = [SimpleNamespace(path='/')]
        def add_route(self, path, app): self.routes.append(SimpleNamespace(path=path, app=app))
    return SimpleNamespace(router=Router())


def invoke(app, request):
    sent = []
    async def receive():
        raw = b''
        async for chunk in request.stream(): raw += chunk
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    async def send(value): sent.append(value)
    asyncio.run(app.router.routes[0].app(request.scope, receive, send))
    return SimpleNamespace(status_code=sent[0]['status'], body=sent[1]['body'])


@pytest.mark.parametrize('change', [{'client': ('10.1.1.1', 1)}, {'query_string': b'x=1'},
                                   {'raw_path': b'/bridge/%62eta-notify'}, {'method': 'GET'}])
def test_route_origin_path_and_method_guards(monkeypatch, change):
    bridge, calls = binding()
    app = application()
    notify.attach(app, bridge)
    assert app.router.routes[0].path == notify.PATH
    response = invoke(app, request_fixture(**change))
    assert response.status_code in (403, 405)
    assert not calls


@pytest.mark.parametrize('header', [(b'origin', b'http://localhost'), (b'host', b'127.0.0.1:8765'),
                                  (b'transfer-encoding', b'chunked'), (b'expect', b'100-continue'),
                                  (b'x-forwarded-for', b'127.0.0.1')])
def test_route_duplicate_proxy_browser_and_framing_guards(header):
    bridge, calls = binding()
    app = application()
    notify.attach(app, bridge)
    response = invoke(app, request_fixture(headers=[header]))
    assert response.status_code == 403 and not calls


def test_route_success_and_default_off(monkeypatch):
    bridge, calls = binding()
    app = application()
    monkeypatch.setattr(notify.time, 'time', lambda: NOW)
    assert notify.attach(app, bridge)
    response = invoke(app, request_fixture())
    assert response.status_code == 202 and len(calls) == 1
    assert response.body == b'{"status":"notified"}'
    with pytest.raises(ValueError): notify.attach(app, bridge)
    bridge._beta_forwarder = None
    assert not notify.attach(SimpleNamespace(router=SimpleNamespace(routes=[])), bridge)


def test_client_default_off_no_key_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, 'STATE', str(tmp_path))
    monkeypatch.setattr(wakeup_key, 'load', lambda *a: pytest.fail('key read'))
    assert notify.notify('test_fixture.job') is False
    assert notify.notify('../../file') is False


def test_client_one_literal_bounded_request_no_body_output(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, 'STATE', str(tmp_path))
    (tmp_path / 'bridge-wakeup.enabled').touch(mode=0o600)
    monkeypatch.setattr(wakeup_key, 'load', lambda path: KEY)
    seen = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): assert 0 < value <= 2
        def connect(self, value): seen.append(value)
        def sendall(self, value): seen.append(value)
        def recv(self, size):
            assert size <= 1024
            return b'HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n'
    monkeypatch.setattr(notify.socket, 'socket', lambda *a: Connection())
    assert notify.notify('test_fixture.job') is True
    assert seen[0] == ('127.0.0.1', 8765)
    header, raw = seen[1].split(b'\r\n\r\n', 1)
    assert header.startswith(b'POST /bridge/beta-notify HTTP/1.1')
    assert set(json.loads(raw)) == {'job_id', 'timestamp', 'queue', 'task_id'}
    signature = header.split(b'X-Hermes-Notify: ')[1].split(b'\r\n')[0]
    assert signature == ('sha256=' + hmac.new(KEY, notify.DOMAIN + raw, hashlib.sha256).hexdigest()).encode()


def test_client_bad_marker_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, 'STATE', str(tmp_path))
    marker = tmp_path / 'bridge-wakeup.enabled'
    marker.touch(mode=0o600)
    marker.chmod(0o664)
    monkeypatch.setattr(wakeup_key, 'load', lambda *a: pytest.fail('key read'))
    assert notify.notify('test_fixture.job') is False
