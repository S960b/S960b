#!/usr/bin/env python3
"""Fail-closed 35G: pinned inactive service override + one-shot local MCP smoke.

No daemon enablement, no old tunnel rewire, no DB writes, no control plane calls.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage35g-managed-runtime'
C=HOME/'hermes-pg-stage35c-static-guard'
D=HOME/'hermes-pg-stage35d-local-smoke'
F=HOME/'hermes-pg-stage35f-guard-probe'
UNIT='hermes-pg-stage35c-static-shadow.service'
BASEUNIT=HOME/'.config/systemd/user'/UNIT
DROP=HOME/'.config/systemd/user'/ (UNIT+'.d')
DROPFILE=DROP/'35g-patched-runtime.conf'
ATTEMPT=ROOT/'stage35g-service-smoke-attempt.json'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
OLD_SHA='643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c'
D_SHA='bbed81ef18b508dedf08aa90ca6cb55ceced797242ad80f1c8c567d1c7a0ccfb'
C_STAGE_SHA='7e20d48e56ca5c4238641dcf2f8c31c633b43afaeaffad7a19f0a13a38b09a36'
FIX_SHA='695622407566b316482f676da0bf3c5a63a12b3f4b6e6d706c92130a760aaa88'
F_STAGE_SHA=''
PORT=18777
UNIT_DROP='''[Service]
ExecStart=
ExecStart=/usr/bin/env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage35g-managed-runtime/service35g.py
Restart=no
'''
class Stop(Exception):pass

def need(ok,reason):
    if not ok:raise Stop(reason)

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def safe_file(path,digest=None,mode_private=True):
    try:st=path.lstat()
    except OSError:return False
    if not (stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and st.st_nlink==1):return False
    if (st.st_mode & (0o077 if mode_private else 0o022)):return False
    return digest is None or sha(path)==digest

@contextmanager
def cwd(path):
    before=Path.cwd();os.chdir(path)
    try:yield
    finally:os.chdir(before)

def cmd(*args,timeout=15):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)

def active(name):return cmd('systemctl','--user','is-active','--quiet',name).returncode==0

def port_unused():
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.settimeout(.8)
    try:return s.connect_ex(('127.0.0.1',PORT))!=0
    finally:s.close()

def check_original():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==HOME and Path.cwd().resolve()==ROOT,'BAD_USER_OR_CWD')
    need(safe_file(C/'static_guard35c.py',OLD_SHA), 'ORIGINAL_35C_GATE_DRIFT_OR_MODE')
    need(safe_file(C/'stage35c.py',C_STAGE_SHA,mode_private=False), 'ORIGINAL_35C_STAGE_DRIFT')
    need(safe_file(D/'stage35d.py',D_SHA), 'ORIGINAL_35D_STAGE_DRIFT')
    need(safe_file(ROOT/'guard35g.py',FIX_SHA), '35G_PATCH_DRIFT_OR_MODE')
    # Literal and exact one-line equivalence to the already proven Stage35F patch.
    source=(C/'static_guard35c.py').read_text('utf8')
    old="if used:return {'type':'http.request','body':b'','more_body':False}"
    new='if used:return await receive()  # Stage35F: forward the real disconnect; never spin'
    need(source.count(old)==1 and source.replace(old,new)==(ROOT/'guard35g.py').read_text('utf8'),
         'PATCH_NOT_THE_EXACT_35F_FIX')
    need(safe_file(F/'guard35f.py',FIX_SHA) and
         safe_file(F/'stage35f-once-attempt.json') and
         safe_file(F/'stage35f-uvicorn-private.log'), 'STAGE35F_SUCCESS_ARTIFACTS_UNSAFE')
    obj=json.loads((F/'stage35f-once-attempt.json').read_text('utf8'))
    need(obj.get('once_attempted') is True and obj.get('tunnel_switched') is False,
         'STAGE35F_ATTEMPT_MARKER_DRIFT')
    sys.path.insert(0,str(D))
    import stage35d
    with cwd(D):stage35d.live_checks()
    sys.path.insert(0,str(C))
    import stage35c
    need(safe_file(BASEUNIT) and BASEUNIT.read_text('utf8')==stage35c.UNIT_TEXT,
         'ORIGINAL_SYSTEMD_UNIT_DRIFT')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'WRITE_PERMIT_PRESENT')
    need(not active(UNIT) and port_unused(),'ORIGINAL_SERVICE_ACTIVE_OR_PORT_OCCUPIED')
    need(safe_file(ROOT/'service35g.py') and safe_file(ROOT/'stream35g.py'),
         '35G_SERVICE_OR_CLIENT_UNSAFE')
    print('STAGE35G_ORIGINAL_35C_35D_35F_PINS_AND_OLD_TUNNEL_OK',flush=True)

def preflight():
    check_original()
    need(not DROP.exists() and not DROP.is_symlink(), 'DROPIN_ALREADY_EXISTS')
    need(not ATTEMPT.exists() and not ATTEMPT.is_symlink(),'35G_ALREADY_ATTEMPTED')
    print('STAGE35G_PREFLIGHT_INACTIVE_SERVICE_READY_NO_TUNNEL_SWITCH',flush=True)

def create_file(path,data):
    flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY
    if hasattr(os,'O_NOFOLLOW'):flags|=os.O_NOFOLLOW
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())

def show(field):
    r=cmd('systemctl','--user','show',UNIT,'--property='+field,'--value')
    need(r.returncode==0,'SYSTEMCTL_SHOW_'+field+'_FAILED')
    return r.stdout.strip()

def check_dropin():
    need(DROP.is_dir() and not DROP.is_symlink() and DROP.stat().st_uid==os.getuid() and
         (DROP.stat().st_mode&0o077)==0,'DROPIN_DIRECTORY_UNSAFE')
    need({p.name for p in DROP.iterdir()}=={'35g-patched-runtime.conf'} and
         safe_file(DROPFILE) and DROPFILE.read_text('utf8')==UNIT_DROP,
         'DROPIN_CONTENT_UNEXPECTED')
    drop_paths=show('DropInPaths')
    need(str(DROPFILE) in drop_paths,'SYSTEMD_DROPIN_NOT_LOADED')
    start=show('ExecStart')
    need('service35g.py' in start and 'managed_mcp35c.py' not in start,
         'SYSTEMD_EXECSTART_NOT_PATCHED')
    need(show('Restart')=='no','SYSTEMD_RESTART_NOT_DISABLED')
    need(show('UnitFileState') in ('disabled','static'), 'SHADOW_UNIT_UNEXPECTEDLY_ENABLED')
    print('STAGE35G_SYSTEMD_EFFECTIVE_EXECSTART_PATCHED_RESTART_NO_INACTIVE',flush=True)

def install_dropin():
    preflight()
    DROP.mkdir(mode=0o700)
    create_file(DROPFILE,UNIT_DROP.encode('utf8'))
    r=cmd('systemctl','--user','daemon-reload')
    need(r.returncode==0,'SYSTEMD_DAEMON_RELOAD_FAILED')
    check_dropin()
    need(not active(UNIT),'35G_SHADOW_UNEXPECTEDLY_ACTIVE_AFTER_INSTALL')
    print('STAGE35G_INSTALLED_DROPIN_ONLY_ORIGINAL_35C_BYTES_UNCHANGED',flush=True)

def ping():
    c=http.client.HTTPConnection('127.0.0.1',PORT,timeout=1.5)
    r=None
    try:
        c.request('GET','/ping',headers={'Connection':'close'})
        r=c.getresponse()
        return r.status==200 and b'stage35c-static-shadow' in r.read(512)
    except (TimeoutError,OSError):return False
    finally:
        if r is not None:r.close()
        c.close()

def deny(secret=None):
    c=http.client.HTTPConnection('127.0.0.1',PORT,timeout=5)
    r=None
    try:
        h={'Accept':'application/json, text/event-stream','Content-Type':'application/json',
           'Connection':'close'}
        if secret is not None:h['X-Bridge-Local-Key']=secret
        c.request('POST','/mcp',body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',headers=h)
        r=c.getresponse()
        return r.status==403
    finally:
        if r is not None:r.close()
        c.close()

def probe():
    from stream35g import rpc,delete_session,tool_payload
    sys.path.insert(0,str(C))
    from static_guard35c import local_secret
    secret=local_secret().decode('ascii')
    need(deny() and deny('wrong'), 'AUTH_DENIAL_FAILED')
    print('STAGE35G_SERVICE_AUTH_DENIED_403_TWICE',flush=True)
    session=None;protocol='2025-06-18'
    try:
        reply,h=rpc('initialize',{'protocolVersion':protocol,'capabilities':{},
                     'clientInfo':{'name':'stage35g-managed-service-smoke','version':'1.0'}},
                    secret=secret,request_id=61)
        need(isinstance(reply.get('protocolVersion'),str),'RPC_INIT_BAD_PROTOCOL')
        protocol=reply['protocolVersion'];session=h['session']
        print('STAGE35G_SERVICE_INITIALIZE_OK_FRAMING_'+('SSE' if h['content_type']=='text/event-stream' else 'JSON'),flush=True)
        rpc('notifications/initialized',secret=secret,session=session,protocol=protocol,notification=True)
        listing,_=rpc('tools/list',secret=secret,session=session,protocol=protocol,request_id=62)
        names={x.get('name') for x in listing.get('tools',[]) if isinstance(x,dict)}
        need(names=={'managed_health','managed_create_status','managed_list','managed_status',
                     'managed_read','managed_send_status','managed_delivery_status','managed_create',
                     'managed_set_dispatch','managed_send_task','managed_ack_report'},'TOOLS_LIST_UNEXPECTED')
        print('STAGE35G_SERVICE_TOOLS_LIST_11_EXPECTED_NO_WRITE_CALLS',flush=True)
        r,_=rpc('tools/call',{'name':'managed_health','arguments':{}},secret=secret,
                session=session,protocol=protocol,request_id=63)
        d=tool_payload(r)
        need(d.get('ok') is True and isinstance(d.get('result'),dict) and
             d['result'].get('connected') is True and d['result'].get('role')=='bridge_chatgpt',
             'HEALTH_ROLE_OR_DB_BAD')
        r,_=rpc('tools/call',{'name':'managed_list','arguments':{'offset':0,'limit':10}},
                secret=secret,session=session,protocol=protocol,request_id=64)
        d=tool_payload(r)
        need(d.get('ok') is True and isinstance(d.get('result'),list) and
             len(d['result'])==1 and d['result'][0].get('task_id')==
             'task_bddaadef89b7458fb7f8cf437dc0f023' and
             d['result'][0].get('dispatch_enabled') is False,'MANAGED_PILOT_DRIFT')
        print('STAGE35G_SERVICE_ROLE_BOUND_PG_READS_PILOT_DISABLED',flush=True)
    finally:
        if session:
            delete_session(secret=secret,session=session,protocol=protocol)

def stop_and_wait():
    # Only this named unit was started by this one-shot sequence; do not touch the tunnel.
    r=cmd('systemctl','--user','stop','--no-block',UNIT)
    if r.returncode!=0: print('STAGE35G_STOP_REQUEST_RETURNED_NONZERO',flush=True)
    start=time.monotonic()
    while time.monotonic()-start<20:
        state=show('ActiveState')
        if state in ('inactive','failed') and port_unused():
            return 'sigterm' if state=='inactive' else 'failed'
        time.sleep(.4)
    print('STAGE35G_SIGTERM_WAIT_TIMEOUT_SERVICE_TARGET_ONLY',flush=True)
    cmd('systemctl','--user','kill','--signal=SIGKILL',UNIT)
    cmd('systemctl','--user','stop','--no-block',UNIT)
    for _ in range(20):
        if not active(UNIT) and port_unused():return 'sigkill_after_timeout'
        time.sleep(.4)
    return 'not_stopped'

def smoke_once():
    check_original()
    check_dropin()
    need(not ATTEMPT.exists() and not ATTEMPT.is_symlink(),'35G_SMOKE_ALREADY_ATTEMPTED')
    # Marker before systemctl start: no ambiguous replay after interrupted attempt.
    create_file(ATTEMPT,b'{"stage":"35G","service_smoke_attempted":true,"tunnel_switched":false}\n')
    print('STAGE35G_SERVICE_ONE_SHOT_MARKER_CREATED',flush=True)
    started=False;err=None
    try:
        r=cmd('systemctl','--user','reset-failed',UNIT)
        need(r.returncode==0,'SYSTEMD_RESET_FAILED_STATE_FAILED')
        r=cmd('systemctl','--user','start',UNIT,timeout=20)
        need(r.returncode==0,'SERVICE_START_FAILED')
        started=True
        t=time.monotonic()
        while time.monotonic()-t<60:
            if ping():break
            need(show('ActiveState') not in ('failed','inactive'),'SHADOW_FAILED_DURING_START')
            time.sleep(.4)
        else:raise Stop('SHADOW_STARTUP_60S_TIMEOUT')
        print('STAGE35G_SYSTEMD_18777_PING_UP_NO_TUNNEL_CUTOVER',flush=True)
        probe()
        need(active(UNIT),'SHADOW_EXITED_BEFORE_SHUTDOWN')
    except Exception as e:err=e
    finally:
        # A service start may have reached systemd even if the start subprocess failed.
        try:
            how=stop_and_wait()
            print('STAGE35G_SERVICE_STOP_METHOD_'+how,flush=True)
            if how!='sigterm' and err is None:err=Stop('SERVICE_DID_NOT_STOP_CLEANLY')
        except Exception as e:
            if err is None:err=e
            else:print('STAGE35G_SERVICE_STOP_ERROR_'+type(e).__name__,flush=True)
    need(port_unused() and not active(UNIT),'SHADOW_LEFT_RUNNING')
    sys.path.insert(0,str(D))
    import stage35d
    with cwd(D):stage35d.live_checks()
    print('STAGE35G_POSTCHECK_18777_CLOSED_18773_ACTIVE_PERMIT_ABSENT',flush=True)
    if err is not None:raise err
    print('STAGE35G_SYSTEMD_PATCHED_GATE_SMOKE_SUCCESS_SERVICE_STOPPED',flush=True)

def client_help():
    # No real tunnel process, credentials, external session or control-plane call.
    sys.path.insert(0,str(D));import stage35d
    binary=stage35d.BIN
    env={'PATH':'/usr/bin:/bin','HOME':'/home/kali','LANG':'C','LC_ALL':'C'}
    p=subprocess.run([str(binary),'run','--help'],capture_output=True,text=True,timeout=15,env=env)
    need(p.returncode==0,'CLIENT_HELP_FAILED')
    value=p.stdout+'\n'+p.stderr
    need('mcp.extra-headers' in value.lower(),'CLIENT_EXTRA_HEADERS_FLAG_MISSING')
    print('STAGE35G_CLIENT_EXTRA_HEADERS_FLAG_PRESENT_FILE_REFERENCE_NOT_PROVEN',flush=True)
    # Only sanitized flag description, no environment or config values.
    for ln in value.splitlines():
        if 'mcp.extra-headers' in ln.lower():
            clean=re.sub(r'[^A-Za-z0-9 _.,:\[\]{}()/=+<>-]','',ln.strip())[:220]
            print('STAGE35G_CLIENT_HELP_FLAG_'+clean,flush=True)
            break

def main():
    try:
        if sys.argv[1:]==['preflight']:preflight()
        elif sys.argv[1:]==['install-dropin']:install_dropin()
        elif sys.argv[1:]==['service-smoke-once']:smoke_once()
        elif sys.argv[1:]==['client-help']:client_help()
        else:raise Stop('INVALID_STAGE35G_COMMAND')
        return 0
    except Exception as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE35G_STOP_'+code,file=sys.stderr,flush=True)
        return 2
if __name__=='__main__':raise SystemExit(main())
