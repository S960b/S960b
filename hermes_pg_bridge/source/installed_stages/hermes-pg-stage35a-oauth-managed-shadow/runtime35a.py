#!/usr/bin/env python3
"""Only bridge_chatgpt SQL wrappers. Write actions need extra local permit.

No legacy database login, no Git, and no direct table rights. All task IDs must
be registered in managed_dialogs, checked using role-bound wrapper.
"""
from __future__ import annotations
import hashlib, json, os, re, stat, sys, time
from pathlib import Path
from oauth_guard35a import AuthDenied, private_regular

IMPORT='/home/kali/hermes-pg-stage33e-shadow-clients'
PERMIT=Path('/home/kali/.config/bridge-pg-v2/managed-mcp-write-permit35b.json')
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
TASK=re.compile(r'^task_[0-9a-f]{32}$')
MID=re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$')
REQ=re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$')
CONSUMER='chatgpt-managed-oauth-mcp-35a'

class Invalid(Exception):pass

def need(ok,why):
    if not ok:raise Invalid(why)

def task_id(t):
    need(isinstance(t,str) and TASK.fullmatch(t) and t!=CANARY,'invalid_managed_id')
    return t

def message_id(v):
    need(isinstance(v,str) and MID.fullmatch(v),'invalid_message_id')
    return v

def version(v):
    need(type(v) is int and 0<=v<=2**53-1,'invalid_version')
    return v

def pagination(offset,limit):
    need(type(offset) is int and 0<=offset<=100000 and
         type(limit) is int and 1<=limit<=50, 'invalid_pagination')
    return offset,limit

def cg_db():
    sys.path.insert(0,IMPORT)
    from actor_import33e import actor_classes
    ChatGPT,_=actor_classes()
    return ChatGPT()

def registered(cg,tid):
    task_id(tid)
    detail=cg._one('SELECT bridge.cg_managed_detail(%s) AS result',(tid,))
    need(isinstance(detail,dict) and detail.get('registered') is True,
         'not_managed')
    return detail

def permitted(kind,tid=None):
    """No permit shipped in Stage35A. Future Stage35B will issue, pin and expire it.

    Deliberately requires an exact active config SHA and one task allowlist;
    handcrafting a file is NOT a supported activation method.
    """
    if not private_regular(PERMIT,owner=os.geteuid()):return False
    try:
        p=json.loads(PERMIT.read_text(encoding='utf8'))
        if set(p)!={'version','expires_unix','config_sha256','task_ids','allow_create','oauth_cutover_verified'}:return False
        if p['version']!=1 or p['oauth_cutover_verified'] is not True:return False
        if type(p['expires_unix']) is not int or not time.time()<p['expires_unix']<=time.time()+86400:return False
        if not isinstance(p['task_ids'],list) or len(p['task_ids'])>1:return False
        if any(not isinstance(x,str) or not TASK.fullmatch(x) or x==CANARY for x in p['task_ids']):return False
        cfg=Path('/home/kali/.config/gigatool/opencode.json')
        if cfg.is_symlink() or not cfg.is_file():return False
        if hashlib.sha256(cfg.read_bytes()).hexdigest()!=p['config_sha256']:return False
        if kind=='create':return p['allow_create'] is True
        return tid in p['task_ids']
    except (ValueError,TypeError,OSError,KeyError): return False

def health():
    cg=cg_db()
    h=cg.health()
    listing=cg._one('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS result',(0,50))
    return {'connected':h.get('ok') is True,'role':'bridge_chatgpt',
            'managed_count_first_page':len(listing),'shadow':True,
            'write_permit_present':False if not PERMIT.exists() else True}

def list_managed(offset=0,limit=20):
    pagination(offset,limit)
    return cg_db()._one('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS result',(offset,limit))

def detail(tid):return registered(cg_db(),tid)

def view(tid,offset=0,limit=20):
    pagination(offset,limit)
    cg=cg_db();registered(cg,tid)
    return cg._one('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',(tid,offset,limit))

def delivery(tid,seq):
    need(type(seq) is int and seq>=1,'invalid_seq')
    cg=cg_db();registered(cg,tid)
    return cg.delivery_status(tid,seq)

def create_status(request):
    need(isinstance(request,str) and REQ.fullmatch(request),'invalid_request_id')
    cg=cg_db();s=cg.creation_status(request)
    if not s or not isinstance(s,dict) or not s.get('task_id'):
        return {'found':False}
    d=cg._one('SELECT bridge.cg_managed_detail(%s) AS result',(s['task_id'],))
    if not isinstance(d,dict) or not d.get('registered'):return {'found':False}
    return {'found':True,'task_id':s['task_id'],'detail':d}

