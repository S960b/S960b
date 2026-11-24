#!/usr/bin/env python3
"""Stage 33D: one isolated, actor-bound PG transaction chain, no SDK dispatch.

This deliberately writes TWO harmless messages to ONE new task, then tombstones
that task and cancels the undelivered ChatGPT outbox. No existing dialogue is
modified. No MultiTool plugin or service changes. One shot only.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re
import stat
import sys

ACTOR_ROOT = Path('/home/kali/hermes-pg-stage33c-shadow-tunnel')
ACTOR_SHA = 'a17910521c2dd6b95ced3ca3ff45ac4e3a4277631050d1f2e887195b2a8af509'
CFG = Path('/home/kali/.config/gigatool/opencode.json')
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
REQUEST_ID = 'pgv2-shadow-roles-33d-20261122-v1'
TITLE = 'PG v2 Stage 33D isolated two-role test; no SDK'
TASK_MID = 'pgv2-33d-shadow-task-v1'
REPORT_MID = 'pgv2-33d-shadow-report-v1'
TASK_BODY = 'STAGE33D_SQL_ROLE_TEST_ONLY_DO_NOT_EXECUTE'
REPORT_BODY = 'STAGE33D_SQL_ROLE_REPORT_OK_NO_MULTITOOL_SDK'
CONSUMER = 'pg-v2-stage33d-isolated'

class Stop(Exception):
    pass

def fail(code):
    raise Stop(code)

def get_adapters():
    path = ACTOR_ROOT/'actor_adapter.py'
    if not path.is_file() or path.is_symlink() or path.stat().st_uid != os.geteuid():
        fail('ACTOR_ADAPTER_NOT_TRUSTED')
    if hashlib.sha256(path.read_bytes()).hexdigest() != ACTOR_SHA:
        fail('ACTOR_ADAPTER_SHA_MISMATCH')
    sys.path.insert(0, str(ACTOR_ROOT))
    from actor_adapter import ChatGPTActorDB, AgentActorDB
    return ChatGPTActorDB(), AgentActorDB()

def locate_plugin(entry):
    if isinstance(entry, str):
        return entry, {}
    if (isinstance(entry, list) and len(entry) == 2 and
        isinstance(entry[0], str) and isinstance(entry[1], dict)):
        return entry[0], entry[1]
    fail('PLUGIN_CONFIG_ENTRY_UNEXPECTED')

def verify_filter(config: dict, *, plugin_src: str) -> None:
    """No writes unless every PG inbox in config is strictly one-task gated."""
    plugins = config.get('plugin')
    if not isinstance(plugins, list) or not plugins:
        fail('PLUGIN_CONFIG_UNEXPECTED')
    canary = []
    for entry in plugins:
        path, opts = locate_plugin(entry)
        name = Path(path).name
        if 'mt-pg-inbox' in name:
            if name != 'mt-pg-inbox-canary.mjs':
                fail('UNFILTERED_PG_PLUGIN_PRESENT')
            canary.append((path, opts))
    if len(canary) != 1:
        fail('CANARY_PLUGIN_NOT_UNIQUE')
    path, opts = canary[0]
    if opts.get('dry_run') is not False or opts.get('task_ids') != [CANARY]:
        fail('CANARY_TASK_FILTER_NOT_FROZEN')
    if CANARY not in plugin_src or 'Object.freeze' not in plugin_src:
        fail('CANARY_SOURCE_HARD_FILTER_NOT_FOUND')
    # MT config supports either an absolute path or a file:// URI.
    if path.startswith('file:///'):
        parsed = urlsplit(path)
        if parsed.netloc or parsed.query or parsed.fragment:
            fail('CANARY_PLUGIN_URI_INVALID')
        normalized_path = unquote(parsed.path)
    else:
        normalized_path = path
    if normalized_path != '/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs':
        fail('CANARY_PLUGIN_PATH_MISMATCH')


def local_preflight():
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'kali' or Path.home() != Path('/home/kali'):
        fail('WRONG_OS_USER')
    if CFG.is_symlink() or not CFG.is_file() or CFG.stat().st_uid != os.geteuid():
        fail('PLUGIN_CONFIG_UNTRUSTED')
    path = Path('/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs')
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid():
        fail('CANARY_PLUGIN_UNTRUSTED')
    config = json.loads(CFG.read_text(encoding='utf-8'))
    verify_filter(config, plugin_src=path.read_text(encoding='utf-8'))
    print('STAGE33D_EXACT_CANARY_FILTER_VERIFIED')


def db_preflight(cg, ag):
    if cg.health().get('ok') is not True or ag.health().get('ok') is not True:
        fail('ACTOR_HEALTH_NOT_READY')
    meta = cg.dialog_metadata(CANARY)
    if meta is None or meta.get('last_seq') != 4 or meta.get('next_actor') != 'chatgpt':
        fail('CANARY_CHANGED_OR_UNEXPECTED_TURN')
    if ag.dialog_metadata(CANARY).get('last_seq') != 4:
        fail('AGENT_CANARY_VIEW_MISMATCH')
    if cg.creation_status(REQUEST_ID) is not None:
        fail('TEST_DIALOG_ALREADY_EXISTS_DO_NOT_REPEAT')
    if ag.delivery_status(CANARY, 3).get('status') != 'delivered':
        fail('CANARY_DELIVERY_CHANGED')
    print('STAGE33D_REAL_ACTOR_LOGINS_AND_CANARY_UNCHANGED')


def perform(cg, ag):
    """After the first write, any failure is reported; no automatic retry."""
    dialog = cg.create_dialog(REQUEST_ID, TITLE)
    if dialog.get('created') is not True or dialog.get('initiator') != 'chatgpt':
        fail('SHADOW_DIALOG_CREATION_UNEXPECTED')
    tid = dialog['task_id']
    if not re.fullmatch(r'task_[0-9a-f]{32}', tid):
        fail('SHADOW_TASK_ID_INVALID')
    print('STAGE33D_ISOLATED_DIALOG_CREATED id='+tid, flush=True)

    first = cg.send_message(tid, TASK_MID, 'task', TASK_BODY, expected_version=0)
    if first.get('created') is not True or first.get('seq') != 1 or first.get('version') != 1:
        fail('SHADOW_CHATGPT_MESSAGE_UNEXPECTED')
    # This must be a *read-only* assertion on the idempotent existing message.
    again = cg.send_message(tid, TASK_MID, 'task', TASK_BODY, expected_version=0)
    if again.get('created') is not False or again.get('seq') != 1:
        fail('SHADOW_DUPLICATE_CREATED')
    print('STAGE33D_CHATGPT_BOUND_SEND_AND_DEDUP_OK', flush=True)

    lease = ag.claim_message(tid, 1, TASK_MID, CONSUMER, lease_seconds=60)
    if lease.get('claimed') is not True or not lease.get('lease_token') or not isinstance(lease.get('delivery_id'), int):
        fail('AGENT_CLAIM_FAILED')
    if ag.ack_outbox(lease['delivery_id'], str(lease['lease_token']), CONSUMER) is not True:
        fail('AGENT_ACK_FAILED')
    if ag.delivery_status(tid, 1).get('status') != 'delivered':
        fail('AGENT_DELIVERY_NOT_ACKED')
    print('STAGE33D_AGENT_BOUND_CLAIM_AND_ACK_OK', flush=True)

    report = ag.send_report(tid, REPORT_MID, 'report', REPORT_BODY, expected_version=1)
    if report.get('created') is not True or report.get('seq') != 2 or report.get('version') != 2:
        fail('AGENT_BOUND_REPORT_FAILED')
    view = cg.view_dialog(tid, 0, 3)
    msg = view.get('messages')
    if not isinstance(msg, list) or len(msg) != 2:
        fail('CHATGPT_REPORT_NOT_VISIBLE')
    if [(m['seq'],m['sender'],m['body']) for m in msg] != [
        (2,'agent',REPORT_BODY),(1,'chatgpt',TASK_BODY)]:
        fail('CHATGPT_REPORT_CONTENT_UNEXPECTED')
    print('STAGE33D_AGENT_BOUND_REPORT_AND_CHATGPT_READ_OK', flush=True)

    done = cg.tombstone(tid, expected_version=2)
    if done.get('deleted') is not True:
        fail('ISOLATED_DIALOG_TOMBSTONE_FAILED')
    meta = cg.dialog_metadata(tid)
    if meta.get('state') != 'tombstoned' or meta.get('last_seq') != 2:
        fail('ISOLATED_DIALOG_CLEANUP_UNEXPECTED')
    if cg.delivery_status(tid, 2).get('status') != 'cancelled':
        fail('ISOLATED_CHATGPT_OUTBOX_NOT_CANCELLED')
    if cg.dialog_metadata(CANARY).get('last_seq') != 4:
        fail('EXISTING_CANARY_CHANGED')
    print('STAGE33D_ISOLATED_OUTBOX_CLEANED_AND_CANARY_PRESERVED', flush=True)
    print('STAGE33D_SHADOW_ACTORS_ROUNDTRIP_VERIFIED_NO_MT_SDK', flush=True)


def verify_only(cg, ag):
    creation = cg.creation_status(REQUEST_ID)
    if creation is None:
        fail('SHADOW_DIALOG_NOT_YET_CREATED')
    tid = creation['task_id']
    meta = cg.dialog_metadata(tid)
    first = cg.send_status(tid, TASK_MID)
    second = ag.send_status(tid, REPORT_MID)
    if (meta.get('state') != 'tombstoned' or meta.get('last_seq') != 2 or
        first.get('found') is not True or first.get('sender') != 'chatgpt' or
        second.get('found') is not True or second.get('sender') != 'agent' or
        ag.delivery_status(tid,1).get('status') != 'delivered' or
        cg.delivery_status(tid,2).get('status') != 'cancelled' or
        cg.dialog_metadata(CANARY).get('last_seq') != 4):
        fail('SHADOW_ROUNDTRIP_NOT_CLEANLY_FINISHED')
    print('STAGE33D_VERIFY_ONLY_COMPLETE_SHADOW_CLOSED', flush=True)


def main(argv):
    if argv not in (['--preflight'],['--run-once'],['--verify-only']):
        print('STAGE33D_STOP_INVALID_MODE', file=sys.stderr)
        return 2
    try:
        local_preflight()
        cg, ag = get_adapters()
        if argv == ['--preflight']:
            db_preflight(cg,ag)
            print('STAGE33D_READ_ONLY_PREFLIGHT_OK')
        elif argv == ['--run-once']:
            db_preflight(cg,ag)
            perform(cg,ag)
        else:
            verify_only(cg,ag)
    except Stop as err:
        print('STAGE33D_STOP_'+str(err), file=sys.stderr)
        return 2
    except Exception as err:
        # No DB error strings: may contain passwords/DSNs or user content.
        print('STAGE33D_STOP_EXCEPTION_'+type(err).__name__, file=sys.stderr)
        return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
