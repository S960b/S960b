#!/usr/bin/env python3
"""ChatGPT-authored SSH forward runner. No shell, no plaintext host in unit."""
from __future__ import annotations
import os, re, stat, sys
from pathlib import Path

BASE = Path('/home/kali')
CFG = BASE / '.config/bridge-pg-v2'
TARGET = CFG / 'ssh-shadow-target'
KEY = BASE / '.ssh/mt_tun_ed25519'
HOSTS = BASE / '.ssh/known_hosts'
UNIT = BASE / '.config/systemd/user/bridge-pg-v2-ssh-shadow.service'
PATTERN = re.compile(r'^(?:[a-zA-Z_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$')

class Stop(Exception):
    pass

def safe_user() -> None:
    if os.geteuid() == 0 or os.environ.get('HOME') != str(BASE):
        raise Stop('EXPECTED_KALI_NONROOT_USER')

def validate_target(v: str) -> str:
    if not PATTERN.fullmatch(v) or '..' in v or len(v) > 256:
        raise Stop('INVALID_SSH_TARGET_FORMAT')
    return v

def private_file(p: Path, exact: int = 0o600, max_size: int = 512) -> bytes:
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != exact or s.st_nlink != 1 or s.st_size > max_size:
            raise Stop('UNSAFE_PRIVATE_FILE')
        return os.read(fd, max_size + 1)
    finally:
        os.close(fd)

def write_target() -> None:
    safe_user()
    v = validate_target(os.environ.get('PG_V2_SSH_TARGET', ''))
    if not CFG.is_dir() or CFG.is_symlink() or CFG.stat().st_uid != os.geteuid():
        raise Stop('UNSAFE_CONFIG_DIR')
    if not KEY.is_file() or not HOSTS.is_file() or KEY.is_symlink() or HOSTS.is_symlink():
        raise Stop('SSH_IDENTITY_OR_KNOWN_HOSTS_MISSING')
    if TARGET.exists() or TARGET.is_symlink():
        raise Stop('TARGET_FILE_ALREADY_EXISTS')
    fd = os.open(TARGET, os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(v + '\n')
        out.flush(); os.fsync(out.fileno())
    print('STAGE33C_SHADOW_PRIVATE_TARGET_CONFIGURED')

def run() -> None:
    safe_user()
    v = validate_target(private_file(TARGET).decode().strip())
    argv = ['/usr/bin/ssh','-N','-T','-i',str(KEY),
      '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
      '-o','UserKnownHostsFile='+str(HOSTS),'-o','ExitOnForwardFailure=yes',
      '-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3',
      '-o','ConnectTimeout=10',
      '-L','127.0.0.1:15433:127.0.0.1:5432',v]
    os.execv('/usr/bin/ssh', argv)

def main() -> int:
    try:
        if sys.argv[1:] == ['configure']:
            write_target()
        elif sys.argv[1:] == ['run']:
            run()
        else:
            raise Stop('USAGE_configure_or_run')
        return 0
    except (Stop, OSError, UnicodeError) as e:
        # Never include exception text (might contain host or filesystem information).
        print('STAGE33C_STOP_'+ (str(e) if isinstance(e,Stop) else type(e).__name__), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
