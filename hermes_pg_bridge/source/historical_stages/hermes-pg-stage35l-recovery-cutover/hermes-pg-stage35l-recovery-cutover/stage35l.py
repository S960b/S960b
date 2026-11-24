#!/usr/bin/env python3
"""Stage 35L: single-use recovery of successful 35J + rolled-back 35K.

Pins and uses the original 35JK code without modifying it. Re-uses the proven
candidate launcher and same tunnel identity, fixes only owner-owned unitdir
permissions, then completes guarded cutover or restores old tunnel.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import sys
import time

HOME = Path('/home/kali')
ROOT = HOME / 'hermes-pg-stage35l-recovery-cutover'
JK = HOME / 'hermes-pg-stage35jk-cutover'
SOURCE = JK / 'stage35jk.py'
SOURCE_SHA = '5644bfca0617de256de4fe31fbb04ab4543c0862c2c49e2f26e6f03f80875c0d'
RUN_SHA = '9c41cf36448069031669fcdd502507e10b78541734ffea1e7ace4ee6c10e485a'
UNITDIR = HOME / '.config/systemd/user'
ATTEMPT = ROOT / 'stage35l-resume-attempt.json'
ACTIVE_MARK = ROOT / 'stage35l-active.json'
ROLL_MARK = ROOT / 'stage35l-rolled-back.json'
MODE_MARK = ROOT / 'stage35l-unitdir-secured.json'
PACKAGE_FILES = frozenset({'stage35l.py','test_stage35l.py',
    '190_install_and_resume_cutover_once.sh','191_verify_only.sh',
    '192_owner_rollback.sh','README.md','SHA256SUMS'})
RUNTIME_FILES = frozenset({ATTEMPT.name,ACTIVE_MARK.name,ROLL_MARK.name,MODE_MARK.name})

class Stop(Exception):
    pass

def need(condition, code):
    if not condition:
        raise Stop(code)

def mark(message):
    print('STAGE35L_'+message,flush=True)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def owned_regular(path, sha256=None):
    try: st=path.lstat()
    except OSError: return False
    if not (stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and
            st.st_nlink==1 and not(st.st_mode & 0o077)):
        return False
    return sha256 is None or sha(path)==sha256

def private_marker(path, data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w',encoding='utf8') as f:
        json.dump(data,f,sort_keys=True,separators=(',',':'))
        f.write('\n');f.flush();os.fsync(f.fileno())

def read_marker(path):
    need(owned_regular(path),'MARKER_MISSING_OR_UNSAFE_'+path.name)
    obj=json.loads(path.read_text(encoding='utf8'))
    need(isinstance(obj,dict),'MARKER_INVALID_'+path.name)
    return obj

def load_jk():
    need(owned_regular(SOURCE,SOURCE_SHA),'35JK_SOURCE_DRIFT_OR_UNSAFE')
    need(owned_regular(JK/'run_managed_tunnel.sh',RUN_SHA),'35JK_LAUNCHER_DRIFT_OR_UNSAFE')
    spec=importlib.util.spec_from_file_location('stage35jk_verified35l',SOURCE)
    need(spec is not None and spec.loader is not None,'35JK_IMPORT_UNAVAILABLE')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def dir_state():
    """Validate the unit directory and its immediately containing systemd dir."""
    parent=UNITDIR.parent.lstat()
    child=UNITDIR.lstat()
    need(stat.S_ISDIR(child.st_mode) and child.st_uid==os.geteuid(),
         'SYSTEMD_USER_UNITDIR_NOT_OWNED_REAL_DIRECTORY')
    need(stat.S_IMODE(child.st_mode) in (0o775,0o700),
         'SYSTEMD_USER_UNITDIR_UNEXPECTED_MODE')
    need(stat.S_ISDIR(parent.st_mode) and parent.st_uid==os.geteuid() and
         stat.S_IMODE(parent.st_mode) in (0o775,0o755,0o750,0o700),
         'SYSTEMD_PARENT_UNSAFE')
    config=(HOME/'.config').lstat()
    need(stat.S_ISDIR(config.st_mode) and config.st_uid==os.geteuid() and
         not(config.st_mode & 0o022), 'CONFIG_ANCESTOR_UNSAFE')
    return stat.S_IMODE(child.st_mode)

def pinned_fchmod(directory, permitted, target=0o700):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|getattr(os,'O_NOFOLLOW',0))
    try:
        st=os.fstat(fd);lst=directory.lstat();mode=stat.S_IMODE(st.st_mode)
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid() and
             mode in permitted and (st.st_dev,st.st_ino)==(lst.st_dev,lst.st_ino),
             'SYSTEMD_DIR_RACE_OR_UNEXPECTED_MODE')
        if mode==0o775:
            os.fchmod(fd,target)
        now=os.fstat(fd);lst2=directory.lstat()
        need(not(stat.S_IMODE(now.st_mode)&0o022) and
             (now.st_dev,now.st_ino)==(lst2.st_dev,lst2.st_ino),
             'SYSTEMD_DIR_CHMOD_NOT_STABLE')
        return mode
    finally:
        os.close(fd)

def secure_unitdir():
    previous=dir_state()
    parent_previous=pinned_fchmod(UNITDIR.parent,{0o775,0o755,0o750,0o700})
    current=pinned_fchmod(UNITDIR,{0o775,0o700})
    need(current==previous and dir_state()==0o700,
         'SYSTEMD_UNITDIR_CHMOD_NOT_STABLE')
    mark('SYSTEMD_UNITDIR_PRIVATE_700_PREVIOUS_'+oct(previous)[2:])
    if parent_previous==0o775:
        mark('SYSTEMD_PARENT_GROUP_WRITE_REMOVED_PREVIOUS_775')
    return previous

def package_preflight():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==HOME and Path.cwd().resolve()==ROOT,'WRONG_USER_OR_CWD')
    rs=ROOT.lstat()
    need(stat.S_ISDIR(rs.st_mode) and rs.st_uid==os.geteuid() and
         stat.S_IMODE(rs.st_mode)==0o700,'PACKAGE_ROOT_UNSAFE')
    present={p.name for p in ROOT.iterdir()}
    need(PACKAGE_FILES<=present and present<=PACKAGE_FILES|RUNTIME_FILES,
         'PACKAGE_FILES_UNEXPECTED')
    for name in present:
        need(owned_regular(ROOT/name),'PACKAGE_FILE_UNSAFE_'+name)

def base_preflight(s, *, resume_required=True):
    package_preflight()
    old_dir=Path.cwd()
    try:
        os.chdir(JK)
        s.static_preflight()  # pins 35G/HI, original tunnel launcher, secret, 7 plugins, no write permit
    finally:
        os.chdir(old_dir)
    need(read_marker(s.DEV_PASS).get('core_e2e_proven') is True,
         'PREVIOUS_E2E_NOT_PROVEN')
    cut=read_marker(s.CUT_MARK)
    rolled=read_marker(s.ROLL_MARK)
    need(cut.get('attempted') is True and cut.get('old_service')==s.OLD and
         cut.get('new_service')==s.NEW and rolled.get('rolled_back') is True and
         rolled.get('old_restored') is True,'PREVIOUS_ROLLBACK_MARKER_INVALID')
    need(not s.CUT_PASS.exists() and not s.CUT_PASS.is_symlink(),
         'PREVIOUS_CUTOVER_SUCCESS_MARKER_UNEXPECTED')
    need(s.active(s.BASE) and s.active(s.PG), 'PG_BASELINE_COMPONENTS_INACTIVE')
    need(str(s.OLD_SCRIPT) in s.show(s.OLD,'ExecStart'), 'OLD_TUNNEL_UNIT_DRIFT')
    dir_state()
    if resume_required:
        need(not any(p.exists() or p.is_symlink() for p in (ATTEMPT,ACTIVE_MARK,ROLL_MARK,MODE_MARK)),
             '35L_ALREADY_ATTEMPTED')
        need(not s.NEW_UNIT.exists() and not s.NEW_UNIT.is_symlink(),
             'NEW_UNIT_PRESENT_UNEXPECTED')
        need(s.active(s.OLD) and not s.active(s.NEW) and not s.active(s.SHADOW) and
             s.port_closed(18777) and s.port_closed(18778), 'START_SERVICES_OR_PORTS_DRIFT')
        need(s.http_check(18773,'/ping') and s.http_check(18774,'/readyz'),
             'OLD_TUNNEL_OR_BRIDGE_UNHEALTHY')
        # Re-check systemd exec of already-proven managed MCP service.
        g=s.load_35g();old=Path.cwd()
        try:
            os.chdir(s.G)
            g.check_original();g.check_dropin()
        finally:
            os.chdir(old)
        mark('PRECHECK_PREVIOUS_35J_E2E_AND_35K_ROLLBACK_PINNED_OLD_ACTIVE')

def stop_new_if_present(s):
    """A missing or inactive unit is not an error, unlike a still-running unit."""
    if s.active(s.NEW):
        reply=s.cmd('systemctl','--user','stop','--no-block',s.NEW,timeout=12)
        if reply.returncode!=0:
            mark('STOP_NEW_RETURNED_NONZERO_RECHECKING_STATE')
    return s.wait_for(lambda:not s.active(s.NEW) and s.port_closed(18778),25)

def stop_shadow(s):
    if s.active(s.SHADOW):
        reply=s.cmd('systemctl','--user','stop','--no-block',s.SHADOW,timeout=12)
        if reply.returncode!=0:
            mark('STOP_SHADOW_RETURNED_NONZERO_RECHECKING_STATE')
    return s.wait_for(lambda:not s.active(s.SHADOW) and s.port_closed(18777),35)

def rollback(s, reason, *, owner=False):
    mark('ROLLBACK_TRIGGER_'+reason)
    if not stop_new_if_present(s):
        mark('CRITICAL_NEW_NOT_STOPPED_OLD_WILL_NOT_START')
        return False
    if s.active(s.OLD):
        old_up=s.wait_for(lambda:s.active(s.OLD) and s.http_check(18774,'/readyz') and
                           s.http_check(18773,'/ping'),65)
    else:
        resp=s.cmd('systemctl','--user','start',s.OLD,timeout=25)
        old_up=resp.returncode==0 and s.wait_for(
            lambda:s.active(s.OLD) and s.http_check(18774,'/readyz') and
            s.http_check(18773,'/ping'),70)
    if not old_up:
        mark('CRITICAL_OLD_TUNNEL_NOT_RESTORED')
        return False
    mark('ROLLBACK_OLD_TUNNEL_18774_READY')
    shadow_down=stop_shadow(s)
    if not shadow_down:
        mark('ROLLBACK_WARNING_MANAGED_MCP_STILL_ACTIVE')
    s.plugin_unchanged()
    need(not s.active(s.NEW),'ROLLBACK_DUPLICATE_NEW_TUNNEL')
    if shadow_down and not ROLL_MARK.exists():
        private_marker(ROLL_MARK,{'stage':'35L','rolled_back':True,
                                  'old_restored':True,'owner_requested':owner,
                                  'remote_plugin_check_required':False})
    mark('ROLLBACK_OLD_18773_ACTIVE_NEW_DOWN_7_PLUGINS_UNCHANGED')
    return shadow_down

def cutover_once():
    s=load_jk()
    base_preflight(s)
    private_marker(ATTEMPT,{'stage':'35L','attempted':True,
                            'from_rolled_back_35JK':True,'single_tunnel':True})
    mark('ONE_SHOT_MARKER_CREATED_BEFORE_ANY_MUTATION')
    failure=None
    try:
        previous=secure_unitdir()
        private_marker(MODE_MARK,{'stage':'35L','previous_mode':oct(previous),
                                  'current_mode':'0o700'})
        s.install_new_unit()  # pinned 35JK module, does NOT enable
        need(s.NEW_UNIT.exists() and not s.active(s.NEW),
             'NEW_UNIT_NOT_INSTALLED_INACTIVE')
        mark('NEW_TUNNEL_UNIT_CREATED_UNENABLED')
        s.user_systemctl('start',s.SHADOW,timeout=25)
        need(s.wait_for(lambda:s.http_check(18777,'/ping',needle=b'stage35c-static-shadow'),60),
             'MANAGED_MCP_START_TIMEOUT')
        mark('MANAGED_MCP_18777_HEALTHY')
        s.user_systemctl('stop',s.OLD,timeout=30)
        need(s.wait_for(lambda:not s.active(s.OLD) and s.port_closed(18774),40),
             'OLD_TUNNEL_NOT_STOPPED')
        mark('OLD_TUNNEL_STOPPED_SINGLE_ID_GUARD')
        time.sleep(2)
        s.user_systemctl('start',s.NEW,timeout=25)
        need(s.wait_for(lambda:s.active(s.NEW) and s.http_check(18778,'/readyz'),90,interval=1),
             'NEW_TUNNEL_18778_NOT_READY')
        s.verify_unit()
        need(not s.active(s.OLD) and s.port_closed(18774),
             'DUPLICATE_TUNNEL_OR_OLD_HEALTH_LISTENER')
        need(s.active(s.SHADOW) and
             s.http_check(18777,'/ping',needle=b'stage35c-static-shadow'),
             'SHADOW_NOT_HEALTHY_AFTER_CUTOVER')
        need(s.http_check(18773,'/ping'),'LEGACY_BRIDGE_18773_UNEXPECTEDLY_BROKEN')
        s.plugin_unchanged()
        need(dir_state()==0o700,'SYSTEMD_UNITDIR_MODE_DRIFT_AFTER_CUTOVER')
        private_marker(ACTIVE_MARK,{'stage':'35L','new_tunnel_active':True,
                    'old_tunnel_inactive':True,'managed_mcp_port':18777,
                    'remote_plugin_check_pending':True,'dispatch_armed':False,
                    'new_unit_enabled':False})
        mark('CUTOVER_NEW_18778_READY_MCP_18777_OLD_TUNNEL_INACTIVE')
        mark('CUTOVER_SUCCESS_REMOTE_CHATGPT_MCP_TEST_REQUIRED_NO_DB_WRITES')
        return
    except BaseException as e:
        failure=e
    try:
        restored=rollback(s,'CUTOVER_FAILURE')
        mark('CUTOVER_FAILED_AUTO_ROLLBACK_'+('SUCCESS_OLD_ACTIVE' if restored
                                             else 'INCOMPLETE_MANUAL_REVIEW_REQUIRED'))
    except BaseException as e:
        mark('CRITICAL_ROLLBACK_EXCEPTION_'+type(e).__name__)
    raise failure

def verify_only():
    s=load_jk()
    base_preflight(s,resume_required=False)
    need(ATTEMPT.exists(),'35L_NOT_ATTEMPTED')
    mode=dir_state()
    if ACTIVE_MARK.exists() and not ROLL_MARK.exists():
        value=read_marker(ACTIVE_MARK)
        need(value.get('new_tunnel_active') is True,'ACTIVE_MARKER_INVALID')
        need(s.active(s.NEW) and not s.active(s.OLD) and s.active(s.SHADOW) and
             s.http_check(18778,'/readyz') and
             s.http_check(18777,'/ping',needle=b'stage35c-static-shadow') and
             s.port_closed(18774),'ACTIVE_CUTOVER_HEALTH_FAILED')
        s.verify_unit();s.plugin_unchanged()
        need(mode==0o700,'SYSTEMD_UNITDIR_MODE_DRIFT')
        mark('VERIFY_NEW_TUNNEL_AND_MCP_ACTIVE_REMOTE_VERIFICATION_PENDING')
    elif ROLL_MARK.exists():
        r=read_marker(ROLL_MARK)
        need(r.get('old_restored') is True and s.active(s.OLD) and not s.active(s.NEW) and
             not s.active(s.SHADOW) and s.http_check(18774,'/readyz') and
             s.http_check(18773,'/ping') and s.port_closed(18777) and
             s.port_closed(18778),'ROLLBACK_HEALTH_FAILED')
        s.plugin_unchanged()
        mark('VERIFY_ROLLED_BACK_OLD_TUNNEL_HEALTHY')
    else:
        raise Stop('35L_AMBIGUOUS_STATE_REQUIRES_INSPECTION')

def owner_rollback():
    s=load_jk()
    base_preflight(s,resume_required=False)
    need(read_marker(ATTEMPT).get('attempted') is True,'35L_ATTEMPT_MISSING')
    need(ACTIVE_MARK.exists(),'35L_ACTIVE_MARKER_REQUIRED')
    if ROLL_MARK.exists():
        verify_only()
        mark('OWNER_ROLLBACK_ALREADY_RECORDED')
        return
    need(read_marker(ACTIVE_MARK).get('new_tunnel_active') is True,
         '35L_ACTIVE_MARKER_INVALID')
    need(rollback(s,'OWNER_REQUESTED_REMOTE_CHECK_FAILED',owner=True),
         'OWNER_ROLLBACK_INCOMPLETE')
    mark('OWNER_ROLLBACK_COMPLETED_OLD_ACTIVE')

def handle_signal(signum, frame):
    raise Stop('INTERRUPTED_SIGNAL_'+str(signum))

def main():
    try:
        if sys.argv[1:]==['cutover-once']:
            signal.signal(signal.SIGTERM,handle_signal)
            signal.signal(signal.SIGINT,handle_signal)
        if sys.argv[1:]==['cutover-once']:
            cutover_once()
        elif sys.argv[1:]==['verify']:
            verify_only()
        elif sys.argv[1:]==['owner-rollback']:
            owner_rollback()
        else:
            raise Stop('INVALID_SUBCOMMAND')
        return 0
    except BaseException as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        mark('STOP_'+re.sub('[^A-Za-z0-9_]','_',code)[:90])
        return 2

if __name__=='__main__':
    raise SystemExit(main())
