#!/usr/bin/env python3
"""Stage 35E: one isolated diagnostic server attempt, read-only MCP probe.

Not the Stage35D start script. No unit modifications, plugin changes, tunnel
cutover, SQL mutations or generated write permits. Captures info-level server
tracebacks privately without printing MCP data or header secrets.
"""
from __future__ import annotations
import hashlib, http.client, json, os, pwd, signal, socket, stat, subprocess, sys, time
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def in_directory(directory):
    original=Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(original)


ROOT=Path('/home/kali/hermes-pg-stage35e-diag')
STAGE35D=Path('/home/kali/hermes-pg-stage35d-local-smoke')
STAGE35C=Path('/home/kali/hermes-pg-stage35c-static-guard')
UNIT='hermes-pg-stage35c-static-shadow.service'
ATTEMPT=ROOT/'stage35e-local-probe-attempt.json'
LOG=ROOT/'stage35e-uvicorn-info.log'
PREVIOUS=STAGE35D/'local-shadow-start-attempt.json'
HOST='127.0.0.1';PORT=18777
EXPECTED_35D='bbed81ef18b508dedf08aa90ca6cb55ceced797242ad80f1c8c567d1c7a0ccfb'

class Stop(Exception):pass

def need(condition, code):
    if not condition:raise Stop(code)

def check_context():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,'WRONG_USER_OR_CWD')

def hash_file(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def active(unit):
    return subprocess.run(['systemctl','--user','is-active','--quiet',unit],
                          timeout=10,capture_output=True).returncode==0

def port_closed():
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.settimeout(.4)
    try:return sock.connect_ex((HOST,PORT)) != 0
    finally:sock.close()

def pinned_preflight():
    check_context()
    need((STAGE35D/'stage35d.py').is_file() and
         hash_file(STAGE35D/'stage35d.py')==EXPECTED_35D,'35D_PINFIX_SOURCE_CHANGED')
    # Call the previously audited preflight exactly as installed; checks
    # 35C hashes, 7-plugin config, active old tunnel, secret, missing permit.
    sys.path.insert(0,str(STAGE35D))
    import stage35d
    with in_directory(STAGE35D):
        stage35d.preflight()
    need(PREVIOUS.is_file() and not PREVIOUS.is_symlink() and
         (PREVIOUS.stat().st_mode & 0o077)==0,'35D_ATTEMPT_PROOF_MISSING')
    try:p=json.loads(PREVIOUS.read_text())
    except (ValueError,OSError):raise Stop('35D_ATTEMPT_INVALID') from None
    need(p.get('local_service_start_attempted') is True and
         p.get('tunnel_switched') is False,'35D_ATTEMPT_UNEXPECTED')
    need(not active(UNIT) and port_closed(),'SHADOW_ALREADY_RUNNING_OR_PORT_IN_USE')
    need(not ATTEMPT.exists() and not ATTEMPT.is_symlink(),'35E_DIAGNOSTIC_ALREADY_ATTEMPTED')
    need(not LOG.exists() and not LOG.is_symlink(),'35E_LOG_ALREADY_EXISTS')
    print('STAGE35E_READONLY_PREFLIGHT_OK_35D_ONE_SHOT_MARKER_UNTOUCHED',flush=True)

def create_exclusive(path:Path,data:bytes):
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os,'O_NOFOLLOW'):flags|=os.O_NOFOLLOW
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())

def get_ping():
    conn=http.client.HTTPConnection(HOST,PORT,timeout=1.5)
    r=None
    try:
        conn.request('GET','/ping',headers={'Connection':'close'})
        r=conn.getresponse();raw=r.read(512)
        return r.status==200 and b'stage35c-static-shadow' in raw
    except (OSError,TimeoutError):return False
    finally:
        if r is not None:r.close()
        conn.close()

