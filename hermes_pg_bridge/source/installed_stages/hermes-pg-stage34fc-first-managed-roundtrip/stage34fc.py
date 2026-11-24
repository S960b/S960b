#!/usr/bin/env python3
"""Hermes PG v2 Stage 34F-C: exactly one managed pilot turn.

Authoritative writes use role-bound SECURITY DEFINER cg_* functions only.
No broad bridge_app access, Git, schema changes, MCP cutover, or SDK calls here.
Do NOT reissue after a lost COMMIT response: use read-only verify.
"""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage34fc-first-managed-roundtrip')
HOME = Path('/home/kali')
ACTIVE_CONFIG = HOME / '.config/gigatool/opencode.json'
PILOT_JS = HOME / 'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CANARY_JS = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
MANAGED_CLI = HOME / 'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py'
PILOT_CREATED = HOME / 'hermes-pg-stage34f-pilot-enroll/pilot-created.json'
PILOT_STATE = HOME / '.local/state/mt-pg-inbox-managed-stage34e/state.json'
PILOT_LOG = HOME / '.local/state/mt-pg-inbox-managed-stage34e/inbox.log'
GATE = HOME / '.config/bridge-pg-v2/managed-writes-stage34d-armed'
ARM_CONTENT = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
ACTIVATION_ATTEMPT = ROOT / '.activation-attempted.json'
ACTIVATION_RECEIPT = ROOT / 'activation-receipt.json'
REPORT_ACK_ATTEMPT = ROOT / '.report-ack-attempted.json'
REPORT_ACK_RECEIPT = ROOT / 'report-ack-receipt.json'
CONFIG_SHA = '4b32ac0fe83c902f5a7e2e5a749b104506987e1380aadc863ff15ca57b7786c3'
PILOT_JS_SHA = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
CANARY_JS_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
MANAGED_CLI_SHA = 'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485'
PILOT = 'task_bddaadef89b7458fb7f8cf437dc0f023'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
REQUEST = 'stage34f_pilot_20261123_v1'
TITLE = 'Hermes PG v2 Managed Pilot Stage 34F (dispatch disabled)'
TASK_MID = 'pgv2-mg-34fc-pilot-task-20261123-v1'
REPORT_MID = 'pgv2-mg-' + PILOT[5:] + '-1-report'
REPORT_BODY = 'PGV2_MANAGED_PILOT_ROUNDTRIP_OK_20261123'
REPORT_FILE = HOME / '.local/state/mt-pg-inbox-managed-stage34e/pilot-report-stage34fc.txt'
TASK_BODY = (
    'Stage 34F-C: one-time managed PostgreSQL transport canary.\n'
    'This is a connectivity check only; do not modify services, configuration or database policy.\n'
    'Do not access Git, do not run unrelated commands or other tasks.\n'
    'Write the following exact one-line UTF-8 report without extra whitespace or newline:\n'
    + REPORT_BODY + '\n'
    'Use a local private report file at ' + str(REPORT_FILE) + ' (mode 0600),\n'
    'then submit the report ONCE using the managed_agent_cli34d.py instructions\n'
    'from this SDK request. The report message_id must be exactly the deterministic\n'
    'value supplied by the managed Inbox prompt. If outcome is unknown, check status.\n'
)
ACTOR_IMPORT = '/home/kali/hermes-pg-stage33e-shadow-clients'
CONSUMER = 'chatgpt-managed-pilot-34fc'

class Refused(Exception):
    pass

def need(cond, code):
    if not cond:
        raise Refused(code)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def trusted(path, private=False):
    try:
        s = path.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                and s.st_nlink == 1 and not path.is_symlink()
                and (not private or (s.st_mode & 0o077) == 0))
    except OSError:
        return False

def plugin_path(entry):
    if isinstance(entry, str): return entry
    if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str) and isinstance(entry[1], dict):
        return entry[0]
    raise Refused('INVALID_PLUGIN_ENTRY')

