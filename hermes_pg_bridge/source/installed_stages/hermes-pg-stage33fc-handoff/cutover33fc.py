#!/usr/bin/env python3
"""Stage 33F-C: consent-gated, zero-MultiTool-process config handoff.

Never kills processes, starts the SDK, arms SQL writes, or touches the tunnel.
Only mutation: atomic replacement of opencode.json after full shutdown.
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

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage33fc-handoff'
CFG=HOME/'.config/gigatool/opencode.json'
FB=HOME/'hermes-pg-stage33fb-mcp-ready'
SNAP=FB/'opencode-original-snapshot.json'
DRAFT=FB/'opencode-stage33f-draft.json'
CANDIDATE=HOME/'hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs'
ORIG_SHA='83a8a84d40379d230f30cd48623abefb2eed6d33be6ffa49a3d59d860a10acc5'
DRAFT_SHA='46de93ce5de3011d05e6764c59627a0051475c7b1b80bea5b4d5ce411de4a95f'
CAND_SHA='3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
BACKUP=ROOT/'original-private.json'
FINAL=ROOT/'pg-only-cutover.json'
STATE=ROOT/'handoff-result.json'
WAIT_SECONDS=900
STABLE_SECONDS=12

class Stop(RuntimeError):pass

def need(ok,code):
    if not ok: raise Stop(code)

def sha(b): return hashlib.sha256(b).hexdigest()

def trusted_file(p):
    try:
        s=p.lstat()
        return stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and not p.is_symlink()
    except OSError:return False

def context():
    need(os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali' and
         HOME==Path.home() and Path.cwd().resolve()==ROOT,'wrong_local_context')
    need(trusted_file(CFG) and trusted_file(SNAP) and trusted_file(DRAFT) and trusted_file(CANDIDATE),
         'untrusted_input')
    need(sha(SNAP.read_bytes())==ORIG_SHA,'snapshot_hash_changed')
    need(sha(DRAFT.read_bytes())==DRAFT_SHA,'draft_hash_changed')
    need(sha(CANDIDATE.read_bytes())==CAND_SHA,'candidate_hash_changed')

def plugin_path(x):
    if isinstance(x,str):return x
    if isinstance(x,list) and len(x)==2 and isinstance(x[0],str) and isinstance(x[1],dict):return x[0]
    raise Stop('plugin_entry_invalid')

def make_final(original:bytes,draft:bytes)->bytes:
    a=json.loads(original);b=json.loads(draft)
    x=a.get('plugin');y=b.get('plugin')
    need(isinstance(x,list) and isinstance(y,list) and len(x)==len(y)==8,'plugin_count_not_eight')
    git=[i for i,v in enumerate(x) if Path(plugin_path(v)).name=='mt-git-inbox.mjs']
    old=[i for i,v in enumerate(x) if Path(plugin_path(v)).name=='mt-pg-inbox-canary.mjs']
    new=[i for i,v in enumerate(y) if plugin_path(v)==str(CANDIDATE)]
    need(len(git)==len(old)==len(new)==1 and old==new,'plugin_targets_not_unique')
    need(git[0]!=old[0],'git_and_pg_same_slot')
    need(all(x[i]==y[i] for i in range(8) if i!=old[0]),'unexpected_draft_edit')
    need(x[old[0]]!=y[old[0]],'pg_not_replaced')
    need(sum(Path(plugin_path(v)).name=='mt-git-inbox.mjs' for v in y)==1,'git_entry_missing_in_draft')
    # Ensure exact config structure and all other keys were preserved by the prior draft.
    aa=dict(a);bb=dict(b);del aa['plugin'];del bb['plugin']
    need(aa==bb,'top_level_config_changed_in_draft')
    final=json.loads(draft)
    final['plugin']=[v for i,v in enumerate(y) if i!=git[0]]
    need(len(final['plugin'])==7,'final_plugin_count_mismatch')
    paths=[plugin_path(v) for v in final['plugin']]
    need(str(CANDIDATE) in paths,'stage33f_candidate_missing')
    need(not any(Path(p).name in ('mt-git-inbox.mjs','mt-pg-inbox-canary.mjs') for p in paths),
         'legacy_plugins_present')
    for i,old_entry in enumerate(x):
        if i not in (git[0],old[0]): need(old_entry in final['plugin'],'unrelated_plugin_missing')
    return (json.dumps(final,ensure_ascii=False,indent=2)+'\n').encode('utf-8')

def atom_write(path:Path,data:bytes,replace=False):
    temp=path.with_name('.'+path.name+'.tmp-'+str(os.getpid()))
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        if replace:
            os.replace(temp,path)
        else:
            # For prepared artifacts, refusing overwrite is deliberate.
            need(not path.exists() and not path.is_symlink(),'prepared_artifact_already_exists')
            os.link(temp,path)
            os.unlink(temp)
        dfd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if temp.exists():temp.unlink()

def service_state(name):
    try:
        p=subprocess.run(['systemctl','--user','is-active',name],capture_output=True,text=True,timeout=5)
        return p.stdout.strip() if p.stdout else 'unknown'
    except Exception:return 'unknown'

def process_list():
    """Fail-closed snapshot of processes that could have inherited old SDK plugin.

    Never show command line or environment; Node/Bun checked internally for app markers.
    """
    out=[];unknown=[]
    uid=os.geteuid()
    for d in Path('/proc').iterdir():
        if not d.name.isdecimal():continue
        try:
            s=d.stat()
            if s.st_uid!=uid:continue
            comm=(d/'comm').read_text('utf8').strip()[:32]
            lc=comm.lower()
            possible=any(m in lc for m in ('multitool','gigatool','opencode'))
            if not possible and lc in ('node','bun','electron'):
                try:
                    cmd=(d/'cmdline').read_bytes().lower()
                    possible=any(m in cmd for m in (b'multitool',b'gigatool',b'opencode'))
                except OSError:
                    unknown.append(int(d.name));continue
            if not possible:continue
            raw=(d/'stat').read_text('utf8')
            tail=raw[raw.rfind(')')+2:].split()
            out.append({'pid':int(d.name),'ppid':int(tail[1]),
                        'start_ticks':int(tail[19]),'comm':comm})
        except FileNotFoundError:continue
        except (OSError,ValueError,IndexError):unknown.append(int(d.name))
    return sorted(out,key=lambda x:x['pid']),sorted(set(unknown))

def actor_guard():
    # Official Stage 33F-B read-only inventory also checks real actor roles,
    # canary state and the local Stage 33F-B MCP. Its context is pinned below.
    import importlib.util
    p=FB/'inventory33fb.py'
    need(trusted_file(p) and sha(p.read_bytes())=='1c33b73b40d89441626c5a047930f515e5fb7166299b9cee6bf96b954fda4422',
         'stage33fb_inventory_changed')
    sys.path.insert(0,str(FB))
    cwd=Path.cwd()
    try:
        os.chdir(FB)
        from inventory33fb import run
        r=run(require_service=True)
    finally:os.chdir(cwd)
    need(r['safe_state']['seq']==4 and not r['safe_state']['test_task_committed'],
         'test_task_not_absent')
    return True

def canary_unmodified_after_config_change():
    # Unlike actor_guard(), this MUST NOT require the original live config SHA:
    # rollback is called after the config was switched to the 7-plugin version.
    import importlib.util
    source=FB/'logic33fb.py'
    need(trusted_file(source) and
         sha(source.read_bytes())=='7bd588ef646ece1b7c47332692a937b669dce5ea4f98c1efddafc170116f5212',
         'stage33fb_canary_logic_changed')
    sys.path.insert(0,str(FB))
    sys.path.insert(0,str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    from logic33fb import status
    ChatGPT,_=actor_classes()
    db=ChatGPT()
    need(db.health().get('ok') is True,'chatgpt_pg_login_unavailable')
    state=status(db)
    need(state['seq']==4 and not state['test_task_committed'],
         'rollback_blocked_test_task_already_sent')


def prepare():
    context()
    original=CFG.read_bytes();need(sha(original)==ORIG_SHA,'live_config_drift')
    final=make_final(original,DRAFT.read_bytes())
    need(not BACKUP.exists() and not FINAL.exists() and not STATE.exists(),'already_prepared')
    actor_guard()
    atom_write(BACKUP,original)
    try:atom_write(FINAL,final)
    except Exception:
        BACKUP.unlink(missing_ok=True);raise
    need(CFG.read_bytes()==original,'live_config_changed_while_preparing')
    print('STAGE33FC_PRIVATE_BACKUP_AND_PG_ONLY_CONFIG_PREPARED')
    print('STAGE33FC_OLD_GIT_AND_CANARY_PLUGINS_REMOVED_FROM_DRAFT')
    print('STAGE33FC_NEW_CONFIG_SHA256='+sha(final))
    print('STAGE33FC_LIVE_CONFIG_NOT_CHANGED')

def inventory():
    context();procs,unknown=process_list()
    data={'stage':'33F-C','config_sha256':sha(CFG.read_bytes()),
          'original_config':sha(CFG.read_bytes())==ORIG_SHA,
          'proposed_config_sha256':sha(FINAL.read_bytes()) if FINAL.exists() else None,
          'process_count':len(procs),'processes':procs,
          'unknown_process_count':len(unknown),
          'git_services':{n:service_state(n) for n in ('hermes-bridge.service','git-dialog-taskboard.service')},
          'pg_services':{n:service_state(n) for n in ('bridge-pg-v2-ssh-shadow.service','hermes-pg-tunnel-one-shot.service','hermes-pg-v2-stage33fb-cg.service')},
          'armed_file_present':(FB/'armed-stage33fc').exists(),
          'task_dispatch_executed_by_this_package':False,'sdk_called_by_this_package':False}
    print(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True))
    print('STAGE33FC_NO_PROCESS_TERMINATION_NO_SDK_NO_DB_WRITE')

def recorded_status(outcome,code,config_sha=None):
    d={'outcome':outcome,'code':code,'current_config_sha256':config_sha}
    atom_write(STATE,(json.dumps(d,sort_keys=True)+'\n').encode(),replace=True)

def worker():
    context()
    need(trusted_file(BACKUP) and trusted_file(FINAL),'handoff_files_missing_or_untrusted')
    need(sha(BACKUP.read_bytes())==ORIG_SHA,'backup_drift')
    need(sha(CFG.read_bytes())==ORIG_SHA,'live_config_modified_before_handoff')
    final=make_final(BACKUP.read_bytes(),DRAFT.read_bytes())
    need(FINAL.read_bytes()==final,'prepared_config_mismatch')
    need(not (FB/'armed-stage33fc').exists(),'canary_write_armed_too_early')
    for n in ('hermes-bridge.service','git-dialog-taskboard.service'):
        need(service_state(n)!='active','legacy_git_service_still_active')
    start=time.monotonic();quiet_since=None
    print('STAGE33FC_WORKER_WAITING_FOR_ALL_MULTITOOL_PROCESSES_TO_EXIT',flush=True)
    while time.monotonic()-start<WAIT_SECONDS:
        procs,unknown=process_list()
        if unknown: quiet_since=None
        elif procs: quiet_since=None
        elif quiet_since is None:quiet_since=time.monotonic()
        elif time.monotonic()-quiet_since>=STABLE_SECONDS:
            break
        time.sleep(2)
    else:
        recorded_status('blocked','multitool_processes_not_quiet')
        raise Stop('multitool_processes_not_quiet')
    procs,unknown=process_list()
    need(not procs and not unknown,'runtime_restart_during_handoff')
    actor_guard()
    need(sha(CFG.read_bytes())==ORIG_SHA,'live_config_changed_during_wait')
    # No new processes should begin before the operator sees completion marker.
    procs,unknown=process_list()
    need(not procs and not unknown,'runtime_changed_at_commit')
    atom_write(CFG,final,replace=True)
    need(CFG.read_bytes()==final,'config_commit_verify_failed')
    os.chmod(CFG,0o600)
    recorded_status('committed','PG_ONLY_CONFIG_INSTALLED',sha(final))
    print('STAGE33FC_PG_ONLY_CONFIG_ATOMICALLY_INSTALLED',flush=True)
    print('STAGE33FC_GIT_PLUGIN_REMOVED_AND_ROLE_AGENT_CANDIDATE_REGISTERED',flush=True)
    print('STAGE33FC_NO_SDK_NO_DB_WRITES_NO_TUNNEL_SWITCH',flush=True)

def verify():
    context();need(trusted_file(FINAL) and trusted_file(BACKUP),'missing_artifacts')
    expected=FINAL.read_bytes();installed=CFG.read_bytes()
    need(sha(BACKUP.read_bytes())==ORIG_SHA,'backup_mismatch')
    need(installed==expected,'new_config_not_installed')
    need(not (FB/'armed-stage33fc').exists(),'send_armed_without_approval')
    need(service_state('hermes-pg-tunnel-one-shot.service')=='active','secure_tunnel_changed')
    need(service_state('bridge-pg-v2-ssh-shadow.service')=='active','postgres_shadow_ssh_offline')
    need(STATE.is_file() and json.loads(STATE.read_text())['outcome']=='committed',
         'handoff_receipt_not_committed')
    print('STAGE33FC_PG_ONLY_CONFIG_VERIFIED_GIT_PLUGIN_ABSENT')
    print('STAGE33FC_NO_SEQ5_AND_SEND_NOT_ARMED')

def rollback():
    context()
    need(trusted_file(BACKUP) and trusted_file(FINAL),'missing_artifacts')
    need(sha(BACKUP.read_bytes())==ORIG_SHA,'backup_mismatch')
    need(CFG.read_bytes()==FINAL.read_bytes(),'rollback_config_mismatch')
    procs,unknown=process_list()
    need(not procs and not unknown,'rollback_requires_all_mt_closed')
    need(not (FB/'armed-stage33fc').exists(),'rollback_blocked_by_send_arm')
    canary_unmodified_after_config_change() # seq5 must be absent
    atom_write(CFG,BACKUP.read_bytes(),replace=True)
    os.chmod(CFG,0o600)
    recorded_status('rolled_back','ORIGINAL_CONFIG_RESTORED',ORIG_SHA)
    print('STAGE33FC_CONFIG_ROLLBACK_OK_NO_DB_CHANGES')

def main(argv):
    need(len(argv)==2,'command_required')
    cmd=argv[1]
    try:
        if cmd=='prepare':prepare()
        elif cmd=='inventory':inventory()
        elif cmd=='watch-commit':worker()
        elif cmd=='verify':verify()
        elif cmd=='rollback-before-seq5':rollback()
        else:raise Stop('unknown_command')
        return 0
    except Stop as e:
        if cmd=='watch-commit' and not STATE.exists():
            try:recorded_status('blocked',str(e))
            except Exception:pass
        print('STAGE33FC_STOP_'+str(e),file=sys.stderr,flush=True)
        return 2
    except Exception as e:
        if cmd=='watch-commit' and not STATE.exists():
            try:recorded_status('blocked',type(e).__name__)
            except Exception:pass
        print('STAGE33FC_STOP_'+type(e).__name__,file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main(sys.argv))