def auth_denied(secret=None):
    conn=http.client.HTTPConnection(HOST,PORT,timeout=5)
    r=None
    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream',
             'Connection':'close'}
    if secret is not None:headers['X-Bridge-Local-Key']=secret
    try:
        conn.request('POST','/mcp',body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',headers=headers)
        r=conn.getresponse()
        # Do not dump response. Both cases must deny.
        return r.status==403
    finally:
        if r is not None:r.close()
        conn.close()

def mcp_probe():
    sys.path.insert(0,str(STAGE35C))
    from static_guard35c import local_secret
    from stream35e import rpc,tool_payload,ProbeError
    secret=local_secret().decode('ascii')
    need(len(secret)==64,'SECRET_INVALID')
    need(auth_denied() and auth_denied('invalid'),'UNAUTH_MCP_NOT_DENIED')
    print('STAGE35E_PING_OK_UNAUTH_AND_WRONG_KEY_DENIED',flush=True)

    init,h=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
                 'clientInfo':{'name':'stage35e-local-diagnostic','version':'1.0'}},
                 secret=secret,request_id=11)
    need(isinstance(init.get('protocolVersion'),str),'MCP_INITIALIZE_INVALID')
    protocol=init['protocolVersion']
    session=h['session']
    print('STAGE35E_STREAM_SAFE_INITIALIZE_OK_FRAMING_'+
          ('SSE' if h['content_type']=='text/event-stream' else 'JSON'),flush=True)
    rpc('notifications/initialized',secret=secret,session=session,
        protocol=protocol,notification=True)
    listing,_=rpc('tools/list',secret=secret,session=session,protocol=protocol,request_id=12)
    names={v.get('name') for v in listing.get('tools',[]) if isinstance(v,dict)}
    expected={'managed_health','managed_create_status','managed_list','managed_status',
              'managed_read','managed_send_status','managed_delivery_status','managed_create',
              'managed_set_dispatch','managed_send_task','managed_ack_report'}
    need(names==expected,'MCP_TOOLSET_NOT_EXPECTED_11')
    print('STAGE35E_EXACT_11_TOOLS_LISTED_NO_TOOL_WRITES',flush=True)
    health,_=rpc('tools/call',{'name':'managed_health','arguments':{}},secret=secret,
                 session=session,protocol=protocol,request_id=13)
    data=tool_payload(health)
    need(data.get('ok') is True and isinstance(data.get('result'),dict) and
         data['result'].get('connected') is True and
         data['result'].get('role')=='bridge_chatgpt','MANAGED_HEALTH_READ_FAILED')
    listing,_=rpc('tools/call',{'name':'managed_list','arguments':{'offset':0,'limit':10}},
                  secret=secret,session=session,protocol=protocol,request_id=14)
    data=tool_payload(listing);items=data.get('result')
    need(data.get('ok') is True and isinstance(items,list) and len(items)==1 and
         items[0].get('task_id')=='task_bddaadef89b7458fb7f8cf437dc0f023' and
         items[0].get('dispatch_enabled') is False,
         'MANAGED_PILOT_NOT_ONE_DISABLED')
    print('STAGE35E_REAL_ROLE_BOUND_DB_READS_OK_ONE_DISABLED_PILOT',flush=True)
    # Session lifetime can be cleaned without modifying database or managed state.
    # We do not send DELETE here; close all response connections promptly instead.
    print('STAGE35E_ZERO_MCP_WRITE_TOOLS_INVOKED',flush=True)


def stop_child(proc):
    if proc.poll() is not None:return 'already_exited'
    os.killpg(proc.pid,signal.SIGTERM)
    try:
        proc.wait(timeout=10)
        return 'sigterm'
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid,signal.SIGKILL)
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:raise Stop('LOCAL_DIAGNOSTIC_CHILD_UNREAPED') from None
        return 'sigkill_after_10s'

