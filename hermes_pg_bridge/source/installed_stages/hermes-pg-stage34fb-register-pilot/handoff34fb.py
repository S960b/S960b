#!/usr/bin/env python3
"""Stage 34F-B: register exactly one disabled managed pilot in MultiTool.

Only an atomic opencode.json replacement after ALL MT processes have exited.
NEVER starts SDK, changes PG state, touches the existing canary, or arms writes.
"""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
import time
from pathlib import Path

HOME = Path('/home/kali')
ROOT = HOME / 'hermes-pg-stage34fb-register-pilot'
CFG = HOME / '.config/gigatool/opencode.json'
PILOT_SRC = HOME / 'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CANARY_SRC = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
MANAGED_CLI = HOME / 'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py'
CREATE_RECEIPT = HOME / 'hermes-pg-stage34f-pilot-enroll/pilot-created.json'
CREATE_BINDING = HOME / 'hermes-pg-stage34f-pilot-enroll/pilot-binding-draft.json'
MANAGED_ARM = HOME / '.config/bridge-pg-v2/managed-writes-stage34d-armed'
PILOT_STATE = HOME / '.local/state/mt-pg-inbox-managed-stage34e'
CANARY_STATE = HOME / '.local/state/mt-pg-inbox-agent-stage33f/state.json'
BACKUP = ROOT / 'previous-opencode.json'
DRAFT = ROOT / 'proposed-opencode.json'
RECEIPT = ROOT / 'handoff-result.json'

PILOT = 'task_bddaadef89b7458fb7f8cf437dc0f023'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
REQUEST = 'stage34f_pilot_20261123_v1'
TITLE = 'Hermes PG v2 Managed Pilot Stage 34F (dispatch disabled)'
CFG7_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
PILOT_JS_SHA = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
CANARY_JS_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
MANAGED_CLI_SHA = 'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485'
WAIT_SECONDS = 900
STABLE_SECONDS = 12

class Stop(Exception): pass

def need(c, code):
    if not c:
        raise Stop(code)

def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def trusted(p: Path, private=False) -> bool:
    try:
        s = p.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                and s.st_nlink == 1 and not p.is_symlink()
                and (not private or not s.st_mode & 0o077))
    except OSError:
        return False

def sha_file(p: Path, private=False) -> str:
    need(trusted(p, private), 'UNTRUSTED_FILE_' + p.name)
    return sha_bytes(p.read_bytes())

def path_of(e):
    if isinstance(e, str): return e
    if (isinstance(e, list) and len(e) == 2 and isinstance(e[0], str)
            and isinstance(e[1], dict)): return e[0]
    raise Stop('INVALID_PLUGIN_ENTRY')

