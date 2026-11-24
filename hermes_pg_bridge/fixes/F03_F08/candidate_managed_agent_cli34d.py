#!/usr/bin/env python3
"""Managed-only Agent actor CLI, Stage 34D shadow candidate.

All writes are disabled until an independent, guarded activation stage.
Never accesses broad bridge_app wrappers, raw tables, or the SDK. The read API
is copied verbatim from the pinned Stage 34C artifact, with its own tests.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
import managed_read34c as read

TASK=read.TASK
MID=read.MID
CONSUMER='mt-pg-managed'
WRITE_OPS=frozenset(('claim','ack','append'))
WRITE_ARM=Path('/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed')
ARM_CONTENT=b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
MAX_REPORT_BYTES=32*1024
EXPECTED_READ_SHA='443967becd000dbbedb3cbcac7291b2c3ddbfb3718318976b984ca9130046fac'

class Denied(Exception): pass

def need(ok,code):
    if not ok: raise Denied(code)

SESSION=Path('/home/kali/hermes-pg-stage39-operator-session/session.json')
PERMIT=Path('/home/kali/.config/bridge-pg-v2/managed-mcp-write-permit35c.json')
CFG=Path('/home/kali/.config/gigatool/opencode.json')
BOOT=Path('/proc/sys/kernel/random/boot_id')
SESSION_KEYS={'version','session_id','task_id','config_sha256','boot_id','created_unix','deadline_unix','status','reason','last_renew_unix'}
PERMIT_KEYS={'version','expires_unix','config_sha256','task_ids','allow_create','static_tunnel_verified'}

def _checked_bytes(path,limit,private=True):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or path.is_symlink() or st.st_uid!=os.geteuid() or st.st_nlink!=1 or st.st_size<1 or st.st_size>limit or st.st_mode&(0o077 if private else 0o022):
        raise Denied('untrusted_capability_file')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        fst=os.fstat(fd)
        if (fst.st_ino,fst.st_dev,fst.st_size)!=(st.st_ino,st.st_dev,st.st_size):
            raise Denied('capability_file_changed')
        raw=os.read(fd,limit+1)
        if len(raw)!=st.st_size:raise Denied('capability_short_read')
        return raw,st
    finally:os.close(fd)

def allow_write(op=None,task_id=None):
    """F03: each write and pre-SDK authorization requires fresh scoped authority.
    Gate presence alone is never an authorization; no timer is assumed alive.
    """
    try:
        if op not in WRITE_OPS|{'authorize'} or not isinstance(task_id,str) or not TASK.fullmatch(task_id):
            return False
        now=time.time()
        gate,gs=_checked_bytes(WRITE_ARM,128,private=True)
        if gate!=ARM_CONTENT or not -60<=now-gs.st_mtime<1200:return False
        raw,_=_checked_bytes(SESSION,4096)
        session=json.loads(raw)
        if not isinstance(session,dict) or set(session)!=SESSION_KEYS or type(session.get('version')) is not int or session['version']!=1:
            return False
        if session.get('status')!='active' or session.get('task_id')!=task_id:return False
        if not isinstance(session.get('boot_id'),str) or session['boot_id']!=BOOT.read_text().strip():return False
        if any(type(session.get(k)) is not int for k in ('created_unix','deadline_unix','last_renew_unix')):return False
        if not (session['created_unix']<=now<session['deadline_unix'] and 0<session['deadline_unix']-session['created_unix']<=21600):return False
        if not (session['created_unix']<=session['last_renew_unix']<=now+60 and now-session['last_renew_unix']<=180):return False
        pbytes,_=_checked_bytes(PERMIT,4096)
        permit=json.loads(pbytes)
        if not isinstance(permit,dict) or set(permit)!=PERMIT_KEYS or type(permit.get('version')) is not int or permit['version']!=1:
            return False
        if type(permit.get('expires_unix')) is not int or not now<permit['expires_unix']<=session['deadline_unix']:return False
        if permit.get('task_ids')!=[task_id] or permit.get('allow_create') is not False or permit.get('static_tunnel_verified') is not True:return False
        cfg,_=_checked_bytes(CFG,2_097_152,private=False)
        digest=hashlib.sha256(cfg).hexdigest()
        if digest!=session.get('config_sha256') or digest!=permit.get('config_sha256'):return False
        return True
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,Denied):
        return False

def identity(a):
    tid=read.check_id(a.task_id)
    need(tid is not None,'invalid_task_id')
    return tid

def validate_consumer(a):
    need(a.consumer==CONSUMER,'wrong_consumer')
    return CONSUMER

def guarded_body(path):
    need(isinstance(path,str) and path.startswith('/home/kali/'),'report_path_denied')
    p=Path(path)
    try:
        st=p.lstat()
        need(stat.S_ISREG(st.st_mode) and not p.is_symlink() and st.st_nlink==1 and
             st.st_uid==os.geteuid() and st.st_mode&0o077==0 and
             0<st.st_size<=MAX_REPORT_BYTES,'report_file_untrusted')
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            fst=os.fstat(fd)
            need((fst.st_dev,fst.st_ino,fst.st_size)==(st.st_dev,st.st_ino,st.st_size),'report_race')
            blob=os.read(fd,MAX_REPORT_BYTES+1)
            need(len(blob)==st.st_size,'report_size_changed')
        finally:os.close(fd)
        body=blob.decode('utf8')
        need(0<len(body)<=MAX_REPORT_BYTES,'report_invalid_body')
        return body
    except (UnicodeDecodeError,OSError):raise Denied('report_unreadable')

def one(agent,sql,params):
    # All statements are literal in this source. No caller-built SQL identifiers.
    return agent._one(sql,params)

def dispatch(a,agent,*,write_allowed=False):
    op=a.command
    need(op in (*read.ALLOWED_READ,*WRITE_OPS),'unsupported_operation')
    if op not in WRITE_OPS:
        return read.dispatch(a,agent)
    need(write_allowed,'stage34d_writes_not_armed')
    tid=identity(a)
    consumer=validate_consumer(a)
    if op=='claim':
        need(type(a.seq) is int and a.seq>0 and a.seq<=2**63-1,'invalid_seq')
        mid=read.check_mid(a.message_id)
        # The SQL layer itself checks managed registration, enabled policy,
        # actor identity, current turn and a matching undelivered message.
        result=one(agent,'SELECT bridge.ag_claim_managed_message(%s,%s,%s,%s,%s) AS result',
                   (tid,a.seq,mid,consumer,3600))
        need(isinstance(result,dict) and type(result.get('claimed')) is bool,'claim_outcome_unknown')
        if result['claimed']:
            need(type(result.get('delivery_id')) is int and result['delivery_id']>0 and
                 isinstance(result.get('lease_token'),str),'claim_receipt_incomplete')
        return result
    if op=='ack':
        need(type(a.seq) is int and 1<=a.seq<=2**63-1 and
             type(a.delivery_id) is int and a.delivery_id>0 and
             isinstance(a.lease_token,str) and
             re.fullmatch('[0-9a-fA-F-]{36}',a.lease_token),'ack_args_invalid')
        seq=a.seq
        status=one(agent,'SELECT bridge.ag_managed_delivery_status(%s,%s) AS result',(tid,seq))
        need(isinstance(status,dict) and status.get('delivery_id')==a.delivery_id and
             status.get('status') in ('leased','delivered'),'ack_wrong_delivery')
        if status['status']=='delivered':
            return {'acked':True,'already_delivered':True}
        try:
            flag=one(agent,'SELECT bridge.ag_ack_managed_message(%s,%s,%s,%s::uuid,%s) AS result',
                     (tid,seq,a.delivery_id,a.lease_token,consumer))
        except Exception:
            flag=False
        if flag is True:
            return {'acked':True,'already_delivered':False}
        # Lost server reply is possible after successful commit. Read ONLY the
        # same managed delivery_id and never issue a second claim/SDK call.
        state=one(agent,'SELECT bridge.ag_managed_delivery_status(%s,%s) AS result',(tid,seq))
        need(isinstance(state,dict) and state.get('delivery_id')==a.delivery_id and
             state.get('status')=='delivered','ack_unconfirmed_hold_inflight')
        return {'acked':True,'already_delivered':True}
    if op=='append':
        need(a.kind=='report','only_report_allowed')
        mid=read.check_mid(a.message_id)
        need(isinstance(a.head,str) and re.fullmatch('[0-9a-f]{40}',a.head),'invalid_head')
        body=guarded_body(a.body_file)
        page=one(agent,'SELECT bridge.ag_view_managed_dialog(%s,%s,%s) AS result',(tid,0,2))
        need(isinstance(page,dict) and page.get('task_id')==tid and
             page.get('state')=='active' and page.get('next_actor')=='agent' and
             type(page.get('version')) is int and page['version']>=1,'agent_turn_not_active')
        version=page['version']
        need(read.version_token(tid,version)==a.head,'stale_head')
        result=one(agent,'SELECT bridge.ag_send_managed_report(%s,%s,%s,%s) AS result',
                   (tid,mid,body,version))
        need(isinstance(result,dict) and type(result.get('seq')) is int and
             result['seq']==version+1 and type(result.get('version')) is int,
             'report_commit_unconfirmed')
        return {'task_id':tid,'message_id':mid,'seq':result['seq'],
                'created':result.get('created'),'version':result['version'],
                'head':read.version_token(tid,result['version'])}
    raise Denied('unsupported_operation')

def main(argv=None):
    args=sys.argv[1:] if argv is None else list(argv)
    if args and args[0]=='authorize':
        try:
            need(len(args)==3 and args[1]=='--task-id','authorize_args_invalid')
            need(allow_write('authorize',read.check_id(args[2])),'stage34d_writes_not_armed')
            print('{"authorized":true}')
            return 0
        except (Denied,read.Refused) as exc:
            print(json.dumps({'ok':False,'error':str(exc)}))
            return 2
    a=read.parse(args)
    try:
        # An unchanged local copy of Stage34C is the only read path.
        my_read=Path(__file__).with_name('managed_read34c.py')
        need(hashlib.sha256(my_read.read_bytes()).hexdigest()==EXPECTED_READ_SHA,
             'read_code_not_pinned')
        sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
        from actor_import33e import actor_classes
        _,Agent=actor_classes()
        out=dispatch(a,Agent(),write_allowed=(allow_write(a.command,a.task_id) if a.command in WRITE_OPS else False))
        print(json.dumps(out,ensure_ascii=False,separators=(',',':'),default=str))
        return 0
    except (Denied,read.Refused) as exc:
        print(json.dumps({'ok':False,'error':str(exc)}))
        return 2
    except Exception:
        print('{"ok":false,"error":"managed_actor_unavailable"}')
        return 2

if __name__=='__main__':raise SystemExit(main())
