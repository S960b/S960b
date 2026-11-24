#!/usr/bin/env python3
"""Operator-approved, read-only-verified finalization of the Stage38C stale intent.

The agent role cannot query delivery for an agent->ChatGPT report. The attached
operator attestation records the separate verification ALREADY made by ChatGPT
through the pg managed_delivery_status and managed_ack_report APIs. This is NOT
an independent query of that ChatGPT-only receipt by this script.

Only live mutation: atomic archive of the exact pinned local claim-intent file.
No SQL mutation, no permits, no SDK or service changes.
"""
from __future__ import annotations
import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import stat
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
REPORT_MID = 'pgv2-mg-' + TASK.removeprefix('task_') + '-5-report'
EXPECTED_MSG = 'pgv2-stage38-correct-taskboard-8789-20261124-v1'

EXPECTED_RECEIPT = {
    'phase': 'seq5_ack_confirmed',
    'task_id': TASK,
    'seq': 5,
    'delivery_id': 21,
    'report_message_id': REPORT_MID,
}

OPERATOR_ACK = BASE / 'operator_pg_verified_ack38d.json'
EXPECTED_ACK_SHA = '00e738cc343af6919a3f66e8bccff4cfb3b6809650714b13c6d5b6433e03256d'
ORIGINAL_FINISH_SHA = 'c2b546cae7582227f563f56173242072b997c2ca2c6b325b939707eb82b9f1ea'
ORIGINAL_OPS_SHA = '015c3fc944c1c7823731615f751b0f1d008391ae79eec189ee4a6c97c1d685b2'

class Stop(Exception):
    pass


def require(ok, name):
    if not ok:
        raise Stop('STAGE38D_STOP_' + name)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def trusted_file(path: Path, size=250000):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and not path.is_symlink()
            and s.st_uid == os.geteuid() and s.st_nlink == 1
            and s.st_size > 0 and s.st_size <= size and not s.st_mode & 0o022,
            'UNTRUSTED_FILE_' + path.name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        current = os.fstat(fd)
        require((s.st_dev, s.st_ino, s.st_size) ==
                (current.st_dev, current.st_ino, current.st_size), 'FILE_RACE')
        raw = b''
        while len(raw) < s.st_size:
            part = os.read(fd, s.st_size - len(raw))
            if not part: break
            raw += part
        require(len(raw) == s.st_size, 'SHORT_READ')
        return raw
    finally:
        os.close(fd)


def private_dir(path: Path):
    st=path.lstat()
    require(stat.S_ISDIR(st.st_mode) and not path.is_symlink()
            and st.st_uid == os.geteuid() and not st.st_mode & 0o077,
            'UNTRUSTED_DIRECTORY_'+path.name)


def load_module(name, path, expected_sha):
    require(sha(trusted_file(path)) == expected_sha, 'SOURCE_PIN_DRIFT_'+path.name)
    spec=importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, 'BAD_MODULE_'+name)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_operator_ack(content_sha):
    raw=trusted_file(OPERATOR_ACK, 8192)
    require(sha(raw)==EXPECTED_ACK_SHA,'OPERATOR_ATTESTATION_DRIFT')
    record=json.loads(raw)
    require(record == {
        'source':'ChatGPT pg plugin managed_delivery_status / managed_ack_report / managed_status',
        'scope':'operator-verified historical state, not independently queried by bridge_agent',
        'task_id':TASK,
        'report_message_id':REPORT_MID,
        'report_body_sha256':content_sha,
        'seq5':{'status':'delivered','delivery_id':21,'attempts':1},
        'seq6':{'status':'delivered','delivery_id':22,'attempts':1,'ack':True},
        'dialogue':{'version':6,'last_seq':6,'policy_version':4,'dispatch_enabled':False,'next_actor':'chatgpt'},
    }, 'OPERATOR_ATTESTATION_CONTENT_MISMATCH')
    return record


def dirsync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def archive_intent(src, dst):
    # Source is pinned to one specific Stage37B2C preclaim state.
    private_dir(src.parent)
    private_dir(dst.parent)
    source=trusted_file(src, 1024)
    record=json.loads(source)
    require(record == {
        'version':1,
        'phase':'claim_outcome_uncertain',
        'task_id':TASK,
        'seq':5,
        'message_id':EXPECTED_MSG,
    },'INTENT_CONTENT_CHANGED')
    require(not dst.exists() and not dst.is_symlink(),'ARCHIVE_DESTINATION_EXISTS')
    # Refuse if another unrecognized intent exists.
    require([x.name for x in src.parent.iterdir()] == [src.name], 'ADDITIONAL_INTENTS_PRESENT')
    # src/dst must be on the same filesystem for atomic rename.
    require(src.parent.stat().st_dev == dst.parent.stat().st_dev, 'NOT_SAME_FILESYSTEM')
    os.rename(src,dst)
    dirsync(src.parent)
    dirsync(dst.parent)
    require(not src.exists() and dst.is_file() and sha(trusted_file(dst,1024))==sha(source),
            'ARCHIVE_INTEGRITY_FAILED')


