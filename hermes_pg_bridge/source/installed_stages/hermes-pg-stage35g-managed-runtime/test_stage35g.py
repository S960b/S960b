#!/usr/bin/env python3
"""Offline, isolated regression tests for permanent service wrapper and safety policy."""
import asyncio
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import guard35g
import service35g
import stage35g
import stream35g

TEST_KEY=b'K'*64
POST=b'{"jsonrpc":"2.0","id":4,"method":"tools/list"}'

class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.key=Path(self.tmp.name)/'secret';self.key.write_bytes(TEST_KEY+b'\n');self.key.chmod(0o600)
    def tearDown(self):self.tmp.cleanup()
    def call(self,headers,body=POST,chunks=False,method='POST'):
        async def run():
            sent=[];received=[];q=asyncio.Queue()
            if chunks:
                await q.put({'type':'http.request','body':body[:5],'more_body':True})
                await q.put({'type':'http.request','body':body[5:],'more_body':False})
            else:await q.put({'type':'http.request','body':body,'more_body':False})
            await q.put({'type':'http.disconnect'})
            async def recv():return await q.get()
            async def send(x):sent.append(x)
            async def inner(scope,receive,send):
                received.append(await receive())
                received.append(await receive())
                await send({'type':'http.response.start','status':200,'headers':[]})
            app=guard35g.MCPStaticGate(inner,self.key)
            await app({'type':'http','path':'/mcp','method':method,'headers':headers},recv,send)
            return received,sent
        return asyncio.run(asyncio.wait_for(run(),timeout=.5))
    def test_disconnect_is_real(self):
        v,s=self.call([(b'x-bridge-local-key',TEST_KEY)])
        self.assertEqual(v[0]['body'],POST);self.assertEqual(v[1]['type'],'http.disconnect')
        self.assertEqual(s[0]['status'],200)
    def test_chunked_replay(self):
        v,_=self.call([(b'x-bridge-local-key',TEST_KEY)],chunks=True)
        self.assertEqual(v[0]['body'],POST);self.assertEqual(v[1]['type'],'http.disconnect')
    def test_missing_key(self):
        v,s=self.call([]);self.assertEqual(v,[]);self.assertEqual(s[0]['status'],403)
    def test_wrong_key(self):
        v,s=self.call([(b'x-bridge-local-key',b'Z'*64)]);self.assertEqual(s[0]['status'],403)
    def test_duplicate_key(self):
        v,s=self.call([(b'x-bridge-local-key',TEST_KEY),(b'X-BRIDGE-LOCAL-KEY',TEST_KEY)])
        self.assertEqual(s[0]['status'],403)
    def test_write_tool_cannot_bypass_auth(self):
        body=b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"managed_create"}}'
        _,s=self.call([],body=body);self.assertEqual(s[0]['status'],403)
    def test_unknown_method_denied(self):
        body=b'{"jsonrpc":"2.0","method":"evil"}'
        _,s=self.call([(b'x-bridge-local-key',TEST_KEY)],body=body)
        self.assertEqual(s[0]['status'],403)

class Policy(unittest.TestCase):
    def test_exact_fixed_source(self):
        self.assertEqual(hashlib.sha256(Path(guard35g.__file__).read_bytes()).hexdigest(),
            '695622407566b316482f676da0bf3c5a63a12b3f4b6e6d706c92130a760aaa88')
        self.assertIn('if used:return await receive()',Path(guard35g.__file__).read_text())
        self.assertNotIn("if used:return {'type':'http.request'",Path(guard35g.__file__).read_text())
    def test_service_pins_exact_35c(self):
        self.assertEqual(len(service35g.PINS),4)
        self.assertEqual(service35g.PINS[service35g.C/'static_guard35c.py'],stage35g.OLD_SHA)
        self.assertEqual(service35g.PINS[service35g.G/'guard35g.py'],stage35g.FIX_SHA)
    def test_dropin_only_targeted_service(self):
        txt=stage35g.UNIT_DROP
        self.assertEqual(txt.count('ExecStart='),2)
        self.assertIn('service35g.py',txt)
        self.assertNotIn('tunnel-client',txt)
        self.assertNotIn('opencode.json',txt)
        self.assertIn('Restart=no',txt)
        self.assertNotIn('WantedBy=',txt)
        self.assertEqual(stage35g.UNIT,'hermes-pg-stage35c-static-shadow.service')
    def test_safe_file_private(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test';p.write_text('abc');p.chmod(0o600)
            self.assertTrue(stage35g.safe_file(p))
            self.assertFalse(stage35g.safe_file(p,'bad'))
            p.chmod(0o644);self.assertFalse(stage35g.safe_file(p))
            p.chmod(0o600)
            q=Path(d)/'link';q.symlink_to(p);self.assertFalse(stage35g.safe_file(q))
    def test_pin_drift_refused_by_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source';p.write_text('correct');p.chmod(0o600)
            with patch.object(service35g,'PINS',{p:hashlib.sha256(b'wrong').hexdigest()}):
                with self.assertRaisesRegex(RuntimeError,'SOURCE_DRIFT'):service35g.verified_source()
    def test_service_runner_no_proxy_or_enable(self):
        s=Path(service35g.__file__).read_text()
        self.assertIn("host='127.0.0.1',port=18777",s)
        self.assertIn("timeout_graceful_shutdown=5",s)
        self.assertNotIn('CONTROL_PLANE_API_KEY',s)
        self.assertNotIn('systemctl',s)
    def test_no_raw_secret_in_logs(self):
        s=Path(stage35g.__file__).read_text()
        self.assertNotIn('print(secret',s)
        self.assertNotIn('print(hdata',s)
    def test_client_help_only(self):
        s=Path(stage35g.__file__).read_text()
        self.assertIn("'run','--help'",s)
        self.assertIn('FILE_REFERENCE_NOT_PROVEN',s)

class Parser(unittest.TestCase):
    def test_sse_event(self):
        p=stream35g.ReplyParser('text/event-stream',12)
        self.assertIsNone(p.feed(b'event: message\r\ndata: {"id":12,'))
        self.assertEqual(p.feed(b'"result":{"v":7}}\r\n\r\n'),{'v':7})
    def test_mixed_session_messages(self):
        p=stream35g.ReplyParser('text/event-stream',12)
        self.assertIsNone(p.feed(b'data: {"id":11,"result":{}}\n\n'))
        self.assertEqual(p.feed(b'data: {"id":12,"result":{}}\n\n'),{})
    def test_202_is_accepted_for_notifications(self):
        # Stage35F live run saw a 202 acknowledgment; never treat it as a JSON-RPC result.
        s=Path(stream35g.__file__).read_text()
        self.assertIn('200,202,204',s)
    def test_bounded_sse_line(self):
        p=stream35g.ReplyParser('text/event-stream',5)
        with self.assertRaises(stream35g.ProbeError):p.feed(b'x'*17000)
    def test_secret_prefix_redacted(self):
        self.assertEqual(stream35g.safe_prefix(b'SECRET'),'SUPPRESSED')
    def test_bad_rpc_error_redacted(self):
        p=stream35g.ReplyParser('application/json',5)
        with self.assertRaises(stream35g.ProbeError):p.feed(b'{"id":5,"error":{"message":"private"}}')

if __name__=='__main__':unittest.main()
