"""Acceptance checks for review's login throttling and argument validation."""
import sys,asyncio,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bridge_server as bs
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route
import base64
import pytest
from mcp.shared.exceptions import MCPError

def test_extra_event_arguments_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(bs,'DB_PATH',str(tmp_path/'bridge.db'))
    monkeypatch.setattr(bs,'OWNER_USER','owner')
    b=bs.BridgeApp('https://bridge.example')
    token=AccessToken(token='synthetic',client_id='c',scopes=['bridge'],subject='owner',resource=b.base_url+'/mcp',expires_at=int(time.time())+100)
    b.store.save_access(token)
    context=auth_context_var.set(AuthenticatedUser(token))
    callbacks=[]
    monkeypatch.setattr(b,'_validate_callback_url',lambda u:None)
    async def accept(sub): callbacks.append(sub);return True
    monkeypatch.setattr(b,'verify_callback',accept)
    try:
        with pytest.raises(MCPError) as error:
            asyncio.run(b.events_subscribe(None,{'name':'hermes.message.created','arguments':{'queue':'test','unexpected':'field'},'delivery':{'mode':'webhook','url':'https://callback.example/hook','secret':'whsec_'+base64.b64encode(b'x'*32).decode()}}))
        assert error.value.code == -32602
        assert callbacks==[]
    finally:
        auth_context_var.reset(context);b.store.conn.close();b.queue._conn.close()

def test_new_states_do_not_reset_login_failure_budget(tmp_path,monkeypatch):
    monkeypatch.setattr(bs,'DB_PATH',str(tmp_path/'bridge.db'))
    monkeypatch.setattr(bs,'OWNER_USER','owner');monkeypatch.setattr(bs,'OWNER_PASS','synthetic-password')
    b=bs.BridgeApp('https://bridge.example')
    app=Starlette(routes=[Route('/login/callback',b.login_callback,methods=['POST'])])
    try:
        with TestClient(app) as client:
            for i in range(25):
                state='s'+str(i)
                b.store.save_state(state,{'client_id':'c','redirect_uri':'https://callback.example/oauth','redirect_uri_provided_explicitly':True,'code_challenge':'R'*43,'resource':b.base_url+'/mcp'})
                response=client.post('/login/callback',data={'state':state,'username':'owner','password':'wrong'},follow_redirects=False)
            assert response.status_code==429
    finally:
        b.store.conn.close();b.queue._conn.close()
