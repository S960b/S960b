#!/usr/bin/env python3
"""Make an inert, hard-filtered, forced-dry-run copy. NEVER edits opencode.json."""
from __future__ import annotations
from pathlib import Path
import hashlib
import os
import re
import stat

SOURCE=Path('/home/kali/bridge-pg-v2/dist/mt-pg-inbox.mjs')
DEST=Path('/home/kali/hermes-pg-stage33e-shadow-clients/mt-pg-inbox-agent-shadow.mjs')
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
OLD_SPAWN='["-m", "bridge_pg.cli", ...args]'
NEW_SPAWN='["/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py", ...args]'
OLD_PROMPT='-m bridge_pg.cli'
NEW_PROMPT='/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py'
OLD_DRY='const dryRun = options.dry_run !== false;'
TASK_RE=re.compile(r'const taskIds = Array\.isArray\(options\.task_ids\)\s*\? options\.task_ids\.filter\(\(x\) => typeof x === "string" && /\^task_\[0-9a-f\]\{32\}\$/\.test\(x\)\)\s*: null;')


def render(src):
    if any(src.count(x)!=1 for x in (OLD_SPAWN,OLD_PROMPT,OLD_DRY)):
        raise ValueError('unexpected_plugin_cli_or_prompt_layout')
    if len(TASK_RE.findall(src))!=1 or CANARY not in src and 'function latestPending' not in src:
        raise ValueError('unexpected_plugin_filter_layout')
    out=src.replace(OLD_SPAWN,NEW_SPAWN).replace(OLD_PROMPT,NEW_PROMPT).replace(OLD_DRY,'const dryRun = true; // STAGE33E FORCED READ ONLY')
    out=TASK_RE.sub('const taskIds = Object.freeze(["'+CANARY+'"]);',out)
    if 'bridge_pg.cli' in out or OLD_SPAWN in out or OLD_DRY in out:
        raise ValueError('legacy_cli_survived')
    return out


def main():
    if DEST.exists() or DEST.is_symlink():
        raise RuntimeError('candidate_already_exists')
    if SOURCE.is_symlink() or not SOURCE.is_file() or SOURCE.stat().st_uid!=os.geteuid():
        raise RuntimeError('plugin_source_untrusted')
    if not (SOURCE.stat().st_mode & stat.S_IFREG):
        raise RuntimeError('plugin_source_not_regular')
    original=SOURCE.read_text(encoding='utf-8')
    output=render(original)
    fd=os.open(DEST,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.write(fd,output.encode('utf-8'))
        os.fsync(fd)
    finally:
        os.close(fd)
    print('STAGE33E_INERT_PLUGIN_PREPARED_FOR_REVIEW')
    print('STAGE33E_ORIGINAL_PG_PLUGIN_UNCHANGED='+str(hashlib.sha256(SOURCE.read_bytes()).hexdigest()==hashlib.sha256(original.encode('utf-8')).hexdigest()).lower())

if __name__=='__main__':main()