def pin_local(require_gate_absent=False):
    need(os.getuid() == os.geteuid() != 0 and
         pwd.getpwuid(os.geteuid()).pw_name == 'kali' and
         Path.home() == HOME and Path.cwd().resolve() == ROOT,
         'WRONG_USER_OR_DIRECTORY')
    for path, expected in ((ACTIVE_CONFIG, CONFIG_SHA), (PILOT_JS, PILOT_JS_SHA),
                           (CANARY_JS, CANARY_JS_SHA), (MANAGED_CLI, MANAGED_CLI_SHA)):
        need(trusted(path) and sha(path) == expected, 'PINNED_FILE_CHANGED_' + path.name)
    need(trusted(PILOT_CREATED, private=True), 'PILOT_CREATE_RECEIPT_UNTRUSTED')
    created = json.loads(PILOT_CREATED.read_text('utf8'))
    need(created.get('task_id') == PILOT and created.get('request_id') == REQUEST and
         created.get('dispatch_enabled') is False, 'PILOT_CREATE_RECEIPT_CHANGED')
    obj = json.loads(ACTIVE_CONFIG.read_text('utf8'))
    entries = obj.get('plugin')
    need(isinstance(entries, list) and len(entries) == 8, 'EIGHT_PLUGIN_CONFIG_REQUIRED')
    names = [plugin_path(x) for x in entries]
    need(names.count(str(PILOT_JS)) == 1 and names.count(str(CANARY_JS)) == 1,
         'PLUGINS_NOT_UNIQUE')
    p = entries[names.index(str(PILOT_JS))]
    c = entries[names.index(str(CANARY_JS))]
    need(isinstance(p, list) and p[1].get('dry_run') is False and
         p[1].get('tick_ms') == 30000 and p[1].get('task_ids') == [PILOT],
         'PILOT_CONFIG_DRIFT')
    need(isinstance(c, list) and c[1].get('dry_run') is False and
         c[1].get('task_ids') == [CANARY], 'CANARY_CONFIG_DRIFT')
    need(not any('mt-git-inbox' in x for x in names), 'GIT_PLUGIN_REINTRODUCED')
    need(PILOT != CANARY and len(REPORT_MID) <= 64 and
         re.fullmatch(r'[a-z0-9-]+', REPORT_MID), 'IDENTIFIER_ERROR')
    if require_gate_absent:
        need(not GATE.exists() and not GATE.is_symlink(), 'MANAGED_WRITE_GATE_ALREADY_PRESENT')
    elif GATE.exists() or GATE.is_symlink():
        need(trusted(GATE, private=True) and GATE.read_bytes() == ARM_CONTENT and
             stat.S_IMODE(GATE.stat().st_mode) == 0o600,
             'MANAGED_WRITE_GATE_UNTRUSTED')

def systemd_guard():
    for service in ('bridge-pg-v2-ssh-shadow.service',
                    'hermes-pg-v2-stage33fb-cg.service',
                    'hermes-pg-tunnel-stage33fd.service'):
        p = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', service],
                           capture_output=True, timeout=8)
        need(p.returncode == 0, 'REQUIRED_SERVICE_INACTIVE_' + service.split('.')[0])

def plugin_idle_guard():
    need(trusted(PILOT_LOG), 'MANAGED_PLUGIN_LOG_MISSING')
    latest = PILOT_LOG.read_text('utf8', errors='replace').splitlines()[-150:]
    need(any('plugin loaded;' in line for line in latest) and
         any('tick_ok dispatched=0' in line for line in latest),
         'MANAGED_PLUGIN_NOT_CONFIRMED_IDLE')
    if PILOT_STATE.exists() or PILOT_STATE.is_symlink():
        need(trusted(PILOT_STATE, private=True), 'MANAGED_STATE_UNTRUSTED')
        obj = json.loads(PILOT_STATE.read_text('utf8'))
        need(isinstance(obj, dict) and obj.get('inflight', {}) == {} and
             obj.get('seen', {}) == {} and obj.get('sessions', {}) == {},
             'MANAGED_INFLIGHT_ALREADY_EXISTS')

def actors():
    sys.path.insert(0, ACTOR_IMPORT)
    from actor_import33e import actor_classes
    ChatGPT, Agent = actor_classes()
    return ChatGPT(), Agent()

