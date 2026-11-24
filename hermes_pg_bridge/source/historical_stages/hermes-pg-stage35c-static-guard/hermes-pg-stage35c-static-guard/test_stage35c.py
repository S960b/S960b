#!/usr/bin/env python3
"""Offline security, input and deployment constraints (no PostgreSQL writes)."""
import asyncio,hashlib,json,os,stat,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from static_guard35c import (AuthDenied,MCPStaticGate,READ_TOOLS,WRITE_TOOLS,
    private_regular,local_secret,authenticated,required_for_body,MAX_POST)
import runtime35c as rt
KEY=b'a'*64
HEADER=b'x-bridge-local-key'

def hit(secret_path, *,method='POST',path='/mcp', body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', headers=None):
    if headers is None:headers=[]
    async def go():
        result=[];called=[]
        async def app(scope,receive,send):
            called.append(True)
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        gate=MCPStaticGate(app,secret_path)
        used=False
        async def recv():
            nonlocal used
            if used:return {'type':'http.request','body':b'','more_body':False}
            used=True
            return {'type':'http.request','body':body,'more_body':False}
        async def send(msg):result.append(msg)
        await gate({'type':'http','path':path,'method':method,'headers':headers},recv,send)
        return result[0]['status'],len(called)
    return asyncio.run(go())

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'secret'
        self.path.write_bytes(KEY+b'\n');self.path.chmod(0o600)
    def tearDown(self):self.tmp.cleanup()
    def test_01_private_file(self):self.assertTrue(private_regular(self.path,owner=os.geteuid()))
    def test_02_secret_read(self):self.assertEqual(local_secret(self.path),KEY)
    def test_03_missing_secret_denied(self):
        with self.assertRaises(AuthDenied):local_secret(self.path.with_suffix('.missing'))
    def test_04_world_readable_denied(self):
        self.path.chmod(0o644)
        with self.assertRaises(AuthDenied):local_secret(self.path)
    def test_05_symlink_denied(self):
        with self.assertRaises(AuthDenied):local_secret(self.path.with_suffix('.lnk'))
        ln=self.path.with_suffix('.lnk');ln.symlink_to(self.path)
        with self.assertRaises(AuthDenied):local_secret(ln)
    def test_06_short_secret_denied(self):
        self.path.write_bytes(b'abc')
        with self.assertRaises(AuthDenied):local_secret(self.path)
    def test_07_newline_in_secret_denied(self):
        self.path.write_bytes(b'a'*20+b'\n'+b'b'*44)
        with self.assertRaises(AuthDenied):local_secret(self.path)
    def test_08_correct_header(self):authenticated([(HEADER,KEY)],KEY)
    def test_09_wrong_header(self):
        with self.assertRaises(AuthDenied):authenticated([(HEADER,b'b'*64)],KEY)
    def test_10_missing_header(self):
        with self.assertRaises(AuthDenied):authenticated([],KEY)
    def test_11_duplicate_case_header(self):
        with self.assertRaises(AuthDenied):authenticated([(HEADER,KEY),(b'X-Bridge-Local-Key',KEY)],KEY)
    def test_12_no_key_no_tool_list(self):self.assertEqual(hit(self.path),(403,0))
    def test_13_yes_key_tool_list(self):self.assertEqual(hit(self.path,headers=[(HEADER,KEY)]),(200,1))
    def test_14_yes_key_read_tool(self):
        body=json.dumps({'method':'tools/call','params':{'name':'managed_read'}}).encode()
        self.assertEqual(hit(self.path,body=body,headers=[(HEADER,KEY)]),(200,1))
    def test_15_no_key_write_tool(self):
        body=json.dumps({'method':'tools/call','params':{'name':'managed_send_task'}}).encode()
        self.assertEqual(hit(self.path,body=body),(403,0))
    def test_16_yes_key_write_tool_reaches_inner_app(self):
        body=json.dumps({'method':'tools/call','params':{'name':'managed_send_task'}}).encode()
        self.assertEqual(hit(self.path,body=body,headers=[(HEADER,KEY)]),(200,1))
    def test_17_unknown_tool_denied_even_with_key(self):
        body=json.dumps({'method':'tools/call','params':{'name':'bridge_app_admin'}}).encode()
        self.assertEqual(hit(self.path,body=body,headers=[(HEADER,KEY)]),(403,0))
    def test_18_batch_denied(self):
        self.assertEqual(hit(self.path,body=b'[]',headers=[(HEADER,KEY)]),(403,0))
    def test_19_unknown_method_denied(self):
        self.assertEqual(hit(self.path,body=b'{"method":"evil"}',headers=[(HEADER,KEY)]),(403,0))
    def test_20_get_without_key_denied(self):
        self.assertEqual(hit(self.path,method='GET'),(403,0))
    def test_21_get_with_key(self):
        self.assertEqual(hit(self.path,method='GET',headers=[(HEADER,KEY)]),(200,1))
    def test_22_ping_public_no_sensitive_values(self):
        self.assertEqual(hit(self.path,method='GET',path='/ping'),(200,0))
    def test_23_oauth_routes_absent(self):
        self.assertEqual(hit(self.path,method='GET',path='/.well-known/oauth-protected-resource'),(404,0))
    def test_24_overlong_request_denied(self):
        self.assertEqual(hit(self.path,body=b'x'*(MAX_POST+1),headers=[(HEADER,KEY)]),(403,0))
    def test_25_read_write_tools_separate(self):
        self.assertFalse(READ_TOOLS&WRITE_TOOLS)
        self.assertEqual(len(READ_TOOLS),7);self.assertEqual(len(WRITE_TOOLS),4)
    def test_26_no_legacy_methods(self):
        src=Path(__file__).with_name('runtime35c.py').read_text()
        for bad in ('bridge_app','ag_claim_outbox','bridge.view_dialog('):self.assertNotIn(bad,src)
    def test_27_mcp_uses_own_guard(self):
        src=Path(__file__).with_name('managed_mcp35c.py').read_text()
        self.assertIn('MCPStaticGate(app)',src)
        self.assertNotIn('MCPAuthGate',src)
        self.assertIn('PORT=18777',src)
    def test_28_canary_excluded(self):
        with self.assertRaises(rt.Invalid):rt.task_id(rt.CANARY)
    def test_29_write_no_permit_before_db(self):
        with patch.object(rt,'PERMIT',Path('/none/no-write-permit')):
            with patch.object(rt,'cg_db') as fake:
                with self.assertRaises(rt.Invalid):rt.send('task_'+'a'*32,'a1','hello',0)
                fake.assert_not_called()
    def test_30_create_no_permit_before_db(self):
        with patch.object(rt,'PERMIT',Path('/none/no-write-permit')):
            with patch.object(rt,'cg_db') as fake:
                with self.assertRaises(rt.Invalid):rt.create('req','Test')
                fake.assert_not_called()
    def test_31_ack_no_permit_before_db(self):
        with patch.object(rt,'PERMIT',Path('/none/no-write-permit')):
            with patch.object(rt,'cg_db') as fake:
                with self.assertRaises(rt.Invalid):rt.ack_report('task_'+'a'*32,2,'r1', 'a'*64)
                fake.assert_not_called()
    def test_32_permit_invalid_sha(self):
        with patch.object(rt,'PERMIT',Path('/none/no-write-permit')):
            self.assertFalse(rt.permitted('create'))
    def test_33_cannot_set_dispatch_without_permit(self):
        with patch.object(rt,'PERMIT',Path('/none/no-write-permit')):
            class DB:
                def _one(self,sql,args):return {'registered':True}
            with patch.object(rt,'cg_db',return_value=DB()):
                with self.assertRaises(rt.Invalid):rt.set_dispatch('task_'+'a'*32,False,0,0)
    def test_34_malformed_version(self):
        for value in (True,-1,2**53,1.5):
            with self.assertRaises(rt.Invalid):rt.version(value)
    def test_35_long_body_denied(self):
        with self.assertRaises(rt.Invalid):rt.send('task_'+'a'*32,'a1','x'*33000,0)
    def test_36_all_write_names_are_mcp_defined(self):
        text=Path(__file__).with_name('managed_mcp35c.py').read_text()
        for name in WRITE_TOOLS|READ_TOOLS:self.assertIn('def '+name+'(',text)
    def test_37_oauth_old_file_not_modified(self):
        self.assertNotIn('oauth_guard35a',Path(__file__).with_name('managed_mcp35c.py').read_text())
    def test_38_write_permit_not_shipped(self):
        self.assertFalse(Path(__file__).with_name('managed-mcp-write-permit35c.json').exists())
    def test_39_not_allow_credential_in_zip(self):
        self.assertFalse(Path(__file__).with_name('managed-mcp-local-key35c').exists())
    def test_40_scoped_permit_needs_static_flag(self):
        self.assertIn('static_tunnel_verified',Path(__file__).with_name('runtime35c.py').read_text())

if __name__=='__main__':unittest.main()
