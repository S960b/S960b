#!/usr/bin/env python3
"""Stage35U live, ephemeral HTTP metadata observer with unconditional timed restore.

Does not change MCP, PostgreSQL, plugin registration, secret, or tunnel identity.
Adds a bounded loopback TCP pass-through and a temporary systemd ExecStart drop-in.
"""
from __future__ import annotations
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import time

H=Path('/home/kali')
ROOT=H/'hermes-pg-stage35u-connector-observer'
UNITS=H/'.config/systemd/user'
ORIG=H/'hermes-pg-stage35jk-cutover/run_managed_tunnel.sh'
KEY=H/'.config/bridge-pg-v2/managed-mcp-local-key35c'
CFG=H/'.config/gigatool/opencode.json'
PERMIT=H/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
SOURCE_HASH='9c41cf36448069031669fcdd502507e10b78541734ffea1e7ace4ee6c10e485a'
CFG_HASH='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
GATE_HASH='695622407566b316482f676da0bf3c5a63a12b3f4b6e6d706c92130a760aaa88'
GATE=H/'hermes-pg-stage35g-managed-runtime/guard35g.py'
TUNNEL='hermes-pg-tunnel-stage35jk.service'
OLD='hermes-pg-tunnel-stage33fd.service'
MCP='hermes-pg-stage35c-static-shadow.service'
OBS='hermes-pg-stage35u-observer.service'
RESTORE='hermes-pg-stage35u-auto-restore.service'
TIMER='hermes-pg-stage35u-auto-restore.timer'
DROP_DIR=UNITS/'hermes-pg-tunnel-stage35jk.service.d'
DROP=DROP_DIR/'93-stage35u-observer.conf'
ATTEMPT=ROOT/'stage35u-attempt.json'
ACTIVE=ROOT/'stage35u-active.json'
FINISHED=ROOT/'stage35u-restored.json'
EVENTS=ROOT/'events.ndjson'
BASELINE=ROOT/'stage35u-baseline.json'
LOCK=ROOT/'stage35u.lock'
RUN=ROOT/'run_stage35u_instrumented.sh'
PROBE=ROOT/'mcp_probe35u.py'
PREV_ROOT=H/'hermes-pg-stage35t-connector-observer'
PREV_DONE=PREV_ROOT/'stage35t-restored.json'
PREV_DROP=DROP_DIR/'92-stage35t-observer.conf'
from proxy35u import ALLOWED_RPC_LABELS, POLICY_LABELS
SUFFIX='STAGE35U_'

OBS_BODY=f'''[Unit]\nDescription=Stage35U temporary loopback MCP metadata observer\n\n[Service]\nType=simple\nExecStart=/usr/bin/python3 {ROOT}/proxy35u.py\nWorkingDirectory={ROOT}\nUMask=0077\nNoNewPrivileges=yes\nRestart=no\n'''
RES_BODY=f'''[Unit]\nDescription=Stage35U automatic restoration of original tunnel target\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 {ROOT}/stage35u.py restore-auto\nWorkingDirectory={ROOT}\nUMask=0077\nNoNewPrivileges=yes\n'''
TIMER_BODY=f'''[Unit]\nDescription=Stage35U mandatory auto-restore after 12 minutes\n\n[Timer]\nOnActiveSec=12min\nAccuracySec=10s\nUnit={RESTORE}\n\n[Install]\nWantedBy=timers.target\n'''
DROP_BODY=f'''[Service]\nExecStart=\nExecStart={RUN}\n'''
UNIT_FILES={OBS:OBS_BODY,RESTORE:RES_BODY,TIMER:TIMER_BODY}

class Stop(Exception):pass

def mark(m):print(SUFFIX+m,flush=True)
def need(ok,code):
    if not ok:raise Stop(code)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def safe_file(path, mode=None, allow_world_read=False):
    try: s=path.lstat()
    except OSError:return False
    return stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and (s.st_mode&(0o022 if allow_world_read else 0o077))==0 and (mode is None or stat.S_IMODE(s.st_mode)==mode)

