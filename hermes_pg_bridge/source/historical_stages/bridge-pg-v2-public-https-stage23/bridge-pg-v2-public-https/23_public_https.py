#!/usr/bin/env python3
"""ChatGPT-authored temporary HTTPS exposure for PG v2 MCP via verified cloudflared.

Guardrails: existing MCP and DB are unchanged except the MCP OAuth public-base
systemd override; automatic rollback restores original loopback configuration.
The tunnel is intentionally NOT enabled at boot: Quick Tunnel names are ephemeral.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HOME = Path('/home/kali')
ROOT = HOME/'bridge-pg-v2'
CFG = HOME/'.config/bridge-pg-v2'
BIN = HOME/'.local/bin/cloudflared-pgv2'
UNIT_DIR = HOME/'.config/systemd/user'
TUNNEL_UNIT = UNIT_DIR/'bridge-pg-v2-quick-tunnel.service'
OVERRIDE_DIR = UNIT_DIR/'bridge-pg-v2-mcp.service.d'
OVERRIDE = OVERRIDE_DIR/'20-quick-tunnel-base.conf'
URL_REC = CFG/'quick-tunnel-url'
MCP_UNIT = 'bridge-pg-v2-mcp.service'
TUNNEL_NAME = 'bridge-pg-v2-quick-tunnel.service'
ORIGIN = 'http://127.0.0.1:8766'
API = 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest'
MAX_DOWNLOAD = 110 * 1024 * 1024


def stop(msg):
    raise RuntimeError(msg)


def run(*args, check=True):
    cp = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, check=False, timeout=30)
    if check and cp.returncode:
        stop('command_failed_' + Path(args[0]).name + '_' + str(cp.returncode))
    return cp


def private_write(path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.exists():
        stop('refuse_overwrite_' + path.name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def check_user_and_state():
    if pwd.getpwuid(os.geteuid()).pw_name != 'kali' or Path.home() != HOME:
        stop('wrong_user')
    for p in (ROOT, CFG, UNIT_DIR):
        if p.is_symlink() or not p.is_dir() or p.stat().st_uid != os.geteuid():
            stop('wrong_directory_' + p.name)
    if CFG.stat().st_mode & 0o077:
        stop('configuration_directory_not_private')
    for p in (TUNNEL_UNIT, OVERRIDE, URL_REC):
        if p.exists() or p.is_symlink():
            stop('already_configured_' + p.name)
    if run('systemctl','--user','is-active',MCP_UNIT).stdout.strip() != 'active':
        stop('mcp_unit_not_active')
    import urllib.request
    with urllib.request.urlopen(ORIGIN+'/ping', timeout=5) as resp:
        if resp.status != 200 or json.load(resp).get('service') != 'pg-bridge-v2':
            stop('local_mcp_ping_invalid')
    from socket import create_connection
    with create_connection(('127.0.0.1',15432),timeout=3):
        pass
    print('LOCAL_MCP_AND_SSH_TUNNEL_READY')


def install_cloudflared():
    if BIN.exists() or BIN.is_symlink():
        if BIN.is_symlink() or not BIN.is_file() or BIN.stat().st_uid != os.geteuid():
            stop('existing_binary_untrusted')
        cp = run(str(BIN),'--version')
        if 'cloudflared' not in cp.stdout + cp.stderr:
            stop('existing_binary_not_cloudflared')
        print('CLOUDFLARED_INSTALLED_EARLIER')
        return
    arch = {'x86_64':'cloudflared-linux-amd64','aarch64':'cloudflared-linux-arm64'}.get(platform.machine())
    if not arch:
        stop('unsupported_architecture')
    req=urllib.request.Request(API,headers={'User-Agent':'PGBridgeV2-verified-install','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        if resp.status != 200:stop('release_api_failed')
        data=json.loads(resp.read(3_000_000))
    tag=data.get('tag_name')
    if not isinstance(tag,str) or not re.fullmatch(r'\d{4}\.\d{1,2}\.\d{1,3}',tag):
        stop('release_tag_unexpected')
    assets=[x for x in data.get('assets',[]) if x.get('name')==arch]
    if len(assets)!=1:stop('release_binary_missing')
    a=assets[0]
    digest=a.get('digest') or ''
    expected='sha256:' + digest.removeprefix('sha256:')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', expected):stop('release_digest_not_provided')
    url=a.get('browser_download_url','')
    if url != f'https://github.com/cloudflare/cloudflared/releases/download/{tag}/{arch}':
        stop('release_url_unexpected')
    BIN.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if BIN.parent.is_symlink() or BIN.parent.stat().st_uid != os.geteuid():stop('bin_dir_unsafe')
    fd, name=tempfile.mkstemp(prefix='.cloudflared-pgv2-',dir=BIN.parent)
    os.fchmod(fd,0o700)
    h=hashlib.sha256();total=0
    try:
        with os.fdopen(fd,'wb') as dest:
            req=urllib.request.Request(url,headers={'User-Agent':'PGBridgeV2-verified-install'})
            with urllib.request.urlopen(req,timeout=45) as resp:
                while True:
                    buf=resp.read(1024*1024)
                    if not buf:break
                    total+=len(buf)
                    if total>MAX_DOWNLOAD:stop('binary_too_large')
                    h.update(buf);dest.write(buf)
            dest.flush();os.fsync(dest.fileno())
        if not total or h.hexdigest()!=digest[len('sha256:'):]:
            stop('cloudflared_sha256_mismatch')
        if BIN.exists() or BIN.is_symlink():stop('binary_appeared_concurrently')
        os.rename(name,BIN)
        cp=run(str(BIN),'--version')
        if 'cloudflared' not in cp.stdout + cp.stderr:
            stop('downloaded_binary_not_cloudflared')
    finally:
        if os.path.exists(name):os.unlink(name)
    print('OFFICIAL_CLOUDFLARED_SHA256_VERIFIED')


def start_tunnel():
    contents='''[Unit]
Description=Temporary PG Bridge v2 Cloudflare Quick Tunnel (do not enable)
After=network.target bridge-pg-v2-mcp.service
Requires=bridge-pg-v2-mcp.service

[Service]
Type=simple
ExecStart=/home/kali/.local/bin/cloudflared-pgv2 tunnel --no-autoupdate --url http://127.0.0.1:8766
Restart=no
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
'''
    private_write(TUNNEL_UNIT,contents.encode())
    run('systemctl','--user','daemon-reload')
    run('systemctl','--user','start',TUNNEL_NAME)
    started=time.monotonic()
    journal_since=int(time.time())-2
    url=None
    while time.monotonic()-started<35:
        logs=run('journalctl','--user','--unit',TUNNEL_NAME,'--since','@'+str(journal_since),'--no-pager','-o','cat',check=False).stdout
        found=re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com\b',logs)
        if found:
            url=found[-1]
            break
        cp=run('systemctl','--user','is-active',TUNNEL_NAME,check=False)
        if cp.stdout.strip() not in ('active','activating'):
            stop('tunnel_process_exited_before_url')
        time.sleep(.5)
    if url is None:stop('tunnel_url_not_reported')
    if not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com',url):stop('tunnel_url_invalid')
    print('TEMPORARY_CLOUDFLARE_TUNNEL_ESTABLISHED')
    return url


def update_mcp_public_base(url):
    if OVERRIDE_DIR.is_symlink():stop('override_dir_symlink')
    OVERRIDE_DIR.mkdir(mode=0o700,exist_ok=True)
    if OVERRIDE_DIR.stat().st_uid != os.geteuid():stop('override_dir_wrong_owner')
    private_write(OVERRIDE,('[Service]\nEnvironment=PG_V2_MCP_BASE='+url+'\n').encode())
    run('systemctl','--user','daemon-reload')
    run('systemctl','--user','restart',MCP_UNIT)
    print('MCP_OAUTH_PUBLIC_BASE_CONFIGURED')


def fetch(url, method='GET',data=None,headers=None,timeout=8):
    req=urllib.request.Request(url,method=method,data=data,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            body=r.read(128*1024)
            return r.status,r.headers,body
    except urllib.error.HTTPError as e:
        return e.code,e.headers,e.read(4096)


def verify_public(url):
    # Temporary Cloudflare edge provisioning is eventually consistent. Never
    # claim success without a full external HTTPS-origin roundtrip.
    result=None
    for _ in range(25):
        try:
            p,_,body=fetch(url+'/ping',timeout=5)
            if p==200 and json.loads(body).get('service')=='pg-bridge-v2':
                result=True
                break
        except (OSError,ValueError):
            pass
        time.sleep(1)
    if result is not True:stop('public_ping_not_ready')
    p,h,_=fetch(url+'/mcp',headers={'Accept':'application/json,text/event-stream'})
    if p not in (401,403):stop('public_mcp_unauthorized_not_blocked')
    www=h.get('WWW-Authenticate','')
    if url+'/.well-known/oauth-protected-resource/mcp' not in www:
        stop('public_mcp_auth_challenge_metadata_invalid')
    p,_,body=fetch(url+'/.well-known/oauth-authorization-server')
    if p!=200:stop('public_oauth_server_metadata_http_not_200')
    m=json.loads(body)
    if m.get('issuer')!=url:stop('public_oauth_issuer_mismatch')
    for k in ('authorization_endpoint','token_endpoint','registration_endpoint'):
        if not isinstance(m.get(k),str) or not m[k].startswith(url+'/'):
            stop('public_oauth_endpoint_mismatch_'+k)
    p,_,body=fetch(url+'/.well-known/oauth-protected-resource/mcp')
    if p!=200:stop('public_oauth_resource_metadata_not_200')
    rm=json.loads(body)
    if rm.get('resource')!=url+'/mcp':stop('public_oauth_resource_mismatch')
    print('EXTERNAL_HTTPS_PING_200_OK')
    print('EXTERNAL_MCP_401_OAUTH_CHALLENGE_OK')
    print('EXTERNAL_OAUTH_ISSUER_AND_RESOURCE_METADATA_OK')
    print('PUBLIC_MCP_URL='+url+'/mcp')


def rollback():
    # Do not swallow errors but do not print any passwords or URL from files.
    subprocess.run(['systemctl','--user','stop',TUNNEL_NAME],capture_output=True)
    if OVERRIDE.exists() and not OVERRIDE.is_symlink():OVERRIDE.unlink()
    if TUNNEL_UNIT.exists() and not TUNNEL_UNIT.is_symlink():TUNNEL_UNIT.unlink()
    subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True)
    subprocess.run(['systemctl','--user','restart',MCP_UNIT],capture_output=True)


def main():
    os.umask(0o077)
    try:
        check_user_and_state()
        install_cloudflared()
        try:
            url=start_tunnel()
            update_mcp_public_base(url)
            verify_public(url)
            private_write(URL_REC,(url+'\n').encode())
        except BaseException:
            print('HTTPS_ROLLOUT_FAILED_ROLLING_BACK_TO_LOOPBACK',file=sys.stderr)
            rollback()
            raise
    except Exception as exc:
        # Do not dump exception contexts as they may include private metadata.
        print('STOP_'+str(exc)[:120],file=sys.stderr)
        return 1
    print('QUICK_TUNNEL_TEMPORARY_NOT_ENABLED_AT_BOOT')
    print('MT_CANARY_AND_LEGACY_PLUGINS_UNCHANGED')
    print('PGV2_PUBLIC_HTTPS_TEST_READY')
    return 0

if __name__=='__main__':
    sys.exit(main())
