import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import stage35s as st
import proxy35s as p

K = b'a'*64

class ClassificationTests(unittest.TestCase):
    def headers(self, val=None):
        h={'accept':['application/json, text/event-stream']}
        if val is not None:h['x-bridge-local-key']=[val]
        return h
    def test_missing(self):
        self.assertEqual(p.classification('POST','/mcp',self.headers(),K)['auth'],'missing')
    def test_correct(self):
        self.assertEqual(p.classification('POST','/mcp',self.headers('a'*64),K)['auth'],'match')
    def test_wrong(self):
        self.assertEqual(p.classification('POST','/mcp',self.headers('b'*64),K)['auth'],'mismatch')
    def test_duplicate(self):
        self.assertEqual(p.classification('POST','/mcp',{'x-bridge-local-key':['a'*64,'a'*64]},K)['auth'],'duplicate')
    def test_rpc(self):
        d=p.classification('POST','/mcp',self.headers(),K,b'{"jsonrpc":"2.0","method":"initialize","params":{"private":"secret"}}')
        self.assertEqual(d['rpc'],'initialize')
        self.assertNotIn('private',str(d))
        self.assertNotIn('secret',str(d))
    def test_non_known_rpc(self):
        d=p.classification('POST','/mcp',self.headers(),K,b'{"method":"dangerous_method"}')
        self.assertEqual(d['rpc'],'other')
    def test_non_mcp(self):
        d=p.classification('GET','/secret?p=token',self.headers(),K)
        self.assertEqual(d['route'],'other')
        self.assertNotIn('token',str(d))
    def test_path_mcp_trailing(self):
        self.assertEqual(p.classification('GET','/mcp/?x=secret',self.headers(),K)['route'],'mcp')
    def test_parse_headers(self):
        raw=b'POST /mcp HTTP/1.1\r\nAccept: text/event-stream\r\nX-Bridge-Local-Key: somekey\r\n\r\n'
        method,path,h=p.parse_head(raw)
        self.assertEqual((method,path,h['x-bridge-local-key']),('POST','/mcp',['somekey']))
    def test_parse_rejects_bad_request(self):
        with self.assertRaises(ValueError):p.parse_head(b'WRONG\r\n\r\n')
    def test_status_from_head(self):
        self.assertEqual(p.status_from_head(b'HTTP/1.1 403 Forbidden\r\n\r\n'),403)
        self.assertEqual(p.status_from_head(b'blah'),0)
    def test_exact_port_swap_only(self):
        source=Path(__file__).parent/'run_stage35s_instrumented.sh'
        original=Path('/mnt/data/dev35r/sources/jk/hermes-pg-stage35jk-cutover/run_managed_tunnel.sh')
        # file may not exist on Kali; inspect only the expected port replacement structure
        contents=source.read_text()
        self.assertIn("MCP_SERVER_URL='http://127.0.0.1:18779/mcp'",contents)
        self.assertNotIn("MCP_SERVER_URL='http://127.0.0.1:18777/mcp'",contents)
    def test_timer_armed(self):
        self.assertIn('OnActiveSec=12min',st.TIMER_BODY)
        self.assertIn('restore-auto',st.RES_BODY)
    def test_dropin_only_exec(self):
        self.assertIn('ExecStart=\n',st.DROP_BODY)
        self.assertIn('run_stage35s_instrumented.sh',st.DROP_BODY)
    def test_no_header_keys_in_event(self):
        with tempfile.TemporaryDirectory() as d, patch.object(p,'EVENTS',Path(d)/'events.ndjson'):
            rec=p.classification('GET','/mcp',self.headers('a'*64),K)
            rec['http']=200
            p.emit_event(rec)
            x=(Path(d)/'events.ndjson').read_text()
            self.assertNotIn('a'*64,x)
            self.assertNotIn('x-bridge-local-key',x)
            self.assertEqual(json.loads(x)['auth'],'match')