def safe_dir(path):
    try:s=path.lstat()
    except OSError:return False
    return stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and (s.st_mode&0o077)==0

def cmd(*args,timeout=40):
    return subprocess.run(args,text=True,capture_output=True,timeout=timeout)

def system(*args,timeout=40,allow_fail=False):
    r=cmd('systemctl','--user',*args,timeout=timeout)
    if not allow_fail:need(r.returncode==0,'SYSTEMD_FAILED_'+re.sub('[^A-Za-z0-9]','_', '_'.join(args[:2]))[:55])
    return r

def active(unit):return system('is-active','--quiet',unit,timeout=8,allow_fail=True).returncode==0

def prop(unit,name):return system('show',unit,'-p',name,'--value',timeout=8).stdout.strip()

def port(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.settimeout(.5)
        return s.connect_ex(('127.0.0.1',port))==0

def http_code(p,path):
    c=http.client.HTTPConnection('127.0.0.1',p,timeout=2)
    try:
        c.request('GET',path,headers={'Connection':'close'})
        r=c.getresponse()
        return r.status
    except (OSError,TimeoutError,http.client.HTTPException):return 0
    finally:c.close()

def wait(fn,duration=60):
    end=time.monotonic()+duration
    while time.monotonic()<end:
        if fn():return True
        time.sleep(.5)
    return False

def marker(path,obj):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(obj,f,sort_keys=True,separators=(',',':'))
        f.write('\n');f.flush();os.fsync(f.fileno())

def put_unit(path,body):
    need(not(path.exists() or path.is_symlink()),'UNIT_ALREADY_EXISTS_'+path.name)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w') as f:f.write(body);f.flush();os.fsync(f.fileno())

def ensure_unit(path,body):
    need(safe_file(path) and path.read_text()==body,'UNIT_DRIFT_'+path.name)

def user_check():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
         and Path.home()==H and safe_dir(ROOT),'WRONG_USER_HOME_OR_PACKAGE_ROOT')
    need(safe_dir(UNITS) and not (UNITS.parent.stat().st_mode&0o022),'UNIT_DIRECTORY_NOT_PRIVATE')
    need(safe_file(ORIG,0o700) and sha(ORIG)==SOURCE_HASH,'PINNED_LAUNCHER_DRIFT')
    want=ORIG.read_bytes().replace(b'http://127.0.0.1:18777/mcp',b'http://127.0.0.1:18779/mcp')
    need(want!=ORIG.read_bytes() and RUN.read_bytes()==want and safe_file(RUN,0o700),'OBS_LAUNCHER_NOT_EXACT_PORT_ONLY_COPY')
    need(safe_file(GATE,0o600) and sha(GATE)==GATE_HASH,'PATCHED_GATE_SOURCE_DRIFT')
    need(safe_file(KEY,0o600) and re.fullmatch(rb'[A-Za-z0-9_-]{64}',KEY.read_bytes()) is not None,'KEY_NOT_CANONICAL_OR_UNSAFE')
    need(safe_file(CFG,allow_world_read=True) and sha(CFG)==CFG_HASH and len(json.loads(CFG.read_text()).get('plugin',[]))==7,'SEVEN_PLUGIN_CONFIG_DRIFT')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'WRITE_PERMIT_PRESENT')
    need(active(MCP) and active(TUNNEL) and not active(OLD),'TUNNEL_STATE_DRIFT')
    need(http_code(18778,'/readyz')==200 and http_code(18777,'/ping')==200,'LOCAL_HEALTH_UNAVAILABLE')
    need(str(ORIG) in prop(TUNNEL,'ExecStart'),'ORIGINAL_EXECSTART_NOT_ACTIVE')