def pinned_sources():
    need(os.getuid() == os.geteuid() != 0 and
         pwd.getpwuid(os.geteuid()).pw_name == 'kali' and
         Path.home() == HOME and Path.cwd().resolve() == ROOT,
         'BAD_USER_OR_WORKDIR')
    for p, expected in ((PILOT_SRC, PILOT_JS_SHA), (CANARY_SRC, CANARY_JS_SHA),
                        (MANAGED_CLI, MANAGED_CLI_SHA)):
        need(sha_file(p) == expected, 'PINNED_SHA_CHANGED_' + p.name)
    need(not MANAGED_ARM.exists() and not MANAGED_ARM.is_symlink(), 'WRITE_ARM_ALREADY_PRESENT')
    for p in (CREATE_RECEIPT, CREATE_BINDING):
        need(trusted(p, private=True), 'PILOT_LOCAL_RECEIPT_UNTRUSTED')
    created = json.loads(CREATE_RECEIPT.read_text())
    binding = json.loads(CREATE_BINDING.read_text())
    need(created.get('task_id') == PILOT and created.get('request_id') == REQUEST and
         created.get('title') == TITLE and created.get('dispatch_enabled') is False and
         created.get('version') == 0 and created.get('policy_version') == 0,
         'PILOT_CREATED_RECEIPT_MISMATCH')
    need(binding.get('task_id') == PILOT and binding.get('candidate_sha256') == PILOT_JS_SHA and
         binding.get('registered_in_opencode') is False and
         binding.get('write_gate_armed') is False, 'PILOT_BINDING_RECEIPT_MISMATCH')
    need(PILOT != CANARY and PILOT_SRC != CANARY_SRC, 'PILOT_OVERLAPS_CANARY')
    # Candidate must be pinned active-capable, managed-only, and independently locked.
    src = PILOT_SRC.read_text('utf8')
    for marker in ('stage34ebPilotTaskIds(options)',
                   'const dryRun = false;',
                   'const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34e");',
                   'for (const taskId of Object.keys(state.inflight).sort())',
                   'managed_agent_cli34d.py', 'ag_dispatch_list',
                   'if (!ctx.taskIds || !ctx.taskIds.includes(dialog?.task_id)) continue;'):
        need(marker in src, 'PILOT_JS_SAFETY_MARKER_MISSING')
    for marker in ('bridge_pg.cli', 'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122',
                   'inflight_cleared_deleted'):
        need(marker not in src, 'UNSAFE_LEGACY_JS')

def valid_7_config(raw):
    obj = json.loads(raw)
    entries = obj.get('plugin')
    need(isinstance(entries, list) and len(entries) == 7, 'PLUGIN_COUNT_NOT_SEVEN')
    names = [path_of(e) for e in entries]
    need(names.count(str(CANARY_SRC)) == 1, 'CANARY_NOT_UNIQUE')
    need(str(PILOT_SRC) not in names, 'PILOT_ALREADY_REGISTERED')
    need(not any('mt-git-inbox' in p or 'mt-pg-inbox-canary' in p for p in names),
         'LEGACY_BRIDGE_PLUGIN_REINTRODUCED')
    can = entries[names.index(str(CANARY_SRC))]
    need(isinstance(can, list) and can[1].get('dry_run') is False and
         can[1].get('task_ids') == [CANARY], 'CANARY_CONFIG_DRIFT')
    return obj

