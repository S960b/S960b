#!/usr/bin/env python3
"""Stage 35J/K: safe local dev-proxy reproof and guarded single-tunnel cutover.

J: no external control plane, verifies real client's file-backed header and PG reads.
K: exact, single active production tunnel ID; automatic rollback on startup failure.
No database writes or plugin edits, no automatic arming of managed dispatch.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import stat
import subprocess
import sys
import time

HOME = Path('/home/kali')
ROOT = HOME / 'hermes-pg-stage35jk-cutover'
G = HOME / 'hermes-pg-stage35g-managed-runtime'
HI = HOME / 'hermes-pg-stage35hi-local-e2e'
KEY = HOME / '.config/bridge-pg-v2/managed-mcp-local-key35c'
PERMIT = HOME / '.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
RUNTIME_KEY = HOME / '.config/hermes-secure-tunnel-canary/runtime.key'
BIN = HOME / '.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client'
CFG = HOME / '.config/gigatool/opencode.json'
CFG_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
OLD_SCRIPT = HOME / 'hermes-pg-stage33fd-tunnel-cutover/run_stage33fd_tunnel.sh'
OLD_SCRIPT_SHA = '8dc8c432edc5cd01bd57117c1c2abc0b3f53bb7a9c8695e21fe31435df91c029'
HI_SOURCE_SHA = 'e40767750e7090be152bbb04374008175624ea2aec0cceb2986ede9e4e7d22a5'
HI_CAND_SHA = '7441a500c5425d9c374fa9802ffa56a05cae24b79526c4bc0164c0c10ac264fb'
G_SOURCE_SHA = 'ee62f7a6d0ed1ed301c18da2afd05579f13344827f136babb72c8bed7e07bdc5'
OLD = 'hermes-pg-tunnel-stage33fd.service'
NEW = 'hermes-pg-tunnel-stage35jk.service'
SHADOW = 'hermes-pg-stage35c-static-shadow.service'
PG = 'bridge-pg-v2-ssh-shadow.service'
BASE = 'hermes-pg-v2-stage33fb-cg.service'
UNITDIR = HOME / '.config/systemd/user'
NEW_UNIT = UNITDIR / NEW
DEV_MARK = ROOT / 'stage35j-attempt.json'
DEV_PASS = ROOT / 'stage35j-passed.json'
CUT_MARK = ROOT / 'stage35k-cutover-attempt.json'
CUT_PASS = ROOT / 'stage35k-active.json'
ROLL_MARK = ROOT / 'stage35k-rolled-back.json'
RUN = ROOT / 'run_managed_tunnel.sh'
SRC_FILES = {'stage35jk.py', 'test_stage35jk.py', '180_install_verify_and_cutover_once.sh',
             '181_verify_only.sh', '182_rollback_if_needed.sh', 'run_managed_tunnel.sh',
             'README.md', 'SHA256SUMS'}
EXPECTED_TOOLS = {'managed_health','managed_create_status','managed_list','managed_status',
                  'managed_read','managed_send_status','managed_delivery_status','managed_create',
                  'managed_set_dispatch','managed_send_task','managed_ack_report'}

UNIT_TEXT = f'''[Unit]
Description=OpenAI Secure MCP Tunnel - read-only managed PostgreSQL bridge stage35JK
Wants={SHADOW}
After={SHADOW} network-online.target

[Service]
Type=simple
ExecStart=/home/kali/hermes-pg-stage35jk-cutover/run_managed_tunnel.sh
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
UMask=0077
'''

class Stop(Exception):
    pass

def need(value, code):
    if not value:
        raise Stop(code)

def mark(code):
    print('STAGE35JK_' + code, flush=True)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def private_file(path, expected_sha=None, *, executable=False, allow_world_read=False):
    try:
        st = path.lstat()
    except OSError:
        return False
    if not (stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and
            st.st_nlink == 1 and not (st.st_mode & (0o022 if allow_world_read else 0o077))):
        return False
    if executable and not (st.st_mode & stat.S_IXUSR):
        return False
    return expected_sha is None or sha(path) == expected_sha

def private_marker(path, obj):
    data = (json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())

def read_marker(path):
    need(private_file(path),'MARKER_UNSAFE_OR_MISSING_'+path.name)
    value = json.loads(path.read_text('utf8'))
    need(isinstance(value,dict),'MARKER_INVALID_'+path.name)
    return value

def cmd(*argv, timeout=15):
    return subprocess.run(argv, timeout=timeout, capture_output=True, text=True)

def user_systemctl(*argv, timeout=20):
    r=cmd('systemctl','--user',*argv,timeout=timeout)
    need(r.returncode==0,'SYSTEMCTL_'+re.sub('[^A-Za-z0-9_]','_', '_'.join(argv))[:50]+'_FAILED')
    return r.stdout.strip()

def active(unit):
    return cmd('systemctl','--user','is-active','--quiet',unit,timeout=6).returncode==0

def show(unit, field):
    return user_systemctl('show',unit,'--property='+field,'--value')

def port_closed(port):
    with socket.socket() as s:
        s.settimeout(.6)
        return s.connect_ex(('127.0.0.1',port))!=0

def http_check(port, path, *, needle=None, timeout=3):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    r=None
    try:
        c.request('GET',path,headers={'Connection':'close'})
        r=c.getresponse()
        body=r.read(512) if needle is not None else b''
        return r.status==200 and (needle is None or needle in body)
    except (OSError,TimeoutError):
        return False
    finally:
        if r is not None:
            r.close()
        c.close()

def wait_for(fn, seconds, interval=.4):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if fn():return True
        time.sleep(interval)
    return False

def plugin_unchanged():
    need(private_file(CFG,CFG_SHA,allow_world_read=True),'SEVEN_PLUGIN_CONFIG_DRIFT')
    cfg=json.loads(CFG.read_text('utf8'))
    need(isinstance(cfg.get('plugin'),list) and len(cfg['plugin'])==7,'PLUGIN_COUNT_NOT_SEVEN')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'WRITE_PERMIT_PRESENT')

def load_35hi():
    sys.path.insert(0,str(HI))
    import proxy35hi
    return proxy35hi

def load_35g():
    sys.path.insert(0,str(G))
    import stage35g
    return stage35g

def static_preflight():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
         and Path.home()==HOME and Path.cwd().resolve()==ROOT,'WRONG_USER_OR_CWD')
    need(ROOT.is_dir() and not ROOT.is_symlink() and
         ROOT.stat().st_uid==os.getuid() and (ROOT.stat().st_mode&0o077)==0,
         'ROOT_NOT_PRIVATE')
    mutable={DEV_MARK.name,DEV_PASS.name,CUT_MARK.name,CUT_PASS.name,ROLL_MARK.name,
             'dev-proxy-private.log'}
    present={p.name for p in ROOT.iterdir()}
    need(present<=SRC_FILES|mutable and SRC_FILES<=present,
         'PACKAGE_DIRECTORY_UNEXPECTED')
    for filename in present & mutable:
        need(private_file(ROOT/filename),'UNSAFE_PRIVATE_RUNTIME_FILE_'+filename)
    for name in SRC_FILES:
        need(private_file(ROOT/name,executable=(name=='run_managed_tunnel.sh')),
             'PACKAGE_SOURCE_UNSAFE_'+name)
    need(private_file(G/'stage35g.py',G_SOURCE_SHA),'35G_SOURCE_DRIFT')
    need(private_file(HI/'proxy35hi.py',HI_SOURCE_SHA),'35HI_SOURCE_DRIFT')
    need(private_file(HI/'run_stage35hi_candidate_NOT_ACTIVATED.sh',HI_CAND_SHA),
         '35HI_CANDIDATE_DRIFT')
    need(private_file(OLD_SCRIPT,OLD_SCRIPT_SHA,executable=True),'OLD_TUNNEL_SCRIPT_DRIFT')
    need(private_file(KEY) and len(KEY.read_bytes().strip())==64,'35C_LOCAL_SECRET_UNSAFE')
    need(private_file(RUNTIME_KEY) and bool(RUNTIME_KEY.read_bytes().strip()),
         'TUNNEL_RUNTIME_KEY_UNSAFE')
    need(BIN.is_file() and not BIN.is_symlink() and os.access(BIN,os.X_OK),
         'OFFICIAL_TUNNEL_CLIENT_MISSING')
    plugin_unchanged()
    need(active(BASE) and active(PG),'BASELINE_PG_COMPONENTS_INACTIVE')
    need(not active('hermes-pg-stage35a-oauth-shadow.service'),'OAUTH_SHADOW_ACTIVE')
    need(read_marker(HI/'stage35hi-local-proxy-attempt.json').get('attempted') is True,
         '35HI_PREVIOUS_ATTEMPT_NOT_PRESENT')
    need(not (HI/'stage35hi-local-proxy-proven.json').exists(),
         '35HI_UNEXPECTED_PASS_MARKER')
    return True

def preflight():
    static_preflight()
    need(not any(p.exists() or p.is_symlink() for p in
                 (DEV_MARK,DEV_PASS,CUT_MARK,CUT_PASS,ROLL_MARK)),
         'STAGE35JK_ALREADY_ATTEMPTED')
    need(active(OLD),'ORIGINAL_TUNNEL_INACTIVE')
    need(not active(NEW) and not NEW_UNIT.exists() and not NEW_UNIT.is_symlink(),
         'NEW_TUNNEL_UNIT_ALREADY_PRESENT')
    need(not active(SHADOW) and port_closed(18777) and port_closed(18778),
         'MANAGED_SHADOW_OR_NEW_PORT_OCCUPIED')
    need(http_check(18773,'/ping'),'OLD_BRIDGE_18773_PING_FAILED')
    g=load_35g()
    old_cwd=Path.cwd()
    try:
        os.chdir(G)
        g.check_original()
        g.check_dropin()
    finally:
        os.chdir(old_cwd)
    # Old unit should still execute the known, pinned legacy launcher.
    need(str(OLD_SCRIPT) in show(OLD,'ExecStart'), 'OLD_UNIT_EXECSTART_DRIFT')
    need('run_managed_tunnel.sh' in RUN.read_text('utf8') or
         'MCP_SERVER_URL=' in RUN.read_text('utf8'), 'NEW_LAUNCHER_MISSING_TARGET')
    mark('READONLY_PREFLIGHT_GATES_PASSED_OLD_TUNNEL_ACTIVE')

def cleanup_status(port,path,session,proto):
    """Best-effort termination: diagnostic only. Never conflate with RPC success.

    35HI already accepted 200/202/204/404/405, so its failure was not proven
    to be a wrong expected status. Record real status or network exception.
    """
    if not session:
        return 'NO_SESSION'
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    r=None
    try:
        c.request('DELETE',path,headers={
            'Accept':'application/json, text/event-stream',
            'MCP-Protocol-Version':proto,
            'Mcp-Session-Id':session,
            'Connection':'close'})
        r=c.getresponse()
        return 'HTTP_'+str(r.status)
    except (OSError,TimeoutError,http.client.HTTPException) as e:
        return 'NETWORK_'+type(e).__name__
    finally:
        if r is not None:
            try:r.close()
            except OSError:pass
        c.close()

def local_core(port,path):
    """Actual tunnel-client local dev proxy, no secret in outer HTTP requests."""
    hi=load_35hi()
    from stream35g import tool_payload
    session=None
    proto='2025-06-18'
    core_ok=False
    try:
        result,headers=hi.rpc_request(port,path,'initialize',
            {'protocolVersion':proto,'capabilities':{},
             'clientInfo':{'name':'stage35jk-verify','version':'1.0'}},rid=181)
        need(isinstance(result,dict) and isinstance(result.get('protocolVersion'),str),
             'DEV_PROXY_INITIALIZE_MALFORMED')
        proto=result['protocolVersion']
        session=headers['session']
        need(isinstance(session,str) and 0<len(session)<=256,'SESSION_MISSING')
        mark('LOCAL_REAL_CLIENT_FILE_HEADER_INITIALIZE_OK_'+
             ('SSE' if headers['content_type']=='text/event-stream' else 'JSON'))
        hi.rpc_request(port,path,'notifications/initialized',
                       session=session,protocol=proto,notify=True)
        result,_=hi.rpc_request(port,path,'tools/list',session=session,protocol=proto,rid=182)
        need(isinstance(result,dict) and
             {t.get('name') for t in result.get('tools',[]) if isinstance(t,dict)}==EXPECTED_TOOLS,
             'DEV_PROXY_11_TOOL_LIST_DRIFT')
        mark('LOCAL_REAL_CLIENT_ALL_11_TOOLS_READONLY_LIST_OK')
        for name,args,rid in (('managed_health',{},183),
                              ('managed_list',{'offset':0,'limit':10},184)):
            item,_=hi.rpc_request(port,path,'tools/call',{'name':name,'arguments':args},
                                  session=session,protocol=proto,rid=rid)
            payload=tool_payload(item)
            need(payload.get('ok') is True,'READONLY_PG_TOOL_FAILED_'+name)
            d=payload.get('result')
            if name=='managed_health':
                need(isinstance(d,dict) and d.get('role')=='bridge_chatgpt' and
                     d.get('connected') is True,'WRONG_PG_ROLE_OR_DISCONNECTED')
            else:
                need(isinstance(d,list) and len(d)==1 and
                     d[0].get('task_id')=='task_bddaadef89b7458fb7f8cf437dc0f023' and
                     d[0].get('dispatch_enabled') is False,'PILOT_DRIFT')
        mark('LOCAL_REAL_CLIENT_PG_READS_ROLE_BOUND_PILOT_DISABLED')
        core_ok=True
    finally:
        outcome=cleanup_status(port,path,session,proto)
        mark('LOCAL_SESSION_DELETE_'+outcome)
        if outcome not in ('HTTP_200','HTTP_202','HTTP_204','HTTP_404','HTTP_405'):
            mark('LOCAL_SESSION_DELETE_UNCONFIRMED_NONFATAL_NEEDS_REVIEW')
    need(core_ok,'DEV_PROXY_CORE_INCOMPLETE')
    return outcome

def local_once():
    preflight()
    private_marker(DEV_MARK,{'stage':'35J','attempted':True,'production_tunnel_switched':False})
    mark('LOCAL_ONE_SHOT_MARKED_NO_CUTOVER_YET')
    g=load_35g()
    hi=load_35hi()
    proxy=None
    shadow_started=False
    failure=None
    proxy_stop='not_started';shadow_stop='not_started';del_result='not_attempted'
    try:
        user_systemctl('start',SHADOW,timeout=30)
        shadow_started=True
        need(wait_for(lambda:http_check(18777,'/ping',needle=b'stage35c-static-shadow'),60),
             'SHADOW_STARTUP_60S_TIMEOUT')
        mark('MANAGED_SHADOW_LISTENING_LOOPBACK_ONLY')
        env={'PATH':'/usr/bin:/bin','HOME':'/home/kali','LANG':'C','LC_ALL':'C',
             'MCP_EXTRA_HEADERS':'X-Bridge-Local-Key: file:'+str(KEY),
             'MCP_OAUTH_DISCOVERY':'disabled',
             'HTTP_PROXY':'','HTTPS_PROXY':'','ALL_PROXY':'',
             'NO_PROXY':'127.0.0.1,localhost'}
        args=[str(BIN),'dev','proxy','--mcp-server-url','http://127.0.0.1:18777/mcp',
              '--listen','127.0.0.1:0','--print-json']
        logpath=ROOT/'dev-proxy-private.log'
        fd=os.open(logpath,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as logfile:
            proxy=subprocess.Popen(args,env=env,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=logfile,start_new_session=True,bufsize=0)
            port,path=hi.wait_proxy_url(proxy)
            mark('LOCAL_DEV_PROXY_LISTENING_ONLY_LOOPBACK')
            del_result=local_core(port,path)
            need(proxy.poll() is None,'LOCAL_DEV_PROXY_EXITED_EARLY')
    except Exception as e:
        failure=e
    finally:
        if proxy is not None:
            try:proxy_stop=hi.proc_terminated(proxy,seconds=12)
            except Exception as e:
                proxy_stop='termination_failed'
                if failure is None:failure=e
            if proxy.stdout is not None:
                proxy.stdout.close()
        mark('LOCAL_PROXY_SHUTDOWN_'+proxy_stop)
        if shadow_started or not port_closed(18777):
            try:shadow_stop=g.stop_and_wait()
            except Exception as e:
                shadow_stop='termination_failed'
                if failure is None:failure=e
        mark('LOCAL_SHADOW_SHUTDOWN_'+shadow_stop)
    if failure is None:
        need(proxy_stop=='sigterm' and shadow_stop=='sigterm' and
             port_closed(18777) and active(OLD),
             'LOCAL_CLEAN_SHUTDOWN_OR_OLD_TUNNEL_FAILED')
        plugin_unchanged()
        private_marker(DEV_PASS,{'stage':'35J','core_e2e_proven':True,
                                 'cleanup_result':del_result,'cutover':False})
        mark('LOCAL_E2E_PROVEN_OLD_TUNNEL_UNCHANGED')
    else:
        raise failure

def verify_unit():
    need(private_file(NEW_UNIT) and NEW_UNIT.read_text('utf8')==UNIT_TEXT,
         'NEW_SYSTEMD_UNIT_DRIFT')
    need(str(RUN) in show(NEW,'ExecStart'),'NEW_TUNNEL_EXECSTART_DRIFT')
    need(show(NEW,'UnitFileState') in ('disabled','static'),'NEW_TUNNEL_UNEXPECTEDLY_ENABLED')

def graceful_stop(unit):
    r=cmd('systemctl','--user','stop','--no-block',unit,timeout=10)
    if r.returncode!=0:
        mark('STOP_REQUEST_RETURNED_NONZERO_'+unit.replace('.service','').replace('-','_'))
    return wait_for(lambda:not active(unit),20)

def do_rollback(reason, *, record=True):
    """Best effort, serialized: never start old until new has fully stopped."""
    mark('ROLLBACK_TRIGGER_'+re.sub('[^A-Za-z0-9_]','_',reason)[:80])
    new_stopped=graceful_stop(NEW)
    new_closed=wait_for(lambda:port_closed(18778),10)
    if not (new_stopped and new_closed):
        mark('ROLLBACK_CRITICAL_NEW_TUNNEL_NOT_STOPPED_OLD_NOT_STARTED')
        return False
    if active(OLD):
        old_ok=wait_for(lambda:active(OLD) and http_check(18774,'/readyz'),70)
    else:
        r=cmd('systemctl','--user','start',OLD,timeout=20)
        old_ok=r.returncode==0 and wait_for(
            lambda:active(OLD) and http_check(18774,'/readyz'),70)
    if not old_ok:
        mark('ROLLBACK_CRITICAL_OLD_TUNNEL_NOT_RESTORED')
        # Leave shadow up for forensic inspection rather than masking outage.
        return False
    mark('ROLLBACK_OLD_TUNNEL_18774_READY_SINGLE_RUNTIME')
    shadow_ok=graceful_stop(SHADOW) and wait_for(lambda:port_closed(18777),20)
    if not shadow_ok:
        mark('ROLLBACK_WARNING_MANAGED_SHADOW_NOT_STOPPED')
    # Never remove/alter the old tunnel or seven-plugin config.
    plugin_unchanged()
    if record and not ROLL_MARK.exists():
        private_marker(ROLL_MARK,{'stage':'35K','rolled_back':True,'old_restored':True})
    mark('ROLLBACK_COMPLETED_OLD_18773_RESTORED_NO_PLUGIN_EDITS')
    return shadow_ok

def install_new_unit():
    need(not NEW_UNIT.exists() and not NEW_UNIT.is_symlink(),'NEW_UNIT_ALREADY_EXISTS')
    need(UNITDIR.is_dir() and not UNITDIR.is_symlink() and
         UNITDIR.stat().st_uid==os.geteuid() and
         not (UNITDIR.stat().st_mode&0o022), 'SYSTEMD_USER_UNITDIR_UNSAFE')
    fd=os.open(NEW_UNIT,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w',encoding='utf8') as f:
        f.write(UNIT_TEXT)
        f.flush();os.fsync(f.fileno())
    user_systemctl('daemon-reload')
    verify_unit()
    mark('CUTOVER_NEW_SYSTEMD_UNIT_CREATED_BUT_NOT_ENABLED')

def cutover_once():
    static_preflight()
    need(read_marker(DEV_PASS).get('core_e2e_proven') is True,'STAGE35J_NOT_PROVEN')
    need(not any(p.exists() or p.is_symlink() for p in (CUT_MARK,CUT_PASS,ROLL_MARK)),
         'CUTOVER_ALREADY_ATTEMPTED')
    need(active(OLD) and not active(NEW) and not active(SHADOW) and
         port_closed(18777) and port_closed(18778),'CUTOVER_START_STATE_DRIFT')
    need(not NEW_UNIT.exists() and not NEW_UNIT.is_symlink(), 'NEW_UNIT_ALREADY_EXISTS')
    need(http_check(18773,'/ping') and http_check(18774,'/readyz'),
         'ORIGINAL_TUNNEL_HEALTH_CHECK_FAILED')
    plugin_unchanged()
    private_marker(CUT_MARK,{'stage':'35K','attempted':True,'old_service':OLD,
                             'new_service':NEW,'rollback_available':True})
    mark('CUTOVER_ONE_SHOT_MARKED_AUTOMATIC_ROLLBACK_ARMED')
    error=None
    try:
        install_new_unit()
        user_systemctl('start',SHADOW,timeout=25)
        need(wait_for(lambda:http_check(18777,'/ping',needle=b'stage35c-static-shadow'),60),
             'PRODUCTION_SHADOW_NOT_READY')
        mark('CUTOVER_MANAGED_MCP_18777_READY_BEFORE_STOP_OLD')
        # From here forward any failed assertion MUST attempt to restore old.
        user_systemctl('stop',OLD,timeout=25)
        need(wait_for(lambda:not active(OLD) and port_closed(18774),30),
             'OLD_TUNNEL_FAILED_TO_STOP')
        mark('CUTOVER_OLD_TUNNEL_STOPPED_NO_DUPLICATE_ID')
        # Existing 33FD cutover similarly waited for the old control-plane session.
        time.sleep(2)
        user_systemctl('start',NEW,timeout=25)
        need(wait_for(lambda:active(NEW) and http_check(18778,'/readyz'),90,interval=1),
             'NEW_TUNNEL_18778_NOT_READY')
        verify_unit()
        need(not active(OLD) and port_closed(18774),'DUPLICATE_TUNNEL_RUNTIME_DETECTED')
        need(active(SHADOW) and http_check(18777,'/ping',needle=b'stage35c-static-shadow'),
             'MANAGED_SHADOW_NOT_HEALTHY_POSTCUTOVER')
        plugin_unchanged()
        mark('CUTOVER_NEW_SINGLE_TUNNEL_READY_18778_OLD_TUNNEL_INACTIVE')
        # Proof markers reflect only local health; remote ChatGPT MCP call still pending.
        private_marker(CUT_PASS,{'stage':'35K','new_tunnel_active':True,
              'old_tunnel_inactive':True,'local_readonly_e2e_proven':True,
              'remote_plugin_verification_pending':True,'dispatch_armed':False})
        mark('CUTOVER_COMPLETE_LOCAL_HEALTH_OK_REMOTE_PLUGIN_CHECK_PENDING')
        return
    except BaseException as e:
        error=e
    # If stop OLD itself failed, it might still be active. Never start another.
    try:
        restored=do_rollback('CUTOVER_FAILURE',record=True)
        if restored:
            mark('CUTOVER_FAILED_AUTO_ROLLBACK_SUCCEEDED_OLD_TUNNEL_ACTIVE')
        else:
            mark('CUTOVER_FAILED_AUTO_ROLLBACK_INCOMPLETE_MANUAL_REVIEW_REQUIRED')
    except Exception as e:
        mark('CUTOVER_CRITICAL_ROLLBACK_EXCEPTION_'+type(e).__name__)
    raise error

def verify_only():
    static_preflight()
    if CUT_PASS.exists() and not ROLL_MARK.exists():
        need(active(NEW) and not active(OLD) and active(SHADOW) and
             http_check(18778,'/readyz') and
             http_check(18777,'/ping',needle=b'stage35c-static-shadow'),
             'ACTIVE_CUTOVER_HEALTH_FAILED')
        plugin_unchanged()
        mark('VERIFY_ACTIVE_NEW_TUNNEL_18778_AND_MANAGED_MCP_18777_OK')
        mark('VERIFY_REMOTE_PLUGIN_TEST_STILL_REQUIRED')
    elif ROLL_MARK.exists():
        need(active(OLD) and not active(NEW) and http_check(18774,'/readyz'),
             'ROLLED_BACK_TUNNEL_HEALTH_FAILED')
        mark('VERIFY_OLD_TUNNEL_RESTORED')
    else:
        need(active(OLD) and not active(NEW),'TUNNEL_UNEXPECTED_STATE')
        mark('VERIFY_LEGACY_TUNNEL_STILL_ACTIVE')

def rollback_manual():
    static_preflight()
    need(CUT_MARK.exists(),'NO_CUTOVER_ATTEMPT_TO_ROLLBACK')
    if ROLL_MARK.exists():
        need(active(OLD) and not active(NEW),'ROLLBACK_MARKER_STATE_DRIFT')
        mark('ROLLBACK_ALREADY_COMPLETED_OLD_ACTIVE')
        return
    need(do_rollback('OWNER_REQUESTED_ROLLBACK'),
         'MANUAL_ROLLBACK_INCOMPLETE_NEEDS_OPERATOR')
    mark('OWNER_ROLLBACK_SUCCESS')

def abort_signal(sig, _frame):
    raise Stop('INTERRUPTED_SIGNAL_'+str(sig))

def main():
    try:
        if sys.argv[1:] in (['local-once'],['cutover-once']):
            signal.signal(signal.SIGTERM,abort_signal)
        if sys.argv[1:]==['preflight']:
            preflight()
        elif sys.argv[1:]==['local-once']:
            local_once()
        elif sys.argv[1:]==['cutover-once']:
            cutover_once()
        elif sys.argv[1:]==['verify']:
            verify_only()
        elif sys.argv[1:]==['rollback']:
            rollback_manual()
        else:
            raise Stop('BAD_ARGUMENTS')
        return 0
    except BaseException as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE35JK_STOP_'+re.sub('[^A-Za-z0-9_]','_',code)[:100],
              file=sys.stderr,flush=True)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
