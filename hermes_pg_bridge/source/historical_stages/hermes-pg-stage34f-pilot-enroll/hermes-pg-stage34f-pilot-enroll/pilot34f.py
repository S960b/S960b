#!/usr/bin/env python3
"""Stage 34F-A: one disabled managed pilot, real bridge_chatgpt login.

No task/message, policy activation, SDK, claim/ack, or plugin registration.
One-shot attempt journal protects ambiguous commit from blind replay.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage34f-pilot-enroll')
ACTIVE_CONFIG = Path('/home/kali/.config/gigatool/opencode.json')
ACTIVE_PLUGIN = Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
PILOT_CANDIDATE = Path('/home/kali/hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs')
WRITE_GATE = Path('/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed')
CONFIG_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
ACTIVE_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
PILOT_SHA = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
REQUEST_ID = 'stage34f_pilot_20261123_v1'
TITLE = 'Hermes PG v2 Managed Pilot Stage 34F (dispatch disabled)'
ATTEMPT = ROOT / '.create-attempted.json'
RECEIPT = ROOT / 'pilot-created.json'
BINDING = ROOT / 'pilot-binding-draft.json'
ROLE_IMPORT = '/home/kali/hermes-pg-stage33e-shadow-clients'

class Refused(Exception):
    pass

def need(ok: bool, code: str) -> None:
    if not ok:
        raise Refused(code)

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def private_regular(p: Path) -> bool:
    try:
        s = p.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and
                s.st_nlink == 1 and not p.is_symlink())
    except OSError:
        return False

def pin_environment() -> tuple[type, type]:
    need(os.geteuid() == os.getuid() != 0 and Path.home() == Path('/home/kali') and
         Path.cwd().resolve() == ROOT, 'WRONG_EXECUTION_CONTEXT')
    for path, expected in ((ACTIVE_CONFIG, CONFIG_SHA), (ACTIVE_PLUGIN, ACTIVE_SHA),
                           (PILOT_CANDIDATE, PILOT_SHA)):
        need(private_regular(path) and sha(path) == expected,
             'PINNED_RUNTIME_CHANGED_' + path.name)
    need(not WRITE_GATE.exists() and not WRITE_GATE.is_symlink(), 'MANAGED_WRITE_GATE_PRESENT')
    obj = json.loads(ACTIVE_CONFIG.read_text(encoding='utf8'))
    entries = obj.get('plugin')
    need(isinstance(entries, list) and len(entries) == 7, 'ACTIVE_PLUGIN_LIST_CHANGED')
    names = [v if isinstance(v, str) else v[0] if isinstance(v, list) and
             len(v) == 2 and isinstance(v[0], str) else None for v in entries]
    need(names.count(str(ACTIVE_PLUGIN)) == 1 and str(PILOT_CANDIDATE) not in names,
         'PILOT_ALREADY_REGISTERED')
    need(not any(n and ('mt-git-inbox' in n or 'managed-stage34' in n)
                     for n in names), 'UNEXPECTED_BRIDGE_PLUGIN_PRESENT')
    active_entry = entries[names.index(str(ACTIVE_PLUGIN))]
    need(isinstance(active_entry, list) and len(active_entry) == 2 and
         active_entry[1].get('dry_run') is False and
         active_entry[1].get('task_ids') == [CANARY], 'CANARY_FILTER_CHANGED')
    sys.path.insert(0, ROLE_IMPORT)
    from actor_import33e import actor_classes
    return actor_classes()

def read_only_inventory(cg, ag) -> list[dict]:
    # All data access via SECURITY DEFINER actor functions and pg_catalog.
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS u, current_user AS c')
            need(cur.fetchone() == {'u': 'bridge_chatgpt', 'c': 'bridge_chatgpt'},
                 'CHATGPT_LOGIN_NOT_ISOLATED')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
            need(cur.fetchone()['n'] == 42, 'FUNCTION_COUNT_NOT_42')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
            need(cur.fetchone()['n'] == 4, 'TABLE_COUNT_NOT_4')
            cur.execute('SELECT bridge.cg_list_managed_dialogs(0,50) AS r')
            rows = cur.fetchone()['r']
            need(isinstance(rows, list), 'MANAGED_LIST_SHAPE_CHANGED')
            cur.execute('SELECT bridge.cg_managed_status(%s) AS r', (CANARY,))
            need(cur.fetchone()['r'].get('registered') is False, 'CANARY_IN_MANAGED_REGISTRY')
            for seq in (5, 6):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS r', (CANARY, seq))
                need(cur.fetchone()['r'].get('status') == 'delivered', 'CANARY_DELIVERY_CHANGED')
            cur.execute('SELECT bridge.dialog_metadata(%s) AS r', (CANARY,))
            can = cur.fetchone()['r']
            need(can and can.get('last_seq') == 6 and can.get('state') == 'active',
                 'CANARY_STATE_CHANGED')
    with ag._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS u, current_user AS c')
            need(cur.fetchone() == {'u': 'bridge_agent', 'c': 'bridge_agent'},
                 'AGENT_LOGIN_NOT_ISOLATED')
            cur.execute('SELECT bridge.ag_dispatch_list(0,50) AS r')
            need(cur.fetchone()['r'] == [], 'MANAGED_DISPATCH_NOT_EMPTY')
    return rows

def creation_receipt(cg, tid: str) -> dict:
    need(isinstance(tid, str) and re.fullmatch(r'task_[0-9a-f]{32}', tid) and
         tid != CANARY, 'PILOT_TASK_ID_INVALID')
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT bridge.creation_status(%s) AS r', (REQUEST_ID,))
            cr = cur.fetchone()['r']
            need(cr and cr.get('task_id') == tid and cr.get('title') == TITLE and
                 cr.get('initiator') == 'chatgpt' and cr.get('state') == 'active',
                 'CREATION_RECEIPT_MISMATCH')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r', (tid,))
            detail = cur.fetchone()['r']
    need(detail and detail.get('registered') is True and
         detail.get('dispatch_enabled') is False and
         detail.get('policy_version') == 0 and detail.get('version') == 0 and
         detail.get('last_seq') == 0 and detail.get('next_actor') == 'chatgpt' and
         detail.get('state') == 'active' and detail.get('title') == TITLE,
         'PILOT_POLICY_OR_STATE_CHANGED')
    return detail

def write_atomic_no_replace(path: Path, obj: dict) -> None:
    data = (json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    dfd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(dfd)
    finally: os.close(dfd)

def expected_binding(tid: str) -> dict:
    return {'task_id': tid, 'candidate_path': str(PILOT_CANDIDATE),
            'candidate_sha256': PILOT_SHA, 'dispatch_enabled': False,
            'pilot_only': True, 'registered_in_opencode': False,
            'sdk_enabled': False, 'write_gate_armed': False}

def preflight(cg, ag) -> None:
    need(not ATTEMPT.exists() and not RECEIPT.exists() and not BINDING.exists(),
         'PILOT_CREATION_ALREADY_ATTEMPTED_USE_VERIFY_ONLY')
    rows = read_only_inventory(cg, ag)
    need(rows == [], 'MANAGED_REGISTRY_NOT_EMPTY')
    need(cg.creation_status(REQUEST_ID) is None, 'REQUEST_ID_ALREADY_PRESENT')
    print('STAGE34F_READ_ONLY_PREFLIGHT_EMPTY_REGISTRY_AND_CANARY_OK')
    print('STAGE34F_ACTOR_LOGINS_AND_42_FUNCTIONS_VERIFIED')
    print('STAGE34F_NO_ACTIVE_MANAGED_DISPATCH')
    print('STAGE34F_PREPARE_NO_SDK_NO_CONFIG_CHANGES')

def create_once(cg, ag) -> None:
    preflight(cg, ag)
    write_atomic_no_replace(ATTEMPT, {'request_id': REQUEST_ID, 'title': TITLE,
                                       'attempted': True, 'retry_without_reconcile_forbidden': True})
    print('STAGE34F_CREATE_ATTEMPT_JOURNALED_NO_RETRY', flush=True)
    # Existing PostgreSQL actor function atomically inserts dialog + managed row.
    # DB context manager commits at exit. Do not repeat after a lost commit reply.
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_chatgpt', 'WRITE_LOGIN_NOT_CHATGPT')
            cur.execute('SELECT bridge.cg_create_managed_dialog(%s,%s) AS r', (REQUEST_ID, TITLE))
            created = cur.fetchone()['r']
            need(isinstance(created, dict) and created.get('created') is True and
                 created.get('managed') is True and created.get('dispatch_enabled') is False,
                 'MANAGED_CREATION_RESULT_UNEXPECTED')
            tid = created.get('task_id')
            need(isinstance(tid,str) and re.fullmatch(r'task_[0-9a-f]{32}',tid),
                 'MANAGED_CREATION_ID_INVALID')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r', (tid,))
            detail = cur.fetchone()['r']
            need(detail.get('registered') is True and
                 detail.get('dispatch_enabled') is False and
                 detail.get('policy_version') == 0 and detail.get('version') == 0 and
                 detail.get('last_seq') == 0 and detail.get('next_actor') == 'chatgpt',
                 'MANAGED_CREATION_IMMEDIATE_GUARD_FAILED')
    # COMMITTED above. All remaining operations are read-only / local receipt.
    verify_detail = creation_receipt(cg, tid)
    rows = read_only_inventory(cg, ag)
    need(len(rows) == 1 and rows[0].get('task_id') == tid and
         rows[0].get('dispatch_enabled') is False, 'POST_COMMIT_MANAGED_ROW_MISMATCH')
    write_atomic_no_replace(RECEIPT, {'request_id': REQUEST_ID, 'task_id': tid,
                                      'title': TITLE, 'created': True,
                                      'dispatch_enabled': False, 'version': 0,
                                      'policy_version': 0})
    write_atomic_no_replace(BINDING, expected_binding(tid))
    print('STAGE34F_ONE_MANAGED_PILOT_COMMITTED_DISABLED')
    print('STAGE34F_PILOT_TASK_ID=' + tid)
    print('STAGE34F_PILOT_POLICY_VERSION_0_AND_LAST_SEQ_0')
    print('STAGE34F_NO_TASK_NO_CLAIM_NO_ACK_NO_SDK')
    print('STAGE34F_PENDING_PLUGIN_REGISTRATION_NOT_ACTIVATED')

def verify_only(cg, ag) -> None:
    rows = read_only_inventory(cg, ag)
    cr = cg.creation_status(REQUEST_ID)
    if cr is None:
        need(rows == [], 'REGISTRY_ROW_WITHOUT_EXPECTED_PILOT')
        print('STAGE34F_VERIFY_ONLY_PILOT_NOT_COMMITTED')
        return
    need(cr.get('title') == TITLE, 'REQUEST_ID_TITLE_COLLISION')
    tid = cr.get('task_id')
    creation_receipt(cg, tid)
    need(len(rows) == 1 and rows[0].get('task_id') == tid and
         rows[0].get('dispatch_enabled') is False and
         rows[0].get('policy_version') == 0 and rows[0].get('version') == 0 and
         rows[0].get('last_seq') == 0, 'REGISTRY_NOT_ONE_DISABLED_PILOT')
    for path, expected in ((RECEIPT, {'task_id': tid, 'request_id': REQUEST_ID}),
                           (BINDING, {'task_id': tid, 'candidate_sha256': PILOT_SHA})):
        if path.exists():
            need(private_regular(path) and stat.S_IMODE(path.stat().st_mode) == 0o600,
                 'UNTRUSTED_LOCAL_JOURNAL')
            obj = json.loads(path.read_text(encoding='utf8'))
            need(all(obj.get(k) == v for k,v in expected.items()), 'LOCAL_JOURNAL_MISMATCH')
    print('STAGE34F_ONE_DISABLED_MANAGED_DIALOG_VERIFIED')
    print('STAGE34F_PILOT_TASK_ID=' + tid)
    print('STAGE34F_ZERO_DISPATCH_AND_NO_TASK_MESSAGES')
    print('STAGE34F_CANARY_AND_LIVE_CONFIG_UNCHANGED')
    print('STAGE34F_READ_ONLY_VERIFICATION_COMPLETE')

def main(argv: list[str]) -> int:
    try:
        need(len(argv) == 2 and argv[1] in ('preflight','create-once','verify-only'),
             'INVALID_MODE')
        C,A = pin_environment()
        cg,ag = C(),A()
        if argv[1] == 'preflight': preflight(cg,ag)
        elif argv[1] == 'create-once': create_once(cg,ag)
        else: verify_only(cg,ag)
        return 0
    except Exception as e:
        code = str(e) if isinstance(e,Refused) else 'UNEXPECTED_' + type(e).__name__
        print('STAGE34F_STOP_' + code[:110], file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
