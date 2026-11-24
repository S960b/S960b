#!/usr/bin/env python3
"""Static, read-only proof: Stage34EB allowlist skipped the old mocked ctx."""
from pathlib import Path
import hashlib, os, stat, sys
ROOT=Path('/home/kali/hermes-pg-stage37b2c-claim-intent')
SRC=Path('/home/kali/hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs')
DEST=ROOT/'mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
OLD=ROOT/'test_candidate37b2c.cjs'
ALLOW='if (!ctx.taskIds || !ctx.taskIds.includes(dialog?.task_id)) continue;'
EXPECTED_SRC='b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'

def fail(reason): print('STAGE37B2C1_STOP_'+reason, file=sys.stderr);raise SystemExit(2)
def read_private(path):
    try:
        st=path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.geteuid() or st.st_mode & 0o077:fail('UNTRUSTED_'+path.name)
        return path.read_text('utf8')
    except OSError:fail('MISSING_'+path.name)

def main():
    old=read_private(OLD)
    src=read_private(SRC)
    candidate=read_private(DEST)
    if hashlib.sha256(src.encode()).hexdigest()!=EXPECTED_SRC:fail('PINNED_SOURCE_CHANGED')
    if src.count(ALLOW)!=1 or candidate.count(ALLOW)!=1:fail('STAGE34EB_FILTER_MISSING')
    if 'const ctx={paths:{},log:' not in old or 'taskIds:' in old:fail('OLD_HARNESS_DIFFERENT')
    if 'assert(log.some(x=>x.startsWith(\'stage37b2c_uncertain_claim_hold\')))' not in old:fail('OLD_EXPECTATION_DIFFERENT')
    if candidate.index(ALLOW)>candidate.index('if (stage37b2cIntentPresent(dialog.task_id)) {'):fail('FILTER_ORDER_CHANGED')
    print('STAGE37B2C1_SOURCE_ALLOWLIST_GATE_CONFIRMED')
    print('STAGE37B2C1_OLD_HARNESS_OMITTED_CTX_TASKIDS_CONFIRMED')
    print('STAGE37B2C1_OLD_STOP_WAS_EXPECTED_FOR_EMPTY_ALLOWLIST')

if __name__=='__main__':main()
