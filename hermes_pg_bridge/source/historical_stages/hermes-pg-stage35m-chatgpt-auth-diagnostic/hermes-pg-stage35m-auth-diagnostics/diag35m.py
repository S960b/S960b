#!/usr/bin/env python3
"""Stage35M: read-only, no-secret diagnostics for tunneled MCP connector 403.
No tunnel/service restarts, no DB changes, no remote calls, no secret reads.
"""
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOME=pathlib.Path('/home/kali')
UNITS=('hermes-pg-tunnel-stage35jk.service',
       'hermes-pg-tunnel-stage33fd.service',
       'hermes-pg-stage35c-static-shadow.service')
PORTS={18773:'old MCP',18774:'old health',18775:'other shadow',18777:'managed MCP',18778:'new health'}
SAFE_STATUS_KEYS={
    'ready','healthy','live','active','attached','tunnel_attached',
    'connected','is_connected','is_ready', 'startup_completed',
    'last_poll_success','last_successful_poll','last_poll_at',
    'commands_poll_last_successful_timestamp_seconds',
    'last_poll_successful_timestamp_seconds',
    'poll_requests_total','poll_errors_total','commands_received_total',
    'requests_received_total','commands_executed_total',
    'mcp_requests_total','mcp_errors_total','commands_polled_total',
    'poll_success_count','poll_error_count','inflight','pending',
    'queue_depth','mode','state','reason_code',
    'status_code','poll_status',
}
SAFE_COUNTER_PAT=re.compile(r'(?:poll|command|request|mcp|oauth|error|forward|queued|session|response)',re.I)
ALLOWED_COUNTERS=re.compile(r'^[A-Za-z_:][A-Za-z0-9_:]*$')
METRIC_ALLOW=re.compile(r'(?:poll|mcp|commands|tunnel|forward|queue|request|error)',re.I)
SECRET_RE=re.compile(r'(?:token|secret|key|password|authorization|cookie|bearer|url|uri|header|path|host|body|response_text)',re.I)

def run(argv,timeout=8):
    try:
        p=subprocess.run(argv,timeout=timeout,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                         text=True,errors='replace',check=False)
        return p.returncode,p.stdout[:100000]
    except (OSError,subprocess.TimeoutExpired): return 124,''

def http(path,port=18778,method='GET'):
    url=f'http://127.0.0.1:{port}{path}'
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(urllib.request.Request(url,method=method),timeout=2) as r:
            return r.status,r.headers.get('content-type','').split(';',1)[0],r.read(262144)
    except urllib.error.HTTPError as e:
        try: e.close()
        except Exception: pass
        return e.code,'',b''
    except (OSError,TimeoutError): return None,'',b''

def permitted_scalar(v):
    if isinstance(v,bool): return str(v).lower()
    if isinstance(v,(int,float)) and -1e17<=v<=1e17: return str(v)
    if isinstance(v,str) and len(v)<25 and re.fullmatch(r'[a-zA-Z0-9_-]+',v) and not SECRET_RE.search(v):
        return v
    return None

def extract_safe(obj,prefix='',depth=0,out=None):
    if out is None: out=[]
    if depth>=6 or len(out)>=36: return out
    if isinstance(obj,dict):
        for key,value in obj.items():
            if len(out)>=36: break
            if not isinstance(key,str) or SECRET_RE.search(key): continue
            newkey=(prefix+'.'+key if prefix else key)
            if isinstance(value,(dict,list)):
                extract_safe(value,newkey,depth+1,out)
            elif key.lower() in SAFE_STATUS_KEYS or (SAFE_COUNTER_PAT.search(key) and isinstance(value,(int,float,bool))):
                safe=permitted_scalar(value)
                if safe is not None and len(newkey)<130:out.append((newkey,safe))
    elif isinstance(obj,list):
        for value in obj[:3]:
            if isinstance(value,dict):extract_safe(value,prefix+'[]',depth+1,out)
    return out

def metric_summaries(blob):
    out=[]
    for line in blob.decode('utf-8','replace').splitlines():
        if line.startswith('#') or not line or len(out)>=16: continue
        m=re.match(r'^([A-Za-z_:][A-Za-z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eE]+)(?:\s|$)',line)
        if not m:continue
        name,labels,val=m.groups()
        if not ALLOWED_COUNTERS.fullmatch(name) or not METRIC_ALLOW.search(name):continue
        if SECRET_RE.search(name) or len(name)>95:continue
        if labels and any(s in labels.lower() for s in ('user=', 'tenant=', 'token=', 'path=', 'url=', 'header=')):continue
        try: number=float(val)
        except ValueError:continue
        if abs(number)>1e18:continue
        if 'timestamp' in name.lower():
            val='nonzero' if number>0 else 'zero'
        out.append((name, val))
    return out

