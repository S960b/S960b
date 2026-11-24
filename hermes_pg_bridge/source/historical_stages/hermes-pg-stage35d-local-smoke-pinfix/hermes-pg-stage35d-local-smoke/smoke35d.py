#!/usr/bin/env python3
"""Real local MCP read-only smoke test. Never print the local secret or SQL data.
Uses HTTP/JSON-RPC against localhost:18777. Does not write to the database.
"""
from __future__ import annotations
import http.client,json,os,re,sys,time,urllib.error,urllib.request
from pathlib import Path

HOST='127.0.0.1';PORT=18777
URL=f'http://{HOST}:{PORT}/mcp'
TIMEOUT=8
class SmokeError(Exception):pass

def need(c,code):
    if not c:raise SmokeError(code)

def fetch(method,path,body=None,headers=None):
    req=urllib.request.Request(f'http://{HOST}:{PORT}'+path,data=body,
                              method=method,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
            return r.status,dict(r.headers),r.read(131072)
    except urllib.error.HTTPError as e:
        return e.code,dict(e.headers),e.read(4096)

def parse_mcp(body):
    """MCP Streamable HTTP can return JSON or SSE with data: JSON-RPC lines."""
    text=body.decode('utf8')
    if text.startswith('{'):
        obj=json.loads(text)
        need(isinstance(obj,dict),'MCP_BAD_JSON')
        return obj
    for line in text.splitlines():
        if line.startswith('data: '):
            data=line[6:].strip()
            if data and data!='[DONE]':
                obj=json.loads(data)
                if isinstance(obj,dict) and ('result' in obj or 'error' in obj):return obj
    raise SmokeError('MCP_UNEXPECTED_RESPONSE_FRAMING')

def rpc(method,params=None,req_id=1,*,secret,session=None,protocol='2025-06-18',notification=False):
    headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json',
             'MCP-Protocol-Version':protocol,'X-Bridge-Local-Key':secret}
    if session:headers['Mcp-Session-Id']=session
    req={'jsonrpc':'2.0','method':method}
    if not notification:req['id']=req_id
    if params is not None:req['params']=params
    status,h,raw=fetch('POST','/mcp',json.dumps(req,separators=(',',':')).encode(),headers)
    if notification:
        need(status in (200,202,204),'MCP_INITIALIZED_REJECTED')
        return {'status':status},h
    need(status==200,'MCP_RPC_HTTP_NOT_200_'+str(status))
    parsed=parse_mcp(raw)
    need('error' not in parsed,'MCP_JSONRPC_ERROR')
    need(isinstance(parsed.get('result'),dict),'MCP_RPC_RESULT_NOT_OBJECT')
    return parsed['result'],h

def tool_payload(result):
    need(isinstance(result,dict) and result.get('isError') is not True,
         'MCP_TOOL_ERROR')
    structured=result.get('structuredContent')
    if isinstance(structured,dict) and 'ok' in structured:return structured
    for item in result.get('content',[]):
        if isinstance(item,dict) and item.get('type')=='text' and isinstance(item.get('text'),str):
            try:
                value=json.loads(item['text'])
            except ValueError:continue
            if isinstance(value,dict) and 'ok' in value:return value
    raise SmokeError('MCP_TOOL_RESULT_UNEXPECTED')

def run():
    sys.path.insert(0,'/home/kali/hermes-pg-stage35c-static-guard')
    from static_guard35c import local_secret
    secret=local_secret().decode('ascii')
    need(len(secret)==64,'LOCAL_SECRET_INVALID')
    status,_,raw=fetch('GET','/ping')
    need(status==200 and b'stage35c-static-shadow' in raw,'PING_NOT_SHADOW')
    s,_,_=fetch('POST','/mcp',b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                {'Accept':'application/json, text/event-stream','Content-Type':'application/json'})
    need(s==403,'UNAUTHENTICATED_MCP_ACCESS_ALLOWED')
    s,_,_=fetch('POST','/mcp',b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                {'Accept':'application/json, text/event-stream','Content-Type':'application/json',
                 'X-Bridge-Local-Key':'invalid'})
    need(s==403,'INVALID_LOCAL_KEY_ACCEPTED')
    print('STAGE35D_LOCAL_MCP_MISSING_AND_WRONG_KEY_DENIED')
    result,h=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
                          'clientInfo':{'name':'stage35d-offline-smoke','version':'1.0'}},secret=secret)
    need(isinstance(result.get('protocolVersion'),str),'MCP_HANDSHAKE_INVALID')
    session=next((v for k,v in h.items() if k.lower()=='mcp-session-id'),None)
    protocol=result['protocolVersion']
    rpc('notifications/initialized',secret=secret,session=session,protocol=protocol,notification=True)
    tools,_=rpc('tools/list',req_id=2,secret=secret,session=session,protocol=protocol)
    names={x.get('name') for x in tools.get('tools',[]) if isinstance(x,dict)}
    expected={'managed_health','managed_create_status','managed_list','managed_status',
              'managed_read','managed_send_status','managed_delivery_status',
              'managed_create','managed_set_dispatch','managed_send_task','managed_ack_report'}
    need(names==expected,'MANAGED_TOOLSET_UNEXPECTED')
    print('STAGE35D_LOCAL_MCP_INITIALIZE_AND_EXACT_11_MANAGED_TOOLS_OK')
    response,_=rpc('tools/call',{'name':'managed_health','arguments':{}},req_id=3,
                   secret=secret,session=session,protocol=protocol)
    health=tool_payload(response)
    need(health.get('ok') is True and isinstance(health.get('result'),dict) and
         health['result'].get('connected') is True and health['result'].get('role')=='bridge_chatgpt',
         'MANAGED_HEALTH_NOT_CONNECTED')
    response,_=rpc('tools/call',{'name':'managed_list','arguments':{'offset':0,'limit':10}},
                   req_id=4,secret=secret,session=session,protocol=protocol)
    listing=tool_payload(response)
    items=listing.get('result')
    need(listing.get('ok') is True and isinstance(items,list) and len(items)==1 and
         items[0].get('task_id')=='task_bddaadef89b7458fb7f8cf437dc0f023' and
         items[0].get('dispatch_enabled') is False, 'MANAGED_REGISTRY_NOT_ONE_DISABLED_PILOT')
    print('STAGE35D_REAL_ROLE_BOUND_MANAGED_HEALTH_AND_LIST_READ_ONLY_OK')
    # Zero write calls of any kind: no create, set_dispatch, send_task or ack_report.
    print('STAGE35D_NO_MCP_WRITE_CALLS_PERFORMED')

if __name__=='__main__':
    try:run()
    except (SmokeError,OSError,ValueError,KeyError,TimeoutError) as e:
        print('STAGE35D_STOP_'+(str(e) if isinstance(e,SmokeError) else type(e).__name__),file=sys.stderr)
        raise SystemExit(2)