def canary_guard(cur):
    cur.execute('SELECT bridge.dialog_metadata(%s) AS r', (CANARY,))
    d = cur.fetchone()['r']
    need(isinstance(d, dict) and d.get('state') == 'active' and d.get('last_seq') == 6,
         'CANARY_DIALOG_CHANGED')
    for seq in (5, 6):
        cur.execute('SELECT bridge.delivery_status(%s,%s) AS r', (CANARY, seq))
        delivery = cur.fetchone()['r']
        need(isinstance(delivery, dict) and delivery.get('status') == 'delivered',
             'CANARY_RECEIPT_CHANGED')

def read_db(cg, ag):
    """Read via role wrappers; no table grants to actor roles."""
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_chatgpt', 'CG_LOGIN_INVALID')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
            need(cur.fetchone()['n'] == 42, 'SCHEMA_FUNCTION_COUNT_CHANGED')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
            need(cur.fetchone()['n'] == 4, 'SCHEMA_TABLE_COUNT_CHANGED')
            cur.execute('SELECT bridge.creation_status(%s) AS r', (REQUEST,))
            cr = cur.fetchone()['r']
            need(cr and cr.get('task_id') == PILOT and cr.get('title') == TITLE,
                 'PILOT_IDENTITY_CHANGED')
            cur.execute('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS r', (0,50))
            managed = cur.fetchone()['r']
            need(isinstance(managed, list) and len(managed) == 1 and
                 managed[0].get('task_id') == PILOT, 'MANAGED_REGISTRY_CHANGED')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r', (PILOT,))
            d = cur.fetchone()['r']
            need(d and d.get('registered') is True and d.get('state') == 'active',
                 'PILOT_NOT_REGISTERED_ACTIVE')
            canary_guard(cur)
            cur.execute('SELECT bridge.send_status(%s,%s) AS r', (PILOT, TASK_MID))
            task = cur.fetchone()['r']
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS r', (PILOT, 1))
            delivery1 = cur.fetchone()['r']
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS r', (PILOT, 2))
            delivery2 = cur.fetchone()['r']
            cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS r', (PILOT,0,3))
            view = cur.fetchone()['r']
    with ag._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_agent', 'AG_LOGIN_INVALID')
            cur.execute('SELECT bridge.ag_dispatch_list(%s,%s) AS r', (0,50))
            queued = cur.fetchone()['r']
            need(isinstance(queued, list) and all(x.get('task_id') == PILOT for x in queued),
                 'DISPATCH_QUEUE_HAS_UNEXPECTED_TASKS')
            if d.get('dispatch_enabled'):
                cur.execute('SELECT bridge.ag_managed_report_status(%s,%s) AS r', (PILOT,REPORT_MID))
                report_status = cur.fetchone()['r']
            else:
                report_status = None
    need(isinstance(view,dict) and view.get('task_id') == PILOT, 'MANAGED_VIEW_INVALID')
    return {'detail':d, 'task_status':task, 'delivery1':delivery1,
            'delivery2':delivery2, 'view':view, 'queued':queued,
            'report_status':report_status}

def validate_before(x):
    d = x['detail']
    need(d.get('dispatch_enabled') is False and d.get('policy_version') == 0 and
         d.get('version') == 0 and d.get('last_seq') == 0 and
         d.get('next_actor') == 'chatgpt', 'PILOT_NOT_READY_FOR_FIRST_TURN')
    need(x['task_status'] == {'found':False} and x['delivery1'] is None and
         x['delivery2'] is None and x['view'].get('messages') == [] and
         x['queued'] == [], 'PILOT_ALREADY_HAS_TASK_OR_DELIVERY')