def prior35t_restored():
    # Prove former observer has been restored; do not guess or replay it.
    need(safe_dir(PREV_ROOT) and safe_file(PREV_DONE,0o600), 'PRIOR_35T_NOT_RESTORED')
    need(not PREV_DROP.exists() and not PREV_DROP.is_symlink(), 'PRIOR_35T_DROPIN_PRESENT')
    need(not active('hermes-pg-stage35t-observer.service') and
         not active('hermes-pg-stage35t-auto-restore.timer'), 'PRIOR_35T_OBSERVER_OR_TIMER_ACTIVE')
    need(not port(18779),'PRIOR_35T_OBSERVER_STILL_LISTENING')
    mark('PRIOR_35T_ROLLBACK_VERIFIED')


def probe_port_18779():
    # Exercise the proxy with real HTTP and MCP BEFORE changing any tunnel route.
    from mcp_probe35u import run_probe
    need(http_code(18779,'/ping')==200, 'OBSERVER_PING_HTTP_NOT_200')
    need(run_probe(port=18779, secret=KEY.read_bytes().decode('ascii')), 'OBSERVER_MCP_INITIALIZE_LIST_PREFLIGHT_FAILED')
    mark('OBSERVER_REAL_HTTP_PING_AND_MCP_INITIALIZE_TOOLS_LIST_OK')


def count_events():
    if not EVENTS.exists(): return 0
    need(safe_file(EVENTS,0o600) and EVENTS.stat().st_size<65536,'OBSERVER_EVENTS_INVALID')
    return len(EVENTS.read_text().splitlines())


def readiness_metadata():
    # Only fixed-value safe diagnostics; never dump response bodies or headers.
    for label, fn in (
        ('TUNNEL_ACTIVE',lambda:active(TUNNEL)),
        ('MCP_ACTIVE',lambda:active(MCP)),
        ('OBSERVER_ACTIVE',lambda:active(OBS)),
        ('OBSERVER_LISTEN',lambda:port(18779)),
        ('MCP_PING_200',lambda:http_code(18777,'/ping')==200),
        ('OBSERVER_PING_200',lambda:http_code(18779,'/ping')==200),
        ('TUNNEL_READYZ_200',lambda:http_code(18778,'/readyz')==200),
    ):
        try:mark('READINESS_'+label+'_'+('YES' if fn() else 'NO'))
        except Exception:mark('READINESS_'+label+'_UNKNOWN')


def audit_35t_events():
    # Old observer's post-activation window contains the real plugin attempt.
    old=PREV_ROOT/'events.ndjson'
    oldbase=PREV_ROOT/'stage35t-baseline.json'
    if not safe_file(old,0o600) or old.stat().st_size > 65536 or not safe_file(oldbase,0o600):
        mark('OLD_35T_EVENTS_UNAVAILABLE');return
    try:
        rows=[json.loads(line) for line in old.read_text().splitlines()]
        baseline=json.loads(oldbase.read_text()).get('preflight_event_count')
        need(type(baseline) is int and 0<=baseline<=len(rows),'OLD_35T_BASELINE_BAD')
        bad=[x for x in rows[baseline:] if x.get('http')==403 and x.get('route')=='mcp']
        mark('OLD_35T_403_COUNT_'+str(len(bad)))
        for i,x in enumerate(bad[:20],1):
            method=x.get('method')
            rpc=x.get('rpc')
            auth=x.get('auth')
            if method not in ('GET','POST','DELETE','OPTIONS','HEAD','OTHER'):method='OTHER'
            if rpc not in ('initialize','notifications/initialized','tools/list','tools/call','ping','other'):rpc='other'
            if auth not in ('match','missing','duplicate','mismatch'):auth='unknown'
            mark('OLD_35T_DENIAL_%02d_HTTP_%s_RPC_%s_AUTH_%s' % (i,method,rpc.replace('/','_').upper(),auth.upper()))
    except (OSError,ValueError,TypeError,Stop):
        mark('OLD_35T_EVENTS_INVALID_REDACTED')

