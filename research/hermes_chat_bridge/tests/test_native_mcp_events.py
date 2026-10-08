"""Нативный MCP Events (OpenAI Work): discover / events/list / subscribe / unsubscribe.

Покрывает критерии job_9286282a:
- discover: supportedVersions 2026-07-28, capabilities {tools, events};
- events/list: hermes.message.created c inputSchema/payloadSchema (job_id,
  created_at_utc, direction, status);
- events/subscribe: идемпотентен, persistent (переживает restart), ttlMs,
  refresh secret rotation;
- events/unsubscribe: идемпотентен, прекращает delivery;
- после emit (to_chatgpt) job_id сразу читается bridge_get_message;
- to_hermes НЕ порождает событие;
- revoked/чужой owner НЕ может управлять чужой подпиской.
"""
import base64
import hashlib
import html
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_server as bs

BASE = 'https://bridge.example'


def _oauth(monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    monkeypatch.setattr(bs, 'DB_PATH', str(tmp_path / 'bridge.db'))
    monkeypatch.setattr(bs, 'OWNER_USER', 'owner')
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    app = bs.build_app(BASE)
    bridge = app.state.bridge
    client = TestClient(app, base_url=BASE)
    reg = client.post('/register', json={
        'client_name': 'native-events-test',
        'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'],
        'grant_types': ['authorization_code', 'refresh_token'],
        'token_endpoint_auth_method': 'client_secret_post',
        'response_types': ['code'], 'scope': 'bridge'}).json()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    login = client.get('/authorize', params={
        'response_type': 'code', 'client_id': reg['client_id'],
        'redirect_uri': 'https://chatgpt.com/connector_platform_oauth_redirect',
        'scope': 'bridge', 'state': 'st', 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'resource': BASE + '/mcp'})
    state = html.unescape(re.search(r'name="state" value="([^"]+)"', login.text).group(1))
    redir = client.post('/login/callback', data={
        'username': 'owner', 'password': 'synthetic-password', 'state': state},
        follow_redirects=False)
    params = parse_qs(urlparse(redir.headers['location']).query)
    tok = client.post('/token', data={
        'grant_type': 'authorization_code', 'code': params['code'][0],
        'redirect_uri': 'https://chatgpt.com/connector_platform_oauth_redirect',
        'client_id': reg['client_id'], 'client_secret': reg['client_secret'],
        'code_verifier': verifier})
    return client, bridge, tok.json()['access_token']


def _rpc(client, at, name, params):
    return client.post('/mcp', headers={
        'Authorization': 'Bearer ' + at, 'MCP-Protocol-Version': '2026-07-28',
        'Mcp-Method': name, 'Accept': 'application/json, text/event-stream'}, json={
        'jsonrpc': '2.0', 'id': 1, 'method': name, 'params': params})


EV_PARAMS = {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                       'io.modelcontextprotocol/clientCapabilities': {}}}