def validate_after(x):
    d = x['detail']
    need(d.get('dispatch_enabled') is True and d.get('policy_version') == 1 and
         d.get('last_seq') in (1,2) and d.get('version') in (1,2),
         'PILOT_POST_SEND_POLICY_OR_SEQUENCE_INVALID')
    need(x['task_status'].get('found') is True and x['task_status'].get('seq') == 1 and
         x['task_status'].get('message_id') == TASK_MID and
         x['task_status'].get('sender') == 'chatgpt' and
         x['task_status'].get('kind') == 'task', 'PILOT_TASK_IDENTITY_MISMATCH')
    need(isinstance(x['delivery1'],dict) and
         x['delivery1'].get('status') in ('pending','leased','delivered'),
         'TASK_DELIVERY_MISSING')
    messages = x['view'].get('messages',[])
    need(isinstance(messages, list) and len(messages) == d['last_seq'] and
         any(m.get('seq') == 1 and m.get('message_id') == TASK_MID and
             m.get('sender') == 'chatgpt' and m.get('recipient') == 'agent' and
             m.get('body') == TASK_BODY for m in messages),
         'TASK_MESSAGE_CONTENT_MISMATCH')
    if d['last_seq'] == 1:
        need(d.get('next_actor') == 'agent' and x['delivery2'] is None,
             'TURN_STATE_NOT_WAITING_FOR_AGENT')
    else:
        need(d.get('next_actor') == 'chatgpt' and isinstance(x['delivery2'], dict),
             'REPORT_DELIVERY_MISSING')
        reports = [m for m in messages if m.get('seq') == 2]
        need(len(reports) == 1 and reports[0].get('message_id') == REPORT_MID and
             reports[0].get('sender') == 'agent' and reports[0].get('recipient') == 'chatgpt' and
             reports[0].get('kind') == 'report' and reports[0].get('body') == REPORT_BODY,
             'MANAGED_REPORT_CONTENT_NOT_EXACT')
        need(x['report_status'] and x['report_status'].get('found') is True and
             x['report_status'].get('seq') == 2 and x['report_status'].get('sender') == 'agent',
             'MANAGED_REPORT_STATUS_INCONSISTENT')

def report_ready(x):
    return x['detail']['last_seq'] == 2 and x['delivery1'].get('status') == 'delivered'

