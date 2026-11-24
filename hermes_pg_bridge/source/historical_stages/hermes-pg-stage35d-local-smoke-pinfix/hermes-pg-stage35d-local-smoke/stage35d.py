#!/usr/bin/env python3
"""Stage 35D: safe read-only preflight of the currently installed tunnel-client;
then verification of a local 35C shadow. No DB or tunnel changes.
"""
from __future__ import annotations
import hashlib, json, os, pwd, re, socket, stat, subprocess, sys
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage35d-local-smoke'
OLD=HOME/'hermes-pg-stage33fd-tunnel-cutover/run_stage33fd_tunnel.sh'
BIN=HOME/'.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client'
OLD_SHA='8dc8c432edc5cd01bd57117c1c2abc0b3f53bb7a9c8695e21fe31435df91c029'
CFG=HOME/'.config/gigatool/opencode.json'
CFG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
S35C=HOME/'hermes-pg-stage35c-static-guard'
S35C_SOURCES={
 'static_guard35c.py':'643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c',
 'runtime35c.py':'07f5a6a909a954913843491a3ed750361bc4f66826144719f1aac6328d093641',
 'managed_mcp35c.py':'1f3c70f1f06d2e32b8e515cf630d57e319786c82ea756cb4272d21a46d3db508',
}
SECRET=HOME/'.config/bridge-pg-v2/managed-mcp-local-key35c'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
UNIT=HOME/'.config/systemd/user/hermes-pg-stage35c-static-shadow.service'
RUN_UNITS=('hermes-pg-tunnel-stage33fd.service','hermes-pg-v2-stage33fb-cg.service','bridge-pg-v2-ssh-shadow.service')
class Stop(Exception):pass

def need(cond,code):
    if not cond:raise Stop(code)

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def valid_sha256_pin(value):
    """A pinned source hash must be a *literal* 64-character lowercase SHA-256."""
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None

def is_active(name):
    p=subprocess.run(['systemctl','--user','is-active','--quiet',name],capture_output=True,timeout=10)
    return p.returncode==0

def check_source_files():
    # Reject a malformed pin before reading any source file; do not accept
    # changing local hashes as a replacement for the audited pinned values.
    need(all(valid_sha256_pin(digest) for digest in S35C_SOURCES.values()),
         '35C_SOURCE_PIN_MALFORMED')
    for name,digest in S35C_SOURCES.items():
        path=S35C/name
        need(path.is_file() and not path.is_symlink() and sha(path)==digest,
             '35C_SOURCE_CHANGED_'+name)

def live_checks():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
         and Path.home()==HOME and Path.cwd().resolve()==ROOT,'BAD_EXECUTION_CONTEXT')
    need(CFG.is_file() and not CFG.is_symlink() and sha(CFG)==CFG_SHA,'SEVEN_PLUGIN_CONFIG_DRIFT')
    need(OLD.is_file() and not OLD.is_symlink() and sha(OLD)==OLD_SHA,'OLD_TUNNEL_LAUNCHER_DRIFT')
    data=OLD.read_text()
    need("MCP_SERVER_URL='http://127.0.0.1:18773/mcp'" in data and
         "MCP_OAUTH_DISCOVERY='disabled'" in data,'OLD_TUNNEL_TARGET_CHANGED')
    need(all(is_active(u) for u in RUN_UNITS),'WORKING_TUNNEL_UNAVAILABLE')
    need(not is_active('hermes-pg-stage35a-oauth-shadow.service'),'OAUTH_SHADOW_RUNNING')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'MANAGED_WRITE_PERMIT_PRESENT')
    need(UNIT.is_file() and not UNIT.is_symlink() and (UNIT.stat().st_mode&0o077)==0,
         '35C_UNIT_UNSAFE_OR_MISSING')
    check_source_files()
    sys.path.insert(0,str(S35C))
    from static_guard35c import local_secret
    need(len(local_secret(SECRET))==64,'35C_SECRET_MISSING_OR_UNSAFE')
    need(BIN.is_file() and not BIN.is_symlink() and os.access(BIN,os.X_OK),
         'TUNNEL_CLIENT_BIN_UNAVAILABLE')
    print('STAGE35D_SEVEN_PLUGINS_CANARY_AND_TUNNEL_18773_PINNED')
    print('STAGE35D_35C_SECRET_AND_CODE_PRIVATE_WRITE_PERMIT_ABSENT')

def client_capability():
    """Check real installed binary; never inspect its tokens or dump env."""
    need(BIN.is_file() and os.access(BIN,os.X_OK),'TUNNEL_CLIENT_BIN_UNAVAILABLE')
    env={'PATH':'/usr/bin:/bin','HOME':'/home/kali','LANG':'C','LC_ALL':'C'}
    try:
        p=subprocess.run([str(BIN),'run','--help'],env=env,capture_output=True,
                         text=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise Stop('TUNNEL_CLIENT_HELP_UNAVAILABLE') from e
    helptext=(p.stdout+'\n'+p.stderr)[:200000].lower()
    need(p.returncode==0 and len(helptext)>100,'TUNNEL_CLIENT_HELP_FAILED')
    # Env name may not be documented by --help, but the CLI flag must exist.
    feature=('mcp.extra-headers' in helptext)
    need(feature,'INSTALLED_CLIENT_LACKS_MCP_EXTRA_HEADERS_FLAG')
    print('STAGE35D_INSTALLED_TUNNEL_CLIENT_EXTRA_HEADERS_SUPPORTED')
    print('STAGE35D_MCP_EXTRA_HEADERS_FILE_BACKING_REQUIRES_NEXT_STAGE_LOCAL_INJECTION_PROOF')

def preflight():
    live_checks()
    need(not is_active('hermes-pg-stage35c-static-shadow.service'),'35C_SHADOW_ALREADY_ACTIVE')
    try:
        with socket.create_connection(('127.0.0.1',18777),timeout=1):
            raise Stop('PORT_18777_ALREADY_IN_USE')
    except (ConnectionRefusedError,TimeoutError):pass
    client_capability()
    print('STAGE35D_PREFLIGHT_READY_35C_CAN_START_LOCALLY_NO_TUNNEL_SWITCH')

def verify_live():
    live_checks()
    need(is_active('hermes-pg-stage35c-static-shadow.service'),'35C_SHADOW_NOT_ACTIVE')
    client_capability()
    import smoke35d
    smoke35d.run()
    print('STAGE35D_LOCAL_AUTH_AND_MANAGED_READ_VERIFIED')
    print('STAGE35D_NO_TUNNEL_SWITCH_NO_DB_WRITES_NO_SDK')

def main(args):
    try:
        if args==['preflight']:preflight()
        elif args==['verify-live']:verify_live()
        elif args==['client-check']:client_capability()
        else:raise Stop('BAD_ARGUMENTS')
        return 0
    except Stop as e:
        print('STAGE35D_STOP_'+str(e),file=sys.stderr)
        return 2
    except Exception as e:
        print('STAGE35D_STOP_UNEXPECTED_'+type(e).__name__,file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