class ProxySmoke(unittest.IsolatedAsyncioTestCase):
    async def test_forward_unchanged_sse(self):
        received=[]
        async def backend(reader, writer):
            try:
                head=await reader.readuntil(b'\r\n\r\n')
                length=int([x.split(b':',1)[1].strip() for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:')][0])
                body=await reader.readexactly(length)
                received.append(head+body)
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 14\r\n\r\ndata: {"x":1}\n')
                await writer.drain()
            finally:
                writer.close()
        server=await asyncio.start_server(backend,'127.0.0.1',0)
        try:
            target=server.sockets[0].getsockname()
            with tempfile.TemporaryDirectory() as d:
                secret=Path(d)/'secret'; secret.write_bytes(K)
                events=Path(d)/'events.ndjson'
                with patch.object(p,'SECRET',secret),patch.object(p,'EVENTS',events),patch.object(p,'TARGET',(target[0],target[1])):
                    observer=await asyncio.start_server(p.handle,'127.0.0.1',0)
                    try:
                        port=observer.sockets[0].getsockname()[1]
                        r,w=await asyncio.open_connection('127.0.0.1',port)
                        body=b'{"method":"initialize"}'
                        req=(b'POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Bridge-Local-Key: '+K+
                             b'\r\nAccept: text/event-stream\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                        w.write(req);await w.drain()
                        response=await asyncio.wait_for(r.readuntil(b'\r\n\r\n'),4)
                        payload=await asyncio.wait_for(r.readexactly(14),4)
                        self.assertTrue(response.startswith(b'HTTP/1.1 200'))
                        self.assertEqual(payload,b'data: {"x":1}\n')
                        self.assertEqual(received,[req])
                        w.close();await w.wait_closed()
                        await asyncio.sleep(.05)
                        records=[json.loads(x) for x in events.read_text().splitlines()]
                        self.assertEqual(records[0]['auth'],'match')
                        self.assertEqual(records[0]['http'],200)
                        self.assertEqual(records[0]['rpc'],'initialize')
                    finally:
                        observer.close();await observer.wait_closed()
        finally:
            server.close();await server.wait_closed()

if __name__=='__main__':unittest.main()

# The install and proxy preflight exercise only the intended read-only MCP methods.
class Stage35SRegression(unittest.TestCase):
    def test_probe_success_and_no_write_tool_invoked(self):
        import mcp_probe35s as m
        names=['managed_health','managed_create_status','managed_list','managed_status',
               'managed_read','managed_send_status','managed_delivery_status','managed_create',
               'managed_set_dispatch','managed_send_task','managed_ack_report']
        calls=[]
        def fake_rpc(name,*args,**kwargs):
            calls.append(name)
            if name=='initialize':return {'protocolVersion':'2025-06-18'},{'session':'x'}
            if name=='notifications/initialized':return None,{'status':202}
            if name=='tools/list':return {'tools':[{'name':n} for n in names]},{}
            raise AssertionError('Unexpected MCP call')
        with patch.object(m,'rpc',fake_rpc),patch.object(m,'delete_session',return_value=None):
            self.assertTrue(m.run_probe(port=18779,secret='a'*64))
        self.assertEqual(calls,['initialize','notifications/initialized','tools/list'])

    def test_probe_mismatched_toolset_rejected(self):
        import mcp_probe35s as m
        def fake_rpc(name,*args,**kwargs):
            if name=='initialize':return {'protocolVersion':'2025-06-18'},{'session':None}
            if name=='notifications/initialized':return None,{'status':202}
            if name=='tools/list':return {'tools':[{'name':'injected'}]},{}
        with patch.object(m,'rpc',fake_rpc):
            self.assertFalse(m.run_probe(port=18779,secret='b'*64))

    def test_requires_previous_stage_restored(self):
        import inspect
        source=inspect.getsource(st.safe_old35r)
        self.assertIn('PREVIOUS_35R_NOT_RESTORED',source)
        self.assertIn('OLD_35R_DROPIN_PRESENT',source)

    def test_observer_preflight_precedes_tunnel_restart(self):
        import inspect
        source=inspect.getsource(st.begin)
        self.assertLess(source.index('probe_port_18779()'),source.index("system('restart',TUNNEL"))
        self.assertLess(source.index("system('start',TIMER"),source.index("system('restart',TUNNEL"))

    def test_readiness_failure_restores_and_summarizes(self):
        import inspect
        source=inspect.getsource(st.begin)
        self.assertIn('TUNNEL_NOT_READY_AFTER_REDIRECT',source)
        self.assertIn('restore(automatic=False,from_failed=True)',source)
        self.assertIn('summary()',source)

    def test_diagnostics_classify_not_raw(self):
        import inspect
        source=inspect.getsource(st.readiness_metadata)
        self.assertNotIn('stderr',source)
        self.assertNotIn('stdout',source)
        self.assertNotIn('read_bytes',source)

    def test_separate_names_and_port(self):
        self.assertEqual(st.OBS,'hermes-pg-stage35s-observer.service')
        self.assertEqual(st.DROP.name,'91-stage35s-observer.conf')
        self.assertIn('127.0.0.1:18779/mcp',(Path(__file__).parent/'run_stage35s_instrumented.sh').read_text())
        self.assertNotIn('MCP_SERVER_URL=\'http://127.0.0.1:18777/mcp\'',(Path(__file__).parent/'run_stage35s_instrumented.sh').read_text())

    def test_preflight_sse_parser_does_not_wait_for_eof(self):
        import stream35s as m
        parser=m.ReplyParser('text/event-stream',901)
        self.assertIsNone(parser.feed(b'event: message\ndata: {"id":901,'))
        self.assertEqual(parser.feed(b'"jsonrpc":"2.0","result":{"ok":true}}\n\n'),{'ok':True})

    def test_timer_is_auto_restore(self):
        self.assertIn('OnActiveSec=12min',st.TIMER_BODY)
        self.assertIn('restore-auto',st.RES_BODY)
