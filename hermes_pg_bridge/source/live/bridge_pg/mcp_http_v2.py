"""Independent PostgreSQL MCP service, no Git/legacy code imports or runtime.

OAuth definitions are pinned/copied using tools/vendor_oauth.py once during
installation. OAuth sessions are stored in a PRIVATE NEW SQLite auth database;
dialogues and delivery are stored solely in PostgreSQL via Bridge PG v2.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from . import _oauth_v2_vendor as oauth
from .mcp_v2 import register_mcp
from .store import Store as PGStore

PORT = 8766
HOST = '127.0.0.1'
SCOPE = 'bridge'
CFG = Path('/home/kali/.config/bridge-pg-v2')
STATE = Path('/home/kali/.local/state/bridge-pg-v2')


def _read_private(path:Path)->str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('auth_file_missing_or_not_regular')
    meta=path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_nlink != 1 or meta.st_mode & 0o077 or meta.st_size>1024:
        raise RuntimeError('auth_file_unsafe_permissions')
    value=path.read_text('utf-8').strip()
    if not 24<=len(value)<=256 or '\n' in value or '\r' in value:
        raise RuntimeError('auth_password_invalid')
    return value


def _base_url():
    raw=os.environ.get('PG_V2_MCP_BASE','http://127.0.0.1:8766').rstrip('/')
    p=urlparse(raw)
    if not p.hostname or p.username or p.password or p.query or p.fragment or p.params or p.path not in ('','/'):
        raise RuntimeError('mcp_base_invalid')
    if p.scheme!='https' and raw!='http://127.0.0.1:8766':
        raise RuntimeError('mcp_base_must_be_https_or_local_test')
    if p.scheme=='https' and p.hostname in ('127.0.0.1','localhost'):
        raise RuntimeError('mcp_base_invalid')
    if not re.fullmatch(r'[A-Za-z0-9.:-]+',p.netloc):
        raise RuntimeError('mcp_base_invalid')
    return raw

class OwnerBridge:
    """Verified owner identity is never inferred from tool-call arguments."""
    def __init__(self,base_url):
        STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
        if STATE.is_symlink() or STATE.stat().st_uid!=os.getuid() or STATE.stat().st_mode&0o077:
            raise RuntimeError('auth_state_directory_unsafe')
        db=str(STATE/'oauth.sqlite')
        self.store=oauth.Store(db)
        os.chmod(db,0o600)
        self.base_url=base_url
        self.provider=oauth.BridgeOAuthProvider(self.store,base_url)

    def require_owner(self):
        token=get_access_token()
        if (token is None or token.subject!=oauth.OWNER_USER or SCOPE not in token.scopes
                or token.resource!=self.base_url+'/mcp'
                or (token.expires_at is not None and token.expires_at<=time.time())
                or self.store.load_access(token.token) is None):
            raise PermissionError('Authenticated owner required')
        return token.subject

    async def login_handler(self,request:Request):
        state=request.query_params.get('state')
        if not state or self.store.load_state(state) is None:
            raise HTTPException(400,'Invalid state')
        return oauth.login_page(state,self.base_url)

    async def login_callback(self,request:Request):
        form=await request.form()
        user=str(form.get('username') or '')
        pwd=str(form.get('password') or '')
        state=str(form.get('state') or '')
        if self.store.load_state(state) is None:
            raise HTTPException(400,'Invalid state')
        if not self.store.reserve_login_attempt(oauth.OWNER_USER):
            raise HTTPException(429,'Login attempt budget exhausted')
        valid=(hmac.compare_digest(user.encode(),oauth.OWNER_USER.encode()) and
               bool(oauth.OWNER_PASS) and hmac.compare_digest(pwd.encode(),oauth.OWNER_PASS.encode()))
        sd=self.store.attempt_state(state,bool(valid))
        if sd is None:raise HTTPException(400,'Invalid state')
        if not valid:raise HTTPException(401,'Invalid credentials')
        import secrets
        from mcp.server.auth.provider import AuthorizationCode, construct_redirect_uri
        code='code_'+secrets.token_urlsafe(24)
        self.store.save_code(AuthorizationCode(
            code=code, client_id=sd['client_id'],redirect_uri=sd['redirect_uri'] or None,
            redirect_uri_provided_explicitly=sd['redirect_uri_provided_explicitly'],
            expires_at=time.time()+300,scopes=[SCOPE],code_challenge=sd['code_challenge'],
            resource=oauth.normalize_resource(sd.get('resource'),self.base_url),subject=user))
        self.store.del_state(state)
        return RedirectResponse(url=construct_redirect_uri(
            sd['redirect_uri'],code=code,state=sd.get('client_state')),status_code=302)


def create_app():
    # Fail closed on database availability before binding a port. The SSH
    # tunnel/database is the authority for task data; auth SQLite is separate.
    if PGStore().health().get('ok') is not True:
        raise RuntimeError('postgres_health_unavailable')
    oauth.OWNER_USER='owner'
    oauth.OWNER_PASS=_read_private(CFG/'mcp-owner-password')
    base=_base_url()
    bridge=OwnerBridge(base)
    mcp=MCPServer(
        name='PostgreSQL Bridge v2',version='2.0.0',
        instructions='PostgreSQL dialogues; use dialog_view to read MT reports. No Git.',
        debug=False,
        auth=AuthSettings(
            issuer_url=base,
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],resource_server_url=base+'/mcp',
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,valid_scopes=[SCOPE],default_scopes=[SCOPE])),
        auth_server_provider=bridge.provider)
    names=register_mcp(mcp,bridge)
    if len(names)!=9:
        raise RuntimeError('mcp_register_incomplete')

    @mcp.custom_route('/ping',methods=['GET'])
    async def ping(request:Request):
        return JSONResponse({'ok':True,'service':'pg-bridge-v2'})

    @mcp.custom_route('/login',methods=['GET'])
    async def login(request:Request):
        return await bridge.login_handler(request)

    @mcp.custom_route('/login/callback',methods=['POST'])
    async def callback(request:Request):
        return await bridge.login_callback(request)

    allowed=[urlparse(base).netloc,'127.0.0.1:8766','localhost:8766']
    app=mcp.streamable_http_app(
        streamable_http_path='/mcp',host=HOST,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=sorted(set(allowed)),allowed_origins=[]))
    # Preserve the SDK transport lifespan; no independent background task.
    return app


def main():
    # OAuth DB singleton: prevent competing v2 processes on the same port/state.
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    lockpath=STATE/'oauth.lock'
    fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        app=create_app()
        import uvicorn
        cfg=uvicorn.Config(app,host=HOST,port=PORT,log_level='warning',access_log=False)
        uvicorn.Server(cfg).run()
    finally:
        os.close(fd)

if __name__=='__main__':main()
