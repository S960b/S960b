#!/usr/bin/env python3
"""Offline authorization, fail-closed and managed-only contract tests."""
import asyncio,base64,hashlib,json,os,stat,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from oauth_guard35a import (AuthDenied,MCPAuthGate,READ_TOOLS,WRITE_TOOLS,
    bearer_from_headers,check_token,config_from_file,read_limited,
    required_for_body,private_regular)
import runtime35a as rt

class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update({'kid':'key-1','alg':'RS256','use':'sig'})
        cls.cfg={'issuer':'https://accounts.test.example/','audience':'https://example.net/mcp',
                 'subjects':['operator-1'], 'keys':{'key-1':cls.jwk}}
    def token(self,**updates):
        now=int(time.time())
        claims={'iss':self.cfg['issuer'],'aud':self.cfg['audience'],'sub':'operator-1',
                'iat':now-1,'nbf':now-1,'exp':now+120,
                'scope':'managed:read managed:write'}
        claims.update(updates)
        return jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'key-1'})
    def test_01_good_read(self):
        self.assertEqual(check_token(self.token(),self.cfg,'managed:read')['sub'],'operator-1')
    def test_02_good_write(self):
        self.assertIn('managed:write',check_token(self.token(),self.cfg,'managed:write')['scopes'])
    def test_03_missing_scope(self):
        with self.assertRaises(AuthDenied):check_token(self.token(scope='managed:read'),self.cfg,'managed:write')
    def test_04_wrong_subject(self):
        with self.assertRaises(AuthDenied):check_token(self.token(sub='someone-else'),self.cfg,'managed:read')
    def test_05_wrong_issuer(self):
        with self.assertRaises(AuthDenied):check_token(self.token(iss='https://evil.example/'),self.cfg,'managed:read')
    def test_06_wrong_audience(self):
        with self.assertRaises(AuthDenied):check_token(self.token(aud='https://wrong.example'),self.cfg,'managed:read')
    def test_07_expired(self):
        with self.assertRaises(AuthDenied):check_token(self.token(iat=int(time.time())-100,exp=int(time.time())-20),self.cfg,'managed:read')
    def test_08_overlong_lifetime(self):
        with self.assertRaises(AuthDenied):check_token(self.token(exp=int(time.time())+10000),self.cfg,'managed:read')
    def test_09_missing_kid(self):
        token=jwt.encode({'sub':'operator-1'},self.key,algorithm='RS256')
        with self.assertRaises(AuthDenied):check_token(token,self.cfg,'managed:read')
    def test_10_unsigned_rejected(self):
        token=jwt.encode({'sub':'operator-1'},key='',algorithm='none',headers={'kid':'key-1'})
        with self.assertRaises(AuthDenied):check_token(token,self.cfg,'managed:read')
    def test_11_unknown_key(self):
        t=jwt.encode({'sub':'operator-1'},self.key,algorithm='RS256',headers={'kid':'nope'})
        with self.assertRaises(AuthDenied):check_token(t,self.cfg,'managed:read')
    def test_12_malformed_token(self):
        with self.assertRaises(AuthDenied):check_token('one.two.three',self.cfg,'managed:read')
    def test_13_missing_authorization_header(self):
        with self.assertRaises(AuthDenied):bearer_from_headers([])
    def test_14_duplicate_headers(self):
        with self.assertRaises(AuthDenied):bearer_from_headers([(b'authorization',b'Bearer aaa'),(b'AUTHORIZATION',b'Bearer bbb')])
    def test_15_token_extraction(self):
        self.assertEqual(bearer_from_headers([(b'authorization',b'Bearer a.b.c')]),'a.b.c')
    def test_16_write_tools_scoped(self):
        for name in WRITE_TOOLS:
            self.assertEqual(required_for_body(json.dumps({'method':'tools/call','params':{'name':name}}).encode()),'managed:write')
    def test_17_read_tools_scoped(self):
        for name in READ_TOOLS:
            self.assertEqual(required_for_body(json.dumps({'method':'tools/call','params':{'name':name}}).encode()),'managed:read')
    def test_18_unknown_tool_denied(self):
        with self.assertRaises(AuthDenied):required_for_body(b'{"method":"tools/call","params":{"name":"bridge_app_sql"}}')
    def test_19_json_batch_denied(self):
        with self.assertRaises(AuthDenied):required_for_body(b'[{"method":"tools/call"}]')
    def test_20_invalid_method_denied(self):
        with self.assertRaises(AuthDenied):required_for_body(b'{"method":"evil/method"}')
    def test_21_no_auth_config_denied(self):
        with self.assertRaises(AuthDenied):config_from_file('/no/such/file')
    def test_22_config_and_jwks_read(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); k=p/'public-jwks.json'; c=p/'config.json'
            k.write_text(json.dumps({'keys':[self.jwk]}));c.write_text(json.dumps({
                'issuer':self.cfg['issuer'],'audience':self.cfg['audience'],
                'subjects':['operator-1'],'resource':'https://example.net/mcp',
                'jwks_file':str(k)}))
            k.chmod(0o600);c.chmod(0o600)
            loaded=config_from_file(c)
            self.assertEqual(loaded['keys']['key-1']['kty'],'RSA')
    def test_23_world_readable_jwks_denied(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);k=p/'jwks';c=p/'config';k.write_text(json.dumps({'keys':[self.jwk]}))
            c.write_text(json.dumps({'issuer':self.cfg['issuer'],'audience':self.cfg['audience'],
                'subjects':['operator-1'],'resource':'https://example.net/mcp','jwks_file':str(k)}))
            k.chmod(0o644);c.chmod(0o600)
            with self.assertRaises(AuthDenied):config_from_file(c)
    def test_24_private_key_not_allowed_in_jwks(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);k=p/'jwks';c=p/'config'; bad={**self.jwk,'d':'dont'}
            k.write_text(json.dumps({'keys':[bad]}));c.write_text(json.dumps({
                'issuer':self.cfg['issuer'],'audience':self.cfg['audience'],
                'subjects':['operator-1'],'resource':'https://example.net/mcp','jwks_file':str(k)}))
            k.chmod(0o600);c.chmod(0o600)
            with self.assertRaises(AuthDenied):config_from_file(c)

