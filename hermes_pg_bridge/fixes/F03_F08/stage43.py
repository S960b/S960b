#!/usr/bin/env python3
"""Stage43 F03/F08. prepare: offline only; apply: explicit operator cutover after ACK.
Neither path executes SDK, sends messages or mutates PostgreSQL.
"""
from __future__ import annotations
import ast, hashlib, json, os, pathlib, pwd, re, shutil, stat, subprocess, sys, tempfile, time

H=pathlib.Path('/home/kali')
HERE=pathlib.Path(__file__).resolve().parent
PREP=H/'.local/state/bridge-pg-v2/stage43-prepared'
TASK='task_b6c26aa18220412bafdbf32393f16568'
CLI=H/'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py'
READ=CLI.with_name('managed_read34c.py')
WD=H/'hermes-pg-stage37a-permission-control/stage37a.py'
KDIR=H/'hermes-pg-stage39-operator-session'
KEEP=KDIR/'stage39.py'
SUMS=KDIR/'SHA256SUMS'
KEEP_TEST=KDIR/'test_stage39.py'
PLUGIN=H/'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
HELP=PLUGIN.with_name('intent37b2c.mjs')
CAP=H/'.config/bridge-pg-v2'
GATE=CAP/'managed-writes-stage34d-armed'
PERMIT=CAP/'managed-mcp-write-permit35c.json'
STATE=H/'.local/state/mt-pg-inbox-managed-stage34e/state.json'
INTENT=STATE.parent/'claim-intents37b2c'
SESSION=KDIR/'session.json'
STOP=KDIR/'393_close_session.sh'
NODE=H/'.hermes/node/bin/node'
BASE_SHA={
 'cli':'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485',
 'read':'443967becd000dbbedb3cbcac7291b2c3ddbfb3718976b984ca9130046fac',
 'watchdog':'66cdc42d30cacf4f6ac8f038a11001a2fbac722bdd9c4848f89dbe4424625f07',
 'keeper':'4c8e380f1420781409dab3ad1fb66ef9af802d1bba3867aa1d2d16da24901009',
 'plugin':'1c4e7ff0c892b1a9fde92711c44d94886790124bde16328e3d8b38d0b0843427',
 'helper':'19acb9366f71f1c8e030322b914804f8b55664ad7e5f6cf8c14cab790ea876c5',
 'keeper_test':'e6a316320e366e762389b1aadaf7e3b4a08a321b32f991c42100eb490563fc2f',
}
CASES={
 'cli':('candidate_managed_agent_cli34d.py',CLI),
 'watchdog':('candidate_stage37a.py',WD),
 'plugin':('candidate_plugin.mjs',PLUGIN),
 'keeper':('candidate_stage39.py',KEEP),
}
class StopError(Exception):pass
def need(ok,reason):
    if not ok:raise StopError('STAGE43_STOP_'+reason)
def sha(blob):return hashlib.sha256(blob).hexdigest()
def read(p,limit=250000):
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and not p.is_symlink() and st.st_uid==os.geteuid() and st.st_nlink==1
         and 0<st.st_size<=limit and not st.st_mode&0o022,'FILE_UNTRUSTED_'+p.name)
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        f=os.fstat(fd)
        need((f.st_dev,f.st_ino,f.st_size)==(st.st_dev,st.st_ino,st.st_size),'FILE_RACE_'+p.name)
        raw=os.read(fd,limit+1)
        need(len(raw)==st.st_size,'FILE_SHORT_READ_'+p.name)
        return raw
    finally:os.close(fd)
def private_dir(p):
    st=p.lstat()
    need(stat.S_ISDIR(st.st_mode) and not p.is_symlink() and st.st_uid==os.geteuid() and not st.st_mode&0o077,'DIRECTORY_UNTRUSTED')
def fsyncdir(p):
    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
def exclusive(p,raw):
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fsyncdir(p.parent)
def replace(p,raw):
    fd,n=tempfile.mkstemp(prefix='.stage43-replace-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o600);f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(n,p);fsyncdir(p.parent)
    finally:
        if os.path.lexists(n):os.unlink(n)
def owner():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and pathlib.Path.home()==H,'WRONG_OWNER')
def verify_originals():
    p={'cli':CLI,'read':READ,'watchdog':WD,'keeper':KEEP,'plugin':PLUGIN,'helper':HELP,'keeper_test':KEEP_TEST}
    for k,v in p.items():
        need(sha(read(v))==BASE_SHA[k],'BASE_DRIFT_'+k)
