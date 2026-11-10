"""Нормализация OAuth resource (новый коннектор OpenAI шлёт base без /mcp).

Регрессия: tools/list с токеном, выданным после authorize c resource=base
(без /mcp), должен проходить — SDK-валидатор сравнивает с resource_server_url
= base/mcp, поэтому сервер выдаёт токены с каноническим resource.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_server as bs

BASE = 'https://bridge.example'


def test_normalize_resource_unit():
    n = bs.normalize_resource
    b = BASE
    assert n(None, b) is None
    assert n(b, b) == b + '/mcp'
    assert n(b + '/', b) == b + '/mcp'
    assert n(b + '/mcp', b) == b + '/mcp'
    assert n(b + '/mcp/', b) == b + '/mcp'
    # чуждый resource не трогаем
    assert n('https://other.example/mcp', b) == 'https://other.example/mcp'


def test_oauth_roundtrip_resource_without_mcp(tmp_path, monkeypatch):
    """Полный OAuth: authorize c resource=base (как новый коннектор OpenAI),
    токен должен получить resource=base/mcp, tools/list -> 200."""
    import base64, hashlib, html, json, re, secrets
    from starlette.testclient import TestClient
    from urllib.parse import urlparse, parse_qs

    monkeypatch.setattr(bs, 'DB_PATH', str(tmp_path / 'bridge.db'))
    monkeypatch.setattr(bs, 'OWNER_USER', 'owner')
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    app = bs.build_app(BASE)
    bridge = app.state.bridge
    try:
        with TestClient(app, base_url=BASE) as client:
            reg = client.post('/register', json={
                'client_name': 'new-connector',
                'redirect_uris': ['https://chatgpt.com/connector/oauth/xyz'],
                'grant_types': ['authorization_code', 'refresh_token'],
                'token_endpoint_auth_method': 'client_secret_post',
                'response_types': ['code'], 'scope': 'bridge'}).json()
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            # resource БЕЗ /mcp — как шлёт новый коннектор OpenAI
            login = client.get('/authorize', params={
                'response_type': 'code', 'client_id': reg['client_id'],
                'redirect_uri': 'https://chatgpt.com/connector/oauth/xyz',
                'scope': 'bridge', 'state': 'st', 'code_challenge': challenge,
                'code_challenge_method': 'S256', 'resource': BASE})
            assert login.status_code == 200
            state = html.unescape(re.search(r'name="state" value="([^"]+)"', login.text).group(1))
            redir = client.post('/login/callback', data={
                'username': 'owner', 'password': 'synthetic-password', 'state': state},
                follow_redirects=False)
            params = parse_qs(urlparse(redir.headers['location']).query)
            tok = client.post('/token', data={
                'grant_type': 'authorization_code', 'code': params['code'][0],
                'redirect_uri': 'https://chatgpt.com/connector/oauth/xyz',
                'client_id': reg['client_id'], 'client_secret': reg['client_secret'],
                'code_verifier': verifier})
            assert tok.status_code == 200, tok.text
            at = tok.json()['access_token']
            # выданный токен должен иметь resource с /mcp (нормализован)
            stored = [t for t in bridge.store.load_access(at).items()] if hasattr(bridge.store.load_access(at), 'items') else None
            row = bridge.store.conn.execute(
                'SELECT resource FROM access_tokens WHERE token=?', (at,)).fetchone()
            assert row['resource'] == BASE + '/mcp', row['resource']

            def rpc(method, payload):
                p = dict(payload); p['_meta'] = {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                                                'io.modelcontextprotocol/clientCapabilities': {}}
                headers = {'Authorization': 'Bearer ' + at, 'MCP-Protocol-Version': '2026-07-28',
                           'Mcp-Method': method, 'Accept': 'application/json, text/event-stream'}
                if 'name' in p:
                    headers['Mcp-Name'] = p['name']
                return client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1,
                                      'method': method, 'params': p})

            tl = rpc('tools/list', {})
            assert tl.status_code == 200, tl.text
            names = sorted(t['name'] for t in tl.json()['result']['tools'])
            assert names == ['bridge_get_message', 'bridge_put_message',
                             'bridge_put_reply', 'bridge_subscribe',
                             'bridge_subscription_status', 'bridge_unsubscribe'], names
    finally:
        bridge.queue._conn.close()
        bridge.store.conn.close()


def test_refresh_token_normalizes_resource(tmp_path, monkeypatch):
    """Refresh-цепочка: старый refresh c resource без /mcp -> новый access c /mcp."""
    monkeypatch.setattr(bs, 'DB_PATH', str(tmp_path / 'bridge.db'))
    monkeypatch.setattr(bs, 'OWNER_USER', 'owner')
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    app = bs.build_app(BASE)
    bridge = app.state.bridge
    try:
        # вручную завести refresh-токен с «кривым» resource (имитация нового коннектора)
        import time
        rt = 'rt_fixture_normalize'
        bridge.store.save_refresh(bs.RefreshToken(
            token=rt, client_id='client-x', scopes=['bridge'],
            expires_at=int(time.time()) + 86400, resource=BASE, subject='owner'))
        lr = bridge.provider.store.load_refresh(rt)
        import asyncio
        class FakeClient:
            client_id = 'client-x'
        out = asyncio.run(bridge.provider.exchange_refresh_token(FakeClient(), lr, scopes=['bridge']))
        row = bridge.store.conn.execute(
            'SELECT resource FROM access_tokens WHERE token=?', (out.access_token,)).fetchone()
        assert row['resource'] == BASE + '/mcp', row['resource']
    finally:
        bridge.queue._conn.close()
        bridge.store.conn.close()