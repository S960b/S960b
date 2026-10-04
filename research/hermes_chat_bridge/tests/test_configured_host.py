import time
import bridge_server as bs
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient
from test_review_3b082fe import rpc


def test_exact_configured_host_with_port(tmp_path,monkeypatch):
    base='http://127.0.0.1:23456'
    monkeypatch.setattr(bs,'DB_PATH',str(tmp_path/'bridge.db'))
    monkeypatch.setattr(bs,'OWNER_PASS','synthetic-password')
    app=bs.build_app(base)
    b=app.state.bridge
    b.store.save_access(AccessToken(token='at_review',client_id='c',scopes=['bridge'],subject='owner',resource=base+'/mcp',expires_at=int(time.time())+600))
    try:
        with TestClient(app,base_url=base) as c:
            response=rpc(c,'tools/list')
            assert response.status_code==200,response.text
            response=c.post('/mcp',headers={'Host':'127.0.0.1:33333','Authorization':'Bearer at_review','MCP-Protocol-Version':'2026-07-28','Mcp-Method':'tools/list','Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}}})
            assert response.status_code==421
    finally:
        b.queue._conn.close();b.store.conn.close()
