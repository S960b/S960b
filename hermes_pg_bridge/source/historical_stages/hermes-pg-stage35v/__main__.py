"""Kali user-side deployment and recovery. Invoke the ZIP with python3 -IB."""
import collections
import contextlib
import fcntl
import hashlib
import http.client
import io
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from policy35v import EVENTS, PUBLIC, REVIEWED, ROOT, denials, reviewed_subset
from runtime35v import G, HOME, PERMIT, PYTHON, need, probe, serve

MCP = 'hermes-pg-stage35c-static-shadow.service'
TUNNEL = 'hermes-pg-tunnel-stage35jk.service'
ZIP = ROOT / 'stage35v.zip'
U = HOME / 'hermes-pg-stage35u-connector-observer'
RUNTIME = Path('/run/user') / str(os.getuid()) / 'systemd/user'
CONFIG = HOME / '.config/systemd/user'
DROP_NAME = '95-hermes-stage35v.conf'
TEMP = RUNTIME / (MCP + '.d') / DROP_NAME
PERMANENT = CONFIG / (MCP + '.d') / DROP_NAME
ORIGINAL = str(G / 'service35g.py')
KEY_ROUTE = b'MCP_SERVER_URL=http://127.0.0.1:18777/mcp'

def mark(message):
    print('STAGE35V_' + message, flush=True)

def run(*args, timeout=40, check=True):
    r = subprocess.run(list(map(str,args)), capture_output=True, timeout=timeout)
    if check and r.returncode:
        raise ValueError('command_failed_' + Path(args[0]).name)
    return r

def ctl(*args, **kw):
    return run('/usr/bin/systemctl', '--user', *args, **kw)

def prop(unit, name):
    return ctl('show', unit, '-p', name, '--value').stdout.decode().strip()

def private_dir(p):
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = p.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022, 'unsafe_directory')

def write_new(path, data):
    # Link a fully written private file into place, refusing to replace existing state.
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def read_private(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1
             and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size <= limit, 'unsafe_file')
        data = stream.read(limit+1)
        need(len(data) <= limit, 'oversize_file')
        return data

def override(token, methods):
    words = ' '.join([token, *methods])
    return ('[Service]\nExecStart=\nExecStart=/usr/bin/env -u APPDIR -u APPIMAGE '
            f'{PYTHON} -IB {ZIP} serve {words}\nRestart=no\n').encode('ascii')

def read_override(path):
    if not os.path.lexists(path):
        return None
    data = read_private(path, 4096)
    found = re.search(rb' serve ([a-f0-9]{16})((?: [A-Za-z/]+)*)\n', data)
    need(found is not None, 'unrecognized_dropin')
    token = found[1].decode()
    methods = tuple(found[2].decode().split())
    need(set(methods) <= REVIEWED and tuple(sorted(set(methods))) == methods
         and data == override(token, methods), 'unrecognized_dropin')
    return token, methods

