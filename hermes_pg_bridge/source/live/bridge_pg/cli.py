"""Local CLI for MT plugin. Database connection must be through a loopback SSH tunnel.

Never pass message bodies or database passwords as command-line arguments.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from .compat import Catalog, version_token, ID_RE, MID_RE
from .store import BridgeError


def main(argv=None):
    ap=argparse.ArgumentParser(prog='python -m bridge_pg.cli')
    commands=ap.add_subparsers(dest='command',required=True)
    for name in ('list','peek','read','append','status','create','claim','ack'):
        p=commands.add_parser(name)
        p.add_argument('--task-id')
        p.add_argument('--message-id')
        p.add_argument('--seq',type=int)
        p.add_argument('--offset',type=int,default=0)
        p.add_argument('--limit',type=int,default=20)
        if name=='append':
            p.add_argument('--head',required=True)
            p.add_argument('--kind',default='report')
            p.add_argument('--body-file',required=True)
        if name=='create':
            p.add_argument('--request-id',required=True)
            p.add_argument('--title',required=True)
        if name=='claim': p.add_argument('--consumer',required=True)
        if name=='ack':
            p.add_argument('--delivery-id',type=int,required=True)
            p.add_argument('--lease-token',required=True)
            p.add_argument('--consumer',required=True)
    a=ap.parse_args(argv)
    cat=Catalog()
    try:
        if a.command=='list':
            result=cat.list(a.offset,a.limit)
        elif a.command=='peek':
            result=cat.view(a.task_id,a.offset,a.limit)
            result['messages']=result['history']
            result['seq']=result['total_messages']
        elif a.command=='read':
            result=cat.read_message(a.task_id,a.seq,a.message_id)
        elif a.command=='append':
            v=cat.view(a.task_id,0,1)
            if v['head']!=a.head: raise BridgeError('version_conflict')
            data=Path(a.body_file).read_bytes()
            if len(data)>65536: raise BridgeError('body_too_large')
            result=cat.send('mt',a.task_id,a.message_id,a.kind,data.decode('utf-8'))
            result['head']=version_token(a.task_id,result['version'])
        elif a.command=='status':
            result=cat.send_status('mt',a.task_id,a.message_id)
        elif a.command=='create':
            result=cat.create('mt',a.request_id,a.title)
        elif a.command=='claim':
            result=cat.claim(a.task_id,a.seq,a.message_id,a.consumer)
        elif a.command=='ack':
            result={'acked':cat.ack(a.delivery_id,a.lease_token,a.consumer)}
        else: raise BridgeError('invalid_request')
        print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
        return 0
    except (BridgeError,UnicodeError,OSError) as e:
        code=e.code if isinstance(e,BridgeError) else 'request_failed'
        print(json.dumps({'ok':False,'error':code}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
