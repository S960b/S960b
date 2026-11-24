#!/usr/bin/env python3
"""Read-only environment preflight and explicit one-shot local-only shadow install.
No DB mutation, no restart or rewiring of an existing service/tunnel.
"""
from __future__ import annotations
import hashlib,os,pwd,secrets,socket,subprocess,sys
from pathlib import Path
from static_guard35c import local_secret,AuthDenied

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage35c-static-guard'
CONFIG=HOME/'.config/gigatool/opencode.json'
CONFIG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
SECRET=HOME/'.config/bridge-pg-v2/managed-mcp-local-key35c'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
UNIT=HOME/'.config/systemd/user/hermes-pg-stage35c-static-shadow.service'
OLD_UNIT=HOME/'.config/systemd/user/hermes-pg-stage35a-oauth-shadow.service'
class Stop(Exception): pass

def need(ok,msg):
    if not ok:raise Stop(msg)

def active(unit):
    return subprocess.run(['systemctl','--user','is-active','--quiet',unit],capture_output=True,timeout=10).returncode==0

def base_checks():
    need(pwd.getpwuid(os.geteuid()).pw_name=='kali' and
         os.getuid()==os.geteuid()!=0 and Path.home()==HOME and
         Path.cwd().resolve()==ROOT,'EXECUTION_CONTEXT_INVALID')
    need(CONFIG.is_file() and not CONFIG.is_symlink() and
         hashlib.sha256(CONFIG.read_bytes()).hexdigest()==CONFIG_SHA,
         'LIVE_CONFIG_NOT_SEVEN_PLUGIN_PIN')
    need(OLD_UNIT.is_file() and not OLD_UNIT.is_symlink(), 'STAGE35A_UNIT_MISSING')
    need(not active('hermes-pg-stage35a-oauth-shadow.service'),'OAUTH_SHADOW_ACTIVE')
    need(active('hermes-pg-tunnel-stage33fd.service') and
         active('hermes-pg-v2-stage33fb-cg.service') and
         active('bridge-pg-v2-ssh-shadow.service'),'WORKING_TUNNEL_OR_DB_FORWARD_DOWN')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'UNEXPECTED_WRITE_PERMIT_PRESENT')
    import importlib.util
    spec=importlib.util.spec_from_file_location('stage35a_live',str(HOME/'hermes-pg-stage35a-oauth-managed-shadow/stage35a.py'))
    need(spec is not None,'STAGE35A_PREFLIGHT_UNAVAILABLE')
    sys.path.insert(0,str(HOME/'hermes-pg-stage35a-oauth-managed-shadow'))
    import stage35a
    stage35a.read_db()  # read-only under real role-bound logins
    print('STAGE35C_SEVEN_PLUGIN_TUNNEL_AND_QUIESCED_MANAGED_PILOT_PINNED')
    print('STAGE35C_TWO_ROLE_DB_AND_RECEIPTS_READONLY_OK')

def preflight():
    base_checks()
    need(not SECRET.exists() and not SECRET.is_symlink(),'LOCAL_SECRET_ALREADY_EXISTS')
    need(not UNIT.exists() and not UNIT.is_symlink(),'STATIC_UNIT_ALREADY_EXISTS')
    need(not active('hermes-pg-stage35c-static-shadow.service'),'STATIC_SHADOW_ALREADY_ACTIVE')
    with socket.socket() as sock:
        try:sock.connect(('127.0.0.1',18777))
        except ConnectionRefusedError:pass
        else:raise Stop('PORT_18777_IN_USE')
    print('STAGE35C_PREFLIGHT_READONLY_NO_SERVICE_OR_DB_MUTATION')

def write_new(path:Path,data:bytes):
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
    if hasattr(os,'O_NOFOLLOW'):flags|=os.O_NOFOLLOW
    fd=os.open(str(path),flags,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
    except Exception:
        raise

UNIT_TEXT='''[Unit]
Description=Hermes PG Stage35C private static-header managed MCP shadow
After=bridge-pg-v2-ssh-shadow.service
Wants=bridge-pg-v2-ssh-shadow.service

[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-stage35c-static-guard
ExecStart=/usr/bin/env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage35c-static-guard/managed_mcp35c.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
UMask=0077

[Install]
# intentionally not enabled; separate authorization and tunnel cutover required
'''

def install():
    preflight()
    need(SECRET.parent.is_dir() and not SECRET.parent.is_symlink() and
         (SECRET.parent.stat().st_mode&0o077)==0,'PRIVATE_CONFIG_DIR_UNSAFE')
    need(UNIT.parent.is_dir() and not UNIT.parent.is_symlink(),'SYSTEMD_USER_DIR_MISSING')
    value=secrets.token_urlsafe(48).encode('ascii')+b'\n'
    write_new(SECRET,value)
    need(len(local_secret(SECRET))==64,'GENERATED_SECRET_UNEXPECTED')
    print('STAGE35C_LOCAL_SECRET_CREATED_PRIVATE_NO_VALUE_PRINTED')
    write_new(UNIT,UNIT_TEXT.encode('utf8'))
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=15)
    print('STAGE35C_STATIC_SHADOW_UNIT_CREATED_INACTIVE')
    verify()

def verify():
    base_checks()
    need(UNIT.is_file() and not UNIT.is_symlink() and
         UNIT.read_text()==UNIT_TEXT and (UNIT.stat().st_mode&0o077)==0,
         'STATIC_UNIT_NOT_PINNED')
    need(len(local_secret(SECRET))==64,'LOCAL_SECRET_MISSING_OR_UNSAFE')
    need(not active('hermes-pg-stage35c-static-shadow.service'),'SHADOW_UNEXPECTEDLY_ACTIVE')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'WRITE_PERMIT_UNEXPECTEDLY_PRESENT')
    print('STAGE35C_LOCAL_SECRET_PRIVATE_AND_MCP_SERVICE_INACTIVE')
    print('STAGE35C_NO_OAUTH_NO_DOMAIN_NO_PUBLIC_PORT_NO_WRITES')
    print('STAGE35C_ACTIVE_TUNNEL_REMAINS_18773')
    print('STAGE35C_VERIFY_COMPLETE_NO_TUNNEL_CUTOVER')

def main(argv):
    try:
        if argv==['preflight']:preflight()
        elif argv==['install-once']:install()
        elif argv==['verify']:verify()
        else:raise Stop('INVALID_COMMAND')
        return 0
    except Exception as exc:
        print('STAGE35C_STOP_'+(str(exc) if isinstance(exc,Stop) else
                                 'UNEXPECTED_'+type(exc).__name__)[:110],file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
