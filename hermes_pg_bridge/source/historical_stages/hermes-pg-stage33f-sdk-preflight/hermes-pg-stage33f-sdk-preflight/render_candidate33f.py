#!/usr/bin/env python3
"""Create *unregistered* Stage 33F live-capable candidate from 33E shadow.

It CANNOT enable or register the plugin. The next stage must explicitly
back up config, enforce a single dispatcher, and control MT process restart.
"""
from pathlib import Path
import os
import stat
import hashlib

ROOT=Path('/home/kali/hermes-pg-stage33f-sdk-preflight')
SRC=Path('/home/kali/hermes-pg-stage33e-shadow-clients/mt-pg-inbox-agent-shadow.mjs')
DST=ROOT/'mt-pg-inbox-agent-stage33f.mjs'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
OLD_CLI='/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py'
NEW_CLI='/home/kali/hermes-pg-stage33f-sdk-preflight/stage33f_agent_cli.py'
OLD_DRY='const dryRun = true; // STAGE33E FORCED READ ONLY'
NEW_DRY='const dryRun = false; // STAGE33F PROSPECTIVE ONLY: NOT REGISTERED'
OLD_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox");'
NEW_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-agent-stage33f");'

def replace_exact(s, old, new, n):
    if s.count(old)!=n: raise RuntimeError('stage33f_source_layout_mismatch')
    return s.replace(old,new)

def render(src):
    s=src
    s=replace_exact(s,OLD_CLI,NEW_CLI,2)
    s=replace_exact(s,OLD_DRY,NEW_DRY,1)
    s=replace_exact(s,OLD_STATE,NEW_STATE,1)
    s=replace_exact(s,'--message-id <UNIQUE_ID>',
                    '--message-id pgv2-stage33f-sdk-report-v1',1)
    # The prompt must instruct a bounded exact report. We do not allow arbitrary
    # report text in the candidate's one-turn CLI.
    needle='"Ответ публикуется в PostgreSQL, а НЕ в Git.",'
    if s.count(needle)!=1: raise RuntimeError('stage33f_prompt_layout_mismatch')
    s=s.replace(needle,needle+'\n    "В этом тесте тело отчёта должно быть строго: PGV2_ROLE_BOUND_MT_SDK_ACK_20261122. Не выполняй другие задания.",',1)
    for marker in ('Object.freeze(["'+CANARY+'"]);','ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)',
      'state.inflight[key]', 'op:"claim"','op:"ack"', 'session.prompt'):
        if marker not in s:raise RuntimeError('stage33f_missing_guard')
    if 'bridge_pg.cli' in s or OLD_DRY in s or OLD_CLI in s:
        raise RuntimeError('stage33f_old_entrypoint_survived')
    return s

def main():
    if DST.exists() or DST.is_symlink():raise SystemExit('STAGE33F_STOP_ALREADY_GENERATED')
    st=SRC.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1:
        raise SystemExit('STAGE33F_STOP_UNTRUSTED_SHADOW_SOURCE')
    code=render(SRC.read_text('utf-8'))
    fd=os.open(DST,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        f.write(code);f.flush();os.fsync(f.fileno())
    print('STAGE33F_UNREGISTERED_SDK_CANDIDATE_GENERATED')
    print('STAGE33F_CANDIDATE_SHA256='+hashlib.sha256(code.encode()).hexdigest())

if __name__=='__main__':main()
