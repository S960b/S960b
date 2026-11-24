#!/usr/bin/env python3
"""Stage 33I-B: config-only MT ACK-fix handoff with a no-MT-process gate.

NO SDK calls, SQL writes, claims, ACKs, service/tunnel changes or process killing.
User MUST close all MultiTool instances before the worker touches the config.
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
ROOT = HOME / 'hermes-pg-stage33ib-ackfix-handoff'
CFG = HOME / '.config/gigatool/opencode.json'
PREPARED = ROOT / 'proposed-opencode.json'
BACKUP = ROOT / 'previous-opencode.json'
RECEIPT = ROOT / 'handoff-result.json'
OLD = HOME / 'hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs'
NEW = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
NEW_CLI = HOME / 'hermes-pg-stage33i-ackfix/stage33i_agent_cli.py'
STATE_DIR = HOME / '.local/state/mt-pg-inbox-agent-stage33f'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
OLD_CFG_SHA = 'a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3'
OLD_JS_SHA = '3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7'
NEW_JS_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
NEW_CLI_SHA = '4f0d2431342a389543e33789066b01e3166187b95848604c5a6c5c6a99e5095e'
WAIT_SECONDS = 900
STABLE_SECONDS = 12

class Stop(RuntimeError): pass

def need(condition, code):
    if not condition: raise Stop(code)

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def trusted_file(p: Path, private=False):
    try:
        s = p.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                and s.st_nlink == 1 and not p.is_symlink()
                and (not private or not (s.st_mode & 0o077)))
    except OSError:
        return False

def file_sha(p: Path):
    need(trusted_file(p), 'untrusted_file_'+p.name)
    return sha(p.read_bytes())

def proper_context():
    need(os.geteuid() != 0 and pwd.getpwuid(os.geteuid()).pw_name == 'kali'
         and Path.home() == HOME and Path.cwd().resolve() == ROOT,
         'wrong_execution_context')
    need(file_sha(OLD) == OLD_JS_SHA, 'old_plugin_hash_mismatch')
    need(file_sha(NEW) == NEW_JS_SHA, 'new_plugin_hash_mismatch')
    need(file_sha(NEW_CLI) == NEW_CLI_SHA, 'new_cli_hash_mismatch')
    # Pure syntax check only; candidate is NOT imported as an SDK plugin.
    need(STATE_DIR.is_dir() and not STATE_DIR.is_symlink(), 'durable_state_dir_missing')
    need(STATE_DIR.stat().st_uid == os.geteuid(), 'durable_state_wrong_owner')

def plugin_path(e):
    if isinstance(e, str): return e
    if (isinstance(e, list) and len(e) == 2 and isinstance(e[0], str)
        and isinstance(e[1], dict)): return e[0]
    raise Stop('invalid_plugin_entry')

def transformed_bytes(original: bytes):
    data = json.loads(original)
    entries = data.get('plugin')
    need(isinstance(entries, list) and len(entries) == 7, 'plugin_count_changed')
    paths = [plugin_path(e) for e in entries]
    need(paths.count(str(OLD)) == 1, 'old_plugin_entry_not_unique')
    need(str(NEW) not in paths, 'new_plugin_already_loaded')
    need(not any('mt-git-inbox' in p or 'mt-pg-inbox-canary' in p for p in paths),
         'legacy_git_or_canary_plugin_reintroduced')
    i = paths.index(str(OLD))
    old_entry = entries[i]
    need(isinstance(old_entry, list) and len(old_entry) == 2,
         'old_plugin_not_option_pair')
    options = old_entry[1]
    need(options.get('dry_run') is False and options.get('task_ids') == [CANARY],
         'old_plugin_options_drifted')
    # Preserve every other option exactly, as well as other six plugins.
    replacement = [str(NEW), dict(options)]
    data['plugin'][i] = replacement
    result = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf8')
    check = json.loads(result)
    need(len(check['plugin']) == 7 and check['plugin'][i] == replacement,
         'failed_to_prepare_new_config')
    need(check['plugin'][:i] + check['plugin'][i+1:] == entries[:i] + entries[i+1:],
         'unrelated_plugin_changed')
    outside_original = dict(json.loads(original)); outside_new = dict(check)
    del outside_original['plugin']; del outside_new['plugin']
    need(outside_original == outside_new, 'unrelated_config_changed')
    return result

def safe_process_list():
    """Guard all potential MT runtimes, not just one GUI PID.

    If /proc cannot be read sufficiently, refuse to switch. Never emit cmdline.
    """
    found, unknown = [], []
    uid = os.geteuid()
    for d in Path('/proc').iterdir():
        if not d.name.isdecimal(): continue
        try:
            s = d.stat()
            if s.st_uid != uid: continue
            comm = (d/'comm').read_text('utf8').strip()[:32]
            low = comm.lower()
            mt = any(word in low for word in ('multitool', 'gigatool', 'opencode'))
            if not mt and low in ('node', 'bun', 'electron'):
                try:
                    cmd = (d/'cmdline').read_bytes().lower()
                    mt = any(word in cmd for word in (b'multitool', b'gigatool', b'opencode'))
                except OSError:
                    unknown.append(int(d.name)); continue
            if mt:
                raw = (d/'stat').read_text('utf8')
                tail = raw[raw.rfind(')')+2:].split()
                found.append({'pid': int(d.name), 'start_ticks': int(tail[19]),
                              'comm': comm})
        except FileNotFoundError: continue
        except (OSError, ValueError, IndexError): unknown.append(int(d.name))
    return sorted(found, key=lambda x: x['pid']), sorted(set(unknown))

def services_ok():
    for unit in ('bridge-pg-v2-ssh-shadow.service',
                 'hermes-pg-v2-stage33fb-cg.service',
                 'hermes-pg-tunnel-stage33fd.service'):
        p = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', unit],
                           timeout=8, capture_output=True)
        need(p.returncode == 0, 'required_service_inactive_'+unit.split('.')[0])

def pg_status():
    # The same public SECURITY DEFINER wrappers as Stage 33D, no direct table reads.
    sys.path.insert(0, str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT, Agent = actor_classes()
    for name, cls in (('chatgpt',ChatGPT), ('agent',Agent)):
        db=cls()
        need(db.health().get('ok') is True,'pg_health_failed_'+name)
        m=db.dialog_metadata(CANARY)
        need(m.get('state')=='active' and m.get('last_seq')==6,
             'canary_seq_or_state_changed_'+name)
        for seq in (5,6):
            r=db.delivery_status(CANARY,seq)
            need(isinstance(r,dict) and r.get('status')=='delivered',
                 'canary_receipt_changed_'+name+'_'+str(seq))

def durable_state_guard():
    # MT's pathsFor() uses state.json in the same Stage33F state directory.
    # NEVER reset, rewrite, copy into a different directory, or clear this file.
    p=STATE_DIR/'state.json'
    need(trusted_file(p,private=True), 'untrusted_durable_state_file')
    need(p.stat().st_size < 2_000_000, 'durable_state_unexpected_size')
    state=json.loads(p.read_text('utf8'))
    need(isinstance(state,dict) and isinstance(state.get('seen'),dict)
         and isinstance(state.get('inflight'),dict)
         and isinstance(state.get('sessions'),dict),'durable_state_schema_changed')
    need(state['seen'].get(CANARY)==5, 'seen_cursor_not_five')
    need(state['inflight']=={},'unresolved_inflight_present')
    return sha(p.read_bytes())

def atom_write(path:Path, data:bytes, replace=False):
    temp = path.with_name('.'+path.name+'.tmp-'+str(os.getpid()))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        if replace:
            os.replace(temp,path)
        else:
            need(not path.exists() and not path.is_symlink(),
                 'prepared_output_already_exists')
            os.link(temp,path)
            os.unlink(temp)
        fd2=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd2)
        finally:os.close(fd2)
    finally:
        temp.unlink(missing_ok=True)

def receipt(outcome, code, config_sha=None):
    payload={'outcome':outcome,'code':code,'config_sha256':config_sha}
    atom_write(RECEIPT,(json.dumps(payload,sort_keys=True)+'\n').encode(),replace=True)

def prepare():
    proper_context();services_ok();pg_status()
    need(file_sha(CFG)==OLD_CFG_SHA,'live_config_changed')
    need(not BACKUP.exists() and not PREPARED.exists() and not RECEIPT.exists(),
         'handoff_already_prepared')
    original=CFG.read_bytes()
    proposed=transformed_bytes(original)
    atom_write(BACKUP,original)
    try:atom_write(PREPARED,proposed)
    except Exception:
        BACKUP.unlink(missing_ok=True);raise
    need(file_sha(CFG)==OLD_CFG_SHA,'config_changed_during_prepare')
    print('STAGE33IB_PRIVATE_BACKUP_AND_FIXED_DRAFT_READY')
    print('STAGE33IB_SEVEN_PLUGINS_ONLY_ONE_ENTRY_CHANGED')
    print('STAGE33IB_CANDIDATE_CLI_AND_JS_HASH_PINNED')
    print('STAGE33IB_PROPOSED_CONFIG_SHA256='+sha(proposed))
    print('STAGE33IB_NO_RUNTIME_OR_DB_CHANGE')

def prepared_ok():
    proper_context()
    need(trusted_file(BACKUP,private=True) and trusted_file(PREPARED,private=True),
         'handoff_artifacts_missing')
    need(sha(BACKUP.read_bytes())==OLD_CFG_SHA,'backup_hash_changed')
    proposed=transformed_bytes(BACKUP.read_bytes())
    need(PREPARED.read_bytes()==proposed,'prepared_config_changed')
    return proposed

def worker():
    proposed=prepared_ok();services_ok()
    need(file_sha(CFG)==OLD_CFG_SHA,'live_config_changed_before_handoff')
    need(not RECEIPT.exists(),'handoff_already_attempted')
    print('STAGE33IB_WAITING_FOR_ALL_MULTITOOL_PROCESSES_TO_EXIT',flush=True)
    t0=time.monotonic(); quiet_since=None
    while time.monotonic()-t0<WAIT_SECONDS:
        procs,unknown=safe_process_list()
        if procs or unknown:quiet_since=None
        elif quiet_since is None:quiet_since=time.monotonic()
        elif time.monotonic()-quiet_since>=STABLE_SECONDS:break
        time.sleep(2)
    else:
        receipt('blocked','multitool_processes_not_quiet')
        raise Stop('multitool_processes_not_quiet')
    p,u=safe_process_list()
    need(not p and not u,'multitool_runtime_reappeared')
    pg_status();services_ok()
    state_before=durable_state_guard()
    need(file_sha(CFG)==OLD_CFG_SHA,'live_config_changed_during_wait')
    # Final check immediately before atomic swap; no process termination.
    p,u=safe_process_list()
    need(not p and not u,'multitool_runtime_changed_at_commit')
    need(durable_state_guard()==state_before,'durable_state_changed_before_commit')
    atom_write(CFG,proposed,replace=True)
    os.chmod(CFG,0o600)
    need(durable_state_guard()==state_before,'durable_state_changed_at_commit')
    need(file_sha(CFG)==sha(proposed),'config_install_verify_failed')
    receipt('committed','ACKFIX_PLUGIN_REGISTERED',sha(proposed))
    print('STAGE33IB_ACKFIX_PLUGIN_CONFIG_ATOMICALLY_INSTALLED',flush=True)
    print('STAGE33IB_PREVIOUS_DURABLE_STATE_UNCHANGED',flush=True)
    print('STAGE33IB_NO_SDK_NO_DB_WRITES_NO_TUNNEL_SWITCH',flush=True)

def verify():
    proposed=prepared_ok()
    need(file_sha(CFG)==sha(proposed),'fixed_config_not_installed')
    need(trusted_file(RECEIPT,private=True),'handoff_receipt_missing')
    status=json.loads(RECEIPT.read_bytes())
    need(status['outcome']=='committed' and status['code']=='ACKFIX_PLUGIN_REGISTERED'
         and status['config_sha256']==sha(proposed),'handoff_receipt_not_committed')
    services_ok();pg_status();durable_state_guard()
    print('STAGE33IB_SEVEN_PLUGIN_ACKFIX_INSTALLED_GIT_ABSENT')
    print('STAGE33IB_SEQ5_AND_SEQ6_DELIVERED_UNCHANGED')
    print('STAGE33IB_VERIFY_COMPLETE_NO_SDK_OR_NEW_MESSAGES')
    print(json.dumps({'stage':'33I-B','config_sha256':sha(proposed),
       'candidate_plugin_sha256':NEW_JS_SHA,'candidate_cli_sha256':NEW_CLI_SHA,
       'canary_last_seq':6,'seq5':'delivered','seq6':'delivered',
       'pg_writes_by_package':False,'sdk_called_by_package':False},sort_keys=True))

def rollback():
    proposed=prepared_ok()
    need(file_sha(CFG)==sha(proposed),'unexpected_config_before_rollback')
    p,u=safe_process_list()
    need(not p and not u,'rollback_requires_all_mt_closed')
    services_ok();pg_status();durable_state_guard() # Only safe with same cursor and no new tasks
    atom_write(CFG,BACKUP.read_bytes(),replace=True)
    os.chmod(CFG,0o600)
    receipt('rolled_back','PREVIOUS_PG_CONFIG_RESTORED',OLD_CFG_SHA)
    print('STAGE33IB_ROLLBACK_CONFIG_ONLY_NO_DB_WRITES')

def status():
    proper_context()
    p,u=safe_process_list()
    info={'stage':'33I-B', 'config_sha256':file_sha(CFG),
          'old_config_active':file_sha(CFG)==OLD_CFG_SHA,
          'new_config_active':PREPARED.is_file() and file_sha(CFG)==sha(PREPARED.read_bytes()),
          'candidate_js_sha256':file_sha(NEW),
          'candidate_cli_sha256':file_sha(NEW_CLI),
          'multitool_processes':p,'unknown_process_pids':u,
          'receipt':json.loads(RECEIPT.read_bytes()) if RECEIPT.exists() else None,
          'sdk_invoked_by_this_package':False,'pg_mutated_by_this_package':False}
    print(json.dumps(info,ensure_ascii=False,indent=2,sort_keys=True))

def main(argv):
    cmd=argv[1] if len(argv)==2 else ''
    try:
        if cmd=='prepare':prepare()
        elif cmd=='worker':worker()
        elif cmd=='verify':verify()
        elif cmd=='rollback':rollback()
        elif cmd=='status':status()
        else:raise Stop('unsupported_command')
        return 0
    except Stop as e:
        print('STAGE33IB_STOP_'+str(e),file=sys.stderr,flush=True)
        return 2
    except Exception as e:
        print('STAGE33IB_STOP_'+type(e).__name__,file=sys.stderr,flush=True)
        return 2
if __name__=='__main__':raise SystemExit(main(sys.argv))