def anchor(s,needle,repl):
    need(s.count(needle)==1,'KEEPER_ANCHOR_COUNT')
    return s.replace(needle,repl)
def make_keeper(wdhash,pluginhash):
    raw=read(KEEP)
    need(sha(raw)==BASE_SHA['keeper'],'KEEPER_SOURCE_MISMATCH')
    s=raw.decode('utf8')
    s=anchor(s,"WATCHDOG_SHA = '66cdc42d30cacf4f6ac8f038a11001a2fbac722bdd9c4848f89dbe4424625f07'",
                "WATCHDOG_SHA = '"+wdhash+"'")
    s=anchor(s,"CANDIDATE_SHA256 = '1c4e7ff0c892b1a9fde92711c44d94886790124bde16328e3d8b38d0b0843427'",
                "CANDIDATE_SHA256 = '"+pluginhash+"'")
    a=s.index('def revoke_locked():\n');b=s.index('def closed(doc, reason):\n',a)
    need(a>=0 and b>a and 'quarantine_one(GATE)' in s[a:b],'REVOCATION_ANCHOR')
    replacement='''def revoke_locked():
    # F08: never let an error on one path prevent attempting the other.
    results=[]
    errors=[]
    for label,path in (('GATE',GATE),('PERMIT',PERMIT)):
        try:
            results.append(quarantine_one(path))
        except (Stop,OSError,ValueError,TypeError):
            results.append(False)
            errors.append(label)
    for label,path in (('GATE',GATE),('PERMIT',PERMIT)):
        try:
            if exists(path):errors.append(label)
        except (Stop,OSError,ValueError,TypeError):
            errors.append(label)
    if errors:
        raise Stop('STAGE39_STOP_PARTIAL_REVOKE_'+','.join(sorted(set(errors))))
    return tuple(results)

'''
    s=s[:a]+replacement+s[b:]
    a=s.index('def stop():\n');b=s.index('def status():\n',a)
    need(a>=0 and b>a and 'doc = session_read()' in s[a:b],'OPERATOR_STOP_ANCHOR')
    replacement='''def stop():
    owner()
    with guard_lock():
        # F08: revoke both active capabilities FIRST, even if session.json is corrupt.
        a,b=revoke_locked()
        try:
            doc=session_read()
        except (Stop,OSError,ValueError,TypeError):
            print('STAGE39_OPERATOR_STOP_CAPABILITIES_REVOKED_SESSION_UNREADABLE')
            raise Stop('STAGE39_STOP_SESSION_UNREADABLE_CAPABILITIES_REVOKED')
        if doc is not None and doc['status'] not in ('closed','fault'):
            closed(doc,'OPERATOR_CLOSED')
    print('STAGE39_OPERATOR_STOP_REVOKED_GATE',a)
    print('STAGE39_OPERATOR_STOP_REVOKED_PERMIT',b)
    print('STAGE39_ACTIVE_CAPABILITIES_ABSENT')

'''
    s=s[:a]+replacement+s[b:]
    ast.parse(s)
    return s.encode('utf8')
def run_test(cmd,timeout=90):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,text=True)
    need(p.returncode==0,'OFFLINE_TEST_FAILED_'+str(p.returncode)+'_'+''.join(ch for ch in pathlib.Path(cmd[1]).name if ch.isalnum())[:25])
    return p.stdout
def compute_candidates():
    verify_originals()
    raw={}
    for k in ('cli','watchdog','plugin'):
        name,_=CASES[k];raw[k]=read(HERE/name)
    need(b"def allow_write(op=None,task_id=None):" in raw['cli'] and b"args[0]=='authorize'" in raw['cli'],'CLI_AUTH_MISSING')
    need(b'def _revoke_both_independently' in raw['watchdog'],'WATCHDOG_REVOKE_MISSING')
    need(b'managed_pre_prompt_authorization_denied' in raw['plugin'] and
         b'sdk_prompt_outcome_unknown_hold' in raw['plugin'] and
         b'await send(recoveryDirectory)' not in raw['plugin'],'PLUGIN_POLICY_MISSING')
    raw['keeper']=make_keeper(sha(raw['watchdog']),sha(raw['plugin']))
    ast.parse(raw['cli'].decode('utf8'));ast.parse(raw['watchdog'].decode('utf8'))
    need(raw['cli']!=read(CLI) and raw['watchdog']!=read(WD) and raw['plugin']!=read(PLUGIN),'UNMODIFIED_CANDIDATE')
    return raw