def run():
    require(len(sys.argv)==1,'NO_ARGUMENTS')
    require(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
            and Path.home()==Path('/home/kali'), 'WRONG_USER')
    m=load_module('stage38d_pinned_finish38c',BASE/'finish38c.py',ORIGINAL_FINISH_SHA)
    ops=load_module('stage38d_pinned_ops38c',BASE/'ops38c.py',ORIGINAL_OPS_SHA)
    require(m.TASK==TASK and m.REPORT_MID==REPORT_MID, 'SOURCE_SCOPE_MISMATCH')
    for p, pin in m.PINS.items():
        m.pin(p,pin)
    private_dir(m.ROOT)
    receipt=json.loads(trusted_file(m.RECEIPT,2048))
    require(receipt == EXPECTED_RECEIPT, 'RECOVERY_CHECKPOINT_INCOMPLETE')
    report=m.report_status()
    require(report.get('attempted') is True and report.get('phase')=='committed'
            and report.get('confirmed_seq')==6, 'REPORT_NOT_COMMITTED')
    delivery5=m.delivery(5)
    require(delivery5.get('status')=='delivered' and delivery5.get('attempts')==1
            and delivery5.get('delivery_id')==21, 'SEQ5_DELIVERY_INCONSISTENT')
    peek=m.checked('peek','--task-id',TASK,'--offset','0','--limit','6')
    require(peek.get('task_id')==TASK and peek.get('seq')==6
            and peek.get('waiting_for')=='chatgpt', 'DIALOGUE_HEAD_CHANGED')
    rows=peek.get('messages',[])
    require(isinstance(rows,list), 'DIALOGUE_MESSAGES_SHAPE')
    exact=[row for row in rows if isinstance(row,dict) and row.get('seq')==6]
    require(len(exact)==1 and exact[0].get('message_id')==REPORT_MID
            and exact[0].get('body')==m.CONTENT.decode()
            and exact[0].get('kind')=='report'
            and exact[0].get('from')=='mt' and exact[0].get('to')=='chatgpt',
            'REPORT_BODY_NOT_EXACT')
    read_operator_ack(sha(m.CONTENT))
    print('STAGE38D_OPERATOR_PG_SEQ6_DELIVERED_ACK_ATTESTATION_PINNED', flush=True)
    print('STAGE38D_AGENT_CLI_SEQ5_DELIVERED_AND_REPORT_SEQ6_EXACT', flush=True)
    # preflight enforces exact live plugin and config pins, empty inflight,
    # watchdog active, original journal intact and valid before rename.
    ops.preflight()
    require(not ops.PERMIT.exists() and not ops.PERMIT.is_symlink()
            and not ops.GATE.exists() and not ops.GATE.is_symlink(),
            'LIVE_CAPABILITIES_PRESENT')
    src=ops.INTENTS/(TASK+'.json')
    dest=m.ROOT/'archived-intent-seq5-after-ack.json'
    lock=m.ROOT/'.stage38d-archive.lock'
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        ls=os.fstat(fd)
        require(stat.S_ISREG(ls.st_mode) and ls.st_uid==os.geteuid()
                and ls.st_nlink==1 and not ls.st_mode & 0o077,'ARCHIVE_LOCK_UNTRUSTED')
        fcntl.flock(fd,fcntl.LOCK_EX)
        # Make a second check that the watchdog hasn't reopened permissions.
        require(not ops.PERMIT.exists() and not ops.PERMIT.is_symlink()
                and not ops.GATE.exists() and not ops.GATE.is_symlink(),
                'LIVE_CAPABILITIES_PRESENT')
        archive_intent(src,dest)
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)
    print('STAGE38D_OLD_SEQ5_CLAIM_INTENT_ARCHIVED_ATOMICALLY', flush=True)
    print('STAGE38D_CAPABILITIES_REVOKED_DISPATCH_OFF_NO_SQL_WRITES',flush=True)
    print('STAGE38D_FINALIZATION_COMPLETE_NEXT_TASK_NEEDS_SCOPED_WINDOW',flush=True)


def main():
    try:
        run()
        return 0
    except Stop as err:
        print(str(err),file=sys.stderr,flush=True)
    except Exception as err:
        detail=str(err)
        if detail.startswith(('STAGE38_STOP_', 'STAGE38C_STOP_', 'STAGE38C_FINAL_STOP_')):
            print(detail,file=sys.stderr,flush=True)
        else:
            print('STAGE38D_STOP_'+type(err).__name__,file=sys.stderr,flush=True)
    return 2

if __name__=='__main__':
    raise SystemExit(main())
