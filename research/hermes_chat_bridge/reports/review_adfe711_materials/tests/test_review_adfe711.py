"""Independent acceptance of adfe711; temporary DBs, mocked callbacks only.

These assertions express desired contracts. Run with the project's isolation guard.
"""
import asyncio
import base64
import json
import sqlite3
import time
from pathlib import Path

import pytest
from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.testclient import TestClient

import bridge_server as bs
from test_review_3b082fe import bridge, sub_params, stored_sub, BASE, SECRET


@pytest.fixture
def http_api(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'DB_PATH', str(tmp_path / 'http.db'))
    monkeypatch.setattr(bs, 'OWNER_USER', 'owner')
    monkeypatch.setattr(bs, 'OWNER_PASS', 'review-synthetic-password')
    app = bs.build_app(BASE)
    b = app.state.bridge
    with TestClient(app, base_url=BASE, raise_server_exceptions=False) as c:
        reg = c.post('/register', json={
            'client_name': 'independent-review', 'redirect_uris': ['https://callback.example/oauth'],
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'], 'token_endpoint_auth_method': 'client_secret_post',
            'scope': 'bridge'})
        assert reg.status_code == 201, reg.text
        yield c, b, reg.json()
    b.store.conn.close()
    b.queue._conn.close()


def http_rpc(client, method, params, token):
    p = dict(params)
    p['_meta'] = {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                  'io.modelcontextprotocol/clientCapabilities': {}}
    headers = {'Authorization': 'Bearer ' + token, 'MCP-Protocol-Version': '2026-07-28',
               'Mcp-Method': method, 'Accept': 'application/json, text/event-stream'}
    if 'name' in p:
        headers['Mcp-Name'] = p['name']
    return client.post('/mcp', headers=headers,
                       json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': p})


@pytest.mark.parametrize('kind', ['access_token', 'refresh_token'])
def test_real_revoke_endpoint_accepts_sdk_token_model(http_api, kind):
    c, b, reg = http_api
    fields = dict(token='synthetic-to-revoke', client_id=reg['client_id'], scopes=['bridge'],
                  subject='owner', resource=BASE + '/mcp', expires_at=int(time.time()) + 600)
    if kind == 'access_token':
        b.store.save_access(AccessToken(**fields))
    else:
        b.store.save_refresh(RefreshToken(**fields))
    s = stored_sub(b)
    b.emit_test_event('job_synthetic')
    response = c.post('/revoke', data={
        'client_id': reg['client_id'], 'client_secret': reg['client_secret'],
        'token': fields['token'], 'token_type_hint': kind})
    assert response.status_code == 200, response.text
    assert b.store.load_access(fields['token']) is None
    assert b.store.load_refresh(fields['token']) is None
    assert not b.store.load_sub(s['id'])['active']
    assert b.store.conn.execute('SELECT done FROM deliveries').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['access_token', 'refresh_token'])
def test_provider_revocation_accepts_sdk_object(bridge, kind):
    fields = dict(token='sdk-model', client_id='c', scopes=['bridge'], subject='owner',
                  resource=BASE+'/mcp', expires_at=int(time.time()) + 600)
    token = AccessToken(**fields) if kind == 'access_token' else RefreshToken(**fields)
    if kind == 'access_token':
        bridge.store.save_access(token)
    else:
        bridge.store.save_refresh(token)
    stored_sub(bridge)
    # SDK RevocationHandler calls provider.revoke_token(token), not a string.
    asyncio.run(bridge.provider.revoke_token(token))
    assert bridge.store.load_access(token.token) is None
    assert bridge.store.load_refresh(token.token) is None
    assert bridge.store.active_subscriptions() == []


def test_callback_failure_is_top_level_jsonrpc_error(http_api, monkeypatch):
    c, b, reg = http_api
    token = AccessToken(token='at_review', client_id=reg['client_id'], scopes=['bridge'],
                        subject='owner', resource=BASE + '/mcp', expires_at=int(time.time()) + 600)
    b.store.save_access(token)
    monkeypatch.setattr(b, '_validate_callback_url', lambda url: None)
    async def fail(sub):
        return False
    monkeypatch.setattr(b, 'verify_callback', fail)
    response = http_rpc(c, 'events/subscribe', sub_params(), token.token)
    payload = response.json()
    assert payload.get('error', {}).get('code') == -32015, payload
    assert 'result' not in payload


def test_unsubscribe_during_pending_challenge_wins(bridge, monkeypatch):
    monkeypatch.setattr(bridge, '_validate_callback_url', lambda url: None)
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def verify(sub):
            started.set()
            await release.wait()
            return True
        monkeypatch.setattr(bridge, 'verify_callback', verify)
        task = asyncio.create_task(bridge.events_subscribe(None, sub_params()))
        await asyncio.wait_for(started.wait(), 2)
        try:
            await bridge.events_unsubscribe(None, sub_params())
        finally:
            release.set()
        await task
        assert bridge.store.active_subscriptions() == [], 'late challenge resurrected cancelled subscription'
    asyncio.run(scenario())