class ApiInputTest(unittest.TestCase):
    def test_25_canary_never_managed(self):
        with self.assertRaises(rt.Invalid):rt.task_id(rt.CANARY)
    def test_26_valid_task(self):
        self.assertEqual(rt.task_id('task_'+'a'*32),'task_'+'a'*32)
    def test_27_task_invalid(self):
        for v in ('../etc/passwd','task_bad','task_'+'g'*32,'task_'+'a'*33):
            with self.assertRaises(rt.Invalid):rt.task_id(v)
    def test_28_message_id_bounds(self):
        rt.message_id('first-message_1')
        for v in ('','x'*65,'a/b','a\n'):
            with self.assertRaises(rt.Invalid):rt.message_id(v)
    def test_29_version_bool_denied(self):
        with self.assertRaises(rt.Invalid):rt.version(True)
    def test_30_page_bounds(self):
        with self.assertRaises(rt.Invalid):rt.pagination(0,51)
    def test_31_no_permit_never_send_sql(self):
        with patch.object(rt,'PERMIT',Path('/definitely/missing/permitted.json')):
            with patch.object(rt,'cg_db') as c:
                with self.assertRaises(rt.Invalid):rt.send('task_'+'a'*32,'id1','payload',0)
                c.assert_not_called()
    def test_32_no_permit_create_refused(self):
        with patch.object(rt,'PERMIT',Path('/definitely/missing/permitted.json')):
            with patch.object(rt,'cg_db') as c:
                with self.assertRaises(rt.Invalid):rt.create('abc','Title')
                c.assert_not_called()
    def test_33_disabled_dispatch_not_managed_denied(self):
        class DB:
            def _one(self,sql,args):return {'registered':False}
        with patch.object(rt,'cg_db',return_value=DB()):
            with self.assertRaises(rt.Invalid):rt.set_dispatch('task_'+'a'*32,False,0,0)
    def test_34_report_hash_validation(self):
        with self.assertRaises(rt.Invalid):rt.ack_report('task_'+'a'*32,2,'report','aabb')
    def test_35_managed_only_send_status(self):
        class DB:
            def _one(self,sql,args):return {'registered':False}
        with patch.object(rt,'cg_db',return_value=DB()):
            with self.assertRaises(rt.Invalid):rt.send_status('task_'+'a'*32,'report-1')
    def test_36_no_legacy_access_from_server(self):
        s=Path(__file__).with_name('runtime35a.py').read_text()
        for forbidden in ('bridge_app','ag_claim_outbox','cg_send_message(','bridge.view_dialog('):
            self.assertNotIn(forbidden,s)
    def test_37_write_permit_not_shipped(self):
        self.assertFalse(Path(__file__).with_name('managed-mcp-write-permit35b.json').exists())
    def test_38_mcp_has_only_managed_names(self):
        txt=Path(__file__).with_name('managed_mcp35a.py').read_text()
        for name in READ_TOOLS|WRITE_TOOLS:self.assertIn('def '+name+'(',txt)
        self.assertNotIn('stage33f_send_once',txt)

