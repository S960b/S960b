#!/usr/bin/env python3
"""One-shot, strictly pinned manual recovery of already completed seq5.

The existing claim-intent journal REMAINS in place to block the live SDK
while this operator-approved recovery obtains a lease, submits the already
observed report, and ACKs delivery. No SDK/tool execution occurs here.
"""
from __future__ import annotations
import hashlib, importlib.util, json, os, pwd, re, stat, subprocess, sys, tempfile
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage38c-manual-recovery'
BASE=Path(__file__).resolve().parent
OPS=BASE/'ops38c.py'
CLI=HOME/'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py'
READ=CLI.with_name('managed_read34c.py')
WATCHDOG=HOME/'hermes-pg-stage37a-permission-control/stage37a.py'
REVOKE=HOME/'hermes-pg-stage37a-permission-control/371_emergency_revoke.sh'
TASK='task_b6c26aa18220412bafdbf32393f16568'
SEQ=5
TASK_MID='pgv2-stage38-correct-taskboard-8789-20261124-v1'
REPORT_MID='pgv2-mg-b6c26aa18220412bafdbf32393f16568-5-report'
CONTENT=('STAGE38_CORRECT_TASKBOARD_PORT_8789\n'
'ENDPOINT / HTTP=401\nENDPOINT /health HTTP=401\nENDPOINT /api/tasks HTTP=401\nENDPOINT /api/taskboard HTTP=401\n'
'STAGE38_CORRECT_TASKBOARD_READONLY_DONE\n').encode()
RECEIPT=ROOT/'one-shot-receipt.json'
PINS={
  OPS:'015c3fc944c1c7823731615f751b0f1d008391ae79eec189ee4a6c97c1d685b2',
  CLI:'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485',
  READ:'443967becd000dbbedb3cbcac7291b2c3ddbfb3718318976b984ca9130046fac',
  WATCHDOG:'66cdc42d30cacf4f6ac8f038a11001a2fbac722bdd9c4848f89dbe4424625f07',
  REVOKE:'a817e9618e383f911f0e9b010c2427814d64a2dc03e95990a694f5f7cde81ef3'
}
class Stop(Exception):pass
def need(ok,reason):
    if not ok:raise Stop('STAGE38C_STOP_'+reason)
def sha(data):return hashlib.sha256(data).hexdigest()
def pin(path,hashval):
    s=path.lstat()
    need(stat.S_ISREG(s.st_mode) and not path.is_symlink() and s.st_uid==os.geteuid() and s.st_nlink==1 and not s.st_mode&0o022 and 0<s.st_size<250000, 'SOURCE_UNTRUSTED_'+path.name)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        t=os.fstat(fd)
        need((t.st_dev,t.st_ino,t.st_size)==(s.st_dev,s.st_ino,s.st_size),'SOURCE_RACE_'+path.name)
        blob=os.read(fd,250001)
        need(len(blob)==s.st_size and sha(blob)==hashval,'SOURCE_DRIFT_'+path.name)
    finally:os.close(fd)

def cli(*args):
    env=os.environ.copy();env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    p=subprocess.run([sys.executable,'-E','-s','-B',str(CLI),*args],capture_output=True,timeout=30,env=env)
    try:doc=json.loads(p.stdout)
    except (ValueError,UnicodeError):raise Stop('STAGE38C_STOP_CLI_NONJSON_'+args[0])
    need(isinstance(doc,dict),'CLI_NONDICT_'+args[0])
    return p.returncode,doc

def checked(*args):
    rc,doc=cli(*args)
    need(rc==0 and doc.get('ok') is not False,'CLI_'+args[0].upper()+'_FAILED_'+str(doc.get('error','unavailable')))
    return doc