def test_revocation_during_challenge_blocks_activation(bridge, monkeypatch):
    monkeypatch.setattr(bridge, '_validate_callback_url', lambda url: None)
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def verify(sub):
            started.set()
            await release.wait()
            return True
        monkeypatch.setattr(bridge, 'verify_callback', verify)
        task = asyncio.create_task(bridge.events_subscribe(None, sub_params()))
        await asyncio.wait_for(started.wait(), 2)
        try:
            await bridge.provider.revoke_token('at_fixture')
        finally:
            release.set()
        with pytest.raises(PermissionError):
            await task
        assert bridge.store.active_subscriptions() == []
    asyncio.run(scenario())


def test_legacy_active_flag_is_not_proof_of_callback_verification(tmp_path, monkeypatch):
    # Copy only the old schema/data shape into a temporary DB; no real user DB.
    path = tmp_path / 'legacy.db'
    params = sub_params()
    sub_id = 'sub_' + __import__('hashlib').sha256(('owner' + params['name'] +
        json.dumps(params['arguments'], sort_keys=True) + params['delivery']['url']).encode()).hexdigest()[:24]
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE subs(sub_id TEXT PRIMARY KEY,owner TEXT,event TEXT,arguments TEXT,'
                 'callback_url TEXT,secret TEXT,expires_at REAL,active INTEGER,created_at TEXT)')
    conn.execute('INSERT INTO subs VALUES(?,?,?,?,?,?,?,?,?)', (
        sub_id, 'owner', params['name'], json.dumps(params['arguments']), params['delivery']['url'],
        SECRET, time.time() + 3600, 1, 'legacy-unverified'))
    conn.commit()
    conn.close()
    monkeypatch.setattr(bs, 'DB_PATH', str(path))
    monkeypatch.setattr(bs, 'OWNER_USER', 'owner')
    b = bs.BridgeApp(BASE)
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    token = AccessToken(token='at_migration', client_id='c', scopes=['bridge'], subject='owner',
                        resource=BASE + '/mcp', expires_at=int(time.time()) + 600)
    b.store.save_access(token)
    context = auth_context_var.set(AuthenticatedUser(token))
    try:
        # First ensure that old active=True cannot immediately emit.
        assert b.store.active_subscriptions() == [], 'legacy unverified row remained active after migration'
    finally:
        auth_context_var.reset(context)
        b.store.conn.close()
        b.queue._conn.close()


def test_retry_outbox_survives_store_restart(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s['id'], 'evt_restart', {'job_id': 'job_synthetic', 'queue': 'test'})
    first_body = bridge.store.conn.execute('SELECT body FROM deliveries').fetchone()[0]
    async def fail(*args, **kwargs):
        return 503, b'{}'
    monkeypatch.setattr(bridge, '_http_post', fail)
    asyncio.run(bridge.deliver_pending())
    bridge.store.conn.close()
    bridge.store = bs.Store(bs.DB_PATH)
    bridge.provider.store = bridge.store
    bridge.store.conn.execute('UPDATE deliveries SET next_attempt=0')
    bridge.store.conn.commit()
    captured = []
    async def ok(url, body, headers, timeout=10):
        captured.append(body)
        return 200, b'{}'
    monkeypatch.setattr(bridge, '_http_post', ok)
    asyncio.run(bridge.deliver_pending())
    assert captured == [first_body]
    row = bridge.store.conn.execute('SELECT done,terminal_reason FROM deliveries').fetchone()
    assert tuple(row) == (1, 'delivered')


def test_overlapping_delivery_loops_claim_single_attempt(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s['id'], 'evt_workers', {})
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def post(*args, **kwargs):
            nonlocal count
            count += 1
            started.set()
            await release.wait()
            return 200, b'{}'
        monkeypatch.setattr(bridge, '_http_post', post)
        first = asyncio.create_task(bridge.deliver_pending())
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(bridge.deliver_pending())
        await asyncio.sleep(.01)
        release.set()
        await asyncio.gather(first, second)
        assert count == 1, f'{count} concurrent HTTP attempts for same outbox row'
    asyncio.run(scenario())


