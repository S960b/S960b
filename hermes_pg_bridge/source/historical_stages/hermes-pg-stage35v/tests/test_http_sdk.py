"""Exercise the original gate on real loopback HTTP with the locally installed legacy SDK."""
import contextlib
import http.client
import io
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
sys.path.insert(0,str(HERE))
import policy35v

def server(sock, root):
    import logging
    logging.disable(logging.CRITICAL)
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    import uvicorn
    import guard35g
    backend=FastMCP('isolated', transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,allowed_hosts=['127.0.0.1:*'],allowed_origins=[]))
    @backend.tool()
    def managed_health()->dict:
        return {'ok':True}
    policy35v.EVENTS=root/'denials.tsv'
    policy35v.install(guard35g,())
    gate=guard35g.MCPStaticGate(backend.streamable_http_app(),root/'test-key')
    uvicorn.Server(uvicorn.Config(gate,log_level='error',access_log=False)).run(sockets=[sock])

class HTTPTest(unittest.TestCase):
    def test_legacy_sse_and_new_method(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);key=root/'test-key'
            key.write_bytes(b'Q'*64);key.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port=sock.getsockname()[1]
                child=multiprocessing.get_context('fork').Process(target=server,args=(sock,root))
                child.start()
                try:
                    self.exercise(port,root)
                finally:
                    child.terminate();child.join(6)
                    if child.is_alive():
                        child.kill();child.join()
    def exercise(self,port,root):
        def req(method, data=None, headers=None,verb='POST'):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=4)
            try:
                h={'Accept':'application/json, text/event-stream','Content-Type':'application/json',
                   'X-Bridge-Local-Key':'Q'*64,'Connection':'close'}
                h.update(headers or {})
                obj={'jsonrpc':'2.0','method':method}
                if method!='notifications/initialized':obj['id']=120
                if data is not None:obj['params']=data
                conn.request(verb,'/mcp',json.dumps(obj) if verb=='POST' else None,h)
                res=conn.getresponse()
                mime=res.getheader('Content-Type','')
                val=None
                if 'text/event-stream' in mime:
                    while line:=res.readline(16384):
                        if line.startswith(b'data:'):
                            val=json.loads(line[5:]);break
                else:res.read(4096)
                return res.status,res.getheader('Mcp-Session-Id'),mime,val
            finally:conn.close()
        end=time.monotonic()+12
        while True:
            try:
                ini=req('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
                    'clientInfo':{'name':'isolated','version':'1'}})
                break
            except OSError:
                if time.monotonic()>end:raise
                time.sleep(.05)
        self.assertEqual(ini[0],200)
        self.assertIn('text/event-stream',ini[2])
        session={'Mcp-Session-Id':ini[1],'MCP-Protocol-Version':ini[3]['result']['protocolVersion']}
        self.assertEqual(req('notifications/initialized',headers=session)[0],202)
        listing=req('tools/list',headers=session)
        self.assertEqual([t['name'] for t in listing[3]['result']['tools']],['managed_health'])
        self.assertEqual(req('vendor/registryQuery',headers=session)[0],403)
        self.assertEqual(req('vendor/secretVariant',headers={**session,'X-Bridge-Local-Key':'wrong'})[0],403)
        self.assertEqual(policy35v.denials(root/'denials.tsv'),['vendor/registryQuery'])
        self.assertEqual(req('',headers=session,verb='DELETE')[0],200)
