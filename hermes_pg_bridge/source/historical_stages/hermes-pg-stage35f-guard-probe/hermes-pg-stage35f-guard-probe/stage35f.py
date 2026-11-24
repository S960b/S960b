#!/usr/bin/env python3
"""Stage35F one-shot isolated local MCP probe with patched receive semantics.

No systemd writes, existing tunnel changes, plugins changes, permits or SQL writes.
No secrets or response bodies printed. Private server faulthandler log retained.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
import pwd
import signal
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT=Path('/home/kali/hermes-pg-stage35f-guard-probe')
C=Path('/home/kali/hermes-pg-stage35c-static-guard')
D=Path('/home/kali/hermes-pg-stage35d-local-smoke')
E=Path('/home/kali/hermes-pg-stage35e-diag')
ATTEMPT=ROOT/'stage35f-once-attempt.json'
LOG=ROOT/'stage35f-uvicorn-private.log'
PORT=18777
HOST='127.0.0.1'
SHADOW='hermes-pg-stage35c-static-shadow.service'
D_SHA='bbed81ef18b508dedf08aa90ca6cb55ceced797242ad80f1c8c567d1c7a0ccfb'
GUARD_SHA='695622407566b316482f676da0bf3c5a63a12b3f4b6e6d706c92130a760aaa88'
SOURCE_SHA='643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c'

class Stop(Exception): pass

def need(condition,code):
    if not condition:raise Stop(code)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@contextmanager
def cwd(path):
    old=Path.cwd()
    os.chdir(path)
    try: yield
    finally: os.chdir(old)

def active(name):
    return subprocess.run(['systemctl','--user','is-active','--quiet',name],capture_output=True,timeout=8).returncode==0

def no_listener():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.settimeout(.6)
        return sock.connect_ex((HOST,PORT))!=0

def safe_file(p):
    try:st=p.lstat()
    except OSError:return False
    return stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and (st.st_mode&0o077)==0 and st.st_nlink==1

def preflight():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,'BAD_OPERATOR_OR_CWD')
    need(safe_file(D/'stage35d.py') and sha(D/'stage35d.py')==D_SHA,'STAGE35D_PIN_MISMATCH')
    need(safe_file(C/'static_guard35c.py') and sha(C/'static_guard35c.py')==SOURCE_SHA,
         'ORIGINAL_35C_GATE_CHANGED')
    need(safe_file(ROOT/'guard35f.py') and sha(ROOT/'guard35f.py')==GUARD_SHA,
         'PATCHED_GATE_CHANGED')
    # Validate the patch is a literal one-line substitution over pinned 35C.
    source=(C/'static_guard35c.py').read_text()
    old="if used:return {'type':'http.request','body':b'','more_body':False}"
    replacement="if used:return await receive()  # Stage35F: forward the real disconnect; never spin"
    need(source.count(old)==1 and source.replace(old,replacement)==(ROOT/'guard35f.py').read_text(),
         'GUARD_DIFF_NOT_SINGLE_RECEIVE_FIX')
    sys.path.insert(0,str(D))
    import stage35d
    with cwd(D):stage35d.preflight()
    for path,key in ((D/'local-shadow-start-attempt.json','local_service_start_attempted'),
                     (E/'stage35e-local-probe-attempt.json','local_diagnostic_attempted')):
        need(safe_file(path),'PREVIOUS_ONE_SHOT_PROOF_MISSING_OR_UNSAFE')
        try: data=json.loads(path.read_text())
        except (OSError,ValueError):raise Stop('PREVIOUS_ONE_SHOT_PROOF_INVALID') from None
        need(data.get(key) is True and data.get('tunnel_switched') is False,
             'PREVIOUS_ONE_SHOT_PROOF_UNEXPECTED')
    need(not active(SHADOW) and no_listener(),'SHADOW_ACTIVE_OR_PORT_18777_OPEN')
    need(not ATTEMPT.exists() and not ATTEMPT.is_symlink(),'35F_ALREADY_ATTEMPTED')
    need(not LOG.exists() and not LOG.is_symlink(),'35F_LOG_ALREADY_PRESENT')
    print('STAGE35F_PREFLIGHT_PINS_ONE_LINE_GATE_DIFF_AND_OLD_TUNNEL_OK',flush=True)

def create_exclusive(path,data):
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
    if hasattr(os,'O_NOFOLLOW'):flags|=os.O_NOFOLLOW
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data)
        f.flush();os.fsync(f.fileno())

def ping():
    c=http.client.HTTPConnection(HOST,PORT,timeout=1.4)
    r=None
    try:
        c.request('GET','/ping',headers={'Connection':'close'})
        r=c.getresponse()
        return r.status==200 and b'stage35c-static-shadow' in r.read(512)
    except (TimeoutError,OSError):return False
    finally:
        if r is not None:r.close()
        c.close()

def deny_auth(secret=None):
    c=http.client.HTTPConnection(HOST,PORT,timeout=3)
    r=None
    try:
        h={'Accept':'application/json, text/event-stream','Content-Type':'application/json','Connection':'close'}
        if secret is not None:h['X-Bridge-Local-Key']=secret
        c.request('POST','/mcp',body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',headers=h)
        r=c.getresponse()
        return r.status==403
    finally:
        if r is not None:r.close()
        c.close()

def mcp_probe():
    from stream35f import rpc,delete_session,tool_payload
    sys.path.insert(0,str(C))
    from static_guard35c import local_secret
    secret=local_secret().decode('ascii')
    need(len(secret)==64,'SECRET_INVALID')
    need(deny_auth() and deny_auth('wrong'),'AUTH_BYPASS_OR_TIMEOUT')
    print('STAGE35F_NEGATIVE_AUTH_403_TWICE',flush=True)
    session=None
    protocol='2025-06-18'
    try:
        init,h=rpc('initialize',{'protocolVersion':protocol,'capabilities':{},
                  'clientInfo':{'name':'stage35f-one-time-test','version':'1.0'}},
                   secret=secret,request_id=51)
        need(isinstance(init.get('protocolVersion'),str),'INITIALIZE_PROTOCOL_MISSING')
        protocol=init['protocolVersion']
        session=h['session']
        print('STAGE35F_INITIALIZE_OK_FRAMING_'+('SSE' if h['content_type']=='text/event-stream' else 'JSON'),flush=True)
        rpc('notifications/initialized',secret=secret,session=session,
            protocol=protocol,notification=True)
        tools,_=rpc('tools/list',secret=secret,session=session,protocol=protocol,request_id=52)
        names={v.get('name') for v in tools.get('tools',[]) if isinstance(v,dict)}
        need(names=={'managed_health','managed_create_status','managed_list','managed_status',
                     'managed_read','managed_send_status','managed_delivery_status','managed_create',
                     'managed_set_dispatch','managed_send_task','managed_ack_report'},
             'TOOLSET_NOT_11_EXPECTED')
        print('STAGE35F_TOOLS_LIST_11_MATCH_READ_ONLY',flush=True)
        response,_=rpc('tools/call',{'name':'managed_health','arguments':{}},
                       secret=secret,session=session,protocol=protocol,request_id=53)
        hdata=tool_payload(response)
        need(hdata.get('ok') is True and isinstance(hdata.get('result'),dict) and
             hdata['result'].get('connected') is True and
             hdata['result'].get('role')=='bridge_chatgpt','HEALTH_NOT_ROLE_BOUND')
        response,_=rpc('tools/call',{'name':'managed_list','arguments':{'offset':0,'limit':10}},
                       secret=secret,session=session,protocol=protocol,request_id=54)
        ldata=tool_payload(response);items=ldata.get('result')
        need(ldata.get('ok') is True and isinstance(items,list) and len(items)==1 and
             items[0].get('task_id')=='task_bddaadef89b7458fb7f8cf437dc0f023' and
             items[0].get('dispatch_enabled') is False,'MANAGED_PILOT_NOT_ONE_DISABLED')
        print('STAGE35F_ROLE_BOUND_POSTGRES_READS_OK_DISABLED_PILOT',flush=True)
        print('STAGE35F_DB_WRITE_TOOLS_NOT_CALLED_NO_PERMIT',flush=True)
    finally:
        if session:
            try:delete_session(secret=secret,session=session,protocol=protocol)
            except Exception as exc:
                print('STAGE35F_SESSION_CLEANUP_FAILED_'+type(exc).__name__,flush=True)

def signal_trace(proc):
    if proc.poll() is None and hasattr(signal,'SIGUSR1'):
        try:
            os.killpg(proc.pid,signal.SIGUSR1)
            print('STAGE35F_PRIVATE_SERVER_STACK_REQUESTED',flush=True)
            time.sleep(.3)
        except (ProcessLookupError,PermissionError):pass

def stop_child(proc):
    if proc.poll() is not None:return 'already_exited'
    os.killpg(proc.pid,signal.SIGTERM)
    try:
        proc.wait(timeout=10)
        return 'sigterm'
    except subprocess.TimeoutExpired:
        print('STAGE35F_SIGTERM_10S_HANG_REQUESTING_PRIVATE_STACK',flush=True)
        signal_trace(proc)
        if proc.poll() is None:os.killpg(proc.pid,signal.SIGKILL)
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:raise Stop('CHILD_UNREAPED') from None
        return 'sigkill_after_10s'

def postcheck():
    need(not active(SHADOW) and no_listener(),'SHADOW_OR_PORT_LEFT_RUNNING')
    sys.path.insert(0,str(D))
    import stage35d
    with cwd(D):stage35d.live_checks()
    need(safe_file(ATTEMPT) and safe_file(LOG),'35F_PRIVATE_EVIDENCE_MISSING')
    print('STAGE35F_POSTCHECK_18777_CLOSED_18773_TUNNEL_UNCHANGED_NO_PERMIT',flush=True)

def run_once():
    preflight()
    create_exclusive(ATTEMPT,b'{"stage":"35F","once_attempted":true,"tunnel_switched":false,"systemd_untouched":true}\n')
    print('STAGE35F_ONE_SHOT_MARKER_CREATED_35D_35E_MARKERS_UNCHANGED',flush=True)
    fd=os.open(LOG,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    proc=None;error=None
    try:
        with os.fdopen(fd,'w',encoding='utf8') as out:
            env=os.environ.copy()
            for k in ('APPDIR','APPIMAGE','PYTHONPATH'):env.pop(k,None)
            env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONUNBUFFERED']='1'
            proc=subprocess.Popen(['/home/kali/bridge-pg-v2/.venv/bin/python','-u',
                      str(ROOT/'diag_server35f.py')],cwd=C,env=env,
                      stdout=out,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                      start_new_session=True)
            started=time.monotonic()
            while time.monotonic()-started<60:
                if ping():break
                if proc.poll() is not None:raise Stop('UVICORN_EARLY_EXIT')
                time.sleep(.4)
            else:raise Stop('UVICORN_STARTUP_60S_TIMEOUT')
            print('STAGE35F_UVICORN_STARTED_18777_PING_OK',flush=True)
            mcp_probe()
    except Exception as exc:
        error=exc
        if proc is not None:signal_trace(proc)
    finally:
        if proc is not None:
            try:
                how=stop_child(proc)
                print('STAGE35F_STOP_METHOD_'+how,flush=True)
                if how=='sigkill_after_10s' and error is None:error=Stop('CHILD_SIGTERM_10S_TIMEOUT')
            except Exception as exc:
                if error is None:error=exc
                else:print('STAGE35F_STOP_CHILD_ERROR_'+type(exc).__name__,flush=True)
    try:postcheck()
    except Exception as exc:
        if error is None:error=exc
        else:print('STAGE35F_POSTCHECK_ERROR_'+type(exc).__name__,flush=True)
    if error is not None:raise error
    print('STAGE35F_ISOLATED_PATCHED_GATE_SMOKE_SUCCESS_NO_TUNNEL_CUTOVER',flush=True)

def main():
    try:
        if sys.argv[1:]==['preflight']:preflight()
        elif sys.argv[1:]==['once']:run_once()
        elif sys.argv[1:]==['postcheck']:postcheck()
        else:raise Stop('BAD_COMMAND')
        return 0
    except Exception as e:
        from stream35f import ProbeError
        code=str(e) if isinstance(e,(Stop,ProbeError)) else type(e).__name__
        print('STAGE35F_STOP_'+code,file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main())
