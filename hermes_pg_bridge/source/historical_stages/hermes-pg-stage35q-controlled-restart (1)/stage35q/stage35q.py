#!/usr/bin/env python3
"""Stage 35Q: bounded one-shot new-tunnel restart and read-only follow-up."""
from __future__ import annotations
import hashlib, json, os, pathlib, pwd, re, stat, subprocess, sys, time, urllib.request, urllib.error, datetime
H=pathlib.Path('/home/kali')
C=H/'.config/bridge-pg-v2'
S=C/'managed-mcp-local-key35c'
MARK=C/'stage35q-restart-attempt.json'
CFG=H/'.config/gigatool/opencode.json'
CFG_HASH='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
LAUNCHER=H/'hermes-pg-stage35jk-cutover/run_managed_tunnel.sh'
LAUNCHER_HASH='9c41cf36448069031669fcdd502507e10b78541734ffea1e7ace4ee6c10e485a'
NEW='hermes-pg-tunnel-stage35jk.service'
OLD='hermes-pg-tunnel-stage33fd.service'
MCP='hermes-pg-stage35c-static-shadow.service'
PERMIT=C/'managed-mcp-write-permit35c.json'
class Stop(Exception):pass
def require(condition,reason):
    if not condition:raise Stop(reason)
def mark(x):print('STAGE35Q_'+x,flush=True)
def cmd(*args,timeout=20):return subprocess.run(args,text=True,capture_output=True,timeout=timeout)
def systemctl(*args,timeout=20):
    r=cmd('systemctl','--user',*args,timeout=timeout)
    require(r.returncode==0,'SYSTEMCTL_'+('_'.join(args[:2])).upper().replace('-','_'))
    return r.stdout.strip()
def active(name):return cmd('systemctl','--user','is-active','--quiet',name).returncode==0
def regular_owned(path,mode=None):
    try: st=path.lstat()
    except OSError:return False
    return stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1 and not(st.st_mode&0o022) and (mode is None or stat.S_IMODE(st.st_mode)==mode)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def get_status(port,path):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=2) as r:return r.status
    except (OSError,TimeoutError,urllib.error.HTTPError) as e:return getattr(e,'code',0)
def pid():
    p=systemctl('show',NEW,'-p','MainPID','--value')
    require(p.isdecimal() and int(p)>0,'BAD_MAIN_PID')
    return int(p)
def preflight():
    require(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and pathlib.Path.home()==H,'WRONG_USER')
    require(regular_owned(S,0o600),'SECRET_UNSAFE')
    key=S.read_bytes()
    require(re.fullmatch(rb'[A-Za-z0-9_-]{64}',key) is not None,'KEY_NOT_CANONICAL_64')
    require(regular_owned(LAUNCHER) and sha(LAUNCHER)==LAUNCHER_HASH,'LAUNCHER_DRIFT')
    txt=LAUNCHER.read_text('utf8')
    require('MCP_EXTRA_HEADERS=\'X-Bridge-Local-Key: file:'+str(S)+'\'' in txt and "MCP_OAUTH_DISCOVERY='disabled'" in txt,'LAUNCHER_CONFIG_CHANGED')
    require(regular_owned(CFG) and sha(CFG)==CFG_HASH,'PLUGIN_CONFIG_CHANGED')
    require(len(json.loads(CFG.read_text('utf8')).get('plugin',[]))==7,'PLUGINS_NOT_SEVEN')
    require(not PERMIT.exists() and not PERMIT.is_symlink(),'DB_WRITE_PERMIT_EXISTS')
    require(active(NEW) and not active(OLD) and active(MCP),'SERVICE_STATE_DRIFT')
    require(str(LAUNCHER) in systemctl('show',NEW,'-p','ExecStart','--value'),'UNIT_EXECSTART_DRIFT')
    require(get_status(18778,'/readyz')==200 and get_status(18777,'/ping')==200,'LOCAL_HEALTH_FAILED')
    return S.stat().st_mtime_ns

