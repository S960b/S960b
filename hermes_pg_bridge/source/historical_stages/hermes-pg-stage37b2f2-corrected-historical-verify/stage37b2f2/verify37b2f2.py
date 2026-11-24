#!/usr/bin/env python3
"""Safe corrected *retrospective* verification after Stage 37B2F rollback.

No writes, services, SDK or database connections. Never invokes original
Stage 37B2F 'verify' mode after rollback (it requires the wrapper live).
"""
from __future__ import annotations
import hashlib
import os
import re
import sys
from pathlib import Path
import archived_audit37b2f1 as audit

SOURCE_37B2F_SHA = '57687e228640b25323f9f578721ceffd2c92ad117c8bd724f112313414efeb90'
AUDIT_37B2F1_SHA = '44b605fe17012758a9d916e235db3120d7d8d5488601cebc92bc2c9e721d031d'


def corrected_pair_match(mt_pairs, pid, ticks, proc_start):
    """Correct logic for the live case, with strict PID and start-tick pairing.

    mt_pairs is [(pid:int,start_ticks:int), ...], not [str(pid), ...].
    Historical reports must never call proc_start for already-dead processes.
    """
    if type(pid) is not int or pid <= 1:
        return False
    if not isinstance(ticks, str) or re.fullmatch(r'[1-9][0-9]*', ticks) is None:
        return False
    if not isinstance(mt_pairs, (list, tuple)):
        return False
    valid = {(p,t) for item in mt_pairs
             if isinstance(item,(list,tuple)) and len(item)==2
             for p,t in [item]
             if type(p) is int and type(t) is int and p>1 and t>0}
    if (pid, int(ticks)) not in valid:
        return False
    try:
        return str(proc_start(pid)) == ticks
    except (OSError, ValueError, FileNotFoundError, ProcessLookupError):
        return False


def diagnostic_from_foreign_stop(exc):
    """Preserve a safe, known STOP code from another module; never leak raw errors."""
    raw = str(exc)
    if re.fullmatch(r'STAGE37B2(?:D|F)_STOP_[A-Z0-9_]{1,96}', raw):
        return raw
    return 'STAGE37B2F2_STOP_' + type(exc).__name__


def original_bug_pins(src_text):
    # Detect old regressions from their actual source, not from assumptions.
    return ('if str(pid) in set(mt) and proc_start(pid)==ticks' in src_text
            and 'except (FileNotFoundError,OSError,ValueError,TypeError,KeyError):continue' in src_text
            and "str(exc) if isinstance(exc,Stop) else 'STAGE37B2F_STOP_'+type(exc).__name__" in src_text)


def run():
    if os.getuid()!=os.geteuid() or os.getuid()==0 or os.environ.get('HOME')!='/home/kali':
        raise audit.AuditFailure('STAGE37B2F2_STOP_USER_CONTEXT')
    own=Path(__file__).with_name('archived_audit37b2f1.py')
    if hashlib.sha256(own.read_bytes()).hexdigest()!=AUDIT_37B2F1_SHA:
        raise audit.AuditFailure('STAGE37B2F2_STOP_REFERENCE_AUDIT_CHANGED')
    src=audit.safe_bytes(audit.SOURCE, private=True)
    if hashlib.sha256(src).hexdigest()!=SOURCE_37B2F_SHA:
        raise audit.AuditFailure('STAGE37B2F2_STOP_ORIGINAL_SOURCE_DRIFT')
    if not original_bug_pins(src.decode('utf8')):
        raise audit.AuditFailure('STAGE37B2F2_STOP_ORIGINAL_BUG_ANCHORS_CHANGED')
    if not corrected_pair_match([[3139983,39538797]],3139983,'39538797',lambda p:'39538797'):
        raise audit.AuditFailure('STAGE37B2F2_STOP_CORRECTED_PAIR_TEST')
    if corrected_pair_match([[3139983,39538798]],3139983,'39538797',lambda p:'39538797'):
        raise audit.AuditFailure('STAGE37B2F2_STOP_PID_REUSE_GUARD')
    if diagnostic_from_foreign_stop(RuntimeError('STAGE37B2D_STOP_MANAGED_LOCK_PRESENT'))!='STAGE37B2D_STOP_MANAGED_LOCK_PRESENT':
        raise audit.AuditFailure('STAGE37B2F2_STOP_DIAGNOSTIC_GUARD')
    # The full archived evidence validation reads pre-existing proof, pinned
    # candidate, original config, commit/restore metadata, and absent write permit.
    # It never treats a historical PID as alive.
    rc=audit.run()
    if rc!=0:
        raise audit.AuditFailure('STAGE37B2F2_STOP_HISTORICAL_AUDIT_FAILED')
    print('STAGE37B2F2_ORIGINAL_SET_MT_AND_FOREIGN_STOP_BUGS_PINNED')
    print('STAGE37B2F2_CORRECTED_PID_AND_START_TICKS_PAIR_LOGIC_TESTED')
    print('STAGE37B2F2_FOREIGN_STOP_CODE_PRESERVED')
    print('STAGE37B2F2_HISTORICAL_IMPORT_EVIDENCE_VALIDATED')
    print('STAGE37B2F2_LIVE_PID_COMPARISON_NOT_POSSIBLE_AFTER_ROLLBACK')
    print('STAGE37B2F2_CONFIG_RESTORED_READONLY_NO_NEW_TASKS_OR_PERMITS')
    print('STAGE37B2F2_IMPORT_PROOF_STAGE_CLOSED')
    return 0

if __name__=='__main__':
    try:
        raise SystemExit(run())
    except audit.AuditFailure as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(diagnostic_from_foreign_stop(exc),file=sys.stderr)
        raise SystemExit(2)
