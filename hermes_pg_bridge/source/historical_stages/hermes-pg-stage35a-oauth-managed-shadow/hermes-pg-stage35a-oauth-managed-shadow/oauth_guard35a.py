#!/usr/bin/env python3
"""Pinned OAuth JWT resource-server gate. No token/config -> always reject.

This module does NOT run an authorization server, mint tokens, or trust tunnel
identity as user identity. Every HTTP MCP request gets issuer/audience/sub/scope
validation. No auth bypass for MCP tool discovery or SSE sessions.
"""
from __future__ import annotations
import json, os, re, stat, time
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path('/home/kali/.config/bridge-pg-v2/managed-oauth35a.json')
MAX_POST = 131072
READ_TOOLS = frozenset({'managed_health','managed_create_status','managed_list',
                        'managed_status','managed_read','managed_send_status',
                        'managed_delivery_status'})
WRITE_TOOLS = frozenset({'managed_create','managed_set_dispatch','managed_send_task',
                         'managed_ack_report'})

class AuthDenied(Exception):
    pass

def require(ok, code):
    if not ok: raise AuthDenied(code)

def private_regular(p: Path, *, owner=None):
    try:
        s = p.lstat()
        return (stat.S_ISREG(s.st_mode) and not p.is_symlink() and s.st_nlink == 1
                and (s.st_mode & 0o077) == 0
                and s.st_size <= 65536
                and (owner is None or s.st_uid == owner))
    except OSError:
        return False

def config_from_file(path=CONFIG, *, owner=None):
    """No environment overrides; protects prod from accidental env inheritance."""
    if owner is None: owner=os.geteuid()
    require(private_regular(Path(path), owner=owner), 'auth_config_missing_or_unsafe')
    try:
        obj=json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError): raise AuthDenied('auth_config_invalid') from None
    require(isinstance(obj,dict) and set(obj)=={'issuer','audience','subjects','jwks_file','resource'},
            'auth_config_fields_invalid')
    for k in ('issuer','audience','resource'):
        require(isinstance(obj[k],str) and len(obj[k])<512 and
                obj[k].startswith('https://') and urlparse(obj[k]).hostname and
                not urlparse(obj[k]).username and not urlparse(obj[k]).password,
                'auth_config_url_invalid')
    require(isinstance(obj['subjects'],list) and 1<=len(obj['subjects'])<=8 and
            len(set(obj['subjects']))==len(obj['subjects']) and
            all(isinstance(s,str) and 1<=len(s)<=180 for s in obj['subjects']),
            'auth_subjects_invalid')
    p=Path(obj['jwks_file'])
    require(p.is_absolute() and private_regular(p,owner=owner), 'jwks_file_missing_or_unsafe')
    try: jwks=json.loads(p.read_text(encoding='utf8'))
    except (OSError, ValueError): raise AuthDenied('jwks_invalid') from None
    require(isinstance(jwks,dict) and isinstance(jwks.get('keys'),list) and
            1<=len(jwks['keys'])<=8, 'jwks_invalid')
    keys={}
    for k in jwks['keys']:
        require(isinstance(k,dict) and k.get('kty')=='RSA' and
                k.get('alg') in (None,'RS256') and k.get('use') in (None,'sig') and
                isinstance(k.get('kid'),str) and re.fullmatch(r'[A-Za-z0-9._-]{1,128}', k['kid']) and
                k['kid'] not in keys and k.get('n') and k.get('e') and
                'd' not in k and 'p' not in k and 'q' not in k,
                'jwks_key_invalid')
        keys[k['kid']]=k
    return {**obj,'keys':keys}

def check_token(token:str, cfg:dict, needed_scope:str, now=None):
    """Only RS256 JWT access tokens with pinned public JWKS keys are accepted."""
    try:
        import jwt
        header=jwt.get_unverified_header(token)
        if not isinstance(header,dict) or header.get('alg')!='RS256':
            raise AuthDenied('jwt_algorithm_denied')
        kid=header.get('kid')
        require(isinstance(kid,str) and kid in cfg['keys'], 'jwt_kid_denied')
        key=jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(cfg['keys'][kid]))
        opts={'require':['iss','sub','aud','exp','iat','nbf'],
              'verify_aud':True,'verify_iss':True,'verify_exp':True,
              'verify_nbf':True,'verify_iat':True}
        # Leeway 0 intentionally. exp-i at must be small to prevent long-lived tokens.
        claims=jwt.decode(token,key,algorithms=['RS256'],
                          issuer=cfg['issuer'],audience=cfg['audience'],options=opts,leeway=0)
    except AuthDenied: raise
    except Exception: raise AuthDenied('token_invalid') from None
    now=int(time.time()) if now is None else int(now)
    require(isinstance(claims['sub'],str) and claims['sub'] in cfg['subjects'],
            'subject_not_authorized')
    require(type(claims['iat']) is int and type(claims['nbf']) is int and
            type(claims['exp']) is int and
            claims['iat']<=now and claims['nbf']<=now and
            now < claims['exp'] and 0<claims['exp']-claims['iat']<=3600,
            'jwt_lifetime_invalid')
    scopes=claims.get('scope')
    require(isinstance(scopes,str) and len(scopes)<1024, 'scope_missing')
    authorized=set(scopes.split())
    require(needed_scope in authorized, 'scope_denied')
    return {'sub': claims['sub'], 'scopes': authorized}

