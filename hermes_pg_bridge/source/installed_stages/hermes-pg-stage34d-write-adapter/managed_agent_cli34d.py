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
from agent_admission import LEGACY, AdmissionDenied, new_dialogue_write

TASK=read.TASK
MID=read.MID
CONSUMER='mt-pg-managed'
WRITE_OPS=frozenset(('claim','ack','append'))
WRITE_ARM=Path('/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed')
ARM_CONTENT=b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
MAX_REPORT_BYTES=32*1024
EXPECTED_READ_SHA='443967becd000dbbedb3cbcac7291b2c3ddbfb3718318976b984ca9130046fac'


def _bridge_diag58(exc, component):
    """Emit only fixed categories; never stringify an exception or log its payload."""
    try:
        import os
        import stat
        import time
        import fcntl
        from pathlib import Path
        state = getattr(exc, "sqlstate", None)
        if type(state) is not str:
            state = ""
        # AdapterError deliberately strips SQLSTATE. Its own known safe code is
        # compared literally, never written verbatim or taken from server data.
        if state == "28P01" or state.startswith("28"):
            category = "db_authentication"
        elif state == "42501":
            category = "db_permission_denied"
        elif state == "57014":
            category = "db_query_cancelled_or_timeout"
        elif state == "55P03":
            category = "db_lock_unavailable"
        elif state == "40P01":
            category = "db_deadlock"
        elif state == "40001":
            category = "db_serialization_conflict"
        elif state == "53300":
            category = "db_connection_capacity"
        elif state == "57P01":
            category = "db_shutdown"
        elif state.startswith("08"):
            category = "db_connection_failure"
        elif state.startswith("23"):
            category = "db_constraint_failure"
        elif state == "P0001":
            category = "db_application_rejection"
        elif state:
            category = "db_other_sqlstate"
        elif isinstance(exc, TimeoutError):
            category = "local_timeout"
        elif isinstance(exc, ConnectionError):
            category = "local_connection_failure"
        elif isinstance(exc, OSError):
            category = "local_os_error"
        elif type(exc).__module__.startswith("psycopg") and type(exc).__name__ in ("OperationalError", "ConnectionTimeout"):
            category = "db_transport_or_connect_failure"
        elif type(exc).__name__ == "AdapterError" and getattr(exc,"args",()) == ("actor_database_unavailable",):
            category = "actor_database_failure_see_adapter"
        elif type(exc).__name__ == "AdapterError" and getattr(exc,"args",()) == ("actor_identity_mismatch",):
            category = "actor_identity_mismatch"
        elif type(exc).__name__ == "AdapterError":
            category = "actor_adapter_rejection"
        else:
            category = "internal_failure"
        if component not in ("store","actor_adapter","managed_agent_cli"):
            return
        # Local same-user private append log. No symlink following, no new dirs
        # at runtime. Bounded to 1 MiB; rotate only under exclusive file lock by
        # truncating oldest accumulated log in place. Never affects primary path.
        root = Path.home()/".local/state/bridge-pg-v2"
        st = root.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            return
        fd = os.open(root/"diagnostics58.log", os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
                return
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            if os.fstat(fd).st_size>=1048576:
                os.ftruncate(fd,0)
            stamp=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
            os.write(fd,(stamp+" "+component+" "+category+"\n").encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        pass  # Diagnostics must never change delivery/error behavior.


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

def stage39_scope(op=None,task_id=None):
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
        if not isinstance(session,dict) or type(session.get('version')) is not int or session['version'] not in (1,2): 
            return False
        if set(session)!=(SESSION_KEYS if session['version']==1 else SESSION_KEYS|{'last_renew_mono_us'}):
            return False
        if session.get('status')!='active':return False
        sid=session.get('session_id')
        if type(sid) is not str or not re.fullmatch('[0-9a-f]{32}',sid):return False
        if not isinstance(session.get('boot_id'),str) or session['boot_id']!=BOOT.read_text().strip():return False
        if any(type(session.get(k)) is not int for k in ('created_unix','last_renew_unix')):return False
        if session['version']==1:
            if type(session['deadline_unix']) is not int or not (session['created_unix']<=now<session['deadline_unix'] and 0<session['deadline_unix']-session['created_unix']<=21600):return False
        else:
            if session['deadline_unix'] is not None or type(session['last_renew_mono_us']) is not int or session['last_renew_mono_us']<=0:return False
            dt=(time.monotonic_ns()//1000-session['last_renew_mono_us'])/1_000_000
            wall=now-session['last_renew_unix']
            if not (0<=dt<=180 and 0<=wall<=180 and abs(wall-dt)<=30):return False
        if not (session['created_unix']<=session['last_renew_unix']<=now+60 and now-session['last_renew_unix']<=180):return False
        pbytes,_=_checked_bytes(PERMIT,4096)
        permit=json.loads(pbytes)
        if not isinstance(permit,dict) or set(permit)!=PERMIT_KEYS or type(permit.get('version')) is not int or permit['version']!=1:
            return False
        limit=now+3660
        if session['deadline_unix'] is not None:limit=min(limit,session['deadline_unix'])
        if type(permit.get('expires_unix')) is not int or not now<permit['expires_unix']<=limit:return False
        open_mode=permit.get('task_ids')==[] and permit.get('allow_create') is True
        if permit.get('static_tunnel_verified') is not True:return False
        if open_mode:
            if session.get('task_id')!=LEGACY:return False
        elif permit.get('task_ids')!=[task_id] or permit.get('allow_create') is not False:return False
        cfg,_=_checked_bytes(CFG,2_097_152,private=False)
        digest=hashlib.sha256(cfg).hexdigest()
        if digest!=session.get('config_sha256') or digest!=permit.get('config_sha256'):return False
        return sid
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,Denied):
        return False

def run_authorized(op, task_id, operation):
    if task_id == LEGACY:
        need(stage39_scope(op,task_id) is not False,'stage34d_writes_not_armed')
        return operation()
    try:
        with new_dialogue_write(op,task_id,stage39_scope):
            return operation()
    except AdmissionDenied:
        raise Denied('stage34d_writes_not_armed') from None

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

def _f07_reservation(tid, mid, seq, path=None):
    import os, stat, json, re, uuid
    from pathlib import Path
    if path is None:
        path=Path('/home/kali/.local/state/mt-pg-inbox-managed-stage34e/state.json')
    path=Path(path)
    parent=path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid() or parent.st_mode&0o077:
        raise ValueError('bound_state_untrusted')
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_mode&0o077 or not 0<st.st_size<=262144:
        raise ValueError('bound_state_untrusted')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        a=os.fstat(stream.fileno())
        if (a.st_dev,a.st_ino,a.st_size,a.st_mode,a.st_uid,a.st_nlink)!=(st.st_dev,st.st_ino,st.st_size,st.st_mode,st.st_uid,st.st_nlink):
            raise ValueError('bound_state_changed')
        raw=stream.read(262145)
        b=os.fstat(stream.fileno())
        if len(raw)!=st.st_size or (b.st_size,b.st_mtime_ns,b.st_ctime_ns)!=(a.st_size,a.st_mtime_ns,a.st_ctime_ns):
            raise ValueError('bound_state_changed')
    value=json.loads(raw)
    if not isinstance(value,dict) or not isinstance(value.get('inflight'),dict):
        raise ValueError('bound_state_invalid')
    r=value['inflight'].get(tid)
    if not isinstance(r,dict) or type(seq) is not int or not 1<=seq<=9007199254740990 or not isinstance(tid,str) or not re.fullmatch(r'task_[0-9a-f]{32}',tid):
        raise ValueError('bound_reservation_missing')
    n=seq; suffix=''
    while n:
        n,x=divmod(n,36)
        suffix='0123456789abcdefghijklmnopqrstuvwxyz'[x]+suffix
    if mid!='pgv2-mg-'+tid[5:]+'-'+suffix+'-report':
        raise ValueError('bound_report_id_mismatch')
    if type(r.get('seq')) is not int or r['seq']!=seq or r.get('phase')!='prompt_attempted' or not isinstance(r.get('message_id'),str) or not r['message_id']:
        raise ValueError('bound_reservation_mismatch')
    did=r.get('delivery_id')
    tok=r.get('lease_token')
    if type(did) is not int or not 1<=did<=9223372036854775807 or not isinstance(tok,str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',tok):
        raise ValueError('bound_reservation_invalid')
    if str(uuid.UUID(tok))!=tok:
        raise ValueError('bound_reservation_invalid')
    return seq,did,tok

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
        last_seq=page.get('last_seq')
        need(type(last_seq) is int and 1<=last_seq<=version,'invalid_dialog_seq')
        need(read.version_token(tid,version)==a.head,'stale_head')
        expected_seq=last_seq+1
        expected_version=version+1
        # F06: seq and optimistic version are independent counters.
        def exact_receipt(r):
            return (isinstance(r,dict) and r.get('task_id')==tid and
                r.get('message_id')==mid and r.get('sender')=='agent' and
                r.get('recipient')=='chatgpt' and r.get('returned') is False and
                r.get('created') is True and type(r.get('seq')) is int and
                r['seq']==expected_seq and type(r.get('version')) is int and
                r['version']==expected_version)
        try:
            bound_reservation=_f07_reservation(tid,mid,expected_seq-1)
        except ValueError:
            bound_reservation=None
        if bound_reservation is None:
            # Owner handed the turn back: no inbound lease exists. A free report is
            # allowed only when the dialog already ends with an agent message.
            msgs=page.get('messages')
            need(isinstance(msgs,list) and len(msgs)>=1 and isinstance(msgs[0],dict) and
                 msgs[0].get('sender')=='agent','bound_reservation_missing')
            try:
                result=one(agent,'SELECT bridge.ag_send_managed_report(%s,%s,%s,%s) AS result',(tid,mid,body,version))
            except Exception:
                result=None
        else:
            try:
                result=one(agent,'SELECT bridge.ag_send_bound_managed_report(%s,%s,%s,%s,%s,%s,%s::uuid,%s) AS result',(tid,mid,body,version,*bound_reservation,'mt-pg-managed'))
            except Exception:
                result=None
        if exact_receipt(result):
            created=True
        else:
            # No second append. A lost/malformed reply needs independent,
            # read-only proof of the exact committed report, including body.
            try:
                status=one(agent,'SELECT bridge.ag_managed_report_status(%s,%s) AS result',(tid,mid))
                need(isinstance(status,dict) and status.get('found') is True and
                     status.get('task_id')==tid and status.get('message_id')==mid and
                     status.get('sender')=='agent' and status.get('kind')=='report' and
                     status.get('returned') is False and type(status.get('seq')) is int and
                     status['seq']==expected_seq,'report_commit_unconfirmed')
                after=one(agent,'SELECT bridge.ag_view_managed_dialog(%s,%s,%s) AS result',(tid,0,1))
                need(isinstance(after,dict) and after.get('task_id')==tid and
                     after.get('state')=='active' and after.get('next_actor')=='chatgpt' and
                     type(after.get('last_seq')) is int and after['last_seq']==expected_seq and
                     type(after.get('version')) is int and after['version']==expected_version and
                     isinstance(after.get('messages'),list) and len(after['messages'])==1,
                     'report_commit_unconfirmed')
                row=after['messages'][0]
                need(isinstance(row,dict) and type(row.get('seq')) is int and row['seq']==expected_seq and
                     row.get('message_id')==mid and row.get('sender')=='agent' and
                     row.get('recipient')=='chatgpt' and row.get('kind')=='report' and
                     row.get('returned') is False and row.get('body')==body,'report_commit_unconfirmed')
            except Exception:
                raise Denied('report_commit_unconfirmed') from None
            created=False
        if bound_reservation is not None:
            # Close the inbound delivery together with the report so the consumer
            # never sees a stale "leased" after the answer already exists. The
            # receiver's periodic reconciliation stays as the fallback.
            try:
                one(agent,'SELECT bridge.ag_ack_managed_message(%s,%s,%s,%s::uuid,%s) AS result',
                    (tid,bound_reservation[0],bound_reservation[1],bound_reservation[2],'mt-pg-managed'))
            except Exception:
                pass
        return {'task_id':tid,'message_id':mid,'seq':expected_seq,
                'created':created,'version':expected_version,
                'head':read.version_token(tid,expected_version)}
    raise Denied('unsupported_operation')

def main(argv=None):
    args=sys.argv[1:] if argv is None else list(argv)
    if args and args[0]=='authorize':
        try:
            need(len(args)==3 and args[1]=='--task-id','authorize_args_invalid')
            run_authorized('authorize',read.check_id(args[2]),lambda: None)
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
        if a.command in WRITE_OPS:
            out=run_authorized(a.command,identity(a),lambda: dispatch(a,Agent(),write_allowed=True))
        else:
            out=dispatch(a,Agent())
        print(json.dumps(out,ensure_ascii=False,separators=(',',':'),default=str))
        return 0
    except (Denied,read.Refused) as exc:
        print(json.dumps({'ok':False,'error':str(exc)}))
        return 2
    except Exception as _diagnostic_exc58:
        _bridge_diag58(_diagnostic_exc58, 'managed_agent_cli')
        print('{"ok":false,"error":"managed_actor_unavailable"}')
        return 2

if __name__=='__main__':raise SystemExit(main())