def send_status(tid,mid):
    message_id(mid);cg=cg_db();registered(cg,tid)
    return cg.send_status(tid,mid)

def create(request,title):
    need(isinstance(request,str) and REQ.fullmatch(request),'invalid_request_id')
    need(isinstance(title,str) and 1<=len(title.strip())<=160 and len(title)<=160,
         'invalid_title')
    need(permitted('create'),'managed_create_not_armed')
    return cg_db()._one('SELECT bridge.cg_create_managed_dialog(%s,%s) AS result',
                        (request,title))

def set_dispatch(tid,enabled,policy_version,dialog_version):
    task_id(tid)
    need(type(enabled) is bool,'invalid_dispatch_switch')
    version(policy_version);version(dialog_version)
    cg=cg_db();registered(cg,tid)
    # Emergency disable is always available to a valid OAuth writer.
    if enabled:need(permitted('dispatch',tid),'managed_dispatch_enable_not_armed')
    return cg._one('SELECT bridge.cg_set_managed_dispatch(%s,%s,%s,%s) AS result',
                   (tid,enabled,policy_version,dialog_version))

def send(tid,mid,body,expected_version):
    task_id(tid);message_id(mid);version(expected_version)
    need(isinstance(body,str) and 1<=len(body.encode('utf8'))<=32768,'invalid_task_body')
    need(permitted('send',tid),'managed_send_not_armed')
    cg=cg_db();registered(cg,tid)
    # DB independently enforces enabled policy, correct turn, expected version.
    return cg._one('SELECT bridge.cg_send_managed_task(%s,%s,%s,%s) AS result',
                   (tid,mid,body,expected_version))

def ack_report(tid,seq,mid,expected_sha256):
    """Read exact report; claim+ack within one transaction, no lease token exposed.

    An ambiguous COMMIT is resolved by managed_delivery_status, not a second
    claim with a new message ID. The exact body hash is provided by the caller.
    """
    task_id(tid);message_id(mid)
    need(type(seq) is int and seq>=1,'invalid_seq')
    need(isinstance(expected_sha256,str) and re.fullmatch(r'[0-9a-f]{64}',expected_sha256),
         'invalid_report_hash')
    cg=cg_db()
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='12000ms'")
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_chatgpt','db_role_mismatch')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(tid,))
            d=cur.fetchone()['result']
            need(isinstance(d,dict) and d.get('registered') is True,'not_managed')
            need(type(d.get('last_seq')) is int and 1<=seq<=d['last_seq'], 'report_seq_missing')
            cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',
                        (tid,d['last_seq']-seq,1))
            v=cur.fetchone()['result']
            messages=v.get('messages',[]) if isinstance(v,dict) else []
            need(len(messages)==1,'report_not_visible')
            m=messages[0]
            need(m.get('seq')==seq and m.get('message_id')==mid and
                 m.get('sender')=='agent' and m.get('recipient')=='chatgpt' and
                 m.get('kind')=='report' and m.get('returned') is False and
                 isinstance(m.get('body'),str),'report_identity_mismatch')
            need(hashlib.sha256(m['body'].encode('utf8')).hexdigest()==expected_sha256,
                 'report_body_hash_mismatch')
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(tid,seq))
            before=cur.fetchone()['result']
            need(isinstance(before,dict),'report_receipt_missing')
            if before.get('status')=='delivered':return {'acked':True,'already_delivered':True}
            need(before.get('status')=='pending','report_not_pending')
            cur.execute('SELECT bridge.cg_claim_managed_report(%s,%s,%s,%s,%s) AS result',
                        (tid,seq,mid,CONSUMER,180))
            claimed=cur.fetchone()['result']
            need(claimed.get('claimed') is True and type(claimed.get('delivery_id')) is int and
                 isinstance(claimed.get('lease_token'),str), 'report_claim_failed')
            cur.execute('SELECT bridge.cg_ack_managed_report(%s,%s,%s,%s::uuid,%s) AS result',
                        (tid,seq,claimed['delivery_id'],claimed['lease_token'],CONSUMER))
            need(cur.fetchone()['result'] is True,'report_ack_failed')
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(tid,seq))
            after=cur.fetchone()['result']
            need(isinstance(after,dict) and after.get('status')=='delivered','report_ack_unconfirmed')
    return {'acked':True,'already_delivered':False,'task_id':tid,'seq':seq}
