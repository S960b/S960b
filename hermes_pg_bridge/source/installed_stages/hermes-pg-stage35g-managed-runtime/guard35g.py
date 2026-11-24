#!/usr/bin/env python3
"""Private, file-backed shared secret for an outbound-only Secure MCP Tunnel.

The tunnel-client injects X-Bridge-Local-Key on the final localhost HTTP hop.
No OAuth, public issuer, public inbound port or bearer-forwarding is required.
One trusted operator only: this does not identify individual users.
"""
from __future__ import annotations
import hmac, json, os, re, stat
from pathlib import Path

SECRET=Path('/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c')
HEADER=b'x-bridge-local-key'
MAX_POST=131072
READ_TOOLS=frozenset({'managed_health','managed_create_status','managed_list',
                      'managed_status','managed_read','managed_send_status',
                      'managed_delivery_status'})
WRITE_TOOLS=frozenset({'managed_create','managed_set_dispatch','managed_send_task',
                       'managed_ack_report'})
METHODS=frozenset({'initialize','notifications/initialized','ping','tools/list',
                    'resources/list','prompts/list','notifications/cancelled'})
class AuthDenied(Exception): pass

def need(ok,reason):
    if not ok: raise AuthDenied(reason)

def private_regular(path:Path,*,owner=None):
    try:
        st=Path(path).lstat()
        return (stat.S_ISREG(st.st_mode) and st.st_nlink==1 and
                (st.st_mode&0o077)==0 and (owner is None or st.st_uid==owner)
                and 1<=st.st_size<=65536)
    except OSError: return False

def local_secret(path=SECRET,*,owner=None):
    path=Path(path)
    if owner is None: owner=os.geteuid()
    need(private_regular(path,owner=owner),'local_secret_missing_or_unsafe')
    try:
        val=path.read_bytes().strip()
    except OSError: raise AuthDenied('local_secret_unreadable') from None
    need(re.fullmatch(rb'[A-Za-z0-9_-]{64,128}',val) is not None,'local_secret_invalid')
    return val

def authenticated(headers,secret):
    values=[v for k,v in headers if k.lower()==HEADER]
    need(len(values)==1 and isinstance(values[0],bytes),'local_header_missing_or_ambiguous')
    need(len(values[0])<=128 and hmac.compare_digest(values[0],secret),'local_header_denied')

def required_for_body(body):
    try: obj=json.loads(body)
    except (ValueError,UnicodeError):raise AuthDenied('invalid_json') from None
    need(isinstance(obj,dict) and isinstance(obj.get('method'),str),'invalid_rpc')
    if obj['method']=='tools/call':
        p=obj.get('params')
        need(isinstance(p,dict) and isinstance(p.get('name'),str),'invalid_tool')
        name=p['name']
        need(name in READ_TOOLS|WRITE_TOOLS,'unknown_tool_denied')
    else: need(obj['method'] in METHODS,'unknown_method_denied')
    return obj['method']

async def read_limited(receive):
    buf=bytearray()
    while True:
        msg=await receive()
        need(msg.get('type')=='http.request','invalid_request_stream')
        buf.extend(msg.get('body',b''))
        need(len(buf)<=MAX_POST,'request_too_large')
        if not msg.get('more_body',False):return bytes(buf)

class MCPStaticGate:
    """ASGI request guard. No MCP session, method, tool listing bypasses auth."""
    def __init__(self,app,secret_path=SECRET):
        self.app=app
        self.secret_path=Path(secret_path)

    async def __call__(self,scope,receive,send):
        from starlette.responses import JSONResponse
        if scope['type']=='lifespan':
            await self.app(scope,receive,send)
            return
        if scope['type']!='http':
            await JSONResponse({'error':'transport_denied'},status_code=403)(scope,receive,send)
            return
        if scope.get('path')=='/ping' and scope.get('method')=='GET':
            await JSONResponse({'service':'stage35c-static-shadow','auth':'local_header_required',
                                'tunnel_attached':False})(scope,receive,send)
            return
        if scope.get('path') not in ('/mcp','/mcp/'):
            await JSONResponse({'error':'not_found'},status_code=404)(scope,receive,send)
            return
        try:
            secret=local_secret(self.secret_path)
            authenticated(scope.get('headers',[]),secret)
            method=scope.get('method','')
            need(method in {'POST','GET','DELETE'},'method_denied')
            replay=None
            if method=='POST':
                body=await read_limited(receive)
                required_for_body(body)
                used=False
                async def once():
                    nonlocal used
                    if used:return await receive()  # Stage35F: forward the real disconnect; never spin
                    used=True
                    return {'type':'http.request','body':body,'more_body':False}
                replay=once
        except AuthDenied:
            await JSONResponse({'error':'access_denied'},status_code=403)(scope,receive,send)
            return
        except Exception:
            await JSONResponse({'error':'auth_unavailable'},status_code=503)(scope,receive,send)
            return
        await self.app(scope,replay or receive,send)
