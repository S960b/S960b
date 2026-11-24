#!/usr/bin/env python3
"""Stage 37B2D: owner-controlled, zero-write-capability plugin-path cutover.

Never writes to PostgreSQL, touches MCP/tunnel, runs SDK or issues a permit.
Prepare => commit only when all MultiTool processes stopped => verify => rollback
(rollback again requires all MultiTool processes stopped). No implicit reload.
"""
from __future__ import annotations
import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HOME = Path('/home/kali')
ROOT = HOME/'hermes-pg-stage37b2d-safe-cutover'
CFG = HOME/'.config/gigatool/opencode.json'
CAP = HOME/'.config/bridge-pg-v2'
PERMIT = CAP/'managed-mcp-write-permit35c.json'
GATE = CAP/'managed-writes-stage34d-armed'
OLD = HOME/'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
NEW = HOME/'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
RENDER = HOME/'hermes-pg-stage37b2c-claim-intent/render37b2c.py'
HELPER = HOME/'hermes-pg-stage37b2c-claim-intent/intent37b2c.mjs'
JOURNAL_DIR = HOME/'.local/state/mt-pg-inbox-managed-stage34e/claim-intents37b2c'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
CANARY = HOME/'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
B1_SOURCE = HOME/'hermes-pg-stage37b-readonly-restart/stage37b.py'
STAGE36B_PREP = HOME/'hermes-pg-stage36b-managed-inbox-handoff/prepared.json'
TESTER = HOME/'hermes-pg-stage37b2c1-revalidate/test_candidate37b2c1.cjs'
NODE = HOME/'.hermes/node/bin/node'
SRC_SHA = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
RENDER_SHA = 'ff1a7aa51920b1157a57124debb0a3f11b28313e3a245f7b603aaf6353aa2946'
B1_SHA = '2737b7f104a6ccf52d9439bb6e4aae0fef86941546d09ed7fffac6c6667b5562'
# Filled from byte-exact 37B2C1 archive by build script.
TESTER_SHA = '8c8f5daa4309716a9e00f668519c3c7c4af165c85a5fbd4130c5e2d7599cdfe6'
HELPER_SHA = '19acb9366f71f1c8e030322b914804f8b55664ad7e5f6cf8c14cab790ea876c5'
BASELINE_PREFIX = 'b623e140760c'
BACKUP = ROOT/'backup-opencode.json'
DRAFT = ROOT/'draft-opencode.json'
PREP = ROOT/'prepared.json'
ATTEMPT = ROOT/'commit-attempt.json'
COMMITTED = ROOT/'committed.json'
VERIFIED = ROOT/'verified.json'
ROLLBACK_ATTEMPT = ROOT/'rollback-attempt.json'
ROLLED_BACK = ROOT/'rolled-back.json'
LOCK = ROOT/'.stage37b2d.lock'

class Stop(RuntimeError): pass

def need(condition, reason):
    if not condition:
        raise Stop('STAGE37B2D_STOP_' + reason)

def digest(raw): return hashlib.sha256(raw).hexdigest()

def trusted_bytes(p, *, private=True, maximum=2_097_152):
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1
         and not p.is_symlink() and (not private or not(st.st_mode & 0o022))
         and 0 < st.st_size <= maximum and (not private or not (st.st_mode & 0o077)),
         'UNTRUSTED_FILE_'+p.name.replace('.','_'))
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        fst=os.fstat(fd)
        need((st.st_ino,st.st_dev,st.st_size)==(fst.st_ino,fst.st_dev,fst.st_size),
             'FILE_RACE_'+p.name.replace('.','_'))
        result=os.read(fd,maximum+1)
        need(len(result)==st.st_size, 'FILE_READ_CHANGED_'+p.name.replace('.','_'))
        return result
    finally:os.close(fd)

def trusted_dir(p):
    st=p.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
         and not p.is_symlink() and not(st.st_mode & 0o022),
         'UNTRUSTED_DIRECTORY_'+p.name)

def capabilities_absent():
    for p in [PERMIT,GATE]:
        need(not p.exists() and not p.is_symlink(), 'WRITE_CAPABILITY_PRESENT')

