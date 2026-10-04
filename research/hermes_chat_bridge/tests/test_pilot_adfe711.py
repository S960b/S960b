import asyncio
import base64
import sqlite3
import pytest
from starlette.testclient import TestClient
import bridge_server as bs
from test_review_3b082fe import bridge, sub_params, BASE
from test_review_adfe711 import http_api, http_rpc
from mcp.server.auth.provider import AccessToken


def test_explicit_database_required(monkeypatch):
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    monkeypatch.setattr(bs, 'DB_PATH', None)
    with pytest.raises(RuntimeError, match='BRIDGE_DB_PATH'):
        bs.build_app(BASE)


@pytest.mark.parametrize('path', ['relative.db', '/tmp/../tmp/pilot.db'])
def test_unsafe_path_rejected(path, monkeypatch):
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    monkeypatch.setattr(bs, 'DB_PATH', path)
    with pytest.raises(RuntimeError, match='path'):
        bs.build_app(BASE)


def test_legacy_startup_refused_without_change(tmp_path, monkeypatch):
    path = tmp_path/'legacy.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE old_data(value TEXT)')
    conn.execute("INSERT INTO old_data VALUES('untouched')")
    conn.commit(); conn.close()
    before = path.read_bytes()
    monkeypatch.setattr(bs, 'DB_PATH', str(path))
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    with pytest.raises(RuntimeError, match='unversioned'):
        bs.build_app(BASE)
    assert path.read_bytes() == before


def test_pilot_lock_and_versioned_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'DB_PATH', str(tmp_path/'pilot.db'))
    monkeypatch.setattr(bs, 'OWNER_PASS', 'synthetic-password')
    app = bs.build_app(BASE)
    with TestClient(app, base_url=BASE) as c:
        with pytest.raises(RuntimeError, match='process'):
            bs.build_app(BASE)
        health = c.get('/ping').json()
        assert health['process_mode'] == 'single-process-pilot'
        assert health['git_revision'] == app.state.bridge.build['git_revision']
        assert health['git_revision'] == 'unknown' or len(health['git_revision']) == 40
    app.state.bridge.store.conn.close(); app.state.bridge.queue._conn.close()
    reopened = bs.build_app(BASE)
    with TestClient(reopened, base_url=BASE):
        assert reopened.state.bridge.store.conn.execute('PRAGMA user_version').fetchone()[0] == bs.PILOT_SCHEMA_VERSION
    reopened.state.bridge.store.conn.close(); reopened.state.bridge.queue._conn.close()


@pytest.mark.parametrize('rotation', [False, True])
def test_reverse_completion_latest_generation_wins(bridge, monkeypatch, rotation):
    monkeypatch.setattr(bridge, '_validate_callback_url', lambda u: None)
    if rotation:
        async def ok(sub): return True
        monkeypatch.setattr(bridge, 'verify_callback', ok)
        asyncio.run(bridge.events_subscribe(None, sub_params()))
    async def scenario():
        started = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        count = 0
        async def verify(sub):
            nonlocal count
            index = count; count += 1
            started[index].set()
            await release[index].wait()
            return True
        monkeypatch.setattr(bridge, 'verify_callback', verify)
        first_params, second_params = sub_params(), sub_params()
        first_params['delivery']['secret'] = 'whsec_'+base64.b64encode(b'A'*32).decode()
        second_params['delivery']['secret'] = 'whsec_'+base64.b64encode(b'B'*32).decode()
        first = asyncio.create_task(bridge.events_subscribe(None, first_params))
        await asyncio.wait_for(started[0].wait(), 2)
        second = asyncio.create_task(bridge.events_subscribe(None, second_params))
        await asyncio.wait_for(started[1].wait(), 2)
        release[1].set(); result = await second
        release[0].set(); await first
        saved = bridge.store.load_sub(result['id'])
        assert saved['active']
        assert saved['secret'] == second_params['delivery']['secret']
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['access_token', 'refresh_token'])
def test_revoke_metadata_idempotency_wrong_client_and_mcp(http_api, kind):
    c, b, reg = http_api
    from mcp.server.auth.provider import RefreshToken
    token = (AccessToken if kind == 'access_token' else RefreshToken)(token='proof-token', client_id=reg['client_id'], scopes=['bridge'], subject='owner', resource=BASE+'/mcp')
    (b.store.save_access if kind == 'access_token' else b.store.save_refresh)(token)
    access = AccessToken(token='related-access', client_id=reg['client_id'], scopes=['bridge'], subject='owner', resource=BASE+'/mcp')
    b.store.save_access(access)
    metadata = c.get('/.well-known/oauth-authorization-server').json()
    assert metadata['revocation_endpoint'] == BASE+'/revoke'
    other = c.post('/register', json={'redirect_uris':['https://callback.example/oauth'], 'token_endpoint_auth_method':'client_secret_post'}).json()
    data = {'client_id':other['client_id'], 'client_secret':other['client_secret'], 'token':token.token, 'token_type_hint':kind}
    assert c.post('/revoke', data=data).status_code == 200
    assert b.store.load_access(access.token) is not None
    assert (b.store.load_access if kind == 'access_token' else b.store.load_refresh)(token.token) is not None
    assert http_rpc(c, 'tools/list', {}, access.token).status_code == 200
    data.update(client_id=reg['client_id'], client_secret=reg['client_secret'])
    assert c.post('/revoke', data=data).status_code == 200
    assert c.post('/revoke', data=data).status_code == 200
    assert http_rpc(c, 'tools/list', {}, access.token).status_code == 401


def test_mcp_reports_actual_version(http_api):
    c, b, reg = http_api
    token = AccessToken(token='version-proof', client_id=reg['client_id'], scopes=['bridge'], subject='owner', resource=BASE+'/mcp')
    b.store.save_access(token)
    response = http_rpc(c, 'tools/list', {}, token.token).json()
    assert response['result']['_meta']['io.modelcontextprotocol/serverInfo']['version'] == bs.VERSION


def test_build_identity_is_frozen_per_app(bridge, monkeypatch):
    from types import SimpleNamespace
    revision = 'a' * 40
    def git(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=revision+'\n' if 'rev-parse' in args else '')
    monkeypatch.setattr(bs.subprocess, 'run', git)
    assert bs.process_build()['git_revision'] == revision
    old_build = dict(bridge.build)
    revision = 'b' * 40
    assert bridge.build == old_build
    assert bs.process_build()['git_revision'] == revision
    monkeypatch.setattr(bs.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='dirty'))
    assert bs.process_build()['git_revision'] == 'unknown'