def report_status():
    r=checked('status','--task-id',TASK,'--message-id',REPORT_MID)
    need(r.get('task_id')==TASK and r.get('message_id')==REPORT_MID and type(r.get('attempted')) is bool,'REPORT_STATUS_SHAPE')
    if r['attempted']:
        need(r.get('confirmed_seq')==6 and r.get('phase')=='committed','REPORT_STATUS_BAD_SEQ')
    else:
        need(r.get('confirmed_seq') is None and r.get('phase')=='pre_journal','REPORT_STATUS_BAD_PHASE')
    return r

def delivery(n):
    r=checked('delivery','--task-id',TASK,'--seq',str(n))
    need(isinstance(r,dict) and type(r.get('delivery_id')) is int and r['delivery_id']>0 and
         r.get('status') in ('pending','leased','delivered','cancelled'), 'DELIVERY_SHAPE')
    return r

def pending_head():
    r=checked('peek','--task-id',TASK,'--offset','0','--limit','8')
    need(r.get('task_id')==TASK and r.get('waiting_for')=='mt' and r.get('seq')==5,
         'DIALOGUE_NO_LONGER_WAITING_FOR_SEQ5')
    need(any(isinstance(x,dict) and x.get('seq')==5 and x.get('message_id')==TASK_MID and x.get('kind')=='task' and x.get('to')=='mt' for x in r.get('messages',[])),
         'PINNED_TASK_NOT_IN_HISTORY')
    expect=hashlib.sha1(f'pgbridge-v2\x00{TASK}\x005'.encode()).hexdigest()
    need(r.get('head')==expect,'HEAD_CHANGED')
    return expect

