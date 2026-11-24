#!/usr/bin/env python3
"""Independent read-only verify: exact rendered bytes and DB final receipts."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from build33i import (ROOT,NEW_PLUGIN,NEW_CLI,ORIGINAL_PLUGIN,ORIGINAL_CLI,CONFIG,
                      EXPECTED_PLUGIN_SHA,EXPECTED_CLI_SHA,EXPECTED_CONFIG_SHA,
                      patch_js,patch_cli,sha,trusted,CANARY)


def main():
    if Path.cwd().resolve()!=ROOT:
        raise RuntimeError('invalid_workdir')
    for p in (ORIGINAL_PLUGIN,ORIGINAL_CLI,CONFIG,NEW_PLUGIN,NEW_CLI):
        if not trusted(p): raise RuntimeError('untrusted_file')
    if sha(ORIGINAL_PLUGIN.read_bytes())!=EXPECTED_PLUGIN_SHA or sha(ORIGINAL_CLI.read_bytes())!=EXPECTED_CLI_SHA or sha(CONFIG.read_bytes())!=EXPECTED_CONFIG_SHA:
        raise RuntimeError('active_files_changed')
    if NEW_PLUGIN.read_text('utf8')!=patch_js(ORIGINAL_PLUGIN.read_text('utf8')):
        raise RuntimeError('candidate_plugin_differs_from_pure_render')
    if NEW_CLI.read_text('utf8')!=patch_cli(ORIGINAL_CLI.read_text('utf8')):
        raise RuntimeError('candidate_cli_differs_from_pure_render')
    cfg=json.loads(CONFIG.read_text('utf8'))
    if len(cfg.get('plugin',[]))!=7:
        raise RuntimeError('plugin_count_changed')
    entries=cfg['plugin']
    if sum(1 for e in entries if isinstance(e,list) and e[0]==str(ORIGINAL_PLUGIN))!=1:
        raise RuntimeError('active_plugin_missing')
    if any(str(NEW_PLUGIN) in str(e) for e in entries):
        raise RuntimeError('candidate_active_unexpectedly')
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    ChatGPT, Agent = actor_classes()
    cg=ChatGPT(); ag=Agent()
    for actor in (cg,ag):
        if actor.health().get('ok') is not True:
            raise RuntimeError('postgresql_actor_health_failed')
        meta=actor.dialog_metadata(CANARY)
        if meta.get('state')!='active' or meta.get('last_seq')!=6:
            raise RuntimeError('canary_seq_changed')
        for seq in (5,6):
            status=actor.delivery_status(CANARY,seq)
            if not isinstance(status,dict) or status.get('status')!='delivered':
                raise RuntimeError('canary_receipt_not_delivered')
    print('STAGE33I_BOTH_ACTOR_ROLES_AND_SEQ5_SEQ6_DELIVERED')
    print('STAGE33I_ACK_RECOVERY_CODE_MATCHES_PINNED_SOURCE')
    print('STAGE33I_CANDIDATES_NOT_REGISTERED')
    print('STAGE33I_NO_RUNTIME_OR_DATABASE_CHANGE')
    print('STAGE33I_VERIFY_ONLY_COMPLETE')
    print(json.dumps({'stage':'33I-A','active_config_sha256':EXPECTED_CONFIG_SHA,
       'active_plugin_sha256':EXPECTED_PLUGIN_SHA,
       'candidate_plugin_sha256':sha(NEW_PLUGIN.read_bytes()),
       'candidate_cli_sha256':sha(NEW_CLI.read_bytes()),
       'canary_seq':6,'seq5':'delivered','seq6':'delivered',
       'activated':False,'sdk_called':False,'db_writes_performed':False},sort_keys=True))

if __name__=='__main__':
    try: main()
    except Exception as e:
        print('STAGE33I_VERIFY_STOP_'+type(e).__name__+'_'+str(e).splitlines()[0][:80])
        raise SystemExit(2)