def record_no_replace(path, obj):
    need(not path.exists() and not path.is_symlink(), 'JOURNAL_ALREADY_EXISTS')
    blob = (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(blob)
        f.flush(); os.fsync(f.fileno())
    dfd = os.open(ROOT, os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(dfd)
    finally: os.close(dfd)

def write_gate():
    need(not GATE.exists() and not GATE.is_symlink(), 'WRITE_GATE_ALREADY_EXISTS')
    need(GATE.parent.is_dir() and not GATE.parent.is_symlink() and
         GATE.parent.stat().st_uid == os.geteuid(), 'WRITE_GATE_DIRECTORY_UNTRUSTED')
    fd = os.open(GATE, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(ARM_CONTENT)
        f.flush(); os.fsync(f.fileno())
    dfd = os.open(GATE.parent, os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(dfd)
    finally: os.close(dfd)
    need(trusted(GATE, private=True) and stat.S_IMODE(GATE.stat().st_mode) == 0o600 and
         GATE.read_bytes() == ARM_CONTENT, 'WRITE_GATE_NOT_ARMED_CORRECTLY')

def preflight():
    pin_local(require_gate_absent=True)
    systemd_guard()
    plugin_idle_guard()
    cg,ag = actors()
    x = read_db(cg,ag)
    validate_before(x)
    need(not ACTIVATION_ATTEMPT.exists() and not ACTIVATION_RECEIPT.exists() and
         not REPORT_ACK_ATTEMPT.exists(), 'STAGE34FC_ALREADY_ATTEMPTED')
    print('STAGE34FC_PILOT_ACTIVE_PLUGIN_EIGHT_CONFIG_PINNED')
    print('STAGE34FC_MANAGED_AGENT_IDLE_AND_NO_INFLIGHT')
    print('STAGE34FC_REAL_CHATGPT_AGENT_ROLE_BOUND_PREFLIGHT_OK')
    print('STAGE34FC_NO_PRIOR_MANAGED_TASK_AND_CANARY_OK')
    print('STAGE34FC_PREPARE_READY_NO_WRITES')

def send_once():
    preflight()
    record_no_replace(ACTIVATION_ATTEMPT,
        {'task_id':PILOT,'message_id':TASK_MID,'attempted':True,
         'expected_policy_version':0,'expected_dialog_version':0,
         'no_retry_on_unknown_commit':True})
    print('STAGE34FC_ACTIVATION_ATTEMPT_JOURNALED_NO_RETRY',flush=True)
    write_gate()
    print('STAGE34FC_AGENT_MANAGED_WRITE_GATE_ARMED',flush=True)
    cg,_ = actors()
    # Policy enable + exact first task are one atomic PostgreSQL transaction.
    # No unclaimed activated window can remain if the send fails.
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_chatgpt', 'WRITE_ROLE_NOT_CHATGPT')
            cur.execute('SELECT bridge.cg_set_managed_dispatch(%s,%s,%s,%s) AS r',
                        (PILOT,True,0,0))
            a = cur.fetchone()['r']
            need(isinstance(a,dict) and a.get('task_id') == PILOT and
                 a.get('dispatch_enabled') is True and
                 a.get('changed') is True and a.get('policy_version') == 1,
                 'POLICY_ENABLE_RESULT_UNEXPECTED')
            cur.execute('SELECT bridge.cg_send_managed_task(%s,%s,%s,%s) AS r',
                        (PILOT,TASK_MID,TASK_BODY,0))
            sent = cur.fetchone()['r']
            need(isinstance(sent,dict) and sent.get('task_id') == PILOT and
                 sent.get('message_id') == TASK_MID and sent.get('seq') == 1 and
                 sent.get('version') == 1 and sent.get('created') is True and
                 sent.get('sender') == 'chatgpt' and sent.get('recipient') == 'agent',
                 'SEND_RESULT_UNEXPECTED')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r',(PILOT,))
            detail = cur.fetchone()['r']
            need(detail and detail.get('dispatch_enabled') is True and
                 detail.get('policy_version') == 1 and detail.get('last_seq') == 1 and
                 detail.get('version') == 1 and detail.get('next_actor') == 'agent',
                 'TRANSACTION_POSTCONDITION_FAILED')
    # Reaching here means the context manager committed without an error.
    print('STAGE34FC_DISPATCH_AND_ONE_TASK_ATOMICALLY_COMMITTED',flush=True)
    record_no_replace(ACTIVATION_RECEIPT,
        {'task_id':PILOT,'message_id':TASK_MID,'seq':1,'policy_version':1,
         'task_sha256':hashlib.sha256(TASK_BODY.encode()).hexdigest(),'committed':True})
    x = read_db(*actors())
    validate_after(x)
    print('STAGE34FC_FIRST_MANAGED_TASK_VISIBLE_TO_AGENT')
    print('STAGE34FC_NO_GIT_NO_MCP_CUTOVER_NO_SCHEMA_CHANGE')

def verify():
    pin_local()
    x = read_db(*actors())
    d = x['detail']
    if d.get('dispatch_enabled') is False and d.get('last_seq') == 0:
        validate_before(x)
        print('STAGE34FC_VERIFY_ONLY_PILOT_NOT_COMMITTED')
        print('STAGE34FC_RETRY_FORBIDDEN_IF_ATTEMPT_JOURNAL_PRESENT')
        return
    validate_after(x)
    need(GATE.exists() and GATE.read_bytes() == ARM_CONTENT,
         'MANAGED_WRITE_GATE_MISSING_AFTER_COMMIT')
    print('STAGE34FC_VERIFY_ONLY_SINGLE_PILOT_TASK_COMMITTED')
    print('STAGE34FC_PILOT_TASK_SEQ=1')
    print('STAGE34FC_POLICY_VERSION=1')
    print('STAGE34FC_TASK_DELIVERY=' + x['delivery1']['status'])
    if d['last_seq'] == 1:
        print('STAGE34FC_MANAGED_REPORT_PENDING')
        return
    print('STAGE34FC_MANAGED_REPORT_EXACT_COMMITTED')
    print('STAGE34FC_REPORT_DELIVERY=' + x['delivery2']['status'])
    if x['delivery1']['status'] != 'delivered':
        print('STAGE34FC_AGENT_ACK_PENDING_NO_SDK_RETRY')
        return
    if PILOT_STATE.exists():
        need(trusted(PILOT_STATE, private=True), 'STATE_UNTRUSTED_AFTER_SDK')
        state = json.loads(PILOT_STATE.read_text('utf8'))
        if state.get('inflight',{}).get(PILOT):
            print('STAGE34FC_AGENT_INFLIGHT_RECONCILIATION_PENDING')
            return
        need(state.get('seen',{}).get(PILOT,0) >= 1,
             'AGENT_DELIVERED_WITHOUT_PERSISTED_CURSOR')
    print('STAGE34FC_AGENT_SDK_REPORT_AND_TASK_ACK_VERIFIED')
    if x['delivery2']['status'] == 'delivered':
        print('STAGE34FC_BOTH_DELIVERIES_CONFIRMED')
    else:
        print('STAGE34FC_CHATGPT_REPORT_ACK_PENDING')

def ack_report_once():
    pin_local()
    need(ACTIVATION_ATTEMPT.exists() and not REPORT_ACK_ATTEMPT.exists(),
         'REPORT_ACK_ALREADY_ATTEMPTED_OR_NOT_ACTIVATED')
    x = read_db(*actors())
    validate_after(x)
    need(report_ready(x) and x['delivery2']['status'] == 'pending',
         'REPORT_NOT_READY_FOR_CG_ACK')
    need(trusted(PILOT_STATE, private=True), 'PILOT_STATE_NOT_DURABLE')
    state = json.loads(PILOT_STATE.read_text('utf8'))
    need(state.get('inflight',{}).get(PILOT) is None and
         state.get('seen',{}).get(PILOT,0) >= 1,
         'PILOT_ACK_NOT_RECONCILED')
    record_no_replace(REPORT_ACK_ATTEMPT,
        {'task_id':PILOT,'seq':2,'message_id':REPORT_MID,
         'no_retry_on_unknown_commit':True})
    print('STAGE34FC_REPORT_ACK_ATTEMPT_JOURNALED_NO_RETRY',flush=True)
    cg,_ = actors()
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor'] == 'bridge_chatgpt','REPORT_ACK_ROLE_INVALID')
            cur.execute('SELECT bridge.cg_claim_managed_report(%s,%s,%s,%s,%s) AS r',
                        (PILOT,2,REPORT_MID,CONSUMER,3600))
            claim = cur.fetchone()['r']
            need(isinstance(claim, dict) and claim.get('claimed') is True and
                 isinstance(claim.get('delivery_id'),int) and
                 isinstance(claim.get('lease_token'),str),
                 'REPORT_CLAIM_NOT_CONFIRMED')
            cur.execute('SELECT bridge.cg_ack_managed_report(%s,%s,%s,%s::uuid,%s) AS r',
                        (PILOT,2,claim['delivery_id'],claim['lease_token'],CONSUMER))
            need(cur.fetchone()['r'] is True,'REPORT_ACK_NOT_CONFIRMED')
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS r',(PILOT,2))
            delivered = cur.fetchone()['r']
            need(delivered and delivered.get('status') == 'delivered',
                 'REPORT_ACK_TRANSACTION_POSTCONDITION_FAILED')
    print('STAGE34FC_CHATGPT_REPORT_ACK_ATOMICALLY_COMMITTED',flush=True)
    record_no_replace(REPORT_ACK_RECEIPT,
        {'task_id':PILOT,'seq':2,'message_id':REPORT_MID,'committed':True})
    verify()

def main(argv):
    try:
        need(len(argv)==2 and argv[1] in ('preflight','send-once','verify-only','ack-report-once'),
             'INVALID_ACTION')
        op=argv[1]
        if op=='preflight':preflight()
        elif op=='send-once':send_once()
        elif op=='verify-only':verify()
        elif op=='ack-report-once':ack_report_once()
        return 0
    except Exception as e:
        code = str(e) if isinstance(e,Refused) else 'UNEXPECTED_' + type(e).__name__
        print('STAGE34FC_STOP_' + code[:96], file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main(sys.argv))