def offline_tests(raw):
    with tempfile.TemporaryDirectory(prefix='.stage43-tests-',dir=str(H)) as temp:
        t=pathlib.Path(temp)
        for k,blob in raw.items(): (t/CASES[k][0]).write_bytes(blob)
        shutil.copy2(READ,t/'managed_read34c.py')
        shutil.copy2(KEEP_TEST,t/'test_stage39.py')
        # Stage39 tests import a local module named stage39.py
        shutil.copy2(t/CASES['keeper'][0],t/'stage39.py')
        run_test(['/usr/bin/python3','-I','-B',str(t/'test_stage39.py')])
        print('STAGE43_37_STAGE39_ORIGINAL_TESTS_OK',flush=True)
        run_test(['/usr/bin/python3','-I','-B',str(HERE/'test_f03.py'),str(t/CASES['cli'][0])])
        print('STAGE43_F03_NEGATIVE_TESTS_OK',flush=True)
        run_test(['/usr/bin/python3','-I','-B',str(HERE/'test_f08.py'),
                  str(t/CASES['watchdog'][0]),str(t/CASES['keeper'][0])])
        print('STAGE43_F08_FAULT_TESTS_OK',flush=True)
        run_test([str(NODE),'--check',str(t/CASES['plugin'][0])])
        run_test([str(NODE),str(HERE/'test_f03_f04.mjs'),
                  str(t/CASES['plugin'][0]),str(HELP)])
        print('STAGE43_F03_F04_NODE_TESTS_OK',flush=True)
def prepare():
    owner()
    need(not PREP.exists() and not PREP.is_symlink(),'ALREADY_PREPARED_NO_OVERWRITE')
    for p in (CAP,STATE.parent,INTENT,KDIR):private_dir(p)
    need(NODE.is_file(),'NODE_MISSING')
    raw=compute_candidates()
    offline_tests(raw)
    PREP.mkdir(mode=0o700);private_dir(PREP)
    files={}
    for k,blob in raw.items():
        name,_=CASES[k];exclusive(PREP/name,blob);files[name]=sha(blob)
    exclusive(PREP/'stage43.py',read(HERE/'stage43.py'))
    manifest={'version':1,'task_id':TASK,'base_sha':BASE_SHA,
              'files':files,'source_commit':os.environ.get('STAGE43_PINNED_REF','')}
    need(re.fullmatch(r'[0-9a-f]{40}',manifest['source_commit'] or '') is not None,'MISSING_COMMIT_PIN')
    exclusive(PREP/'manifest.json',(json.dumps(manifest,sort_keys=True)+'\n').encode())
    print('STAGE43_PREPARED_CANDIDATES_ONLY',flush=True)
    for key,hashval in sorted(files.items()):print('STAGE43_CANDIDATE_SHA',key,hashval,flush=True)
    print('STAGE43_NO_LIVE_MUTATION_NO_DB_WRITE_NO_REARM',flush=True)
    print('STAGE43_PREPARE_OK',flush=True)
