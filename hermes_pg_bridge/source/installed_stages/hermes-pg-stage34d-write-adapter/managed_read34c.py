#!/usr/bin/env python3
"""Stage34C inert managed-only bridge_agent CLI.

READ ONLY in this stage. The future write-capable CLI must be separately
reviewed and installed; changing --dry-run or the plugin config cannot enable
claim/ack/append through this CLI. Queries are constant SQL, with true
bridge_agent credentials and SECURITY DEFINER role checks.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

TASK = re.compile(r'^task_[0-9a-f]{32}$')
MID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
ALLOWED_READ = ('list','peek','read','status','delivery','health')
DENIED = ('claim','ack','append','create','release')

class Refused(Exception): pass

def need(ok, code):
    if not ok: raise Refused(code)

def check_id(value):
    need(isinstance(value,str) and TASK.fullmatch(value),'invalid_task_id')
    return value

def check_mid(value):
    need(isinstance(value,str) and MID.fullmatch(value),'invalid_message_id')
    return value

def version_token(tid, v):
    need(type(v) is int and v>=0,'invalid_version')
    return hashlib.sha1(f'pgbridge-v2\x00{tid}\x00{v}'.encode()).hexdigest()

def iso(value):
    if value is None:return None
    d=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    return d.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def message(tid,row):
    need(isinstance(row,dict) and row.get('sender') in ('chatgpt','agent') and
        row.get('recipient') in ('chatgpt','agent'),'invalid_message_row')
    return {'protocol':'pg-dialog','version':'2','task_id':tid,
        'message_id':row['message_id'],'seq':row['seq'],
        'from':'mt' if row['sender']=='agent' else 'chatgpt',
        'to':'mt' if row['recipient']=='agent' else 'chatgpt',
        'kind':row['kind'],'reply_to':None,'body':row['body'],
        'returned':row.get('returned',False),'created_at_utc':iso(row.get('created_at'))}

def query(agent,sql,args=()):
    # Constant statements only; caller supplies parameter values, never SQL.
    return agent._one(sql,args)

def dispatch(a, agent):
    op=a.command
    need(op in ALLOWED_READ or op in DENIED,'unsupported_operation')
    if op in DENIED: raise Refused('stage34c_mutations_disabled')
    if op=='health':
        result=query(agent,'SELECT bridge.ag_dispatch_list(%s,%s) AS result',(0,1))
        need(isinstance(result,list),'managed_dispatch_unavailable')
        return {'actor_role':'bridge_agent','read_only':True,'managed_dispatch_page_count':len(result)}
    if op=='list':
        need(type(a.offset) is int and 0<=a.offset<=100000 and
             type(a.limit) is int and 1<=a.limit<=50,'invalid_page')
        rows=query(agent,'SELECT bridge.ag_dispatch_list(%s,%s) AS result',(a.offset,a.limit))
        need(isinstance(rows,list) and len(rows)<=a.limit,'invalid_dispatch_rows')
        for x in rows:
            need(isinstance(x,dict) and TASK.fullmatch(x.get('task_id','')) and
                 x.get('next_actor')=='agent','invalid_dispatch_row')
        return {'dialogs':[{'task_id':x['task_id'],'branch':None,'title':x['title'],
                 'initiator':'chatgpt','lifecycle':'active'} for x in rows],
                'next_offset':a.offset+len(rows) if len(rows)==a.limit else None,
                'order':'newest'}
    tid=check_id(a.task_id)
    if op=='peek':
        need(type(a.offset) is int and 0<=a.offset<=100000 and
             type(a.limit) is int and 1<=a.limit<=50,'invalid_page')
        page=query(agent,'SELECT bridge.ag_view_managed_dialog(%s,%s,%s) AS result',
                   (tid,a.offset,a.limit))
        need(isinstance(page,dict) and page.get('task_id')==tid and
             page.get('state')=='active' and isinstance(page.get('messages'),list),'managed_view_unavailable')
        history=[message(tid,x) for x in page['messages']]
        n=page['last_seq']
        need(type(n) is int and n>=0,'invalid_dialog_seq')
        return {'task_id':tid,'title':page['title'],'head':version_token(tid,page['version']),
            'waiting_for':'mt' if page['next_actor']=='agent' else 'chatgpt',
            'messages':history,'history':history,'seq':n,'total_messages':n,
            'next_offset':a.offset+len(history) if a.offset+len(history)<n else None,
            'lifecycle':'active'}
    if op=='read':
        need(type(a.seq) is int and a.seq>=1,'invalid_seq')
        mid=check_mid(a.message_id)
        row=query(agent,'SELECT bridge.ag_read_managed_message(%s,%s,%s) AS result',
                  (tid,a.seq,mid))
        need(isinstance(row,dict) and row.get('seq')==a.seq and
             row.get('message_id')==mid and row.get('sender')=='chatgpt' and
             row.get('recipient')=='agent' and row.get('kind')=='task','managed_task_not_found')
        return message(tid,row)
    if op=='delivery':
        need(type(a.seq) is int and a.seq>=1,'invalid_seq')
        result=query(agent,'SELECT bridge.ag_managed_delivery_status(%s,%s) AS result',
                     (tid,a.seq))
        need(isinstance(result,dict) and result.get('status') in
             ('pending','leased','delivered','cancelled'),'managed_delivery_unavailable')
        return result
    if op=='status':
        mid=check_mid(a.message_id)
        r=query(agent,'SELECT bridge.ag_managed_report_status(%s,%s) AS result',(tid,mid))
        need(isinstance(r,dict) and type(r.get('found')) is bool,'managed_status_unavailable')
        if not r['found']:
            return {'task_id':tid,'message_id':mid,'attempted':False,
                    'confirmed_seq':None,'phase':'pre_journal'}
        need(r.get('sender')=='agent' and r.get('kind')=='report' and
             type(r.get('seq')) is int,'report_sender_or_kind_mismatch')
        return {'task_id':tid,'message_id':mid,'attempted':True,
                'confirmed_seq':r['seq'],'phase':'committed'}
    raise Refused('unsupported_operation')

def parse(argv=None):
    p=argparse.ArgumentParser(description='Inert managed-only PostgreSQL agent CLI')
    sub=p.add_subparsers(dest='command',required=True)
    for command in (*ALLOWED_READ,*DENIED):
        q=sub.add_parser(command)
        q.add_argument('--task-id');q.add_argument('--message-id')
        q.add_argument('--seq',type=int)
        q.add_argument('--offset',type=int,default=0)
        q.add_argument('--limit',type=int,default=20)
        # Compatibility parameters parsed but never acted on in read-only mode.
        q.add_argument('--consumer');q.add_argument('--delivery-id',type=int)
        q.add_argument('--lease-token');q.add_argument('--head')
        q.add_argument('--kind');q.add_argument('--body-file')
    return p.parse_args(argv)

def main(argv=None):
    a=parse(argv)
    try:
        if a.command in DENIED: raise Refused('stage34c_mutations_disabled')
        sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
        from actor_import33e import actor_classes
        _, Agent = actor_classes()
        out=dispatch(a,Agent())
        print(json.dumps(out,ensure_ascii=False,separators=(',',':'),default=str))
        return 0
    except Refused as e:
        print(json.dumps({'ok':False,'error':str(e)}))
        return 2
    except Exception:
        print('{"ok":false,"error":"managed_actor_api_unavailable"}')
        return 2

if __name__=='__main__':raise SystemExit(main())