def save_marker(data):
    fd=os.open(MARK,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(data,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())

def restart():
    stamp=preflight()
    require(not MARK.exists() and not MARK.is_symlink(),'ALREADY_ATTEMPTED_NO_RETRY')
    oldpid=pid()
    save_marker({'attempt_time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pre_pid':oldpid,'secret_mtime_ns':stamp,'restarted':False,'stage':'35Q'})
    mark('ONE_SHOT_MARKER_CREATED_AFTER_SAFE_PREFLIGHT')
    r=cmd('systemctl','--user','restart',NEW,timeout=45)
    require(r.returncode==0,'RESTART_RETURNED_NONZERO')
    deadline=time.monotonic()+40
    ok=False
    while time.monotonic()<deadline:
        try:
            if active(NEW) and active(MCP) and not active(OLD) and pid()!=oldpid and get_status(18778,'/readyz')==200 and get_status(18777,'/ping')==200:
                ok=True;break
        except (Stop,subprocess.TimeoutExpired):pass
        time.sleep(1)
    require(ok,'RESTART_NOT_READY_DO_NOT_RETRY')
    require(S.stat().st_mtime_ns==stamp and regular_owned(S,0o600) and re.fullmatch(rb'[A-Za-z0-9_-]{64}',S.read_bytes()) is not None,'SECRET_CHANGED_AFTER_RESTART')
    require(sha(CFG)==CFG_HASH and not PERMIT.exists(),'PLUGIN_OR_PERMIT_CHANGED')
    mark('NEW_TUNNEL_RESTARTED_WITH_CANONICAL_KEY_FILE_UNCHANGED')
    mark('NEW_TUNNEL_PID_CHANGED_OLD_TUNNEL_INACTIVE')
    mark('NEW_READYZ_200_MCP_PING_200_SEVEN_PLUGINS_NO_WRITE_PERMIT')
    mark('LOCAL_PASS_REMOTE_CHATGPT_CREATE_REQUIRED')

def trace():
    require(MARK.exists() and regular_owned(MARK,0o600),'MISSING_RESTART_MARKER')
    info=json.loads(MARK.read_text('utf8'))
    since=info.get('attempt_time_utc','')
    require(re.fullmatch(r'2026-\d\d-\d\dT[\d:.]+\+00:00',since) is not None,'INVALID_MARKER_TIME')
    # No raw log contents, only aggregate counts. Journal filtering applies to post-restart evidence.
    since_dt=datetime.datetime.fromisoformat(since)
    since_arg=since_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    mark('TRACE_SINCE_UTC_'+since_dt.strftime('%Y%m%dT%H%M%S'))
    for label,unit in [('TUNNEL',NEW),('MCP',MCP)]:
        r=cmd('journalctl','--user','-u',unit,'--since',since_arg,'-o','cat','--no-pager',timeout=15)
        if r.returncode:
            mark(label+'_JOURNAL_UNAVAILABLE');continue
        lines=r.stdout.splitlines()
        counts={'HTTP_403':0,'UPSTREAM_ERROR':0,'UNSUPPORTED_CHANNEL':0,'INITIALIZE':0,'TOOLS_LIST':0,'POLL_ERROR':0,'MAIN':0,'HARPOON':0}
        for line in lines:
            low=line.lower()
            for key,pat in [('HTTP_403',r'(?<!\d)403(?!\d)'),('UPSTREAM_ERROR',r'upstream.{0,20}error'),('UNSUPPORTED_CHANNEL',r'unsupported.{0,20}channel'),('INITIALIZE',r'\binitialize\b'),('TOOLS_LIST',r'tools/list'),('POLL_ERROR',r'poll.{0,20}(?:fail|error)'),('MAIN',r'\bmain\b'),('HARPOON',r'\bharpoon\b')]:
                if re.search(pat,low):counts[key]+=1
        mark(label+'_LINES_'+str(len(lines)))
        for k,v in counts.items():mark(label+'_'+k+'_'+str(v))
    mark('TRACE_COMPLETE_NO_CHANGES_RAW_LOGS_WITHHELD')

def main():
    try:
        if len(sys.argv)!=2 or sys.argv[1] not in ('restart','trace'):raise Stop('USAGE_RESTART_OR_TRACE')
        if sys.argv[1]=='restart':restart()
        else:trace()
        return 0
    except (Stop,subprocess.TimeoutExpired,OSError,ValueError) as e:
        mark('STOP_'+(str(e) if isinstance(e,Stop) else type(e).__name__))
        return 2
if __name__=='__main__':sys.exit(main())