def run_once():
    pinned_preflight()
    # Atomic one-shot mark is written before binding a listening socket.
    create_exclusive(ATTEMPT,b'{"stage":"35E","local_diagnostic_attempted":true,"tunnel_switched":false,"service_unit_untouched":true}\n')
    print('STAGE35E_DIAG_ONCE_MARKED_NO_35D_REPLAY',flush=True)
    fd=os.open(LOG,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    proc=None;error=None
    try:
        with os.fdopen(fd,'w',encoding='utf8') as logfile:
            env=os.environ.copy()
            for k in ('APPDIR','APPIMAGE','PYTHONPATH'):env.pop(k,None)
            env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONUNBUFFERED']='1'
            proc=subprocess.Popen(['/home/kali/bridge-pg-v2/.venv/bin/python','-u',
                                    str(ROOT/'diag_server35e.py')],cwd=STAGE35C,
                                   env=env,stdout=logfile,stderr=subprocess.STDOUT,
                                   start_new_session=True,stdin=subprocess.DEVNULL)
            started=time.monotonic()
            while time.monotonic()-started<60:
                if get_ping():break
                if proc.poll() is not None:raise Stop('UVICORN_EARLY_EXIT')
                time.sleep(.4)
            else:raise Stop('UVICORN_STARTUP_60S_TIMEOUT')
            print('STAGE35E_UVICORN_INFO_STARTED_LOOPBACK_18777_PING_OK',flush=True)
            mcp_probe()
    except Exception as exc:
        error=exc
    finally:
        if proc is not None:
            try:
                how=stop_child(proc)
                print('STAGE35E_DIAGNOSTIC_STOP_METHOD_'+how,flush=True)
                if how=='sigkill_after_10s' and error is None:
                    error=Stop('UVICORN_SHUTDOWN_HUNG_CHILD_SIGKILLED')
            except Exception as exc:
                if error is None:error=exc
                else:print('STAGE35E_STOP_CHILD_ERROR_'+type(exc).__name__,flush=True)
    # No tracebacks from untrusted or secret-bearing runtime printed to stdout.
    # Private log remains available locally for targeted review if a stage fails.
    try:
        check_postflight()
    except Exception as exc:
        if error is None:error=exc
        else:print('STAGE35E_POSTCHECK_FAILED_'+type(exc).__name__,flush=True)
    if error is not None:raise error
    print('STAGE35E_READ_ONLY_MCP_DIAG_SUCCESS_OLD_TUNNEL_UNCHANGED',flush=True)


def check_postflight():
    check_context()
    need(not active(UNIT) and port_closed(),'LOCAL_DIAG_PORT_OR_UNIT_STILL_ACTIVE')
    sys.path.insert(0,str(STAGE35D))
    import stage35d
    # Pure checks: active original tunnel, original config, no write permit,
    # secret private and original 35C sources pinned.
    with in_directory(STAGE35D):
        stage35d.live_checks()
    need(ATTEMPT.is_file() and (ATTEMPT.stat().st_mode & 0o077)==0,
         'DIAGNOSTIC_ATTEMPT_MARKER_NOT_PRIVATE')
    need(LOG.is_file() and (LOG.stat().st_mode & 0o077)==0,
         'DIAGNOSTIC_LOG_NOT_PRIVATE')
    print('STAGE35E_POSTCHECK_PORT_CLOSED_ORIGINAL_TUNNEL_ACTIVE_NO_PERMIT',flush=True)


def main():
    try:
        if sys.argv[1:]==['preflight']:pinned_preflight()
        elif sys.argv[1:]==['once']:run_once()
        elif sys.argv[1:]==['postcheck']:check_postflight()
        else:raise Stop('INVALID_ARGUMENTS')
    except Exception as e:
        reason=str(e) if isinstance(e,(Stop,)) else type(e).__name__
        if not isinstance(e,Stop):
            # only safe categorical errors; no secret-bearing exception bodies.
            from stream35e import ProbeError
            if isinstance(e,ProbeError):reason=str(e)
        print('STAGE35E_STOP_'+reason,file=sys.stderr,flush=True)
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
