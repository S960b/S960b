#!/usr/bin/env python3
"""Narrow fail-closed patch for audited F04; no DB or service mutations."""
from __future__ import annotations
import hashlib
import os
import pathlib
import stat
import sys

EXPECTED = '06be5ea765f0a47204fd2d039a0ea7fb03283148102b35c66cb4dab64f1b2660'
DEFAULT = pathlib.Path('/home/kali/hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs')
START = '  try {\n    await send(promptDirectory);\n  } catch (e) {\n'
END = "  // No 'seen' receipt until PostgreSQL confirms this specific lease."
REPLACEMENT = '''  try {
    await send(promptDirectory);
  } catch (e) {
    // F04: a transport/JSON/404 error after invoking prompt is not proof
    // that the first request or any of its effects were rejected. Session
    // absence checked later cannot make that operation safe to replay.
    // Keep the already durable inflight/lease for report/ACK reconciliation.
    ctx.log("sdk_prompt_outcome_unknown_hold " + key + " seq=" + message.seq);
    throw e;
  }
'''

def patch(src: bytes) -> bytes:
    assert hashlib.sha256(src).hexdigest() == EXPECTED, 'SOURCE_SHA_MISMATCH_STOP'
    old = src.decode('utf-8')
    assert old.count(START) == 1, 'PROMPT_CATCH_START_NOT_UNIQUE'
    assert old.count(END) == 1, 'PROMPT_CATCH_END_NOT_UNIQUE'
    a = old.index(START)
    b = old.index(END, a)
    removed = old[a:b]
    assert 'await send(recoveryDirectory)' in removed, 'F04_RETRY_NOT_FOUND'
    assert 'state.inflight[key].phase' in removed, 'F04_STATE_CONTEXT_MISMATCH'
    new = old[:a] + REPLACEMENT + old[b:]
    assert new.count('await send(promptDirectory)') == 1, 'F04_PRIMARY_PROMPT_COUNT'
    assert 'await send(recoveryDirectory)' not in new, 'F04_RETRY_STILL_PRESENT'
    assert 'stage37b2cWriteIntent' in new and 'reconcileAnsweredInflight' in new
    return new.encode('utf-8')

if __name__ == '__main__':
    assert len(sys.argv) in (1,3), 'USAGE patch_f04.py [SOURCE OUTPUT]'
    source = pathlib.Path(sys.argv[1]) if len(sys.argv) == 3 else DEFAULT
    target = pathlib.Path(sys.argv[2]) if len(sys.argv) == 3 else source.with_name(source.name + '.f04-candidate.mjs')
    assert source.is_file() and not source.is_symlink(), 'SOURCE_PATH_UNSAFE'
    st = source.lstat()
    assert stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1, 'SOURCE_METADATA_UNSAFE'
    assert not target.exists() and not target.is_symlink(), 'CANDIDATE_ALREADY_EXISTS_STOP'
    out = patch(source.read_bytes())
    target.write_bytes(out)
    target.chmod(0o600)
    print('STAGE42_F04_SOURCE_SHA', EXPECTED)
    print('STAGE42_F04_CANDIDATE_SHA', hashlib.sha256(out).hexdigest())
    print('STAGE42_F04_CANDIDATE', str(target))
    print('STAGE42_F04_NO_INSTALL_NO_RESTART')
