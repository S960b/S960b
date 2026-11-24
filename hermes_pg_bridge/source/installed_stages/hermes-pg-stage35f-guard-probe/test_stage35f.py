#!/usr/bin/env python3
"""Offline regression of ASGI disconnect forwarding and streaming JSON-RPC."""
from __future__ import annotations
import asyncio
import hashlib
import http.server
import json
from pathlib import Path
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import guard35f
import stream35f as stream

KEY=b'K'*64
AUTH=(b'x-bridge-local-key',KEY)
BODY=b'{"jsonrpc":"2.0","id":5,"method":"tools/list"}'

class TestGate(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.key=Path(self.tmp.name)/'key'
        self.key.write_bytes(KEY+b'\n')
        self.key.chmod(0o600)
    def tearDown(self): self.tmp.cleanup()

    def call(self,body=BODY,headers=None,method='POST',scope_type='http',chunk=0):
        async def go():
            received=[];sent=[];queue=asyncio.Queue()
            if chunk:
                await queue.put({'type':'http.request','body':body[:chunk],'more_body':True})
                await queue.put({'type':'http.request','body':body[chunk:],'more_body':False})
            else:
                await queue.put({'type':'http.request','body':body,'more_body':False})
            await queue.put({'type':'http.disconnect'})
            async def recv():return await queue.get()
            async def send(data):sent.append(data)
            async def app(scope,receive,send):
                received.append(await asyncio.wait_for(receive(),timeout=.1))
                received.append(await asyncio.wait_for(receive(),timeout=.1))
                await send({'type':'http.response.start','status':200,'headers':[]})
                await send({'type':'http.response.body','body':b'OK'})
            gate=guard35f.MCPStaticGate(app,self.key)
            await asyncio.wait_for(gate({'type':scope_type,'method':method,'path':'/mcp',
                      'headers':headers if headers is not None else [AUTH]},recv,send),timeout=.4)
            return received,sent
        return asyncio.run(go())

    def test_disconnect_forwarded_not_synthetic_request(self):
        rec,send=self.call()
        self.assertEqual(rec[0]['body'],BODY)
        self.assertEqual(rec[1]['type'],'http.disconnect')
        self.assertEqual(send[0]['status'],200)
    def test_chunked_post_reconstructed_once(self):
        rec,_=self.call(chunk=17)
        self.assertEqual(rec[0]['body'],BODY)
        self.assertEqual(rec[1]['type'],'http.disconnect')
    def test_absent_key_denied(self):
        rec,send=self.call(headers=[])
        self.assertEqual(rec,[])
        self.assertEqual(send[0]['status'],403)
    def test_wrong_key_denied(self):
        rec,send=self.call(headers=[(AUTH[0],b'Z'*64)])
        self.assertEqual(send[0]['status'],403)
    def test_ambiguous_keys_denied(self):
        rec,send=self.call(headers=[AUTH,(b'X-Bridge-Local-Key',KEY)])
        self.assertEqual(send[0]['status'],403)
    def test_unknown_tool_denied(self):
        payload=json.dumps({'method':'tools/call','params':{'name':'unsafe'}}).encode()
        rec,send=self.call(body=payload)
        self.assertEqual(send[0]['status'],403)
    def test_guard_exact_edit_only(self):
        # Check installed original in live preflight. Offline tests always verify
        # the diagnostic fork's only behavioral change remains the single line.
        patched=Path(guard35f.__file__).read_text()
        self.assertEqual(patched.count('if used:return await receive()'),1)
        self.assertNotIn("if used:return {'type':'http.request'",patched)
        self.assertIn("await self.app(scope,replay or receive,send)",patched)
    def test_local_key_not_in_source(self):
        self.assertNotIn('K'*64,Path(guard35f.__file__).read_text())

class TestParser(unittest.TestCase):
    def test_sse_crlf_split(self):
        p=stream.ReplyParser('text/event-stream',5)
        msg=b'event: message\r\ndata: {"jsonrpc":"2.0","id":5,"result":{"ok":true}}\r\n\r\n'
        self.assertIsNone(p.feed(msg[:5]))
        self.assertIsNone(p.feed(msg[5:34]))
        self.assertEqual(p.feed(msg[34:]),{'ok':True})
    def test_sse_ignores_other_id(self):
        p=stream.ReplyParser('text/event-stream',5)
        self.assertIsNone(p.feed(b'data: {"id":2,"result":{}}\n\n'))
        self.assertEqual(p.feed(b'data: {"id":5,"result":{"v":1}}\n\n'),{'v':1})
    def test_json_incremental(self):
        p=stream.ReplyParser('application/json',7)
        self.assertIsNone(p.feed(b'{"id":7,"result":'))
        self.assertEqual(p.feed(b'{"p":true}}'),{'p':True})
    def test_json_error_redacted(self):
        p=stream.ReplyParser('application/json',7)
        with self.assertRaisesRegex(stream.ProbeError,'JSONRPC_ERROR_NO_BODY_EXPOSED'):
            p.feed(b'{"id":7,"error":{"message":"secret secret"}}')
    def test_sse_error_redacted(self):
        p=stream.ReplyParser('text/event-stream',7)
        with self.assertRaisesRegex(stream.ProbeError,'JSONRPC_ERROR_NO_BODY_EXPOSED'):
            p.feed(b'data: {"id":7,"error":{"message":"private"}}\n\n')
    def test_bad_content_type(self):
        p=stream.ReplyParser('text/html',2)
        with self.assertRaises(stream.ProbeError):p.feed(b'hi')
    def test_bound_line(self):
        p=stream.ReplyParser('text/event-stream',1)
        with self.assertRaises(stream.ProbeError):p.feed(b'a'*16385)
    def test_response_limit(self):
        p=stream.ReplyParser('application/json',1)
        with self.assertRaises(stream.ProbeError):p.feed(b'x'*131073)
    def test_prefix_log_never_arbitrary_data(self):
        self.assertEqual(stream.safe_prefix(b'SECRET_FROM_DB'),'SUPPRESSED')
        self.assertEqual(stream.safe_prefix(b'data: {"id":5}'),'646174613a')
    def test_tool_structured(self):
        self.assertEqual(stream.tool_payload({'structuredContent':{'ok':True}}),{'ok':True})
    def test_tool_text(self):
        self.assertEqual(stream.tool_payload({'content':[{'type':'text','text':'{"ok":true}'}]}),{'ok':True})
    def test_tool_failure(self):
        with self.assertRaises(stream.ProbeError):stream.tool_payload({'isError':True})

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def do_POST(self):
        self.rfile.read(int(self.headers.get('content-length','0')))
        data=b'data: {"jsonrpc":"2.0","id":15,"result":{"ok":true}}\r\n\r\n'
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.send_header('Transfer-Encoding','chunked')
        self.end_headers()
        self.wfile.write(b'%x\r\n'%len(data)+data+b'\r\n')
        self.wfile.flush()
        time.sleep(1.0) # SSE deliberately never closes before diagnostic returns
    def log_message(self,*args): pass
class Server(socketserver.ThreadingMixIn,http.server.HTTPServer):
    daemon_threads=True
class TestHTTP(unittest.TestCase):
    def test_streaming_response_without_eof(self):
        with Server(('127.0.0.1',0),Handler) as srv:
            t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
            with patch.object(stream,'PORT',srv.server_port):
                start=time.monotonic()
                res,_=stream.rpc('initialize',secret='DUMMY',request_id=15)
                self.assertEqual(res,{'ok':True})
                self.assertLess(time.monotonic()-start,.9)
            srv.shutdown()
    def test_accept_header_correct(self):
        self.assertIn('application/json, text/event-stream',Path(stream.__file__).read_text())

if __name__=='__main__':unittest.main()
