"""Offline OAuth -> authenticated MCP tools -> CLI read; synthetic state only."""
import sys, base64, hashlib, secrets, re, json, html, os, subprocess
from urllib.parse import urlparse, parse_qs
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bridge_server as bs
from starlette.testclient import TestClient

BASE='https://bridge.example'
def test_actual_oauth_and_reply_roundtrip(tmp_path,monkeypatch):
    monkeypatch.setattr(bs,'DB_PATH',str(tmp_path/'bridge.db'))
    monkeypatch.setattr(bs,'OWNER_USER','owner')
    monkeypatch.setattr(bs,'OWNER_PASS','synthetic-password')
    app=bs.build_app(BASE)
    bridge=app.state.bridge
    try:
        with TestClient(app,base_url=BASE) as client:
            registered=client.post('/register',json={'client_name':'offline-test','redirect_uris':['https://chatgpt.com/connector_platform_oauth_redirect'],'grant_types':['authorization_code','refresh_token'],'token_endpoint_auth_method':'client_secret_post','response_types':['code'],'scope':'bridge'})
            assert registered.status_code==201,registered.text
            reg=registered.json()
            verifier=secrets.token_urlsafe(48)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            login=client.get('/authorize',params={'response_type':'code','client_id':reg['client_id'],'redirect_uri':'https://chatgpt.com/connector_platform_oauth_redirect','scope':'bridge','state':'outside-state','code_challenge':challenge,'code_challenge_method':'S256','resource':BASE+'/mcp'})
            assert login.status_code==200,login.text
            state=html.unescape(re.search(r'name="state" value="([^"]+)"',login.text).group(1))
            assert state!='outside-state'
            redirect=client.post('/login/callback',data={'username':'owner','password':'synthetic-password','state':state},follow_redirects=False)
            assert redirect.status_code==302,redirect.text
            params=parse_qs(urlparse(redirect.headers['location']).query)
            assert params['state']==['outside-state']
            token=client.post('/token',data={'grant_type':'authorization_code','code':params['code'][0],'redirect_uri':'https://chatgpt.com/connector_platform_oauth_redirect','client_id':reg['client_id'],'client_secret':reg['client_secret'],'code_verifier':verifier})
            assert token.status_code==200,token.text
            access=token.json()['access_token']
            def rpc(method,p):
                p=dict(p);p['_meta']={'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}
                headers={'Authorization':'Bearer '+access,'MCP-Protocol-Version':'2026-07-28','Mcp-Method':method,'Accept':'application/json, text/event-stream'}
                if 'name' in p: headers['Mcp-Name']=p['name']
                return client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':method,'params':p})
            discover=rpc('server/discover',{})
            assert discover.status_code==200
            assert 'events' in discover.json()['result']['capabilities']
            job=bridge.queue.put_message('synthetic only')
            answer='BRIDGE_OK:'+job['job_id']
            read=rpc('tools/call',{'name':'bridge_get_message','arguments':{'job_id':job['job_id']}})
            assert 'synthetic only' in read.text
            response=rpc('tools/call',{'name':'bridge_put_reply','arguments':{'job_id':job['job_id'],'text':answer}})
            assert response.status_code==200 and not response.json()['result'].get('isError'),response.text
            assert bridge.queue.get_message(job['job_id'])['reply']==answer
            # bridge_put_message: ChatGPT создаёт новое сообщение для Hermes
            create=rpc('tools/call',{'name':'bridge_put_message','arguments':{'text':'inbound from ChatGPT (synthetic)'}})
            assert create.status_code==200 and not create.json()['result'].get('isError'),create.text
            created=create.json()['result']['content'][0]['text']
            import json as _json
            created_obj=_json.loads(created)
            assert created_obj['status']=='pending',created
            got=bridge.queue.get_message(created_obj['job_id'])
            assert got is not None and got['text']=='inbound from ChatGPT (synthetic)',got
    finally:
        bridge.queue._conn.close();bridge.store.conn.close()
