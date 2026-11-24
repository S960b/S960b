#!/usr/bin/env python3
import http.server, importlib, json, os, socket, socketserver, tempfile, threading, time, unittest
from unittest.mock import patch
import stream35e

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    response_kind='sse'
    def do_POST(self):
        count=int(self.headers.get('content-length','0'))
        body=self.rfile.read(count)
        rpc=json.loads(body)
        payload=json.dumps({'jsonrpc':'2.0','id':rpc.get('id'),
                            'result':{'protocolVersion':'2025-06-18'}}).encode()
        if self.response_kind=='sse':
            msg=b'event: message\r\ndata: '+payload+b'\r\n\r\n'
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Connection','keep-alive')
            self.end_headers()
            self.wfile.write(msg)
            self.wfile.flush()
            # Intentionally keep SSE stream alive after complete event.
            time.sleep(2.0)
        elif self.response_kind=='json':
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload);self.wfile.flush()
        elif self.response_kind=='forbidden':
            self.send_response(403)
            self.send_header('Content-Length','0')
            self.end_headers()
    def log_message(self,*args):pass

class Server(socketserver.ThreadingMixIn,http.server.HTTPServer):
    daemon_threads=True

class TestStream(unittest.TestCase):
    def test_sse_completed_event_before_connection_closed(self):
        with Server(('127.0.0.1',0),Handler) as s:
            thread=threading.Thread(target=s.serve_forever,daemon=True);thread.start()
            with patch.object(stream35e,'PORT',s.server_port):
                st=time.monotonic()
                res,headers=stream35e.rpc('initialize',{'protocolVersion':'2025-06-18'},secret='test',request_id=45,protocol='2025-06-18')
                elapsed=time.monotonic()-st
                self.assertLess(elapsed,1.5,'reader must not await SSE stream end')
                self.assertEqual(res['protocolVersion'],'2025-06-18')
                self.assertEqual(headers['content_type'],'text/event-stream')
            s.shutdown()
    def test_chunked_sse_completed_before_stream_end(self):
        class Chunked(Handler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('content-length','0')))
                data=b'data: {"jsonrpc":"2.0","id":5,"result":{}}\n\n'
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Transfer-Encoding','chunked')
                self.end_headers()
                self.wfile.write((b'%x\r\n'%len(data))+data+b'\r\n')
                self.wfile.flush()
                time.sleep(2)
                # No final zero chunk; response stays open.
        with Server(('127.0.0.1',0),Chunked) as s:
            t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
            with patch.object(stream35e,'PORT',s.server_port):
                start=time.monotonic()
                x,_=stream35e.rpc('initialize',secret='test',request_id=5)
                self.assertEqual(x,{})
                self.assertLess(time.monotonic()-start,1.5)
            s.shutdown()
    def test_json_completion(self):
        class Json(Handler):response_kind='json'
        with Server(('127.0.0.1',0),Json) as s:
            t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
            with patch.object(stream35e,'PORT',s.server_port):
                x,_=stream35e.rpc('initialize',secret='test',request_id=42)
                self.assertEqual(x['protocolVersion'],'2025-06-18')
            s.shutdown()
    def test_http_403_is_rejected(self):
        class Forbidden(Handler):response_kind='forbidden'
        with Server(('127.0.0.1',0),Forbidden) as s:
            t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
            with patch.object(stream35e,'PORT',s.server_port):
                with self.assertRaisesRegex(stream35e.ProbeError,'MCP_RPC_HTTP_403'):
                    stream35e.rpc('initialize',secret='test',request_id=42)
            s.shutdown()
    def test_split_sse_event(self):
        event=b'event: message\r\ndata: '+json.dumps({'id':7,'result':{'ok':True}}).encode()+b'\r\n\r\n'
        self.assertIsNone(stream35e.parse_prefix(event[:16],'text/event-stream',7))
        self.assertEqual(stream35e.parse_prefix(event,'text/event-stream',7)['result']['ok'],True)
    def test_noise_sse_event_skipped(self):
        event=b'event: ping\ndata: {}\n\nevent: message\ndata: {"id":2,"result":{}}\n\n'
        self.assertEqual(stream35e.parse_prefix(event,'text/event-stream',2)['id'],2)
    def test_unknown_format_rejected(self):
        with self.assertRaises(stream35e.ProbeError):
            stream35e.parse_prefix(b'x','text/html',1)
    def test_json_missing_then_completion(self):
        self.assertIsNone(stream35e.parse_prefix(b'{"id":','application/json',5))
        self.assertEqual(stream35e.parse_prefix(b'{"id":5,"result":{}}','application/json',5)['id'],5)
    def test_json_id_mismatch_rejected(self):
        with self.assertRaisesRegex(stream35e.ProbeError,'JSONRPC_ID_MISMATCH'):
            stream35e.parse_prefix(b'{"id":6,"result":{}}','application/json',5)
    def test_rpc_error_can_be_parsed_without_echoing_private_message(self):
        event=b'data: {"id":5,"error":{"code":-32600,"message":"private value"}}\n\n'
        result=stream35e.parse_prefix(event,'text/event-stream',5)
        self.assertIn('error',result)
    def test_tool_structured(self):
        self.assertEqual(stream35e.tool_payload({'structuredContent':{'ok':True}}),{'ok':True})
    def test_tool_text(self):
        self.assertEqual(stream35e.tool_payload({'content':[{'type':'text','text':'{"ok":true}'}]}),{'ok':True})
    def test_tool_invalid(self):
        with self.assertRaises(stream35e.ProbeError):stream35e.tool_payload({'isError':True})
    def test_sse_incomplete(self):
        self.assertIsNone(stream35e.parse_event_stream(b'data: {"id":2,"result":{}}\n',2))

if __name__=='__main__':unittest.main()
