#!/usr/bin/env python3
"""Stage 38B: one-shot completion of *existing* seq=5 report.

Pins deployed Stage 38 armer, agent CLI, managed read code. No SQL or service
changes except the single permitted managed report append through the agent CLI.
Never repeats a message under a different id. Idempotency is status-based.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parent
HOME = Path('/home/kali')
CAP = HOME / '.config/bridge-pg-v2'
CLI = HOME / 'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py'
READ = CLI.with_name('managed_read34c.py')
OPS = BASE / 'pinned_ops38.py'
OPS_SHA = '883c9710970a23829fffee17950fd6d00bdf1d1f9dde00a4c41503d757e271d1'
CLI_SHA = 'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485'
READ_SHA = '443967becd000dbbedb3cbcac7291b2c3ddbfb3718318976b984ca9130046fac'
WATCHDOG_CODE = HOME / 'hermes-pg-stage37a-permission-control/stage37a.py'
WATCHDOG_SHA = '66cdc42d30cacf4f6ac8f038a11001a2fbac722bdd9c4848f89dbe4424625f07'
REVOKE_CODE = HOME / 'hermes-pg-stage37a-permission-control/371_emergency_revoke.sh'
REVOKE_SHA = 'a817e9618e383f911f0e9b010c2427814d64a2dc03e95990a694f5f7cde81ef3'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
MID = 'pgv2-mg-b6c26aa18220412bafdbf32393f16568-5-report'
TASK_MID = 'pgv2-stage38-correct-taskboard-8789-20261124-v1'
CONTENT = ('STAGE38_CORRECT_TASKBOARD_PORT_8789\n'
           'ENDPOINT / HTTP=401\n'
           'ENDPOINT /health HTTP=401\n'
           'ENDPOINT /api/tasks HTTP=401\n'
           'ENDPOINT /api/taskboard HTTP=401\n'
           'STAGE38_CORRECT_TASKBOARD_READONLY_DONE\n').encode('utf-8')
HEAD_VERSION = 5

class Stop(Exception): pass

def need(ok, why):
    if not ok: raise Stop('STAGE38B_STOP_' + why)

def pin(path, expected):
    st = path.lstat()
    need(stat.S_ISREG(st.st_mode) and not path.is_symlink() and
         st.st_uid == os.geteuid() and st.st_nlink == 1 and not st.st_mode & 0o022 and
         0 < st.st_size < 200000, 'UNTRUSTED_SOURCE_' + path.name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        actual = os.fstat(fd)
        need((st.st_dev, st.st_ino, st.st_size) == (actual.st_dev,actual.st_ino,actual.st_size),
             'SOURCE_CHANGED_' + path.name)
        blob = os.read(fd, 200001)
        need(len(blob) == st.st_size and hashlib.sha256(blob).hexdigest() == expected,
             'SOURCE_SHA_' + path.name)
    finally:os.close(fd)

def cli(*args):
    # Local Python must find its adjacent pinned managed_read34c.py. Do not
    # use -I here (removes the script directory on Python 3.11+).
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    p = subprocess.run([sys.executable, '-E', '-s', '-B', str(CLI), *args],
                       capture_output=True, timeout=25, env=env)
    try:
        doc = json.loads(p.stdout.decode('utf-8'))
    except (ValueError,UnicodeError):
        raise Stop('STAGE38B_STOP_AGENT_CLI_INVALID_JSON')
    need(isinstance(doc,dict), 'CLI_NON_OBJECT')
    return p.returncode,doc

def checked_cli(*args):
    code,doc = cli(*args)
    need(code == 0 and doc.get('ok') is not False,'CLI_'+args[0].upper()+'_FAILED_'+str(doc.get('error','UNKNOWN')))
    return doc

def get_status():
    r = checked_cli('status','--task-id',TASK,'--message-id',MID)
    need(r.get('task_id') == TASK and r.get('message_id') == MID and
         type(r.get('attempted')) is bool,'REPORT_STATUS_MISMATCH')
    if r['attempted']:
        need(r.get('confirmed_seq') == 6 and r.get('phase') == 'committed','REPORT_STATUS_UNEXPECTED_SEQ')
    else:
        need(r.get('confirmed_seq') is None and r.get('phase') == 'pre_journal','REPORT_STATUS_UNEXPECTED_PHASE')
    return r

def get_pending():
    r = checked_cli('peek','--task-id',TASK,'--offset','0','--limit','8')
    need(r.get('task_id') == TASK and r.get('waiting_for') == 'mt' and
         r.get('seq') == HEAD_VERSION, 'DIALOGUE_NOT_WAITING_FOR_AGENT_SEQ5')
    hist = r.get('messages',[])
    need(isinstance(hist,list) and any(isinstance(x,dict) and x.get('seq') == 5 and
         x.get('message_id') == TASK_MID and x.get('kind') == 'task' and
         x.get('from') == 'chatgpt' and x.get('to') == 'mt' for x in hist),
         'SEQ5_TASK_NOT_PINNED')
    expect = hashlib.sha1(f'pgbridge-v2\x00{TASK}\x005'.encode()).hexdigest()
    need(r.get('head') == expect,'DIALOGUE_HEAD_UNEXPECTED')
    return expect

def preflight_sources():
    need(os.getuid() == os.geteuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == 'kali' and
         HOME == Path.home() and os.environ.get('HOME') == str(HOME),'WRONG_USER')
    for path,sha in [(OPS,OPS_SHA),(CLI,CLI_SHA),(READ,READ_SHA),
                     (WATCHDOG_CODE,WATCHDOG_SHA),(REVOKE_CODE,REVOKE_SHA)]: pin(path,sha)
    need(len(CONTENT) < 32768 and CONTENT.endswith(b'\n'), 'REPORT_BYTES_INVALID')
    print('STAGE38B_PINNED_STAGE38_ARMER_AND_AGENT_CLI')
    print('STAGE38B_REPORT_BYTES',len(CONTENT))

def load_ops():
    # Only load the verified original Stage38 armer. Loading does not arm.
    spec = importlib.util.spec_from_file_location('stage38_ops_pinned',OPS)
    need(spec and spec.loader,'OPS_MODULE_UNAVAILABLE')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def revoke_safely(ops):
    # Stage37A operator-safe revoker works after config SHA drift, too.
    p = subprocess.run(['bash',str(ops.REVOKE)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    if p.returncode == 0:
        print('STAGE38B_FAILURE_CAPABILITIES_REVOKED')
    else:
        print('STAGE38B_CRITICAL_REVOKE_FAILED_USE_371_EMERGENCY_REVOKE', file=sys.stderr)

def finish():
    preflight_sources()
    # Check exact existing report status and seq BEFORE granting any permissions.
    if get_status()['attempted']:
        print('STAGE38B_REPORT_ALREADY_COMMITTED_SEQ6_NO_NEW_WRITE')
        return
    head = get_pending()
    ops = load_ops()
    ops.preflight()              # checks config, state, no pending claim intents, watchdog
    need(not ops.PERMIT.exists() and not ops.PERMIT.is_symlink(), 'PERMIT_ALREADY_PRESENT')
    need(not ops.GATE.exists() and not ops.GATE.is_symlink(), 'GATE_ALREADY_PRESENT')
    print('STAGE38B_EXISTING_SEQ5_AND_MISSING_SEQ6_CONFIRMED')
    print('STAGE38B_SCOPED_CONFIG_PLUGIN_WATCHDOG_PREFLIGHT_OK')
    ops.arm()                    # synchronized exclusive create; both caps one task
    print('STAGE38B_SCOPED_PERMIT_AND_GATE_ARMED')
    try:
        # Recheck report status after opening the gate: if another actor wrote
        # the same report, do not append. Never guess if status fails.
        if get_status()['attempted']:
            print('STAGE38B_REPORT_SEQ6_CONFIRMED_BY_CONCURRENT_WORKER')
            return
        head = get_pending()     # the current head may have changed
        fd,report_path=tempfile.mkstemp(prefix='.stage38b-fixed-report-',dir=CAP)
        try:
            os.fchmod(fd,0o600)
            os.write(fd,CONTENT); os.fsync(fd); os.close(fd); fd=-1
            code,doc = cli('append','--task-id',TASK,'--message-id',MID,
                           '--consumer','mt-pg-managed','--kind','report',
                           '--head',head,'--body-file',report_path)
        finally:
            if fd>=0: os.close(fd)
            os.unlink(report_path)
        # Do not retry append after ambiguous/failed outcome, check only
        # the durable status for the same idempotency key.
        confirmed = get_status()
        need(confirmed['attempted'], 'REPORT_NOT_COMMITTED_AFTER_ONE_APPEND')
        need(code == 0 or doc.get('error') is not None or doc.get('seq') == 6,
             'APPEND_RETURN_UNEXPECTED')
        print('STAGE38B_REPORT_COMMITTED_SEQ6_SINGLE_MESSAGE_ID')
        print('STAGE38B_REPORT_MESSAGE_ID',MID)
        print('STAGE38B_APPEND_EXIT_CODE',code)
        print('STAGE38B_NEXT_CHATGPT_ACK_SEQ6_AND_REVOKE')
    except BaseException:
        revoke_safely(ops)
        raise

def main():
    try:
        need(len(sys.argv)==1, 'NO_ARGS')
        finish()
        return 0
    except Stop as exc:
        print(str(exc),file=sys.stderr)
    except Exception as exc:
        print('STAGE38B_STOP_'+type(exc).__name__,file=sys.stderr)
    return 2

if __name__=='__main__':raise SystemExit(main())