def begin():
    user_check()
    prior35t_restored()
    audit_35t_events()
    need(not any(p.exists() or p.is_symlink() for p in [ATTEMPT,ACTIVE,FINISHED,BASELINE,DROP]),'ALREADY_ATTEMPTED')
    need(not port(18779) and not active(OBS) and not active(TIMER),'OBSERVER_ALREADY_RUNNING')
    need(not DROP_DIR.exists() and not DROP_DIR.is_symlink(),'TUNNEL_HAS_UNEXPECTED_DROPIN_DIR')
    for name in UNIT_FILES: need(not(UNITS/name).exists() and not(UNITS/name).is_symlink(),'UNIT_NAME_ALREADY_EXISTS')
    before=prop(TUNNEL,'MainPID')
    need(before.isdecimal() and int(before)>0,'BAD_ORIGINAL_PID')
    marker(ATTEMPT,{'stage':'35U','attempted_utc':datetime.now(timezone.utc).isoformat(),'oldpid':int(before)})
    mark('ONE_SHOT_MARKER_CREATED_ORIGINAL_CONFIG_PINNED')
    try:
        for name,body in UNIT_FILES.items():put_unit(UNITS/name,body)
        system('daemon-reload',timeout=20)
        system('start',OBS,timeout=20)
        need(wait(lambda:active(OBS) and port(18779),15),'OBSERVER_NOT_LISTENING')
        mark('PRIVATE_TCP_OBSERVER_READY_18779_NO_BODY_LOGGING')
        probe_port_18779()
        preflight_event_count=count_events()
        marker(BASELINE,{'preflight_event_count':preflight_event_count})
        DROP_DIR.mkdir(mode=0o700)
        need(safe_dir(DROP_DIR),'DROPIN_DIR_UNSAFE')
        put_unit(DROP,DROP_BODY)
        system('daemon-reload',timeout=20)
        # Arm automatic restore BEFORE redirecting any runtime traffic.
        system('start',TIMER,timeout=15)
        need(active(TIMER),'AUTO_RESTORE_TIMER_NOT_ARMED')
        mark('AUTOMATIC_RESTORE_ARMED_12_MINUTES')
        system('restart',TUNNEL,timeout=50)
        need(wait(lambda: active(TUNNEL) and active(OBS) and active(MCP) and not active(OLD)
                  and http_code(18778,'/readyz')==200 and prop(TUNNEL,'MainPID')!=before,115),
             'TUNNEL_NOT_READY_AFTER_REDIRECT')
        need(safe_file(KEY,0o600) and sha(CFG)==CFG_HASH and not PERMIT.exists(),'STATE_DRIFT_AFTER_REDIRECT')
        marker(ACTIVE,{'stage':'35U','observer_active':True,'original_route':18777,'temporary_route':18779,
                       'preflight_event_count':preflight_event_count})
        readiness_metadata()
        mark('OBSERVATION_ACTIVE_MCP_ORIGINAL_18777_NEW_TUNNEL_VIA_18779')
        mark('NOW_ATTEMPT_CHATGPT_PLUGIN_CREATION_ONCE_THEN_COLLECT')
    except BaseException:
        mark('ACTIVATION_FAILED_RESTORING_ORIGINAL_ROUTE')
        readiness_metadata()
        try:restore(automatic=False,from_failed=True)
        except Exception:mark('CRITICAL_AUTORESTORE_FAILED_MANUAL_REVIEW_REQUIRED')
        try:summary()
        except Exception:mark('ACTIVATION_DIAGNOSTICS_UNAVAILABLE')
        raise