def bearer_from_headers(headers):
    found=[v for (k,v) in headers if k.lower()==b'authorization']
    require(len(found)==1,'authorization_header_required')
    try: value=found[0].decode('ascii')
    except UnicodeError: raise AuthDenied('authorization_header_invalid') from None
    require(re.fullmatch(r'Bearer [A-Za-z0-9_\-\.]+',value) is not None,
            'authorization_header_invalid')
    return value[7:]

async def read_limited(receive):
    data=bytearray()
    while True:
        m=await receive()
        require(m['type']=='http.request','http_body_unexpected')
        data.extend(m.get('body',b''))
        require(len(data)<=MAX_POST,'request_too_large')
        if not m.get('more_body',False): return bytes(data)

def required_for_body(body:bytes):
    try: obj=json.loads(body)
    except Exception: raise AuthDenied('invalid_json') from None
    require(isinstance(obj,dict) and isinstance(obj.get('method'),str), 'invalid_rpc')
    method=obj['method']
    if method=='tools/call':
        params=obj.get('params')
        require(isinstance(params,dict) and isinstance(params.get('name'),str), 'invalid_tool')
        name=params['name']
        if name in WRITE_TOOLS: return 'managed:write'
        require(name in READ_TOOLS, 'unknown_tool_denied')
    else:
        require(method in {'initialize','notifications/initialized','ping','tools/list',
                           'resources/list','prompts/list','notifications/cancelled'},
                'unknown_method_denied')
    return 'managed:read'

class MCPAuthGate:
    """ASGI outer wrapper: rejects unauthenticated tool listing, calls and SSE.

    Inspect JSON-RPC POST method before dispatch, independent of SDK task
    context or session-resume behavior. Deny unsupported methods/batches.
    """
    def __init__(self,app,config_path=CONFIG):
        self.app=app
        self.config_path=Path(config_path)

    async def __call__(self,scope,receive,send):
        from starlette.responses import JSONResponse
        if scope['type']=='lifespan':
            await self.app(scope,receive,send)
            return
        if scope['type']!='http':
            await JSONResponse({'error':'unsupported_transport'},status_code=403)(scope,receive,send)
            return
        path=scope.get('path','')
        if path=='/ping' and scope.get('method')=='GET':
            # Response is generic; leaks no DB state or secret.
            await JSONResponse({'service':'stage35a-managed-shadow',
                                'mcp_auth':'required','tunnel_attached':False})(scope,receive,send)
            return
        if path in ('/.well-known/oauth-protected-resource/mcp',
                    '/.well-known/oauth-protected-resource') and scope.get('method')=='GET':
            try:
                cfg=config_from_file(self.config_path)
                metadata={'resource':cfg['resource'],
                          'authorization_servers':[cfg['issuer']],
                          'scopes_supported':['managed:read','managed:write']}
                await JSONResponse(metadata)(scope,receive,send)
            except Exception:
                await JSONResponse({'error':'oauth_not_configured'},status_code=503)(scope,receive,send)
            return
        if path not in ('/mcp','/mcp/'):
            await JSONResponse({'error':'not_found'},status_code=404)(scope,receive,send)
            return
        try:
            cfg=config_from_file(self.config_path)
            token=bearer_from_headers(scope.get('headers',[]))
            method=scope.get('method','')
            require(method in {'POST','GET','DELETE'},'method_denied')
            needed='managed:read'
            saved=None
            if method=='POST':
                body=await read_limited(receive)
                needed=required_for_body(body)
                served=False
                async def replay():
                    nonlocal served
                    if served: return {'type':'http.request','body':b'','more_body':False}
                    served=True
                    return {'type':'http.request','body':body,'more_body':False}
                saved=replay
            check_token(token,cfg,needed)
        except AuthDenied:
            # RFC9728 discovery hint is based only on pinned resource metadata.
            try:
                c=config_from_file(self.config_path)
                resource=urlparse(c['resource'])
                route='/.well-known/oauth-protected-resource'+(resource.path or '')
                meta=f'{resource.scheme}://{resource.netloc}{route}'
                challenge='Bearer resource_metadata=\"'+meta+'\"'
            except Exception:
                challenge='Bearer'
            await JSONResponse({'error':'unauthorized'},status_code=401,
                    headers={'WWW-Authenticate':challenge})(scope,receive,send)
            return
        except Exception:
            await JSONResponse({'error':'auth_unavailable'},status_code=503)(scope,receive,send)
            return
        await self.app(scope,saved or receive,send)
