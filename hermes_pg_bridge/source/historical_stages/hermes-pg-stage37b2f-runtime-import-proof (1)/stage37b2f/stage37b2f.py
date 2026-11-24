#!/usr/bin/env python3
"""Stage 37B2F: exact one-path, reversible, import-only MultiTool probe.

Never permits writes, changes DB, calls an SDK, or starts/stops services.
Instrumented wrapper re-exports the existing, SHA-pinned candidate unchanged.
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
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage37b2f-runtime-import-proof'
SOURCE=HOME/'hermes-pg-stage37b2d-safe-cutover/stage37b2d.py'
SOURCE_SHA='4d433c4596025b6ebe572c38096658e70dc7d8139b0b4d0eb438d2953f4c861e'
CFG=HOME/'.config/gigatool/opencode.json'
CANDIDATE=HOME/'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
WRAPPER=ROOT/'mt-pg-managed-runtime-import-proof37b2f.mjs'
PROOFS=ROOT/'proofs'
BACKUP=ROOT/'backup-opencode.json'
DRAFT=ROOT/'draft-opencode.json'
META=ROOT/'prepared.json'
ATTEMPT=ROOT/'commit-attempt.json'
COMMITTED=ROOT/'committed.json'
VERIFIED=ROOT/'verified.json'
RESTORE_ATTEMPT=ROOT/'restore-attempt.json'
RESTORED=ROOT/'restored.json'
LOCK=ROOT/'.stage37b2f.lock'
TASK='task_b6c26aa18220412bafdbf32393f16568'
CANDIDATE_PREFIX='06be5ea765f0'
BASELINE_PREFIX='f46c3d4c2a46'
NODE=HOME/'.hermes/node/bin/node'

class Stop(RuntimeError): pass

def need(x,reason):
    if not x: raise Stop('STAGE37B2F_STOP_'+reason)

def sha(b):return hashlib.sha256(b).hexdigest()

def jbytes(x):return (json.dumps(x,sort_keys=True,separators=(',',':'))+'\n').encode('utf8')

def makedir(p,mode=0o700):
    if not p.exists() and not p.is_symlink():p.mkdir(mode=mode)
    s=p.lstat()
    need(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and not(s.st_mode & 0o077),'UNTRUSTED_DIR_'+p.name)

def load_b2d():
    need(SOURCE.is_file() and not SOURCE.is_symlink(),'STAGE37B2D_SOURCE_MISSING')
    need(sha(SOURCE.read_bytes())==SOURCE_SHA,'STAGE37B2D_PIN_CHANGED')
    spec=importlib.util.spec_from_file_location('stage37b2d_pinned',str(SOURCE))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def fsync_dir(directory):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def exclusive_write(p,raw,mode=0o600):
    need(not p.exists() and not p.is_symlink(),'FILE_ALREADY_EXISTS_'+p.name)
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    fsync_dir(p.parent)

def trusted_file(b,p):return b.trusted_bytes(p)

def chk_user(b):
    b.user_check()  # owner, private paths, fail closed on active permits
    makedir(ROOT)
    if PROOFS.exists():makedir(PROOFS)

def guard_status(b):
    need(b.COMMITTED.exists() and b.VERIFIED.exists(),'37B2D_NOT_COMPLETED')
    need(not b.ROLLED_BACK.exists() and not b.ROLLBACK_ATTEMPT.exists(),'37B2D_ROLLED_BACK')
    m,old,draft=b.pinned_meta()
    need(m['new_config_sha'].startswith(BASELINE_PREFIX),'BASELINE_SHA_CHANGED')
    need(sha(trusted_file(b,CFG))==m['new_config_sha'],'ACTIVE_CONFIG_NOT_37B2D')
    need(b.verify_candidate()==m['new_plugin_sha'],'CANDIDATE_CHANGED')
    need(m['new_plugin_sha'].startswith(CANDIDATE_PREFIX),'CANDIDATE_PIN_CHANGED')
    b.preflight_runtime()  # readonly PostgreSQL and managed Inbox state
    return m

def wrapper_source(candidate,marker_dir,candidate_sha,target_path=None):
    # Preserve star-export names; ESM star excludes default, so re-export it only if present.
    need(isinstance(candidate,str) and '\x00' not in candidate and
         'export async function runTick(ctx)' in candidate,'PLUGIN_EXPORT_CONTRACT_CHANGED')
    has_default=bool(re.search(r'(?m)^\s*export\s+default\b|\bexport\s*\{[^}]*\bdefault\b[^}]*\}',candidate))
    target=str(target_path or CANDIDATE)
    p=str(marker_dir)
    source=(
        '// Stage 37B2F: temporary import-only wrapper. No SQL/SDK calls.\n'
        "import * as proofFs from 'node:fs';\n"
        "import * as child from "+json.dumps(target)+";\n"
        "export * from "+json.dumps(target)+";\n"
        +('export { default } from '+json.dumps(target)+';\n' if has_default else '')+
        'try {\n'
        '  const stat = proofFs.readFileSync("/proc/self/stat", "utf8");\n'
        '  const fields = stat.slice(stat.lastIndexOf(") ") + 2).trim().split(/\\s+/);\n'
        '  const startTicks = fields[19];\n'
        '  if (!/^\\d+$/.test(startTicks)) throw Error("proc-starttime");\n'
        '  const proofPath = '+json.dumps(p+'/load-')+' + process.pid + "-" + startTicks + ".json";\n'
        '  const body = JSON.stringify({schema:1, pid:process.pid,start_ticks:startTicks,\n'
        '    created_unix:Math.floor(Date.now()/1000),candidate_sha:'+json.dumps(candidate_sha)+',\n'
        '    module_path:'+json.dumps(target)+'}) + "\\n";\n'
        '  const fd = proofFs.openSync(proofPath, "wx", 0o600);\n'
        '  try { proofFs.writeSync(fd, body); proofFs.fsyncSync(fd); }\n'
        '  finally { proofFs.closeSync(fd); }\n'
        '  const directoryFd = proofFs.openSync('+json.dumps(p)+',"r");\n'
        '  try { proofFs.fsyncSync(directoryFd); } finally { proofFs.closeSync(directoryFd); }\n'
        '} catch { /* non-invasive: never break plugin import due to diagnostic I/O */ }\n'
    )
    return source.encode('utf8')

def config_draft(raw):
    obj=json.loads(raw)
    plugins=obj.get('plugin')
    need(isinstance(plugins,list) and len(plugins)==8,'CONFIG_PLUGIN_COUNT')
    names=[p[0] if isinstance(p,list) and len(p)==2 else p for p in plugins]
    need(names.count(str(CANDIDATE))==1 and names.index(str(CANDIDATE))==7,'CANDIDATE_NOT_ONE_EIGHTH_PLUGIN')
    entry=plugins[7]
    need(isinstance(entry,list) and entry[1]=={'dry_run':False,'tick_ms':30000,'task_ids':[TASK]},'MANAGED_POLICY_CHANGED')
    need(str(WRAPPER) not in names,'WRAPPER_ALREADY_REGISTERED')
    before=json.dumps(str(CANDIDATE)).encode('utf8')
    need(raw.count(before)==1,'CONFIG_PLUGIN_PATH_NOT_UNIQUE')
    replacement=json.dumps(str(WRAPPER)).encode('utf8')
    result=raw.replace(before,replacement,1)
    changed=json.loads(result)
    obj['plugin'][7][0]=str(WRAPPER)
    need(changed==obj,'CONFIG_OTHER_FIELDS_CHANGED')
    return result

def pinned(b):
    meta=json.loads(trusted_file(b,META))
    need(set(meta)=={'v','old_sha','new_sha','wrapper_sha','candidate_sha','task_id'},'META_SHAPE_CHANGED')
    need(meta['v']==1 and meta['task_id']==TASK,'META_CHANGED')
    old=trusted_file(b,BACKUP);new=trusted_file(b,DRAFT);w=trusted_file(b,WRAPPER)
    candidate=trusted_file(b,CANDIDATE)
    need(sha(old)==meta['old_sha'] and sha(new)==meta['new_sha']
         and sha(w)==meta['wrapper_sha'] and sha(candidate)==meta['candidate_sha'],
         'STAGED_BYTES_DRIFT')
    need(new==config_draft(old) and w==wrapper_source(candidate.decode('utf8'),PROOFS,meta['candidate_sha']),
         'STAGED_MODEL_CHANGED')
    return meta,old,new

def prepare(b):
    for f in [BACKUP,DRAFT,META,WRAPPER,ATTEMPT,COMMITTED,VERIFIED,RESTORE_ATTEMPT,RESTORED]:
        need(not f.exists() and not f.is_symlink(),'STAGE_ALREADY_STARTED_'+f.name)
    makedir(PROOFS)
    need(not list(PROOFS.iterdir()),'OLD_PROOFS_PRESENT')
    baseline=guard_status(b)
    original=trusted_file(b,CFG);cand=trusted_file(b,CANDIDATE)
    need(sha(original)==baseline['new_config_sha'],'CONFIG_CHANGED_BEFORE_PREPARE')
    need(NODE.is_file() and os.access(NODE,os.X_OK),'EXPLICIT_NODE_NOT_FOUND')
    wb=wrapper_source(cand.decode('utf8'),PROOFS,sha(cand))
    draft=config_draft(original)
    # Offline syntax check is not an import and MUST NOT create a proof.
    exclusive_write(WRAPPER,wb)
    proc=subprocess.run([str(NODE),'--check',str(WRAPPER)],capture_output=True,text=True,
                        env={'PATH':str(NODE.parent)+':/usr/bin:/bin','HOME':str(HOME)},timeout=20)
    need(proc.returncode==0,'WRAPPER_SYNTAX_CHECK_FAILED')
    need(not list(PROOFS.iterdir()),'PREPARE_CREATED_RUNTIME_PROOFS')
    exclusive_write(BACKUP,original);exclusive_write(DRAFT,draft)
    exclusive_write(META,jbytes({'v':1,'old_sha':sha(original),'new_sha':sha(draft),
        'wrapper_sha':sha(wb),'candidate_sha':sha(cand),'task_id':TASK}))
    need(sha(trusted_file(b,CFG))==sha(original),'LIVE_CONFIG_CHANGED_IN_PREPARE')
    print('STAGE37B2F_WRAPPER_REEXPORTS_PINNED_CANDIDATE')
    print('STAGE37B2F_PREPARE_BACKUP_DRAFT_ONLY')
    print('STAGE37B2F_NO_RUNTIME_PROOF_PREGENERATED')
    print('STAGE37B2F_OLD_CONFIG_SHA',sha(original)[:12])
    print('STAGE37B2F_WRAPPER_CONFIG_SHA',sha(draft)[:12])
    print('STAGE37B2F_READY_CLOSE_MULTITOOL_AND_COMMIT_ONCE')

def commit(b):
    need(not ATTEMPT.exists() and not COMMITTED.exists(),'COMMIT_ALREADY_ATTEMPTED')
    meta,old,draft=pinned(b)
    need(sha(trusted_file(b,CFG))==meta['old_sha'],'CONFIG_CHANGED')
    b.preflight_runtime();b.processes_stopped(b.load_pinned_python(b.B1_SOURCE,b.B1_SHA,'b1_instrumented'))
    b.capabilities_absent()
    need(not list(PROOFS.iterdir()),'PROOF_BEFORE_IMPORT')
    exclusive_write(ATTEMPT,jbytes({'event':'commit_attempt','at':int(time.time())}))
    print('STAGE37B2F_COMMIT_JOURNALED_NO_REPEAT',flush=True)
    b.atomic_replace_config(meta['old_sha'],draft)
    exclusive_write(COMMITTED,jbytes({'event':'wrapper_registered','at':int(time.time())}))
    print('STAGE37B2F_WRAPPER_CONFIG_ACTIVE_OPEN_ONE_MULTITOOL')

def proc_start(pid):
    raw=Path(f'/proc/{pid}/stat').read_text('utf8')
    return raw[raw.rfind(') ')+2:].split()[19]

def verify(b):
    meta,old,draft=pinned(b)
    need(COMMITTED.exists() and not RESTORE_ATTEMPT.exists(),'WRAPPER_NOT_ACTIVE')
    need(sha(trusted_file(b,CFG))==meta['new_sha'],'WRAPPER_CONFIG_DRIFT')
    b.preflight_runtime()
    b1=b.load_pinned_python(b.B1_SOURCE,b.B1_SHA,'b1_verify')
    mt,unknown=b1.multitool_processes()
    need(len(mt)>0 and unknown==0,'MULTITOOL_NOT_RUNNING')
    found=[];unmatched=[]
    for p in sorted(PROOFS.glob('load-*.json')):
        try:
            d=json.loads(trusted_file(b,p))
            pid=d['pid'];ticks=d['start_ticks']
            if set(d)!={'schema','pid','start_ticks','created_unix','candidate_sha','module_path'}:continue
            if d['schema']!=1 or d['candidate_sha']!=meta['candidate_sha'] or d['module_path']!=str(CANDIDATE):continue
            if not isinstance(pid,int) or pid<=1 or not re.fullmatch('[0-9]+',str(ticks)):continue
            if p.name!=f'load-{pid}-{ticks}.json':continue
            if str(pid) in set(mt) and proc_start(pid)==ticks:found.append(pid)
            else:unmatched.append(pid)
        except (FileNotFoundError,OSError,ValueError,TypeError,KeyError):continue
    print('STAGE37B2F_RUNTIME_PROOF_FILES',len(list(PROOFS.glob('load-*.json'))))
    print('STAGE37B2F_RUNTIME_PROOF_MT_PID_MATCHES',len(found))
    print('STAGE37B2F_RUNTIME_PROOF_UNMATCHED',len(unmatched))
    need(bool(found),'RUNTIME_IMPORT_NOT_INDEPENDENTLY_PROVEN')
    if not VERIFIED.exists():exclusive_write(VERIFIED,jbytes({'pids':found,'at':int(time.time())}))
    print('STAGE37B2F_RUNTIME_WRAPPER_EVALUATED_IN_LIVE_MT')
    print('STAGE37B2F_CANDIDATE_REEXPORTED_WITHOUT_EDIT')
    print('STAGE37B2F_NO_TASKS_NO_PERMITS_NO_DB_CHANGES')
    print('STAGE37B2F_READY_CLOSE_MULTITOOL_AND_RESTORE_CANDIDATE_PATH')

def restore(b):
    need(ATTEMPT.exists(),'NO_COMMIT_ATTEMPT')
    need(not RESTORE_ATTEMPT.exists() and not RESTORED.exists(),'RESTORE_ALREADY_ATTEMPTED')
    meta,old,draft=pinned(b)
    b.preflight_runtime()
    b1=b.load_pinned_python(b.B1_SOURCE,b.B1_SHA,'b1_restore')
    b.processes_stopped(b1)
    b.capabilities_absent()
    cur=sha(trusted_file(b,CFG))
    need(cur in {meta['old_sha'],meta['new_sha']},'CONFIG_UNKNOWN_DO_NOT_RESTORE')
    exclusive_write(RESTORE_ATTEMPT,jbytes({'at':int(time.time()),'current':cur}))
    print('STAGE37B2F_RESTORE_JOURNALED_NO_REPEAT',flush=True)
    if cur==meta['new_sha']:b.atomic_replace_config(meta['new_sha'],old)
    need(sha(trusted_file(b,CFG))==meta['old_sha'],'RESTORE_BYTE_MATCH_FAILED')
    exclusive_write(RESTORED,jbytes({'at':int(time.time()),'old_sha':meta['old_sha']}))
    print('STAGE37B2F_ORIGINAL_STAGE37B2C_PLUGIN_PATH_RESTORED')
    print('STAGE37B2F_CONFIG_SHA',meta['old_sha'][:12])
    print('STAGE37B2F_NO_DB_OR_SERVICE_CHANGES')

def status(b):
    meta,old,draft=pinned(b)
    cur=sha(trusted_file(b,CFG))
    print('STAGE37B2F_CONFIG_ORIGINAL',cur==meta['old_sha'])
    print('STAGE37B2F_CONFIG_WRAPPER',cur==meta['new_sha'])
    print('STAGE37B2F_COMMIT_ATTEMPTED',ATTEMPT.exists())
    print('STAGE37B2F_COMMITTED',COMMITTED.exists())
    print('STAGE37B2F_VERIFIED',VERIFIED.exists())
    print('STAGE37B2F_RESTORED',RESTORED.exists())
    print('STAGE37B2F_PROOF_FILE_COUNT',len(list(PROOFS.glob('load-*.json'))))
    b.capabilities_absent()
    print('STAGE37B2F_READONLY_NO_MUTATION')

def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in {'prepare','commit','verify','restore','status'},'USAGE')
        need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and str(HOME)==os.getenv('HOME'),'USER')
        if sys.argv[1]=='prepare':makedir(ROOT)
        else:need(ROOT.exists(),'NOT_INSTALLED')
        b=load_b2d();chk_user(b)
        lockfd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(lockfd,fcntl.LOCK_EX)
            {'prepare':prepare,'commit':commit,'verify':verify,'restore':restore,'status':status}[sys.argv[1]](b)
        finally:
            fcntl.flock(lockfd,fcntl.LOCK_UN);os.close(lockfd)
        return 0
    except Exception as exc:
        print(str(exc) if isinstance(exc,Stop) else 'STAGE37B2F_STOP_'+type(exc).__name__,file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main())