def proc_args(unit):
    pid = prop(unit, 'MainPID')
    if pid.isdecimal() and int(pid):
        try:
            return (Path('/proc') / pid / 'cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:
            pass
    return []

def service_ok(script):
    try:
        if (prop(MCP, 'ActiveState') != 'active' or os.fsencode(script) not in proc_args(MCP)
                or script not in prop(MCP,'ExecStart')):
            return False
        conn = http.client.HTTPConnection('127.0.0.1',18777,timeout=2)
        try:
            conn.request('GET','/ping')
            response = conn.getresponse()
            return response.status == 200
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False

def wait_service(script):
    end = time.monotonic()+24
    while time.monotonic()<end:
        if service_ok(script):
            return
        time.sleep(.35)
    raise ValueError('service_not_ready')

def base_state():
    need(not os.path.lexists(PERMIT), 'write_permit_present')
    receipt = json.loads(read_private(U/'stage35u-restored.json', 4096))
    need(receipt.get('stage') == '35U', 'restore_receipt_invalid')
    need(not os.path.lexists(CONFIG/(TUNNEL+'.d')/'93-stage35u-observer.conf'), '35u_route_still_active')
    for unit in ['hermes-pg-stage35u-observer.service','hermes-pg-stage35u-auto-restore.timer']:
        need(prop(unit,'ActiveState') == 'inactive', '35u_still_active')
    need(prop(TUNNEL,'ActiveState') == 'active','tunnel_inactive')
    pid = prop(TUNNEL,'MainPID')
    need(pid.isdecimal() and int(pid)>0,'no_tunnel_pid')
    env = (Path('/proc')/pid/'environ').read_bytes().split(b'\0')
    need([v for v in env if v.startswith(b'MCP_SERVER_URL=')] == [KEY_ROUTE]
         and pid == prop(TUNNEL,'MainPID'), 'tunnel_route_drift')
    return pid

def smoke(methods):
    r = run(PYTHON,'-IB',ZIP,'probe',*methods,timeout=70,check=False)
    if r.returncode:
        safe = re.findall(rb'^STAGE35V_STOP_([a-z0-9_]{1,64})$',r.stdout,re.M)
        if safe:
            mark('PROBE_DETAIL_' + safe[-1].decode())
        raise ValueError('real_sdk_smoke_failed')

def rollback(expected=None):
    temp, perm = read_override(TEMP), read_override(PERMANENT)
    live = proc_args(MCP)
    live_v = os.fsencode(ZIP) in live
    if expected is not None:
        if perm or (temp and temp[0]!=expected):
            return
        if not temp and expected.encode() not in live and expected not in prop(MCP,'ExecStart'):
            return
    if not temp and not perm and not live_v and service_ok(ORIGINAL):
        mark('ORIGINAL_ALREADY_ACTIVE')
        return
    # A timer must also repair an interrupted rollback: config removed, process still stage 35V.
    if not (temp or perm or live_v or str(ZIP) in prop(MCP,'ExecStart')):
        need(not live and ORIGINAL in prop(MCP,'ExecStart'), 'unrecognized_runtime')
    if temp:
        TEMP.unlink()
    if perm:
        PERMANENT.unlink()
    ctl('daemon-reload')
    need(ORIGINAL in prop(MCP,'ExecStart'), 'original_execstart_not_restored')
    if not service_ok(ORIGINAL):
        ctl('restart', MCP, timeout=50)
        wait_service(ORIGINAL)
    mark('RESTORED_35G_ON_18777')

def show(private=False):
    rows = denials()
    for index,(name,count) in enumerate(sorted(collections.Counter(rows).items()),1):
        label = name if name in PUBLIC or private else f'LOCAL_NAME_{index:02d}'
        mark(f'GATE_unknown_method_denied name={label} count={count}')
    mark(f'GATE_DENIAL_COUNT_{len(rows)}')
    if set(rows)-PUBLIC:
        mark('EXACT_IDENTIFIERS_IN_PRIVATE_LOCAL_REPORT')
    if '[redacted]' in rows:
        mark('UNSAFE_IDENTIFIER_OR_TRUNCATION_REQUIRES_REVIEW')

def activate(action):
    temp,perm = read_override(TEMP),read_override(PERMANENT)
    need(not temp and not perm, 'stage35v_already_active_collect_or_rollback')
    tunnel_pid = base_state()
    need(service_ok(ORIGINAL), 'expected_original_service')
    if action == 'capture':
        need(not os.path.lexists(EVENTS), 'prior_capture_exists_use_report')
        methods = ()
    else:
        methods = reviewed_subset(denials())
    smoke(())
    token = secrets.token_hex(8)
    timer = 'hermes-pg-stage35v-' + token
    private_dir(TEMP.parent)
    run('/usr/bin/systemd-run','--user','--quiet','--unit='+timer,'--on-active=20min',
        '--timer-property=AccuracySec=1s','/usr/bin/python3','-IB',ZIP,'timeout',token)
    need(prop(timer+'.timer','ActiveState') == 'active', 'timer_not_armed')
    try:
        write_new(TEMP,override(token, methods))
        ctl('daemon-reload')
        need(str(ZIP) in prop(MCP,'ExecStart') and token in prop(MCP,'ExecStart'),'override_not_effective')
        ctl('restart',MCP, timeout=50)
        wait_service(str(ZIP))
        smoke(methods)
        need(tunnel_pid == base_state(), 'tunnel_pid_changed')
    except BaseException:
        rollback(expected=token)
        raise
    mark(('CAPTURE' if action=='capture' else 'TRIAL')+'_ACTIVE_20MIN_REAL_SDK_11_TOOLS_READONLY')
    if methods:
        mark('OBSERVED_METHODS_ENABLED ' + ' '.join(methods))

def commit():
    temp = read_override(TEMP)
    perm = read_override(PERMANENT)
    if not temp and perm and service_ok(str(ZIP)):
        mark('ALREADY_COMMITTED')
        return
    need(temp and temp[1] and (not perm or perm==temp), 'no_reviewed_trial')
    need(temp[1] == reviewed_subset(denials()), 'new_denial_requires_review')
    need(service_ok(str(ZIP)) and temp[0].encode() in proc_args(MCP), 'trial_not_live')
    base_state()
    smoke(temp[1])
    private_dir(PERMANENT.parent)
    if not perm:
        write_new(PERMANENT, override(*temp))
    try:
        TEMP.unlink()
        ctl('daemon-reload')
        need(temp[0] in prop(MCP,'ExecStart') and service_ok(str(ZIP)), 'commit_verification_failed')
    except BaseException:
        rollback()
        raise
    mark('COMMITTED_AFTER_OPERATOR_UI_VERIFICATION')

def dispatch(args):
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
         and Path.home()==HOME,'wrong_host_user')
    action = args[0] if args else ''
    if action == 'serve':
        need(len(args)>=2 and re.fullmatch('[0-9a-f]{16}',args[1]), 'bad_service_args')
        serve(args[2:])
        return
    if action == 'probe':
        with contextlib.redirect_stdout(io.StringIO()):
            probe(tuple(args[1:]))
        return
    if action == 'timeout':
        need(len(args)==2 and re.fullmatch('[0-9a-f]{16}',args[1]), 'bad_timer_args')
    else:
        need(len(args)==1 and action in ('capture','collect','report','names','trial','commit','rollback'),
             'usage_capture_collect_report_names_trial_commit_rollback')
    private_dir(ROOT)
    need(stat.S_IMODE(ROOT.lstat().st_mode)==0o700, 'private_root_required')
    fd = os.open(ROOT/'controller.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    with os.fdopen(fd,'r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action in ('capture','trial'):
            source = Path(sys.argv[0]).read_bytes()
            if not os.path.lexists(ZIP):
                write_new(ZIP,source)
            need(hashlib.sha256(read_private(ZIP,1000000)).digest()==hashlib.sha256(source).digest(),
                 'installed_archive_differs')
            activate(action)
        elif action=='timeout':
            rollback(args[1])
        elif action=='rollback':
            rollback()
        elif action=='collect':
            try:
                if read_override(PERMANENT):
                    mark('PERMANENT_CONFIGURATION_PRESENT_REPORT_ONLY')
                else:
                    rollback()
            finally:
                show()
        elif action=='commit':
            commit()
        else:
            show(private=action=='names')

if __name__=='__main__':
    try:
        dispatch(sys.argv[1:])
    except (Exception,KeyboardInterrupt) as e:
        reason=str(e) if type(e) is ValueError and re.fullmatch('[a-z0-9_]{1,75}',str(e)) else type(e).__name__
        mark('STOP_'+reason)
        sys.exit(2)
