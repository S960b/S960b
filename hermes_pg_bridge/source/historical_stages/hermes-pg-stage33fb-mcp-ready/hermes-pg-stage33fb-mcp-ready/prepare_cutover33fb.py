#!/usr/bin/env python3
"""Prepare ONLY a private config draft. NEVER edits live opencode.json."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import stat
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage33fb-mcp-ready')
CFG = Path('/home/kali/.config/gigatool/opencode.json')
SRC = Path('/home/kali/hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs')
OLD = '/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
CFG_SHA = '83a8a84d40379d230f30cd48623abefb2eed6d33be6ffa49a3d59d860a10acc5'
PLUGIN_SHA = '3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7'


def require(cond, msg):
    if not cond:
        raise RuntimeError(msg)


def trusted(path):
    s = path.lstat()
    return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
            and s.st_nlink == 1 and not path.is_symlink())


def _path_and_options(item):
    if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1],dict):
        return item[0], item[1]
    raise RuntimeError('unexpected_plugin_entry')


def draft_json(raw: bytes):
    src = json.loads(raw)
    entries = src.get('plugin')
    require(isinstance(entries, list) and len(entries) == 8, 'plugin_layout_changed')
    hits = [i for i, item in enumerate(entries)
            if isinstance(item, list) and len(item) == 2 and item[0] == OLD]
    require(len(hits) == 1, 'active_canary_plugin_not_unique')
    i = hits[0]
    name, opts = _path_and_options(entries[i])
    require(opts.get('dry_run') is False and opts.get('task_ids') == [CANARY],
            'current_canary_options_changed')
    require(str(SRC) not in raw.decode('utf-8'), 'new_plugin_already_registered')
    new = copy.deepcopy(src)
    new['plugin'][i] = [str(SRC), copy.deepcopy(opts)]
    require(len(new['plugin']) == 8, 'plugin_count_modified')
    for j in range(8):
        if j != i:
            require(new['plugin'][j] == entries[j], 'other_plugin_modified')
    return json.dumps(new, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'


def exclusive_write(path, contents):
    fd=os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(contents)
        f.flush()
        os.fsync(f.fileno())


def main():
    require(os.geteuid() != 0 and Path.home() == Path('/home/kali') and Path.cwd().resolve() == ROOT,
            'invalid_os_context')
    require(trusted(CFG) and trusted(SRC), 'untrusted_source')
    raw = CFG.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == CFG_SHA, 'live_config_sha_changed')
    require(hashlib.sha256(SRC.read_bytes()).hexdigest() == PLUGIN_SHA, 'active_candidate_sha_changed')
    original = ROOT / 'opencode-original-snapshot.json'
    draft = ROOT / 'opencode-stage33f-draft.json'
    require(not original.exists() and not draft.exists(), 'already_prepared')
    desired = draft_json(raw)
    exclusive_write(original, raw)
    try:
        exclusive_write(draft, desired)
    except Exception:
        original.unlink()
        raise
    require(CFG.read_bytes() == raw, 'live_config_changed_during_draft')
    print('STAGE33FB_PRIVATE_ROLLBACK_SNAPSHOT_AND_CONFIG_DRAFT_READY')
    print('STAGE33FB_LIVE_OPENCODE_CONFIG_UNMODIFIED')
    print('STAGE33FB_ORIGINAL_CONFIG_SHA256='+CFG_SHA)
    print('STAGE33FB_DRAFT_SHA256='+hashlib.sha256(desired).hexdigest())


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print('STAGE33FB_STOP_'+str(e).splitlines()[0][:100])
        raise SystemExit(2)
