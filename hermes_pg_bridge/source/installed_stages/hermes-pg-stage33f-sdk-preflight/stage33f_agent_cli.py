#!/usr/bin/env python3
"""Stage 33F LIVE-CAPABLE but NOT ACTIVATED agent CLI for ONE fixed test turn.

Only the bounded task_id and message IDs below are accepted. This file is
not installed in opencode.json and must not be executed for mutations during
preflight. Actual activation is a separate, explicitly approved stage.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
TASK_MID='pgv2-stage33f-sdk-task-v1'
REPORT_MID='pgv2-stage33f-sdk-report-v1'
ACK='PGV2_ROLE_BOUND_MT_SDK_ACK_20261122'
CONSUMER='mt-pg-inbox'

class Stop(Exception): pass

def need(ok, code):
    if not ok: raise Stop(code)

def version_token(task_id, version):
    return hashlib.sha1(f'pgbridge-v2\x00{task_id}\x00{version}'.encode()).hexdigest()

def format_time(value):
    if value is None: return None
    if isinstance(value,str): dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    else: dt=value
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def message(task_id,row):
    return {'protocol':'pg-dialog','version':'2','task_id':task_id,
      'message_id':row['message_id'],'seq':row['seq'],
      'from':'mt' if row['sender']=='agent' else 'chatgpt',
      'to':'mt' if row['recipient']=='agent' else 'chatgpt',
      'reply_to':None,'kind':row['kind'],'body':row['body'],
      'returned':row.get('returned',False),'created_at_utc':format_time(row['created_at'])}

def metadata(agent):
    m=agent.dialog_metadata(CANARY)
    need(isinstance(m,dict) and m.get('state')=='active' and
         type(m.get('last_seq')) is int and 4 <= m['last_seq'] <= 6,
         'canary_state_not_allowed')
    return m

def dispatch(args,agent):
    op=args.command
    need(op in ('list','peek','read','claim','ack','append','status'), 'operation_denied')
    if op == 'list':
        need(type(args.offset) is int and 0<=args.offset<=10000 and
             type(args.limit) is int and 1<=args.limit<=100,'invalid_page')
        m=metadata(agent)
        return {'dialogs':([{'task_id':CANARY,'branch':None,'title':m['title'],
          'initiator':'chatgpt','lifecycle':'active'}] if args.offset==0 else []),
          'next_offset':None,'order':'newest'}
    need(args.task_id == CANARY,'task_not_allowed')
    if op == 'peek':
        need(type(args.offset) is int and 0<=args.offset<=10000 and
             type(args.limit) is int and 1<=args.limit<=100,'invalid_page')
        m=metadata(agent)
        page=agent.view_dialog(CANARY,args.offset,args.limit)
        need(isinstance(page,dict) and page.get('last_seq')==m['last_seq'] and
             isinstance(page.get('messages'),list),'view_unavailable')
        rows=[message(CANARY,r) for r in page['messages']]
        total=m['last_seq']
        return {'task_id':CANARY,'title':m['title'],'head':version_token(CANARY,m['version']),
          'waiting_for':'mt' if m['next_actor']=='agent' else 'chatgpt',
          'messages':rows,'history':rows,'seq':total,'total_messages':total,
          'next_offset':args.offset+len(rows) if args.offset+len(rows)<total else None,
          'lifecycle':'active'}
    if op=='read':
        need(args.seq==5 and args.message_id==TASK_MID,'message_not_allowed')
        row=agent._one('SELECT bridge.get_message(%s,%s,%s) AS result',(CANARY,5,TASK_MID))
        need(isinstance(row,dict) and row.get('sender')=='chatgpt' and
             row.get('recipient')=='agent' and row.get('message_id')==TASK_MID,
             'task_message_not_found')
        return message(CANARY,row)
    if op=='claim':
        need(args.seq==5 and args.message_id==TASK_MID and args.consumer==CONSUMER,
             'claim_not_allowed')
        m=metadata(agent)
        need(m['last_seq']==5 and m['next_actor']=='agent','claim_turn_not_allowed')
        return agent.claim_message(CANARY,5,TASK_MID,CONSUMER,lease_seconds=3600)
    if op=='ack':
        need(type(args.delivery_id) is int and args.delivery_id>0 and
             isinstance(args.lease_token,str) and
             re.fullmatch(r'[0-9a-fA-F-]{36}',args.lease_token) and
             args.consumer==CONSUMER,'ack_args_denied')
        status=agent.delivery_status(CANARY,5)
        need(isinstance(status,dict) and status.get('delivery_id')==args.delivery_id and
             status.get('status')=='leased','ack_not_bound_to_test_turn')
        return {'acked':agent.ack_outbox(args.delivery_id,args.lease_token,CONSUMER) is True}
    if op=='status':
        need(args.message_id==REPORT_MID,'status_not_allowed')
        data=agent.send_status(CANARY,REPORT_MID)
        if not data or not data.get('found'):
            return {'task_id':CANARY,'message_id':REPORT_MID,
                    'attempted':False,'confirmed_seq':None,'phase':'pre_journal'}
        need(data.get('sender')=='agent' and data.get('seq')==6,'report_status_mismatch')
        return {'task_id':CANARY,'message_id':REPORT_MID,'attempted':True,
                'confirmed_seq':6,'phase':'committed'}
    if op=='append':
        need(args.kind=='report' and args.message_id==REPORT_MID and
             isinstance(args.body_file,str) and args.body_file,'report_not_allowed')
        bpath=Path(args.body_file)
        need(bpath.is_file() and not bpath.is_symlink() and bpath.stat().st_size<=256,
             'report_file_not_allowed')
        body=bpath.read_text(encoding='utf-8').strip()
        need(body==ACK,'report_body_not_allowed')
        m=metadata(agent)
        need(m['last_seq']==5 and m['next_actor']=='agent' and
             args.head==version_token(CANARY,m['version']), 'report_wrong_turn_or_head')
        row=agent.send_report(CANARY,REPORT_MID,'report',ACK,expected_version=m['version'])
        need(isinstance(row,dict) and row.get('seq')==6,'report_commit_unconfirmed')
        return {'task_id':CANARY,'message_id':REPORT_MID,'seq':6,
          'created':row['created'],'version':row['version'],
          'head':version_token(CANARY,row['version'])}
    raise Stop('operation_denied')

def parse_args(argv=None):
    ap=argparse.ArgumentParser()
    commands=ap.add_subparsers(dest='command',required=True)
    for cmd in ('list','peek','read','claim','ack','append','status','create'):
        q=commands.add_parser(cmd)
        q.add_argument('--task-id');q.add_argument('--message-id')
        q.add_argument('--seq',type=int);q.add_argument('--offset',type=int,default=0)
        q.add_argument('--limit',type=int,default=20)
        q.add_argument('--consumer');q.add_argument('--delivery-id',type=int)
        q.add_argument('--lease-token');q.add_argument('--head')
        q.add_argument('--kind');q.add_argument('--body-file')
    return ap.parse_args(argv)

def main(argv=None):
    args=parse_args(argv)
    try:
        # Import is kept inside the actual invocation; offline tests use fakes.
        sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
        from actor_import33e import actor_classes
        _,Agent=actor_classes()
        out=dispatch(args,Agent())
        print(json.dumps(out,ensure_ascii=False,separators=(',',':'),default=str))
        return 0
    except Stop as e:
        print(json.dumps({'ok':False,'error':str(e)}))
        return 2
    except Exception:
        print('{"ok":false,"error":"actor_cli_unavailable"}')
        return 2

if __name__=='__main__':raise SystemExit(main())