def restore(*, automatic=False,from_failed=False):
    need(ATTEMPT.exists() and safe_file(ATTEMPT),'NO_ATTEMPT_OR_UNSAFE')
    if FINISHED.exists():
        need(safe_file(FINISHED),'RESTORE_MARKER_UNSAFE')
        mark('ORIGINAL_ROUTE_ALREADY_RESTORED');return
    # Avoid restarting a healthy production tunnel if activation failed before a redirect.
    had_drop=DROP.exists() or DROP.is_symlink()
    # Ensure no unrecognized systemd code is removed.
    if had_drop:
        ensure_unit(DROP,DROP_BODY)
        DROP.unlink()
    if DROP_DIR.exists():
        need(safe_dir(DROP_DIR) and not any(DROP_DIR.iterdir()),'DROPIN_DIR_UNEXPECTED_CONTENT')
        DROP_DIR.rmdir()
    system('daemon-reload',timeout=20)
    for name,body in UNIT_FILES.items():
        if (UNITS/name).exists():ensure_unit(UNITS/name,body)
    # Stop timer except if currently executing itself (stopping timer is safe).
    system('stop',TIMER,timeout=15,allow_fail=True)
    # Restart same tunnel ID only if a redirect could have affected its runtime.
    # A failed observer preflight must not bounce the unaffected production tunnel.
    if had_drop or str(ORIG) not in prop(TUNNEL,'ExecStart'):
        system('restart',TUNNEL,timeout=50)
        mark('ORIGINAL_TUNNEL_RESTARTED_AFTER_REDIRECT')
    else:
        mark('ORIGINAL_TUNNEL_PRESERVED_NO_REDIRECT_STARTED')
    need(wait(lambda:active(TUNNEL) and active(MCP) and not active(OLD)
              and str(ORIG) in prop(TUNNEL,'ExecStart')
              and http_code(18778,'/readyz')==200,70),'RESTORE_ORIGINAL_TUNNEL_FAILED')
    system('stop',OBS,timeout=20,allow_fail=True)
    need(wait(lambda:not port(18779),12),'OBSERVER_PORT_STILL_OPEN')
    marker(FINISHED,{'stage':'35U','restored_utc':datetime.now(timezone.utc).isoformat(),
                     'automatic':automatic,'from_failed_activation':from_failed})
    mark('RESTORED_ORIGINAL_18777_TARGET_TUNNEL_ACTIVE_NO_PLUGIN_DB_MUTATION')
    mark('AUTOMATIC_RESTORE' if automatic else 'MANUAL_RESTORE')