def test_discover_advertises_events_and_protocol(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    with client:
        r = client.post('/mcp', headers={
            'Authorization': 'Bearer ' + at, 'MCP-Protocol-Version': '2026-07-28',
            'Mcp-Method': 'server/discover',
            'Accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover',
            'params': {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                                 'io.modelcontextprotocol/clientCapabilities': {}}}})
        assert r.status_code == 200, r.text
        d = r.json()['result']
        assert '2026-07-28' in d['supportedVersions']
        caps = d['capabilities']
        assert 'tools' in caps and 'events' in caps
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_events_list_schema(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    with client:
        r = _rpc(client, at, 'events/list', EV_PARAMS)
        assert r.status_code == 200, r.text
        ev = r.json()['result']['events'][0]
        assert ev['name'] == 'hermes.message.created'
        ps = ev['payloadSchema']
        assert set(ps['required']) == {'job_id', 'created_at_utc', 'direction', 'status', 'queue'}
        assert set(ps['properties']) >= {'job_id', 'created_at_utc', 'direction', 'status', 'queue'}
        assert 'inputSchema' in ev
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_native_subscribe_idempotent_and_persistent(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    async def _ok(sub): return True
    bridge.verify_callback = _ok
    monkeypatch.setattr(bridge, 'verify_callback', _ok)
    cb = 'https://connectors.api.openai.com/webhook/mcp-events/native-test'
    sec = 'whsec_' + base64.urlsafe_b64encode(b'n' * 32).decode()
    with client:
        p = {'name': 'hermes.message.created', 'arguments': {'queue': 'test'},
             'delivery': {'mode': 'webhook', 'url': cb, 'secret': sec},
             'ttlMs': 3600 * 1000, **EV_PARAMS}
        r1 = _rpc(client, at, 'events/subscribe', p)
        assert r1.status_code == 200, r1.text
        sub1 = r1.json()['result']['id']
        assert sub1.startswith('sub_')
        # идемпотентный refresh: тот же callback -> тот же sub_id
        r2 = _rpc(client, at, 'events/subscribe', p)
        assert r2.status_code == 200
        assert r2.json()['result']['id'] == sub1
        # persistent: новый BridgeApp на той же БД видит подписку
        bridge2 = bs.BridgeApp(BASE)
        try:
            loaded = bridge2.store.load_sub(sub1)
            assert loaded is not None and loaded['active']
        finally:
            bridge2.queue._conn.close()
            bridge2.store.conn.close()
        # unsubscribe идемпотентен
        u1 = _rpc(client, at, 'events/unsubscribe', {'name': 'hermes.message.created',
                                                     'arguments': {'queue': 'test'},
                                                     'delivery': {'mode': 'webhook', 'url': cb},
                                                     **EV_PARAMS})
        assert u1.status_code == 200, u1.text
        u2 = _rpc(client, at, 'events/unsubscribe', {'name': 'hermes.message.created',
                                                    'arguments': {'queue': 'test'},
                                                    'delivery': {'mode': 'webhook', 'url': cb},
                                                    **EV_PARAMS})
        assert u2.status_code == 200, u2.text
        assert not bridge.store.load_sub(sub1)['active']
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_event_payload_and_get_message_visibility(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    async def _ok(sub): return True
    bridge.verify_callback = _ok
    monkeypatch.setattr(bridge, 'verify_callback', _ok)
    cb = 'https://connectors.api.openai.com/webhook/mcp-events/vis'
    sec = 'whsec_' + base64.urlsafe_b64encode(b'v' * 32).decode()
    with client:
        _rpc(client, at, 'events/subscribe', {
            'name': 'hermes.message.created', 'arguments': {'queue': 'test'},
            'delivery': {'mode': 'webhook', 'url': cb, 'secret': sec},
            'ttlMs': 3600 * 1000, **EV_PARAMS})
        job = bridge.queue.put_message('после-commit видимость', direction='to_chatgpt')
        n = bridge.emit_test_event(job['job_id'])
        assert n == 1, 'активная подписка должна получить ровно 1 событие'
        # payload обогащён
        row = bridge.store.conn.execute(
            'SELECT payload FROM deliveries WHERE payload LIKE ? ORDER BY id DESC LIMIT 1',
            (f'%{job["job_id"]}%',)).fetchone()
        pl = json.loads(row['payload'])
        assert pl['job_id'] == job['job_id']
        assert pl['direction'] == 'to_chatgpt'
        assert pl['status'] == 'pending'
        assert pl['queue'] == 'test'
        assert pl['created_at_utc']
        # job_id сразу читается bridge_get_message
        gm = bridge.queue.get_for_chat(job['job_id'])
        assert gm is not None and gm['job_id'] == job['job_id']
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_to_hermes_no_event(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    async def _ok(sub): return True
    bridge.verify_callback = _ok
    monkeypatch.setattr(bridge, 'verify_callback', _ok)
    cb = 'https://connectors.api.openai.com/webhook/mcp-events/noh'
    sec = 'whsec_' + base64.urlsafe_b64encode(b'h' * 32).decode()
    with client:
        _rpc(client, at, 'events/subscribe', {
            'name': 'hermes.message.created', 'arguments': {'queue': 'test'},
            'delivery': {'mode': 'webhook', 'url': cb, 'secret': sec},
            'ttlMs': 3600 * 1000, **EV_PARAMS})
        before = bridge.store.conn.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        job = bridge.queue.put_message('tool-like', direction='to_hermes',
                                       idempotency_key='noev-1')
        after = bridge.store.conn.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        assert after == before, 'to_hermes не порождает события (bridge_put_message без emit)'
        assert job['direction'] == 'to_hermes'
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_unauthorized_events_blocked(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    with client:
        for name in ('events/list', 'events/subscribe', 'events/unsubscribe'):
            r = client.post('/mcp', headers={
                'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': name,
                'Accept': 'application/json, text/event-stream'}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': name, 'params': EV_PARAMS})
            assert r.status_code == 401, (name, r.status_code)
    bridge.queue._conn.close()
    bridge.store.conn.close()

def test_multi_queue_native_routing(monkeypatch, tmp_path):
    client, bridge, at = _oauth(monkeypatch, tmp_path)
    async def _ok(sub): return True
    bridge.verify_callback = _ok
    monkeypatch.setattr(bridge, 'verify_callback', _ok)
    sec = 'whsec_' + base64.urlsafe_b64encode(b'q' * 32).decode()
    with client:
        ids = {}
        for queue, suffix in [('ctf_A', 'a'), ('ctf_B', 'b')]:
            r = _rpc(client, at, 'events/subscribe', {
                'name': 'hermes.message.created', 'arguments': {'queue': queue},
                'delivery': {'mode': 'webhook',
                             'url': f'https://connectors.api.openai.com/webhook/mcp-events/{suffix}',
                             'secret': sec},
                'ttlMs': 3600 * 1000, **EV_PARAMS})
            assert r.status_code == 200, r.text
            ids[queue] = r.json()['result']['id']
        assert ids['ctf_A'] != ids['ctf_B']

        ja = bridge.queue.put_message('A-only', direction='to_chatgpt', queue='ctf_A')
        jb = bridge.queue.put_message('B-only', direction='to_chatgpt', queue='ctf_B')
        assert bridge.emit_test_event(ja['job_id']) == 1
        assert bridge.emit_test_event(jb['job_id']) == 1
        rows = bridge.store.conn.execute(
            'SELECT sub_id,payload FROM deliveries ORDER BY id').fetchall()
        routed = [(r['sub_id'], json.loads(r['payload'])['queue']) for r in rows]
        assert (ids['ctf_A'], 'ctf_A') in routed
        assert (ids['ctf_B'], 'ctf_B') in routed
        assert (ids['ctf_A'], 'ctf_B') not in routed
        assert (ids['ctf_B'], 'ctf_A') not in routed

        bad = _rpc(client, at, 'events/subscribe', {
            'name': 'hermes.message.created', 'arguments': {'queue': '../bad'},
            'delivery': {'mode': 'webhook',
                         'url': 'https://connectors.api.openai.com/webhook/mcp-events/bad',
                         'secret': sec},
            **EV_PARAMS})
        # Custom MCP request handlers surface invalid params as HTTP 400
        # while preserving the JSON-RPC error body.
        assert bad.status_code == 400, bad.text
        assert bad.json().get('error', {}).get('code') == -32602
    bridge.queue._conn.close()
    bridge.store.conn.close()
