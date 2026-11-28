"""Isolated, loopback-only Taskboard v2 with the original Taskboard static UI.

No Git API. All writes require owner Basic auth, Origin and CSRF. The secret
file never appears in application responses. Deletes tombstone, not erase.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from contextlib import nullcontext
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from socketserver import ThreadingMixIn

from .compat import Catalog, version_token, ID_RE, MID_RE
from .store import BridgeError

PORT = 8789
HOST='127.0.0.1'
STATE=Path.home()/'.local/state/bridge-pg-v2/taskboard-auth.json'
ASSETS=Path(__file__).resolve().parent/'static'
COOKIE_POLICY="default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def _json(x):
    return json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


def make_credentials(password:str) -> dict:
    if not isinstance(password,str) or len(password)<12 or len(password)>256:
        raise ValueError('password length must be 12..256 characters')
    salt=secrets.token_bytes(16)
    return {'version':1,'salt':salt.hex(),'digest':hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex(),
            'ticket_key':secrets.token_bytes(32).hex()}


def _write_auth(path:Path, obj:dict):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(obj,f)
        f.flush();os.fsync(f.fileno())
    os.chmod(path,0o600)


def _read_auth(path:Path) -> dict:
    st=path.lstat()
    if not path.is_file() or st.st_uid!=os.getuid() or st.st_mode&0o077 or st.st_nlink!=1 or st.st_size>4096:
        raise RuntimeError('taskboard_auth_permissions_invalid')
    obj=json.loads(path.read_text())
    if obj['version']!=1 or len(bytes.fromhex(obj['ticket_key']))!=32:
        raise RuntimeError('taskboard_auth_invalid')
    return obj


class App:
    def __init__(self,catalog:Catalog|None=None,auth_file:Path=STATE,port:int=PORT):
        self.cat=catalog or Catalog()
        self.auth=_read_auth(auth_file)
        self.port=port
        self.csrf=secrets.token_urlsafe(32)
        self.hosts={'127.0.0.1:'+str(port),'localhost:'+str(port)}
        self.origins={'http://'+host for host in self.hosts}

    def verify(self,header)->bool:
        if not isinstance(header,str) or len(header)>1024 or not header.startswith('Basic '):
            return False
        try:
            username, password=base64.b64decode(header[6:],validate=True).decode('utf-8').split(':',1)
            if username!='owner':return False
            hashed=hashlib.scrypt(password.encode(),salt=bytes.fromhex(self.auth['salt']),n=16384,r=8,p=1).hex()
            return hmac.compare_digest(hashed,self.auth['digest'])
        except (ValueError,UnicodeError,OverflowError):
            return False

    def _ticket(self,task_id:str,head:str,expires:int)->str:
        data=f'{task_id}\0{head}\0{expires}'.encode()
        return f'{expires}.{hmac.new(bytes.fromhex(self.auth["ticket_key"]),data,hashlib.sha256).hexdigest()}'

    def _read_json(self,env):
        if (env.get('CONTENT_TYPE')!='application/json' or env.get('HTTP_ORIGIN') not in self.origins or
            env.get('HTTP_X_CSRF_TOKEN')!=self.csrf):
            raise BridgeError('request_rejected')
        n=env.get('CONTENT_LENGTH','')
        if not re.fullmatch(r'[1-9][0-9]{0,3}',n) or int(n)>2048:
            raise BridgeError('request_rejected')
        data=env['wsgi.input'].read(int(n))
        if len(data)!=int(n):raise BridgeError('request_rejected')
        val=json.loads(data.decode('utf-8'))
        if not isinstance(val,dict):raise BridgeError('request_rejected')
        return val

    def __call__(self,env,start):
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
             'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
             'Content-Security-Policy':COOKIE_POLICY}
        code=403; ctype='text/plain; charset=utf-8'; data=b'Forbidden'
        try:
            method,path=env.get('REQUEST_METHOD'),env.get('PATH_INFO')
            if (env.get('HTTP_HOST') not in self.hosts or env.get('REMOTE_ADDR')!=HOST or
                any(x=='HTTP_FORWARDED' or x.startswith('HTTP_X_FORWARDED_') for x in env) or
                env.get('HTTP_ORIGIN') not in (None,*self.origins)):
                raise BridgeError('boundary_rejected')
            if not self.verify(env.get('HTTP_AUTHORIZATION')):
                code,data=401,b'Authentication required'
                headers['WWW-Authenticate']='Basic realm="PostgreSQL Bridge Taskboard v2", charset="UTF-8"'
            elif method=='GET' and path in ('/','/taskboard.js','/taskboard.css') and not env.get('QUERY_STRING'):
                filename={'/':'taskboard.html','/taskboard.js':'taskboard.js','/taskboard.css':'taskboard.css'}[path]
                ctype='text/html; charset=utf-8' if path=='/' else ('text/javascript; charset=utf-8' if filename.endswith('.js') else 'text/css; charset=utf-8')
                data=(ASSETS/filename).read_bytes();code=200
            elif method=='GET' and path in ('/api/dialogs','/api/history'):
                params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if any(len(v)!=1 for v in params.values()):raise BridgeError('request_rejected')
                q={k:v[0] for k,v in params.items()}
                if path=='/api/dialogs':
                    if set(q)-{'offset','limit'}:raise BridgeError('request_rejected')
                    off=int(q.get('offset','0'));limit=int(q.get('limit','30'))
                    result=self.cat.taskboard_rows(off,limit)
                    result['csrf']=self.csrf
                else:
                    if set(q)-{'task_id','offset','limit'}:raise BridgeError('request_rejected')
                    result=self.cat.view(q['task_id'],int(q.get('offset','0')),int(q.get('limit','20')))
                    from .adapter64 import enrollment_history
                    result=enrollment_history(result,q['task_id'],int(q.get('offset','0')),
                       lambda tid:self.cat._one('SELECT bridge.tb_enrollment_event_v64(%s) AS result',(tid,)))
                data=_json(result);code=200;ctype='application/json; charset=utf-8'
            elif method=='POST' and path=='/api/owner_turn':
                from .owner_http57 import handle_owner_turn
                result=handle_owner_turn(self.cat,self._read_json(env))
                data=_json(result);code=200;ctype='application/json; charset=utf-8'
            elif method=='POST' and path in ('/api/delete_prepare','/api/delete_confirm','/api/return_turn','/api/hand_back'):
                obj=self._read_json(env)
                if path=='/api/delete_prepare':
                    if set(obj)!={'task_id','head'}:raise BridgeError('request_rejected')
                    d=self.cat.metadata(obj['task_id'])
                    if obj['head']!=version_token(obj['task_id'],d['version']):raise BridgeError('version_conflict')
                    expires=int(datetime.now(timezone.utc).timestamp())+120
                    result={'task_id':obj['task_id'],'head':obj['head'],'expected_head':obj['head'],
                      'confirmation':self._ticket(obj['task_id'],obj['head'],expires),
                      'expires_at':expires,'state':'closing'}
                elif path=='/api/delete_confirm':
                    if set(obj)!={'task_id','head','confirmation'}:raise BridgeError('request_rejected')
                    tok=obj['confirmation'];parts=tok.split('.') if isinstance(tok,str) else []
                    if len(parts)!=2 or not parts[0].isdigit() or len(parts[1])!=64:
                        raise BridgeError('request_rejected')
                    expires=int(parts[0])
                    now=int(datetime.now(timezone.utc).timestamp())
                    if expires<now or expires>now+120 or not hmac.compare_digest(tok,self._ticket(obj['task_id'],obj['head'],expires)):
                        raise BridgeError('request_rejected')
                    d=self.cat.metadata(obj['task_id'])
                    if obj['head']!=version_token(obj['task_id'],d['version']):raise BridgeError('version_conflict')
                    result=self.cat._one('SELECT bridge.tombstone_dialog(%s,%s) AS result',(obj['task_id'],d['version']))
                    result.update(head=obj['head'],remote_absent=True)
                elif path=='/api/return_turn':
                    if set(obj)!={'task_id','head','last_message_id'}:raise BridgeError('request_rejected')
                    if obj['task_id']=='task_b6c26aa18220412bafdbf32393f16568':raise BridgeError('return_not_allowed')
                    result=self.cat.return_turn(obj['task_id'],obj['head'],obj['last_message_id'])
                    result['created']=True
                else:
                    if set(obj)!={'task_id','head'}:raise BridgeError('request_rejected')
                    result=self.cat.hand_back(obj['task_id'],obj['head'])
                    result['created']=True
                data=_json(result);code=200;ctype='application/json; charset=utf-8'
            else:
                code,data=404,b'Not found'
        except BridgeError as e:
            if e.code=='database_unavailable':
                code,ctype,data=503,'application/json; charset=utf-8',_json({'ok':False,'error':'database_unavailable'})
            else:
                code,ctype,data=409,'application/json; charset=utf-8',_json({'ok':False,'error':e.code})
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            code,ctype,data=409,'application/json; charset=utf-8',_json({'ok':False,'error':'request_rejected_or_unconfirmed'})
        except Exception:
            code,ctype,data=503,'application/json; charset=utf-8',_json({'ok':False,'error':'database_unavailable'})
        phrase={200:'OK',401:'Unauthorized',403:'Forbidden',404:'Not Found',409:'Conflict',503:'Service Unavailable'}[code]
        raw=[('Content-Type',ctype),('Content-Length',str(len(data))),*headers.items()]
        start(f'{code} {phrase}',raw)
        return [data]


class ThreadedWSGI(ThreadingMixIn,WSGIServer):
    daemon_threads=True


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):pass


def main(argv=None):
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='action',required=True)
    sub.add_parser('init-auth')
    srv=sub.add_parser('serve')
    srv.add_argument('--port',type=int,default=PORT)
    a=ap.parse_args(argv)
    if a.action=='init-auth':
        p1=getpass.getpass('New Taskboard password (not echoed): ')
        p2=getpass.getpass('Repeat password: ')
        if p1!=p2:raise SystemExit('passwords differ')
        _write_auth(STATE,make_credentials(p1))
        print('AUTH_CREATED')
        return
    if a.port!=PORT: raise SystemExit('v2 Taskboard port fixed to 8789')
    app=App(port=a.port)
    with make_server(HOST,a.port,app,server_class=ThreadedWSGI,handler_class=QuietHandler) as server:
        print('PG Taskboard v2 started on 127.0.0.1:8789',flush=True)
        server.serve_forever()

if __name__=='__main__':main()
