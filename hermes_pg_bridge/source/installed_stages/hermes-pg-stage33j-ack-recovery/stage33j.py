#!/usr/bin/env python3
"""Stage 33J one-shot, isolated PG reconciliation test with actual live JS function.

Only creates one bounded test dialog. Production SDK and MT durable state are NEVER used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage33j-ack-recovery')
CONF = Path('/home/kali/.config/gigatool/opencode.json')
LIVE = Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
CLI = Path('/home/kali/hermes-pg-stage33i-ackfix/stage33i_agent_cli.py')
PROD_STATE = Path('/home/kali/.local/state/mt-pg-inbox-agent-stage33f/state.json')
FIXTURE = ROOT / 'one-shot-fixture.json'
RESULT = ROOT / 'test-receipt.json'
NODE = '/home/kali/.hermes/node/bin/node'
PYTHON = '/home/kali/bridge-pg-v2/.venv/bin/python'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
REQUEST_ID = 'pgv2-stage33j-reconcile-fault-20261122-v1'
TITLE = 'PG v2 Stage33J isolated ACK recovery; NO SDK'
TASK_MID = 'pgv2-stage33j-no-sdk-task-v1'
REPORT_MID = 'pgv2-stage33j-no-sdk-report-v1'
TASK_BODY = 'STAGE33J_ISOLATED_SQL_RECEIPT_TEST_DO_NOT_EXECUTE'
REPORT_BODY = 'STAGE33J_AGENT_REPORT_ISOLATED_NO_SDK'
CONSUMER = 'stage33j-ack-reconcile-test'
CONF_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
LIVE_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CLI_SHA = '4f0d2431342a389543e33789066b01e3166187b95848604c5a6c5c6a99e5095e'
NODE_JS = ROOT / 'reconcile33j.cjs'

class Stop(Exception):
    pass

def need(ok, code):
    if not ok: raise Stop(code)

def sha(data): return hashlib.sha256(data).hexdigest()

def trusted(path, private=False):
    try:
        s = path.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and s.st_nlink == 1
                and not path.is_symlink() and (not private or not (s.st_mode & 0o077)))
    except OSError:
        return False

def version_token(tid, version):
    return hashlib.sha1(f'pgbridge-v2\x00{tid}\x00{version}'.encode('utf-8')).hexdigest()

def atom_once(path, payload):
    need(not path.exists() and not path.is_symlink(),'file_already_exists_'+path.name)
    tmp = path.with_name('.'+path.name+'.'+str(os.getpid())+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(payload)
            f.flush(); os.fsync(f.fileno())
        os.link(tmp,path,follow_symlinks=False)
        fd2=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(fd2)
        finally:os.close(fd2)
    finally:
        tmp.unlink(missing_ok=True)

def actor_classes():
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes as ac
    return ac()

def preflight_files():
    need(os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali'
         and Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,
         'wrong_os_user_or_workdir')
    for p, expected in ((CONF,CONF_SHA),(LIVE,LIVE_SHA),(CLI,CLI_SHA)):
        need(trusted(p),'untrusted_'+p.name)
        need(sha(p.read_bytes())==expected,'sha_mismatch_'+p.name)
    need(trusted(PROD_STATE,private=True),'production_state_untrusted')
    obj=json.loads(PROD_STATE.read_bytes())
    need(obj.get('seen',{}).get(CANARY)==5 and obj.get('inflight')=={},
         'production_state_has_inflight_or_changed')
    cfg=json.loads(CONF.read_bytes())
    entries=cfg.get('plugin')
    need(isinstance(entries,list) and len(entries)==7,'plugin_count_changed')
    def path(e):return e if isinstance(e,str) else e[0]
    def options(e):return {} if isinstance(e,str) else e[1]
    need(sum(1 for e in entries if path(e)==str(LIVE))==1,'active_plugin_missing')
    matches=[e for e in entries if path(e)==str(LIVE)]
    need(options(matches[0]).get('dry_run') is False and
         options(matches[0]).get('task_ids')==[CANARY], 'canary_allowlist_changed')
    need(sum('mt-pg-inbox' in path(e) for e in entries)==1, 'extra_pg_receiver')
    need(all('mt-git-inbox' not in path(e) for e in entries), 'git_receiver_reintroduced')
    src=LIVE.read_text('utf8')
    need('Object.freeze(["'+CANARY+'"])' in src and 'inflight_reconciled_ack_confirmed' in src,
         'fixed_reconcile_guards_missing')
    need(Path(NODE).is_file() and Path(PYTHON).is_file(),'runtime_missing')


def preflight_db(cg, ag, creating=False):
    need(cg.health().get('ok') is True and ag.health().get('ok') is True,
         'actor_connection_failed')
    for actor in (cg,ag):
        meta=actor.dialog_metadata(CANARY)
        need(meta.get('state')=='active' and meta.get('last_seq')==6,
             'canary_state_changed')
        for seq in (5,6):
            status=actor.delivery_status(CANARY,seq)
            need(isinstance(status,dict) and status.get('status')=='delivered',
                 'canary_receipt_changed')
    if creating:
        need(cg.creation_status(REQUEST_ID) is None, 'test_dialog_already_exists')
        need(not FIXTURE.exists() and not RESULT.exists(), 'previous_test_artifacts_exist')


def fixed_record(cg,ag,tid):
    need(isinstance(tid,str) and re.fullmatch(r'task_[0-9a-f]{32}',tid),
         'isolated_task_id_invalid')
    creation=cg.creation_status(REQUEST_ID)
    need(isinstance(creation,dict) and creation.get('task_id')==tid,
         'isolated_creation_id_mismatch')
    meta=cg.dialog_metadata(tid)
    need(isinstance(meta,dict) and meta.get('last_seq')==2 and meta.get('version')==2,
         'isolated_turns_not_two')
    first=cg.send_status(tid,TASK_MID)
    second=ag.send_status(tid,REPORT_MID)
    need(first.get('found') is True and first.get('seq')==1 and
         first.get('sender')=='chatgpt' and second.get('found') is True and
         second.get('seq')==2 and second.get('sender')=='agent',
         'test_messages_not_exact')
    view=cg.view_dialog(tid,0,3)
    rows=view.get('messages')
    need(isinstance(rows,list) and len(rows)==2,'test_history_wrong_size')
    byseq={r['seq']:r for r in rows}
    need(set(byseq)=={1,2} and
         byseq[1]['message_id']==TASK_MID and byseq[1]['sender']=='chatgpt' and
         byseq[1]['recipient']=='agent' and byseq[1]['body']==TASK_BODY and
         byseq[2]['message_id']==REPORT_MID and byseq[2]['sender']=='agent' and
         byseq[2]['recipient']=='chatgpt' and byseq[2]['body']==REPORT_BODY,
         'test_message_content_mismatch')
    status=ag.delivery_status(tid,1)
    need(isinstance(status,dict) and isinstance(status.get('delivery_id'),int),
         'isolated_delivery_id_unavailable')
    return meta, status


def fixture_from_db(cg,ag,tid,lease):
    meta,status=fixed_record(cg,ag,tid)
    need(status.get('status')=='leased' and status['delivery_id']==lease['delivery_id'],
         'lease_changed_before_fixture')
    snapshot={'task_id':tid,'head':version_token(tid,2),'seq':2,
              'history':[{'seq':1,'message_id':TASK_MID,'from':'chatgpt','to':'mt','kind':'task'},
                         {'seq':2,'message_id':REPORT_MID,'from':'mt','to':'chatgpt','kind':'report'}]}
    return {'stage':'33J','task_id':tid,'request_id':REQUEST_ID,'consumer':CONSUMER,
            'delivery_id':lease['delivery_id'],'lease_token':str(lease['lease_token']),
            'snapshot':snapshot,'seq':1,'message_id':TASK_MID}


def do_run():
    preflight_files()
    cg_cls,ag_cls=actor_classes();cg=cg_cls();ag=ag_cls()
    preflight_db(cg,ag,creating=True)
    print('STAGE33J_SEPARATE_ROLES_AND_EXCLUSIVE_CANARY_PREFLIGHT_OK',flush=True)
    # Offline check against the EXACT installed JS before changing database.
    runnode=['/home/kali/.hermes/node/bin/node',str(NODE_JS),'--preflight-only',str(LIVE)]
    env={k:v for k,v in os.environ.items() if k not in ('APPDIR','APPIMAGE')}
    probe=subprocess.run(runnode,cwd=ROOT,env=env,timeout=15,capture_output=True,text=True)
    need(probe.returncode==0,'actual_js_offline_preflight_failed')
    print('STAGE33J_ACTUAL_INSTALLED_RECONCILE_OFFLINE_OK',flush=True)
    before=sha(PROD_STATE.read_bytes())
    dialog=cg.create_dialog(REQUEST_ID,TITLE)
    need(dialog.get('created') is True and dialog.get('initiator')=='chatgpt',
         'isolated_create_unconfirmed')
    tid=dialog['task_id']
    print('STAGE33J_ISOLATED_DIALOG_CREATED id='+tid,flush=True)
    sent=cg.send_message(tid,TASK_MID,'task',TASK_BODY,expected_version=0)
    need(sent.get('created') is True and sent.get('seq')==1,'isolated_send_unconfirmed')
    lease=ag.claim_message(tid,1,TASK_MID,CONSUMER,lease_seconds=900)
    need(lease.get('claimed') is True and type(lease.get('delivery_id')) is int and
         isinstance(lease.get('lease_token'),str),'isolated_claim_unconfirmed')
    report=ag.send_report(tid,REPORT_MID,'report',REPORT_BODY,expected_version=1)
    need(report.get('created') is True and report.get('seq')==2,'isolated_report_unconfirmed')
    fixture=fixture_from_db(cg,ag,tid,lease)
    atom_once(FIXTURE,(json.dumps(fixture,separators=(',',':'),sort_keys=True)+'\n').encode('utf8'))
    print('STAGE33J_REAL_DB_TEST_REPORT_WRITTEN_NO_SDK',flush=True)
    test=subprocess.run(['/home/kali/.hermes/node/bin/node',str(NODE_JS),'--run-once',
                         str(LIVE),str(FIXTURE)],cwd=ROOT,env=env,timeout=100,
                        capture_output=True,text=True)
    # Print markers only if child succeeded, no raw exception/body/credentials.
    need(test.returncode==0,'real_js_reconcile_execution_failed')
    need('STAGE33J_ACTUAL_JS_ACK_LOST_REPLY_RECOVERED' in test.stdout,
         'real_js_reconcile_marker_absent')
    print('STAGE33J_ACTUAL_JS_ACK_LOST_REPLY_RECOVERED',flush=True)
    meta,status=fixed_record(cg,ag,tid)
    need(meta.get('state')=='active' and status['status']=='delivered',
         'isolation_ack_not_delivered')
    # Cancel the remaining reply outbox by tombstoning ONLY the isolated dialog.
    deletion=cg.tombstone(tid,expected_version=2)
    need(deletion.get('deleted') is True,'isolated_tombstone_not_confirmed')
    need(cg.delivery_status(tid,2).get('status')=='cancelled',
         'isolated_reply_outbox_not_cancelled')
    need(sha(PROD_STATE.read_bytes())==before,'production_state_changed')
    preflight_db(cg,ag)
    atomic={'task_id':tid,'isolated_seq1':'delivered','isolated_seq2':'cancelled',
            'test_dialog_state':'tombstoned','sdk_calls':0,
            'production_state_unchanged':True,'production_config_unchanged':True}
    atom_once(RESULT,(json.dumps(atomic,sort_keys=True)+'\n').encode('utf8'))
    print('STAGE33J_ISOLATED_OUTBOX_CLEANED_CANARY_UNCHANGED',flush=True)
    print('STAGE33J_REAL_DB_LOST_ACK_RESPONSE_RECOVERED_NO_SDK',flush=True)


def ack_only():
    # Executed only as the child of the synthetic JS reconciler. All selectors
    # derive from the private, one-shot fixture, never a caller-selected task ID.
    need(trusted(FIXTURE,private=True),'isolated_fixture_untrusted')
    payload=json.loads(FIXTURE.read_bytes())
    tid=payload.get('task_id')
    need(payload.get('stage')=='33J' and payload.get('request_id')==REQUEST_ID
         and payload.get('consumer')==CONSUMER and payload.get('seq')==1
         and payload.get('message_id')==TASK_MID,
         'isolated_fixture_fields_invalid')
    arg=json.load(sys.stdin)
    need(type(arg) is dict and set(arg)=={'delivery_id','lease_token','consumer'}
         and arg['delivery_id']==payload['delivery_id']
         and arg['lease_token']==payload['lease_token']
         and arg['consumer']==CONSUMER, 'ack_not_for_isolated_lease')
    cg_cls,ag_cls=actor_classes();cg=cg_cls();ag=ag_cls()
    _,status=fixed_record(cg,ag,tid)
    need(status.get('delivery_id')==payload['delivery_id'],
         'isolated_delivery_changed')
    if status['status']=='delivered':
        print('{"acked":true,"already_delivered":true}')
        return
    need(status['status']=='leased','isolated_delivery_not_leased')
    try:
        ok=ag.ack_outbox(payload['delivery_id'],payload['lease_token'],CONSUMER)
    except Exception:
        ok=False
    if ok is not True:
        status=ag.delivery_status(tid,1)
        need(status.get('delivery_id')==payload['delivery_id'] and
             status.get('status')=='delivered', 'isolated_ack_unconfirmed')
    print('{"acked":true,"already_delivered":false}')


def verify_only():
    preflight_files()
    cg_cls,ag_cls=actor_classes();cg=cg_cls();ag=ag_cls()
    preflight_db(cg,ag)
    creation=cg.creation_status(REQUEST_ID)
    need(isinstance(creation,dict),'isolated_test_not_yet_created')
    tid=creation['task_id']
    meta=cg.dialog_metadata(tid)
    first=cg.send_status(tid,TASK_MID)
    second=ag.send_status(tid,REPORT_MID)
    status=ag.delivery_status(tid,1)
    seq2=cg.delivery_status(tid,2)
    need(first.get('found') is True and first.get('sender')=='chatgpt'
         and first.get('seq')==1 and second.get('found') is True and
         second.get('sender')=='agent' and second.get('seq')==2,
         'isolated_messages_not_exact')
    print(json.dumps({'stage':'33J','task_id':tid,'state':meta.get('state'),
        'last_seq':meta.get('last_seq'),'seq1':status.get('status'),
        'seq2':seq2.get('status'),'finished':trusted(RESULT,private=True)},sort_keys=True))
    need(trusted(FIXTURE,private=True),'isolated_fixture_missing')
    fixture=json.loads(FIXTURE.read_bytes())
    need(fixture.get('task_id')==tid and fixture.get('delivery_id')==status['delivery_id'],
         'isolated_fixture_id_changed')
    need(trusted(RESULT,private=True),'isolated_test_incomplete')
    receipt=json.loads(RESULT.read_bytes())
    need(meta.get('state')=='tombstoned' and meta.get('last_seq')==2
         and status['status']=='delivered' and seq2['status']=='cancelled'
         and receipt.get('task_id')==tid and receipt.get('sdk_calls')==0,
         'isolated_test_receipt_mismatch')
    print('STAGE33J_VERIFY_ONLY_CLEANLY_CLOSED',flush=True)


def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['preflight','run-once','verify-only','ack-only'])
    args=ap.parse_args(argv)
    try:
        if args.action=='preflight':
            preflight_files()
            cg_cls,ag_cls=actor_classes();cg=cg_cls();ag=ag_cls()
            preflight_db(cg,ag,creating=True)
            print('STAGE33J_READ_ONLY_PREFLIGHT_OK')
        elif args.action=='run-once':do_run()
        elif args.action=='verify-only':verify_only()
        else:ack_only()
    except Exception as exc:
        code=str(exc) if isinstance(exc,Stop) else type(exc).__name__
        print('STAGE33J_STOP_'+re.sub(r'[^a-zA-Z0-9_-]','_',code)[:90],file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