def user_check():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
         and Path.home()==HOME and os.getenv('HOME')==str(HOME),'WRONG_OWNER')
    for p in [HOME,ROOT,CFG.parent,CAP]: trusted_dir(p)
    capabilities_absent()

def file_pin(p,sha,*,private=True):
    raw=trusted_bytes(p,private=private)
    need(digest(raw)==sha,'PIN_CHANGED_'+p.name.replace('.','_'))
    return raw

def load_pinned_python(path,sha,mod_name):
    file_pin(path,sha)
    spec=importlib.util.spec_from_file_location(mod_name,str(path))
    need(spec is not None and spec.loader is not None,'MODULE_LOAD_UNAVAILABLE')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def schema_name(entry):
    if isinstance(entry,str):return entry
    need(isinstance(entry,list) and len(entry)==2 and isinstance(entry[0],str)
         and isinstance(entry[1],dict),'CONFIG_PLUGIN_SHAPE')
    return entry[0]

def parse_config(raw):
    data=json.loads(raw)
    need(isinstance(data,dict) and isinstance(data.get('plugin'),list)
         and len(data['plugin'])==8,'CONFIG_EIGHT_ONLY')
    names=[schema_name(x) for x in data['plugin']]
    need(len(set(names))==8 and names.count(str(CANARY))==1,'PLUGIN_DUPLICATES_OR_CANARY_MISSING')
    return data,names

def build_draft(raw):
    config,names=parse_config(raw)
    need(names.count(str(OLD))==1 and str(NEW) not in names,'EXPECTED_OLD_REGISTERED_ONCE')
    index=names.index(str(OLD))
    need(index==7,'MANAGED_NOT_LAST_ENTRY')
    e=config['plugin'][index]
    need(isinstance(e,list) and e[1].get('task_ids')==[TASK]
         and e[1].get('dry_run') is False and e[1].get('tick_ms')==30000,
         'MANAGED_POLICY_DRIFT')
    anchor=json.dumps(str(OLD)).encode('utf8')
    replacement=json.dumps(str(NEW)).encode('utf8')
    need(raw.count(anchor)==1,'OLD_PATH_ANCHOR_NOT_UNIQUE')
    draft=raw.replace(anchor,replacement,1)
    after,newnames=parse_config(draft)
    need(newnames[index]==str(NEW) and newnames[:7]==names[:7],
         'NEW_PLUGIN_NOT_EXCLUSIVE')
    expected=json.loads(raw)
    expected['plugin'][index][0]=str(NEW)
    need(after==expected, 'UNRELATED_CONFIG_CHANGED')
    return draft

def checked_config_meta():
    raw=trusted_bytes(CFG)
    baseline=json.loads(trusted_bytes(STAGE36B_PREP,maximum=8192))
    originalsha=baseline.get('cfg8_sha')
    need(isinstance(originalsha,str) and re.fullmatch(r'[0-9a-f]{64}',originalsha)
         and originalsha.startswith(BASELINE_PREFIX),'BASELINE_PIN_MISMATCH')
    return raw,originalsha

def verify_candidate():
    original=file_pin(OLD,SRC_SHA)
    renderer=load_pinned_python(RENDER,RENDER_SHA,'stage37b2c_renderer_pinned')
    file_pin(HELPER,HELPER_SHA)
    candidate=trusted_bytes(NEW)
    need(candidate==renderer.patched(original.decode('utf8')).encode('utf8'),
         'RENDERED_CANDIDATE_BYTES_CHANGED')
    # Ensure a literal, immutable helper import and a narrowly scoped sandbox verifier.
    need(str(HELPER).encode() in candidate and b'stage37b2cWriteIntent' in candidate,
         'CANDIDATE_HELPER_IMPORT_MISSING')
    file_pin(TESTER,TESTER_SHA)
    need(NODE.exists() and os.access(NODE,os.X_OK),'NODE_MISSING')
    for args in [[str(NODE),'--check',str(NEW)],
                 [str(NODE),str(TESTER),str(NEW)]]:
        result=subprocess.run(args,capture_output=True,text=True,timeout=30,
                              env={'PATH':str(NODE.parent)+':/usr/bin:/bin','HOME':str(HOME)})
        need(result.returncode==0,'CANDIDATE_NODE_VERIFIER_FAILED')
    return digest(candidate)