def put_receipt(record):
    ROOT.mkdir(mode=0o700,exist_ok=True)
    st=ROOT.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid() and (st.st_mode&0o077)==0 and not ROOT.is_symlink(),'UNTRUSTED_ROOT')
    data=(json.dumps(record,sort_keys=True,separators=(',',':'))+'\n').encode()
    need(len(data)<2048,'RECEIPT_TOO_LONG')
    if not RECEIPT.exists():
        fd=os.open(RECEIPT,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    else:
        old=RECEIPT.lstat()
        need(stat.S_ISREG(old.st_mode) and old.st_uid==os.geteuid() and (old.st_mode&0o077)==0 and old.st_nlink==1 and not RECEIPT.is_symlink(),'BAD_EXISTING_CHECKPOINT')
        # Atomic replacement, never a second claim. This is a progression of the same
        # recovery; the already-created checkpoint is never an authorization to rerun.
        fd,name=tempfile.mkstemp(prefix='.receipt-',dir=ROOT)
        try:
            with os.fdopen(fd,'wb') as f:
                os.fchmod(f.fileno(),0o600);f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(name,RECEIPT)
        finally:
            if os.path.lexists(name):os.unlink(name)
    fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def load_ops():
    spec=importlib.util.spec_from_file_location('stage38c_pinned_ops',OPS)
    need(spec and spec.loader,'OPS_NOT_LOADABLE')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def revoke():
    try:
        p=subprocess.run(['bash',str(REVOKE)],capture_output=True,timeout=25)
        print('STAGE38C_ON_FAILURE_CAPS_REVOKED' if p.returncode==0 else 'STAGE38C_ALERT_REVOKE_FAILED')
    except Exception:print('STAGE38C_ALERT_REVOKE_EXCEPTION')

def owner_ok():
    return os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and Path.home()==HOME

def run():
    need(owner_ok(), 'WRONG_USER')
    for p,expected in PINS.items():pin(p,expected)
    need(not RECEIPT.exists() and not RECEIPT.is_symlink(),'RECOVERY_ALREADY_STARTED_NO_REPEAT')
    need(not report_status()['attempted'],'REPORT_ALREADY_EXISTS_NO_REPEAT')
    d=delivery(5)
    need(d.get('status')=='pending' and d.get('attempts')==0 and d.get('delivery_id')==21, 'SEQ5_NOT_UNCLAIMED_PENDING')
    head=pending_head()
    ops=load_ops()
    ops.preflight()       # includes pinned journal, empty inflight, config, watchdog
    need(not ops.PERMIT.exists() and not ops.PERMIT.is_symlink() and not ops.GATE.exists() and not ops.GATE.is_symlink(),'PERMISSION_ALREADY_ACTIVE')
    print('STAGE38C_DB_SEQ5_PENDING_ATTEMPTS_ZERO_AND_REPORT_ABSENT',flush=True)
    print('STAGE38C_EXACT_UNCERTAIN_INTENT_PRESERVED_BLOCKS_SDK',flush=True)
    # Prevent implicit rerun if we crash at any point past authorization.
    put_receipt({'phase':'preclaim','task_id':TASK,'seq':5,'report_message_id':REPORT_MID})
    ops.arm()             # guarded synchronized creation, narrow and expiring
    print('STAGE38C_SCOPED_CAPABILITIES_ARMED',flush=True)
    try:
        need(not report_status()['attempted'],'CONCURRENT_REPORT_ALREADY_SENT')
        d=delivery(5)
        need(d.get('status')=='pending' and d.get('attempts')==0 and d.get('delivery_id')==21,'SEQ5_CHANGED_BEFORE_CLAIM')
        head=pending_head()
        # Manual bookkeeping for the exact already-executed GET result.
        # Absolutely no SDK and no task execution from this code.
        claim=checked('claim','--task-id',TASK,'--seq','5','--message-id',TASK_MID,'--consumer','mt-pg-managed')
        need(claim.get('claimed') is True and claim.get('delivery_id')==21 and
             isinstance(claim.get('lease_token'),str) and re.fullmatch('[0-9a-fA-F-]{36}',claim['lease_token']),
             'CLAIM_RECEIPT_MISSING_NO_AUTO_RETRY')
        token=claim['lease_token']
        put_receipt({'phase':'claimed','task_id':TASK,'seq':5,'delivery_id':21,'lease_token':token,'report_message_id':REPORT_MID})
        print('STAGE38C_MANUAL_LEASE_RECEIVED_AND_FSYNCED_NO_SDK_RUN',flush=True)
        fd,fn=tempfile.mkstemp(prefix='.stage38c-report-',dir=ops.CAP)
        try:
            os.fchmod(fd,0o600);os.write(fd,CONTENT);os.fsync(fd);os.close(fd);fd=-1
            code,doc=cli('append','--task-id',TASK,'--message-id',REPORT_MID,'--consumer','mt-pg-managed','--kind','report','--head',head,'--body-file',fn)
        finally:
            if fd>=0:os.close(fd)
            os.unlink(fn)
        need(report_status()['attempted'],'REPORT_NOT_COMMITTED_AFTER_SINGLE_APPEND')
        put_receipt({'phase':'report_committed','task_id':TASK,'seq':5,'delivery_id':21,'lease_token':token,'report_message_id':REPORT_MID})
        print('STAGE38C_REPORT_COMMITTED_SEQ6_SINGLE_MESSAGE_ID',flush=True)
        a=checked('ack','--task-id',TASK,'--seq','5','--delivery-id','21','--lease-token',token,'--consumer','mt-pg-managed')
        need(a.get('acked') is True and delivery(5).get('status')=='delivered','SEQ5_ACK_NOT_CONFIRMED')
        put_receipt({'phase':'seq5_ack_confirmed','task_id':TASK,'seq':5,'delivery_id':21,'report_message_id':REPORT_MID})
        print('STAGE38C_SEQ5_DELIVERY_ACKED_ONCE',flush=True)
        print('STAGE38C_NEXT_CHATGPT_ACK_SEQ6_DISABLE_DISPATCH',flush=True)
    except BaseException:
        revoke()
        raise

def main():
    try:
        need(len(sys.argv)==1,'NO_ARGS')
        run()
        return 0
    except Stop as exc:print(str(exc),file=sys.stderr)
    except Exception as exc:print('STAGE38C_STOP_'+type(exc).__name__,file=sys.stderr)
    return 2
if __name__=='__main__':raise SystemExit(main())
