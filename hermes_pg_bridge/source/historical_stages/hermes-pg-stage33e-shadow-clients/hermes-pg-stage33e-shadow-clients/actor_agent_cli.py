#!/usr/bin/env python3
"""Shadow MT CLI for Stage 33E: SAME read-only interface, NO mutations/SDK.

Intentionally bounded to the existing, completed canary task. This is NOT the
production agent CLI. Writes, ACKs and claims are unavailable in this stage.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, '/home/kali/bridge-pg-v2')

from actor_import33e import actor_classes

CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
MID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')

class CLIError(Exception):
    pass


def validate_task(task_id):
    if task_id != CANARY:
        raise CLIError('shadow_task_not_allowed')


def bounded(value, maximum=100):
    if type(value) is not int or not 0 <= value <= maximum:
        raise CLIError('invalid_page')


def format_message(task_id, r):
    from bridge_pg.compat import _message
    return _message(r, task_id)


def dispatch(args, agent):
    from bridge_pg.compat import version_token
    if args.command == 'list':
        bounded(args.offset,10000); bounded(args.limit,100)
        if args.limit < 1:
            raise CLIError('invalid_page')
        meta=agent.dialog_metadata(CANARY)
        if not isinstance(meta,dict) or meta.get('state')!='active':
            raise CLIError('canary_not_active')
        # No other tasks or message bodies are exposed from this shadow CLI.
        row={'task_id':CANARY,'branch':None,'title':meta['title'],
             'initiator':'chatgpt','lifecycle':'active'}
        return {'dialogs':[row] if args.offset==0 else [],'next_offset':None,'order':'newest'}
    if args.command in ('append','claim','ack','create'):
        raise CLIError('stage33e_writes_disabled')
    validate_task(args.task_id)
    if args.command == 'peek':
        bounded(args.offset,10000);bounded(args.limit,100)
        if args.limit < 1:raise CLIError('invalid_page')
        meta=agent.dialog_metadata(CANARY)
        page=agent.view_dialog(CANARY,args.offset,args.limit)
        if meta.get('state')!='active' or not isinstance(page,dict):
            raise CLIError('canary_not_active')
        rows=[format_message(CANARY,r) for r in page['messages']]
        total=int(page['last_seq'])
        return {'task_id':CANARY,'title':meta['title'],
                'head':version_token(CANARY,int(meta['version'])),
                'waiting_for':'mt' if meta['next_actor']=='agent' else 'chatgpt',
                'messages':rows,'history':rows,'seq':total,'total_messages':total,
                'next_offset':args.offset+len(rows) if args.offset+len(rows)<total else None,
                'lifecycle':'active'}
    if args.command=='read':
        if type(args.seq) is not int or args.seq<1 or args.seq>4 or not MID.fullmatch(args.message_id or ''):
            raise CLIError('invalid_message_selector')
        row=agent._one('SELECT bridge.get_message(%s,%s,%s) AS result',
                       (CANARY,args.seq,args.message_id))
        if row is None or row.get('seq')!=args.seq or row.get('message_id')!=args.message_id:
            raise CLIError('message_not_found')
        return format_message(CANARY,row)
    if args.command=='status':
        if not MID.fullmatch(args.message_id or ''):
            raise CLIError('invalid_message_selector')
        r=agent.send_status(CANARY,args.message_id)
        if r is None or not r.get('found'):
            return {'task_id':CANARY,'message_id':args.message_id,'attempted':False,
                    'confirmed_seq':None,'phase':'pre_journal'}
        if r.get('sender')!='agent':
            raise CLIError('actor_denied')
        return {'task_id':CANARY,'message_id':args.message_id,'attempted':True,
                'confirmed_seq':r['seq'],'phase':'committed'}
    raise CLIError('unsupported_operation')


def main(argv=None):
    p=argparse.ArgumentParser(description='Stage 33E bounded, read-only MT role CLI')
    sub=p.add_subparsers(dest='command',required=True)
    for command in ('list','peek','read','append','status','create','claim','ack'):
        q=sub.add_parser(command)
        q.add_argument('--task-id')
        q.add_argument('--message-id')
        q.add_argument('--seq',type=int)
        q.add_argument('--offset',type=int,default=0)
        q.add_argument('--limit',type=int,default=20)
        # Parse old CLI options for fail-closed compatibility; never read --body-file.
        if command=='append':
            q.add_argument('--head');q.add_argument('--kind');q.add_argument('--body-file')
        if command=='create':q.add_argument('--request-id');q.add_argument('--title')
        if command in ('claim','ack'):q.add_argument('--consumer')
        if command=='ack':q.add_argument('--delivery-id');q.add_argument('--lease-token')
    args=p.parse_args(argv)
    try:
        _, Agent = actor_classes()
        out=dispatch(args,Agent())
        print(json.dumps(out,ensure_ascii=False,separators=(',',':'),default=str))
        return 0
    except (CLIError,Exception) as e:
        code=str(e) if isinstance(e,CLIError) else 'shadow_cli_unavailable'
        print(json.dumps({'ok':False,'error':code}))
        return 2

if __name__=='__main__':raise SystemExit(main())
