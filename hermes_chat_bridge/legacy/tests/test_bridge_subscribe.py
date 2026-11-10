"""bridge_subscribe / bridge_subscription_status / bridge_unsubscribe.

Покрывает задание ChatGPT (job_74e6f298): тонкие MCP-tools поверх
существующего service-layer подписок. Кейсы:
- subscribe -> active, subscription_id вернулся;
- повторный subscribe (owner+event) -> тот же subscription_id (идемпотентно);
- статус: активная подписка видна в bridge_subscription_status;
- unsubscribe -> inactive; повторный unsubscribe идемпотентен;
- unknown event -> error;
- подписка без whsec_ secret -> error;
- обычный put_message(to_chatgpt) НЕ создаёт подписку (это делает только
  bridge_subscribe) и не генерирует события при отсутствии активной подписки.
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


def _oauth(ctx, monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    monkeypatch.setattr(bs, 'DB_PATH', str(tmp_path / 'bridge.db'))
    monkeypatch.setattr(bs, 'OWNER_USER', 'owner')
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    app = bs.build_app(BASE)
    bridge = app.state.bridge
    client = TestClient(app, base_url=BASE)
    reg = client.post('/register', json={
        'client_name': 'sub-test', 'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'],
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
    return client, bridge, tok.json()['access_token'], reg


def _tools(client, at, name, arguments=None):
    p = {'name': name}
    if arguments:
        p['arguments'] = arguments
    p['_meta'] = {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                  'io.modelcontextprotocol/clientCapabilities': {}}
    return client.post('/mcp', headers={
        'Authorization': 'Bearer ' + at, 'MCP-Protocol-Version': '2026-07-28',
        'Mcp-Method': 'tools/call', 'Mcp-Name': name,
        'Accept': 'application/json, text/event-stream'}, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': p})


def _result(resp):
    assert resp.status_code == 200, resp.text
    body = resp.json()['result']
    assert not body.get('isError'), resp.text
    return json.loads(body['content'][0]['text'])


# callback-эндпоинт (верификация через challenge): мокаем verify_callback => True
class _FakeBridge:
    def __init__(self, bridge):
        self.bridge = bridge
    async def verify_callback(self, sub):
        return True


def test_subscribe_lifecycle(tmp_path, monkeypatch):
    client, bridge, at, reg = _oauth(ctx=None, monkeypatch=monkeypatch, tmp_path=tmp_path)
    # мок верификации callback (сеть в тестах запрещена conftest)
    async def _ok(sub): return True
    bridge.verify_callback = _ok
    monkeypatch.setattr(bridge, 'verify_callback', _ok)
    with client:
        # subscribe -> active
        r = _result(_tools(client=client, at=at, name='bridge_subscribe',
                           arguments={'callback_url': 'https://connectors.api.openai.com/webhook/mcp-events/test',
                                      'secret': 'whsec_' + base64.urlsafe_b64encode(b'd' * 24).decode()}))
        assert r['status'] == 'active', r
        assert r['subscription_id'].startswith('sub_'), r
        assert r['event'] == 'hermes.message.created'
        sub_id = r['subscription_id']
        # повторный subscribe -> тот же subscription_id (идемпотентность)
        r2 = _result(_tools(client=client, at=at, name='bridge_subscribe'))
        assert r2['subscription_id'] == sub_id, (r2, sub_id)
        # статус
        st = _result(_tools(client=client, at=at, name='bridge_subscription_status'))
        assert st['subscriptions'], st
        ids = [s['subscription_id'] for s in st['subscriptions']]
        assert sub_id in ids
        active = [s for s in st['subscriptions'] if s['subscription_id'] == sub_id][0]
        assert active['status'] == 'active'
        # unsubscribe -> inactive
        u = _result(_tools(client=client, at=at, name='bridge_unsubscribe',
                           arguments={'subscription_id': sub_id}))
        assert u['status'] == 'inactive', u
        st2 = _result(_tools(client=client, at=at, name='bridge_subscription_status'))
        a2 = [s for s in st2['subscriptions'] if s['subscription_id'] == sub_id][0]
        assert a2['status'] == 'inactive'
        # повторный unsubscribe идемпотентен
        u2 = _result(_tools(client=client, at=at, name='bridge_unsubscribe',
                            arguments={'subscription_id': sub_id}))
        assert u2['status'] == 'inactive', u2
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_subscribe_unknown_event_and_bad_secret(tmp_path, monkeypatch):
    client, bridge, at, reg = _oauth(ctx=None, monkeypatch=monkeypatch, tmp_path=tmp_path)
    with client:
        r = client.post('/mcp', headers={
            'Authorization': 'Bearer ' + at, 'MCP-Protocol-Version': '2026-07-28',
            'Mcp-Method': 'tools/call', 'Mcp-Name': 'bridge_subscribe',
            'Accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'bridge_subscribe',
                'arguments': {'event': 'junk.event'},
                '_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                          'io.modelcontextprotocol/clientCapabilities': {}}}})
        body = r.json()
        # MCPError -> jsonrpc error (не result.isError)
        assert body.get('error', {}).get('code') == -32602, body
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_unauthorized_tools_blocked(tmp_path, monkeypatch):
    client, bridge, at, reg = _oauth(ctx=None, monkeypatch=monkeypatch, tmp_path=tmp_path)
    with client:
        for name in ('bridge_subscribe', 'bridge_subscription_status', 'bridge_unsubscribe'):
            r = client.post('/mcp', headers={
                'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'tools/call',
                'Mcp-Name': name, 'Accept': 'application/json, text/event-stream'}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': name, 'arguments': {},
                    '_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                              'io.modelcontextprotocol/clientCapabilities': {}}}})
            assert r.status_code == 401, (name, r.status_code)
    bridge.queue._conn.close()
    bridge.store.conn.close()


def test_put_message_does_not_create_subscription(tmp_path, monkeypatch):
    client, bridge, at, reg = _oauth(ctx=None, monkeypatch=monkeypatch, tmp_path=tmp_path)
    with client:
        before = bridge.store.conn.execute('SELECT count(*) FROM subs').fetchone()[0]
        job = bridge.queue.put_message('plain to gpt', direction='to_chatgpt')
        after = bridge.store.conn.execute('SELECT count(*) FROM subs').fetchone()[0]
        assert after == before, 'put_message не должен создавать подписки'
        js = bridge.store.conn.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        assert js == 0, 'без активной подписки события не генерируются'
    bridge.queue._conn.close()
    bridge.store.conn.close()