#!/usr/bin/env python3
"""Stage35H+I: actual installed tunnel-client via *local* dev proxy only.

No control-plane key, external tunnel ID, systemd tunnel edits or database writes.
The real tunnel-client must resolve the private file: value and forward MCP.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage35hi-local-e2e'
G=HOME/'hermes-pg-stage35g-managed-runtime'
D=HOME/'hermes-pg-stage35d-local-smoke'
F=HOME/'hermes-pg-stage35f-guard-probe'
C=HOME/'hermes-pg-stage35c-static-guard'
KEY=HOME/'.config/bridge-pg-v2/managed-mcp-local-key35c'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
UNIT='hermes-pg-stage35c-static-shadow.service'
BIN=HOME/'.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client'
ATTEMPT=ROOT/'stage35hi-local-proxy-attempt.json'
PASSMARK=ROOT/'stage35hi-local-proxy-proven.json'
CAND=ROOT/'run_stage35hi_candidate_NOT_ACTIVATED.sh'
LEGACY='hermes-pg-tunnel-stage33fd.service'
TOOLS={'managed_health','managed_create_status','managed_list','managed_status',
       'managed_read','managed_send_status','managed_delivery_status','managed_create',
       'managed_set_dispatch','managed_send_task','managed_ack_report'}
SOURCES={'proxy35hi.py','test_stage35hi.py','README.md','SHA256SUMS',
         '170_install_and_run_once.sh','run_stage35hi_candidate_NOT_ACTIVATED.sh'}

class Stop(Exception):pass

def need(v,why):
    if not v:raise Stop(why)

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def safe_file(path, digest=None):
    try:s=path.lstat()
    except OSError:return False
    if not(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and
           (s.st_mode&0o077)==0):return False
    return digest is None or sha(path)==digest

def do(*a,timeout=12):
    return subprocess.run(a,timeout=timeout,capture_output=True,text=True)

def active(unit):return do('systemctl','--user','is-active','--quiet',unit).returncode==0

def private_create(path,data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())

def local_port_closed(port):
    with socket.socket() as sock:
        sock.settimeout(.6)
        return sock.connect_ex(('127.0.0.1',port))!=0

def ping(port):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
    try:
        c.request('GET','/ping')
        r=c.getresponse()
        return r.status==200 and b'stage35c-static-shadow' in r.read(512)
    except (OSError,TimeoutError):return False
    finally:c.close()

def source_ready():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==HOME and Path.cwd().resolve()==ROOT,'WRONG_USER_OR_CWD')
    need(ROOT.is_dir() and not ROOT.is_symlink() and (ROOT.stat().st_mode&0o077)==0,'ROOT_UNSAFE')
    for name in SOURCES:
        need(safe_file(ROOT/name),'STAGE35HI_PACKAGE_FILE_UNSAFE_'+name)
    need(not ATTEMPT.exists() and not ATTEMPT.is_symlink(),'35HI_ONE_SHOT_ALREADY_ATTEMPTED')
    need(not PASSMARK.exists() and not PASSMARK.is_symlink(),'35HI_ALREADY_PROVEN')
    need(not active(UNIT) and local_port_closed(18777),'35C_SERVICE_OR_PORT_ALREADY_ACTIVE')
    need(active(LEGACY) and active('hermes-pg-v2-stage33fb-cg.service') and
         active('bridge-pg-v2-ssh-shadow.service'),'ORIGINAL_BRIDGE_NOT_ACTIVE')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'MANAGED_WRITE_PERMIT_PRESENT')
    need(safe_file(KEY) and len(KEY.read_bytes().strip())==64,'LOCAL_SECRET_UNSAFE')
    need(BIN.is_file() and not BIN.is_symlink() and os.access(BIN,os.X_OK),'CLIENT_BINARY_MISSING')
    sys.path.insert(0,str(G))
    sys.path.insert(0,str(D))
    import stage35g,stage35d
    # Both original stage checkers pin cwd to their own installation roots.
    from contextlib import contextmanager
    @contextmanager
    def cwd(path):
        before=Path.cwd();os.chdir(path)
        try:yield
        finally:os.chdir(before)
    with cwd(G):
        stage35g.check_original()
        stage35g.check_dropin()
    # Must continue to see original canary, seven plugins and live SSH/PG roles.
    with cwd(D):stage35d.live_checks()
    need(not active(UNIT),'35C_UNIT_UNEXPECTEDLY_ACTIVE')
    r=do(str(BIN),'dev','proxy','--help',timeout=15)
    helptext=(r.stdout+'\n'+r.stderr).lower()
    need(r.returncode==0 and 'mcp-server-url' in helptext and 'print-json' in helptext,
         'CLIENT_DEV_PROXY_NOT_SUPPORTED')
    need('listen' in helptext,'DEV_PROXY_LOOPBACK_LISTEN_UNSUPPORTED')
    print('STAGE35HI_PINNED_35G_SERVICE_SEVEN_PLUGINS_OLD_TUNNEL_OK',flush=True)
    print('STAGE35HI_OFFICIAL_CLIENT_LOCAL_DEV_PROXY_SUPPORTED_NO_EXTERNAL_CONTROL_PLANE',flush=True)

def proxy_url(url):
    try:
        p=urlsplit(url)
        port=p.port
    except ValueError:
        raise Stop('LOCAL_PROXY_INVALID_URL_OR_PORT') from None
    need(p.scheme=='http' and p.hostname=='127.0.0.1' and p.username is None and
         p.password is None and p.query=='' and p.fragment=='' and
         port is not None and 1024<=port<=65535 and
         re.fullmatch(r'/v1/mcp/[A-Za-z0-9_-]{4,160}',p.path) is not None,
         'LOCAL_PROXY_RETURNED_NONLOCAL_OR_UNEXPECTED_MCP_URL')
    return port,p.path

def proc_terminated(p,seconds=8):
    if p.poll() is not None:return 'already_exited'
    try:os.killpg(p.pid,signal.SIGTERM)
    except ProcessLookupError:return 'already_exited'
    try:p.wait(timeout=seconds);return 'sigterm'
    except subprocess.TimeoutExpired:
        os.killpg(p.pid,signal.SIGKILL)
        p.wait(timeout=3)
        return 'sigkill'

def wait_proxy_url(p,seconds=35):
    # The official CLI uses json.Encoder.SetIndent(), so --print-json is
    # MULTILINE. Read bounded chunks, never blocking readline on one fragment.
    sel=selectors.DefaultSelector()
    buf=bytearray()
    try:
        sel.register(p.stdout,selectors.EVENT_READ)
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            for key,_ in sel.select(timeout=max(.0,min(.5,deadline-time.monotonic()))):
                chunk=os.read(key.fileobj.fileno(),4096)
                need(bool(chunk),'DEV_PROXY_STDOUT_EOF_BEFORE_READY')
                buf.extend(chunk)
                need(len(buf)<=16384,'DEV_PROXY_INFO_TOO_LARGE')
                try:obj=json.loads(buf)
                except (ValueError,UnicodeError):continue
                if isinstance(obj,dict) and isinstance(obj.get('mcp_url'),str):
                    need(obj.get('control_plane_transport') in ('unix','tcp'),
                         'DEV_PROXY_CONTROL_PLANE_TRANSPORT_UNEXPECTED')
                    return proxy_url(obj['mcp_url'])
            if p.poll() is not None:raise Stop('DEV_PROXY_EXITED_BEFORE_READY')
        raise Stop('DEV_PROXY_READY_TIMEOUT')
    finally:sel.close()

def rpc_request(port,path,method,params=None,*,rid=1,session=None,protocol='2025-06-18',notify=False,verb='POST'):
    # The *outer* client never sends X-Bridge-Local-Key. Only real tunnel-client adds it.
    headers={'Accept':'application/json, text/event-stream',
             'Content-Type':'application/json','MCP-Protocol-Version':protocol,
             'Connection':'close'}
    if session:headers['Mcp-Session-Id']=session
    if verb=='DELETE':body=None
    else:
        obj={'jsonrpc':'2.0','method':method}
        if params is not None:obj['params']=params
        if not notify:obj['id']=rid
        body=json.dumps(obj,separators=(',',':')).encode()
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
    res=None
    try:
        c.request(verb,path,body=body,headers=headers)
        res=c.getresponse()
        info={'status':res.status,'content_type':(res.getheader('content-type') or '').split(';')[0].strip().lower(),
              'session':res.getheader('mcp-session-id')}
        if notify or verb=='DELETE':
            need(res.status in (200,202,204,404,405) if verb=='DELETE' else res.status in (200,202,204),
                 'PROXY_NOTIFICATION_OR_DELETE_STATUS_UNEXPECTED')
            return None,info
        need(res.status==200,'PROXY_RPC_NON_200')
        sys.path.insert(0,str(G))
        from stream35g import ReplyParser
        parser=ReplyParser(info['content_type'],rid)
        deadline=time.monotonic()+19
        while time.monotonic()<deadline:
            chunk=res.read1(4096)
            need(bool(chunk),'PROXY_RPC_EOF_NO_RESPONSE')
            parsed=parser.feed(chunk)
            if parsed is not None:return parsed,info
        raise Stop('PROXY_RPC_TIMED_OUT')
    except (OSError,TimeoutError) as exc:
        raise Stop('PROXY_NETWORK_'+type(exc).__name__) from None
    finally:
        if res:
            try:res.close()
            except OSError:pass
        c.close()

def exercise(port,path):
    sys.path.insert(0,str(G))
    from stream35g import tool_payload
    session=None;proto='2025-06-18'
    try:
        result,headers=rpc_request(port,path,'initialize',{'protocolVersion':proto,
                'capabilities':{},'clientInfo':{'name':'stage35hi-local-dev-proxy','version':'1'}},rid=170)
        need(isinstance(result.get('protocolVersion'),str),'PROXY_INIT_PROTOCOL_UNEXPECTED')
        proto=result['protocolVersion'];session=headers['session']
        need(session is not None and len(session)<=256,'PROXY_SESSION_MISSING')
        print('STAGE35H_REAL_CLIENT_FILE_BACKED_HEADER_INITIALIZE_OK_'+
              ('SSE' if headers['content_type']=='text/event-stream' else 'JSON'),flush=True)
        rpc_request(port,path,'notifications/initialized',session=session,protocol=proto,notify=True)
        tools,_=rpc_request(port,path,'tools/list',session=session,protocol=proto,rid=171)
        got={t.get('name') for t in tools.get('tools',[]) if isinstance(t,dict)}
        need(got==TOOLS,'PROXY_TOOL_LIST_DIFFERS')
        print('STAGE35H_REAL_CLIENT_TUNNEL_11_TOOLS_LISTED',flush=True)
        for name,args,rid in [('managed_health',{},172),('managed_list',{'offset':0,'limit':10},173)]:
            x,_=rpc_request(port,path,'tools/call',{'name':name,'arguments':args},
                            session=session,protocol=proto,rid=rid)
            d=tool_payload(x)
            need(d.get('ok') is True,'PROXY_READ_RESULT_UNEXPECTED')
            if name=='managed_health':
                v=d.get('result')
                need(isinstance(v,dict) and v.get('role')=='bridge_chatgpt' and v.get('connected') is True,
                     'PROXY_WRONG_POSTGRES_ROLE')
            else:
                v=d.get('result')
                need(isinstance(v,list) and len(v)==1 and v[0].get('task_id')==
                     'task_bddaadef89b7458fb7f8cf437dc0f023' and
                     v[0].get('dispatch_enabled') is False,'PROXY_MANAGED_PILOT_DRIFT')
        print('STAGE35H_REAL_CLIENT_READ_ONLY_PG_ROLE_AND_PILOT_OK',flush=True)
    finally:
        if session:
            try:
                _,meta=rpc_request(port,path,'',session=session,protocol=proto,verb='DELETE')
                print('STAGE35H_PROXY_SESSION_DELETE_STATUS_'+str(meta['status']),flush=True)
            except Exception:
                print('STAGE35H_PROXY_SESSION_CLEANUP_FAILED',flush=True)
                raise

def one_shot():
    source_ready()
    private_create(ATTEMPT,b'{"stage":"35HI","attempted":true,"cutover":false}\n')
    print('STAGE35HI_ONE_SHOT_MARKER_CREATED_NO_TUNNEL_SWITCH',flush=True)
    proc=None;started=False;failure=None;how='not_started';stopmethod='not_started'
    try:
        r=do('systemctl','--user','start',UNIT,timeout=20)
        need(r.returncode==0,'SHADOW_START_FAILED')
        started=True
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if ping(18777):break
            need(active(UNIT),'SHADOW_EXITED_BEFORE_PING')
            time.sleep(.4)
        else:raise Stop('SHADOW_PING_TIMEOUT')
        print('STAGE35H_PATCHED_SYSTEMD_SHADOW_PING_READY_LOOPBACK_ONLY',flush=True)
        # Both control-plane and ingress are local. No real tunnel ID, token or API key.
        env={'PATH':'/usr/bin:/bin','HOME':'/home/kali','LANG':'C','LC_ALL':'C',
             'MCP_EXTRA_HEADERS':'X-Bridge-Local-Key: file:'+str(KEY),
             'MCP_OAUTH_DISCOVERY':'disabled',
             'HTTP_PROXY':'','HTTPS_PROXY':'','ALL_PROXY':'','NO_PROXY':'127.0.0.1,localhost'}
        args=[str(BIN),'dev','proxy','--mcp-server-url','http://127.0.0.1:18777/mcp',
              '--listen','127.0.0.1:0','--print-json']
        # Private log never sent to chat: real tunnel-client can print session metadata.
        fd=os.open(ROOT/'stage35hi-dev-proxy-private.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as logfile:
            proc=subprocess.Popen(args,env=env,stdin=subprocess.DEVNULL,
                 stdout=subprocess.PIPE,stderr=logfile,start_new_session=True,bufsize=0)
            port,path=wait_proxy_url(proc)
            print('STAGE35H_REAL_TUNNEL_CLIENT_DEV_PROXY_LOCAL_INGRESS_READY',flush=True)
            exercise(port,path)
            need(proc.poll() is None,'DEV_PROXY_EXITED_EARLY')
    except Exception as e:failure=e
    finally:
        if proc is not None:
            try:how=proc_terminated(proc)
            except Exception as e:
                how='kill_failed'
                if failure is None:failure=e
        print('STAGE35HI_LOCAL_PROXY_SHUTDOWN_'+how,flush=True)
        if started or not local_port_closed(18777):
            try:
                import stage35g
                stopmethod=stage35g.stop_and_wait()
            except Exception as e:
                stopmethod='failed'
                if failure is None:failure=e
        print('STAGE35HI_35C_SERVICE_SHUTDOWN_'+stopmethod,flush=True)
    if how!='sigterm' or stopmethod!='sigterm':
        if failure is None:failure=Stop('LOCAL_PROXY_OR_SERVICE_SHUTDOWN_NOT_GRACEFUL')
    if not local_port_closed(18777) or active(UNIT):
        if failure is None:failure=Stop('LOCAL_SHADOW_LEFT_RUNNING')
    # Never conceal a failed bridge: require the old tunnel is active AFTER test.
    if not active(LEGACY):
        if failure is None:failure=Stop('ORIGINAL_TUNNEL_NOT_ACTIVE_POSTCHECK')
    if failure is not None:raise failure
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'WRITE_PERMIT_PRESENT_AFTER')
    private_create(PASSMARK,b'{"stage":"35HI","local_proxy_e2e":true,"file_header_proven":true,"cutover":false}\n')
    print('STAGE35H_FILE_BACKED_HEADER_PROVEN_REAL_CLIENT_READ_ONLY',flush=True)
    print('STAGE35I_CANDIDATE_LAUNCHER_PREPARED_NOT_ACTIVATED',flush=True)
    print('STAGE35HI_COMPLETE_OLD_TUNNEL_ACTIVE_NO_PLUGIN_CHANGE_NO_DB_WRITES',flush=True)

def main():
    try:
        if sys.argv[1:]==['preflight']:source_ready()
        elif sys.argv[1:]==['once']:one_shot()
        else:raise Stop('UNKNOWN_COMMAND')
        return 0
    except Exception as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE35HI_STOP_'+re.sub(r'[^A-Za-z0-9_]','_',code)[:100],file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main())