def test_successful_delivery_cannot_be_rescheduled_by_stale_failure(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s['id'], 'evt_stale_worker', {})
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def post(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                started.set()
                await release.wait()
                return 503, b'{}'
            return 200, b'{}'
        monkeypatch.setattr(bridge, '_http_post', post)
        first = asyncio.create_task(bridge.deliver_pending())
        await asyncio.wait_for(started.wait(), 2)
        try:
            await bridge.deliver_pending()
        finally:
            release.set()
        await first
        row = bridge.store.conn.execute('SELECT done,terminal_reason FROM deliveries').fetchone()
        assert tuple(row) == (1, 'delivered'), 'late 503 changed delivered row back to pending'
    asyncio.run(scenario())


def test_single_delivery_loop_cannot_reopen_revoked_row(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s['id'], 'evt_revoked_inflight', {})
    async def fail_after_revocation(*args, **kwargs):
        # The external /revoke route is tested separately. Here exercise its
        # intended storage effect during one in-flight delivery, without overlap.
        await bridge.provider.revoke_token('at_fixture')
        return 503, b'{}'
    monkeypatch.setattr(bridge, '_http_post', fail_after_revocation)
    asyncio.run(bridge.deliver_pending())
    row = bridge.store.conn.execute('SELECT done,terminal_reason FROM deliveries').fetchone()
    assert tuple(row) == (1, 'access_revoked'), 'late failure reopened an explicitly revoked delivery'


def test_access_revocation_before_tcp_write_prevents_send(bridge, monkeypatch):
    s = stored_sub(bridge)
    def validate(url):
        return 'callback.example', '93.184.216.34', 443
    monkeypatch.setattr(bridge, '_validate_callback_url', validate)
    calls = []
    class Connection:
        def __init__(self, *args):
            pass
        def connect(self):
            # Network has not transmitted request bytes yet; revoke during TLS/setup.
            bridge.store.deactivate_sub(s['id'])
        def request(self, *args, **kwargs):
            self.connect()
            calls.append('request-written')
        def getresponse(self):
            return self
        status = 200
        def read(self, n):
            return b'{}'
        def close(self):
            pass
    monkeypatch.setattr(bs, 'PinnedHTTPSConnection', Connection)
    result = bridge._post_pinned(s['callback_url'], b'{}', {
        'webhook-id': 'evt_prewrite', 'X-MCP-Subscription-Id': s['id']}, 1)
    assert calls == [], (calls, result)


def test_authorization_code_sequential_http_reuse_rejected(http_api):
    c, b, reg = http_api
    verifier = 'R' * 43
    challenge = base64.urlsafe_b64encode(__import__('hashlib').sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    code = AuthorizationCode(code='code_one_use', client_id=reg['client_id'],
        redirect_uri='https://callback.example/oauth', redirect_uri_provided_explicitly=True,
        expires_at=time.time() + 600, scopes=['bridge'], code_challenge=challenge,
        resource=BASE + '/mcp', subject='owner')
    b.store.save_code(code)
    data = {'grant_type': 'authorization_code', 'client_id': reg['client_id'],
        'client_secret': reg['client_secret'], 'code': code.code,
        'redirect_uri': 'https://callback.example/oauth', 'code_verifier': verifier}
    assert c.post('/token', data=data).status_code == 200
    second = c.post('/token', data=data)
    assert second.status_code == 400 and second.json()['error'] == 'invalid_grant'


def test_refresh_sequential_http_reuse_rejected(http_api):
    c, b, reg = http_api
    token = RefreshToken(token='rt_one_use', client_id=reg['client_id'], scopes=['bridge'],
                         subject='owner', resource=BASE + '/mcp', expires_at=int(time.time()) + 600)
    b.store.save_refresh(token)
    data = {'grant_type': 'refresh_token', 'client_id': reg['client_id'],
        'client_secret': reg['client_secret'], 'refresh_token': token.token}
    assert c.post('/token', data=data).status_code == 200
    second = c.post('/token', data=data)
    assert second.status_code == 400 and second.json()['error'] == 'invalid_grant'


@pytest.mark.parametrize('kind', ['code', 'refresh'])
def test_stale_loaded_oauth_grant_cannot_be_exchanged_twice(bridge, kind):
    # Provider boundary, not a claim of a default single-loop HTTP exploit.
    client = OAuthClientInformationFull(client_id='c', redirect_uris=['https://callback.example/oauth'])
    if kind == 'code':
        grant = AuthorizationCode(code='code_race', client_id='c',
            redirect_uri='https://callback.example/oauth', redirect_uri_provided_explicitly=True,
            expires_at=time.time()+600, scopes=['bridge'], code_challenge='R'*43,
            resource=BASE+'/mcp', subject='owner')
        bridge.store.save_code(grant)
        async def exchange():
            return await bridge.provider.exchange_authorization_code(client, grant)
    else:
        grant = RefreshToken(token='rt_race', client_id='c', scopes=['bridge'], subject='owner',
                             resource=BASE+'/mcp', expires_at=int(time.time())+600)
        bridge.store.save_refresh(grant)
        async def exchange():
            return await bridge.provider.exchange_refresh_token(client, grant, ['bridge'])
    asyncio.run(exchange())
    with pytest.raises(TokenError):
        asyncio.run(exchange())
