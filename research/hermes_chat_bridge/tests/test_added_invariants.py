import asyncio
import base64
import json
import socket
import time
from urllib.parse import parse_qs, urlparse
import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from starlette.testclient import TestClient
from test_review_3b082fe import bridge, api, rpc, sub_params, stored_sub, BASE, SECRET


def test_no_principal_or_foreign_subject_rejected(api):
    client, b = api
    from mcp.server.auth.provider import AccessToken
    b.store.save_access(AccessToken(token='foreign', client_id='c', scopes=['bridge'], subject='other', resource=BASE+'/mcp', expires_at=int(time.time())+600))
    for method in ['events/list', 'events/subscribe', 'events/unsubscribe', 'tools/list']:
        r = rpc(client, method, sub_params(), token='foreign')
        assert r.status_code == 401 or 'error' in r.json() or r.json().get('result', {}).get('resultType') == 'error'
    from mcp.server.auth.middleware.auth_context import auth_context_var
    context = auth_context_var.set(None)
    try:
        with pytest.raises(PermissionError):
            asyncio.run(b.events_subscribe(None, sub_params()))
    finally:
        auth_context_var.reset(context)


def test_internal_state_distinct_and_one_use(bridge):
    client = OAuthClientInformationFull(client_id='c', redirect_uris=['https://callback.example/oauth'])
    params = AuthorizationParams(state='client-state', scopes=['bridge'], redirect_uri='https://callback.example/oauth', redirect_uri_provided_explicitly=True, code_challenge='R'*43, resource=BASE+'/mcp')
    url = asyncio.run(bridge.provider.authorize(client, params))
    state = parse_qs(urlparse(url).query)['state'][0]
    assert state != 'client-state'
    from starlette.applications import Starlette
    from starlette.routing import Route
    with TestClient(Starlette(routes=[Route('/login/callback', bridge.login_callback, methods=['POST'])])) as c:
        data = dict(username='owner', password='review-fixture-password', state=state)
        r = c.post('/login/callback', data=data, follow_redirects=False)
        assert r.status_code == 302
        assert parse_qs(urlparse(r.headers['location']).query)['state'] == ['client-state']
        assert c.post('/login/callback', data=data, follow_redirects=False).status_code == 400


def test_login_attempt_budget_and_invalid_get(bridge):
    bridge.store.save_state('s', {})
    from starlette.applications import Starlette
    from starlette.routing import Route
    with TestClient(Starlette(routes=[Route('/login', bridge.login_handler), Route('/login/callback', bridge.login_callback, methods=['POST'])])) as c:
        assert c.get('/login?state=unknown').status_code == 400
        for _ in range(5):
            c.post('/login/callback', data=dict(state='s', username='owner', password='bad'))
        assert bridge.store.load_state('s') is None


def test_unique_outbox_and_terminal_reason(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.emit_test_event('job_x')
    bridge.emit_test_event('job_x')
    assert bridge.store.conn.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 1
    bridge.store.deactivate_sub(s['id'])
    asyncio.run(bridge.deliver_pending())
    r = bridge.store.conn.execute('SELECT * FROM deliveries').fetchone()
    assert r['done'] and r['terminal_reason']


def test_callback_pin_rebinding_no_proxy(bridge, monkeypatch):
    calls = []
    answers = iter(['93.184.216.34', '127.0.0.1'])
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (next(answers), 443))])
    class Conn:
        def __init__(self, host, address, port, timeout):
            calls.append((host, address, port))
        def request(self, *a, **k): pass
        def getresponse(self): return self
        status = 302
        def read(self, n): return b'{}'
        def close(self): pass
    # Guard legacy urllib path too: never open a real socket in RED.
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: (_ for _ in ()).throw(RuntimeError('network forbidden in tests')))
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setattr(__import__('bridge_server'), 'PinnedHTTPSConnection', Conn, raising=False)
    assert asyncio.run(bridge._http_post('https://callback.example/hook', b'{}', {}))[0] == 302
    assert calls == [('callback.example', '93.184.216.34', 443)]
    for url in ['https://user:pass@callback.example/', 'https://127.0.0.1/', 'http://callback.example/']:
        with pytest.raises(ValueError): bridge._validate_callback_url(url)


def test_callback_does_not_block_event_loop(bridge, monkeypatch):
    import bridge_server as bs
    def slow(*a):
        time.sleep(.1)
        return 200, b'{}'
    monkeypatch.setattr(bridge, '_post_pinned', slow, raising=False)
    async def check():
        task = asyncio.create_task(bridge._http_post('https://callback.example/', b'{}', {}))
        await asyncio.sleep(.01)
        assert not task.done()
        await task
    asyncio.run(check())


def test_ttl_and_failed_rotation_preserves_working(bridge, monkeypatch):
    import bridge_server as bs
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    token = AccessToken(token='a', client_id='c', scopes=['bridge'], subject='owner', resource=BASE+'/mcp', expires_at=int(time.time())+600)
    bridge.store.save_access(token)
    t = auth_context_var.set(AuthenticatedUser(token))
    try:
        monkeypatch.setattr(bridge, '_validate_callback_url', lambda u: None)
        async def ok(s): return True
        monkeypatch.setattr(bridge, 'verify_callback', ok)
        p = sub_params(); p['ttlMs'] = 2000
        result = asyncio.run(bridge.events_subscribe(None, p))
        s = bridge.store.load_sub(result['id'])
        assert 0 < s['expires_at'] - time.time() <= 2
        async def fail(s): return False
        monkeypatch.setattr(bridge, 'verify_callback', fail)
        p['delivery']['secret'] = 'whsec_' + base64.b64encode(b'Z'*32).decode()
        assert asyncio.run(bridge.events_subscribe(None, p))['resultType'] == 'error'
        assert bridge.store.load_sub(s['id'])['secret'] == SECRET
    finally: auth_context_var.reset(t)


def test_tool_metadata(api):
    c, b = api
    tools = rpc(c, 'tools/list').json()['result']['tools']
    for t in tools:
        assert t['_meta']['securitySchemes'] == [{'type':'oauth2','scopes':['bridge']}]
        assert t['annotations']['readOnlyHint'] == (t['name']=='bridge_get_message')


def test_missing_password_fails_before_db(monkeypatch):
    import bridge_server as bs
    monkeypatch.setattr(bs, 'OWNER_PASS', None)
    monkeypatch.setattr(bs, 'DB_PATH', '/must-not-be-opened/bridge.db')
    with pytest.raises(RuntimeError): bs.build_app(BASE)


def test_revocation_stops_delivery(bridge):
    from mcp.server.auth.provider import AccessToken
    bridge.store.save_access(AccessToken(token='a', client_id='c', scopes=['bridge'], subject='owner', resource=BASE+'/mcp'))
    stored_sub(bridge)
    bridge.emit_test_event('job_x')
    asyncio.run(bridge.provider.revoke_token('a'))
    assert bridge.store.active_subscriptions() == []
    assert bridge.store.conn.execute('SELECT done FROM deliveries').fetchone()[0] == 1