def db_confirmed():
    # Strictly read-only; operator can only apply after ChatGPT has ACKed this stage.
    sys.path.insert(0,str(H/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT,_=actor_classes()
    actor=ChatGPT()
    with actor._connect() as cn:
        with cn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_chatgpt','DB_WRONG_ROLE')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(TASK,))
            d=cur.fetchone()['result']
            need(isinstance(d,dict) and d.get('registered') is True and
                 d.get('next_actor')=='chatgpt' and d.get('last_seq')==24,'DB_STAGE43_REPORT_NOT_FINAL')
            for seq,ident in ((23,'chatgpt'),(24,'agent')):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(TASK,seq))
                v=cur.fetchone()['result']
                need(v and v.get('status')=='delivered' and v.get('attempts')==1,'DB_SEQ_NOT_ACKED_'+str(seq))
            cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',(TASK,0,2))
            v=cur.fetchone()['result'];rows=v.get('messages',[])
            need(len(rows)==2 and rows[0].get('seq')==24 and rows[0].get('sender')=='agent' and
                 rows[1].get('seq')==23 and rows[1].get('sender')=='chatgpt','DB_REPORT_IDENTITY_DRIFT')
        cn.rollback()
def safe_to_apply():
    private_dir(PREP)
    raw=json.loads(read(PREP/'manifest.json',8192))
    need(raw.get('version')==1 and raw.get('task_id')==TASK and raw.get('base_sha')==BASE_SHA,'PREPARED_MANIFEST_BAD')
    need(re.fullmatch(r'[0-9a-f]{40}',raw.get('source_commit','')),'PREPARED_SOURCE_UNPINNED')
    c={}
    for k,(name,_) in CASES.items():
        blob=read(PREP/name)
        need(sha(blob)==raw['files'].get(name),'PREPARED_DRIFT_'+k)
        c[k]=blob
    verify_originals()
    session=json.loads(read(SESSION,8192))
    need(session.get('status')=='active' and session.get('task_id')==TASK,'SESSION_NOT_ACTIVE')
    local=json.loads(read(STATE,65536))
    need(local.get('inflight')=={} and local.get('seen',{}).get(TASK,0)>=23,'LOCAL_INFLIGHT_PRESENT')
    need(list(INTENT.iterdir())==[],'INTENT_PRESENT')
    db_confirmed()
    need(GATE.is_file() and PERMIT.is_file(),'CAPABILITIES_ALREADY_MISSING')
    return c,raw
def apply():
    owner()
    c,meta=safe_to_apply()
    # Owner explicitly called this command. Close live capabilities BEFORE code changes.
    p=subprocess.run(['bash',str(STOP)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
    need(p.returncode==0 and not GATE.exists() and not PERMIT.exists(),'OPERATOR_STOP_UNCONFIRMED')
    print('STAGE43_OPERATOR_SESSION_CLOSED_BOTH_CAPS_ABSENT',flush=True)
    backups=PREP/'backups'
    need(not backups.exists(),'BACKUPS_ALREADY_EXIST_NO_REPEAT')
    backups.mkdir(mode=0o700);private_dir(backups)
    for k in ('cli','watchdog','plugin','keeper'):
        _,dest=CASES[k]
        old=read(dest)
        need(sha(old)==BASE_SHA[k],'LIVE_CHANGED_DURING_CUTOVER_'+k)
        exclusive(backups/(k+'.original'),old)
    sums=read(SUMS,8192)
    m=re.search(rb'(?m)^([0-9a-f]{64})(  |\x20\*)(?:\./)?stage39\.py$',sums)
    need(m is not None and m.group(1).decode()==BASE_SHA['keeper'],'KEEPER_MANIFEST_BAD')
    exclusive(backups/'stage39-original.SHA256SUMS',sums)
    # Since session is closed, watchdog/keeper tick may only retain closed state.
    for k in ('cli','watchdog','plugin','keeper'):
        _,dest=CASES[k];replace(dest,c[k])
        need(sha(read(dest))==sha(c[k]),'POST_REPLACE_MISMATCH_'+k)
        print('STAGE43_APPLIED',k,sha(c[k]),flush=True)
    new_sums=sums[:m.start(1)]+sha(c['keeper']).encode()+sums[m.end(1):]
    replace(SUMS,new_sums)
    p=subprocess.run(['/usr/bin/sha256sum','-c','SHA256SUMS'],cwd=KDIR,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=25)
    need(p.returncode==0,'KEEPER_MANIFEST_POSTCHECK_FAILED')
    run_test([str(NODE),'--check',str(PLUGIN)])
    print('STAGE43_APPLY_DONE_NO_REARM_NO_MULTITOOL_RESTART',flush=True)
    print('STAGE43_NEXT_OPERATOR_RESTART_MULTITOOL_THEN_START_STAGE39',flush=True)
def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in ('prepare','apply'),'USAGE')
        {'prepare':prepare,'apply':apply}[sys.argv[1]]()
        return 0
    except StopError as e:
        print(str(e),file=sys.stderr,flush=True);return 2
    except Exception as e:
        print('STAGE43_STOP_'+type(e).__name__,file=sys.stderr,flush=True);return 2
if __name__=='__main__':raise SystemExit(main())