def journal_counts(unit):
    rc,s=run(['journalctl','--user','--no-pager','-o','cat','-u',unit,'--since','2 hours ago','-n','3000'],timeout=10)
    if rc!=0:return {'accessible':False}
    result={'accessible':True,'lines':len(s.splitlines())}
    for key,pat in {
      'mentions_rpc':r'(?i)(initialize|tools/list|/mcp|mcp request)',
      'mentions_poll':r'(?i)(poll|queue|control.plane)',
      'mentions_401':r'\b401\b',
      'mentions_403':r'\b403\b',
      'mentions_404':r'\b404\b',
      'mentions_5xx':r'\b5\d\d\b',
      'mentions_error':r'(?i)(error|failed|forbidden|denied|unauthorized)',
    }.items(): result[key]=sum(bool(re.search(pat,line)) for line in s.splitlines())
    return result

def port_listening(port):
    # /proc/net/tcp{,6} is kernel read-only; no external tools or bindings needed.
    hexport=f'{port:04X}'
    for name in ('/proc/net/tcp','/proc/net/tcp6'):
        try:
            for line in pathlib.Path(name).read_text().splitlines()[1:]:
                columns=line.split()
                if columns[3]=='0A' and columns[1].rsplit(':',1)[-1].upper()==hexport:
                    return True
        except OSError: pass
    return False

def file_meta(path):
    try:
        st=path.lstat()
        return f'exists={stat.S_ISREG(st.st_mode)} symlink={stat.S_ISLNK(st.st_mode)} owner_is_kali={st.st_uid==os.getuid()} mode={stat.S_IMODE(st.st_mode):03o}'
    except OSError: return 'missing'

def main():
    print('STAGE35M_READ_ONLY_CONNECTOR_AUTH_DIAGNOSTICS')
    if os.geteuid()==0 or os.getenv('HOME')!=str(HOME):
        print('STAGE35M_STOP_RUN_AS_KALI');return 2
    rc,uname=run(['id','-un'])
    if rc or uname.strip()!='kali':
        print('STAGE35M_STOP_RUN_AS_KALI');return 2
    print('STAGE35M_SYSTEMD:')
    for u in UNITS:
        rc,s=run(['systemctl','--user','show',u,'-p','ActiveState','-p','SubState','-p','Result','--no-pager'])
        fields=dict(l.split('=',1) for l in s.splitlines() if '=' in l)
        print('  '+u+' '+ ' '.join(k+'='+fields.get(k,'unknown') for k in ('ActiveState','SubState','Result')))
    print('STAGE35M_PORTS: '+ ' '.join(f'{p}={"listen" if port_listening(p) else "closed"}' for p in PORTS))
    print('STAGE35M_PRIVATE_CONFIG:')
    for name,path in {
      'local_mcp_key': HOME/'.config/bridge-pg-v2/managed-mcp-local-key35c',
      'tunnel_runtime_key': HOME/'.config/hermes-secure-tunnel-canary/runtime.key',
      'managed_tunnel_launcher': HOME/'hermes-pg-stage35jk-cutover/run_managed_tunnel.sh',
    }.items(): print('  '+name+' '+file_meta(path))
    launcher=HOME/'hermes-pg-stage35jk-cutover/run_managed_tunnel.sh'
    try:
        b=launcher.read_bytes() if not launcher.is_symlink() else b''
        print('STAGE35M_LAUNCHER_FLAGS: target_18777='+str(b'MCP_SERVER_URL=\'http://127.0.0.1:18777/mcp\'' in b).lower()+
          ' oauth_disabled='+str(b'MCP_OAUTH_DISCOVERY=\'disabled\'' in b).lower()+
          ' file_header='+str(b'X-Bridge-Local-Key: file:' in b).lower())
    except OSError: print('STAGE35M_LAUNCHER_FLAGS: unreadable')
    for path in ('/readyz','/health/oauth','/api/status'):
        status,ct,data=http(path)
        print(f'STAGE35M_TUNNEL_HTTP {path} status={status if status is not None else "UNREACHABLE"} content_type={ct or "unknown"}')
        if path!='/readyz' and status==200 and ('json' in ct or data.lstrip().startswith(b'{')):
            try:
                pairs=extract_safe(json.loads(data))
                for k,v in pairs: print('  STATUS '+k+'='+v)
            except (ValueError,UnicodeDecodeError,RecursionError):print('  STATUS invalid_json')
    stat,ctype,data=http('/metrics')
    print(f'STAGE35M_METRICS status={stat if stat is not None else "UNREACHABLE"}')
    if stat==200:
        for k,v in metric_summaries(data):print('  METRIC '+k+'='+v)
    p,ctype,data=http('/ping',18777)
    print('STAGE35M_MCP_PING status='+str(p if p is not None else 'UNREACHABLE'))
    # GET without backend credential must be denied by original security gate.
    p,ctype,data=http('/mcp',18777)
    print('STAGE35M_NEGATIVE_AUTH status='+str(p if p is not None else 'UNREACHABLE'))
    print('STAGE35M_JOURNAL_SUMMARY_LAST_2H:')
    for u in (UNITS[0],UNITS[2]):
        d=journal_counts(u)
        print('  '+u+' '+ ' '.join(k+'='+str(v) for k,v in d.items()))
    print('STAGE35M_FINISHED_NO_CHANGES_NO_DB_QUERIES_NO_SECRETS')
    return 0

if __name__=='__main__':sys.exit(main())