def journal_clean():
    if not JOURNAL_DIR.exists() and not JOURNAL_DIR.is_symlink():return True
    trusted_dir(JOURNAL_DIR)
    # No pre-existing claim intent or any other unexplained file may be overlooked.
    need(not any(JOURNAL_DIR.iterdir()),'CLAIM_INTENT_DIRECTORY_NONEMPTY')
    return True

def preflight_runtime():
    b1=load_pinned_python(B1_SOURCE,B1_SHA,'stage37b1_pinned_safe')
    need(b1.systemd_timer_active(),'WATCHDOG_NOT_ACTIVE')
    state=b1.state_snapshot()
    db=b1.pg_snapshot()  # SELECT only under SET TRANSACTION READ ONLY
    need(state['seen']>=1 and state['inflight_empty'] is True,'LOCAL_STATE_DRIFT')
    need(db['last_seq']==2 and db['dispatch_enabled'] is False,'DB_STATE_DRIFT')
    journal_clean()
    return b1

def processes_stopped(b1):
    for _ in range(2):
        found,unknown=b1.multitool_processes()
        need(not found and unknown==0,'MULTITOOL_MUST_BE_CLOSED')
        time.sleep(2)
    need(not b1.LOCK.exists() and not b1.LOCK.is_symlink(),'MANAGED_LOCK_PRESENT')

def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def create_exclusive(path,raw):
    need(not path.exists() and not path.is_symlink(),'FILE_EXISTS_'+path.name)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    sync_dir(path.parent)

