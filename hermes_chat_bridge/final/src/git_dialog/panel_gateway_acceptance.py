"""Standalone NEW HTTP-owner-form -> real Unix -> temporary SAME outbox proof.

Own children/sockets/temporary SQLite only. No Git HTTP, real credentials,
services or historical canary IDs. Never run inside offline pytest.
"""
import argparse
import base64
from http.client import HTTPConnection
import json
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from urllib.parse import urlencode

from . import DialogError
from .authority_acceptance import _serve
from .panel_gateway import GatewayPanel
from .service import AuthAuthority, initialize
from .unix_authority import UnixClient


def _http(directory,password,pipe):
    from waitress.server import create_server
    from waitress import wasyncore
    from .wakeup_smoke import StrictChannel,close_owned
    channels={}; server=None
    try:
        app=GatewayPanel(AuthAuthority(Path(directory)/'panel'),0,
                         client=UnixClient(Path(directory)/'mt.sock'))
        server=create_server(app,host='127.0.0.1',port=0,map=channels,threads=2,
            max_request_header_size=8192,max_request_body_size=8192,
            expose_tracebacks=False,log_socket_errors=False)
        server.channel_class=StrictChannel
        app.host='127.0.0.1:'+str(server.effective_port); app.origin='http://'+app.host
        pipe.send({'port':int(server.effective_port)})
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if pipe.poll() and pipe.recv()=='stop': break
            wasyncore.loop(timeout=.05,count=1,map=channels)
    except Exception:
        pipe.send({'error':'panel_acceptance_failed'})
    finally:
        close_owned(channels)
        if server is not None: server.task_dispatcher.shutdown(timeout=2)
        pipe.close()


def run():
    ctx=multiprocessing.get_context('fork'); children=[]; pipes=[]
    password=secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix='panel-gateway-acceptance-') as tmp:
        root=Path(tmp); initialize(root/'panel',password)
        def start(target,args):
            left,right=ctx.Pipe(); p=ctx.Process(target=target,args=(*args,right))
            children.append(p); pipes.append(left); p.start(); right.close()
            if not left.poll(4): raise DialogError('panel_acceptance_failed')
            return left.recv()
        # _serve's signature includes native after pipe, unlike the HTTP child.
        left,right=ctx.Pipe(); native=ctx.Process(target=_serve,args=(tmp,os.getpid(),right,True))
        children.append(native); pipes.append(left); native.start(); right.close()
        try:
            if not left.poll(4) or left.recv()!={'ready':True}: raise DialogError('panel_acceptance_failed')
            cg=UnixClient(root/'cg.sock')
            view=cg.call({'op':'view','offset':0,'limit':20})
            cg.call({'op':'append','head':view['head'],'message_id':'panel_new_task',
                     'kind':'task','body':'Owner form acceptance, inert text'})
            info=start(_http,(tmp,password)); port=info['port']
            auth='Basic '+base64.b64encode(('owner:'+password).encode()).decode()
            def request(method,path,body=None,*,authorized=True,origin=True):
                conn=HTTPConnection('127.0.0.1',port,timeout=4)
                headers={'Connection':'close'}
                if authorized: headers['Authorization']=auth
                if body is not None: headers['Content-Type']='application/x-www-form-urlencoded'
                if origin: headers['Origin']='http://127.0.0.1:'+str(port)
                try:
                    conn.request(method,path,body,headers); response=conn.getresponse()
                    raw=response.read(262145)
                    return response.status,raw
                finally: conn.close()
            if request('GET','/',authorized=False)[0]!=401: raise DialogError('panel_acceptance_failed')
            code,raw=request('GET','/')
            if code!=200: raise DialogError('panel_acceptance_failed')
            token=re.search(b'name="csrf" value="([^"]+)"',raw)[1].decode()
            body=urlencode({'csrf':token,'body':'Owner form actual HTTP/Unix acceptance'})
            if request('POST','/send',body,origin=False)[0]!=403: raise DialogError('panel_acceptance_failed')
            code,raw=request('POST','/send',body); receipt=json.loads(raw)
            if code!=200 or receipt['status']!='confirmed': raise DialogError('panel_acceptance_failed')
            if request('POST','/send',body)[0]!=403: raise DialogError('panel_acceptance_failed')
            view=cg.call({'op':'view','offset':0,'limit':20})
            if view['total_messages']!=4 or view['history'][-1]['message_id']!=receipt['message_id']:
                raise DialogError('panel_acceptance_failed')
            if view['history'][-1]['from']!='mt' or view['waiting_for']!='chatgpt':
                raise DialogError('panel_acceptance_failed')
        finally:
            for pipe in reversed(pipes):
                try: pipe.send('stop')
                except (OSError,EOFError): pass
            for child in reversed(children):
                child.join(3)
                if child.is_alive(): child.terminate(); child.join(1)
            for pipe in pipes: pipe.close()
        if any(p.is_alive() or p.exitcode!=0 for p in children): raise DialogError('panel_acceptance_shutdown_failed')
        if (root/'cg.sock').exists() or (root/'mt.sock').exists(): raise DialogError('panel_acceptance_shutdown_failed')
    return {'status':'panel_gateway_acceptance_passed','real_http':True,'real_unix':True,
        'owner_auth_before_ipc':True,'csrf_and_duplicate_denied':True,'fixed_actor':'mt',
        'temporary_state_only':True,'git_network_used':False,'owned_children_stopped':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',required=True,action='store_true'); parser.parse_args(argv)
    try: result=run()
    except Exception: result={'error':'panel_gateway_acceptance_failed','retry_authorized':False}
    print(json.dumps(result,sort_keys=True)); return int('error' in result)


if __name__=='__main__': raise SystemExit(main())