def build_config(raw: bytes) -> bytes:
    obj = valid_7_config(raw)
    previous = json.loads(raw)
    obj['plugin'].append([str(PILOT_SRC), {
        'dry_run': False, 'tick_ms': 30000, 'task_ids': [PILOT],
    }])
    data = (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode('utf8')
    new = json.loads(data)
    need(len(new['plugin']) == 8 and new['plugin'][:-1] == previous['plugin'],
         'UNRELATED_PLUGINS_CHANGED')
    outside_old = dict(previous); outside_new = dict(new)
    del outside_old['plugin']; del outside_new['plugin']
    need(outside_old == outside_new and
         new['plugin'][-1][1]['task_ids'] == [PILOT] and
         new['plugin'][-1][0] == str(PILOT_SRC), 'PROPOSED_CONFIG_INVALID')
    return data

def safe_processes():
    found, unknown = [], []
    uid = os.geteuid()
    for d in Path('/proc').iterdir():
        if not d.name.isdecimal(): continue
        try:
            if d.stat().st_uid != uid: continue
            comm = (d / 'comm').read_text('utf8').strip()[:32]
            lower = comm.lower()
            mt = any(s in lower for s in ('multitool', 'gigatool', 'opencode'))
            if not mt and lower in ('node', 'bun', 'electron'):
                try:
                    command = (d / 'cmdline').read_bytes().lower()
                    mt = any(s in command for s in (b'multitool', b'gigatool', b'opencode'))
                except OSError:
                    unknown.append(int(d.name)); continue
            if mt:
                raw = (d/'stat').read_text('utf8')
                parts = raw[raw.rfind(')') + 2:].split()
                found.append({'pid': int(d.name), 'comm': comm,
                              'start_ticks': int(parts[19])})
        except FileNotFoundError: continue
        except (OSError, ValueError, IndexError): unknown.append(int(d.name))
    return sorted(found, key=lambda e:e['pid']), sorted(set(unknown))

def services_up():
    for name in ('bridge-pg-v2-ssh-shadow.service',
                 'hermes-pg-v2-stage33fb-cg.service',
                 'hermes-pg-tunnel-stage33fd.service',
                 'bridge-pg-v2-taskboard.service'):
        r = subprocess.run(['systemctl','--user','is-active','--quiet',name],
                           capture_output=True, timeout=8)
        need(r.returncode == 0, 'SERVICE_NOT_ACTIVE_' + name.split('.')[0])

def pg_guard():
    sys.path.insert(0, str(HOME / 'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT, Agent = actor_classes()
    cg, ag = ChatGPT(), Agent()
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_chatgpt', 'WRONG_CHATGPT_ROLE')
            cur.execute('SELECT bridge.creation_status(%s) AS r', (REQUEST,))
            cr = cur.fetchone()['r']
            need(isinstance(cr, dict) and cr.get('task_id') == PILOT and
                 cr.get('title') == TITLE and cr.get('state') == 'active',
                 'PILOT_CREATION_STATUS_CHANGED')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r', (PILOT,))
            detail = cur.fetchone()['r']
            need(isinstance(detail, dict) and
                 detail.get('registered') is True and
                 detail.get('dispatch_enabled') is False and
                 detail.get('policy_version') == 0 and
                 detail.get('version') == 0 and
                 detail.get('last_seq') == 0 and
                 detail.get('next_actor') == 'chatgpt' and
                 detail.get('state') == 'active', 'PILOT_UNEXPECTED_STATE')
            cur.execute('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS r', (0,50))
            rows = cur.fetchone()['r']
            need(isinstance(rows,list) and len(rows)==1 and rows[0].get('task_id') == PILOT and
                 rows[0].get('dispatch_enabled') is False, 'REGISTRY_NOT_EXACTLY_DISABLED_PILOT')
            cur.execute('SELECT bridge.dialog_metadata(%s) AS r', (CANARY,))
            can = cur.fetchone()['r']
            need(can and can.get('state') == 'active' and can.get('last_seq') == 6,
                 'CANARY_CHANGED')
            for seq in (5,6):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS r', (CANARY,seq))
                need(cur.fetchone()['r'].get('status')=='delivered','CANARY_RECEIPT_CHANGED')
    with ag._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_agent', 'WRONG_AGENT_ROLE')
            cur.execute('SELECT bridge.ag_dispatch_list(%s,%s) AS r', (0,50))
            need(cur.fetchone()['r'] == [], 'MANAGED_DISPATCH_NOT_EMPTY')
    need(not MANAGED_ARM.exists() and not MANAGED_ARM.is_symlink(),
         'MANAGED_WRITES_ARMED')

def durable_state_guard():
    # No new candidate may have durable work before first registration.
    if PILOT_STATE.exists() or PILOT_STATE.is_symlink():
        need(PILOT_STATE.is_dir() and not PILOT_STATE.is_symlink() and
             PILOT_STATE.stat().st_uid == os.geteuid(), 'PILOT_STATE_DIR_UNTRUSTED')
        state = PILOT_STATE / 'state.json'
        if state.exists() or state.is_symlink():
            need(trusted(state, private=True), 'PILOT_STATE_UNTRUSTED')
            obj = json.loads(state.read_text('utf8'))
            need(isinstance(obj, dict) and obj.get('inflight') == {} and
                 obj.get('seen') == {} and obj.get('sessions') == {},
                 'PILOT_PREEXISTING_WORK')
    need(trusted(CANARY_STATE, private=True), 'CANARY_DURABLE_STATE_UNTRUSTED')
    state = json.loads(CANARY_STATE.read_text('utf8'))
    need(isinstance(state, dict) and state.get('inflight') == {} and
         state.get('seen', {}).get(CANARY) == 5, 'CANARY_DURABLE_CURSOR_CHANGED')
    return sha_file(CANARY_STATE)

def atom_write(p: Path, data: bytes, replace=False):
    tmp = p.with_name('.' + p.name + '.tmp-' + str(os.getpid()))
    fd = os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        if replace:
            os.replace(tmp, p)
        else:
            need(not p.exists() and not p.is_symlink(), 'OUTPUT_ALREADY_EXISTS')
            os.link(tmp, p); os.unlink(tmp)
        fd = os.open(p.parent, os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)

def prepared():
    pinned_sources()
    need(trusted(BACKUP, private=True) and trusted(DRAFT, private=True),
         'PREPARED_FILES_MISSING')
    need(sha_file(BACKUP) == CFG7_SHA, 'BACKUP_MISMATCH')
    d = build_config(BACKUP.read_bytes())
    need(DRAFT.read_bytes() == d, 'PREPARED_BYTES_CHANGED')
    return d

def prepare():
    pinned_sources(); services_up(); pg_guard(); durable_state_guard()
    need(sha_file(CFG) == CFG7_SHA, 'ORIGINAL_CONFIG_CHANGED')
    need(not any(p.exists() or p.is_symlink() for p in (BACKUP,DRAFT,RECEIPT)),
         'PREPARATION_ALREADY_ATTEMPTED')
    original = CFG.read_bytes()
    draft = build_config(original)
    atom_write(BACKUP, original)
    atom_write(DRAFT, draft)
    need(sha_file(CFG) == CFG7_SHA, 'CONFIG_CHANGED_DURING_PREPARE')
    print('STAGE34FB_PRIVATE_BACKUP_AND_EIGHT_PLUGIN_DRAFT_READY')
    print('STAGE34FB_CANARY_AND_ONE_PILOT_BOUND_TO_DISJOINT_TASK_IDS')
    print('STAGE34FB_PILOT_DISABLED_NO_SDK_AND_MANAGED_WRITE_GATE_ABSENT')
    print('STAGE34FB_PROPOSED_CONFIG_SHA256=' + sha_bytes(draft))

def receipt(outcome, code, digest=''):
    atom_write(RECEIPT, (json.dumps({'outcome':outcome,'code':code,
                                     'config_sha256':digest},sort_keys=True)+'\n').encode(),replace=True)

def worker():
    draft = prepared()
    need(not RECEIPT.exists(), 'WORKER_ALREADY_ATTEMPTED')
    need(sha_file(CFG) == CFG7_SHA, 'ORIGINAL_CONFIG_CHANGED')
    print('STAGE34FB_WAIT_FOR_ALL_MULTITOOL_WINDOWS_TO_CLOSE', flush=True)
    start = time.monotonic(); quiet = None
    while time.monotonic() - start < WAIT_SECONDS:
        live, unknown = safe_processes()
        if live or unknown:
            quiet = None
        elif quiet is None:
            quiet = time.monotonic()
        elif time.monotonic() - quiet >= STABLE_SECONDS:
            break
        time.sleep(2)
    else:
        receipt('blocked','MULTITOOL_PROCESSES_NOT_QUIET')
        raise Stop('MULTITOOL_PROCESSES_NOT_QUIET')
    live, unknown = safe_processes()
    need(not live and not unknown, 'MULTITOOL_REAPPEARED')
    services_up(); pg_guard()
    old_state_hash = durable_state_guard()
    need(sha_file(CFG) == CFG7_SHA, 'CONFIG_CHANGED_BEFORE_COMMIT')
    live, unknown = safe_processes()
    need(not live and not unknown, 'MULTITOOL_REAPPEARED_AT_COMMIT')
    need(durable_state_guard() == old_state_hash, 'DURABLE_STATE_CHANGED_BEFORE_COMMIT')
    atom_write(CFG,draft,replace=True)
    os.chmod(CFG,0o600)
    need(sha_file(CFG)==sha_bytes(draft), 'COMMITTED_CONFIG_SHA_MISMATCH')
    receipt('committed','ONE_DISABLED_MANAGED_PILOT_REGISTERED',sha_bytes(draft))
    print('STAGE34FB_EIGHT_PLUGIN_CONFIG_ATOMICALLY_INSTALLED', flush=True)
    print('STAGE34FB_NO_DB_MUTATIONS_NO_TASK_NO_SDK_NO_WRITE_ARM', flush=True)

def verify():
    draft = prepared()
    need(sha_file(CFG)==sha_bytes(draft), 'PILOT_PLUGIN_NOT_REGISTERED')
    need(trusted(RECEIPT, private=True), 'RECEIPT_MISSING')
    obj = json.loads(RECEIPT.read_text())
    need(obj == {'outcome':'committed','code':'ONE_DISABLED_MANAGED_PILOT_REGISTERED',
                 'config_sha256':sha_bytes(draft)}, 'RECEIPT_NOT_COMMITTED')
    services_up(); pg_guard(); durable_state_guard()
    print('STAGE34FB_EXACT_ONE_DISABLED_MANAGED_PILOT_REGISTERED')
    print('STAGE34FB_EIGHT_PLUGINS_CANARY_AND_PILOT_DISJOINT')
    print('STAGE34FB_MANAGED_DISPATCH_STILL_DISABLED_AND_NO_TASK')
    print('STAGE34FB_VERIFY_COMPLETE_NO_SDK_NO_TUNNEL_SWITCH')
    print(json.dumps({'stage':'34F-B','pilot_task_id':PILOT,
       'config_sha256':sha_bytes(draft),'pilot_js_sha256':PILOT_JS_SHA,
       'plugin_count':8,'dispatch_enabled':False,'last_seq':0,
       'sdk_called_by_package':False,'database_writes_by_package':False},sort_keys=True))

def rollback():
    draft = prepared()
    need(sha_file(CFG)==sha_bytes(draft),'ACTIVE_CONFIG_NOT_EXPECTED_PILOT')
    live,unknown=safe_processes()
    need(not live and not unknown,'ALL_MULTITOOL_PROCESSES_MUST_BE_CLOSED')
    services_up(); pg_guard(); durable_state_guard()
    atom_write(CFG,BACKUP.read_bytes(),replace=True)
    os.chmod(CFG,0o600)
    receipt('rolled_back','RESTORED_SEVEN_PLUGIN_CONFIG',CFG7_SHA)
    print('STAGE34FB_ROLLBACK_SEVEN_PLUGIN_CONFIG_RESTORED')

def status():
    pinned_sources()
    live, unknown = safe_processes()
    current = sha_file(CFG)
    next_sha = sha_bytes(DRAFT.read_bytes()) if trusted(DRAFT,private=True) else None
    out = {'stage':'34F-B','pilot_task_id':PILOT,
       'config_sha256':current,'seven_plugins_active':current==CFG7_SHA,
       'eight_plugins_active':current==next_sha if next_sha else False,
       'prepared':next_sha is not None,
       'multitool_processes':live,'unknown_process_pids':unknown,
       'handoff_receipt':json.loads(RECEIPT.read_text()) if trusted(RECEIPT,private=True) else None}
    print(json.dumps(out,sort_keys=True,ensure_ascii=False,indent=2))

def main(argv):
    try:
        need(len(argv)==2 and argv[1] in ('prepare','worker','verify','status','rollback'),
             'UNSUPPORTED_MODE')
        if argv[1]=='prepare':prepare()
        elif argv[1]=='worker':worker()
        elif argv[1]=='verify':verify()
        elif argv[1]=='status':status()
        else:rollback()
        return 0
    except Exception as e:
        code = str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE34FB_STOP_'+code[:110],file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main(sys.argv))