def json_data(obj):
    return (json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode('utf8')

def pinned_meta():
    m=json.loads(trusted_bytes(PREP,maximum=8192))
    need(set(m)=={'version','old_config_sha','new_config_sha','old_plugin_sha','new_plugin_sha',
                  'helper_sha','task_id','operation'},'META_SHAPE_CHANGED')
    need(m['version']==1 and m['operation']=='replace_one_plugin_path'
         and m['task_id']==TASK and m['old_plugin_sha']==SRC_SHA
         and m['helper_sha']==HELPER_SHA,'META_DATA_CHANGED')
    old=trusted_bytes(BACKUP);draft=trusted_bytes(DRAFT)
    need(digest(old)==m['old_config_sha'] and digest(draft)==m['new_config_sha']
         and draft==build_draft(old),'STAGED_CONFIG_CHANGED')
    return m,old,draft

@contextmanager
def mutex():
    fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fst=os.fstat(fd)
        need(stat.S_ISREG(fst.st_mode) and fst.st_uid==os.geteuid()
             and fst.st_nlink==1 and not(fst.st_mode & 0o077),'LOCK_UNTRUSTED')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def atomic_replace_config(expected_sha,new_bytes):
    # Required filesystem mutator: only active opencode.json, after exact SHA check.
    need(digest(trusted_bytes(CFG))==expected_sha,'CONFIG_DRIFT_BEFORE_REPLACE')
    temp=CFG.parent/('.opencode.json.stage37b2d-'+str(os.getpid()))
    try:
        create_exclusive(temp,new_bytes)
        need(digest(trusted_bytes(CFG))==expected_sha,'CONFIG_CHANGED_DURING_REPLACE')
        os.replace(temp,CFG)
        sync_dir(CFG.parent)
    finally:
        if temp.exists():temp.unlink()
    need(digest(trusted_bytes(CFG))==digest(new_bytes),'ATOMIC_REPLACE_UNCONFIRMED')

def prepare():
    with mutex():
        for p in [BACKUP,DRAFT,PREP,ATTEMPT,COMMITTED,VERIFIED,ROLLBACK_ATTEMPT,ROLLED_BACK]:
            need(not p.exists() and not p.is_symlink(),'ALREADY_PREPARED_OR_USED')
        raw,expectedsha=checked_config_meta()
        need(digest(raw)==expectedsha,'CURRENT_CONFIG_DRIFT')
        newsha=verify_candidate()
        b1=preflight_runtime()
        found,unknown=b1.multitool_processes()
        need(unknown==0 and len(found)>0,'PREPARE_EXPECTED_ACTIVE_MULTITOOL')
        draft=build_draft(raw)
        meta={'version':1,'operation':'replace_one_plugin_path','old_config_sha':expectedsha,
              'new_config_sha':digest(draft),'old_plugin_sha':SRC_SHA,'new_plugin_sha':newsha,
              'helper_sha':HELPER_SHA,'task_id':TASK}
        create_exclusive(BACKUP,raw)
        create_exclusive(DRAFT,draft)
        create_exclusive(PREP,json_data(meta))
        need(digest(trusted_bytes(CFG))==expectedsha,'CONFIG_CHANGED_AFTER_PREPARE')
        print('STAGE37B2D_BACKUP_DRAFT_PREPARED_ONLY')
        print('STAGE37B2D_SOURCE_AND_CANDIDATE_EXACT_BYTES_VERIFIED')
        print('STAGE37B2D_NODE_SANDBOX_TASK_ALLOWLIST_PASSED')
        print('STAGE37B2D_NO_WRITE_CAPABILITIES_AND_DB_BASELINE')
        print('STAGE37B2D_EXPECTED_OLD_CONFIG_SHA',expectedsha[:12])
        print('STAGE37B2D_NEW_CONFIG_SHA',meta['new_config_sha'][:12])
        print('STAGE37B2D_NOW_CLOSE_ALL_MULTITOOL_WINDOWS_THEN_COMMIT')

def commit():
    with mutex():
        need(not ATTEMPT.exists() and not COMMITTED.exists() and not ROLLBACK_ATTEMPT.exists(),
             'COMMIT_ONE_SHOT_ALREADY_ATTEMPTED')
        meta,old,draft=pinned_meta()
        need(digest(trusted_bytes(CFG))==meta['old_config_sha'],'CONFIG_DRIFT')
        need(verify_candidate()==meta['new_plugin_sha'],'CANDIDATE_CHANGED')
        b1=preflight_runtime()
        processes_stopped(b1)
        capabilities_absent()
        create_exclusive(ATTEMPT,json_data({'phase':'commit_attempted','old':meta['old_config_sha'],
                                            'new':meta['new_config_sha'],'at':int(time.time())}))
        print('STAGE37B2D_COMMIT_ONCE_JOURNALED_NO_RETRY',flush=True)
        atomic_replace_config(meta['old_config_sha'],draft)
        create_exclusive(COMMITTED,json_data({'phase':'committed','new':meta['new_config_sha'],
                                              'at':int(time.time())}))
        print('STAGE37B2D_CONFIG_EIGHT_PLUGINS_ONE_PATH_REPLACED')
        print('STAGE37B2D_BACKUP_PRESERVED_FOR_EXACT_ROLLBACK')
        print('STAGE37B2D_NO_DB_SDK_PERMITS_SERVICES_MODIFIED')
        print('STAGE37B2D_NOW_OPEN_ONE_MULTITOOL_AND_RUN_VERIFY')

def verify():
    with mutex():
        meta,old,draft=pinned_meta()
        need(COMMITTED.exists() and not ROLLBACK_ATTEMPT.exists(),'COMMIT_NOT_CONFIRMED_OR_ROLLBACK_STARTED')
        need(digest(trusted_bytes(CFG))==meta['new_config_sha'],'NEW_CONFIG_DRIFT')
        data,names=parse_config(draft)
        need(names.count(str(NEW))==1 and names.count(str(OLD))==0,'PLUGIN_MIGRATION_NOT_EXACT')
        need(verify_candidate()==meta['new_plugin_sha'],'CANDIDATE_CHANGED')
        b1=preflight_runtime()
        proc,unknown=b1.multitool_processes()
        need(len(proc)>0 and unknown==0,'NEW_MULTITOOL_NOT_RUNNING')
        # Historical last closed state guaranteed at commit; do not infer that
        # the host plugin loader imported the new file solely from process count.
        if not VERIFIED.exists():
            create_exclusive(VERIFIED,json_data({'phase':'post_restart_readonly_verified',
                'config_sha':meta['new_config_sha'],'timestamp':int(time.time())}))
        print('STAGE37B2D_NEW_CONFIG_REGISTERED_ONE_MANAGED_CANDIDATE')
        print('STAGE37B2D_MT_REOPENED_PROCESSES',len(proc))
        print('STAGE37B2D_DB_SEQ1_SEQ2_STILL_DELIVERED_ONCE')
        print('STAGE37B2D_DISPATCH_FALSE_NO_PERMITS_NO_AGENT_GATE')
        print('STAGE37B2D_RUNTIME_PLUGIN_IMPORT_NOT_INDEPENDENTLY_PROVEN')
        print('STAGE37B2D_READONLY_CUTOVER_VERIFIED_NO_NEW_TASKS')

def rollback():
    with mutex():
        need(ATTEMPT.exists(),'NO_COMMIT_ATTEMPT_TO_ROLLBACK')
        need(not ROLLBACK_ATTEMPT.exists() and not ROLLED_BACK.exists(),
             'ROLLBACK_ONE_SHOT_ALREADY_ATTEMPTED')
        meta,old,draft=pinned_meta()
        b1=preflight_runtime()
        processes_stopped(b1)
        capabilities_absent()
        current=digest(trusted_bytes(CFG))
        need(current in (meta['old_config_sha'],meta['new_config_sha']),
             'UNKNOWN_CONFIG_CANNOT_ROLLBACK')
        create_exclusive(ROLLBACK_ATTEMPT,json_data({'phase':'rollback_attempted',
                                                       'current_sha':current,'at':int(time.time())}))
        print('STAGE37B2D_ROLLBACK_ONCE_JOURNALED_NO_RETRY',flush=True)
        if current==meta['new_config_sha']:
            atomic_replace_config(meta['new_config_sha'],old)
        need(digest(trusted_bytes(CFG))==meta['old_config_sha'],'ROLLBACK_UNCONFIRMED')
        create_exclusive(ROLLED_BACK,json_data({'phase':'rolled_back','at':int(time.time())}))
        print('STAGE37B2D_ORIGINAL_OPENCODE_RESTORED_BYTE_EXACT')
        print('STAGE37B2D_NO_DB_OR_SERVICE_MUTATION')
        print('STAGE37B2D_NOW_REOPEN_ONE_MULTITOOL_IF_DESIRED')

def status():
    with mutex():
        meta,old,draft=pinned_meta()
        current=digest(trusted_bytes(CFG))
        print('STAGE37B2D_STATUS_CONFIG_OLD',current==meta['old_config_sha'])
        print('STAGE37B2D_STATUS_CONFIG_NEW',current==meta['new_config_sha'])
        print('STAGE37B2D_STATUS_COMMIT_ATTEMPTED',ATTEMPT.exists())
        print('STAGE37B2D_STATUS_COMMITTED',COMMITTED.exists())
        print('STAGE37B2D_STATUS_VERIFIED',VERIFIED.exists())
        print('STAGE37B2D_STATUS_ROLLBACK_ATTEMPTED',ROLLBACK_ATTEMPT.exists())
        print('STAGE37B2D_STATUS_ROLLED_BACK',ROLLED_BACK.exists())
        print('STAGE37B2D_STATUS_PERMIT_ABSENT',not PERMIT.exists() and not PERMIT.is_symlink())
        print('STAGE37B2D_STATUS_AGENT_GATE_ABSENT',not GATE.exists() and not GATE.is_symlink())
        print('STAGE37B2D_STATUS_READONLY_NO_DB_OR_CONFIG_MUTATION')

def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in ('prepare','commit','verify','rollback','status'),'USAGE')
        user_check()
        modes={'prepare':prepare,'commit':commit,'verify':verify,'rollback':rollback,'status':status}
        modes[sys.argv[1]]()
        return 0
    except Exception as e:
        message=str(e) if isinstance(e,Stop) else 'STAGE37B2D_STOP_'+type(e).__name__
        print(message[:180],file=sys.stderr,flush=True)
        return 2

if __name__=='__main__': raise SystemExit(main())
