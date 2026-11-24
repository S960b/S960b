#!/usr/bin/env python3
"""Stage 38A: narrowly rearm *only* the existing Stage34D agent report gate.

No changes to MCP permit, configuration, database, services, plugin or task.
Shares the Stage37A advisory lock and requires a valid preexisting scoped permit.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
import time
from pathlib import Path

HOME = Path('/home/kali')
CONFIG = HOME / '.config/gigatool/opencode.json'
CAP = HOME / '.config/bridge-pg-v2'
PERMIT = CAP / 'managed-mcp-write-permit35c.json'
GATE = CAP / 'managed-writes-stage34d-armed'
LOCK = CAP / '.stage37a-revoke.lock'
WATCHDOG_CODE = HOME / 'hermes-pg-stage37a-permission-control/stage37a.py'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
CFG_SHA = 'f46c3d4c2a46bd28d545ecd8a4c435e9cb6f0938723cdd106cbb58e6aa4d0bf5'
CANDIDATE = HOME / 'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
CANARY = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
CANDIDATE_SHA_PREFIX = '06be5ea765f0'
WATCHDOG_SHA = '66cdc42d30cacf4f6ac8f038a11001a2fbac722bdd9c4848f89dbe4424625f07'
GATE_BYTES = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
MIN_REMAINING = 180  # Never arm if scoped MCP permit is about to expire.


class Stop(RuntimeError):
    pass


def need(condition: bool, code: str):
    if not condition:
        raise Stop('STAGE38A_STOP_' + code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def trusted_dir(path: Path):
    s = path.lstat()
    need(stat.S_ISDIR(s.st_mode) and not path.is_symlink() and
         s.st_uid == os.geteuid() and not s.st_mode & 0o022,
         'UNTRUSTED_DIR_' + path.name)


def read_checked(path: Path, max_len=2097152, private=False):
    st = path.lstat()
    need(stat.S_ISREG(st.st_mode) and not path.is_symlink() and
         st.st_uid == os.geteuid() and st.st_nlink == 1 and
         (not st.st_mode & (0o077 if private else 0o022)) and
         0 < st.st_size <= max_len, 'UNTRUSTED_FILE_' + path.name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        actual = os.fstat(fd)
        need((actual.st_dev, actual.st_ino, actual.st_size) ==
             (st.st_dev, st.st_ino, st.st_size), 'FILE_CHANGED_' + path.name)
        value = os.read(fd, max_len + 1)
        need(len(value) == st.st_size, 'FILE_CHANGED_DURING_READ_' + path.name)
        return value
    finally:
        os.close(fd)


def sync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_permit(cfg_bytes: bytes, permit_bytes: bytes, now: int) -> int:
    need(sha(cfg_bytes) == CFG_SHA, 'CONFIG_SHA_DRIFT')
    try:
        cfg = json.loads(cfg_bytes)
        p = json.loads(permit_bytes)
    except (ValueError, TypeError):
        raise Stop('STAGE38A_STOP_CONFIG_OR_PERMIT_INVALID_JSON')
    need(isinstance(cfg, dict) and isinstance(cfg.get('plugin'), list) and
         len(cfg['plugin']) == 8, 'PLUGIN_COUNT_CHANGED')
    names = [x if isinstance(x, str) else x[0] if isinstance(x, list) and
             len(x) == 2 and isinstance(x[0], str) and isinstance(x[1], dict)
             else None for x in cfg['plugin']]
    need(len(set(names)) == 8 and names.count(str(CANDIDATE)) == 1 and
         names.index(str(CANDIDATE)) == 7 and names.count(str(CANARY)) == 1,
         'PLUGIN_PATH_OR_ORDER_CHANGED')
    need(cfg['plugin'][7][1] == {'dry_run': False, 'tick_ms': 30000,
                                'task_ids': [TASK]}, 'MANAGED_PLUGIN_SCOPE_CHANGED')
    need(isinstance(p, dict) and set(p) == {'version', 'expires_unix',
         'config_sha256', 'task_ids', 'allow_create', 'static_tunnel_verified'},
         'PERMIT_SCHEMA_CHANGED')
    need(type(p['version']) is int and p['version'] == 1 and
         p['config_sha256'] == CFG_SHA and p['task_ids'] == [TASK] and
         type(p['allow_create']) is bool and p['allow_create'] is False and
         type(p['static_tunnel_verified']) is bool and
         p['static_tunnel_verified'] is True, 'PERMIT_NOT_SCOPED_TO_ONE_DIALOGUE')
    expiry = p['expires_unix']
    need(type(expiry) is int and now + MIN_REMAINING <= expiry <= now + 86400,
         'PERMIT_EXPIRED_OR_TOO_CLOSE')
    return expiry


def validate_environment():
    need(os.getuid() == os.geteuid() != 0 and
         pwd.getpwuid(os.getuid()).pw_name == 'kali' and
         Path.home() == HOME and os.environ.get('HOME') == str(HOME),
         'WRONG_OPERATOR')
    for p in (HOME, HOME / '.config', CAP, CONFIG.parent):
        trusted_dir(p)
    wd = read_checked(WATCHDOG_CODE, max_len=100000)
    need(sha(wd) == WATCHDOG_SHA, 'WATCHDOG_CODE_CHANGED')
    proc = subprocess.run(['systemctl', '--user', 'is-active', '--quiet',
                           'hermes-pg-stage37a-permit-watchdog.timer'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=8, check=False)
    need(proc.returncode == 0, 'WATCHDOG_INACTIVE')
    candidate = read_checked(CANDIDATE, max_len=250000)
    need(sha(candidate).startswith(CANDIDATE_SHA_PREFIX), 'MANAGED_PLUGIN_SHA_CHANGED')
    return read_checked(CONFIG), read_checked(PERMIT, max_len=4096, private=True)


def create_exclusive_gate(gate: Path, payload: bytes = GATE_BYTES):
    """Atomically create gate if absent; abort to quarantine if any failure."""
    need(not gate.exists() and not gate.is_symlink(), 'GATE_ALREADY_PRESENT')
    created = False
    try:
        fd = os.open(gate, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        created = True
        try:
            n = os.write(fd, payload)
            need(n == len(payload), 'GATE_SHORT_WRITE')
            os.fsync(fd)
        finally:
            os.close(fd)
        sync_dir(gate.parent)
        st = gate.lstat()
        need(st.st_uid == os.geteuid() and st.st_nlink == 1 and
             stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600 and
             st.st_size == len(payload) and read_checked(gate, 256, private=True) == payload,
             'GATE_POSTCREATE_MISMATCH')
    except BaseException:
        if created and (gate.exists() or gate.is_symlink()):
            dest = gate.parent / ('.stage38a-aborted-gate-' + str(os.getpid()) + '-' + str(time.time_ns()) + '.bak')
            os.rename(gate, dest)
            sync_dir(gate.parent)
        raise


def arm_gate():
    cfg, permit = validate_environment()
    # Same Stage37A lock; do not refresh/replace MCP permit or existing gate.
    lockfd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fst = os.fstat(lockfd)
        need(stat.S_ISREG(fst.st_mode) and fst.st_nlink == 1 and
             fst.st_uid == os.geteuid() and not fst.st_mode & 0o077,
             'WATCHDOG_LOCK_UNTRUSTED')
        fcntl.flock(lockfd, fcntl.LOCK_EX)
        try:
            now = int(time.time())
            # Re-read under lock: watchdog may have removed the permit since preflight.
            cfg2 = read_checked(CONFIG)
            permit2 = read_checked(PERMIT, max_len=4096, private=True)
            need(cfg2 == cfg and permit2 == permit, 'CAPABILITY_CHANGED_DURING_PREFLIGHT')
            expiry = validate_permit(cfg2, permit2, now)
            create_exclusive_gate(GATE)
            print('STAGE38A_ONLY_AGENT_GATE_OPENED')
            print('STAGE38A_SCOPED_PERMIT_UNCHANGED_EXPIRES_UNIX', expiry)
            print('STAGE38A_TASK', TASK)
            print('STAGE38A_GATE_MAX_AGE_SECONDS 1200')
            print('STAGE38A_WATCHDOG_ACTIVE_AND_SAME_LOCK')
            print('STAGE38A_NO_NEW_MCP_PERMIT_NO_DB_CONFIG_SERVICE_MUTATION')
            print('STAGE38A_NOW_AGENT_CAN_RESUME_EXACT_EXISTING_REPORT_SEQ5')
        finally:
            fcntl.flock(lockfd, fcntl.LOCK_UN)
    finally:
        os.close(lockfd)


def main():
    try:
        need(len(sys.argv) == 1, 'NO_ARGUMENTS_ACCEPTED')
        arm_gate()
    except Stop as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        print('STAGE38A_STOP_' + type(exc).__name__, file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