def summary():
    lines=[]
    if EVENTS.exists():
        need(safe_file(EVENTS,0o600),'EVENT_FILE_NOT_PRIVATE')
        for s in EVENTS.read_text().splitlines()[:400]:
            try:
                j=json.loads(s)
                need(isinstance(j,dict) and j.get('auth') in ('match','missing','duplicate','mismatch')
                     and j.get('rpc') in ALLOWED_RPC_LABELS
                     and j.get('route') in ('mcp','ping','other')
                     and j.get('method') in ('GET','POST','DELETE','OPTIONS','HEAD','OTHER')
                     and j.get('policy') in POLICY_LABELS
                     and type(j.get('http')) is int,'OBS_EVENT_UNEXPECTED_SCHEMA')
                lines.append(j)
            except (ValueError,TypeError):
                mark('EVENT_PARSING_ERROR_REDACTED')
    mark('OBSERVED_ALL_EVENTS_'+str(len(lines)))
    if BASELINE.exists() and safe_file(BASELINE,0o600):
        baseline=json.loads(BASELINE.read_text()).get('preflight_event_count',0)
        need(type(baseline) is int and 0<=baseline<=len(lines),'BAD_PREFLIGHT_BASELINE')
    else:baseline=0
    observations=lines[baseline:]
    mark('OBSERVED_AFTER_ACTIVE_'+str(len(observations)))
    mcp_events=[x for x in observations if x['route']=='mcp']
    mark('MCP_REQUESTS_'+str(len(mcp_events)))
    mark('MCP_AUTH_MATCH_'+str(sum(1 for x in mcp_events if x['auth']=='match')))
    mark('MCP_HTTP_403_'+str(sum(1 for x in mcp_events if x['http']==403)))
    mark('MCP_HTTP_200_'+str(sum(1 for x in mcp_events if x['http']==200)))
    mark('MCP_HTTP_202_'+str(sum(1 for x in mcp_events if x['http']==202)))
    for i,x in enumerate([x for x in mcp_events if x['http']==403][:20],1):
        # Fixed labels from the curated schema, never print a body, header or method supplied verbatim.
        mark('DENIAL_403_%02d_HTTP_%s_RPC_%s_PREDICTION_%s_AUTH_%s' % (i, x['method'],
             x['rpc'].replace('/','_').upper(),x['policy'].upper(),x['auth'].upper()))
    if any(x['http']==403 and x['policy'] in ('deny_rpc_method','deny_http_method','deny_tool')
           for x in mcp_events):
        mark('GATE_POLICY_CANDIDATE_STATIC_PREDICTION_REQUIRES_REVIEW_BEFORE_PATCH')
    elif any(x['http']==403 for x in mcp_events):
        mark('403_WITH_NO_LOCAL_POLICY_EXPLANATION_REVIEW_INNER_ASGI_AND_GATE')
    if not mcp_events:mark('NO_REMOTE_MCP_REQUESTS_OBSERVED')
    # Additional, coarse control-plane evidence: no message bodies, paths,
    # URLs, request IDs or headers from journal are ever printed or stored.
    if safe_file(ATTEMPT):
        attempt=json.loads(ATTEMPT.read_text())
        since=attempt.get('attempted_utc','')
        if re.fullmatch(r'2026-\d\d-\d\dT[\d:.]+\+00:00',since):
            r=cmd('journalctl','--user','-u',TUNNEL,'--since',
                  datetime.fromisoformat(since).strftime('%Y-%m-%d %H:%M:%S UTC'),
                  '--no-pager','-o','cat',timeout=15)
            if r.returncode==0:
                rows=r.stdout.splitlines()
                mark('TUNNEL_JOURNAL_LINES_SINCE_ACTIVATION_'+str(len(rows)))
                mark('TUNNEL_MAIN_HTTP_403_'+str(sum(1 for x in rows if
                     'main' in x.lower() and re.search(r'(?<!\d)403(?!\d)',x))))
                mark('TUNNEL_UPSTREAM_ERROR_'+str(sum(1 for x in rows if
                     re.search(r'upstream.{0,25}error',x,re.I))))
                mark('TUNNEL_UNSUPPORTED_CHANNEL_'+str(sum(1 for x in rows if
                     re.search(r'unsupported.{0,25}channel',x,re.I))))
            else:
                mark('TUNNEL_JOURNAL_UNAVAILABLE')
    mark('FIRST_REQUEST_PER_TCP_CONNECTION_ONLY_NO_RAW_HEADERS_OR_BODIES')


def run(argv):
    need(len(argv)==1 and argv[0] in ('activate','collect','restore-auto','restore'),'USAGE_ACTIVATE_COLLECT_RESTORE')
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and safe_dir(ROOT),'USER_OR_ROOT_UNSAFE')
    fd=os.open(LOCK,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'r+'):
        fcntl.flock(fd,fcntl.LOCK_EX)
        if argv[0]=='activate':begin()
        elif argv[0]=='collect':
            try:restore(automatic=False)
            finally:summary()
        elif argv[0]=='restore-auto':
            restore(automatic=True)
        else:restore(automatic=False)
        return 0

if __name__=='__main__':
    try:sys.exit(run(sys.argv[1:]))
    except (Stop,subprocess.TimeoutExpired,OSError,ValueError,RuntimeError) as e:
        mark('STOP_'+(str(e) if isinstance(e,Stop) else type(e).__name__))
        sys.exit(2)
