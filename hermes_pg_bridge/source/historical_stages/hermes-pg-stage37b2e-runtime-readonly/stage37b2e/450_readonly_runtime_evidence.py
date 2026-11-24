#!/usr/bin/env python3
"""Stage 37B2E: non-mutating inspection; absence of evidence is not a failure."""
import hashlib
import json
import os
import re
from pathlib import Path

HOME = Path.home()
CONFIG = HOME/'.config/gigatool/opencode.json'
CANDIDATE = HOME/'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
EXPECTED_PREFIX='f46c3d4c2a46'
TASK='task_b6c26aa18220412bafdbf32393f16568'
STATE=HOME/'.local/state/mt-pg-inbox-managed-stage34e/state.json'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
# Candidate name is distinctive. Only an explicit successful-loading log is interpreted as evidence.
CANDIDATE_NAME=CANDIDATE.name
LOG_DIRS=[HOME/'.local/share/opencode/log', HOME/'.local/state/opencode/log', HOME/'.cache/opencode/log',HOME/'.local/state/mt-pg-inbox-managed-stage34e']

def report(k,v): print(f'STAGE37B2E_{k} {v}')

def valid_trusted(p):
    try:
        st=p.lstat()
        return p.is_file() and not p.is_symlink() and st.st_size<4_000_000
    except OSError: return False

def active_process_count():
    count=0
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            raw=(p/'cmdline').read_bytes()[:8192].replace(b'\0',b' ').decode('utf-8','replace').lower()
            if ('gigatool' in raw or 'opencode' in raw) and 'stage37b2e' not in raw and 'python' not in raw:
                count+=1
        except (OSError,PermissionError): continue
    return count

def logs_evidence():
    found=0; checked=0
    # bounded read of user-owned log files; content is not printed
    for root in LOG_DIRS:
        if not root.is_dir() or root.is_symlink(): continue
        for path in sorted(root.glob('**/*')):
            if checked >=100: break
            try:
                if not valid_trusted(path): continue
                if path.suffix not in ('.log','.jsonl','.txt') and path.name not in ('events','log'):continue
                checked+=1
                content=path.read_text('utf-8',errors='replace')[-1_000_000:]
                for line in content.splitlines():
                    if CANDIDATE_NAME not in line: continue
                    if re.search(r'\b(loaded|imported|registered|initialized|plugin_loaded)\b',line,re.I) and not re.search(r'\b(fail|error|denied|unavailable)\b',line,re.I):
                        found+=1
            except (OSError,UnicodeError,PermissionError): continue
    return checked,found

def main():
    try:
        data=CONFIG.read_bytes()
        config=json.loads(data)
        plugins=config.get('plugin',[])
        names=[p if isinstance(p,str) else p[0] if isinstance(p,list) and p else '' for p in plugins]
        sha=hashlib.sha256(data).hexdigest()
        report('CONFIG_SHA_PREFIX',sha[:12])
        assert sha.startswith(EXPECTED_PREFIX), 'config_hash_unexpected'
        assert len(names)==8 and names.count(str(CANDIDATE))==1, 'candidate_not_unique'
        assert valid_trusted(CANDIDATE),'candidate_missing'
        report('CONFIG_CANDIDATE_REGISTERED_EXACTLY_ONCE','YES')
        report('CANDIDATE_SHA_PREFIX',hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()[:12])
        report('WRITE_PERMIT_PRESENT',PERMIT.exists())
        assert not PERMIT.exists(),'write_permit_present_stop'
        report('MULTITOOL_PROCESS_COUNT',active_process_count())
        if STATE.exists() and valid_trusted(STATE):
            obj=json.loads(STATE.read_text())
            report('LOCAL_SEEN_SEQ1',obj.get('seen',{}).get(TASK)==1)
            report('LOCAL_INFLIGHT_EMPTY',not obj.get('inflight'))
        else: report('LOCAL_STATE_VERIFIED','NO')
        checked,found=logs_evidence()
        report('USER_LOG_FILES_SCANNED',checked)
        report('SUCCESSFUL_RUNTIME_IMPORT_LOG_LINES',found)
        if found:report('IMPORT_LOG_EVIDENCE','FOUND_CHECK_FRESHNESS_AND_CONTEXT')
        else:report('IMPORT_LOG_EVIDENCE','NOT_FOUND_DO_NOT_INFER_PLUGIN_NOT_LOADED')
        report('READONLY_NO_DB_WRITE_NO_CONFIG_CHANGE','YES')
        report('NEXT_STEP','IF_IMPORT_NOT_PROVEN_USE_INSTRUMENTED_STARTUP_OR_CONTROLLED_TEST')
    except Exception as exc:
        report('STOP',type(exc).__name__+':'+str(exc)[:90]);raise SystemExit(2)
if __name__=='__main__':main()
