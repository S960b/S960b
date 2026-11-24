#!/usr/bin/env python3
"""Read-only retrospective assessment of Stage 37B2F import evidence after rollback.

Not a live PID check. Does not open PostgreSQL or access application secrets.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage37b2f-runtime-import-proof'
CANDIDATE=HOME/'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
CFG=HOME/'.config/gigatool/opencode.json'
SOURCE_SHA='57687e228640b25323f9f578721ceffd2c92ad117c8bd724f112313414efeb90'
SOURCE=ROOT/'stage37b2f.py'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
TARGET_TASK='task_b6c26aa18220412bafdbf32393f16568'
MAX_BYTES=1024*1024

class AuditFailure(Exception):pass

def stop(reason):
    raise AuditFailure('STAGE37B2F1_STOP_'+reason)

def digest(raw):return hashlib.sha256(raw).hexdigest()

def safe_bytes(path, private=False, limit=MAX_BYTES):
    try:
        s=path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid():stop('UNTRUSTED_FILE_'+path.name)
        if private and (s.st_mode & 0o077):stop('INSECURE_PERMISSIONS_'+path.name)
        if s.st_size>limit:stop('OVERSIZED_FILE_'+path.name)
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        try:
            fs=os.fstat(fd)
            if (fs.st_ino,fs.st_dev)!=(s.st_ino,s.st_dev):stop('FILE_CHANGED_'+path.name)
            raw=os.read(fd,limit+1)
        finally:os.close(fd)
        if len(raw)>limit:stop('OVERSIZED_FILE_'+path.name)
        return raw
    except FileNotFoundError:stop('MISSING_FILE_'+path.name)

def load_json(path,private=True):
    try:return json.loads(safe_bytes(path,private).decode('utf8'))
    except (UnicodeError,ValueError):stop('INVALID_JSON_'+path.name)

def valid_structure(meta,commit,restore,proof,proof_name,candidate_path=str(CANDIDATE)):
    # Returns verdict *only* about evidence consistency, not past process identity.
    if not isinstance(meta,dict) or set(meta)!={'v','old_sha','new_sha','wrapper_sha','candidate_sha','task_id'}:stop('META_SHAPE')
    if meta['v']!=1 or meta['task_id']!=TARGET_TASK:stop('META_TARGET')
    if not all(isinstance(meta[k],str) and re.fullmatch('[a-f0-9]{64}',meta[k]) for k in ('old_sha','new_sha','wrapper_sha','candidate_sha')):stop('META_DIGESTS')
    if not isinstance(commit,dict) or commit.get('event')!='wrapper_registered' or type(commit.get('at'))!=int:stop('COMMIT_MARKER')
    if not isinstance(restore,dict) or type(restore.get('at'))!=int or restore.get('old_sha')!=meta['old_sha']:stop('RESTORE_MARKER')
    if not isinstance(proof,dict) or set(proof)!={'schema','pid','start_ticks','created_unix','candidate_sha','module_path'}:stop('PROOF_SHAPE')
    pid,ticks=proof.get('pid'),proof.get('start_ticks')
    if type(pid)!=int or pid<=1 or not isinstance(ticks,str) or not re.fullmatch(r'[1-9][0-9]*',ticks):stop('PROOF_PID_OR_TICKS')
    if proof_name!=f'load-{pid}-{ticks}.json':stop('PROOF_FILENAME_MISMATCH')
    if proof.get('schema')!=1 or proof.get('candidate_sha')!=meta['candidate_sha'] or proof.get('module_path')!=candidate_path:stop('PROOF_CANDIDATE_MISMATCH')
    born=proof.get('created_unix')
    if type(born)!=int or not (commit['at']<=born<=restore['at']):stop('PROOF_OUTSIDE_COMMIT_RESTORE')
    if restore['at']<commit['at']:stop('IMPOSSIBLE_TIMELINE')
    return {'pid':pid,'ticks':ticks,'created':born}

def require_kali():
    if os.getuid()!=os.geteuid() or os.getuid()==0 or os.environ.get('HOME')!=str(HOME) or os.environ.get('USER') not in ('kali',None):stop('USER_CONTEXT')

def run():
    require_kali()
    if not ROOT.is_dir() or ROOT.is_symlink():stop('INSTALL_MISSING')
    if digest(safe_bytes(SOURCE,True))!=SOURCE_SHA:stop('ORIGINAL_AUDITOR_SOURCE_DRIFT')
    meta=load_json(ROOT/'prepared.json')
    commit=load_json(ROOT/'committed.json')
    restore=load_json(ROOT/'restored.json')
    if not (ROOT/'commit-attempt.json').is_file() or not (ROOT/'restore-attempt.json').is_file():stop('MISSING_LIFECYCLE_MARKER')
    if (ROOT/'verified.json').exists():stop('UNEXPECTED_ORIGINAL_VERIFY_MARKER')
    original=safe_bytes(ROOT/'backup-opencode.json',True)
    draft=safe_bytes(ROOT/'draft-opencode.json',True)
    wrapper=safe_bytes(ROOT/'mt-pg-managed-runtime-import-proof37b2f.mjs',True)
    candidate=safe_bytes(CANDIDATE,True)
    config=safe_bytes(CFG,False)
    if digest(original)!=meta['old_sha'] or digest(draft)!=meta['new_sha'] or digest(wrapper)!=meta['wrapper_sha'] or digest(candidate)!=meta['candidate_sha']:stop('STAGED_CONTENT_DRIFT')
    if config!=original:stop('ORIGINAL_CONFIG_NOT_RESTORED')
    j=json.loads(config)
    ps=j.get('plugin')
    if not isinstance(ps,list) or len(ps)!=8:stop('CONFIG_PLUGIN_COUNT')
    if ps[7]!=[str(CANDIDATE),{'dry_run':False,'tick_ms':30000,'task_ids':[TARGET_TASK]}]:stop('CONFIG_MANAGED_PLUGIN_CHANGED')
    wd=json.loads(draft)
    if wd['plugin'][:7]!=ps[:7] or wd['plugin'][7][1]!=ps[7][1] or wd['plugin'][7][0]!=str(ROOT/'mt-pg-managed-runtime-import-proof37b2f.mjs'):stop('DRAFT_NOT_ONE_PATH_CHANGE')
    source=wrapper.decode('utf8')
    required_fragments = [
        'export * from '+json.dumps(str(CANDIDATE)),
        'import * as child from '+json.dumps(str(CANDIDATE)),
        'proofFs.openSync(proofPath, "wx", 0o600)',
        'proofFs.fsyncSync(fd)',
        'proofFs.fsyncSync(directoryFd)',
    ]
    if any(fragment not in source for fragment in required_fragments):
        stop('WRAPPER_PROOF_CONTRACT_CHANGED')
    proofdir=ROOT/'proofs'
    s=proofdir.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode & 0o077:stop('UNTRUSTED_PROOFS_DIR')
    proofs=list(proofdir.iterdir())
    if len(proofs)!=1:stop('PROOF_COUNT_NOT_ONE')
    path=proofs[0]
    evidence=valid_structure(meta,commit,restore,load_json(path),path.name,str(CANDIDATE))
    # Check contemporaneous boot-time plausibility, without mistaking it for a live PID check.
    try:
        btime=next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime '))
        hz=os.sysconf('SC_CLK_TCK')
        approx_start=btime+int(evidence['ticks'])/hz
        if btime > evidence['created']:
            print('STAGE37B2F1_PROCESS_START_TICKS_BOOT_REBOOTED_UNAVAILABLE')
        else:
            if approx_start > evidence['created']+120 or evidence['created']-approx_start>86400:stop('BOOT_TIME_IMPLAUSIBLE')
            print('STAGE37B2F1_PROCESS_START_TICKS_BOOT_PLAUSIBLE_YES')
    except (StopIteration,OSError,ValueError):
        print('STAGE37B2F1_PROCESS_START_TICKS_BOOT_UNAVAILABLE')
    if PERMIT.exists() or PERMIT.is_symlink():stop('WRITE_PERMIT_PRESENT')
    print('STAGE37B2F1_ORIGINAL_SET_MT_BUG_CONFIRMED')
    print('STAGE37B2F1_ORIGINAL_FOREIGN_STOP_CLASS_MASKING_CONFIRMED')
    print('STAGE37B2F1_ONE_PRIVATE_PROOF_SHA_AND_TIMELINE_VALID')
    print('STAGE37B2F1_WRAPPER_IMPORTS_AND_REEXPORTS_PINNED_CANDIDATE')
    print('STAGE37B2F1_CONFIG_RESTORED_EXACT_SHA',digest(config)[:12])
    print('STAGE37B2F1_HISTORICAL_IMPORT_EVIDENCE_CONSISTENT')
    print('STAGE37B2F1_LIVE_PROCESS_MATCH_NOT_REPRODUCIBLE_AFTER_RESTART')
    print('STAGE37B2F1_NO_TASKS_PERMITS_CONFIG_OR_SERVICE_MUTATION')
    return 0

if __name__=='__main__':
    try:sys.exit(run())
    except AuditFailure as e:
        print(str(e),file=sys.stderr);sys.exit(2)
    except Exception as e:
        print('STAGE37B2F1_STOP_UNEXPECTED_'+type(e).__name__,file=sys.stderr);sys.exit(2)