class AsyncGateTest(unittest.TestCase):
    @staticmethod
    def hit(method='POST',path='/mcp',body=b'{"method":"tools/list"}',headers=()):
        async def run():
            received=[];calls=[]
            async def app(scope,receive,send):
                calls.append(True)
                await send({'type':'http.response.start','status':200,'headers':[]})
                await send({'type':'http.response.body','body':b'passed'})
            gate=MCPAuthGate(app,config_path='/no/such/valid/oauth.json')
            scope={'type':'http','method':method,'path':path,'headers':list(headers)}
            used=False
            async def recv():
                nonlocal used
                if used:return {'type':'http.request','body':b'','more_body':False}
                used=True
                return {'type':'http.request','body':body,'more_body':False}
            async def send(msg):received.append(msg)
            await gate(scope,recv,send)
            return received,calls
        return asyncio.run(run())
    def test_39_no_config_tools_list_denied(self):
        messages,calls=self.hit()
        self.assertEqual(messages[0]['status'],401)
        self.assertEqual(calls,[])
    def test_40_no_config_tools_call_denied(self):
        msg,_=self.hit(body=b'{"method":"tools/call","params":{"name":"managed_create"}}')
        self.assertEqual(msg[0]['status'],401)
    def test_41_no_config_get_sse_denied(self):
        msg,_=self.hit(method='GET')
        self.assertEqual(msg[0]['status'],401)
    def test_42_no_config_metadata_unavailable(self):
        msg,_=self.hit(method='GET',path='/.well-known/oauth-protected-resource/mcp')
        self.assertEqual(msg[0]['status'],503)
    def test_43_ping_no_secrets(self):
        msg,_=self.hit(method='GET',path='/ping')
        self.assertEqual(msg[0]['status'],200)
        self.assertIn(b'"tunnel_attached":false',msg[1]['body'])
    def test_44_unknown_route_rejected(self):
        msg,_=self.hit(method='GET',path='/internal')
        self.assertEqual(msg[0]['status'],404)
    def test_45_lifespan_passthrough(self):
        async def run():
            calls=[]
            async def app(scope,receive,send):calls.append(scope['type'])
            await MCPAuthGate(app)({'type':'lifespan'},None,None)
            return calls
        self.assertEqual(asyncio.run(run()),['lifespan'])

class AuthenticatedASGITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update({'kid':'auth-1','use':'sig','alg':'RS256'})
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();p=Path(self.tmp.name)
        self.cfg=p/'cfg'; jwk=p/'jwk';jwk.write_text(json.dumps({'keys':[self.jwk]}))
        self.cfg.write_text(json.dumps({'issuer':'https://issuer.test.example/',
            'audience':'https://mcp.test.example/mcp','subjects':['alice'],
            'resource':'https://mcp.test.example/mcp','jwks_file':str(jwk)}))
        self.cfg.chmod(0o600);jwk.chmod(0o600)
    def tearDown(self):self.tmp.cleanup()
    def token(self,scope='managed:read'):
        now=int(time.time())
        return jwt.encode({'iss':'https://issuer.test.example/',
                'aud':'https://mcp.test.example/mcp','sub':'alice',
                'iat':now-1,'nbf':now-1,'exp':now+60,'scope':scope},
                self.key,algorithm='RS256',headers={'kid':'auth-1'})
    def request(self,method='POST',body=b'{"method":"tools/list"}',token=None,path='/mcp'):
        async def call():
            got=[];hit=[]
            async def app(scope,receive,send):
                hit.append(True)
                data=await receive()
                got.append(data)
                await send({'type':'http.response.start','status':200,'headers':[]})
                await send({'type':'http.response.body','body':b'ok'})
            gate=MCPAuthGate(app,self.cfg)
            scope={'type':'http','method':method,'path':path,
                   'headers':[(b'authorization',('Bearer '+(token or self.token())).encode())]}
            used=False
            async def recv():
                nonlocal used
                if used:return {'type':'http.request','body':b'','more_body':False}
                used=True
                return {'type':'http.request','body':body,'more_body':False}
            out=[]
            async def send(x):out.append(x)
            await gate(scope,recv,send)
            return out,got,hit
        return asyncio.run(call())
    def test_46_authorized_tools_list(self):
        out,got,hit=self.request()
        self.assertEqual(out[0]['status'],200);self.assertEqual(len(hit),1)
        self.assertEqual(got[0]['body'],b'{"method":"tools/list"}')
    def test_47_read_scope_write_rejected(self):
        out,_,hit=self.request(body=b'{"method":"tools/call","params":{"name":"managed_create"}}')
        self.assertEqual(out[0]['status'],401);self.assertEqual(hit,[])
    def test_48_write_scope_accepted(self):
        out,_,hit=self.request(body=b'{"method":"tools/call","params":{"name":"managed_create"}}',
                                token=self.token('managed:read managed:write'))
        self.assertEqual(out[0]['status'],200);self.assertEqual(len(hit),1)
    def test_49_oauth_discovery_metadata(self):
        out,_,hit=self.request(method='GET',path='/.well-known/oauth-protected-resource/mcp')
        self.assertEqual(out[0]['status'],200)
        obj=json.loads(out[1]['body'])
        self.assertEqual(obj['authorization_servers'],['https://issuer.test.example/'])
        self.assertEqual(hit,[])
    def test_50_expired_scope_denied(self):
        t=self.token('managed:write')
        out,_,hit=self.request(body=b'{"method":"tools/list"}',token=t)
        self.assertEqual(out[0]['status'],401);self.assertEqual(hit,[])

if __name__=='__main__':unittest.main()
