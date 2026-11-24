"""PG-backed dialogue adapter. Never uses Git, synthetic HEAD is a version token.

Caller identity must be derived from authenticated transport, never from an MCP
argument. A single Store instance is stateless and safe across invocations.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from .store import Store, BridgeError

ID_RE = re.compile(r'^task_[0-9a-f]{32}$')
MID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
KIND = {'task', 'report', 'ack'}


def version_token(task_id: str, version: int) -> str:
    """40-hex UI version token (NOT a Git commit or authentication secret)."""
    if not ID_RE.fullmatch(task_id) or type(version) is not int or version < 0:
        raise BridgeError('invalid_request')
    return hashlib.sha1(f'pgbridge-v2\x00{task_id}\x00{version}'.encode()).hexdigest()


def _checked_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id):
        raise BridgeError('invalid_request')
    return task_id


def _checked_mid(message_id: str) -> str:
    if not isinstance(message_id, str) or not MID_RE.fullmatch(message_id):
        raise BridgeError('invalid_request')
    return message_id


def _page(offset: int, limit: int, order: str) -> None:
    if (type(offset) is not int or not 0 <= offset <= 100000 or
        type(limit) is not int or not 1 <= limit <= 100 or order not in ('newest','oldest')):
        raise BridgeError('invalid_dialog_page')


def _actor_internal(actor: str) -> str:
    if actor not in ('chatgpt', 'mt'):
        raise BridgeError('actor_denied')
    return 'agent' if actor == 'mt' else 'chatgpt'


def _actor_external(actor: str) -> str:
    return 'mt' if actor == 'agent' else 'chatgpt'


def _tstamp(value) -> str | None:
    if value is None: return None
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    else: dt = value
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _message(row: dict, task_id: str) -> dict:
    return {'protocol':'pg-dialog','version':'2','task_id':task_id,
      'message_id':row['message_id'],'seq':row['seq'],
      'from':_actor_external(row['sender']),'to':_actor_external(row['recipient']),
      'reply_to':None, 'kind':row['kind'], 'body':row['body'],
      'returned':row.get('returned',False),'created_at_utc':_tstamp(row['created_at'])}


class Catalog:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    def _one(self, sql: str, args: tuple):
        return self.store._one(sql, args)

    def create(self, actor: str, request_id: str, title: str) -> dict:
        if not isinstance(request_id,str) or not MID_RE.fullmatch(request_id) or not isinstance(title, str) or not title.strip():
            raise BridgeError('invalid_request')
        row = self.store.create_dialog(request_id, title, _actor_internal(actor))
        return {'request_id':request_id,'task_id':row['task_id'],
          'branch':None,'initiator':actor,'status':'confirmed',
          'head':version_token(row['task_id'], row['version']),
          'waiting_for':_actor_external(row['next_actor']),
          'created':row['created'],'retry_authorized':False}

    def creation_status(self, actor: str, request_id: str) -> dict:
        row = self._one('SELECT bridge.creation_status(%s) AS result',(request_id,))
        if row is None or row['initiator'] != _actor_internal(actor):
            raise BridgeError('creation_not_found')
        return {'request_id':request_id,'task_id':row['task_id'],'branch':None,
          'initiator':actor,'status':'confirmed',
          'head':version_token(row['task_id'],row['version']),
          'waiting_for':_actor_external(row['next_actor']),
          'created':False,'retry_authorized':False}

    def list(self, offset: int=0, limit: int=20) -> dict:
        _page(offset,limit,'newest')
        entries = self.store.list_dialogs(offset,limit+1)
        data = [{'task_id':x['task_id'],'branch':None,'lifecycle':'active',
                 'title':x['title'],'initiator':_actor_external(x['initiator'])}
                for x in entries[:limit]]
        return {'dialogs':data,'next_offset':offset+limit if len(entries)>limit else None,
                'order':'newest'}

    def metadata(self, task_id: str) -> dict:
        row = self._one('SELECT bridge.dialog_metadata(%s) AS result', (_checked_id(task_id),))
        if row is None: raise BridgeError('dialog_not_found')
        return row

    def view(self, task_id: str, offset: int=0, limit: int=20, order: str='newest') -> dict:
        _page(offset,limit,order)
        tid=_checked_id(task_id)
        d=self.metadata(tid)
        if d['state'] != 'active':
            raise BridgeError('dialog_not_found')
        # Underlying function provides bounded newest-first pagination.
        # Oldest is supported by reversing a bounded full page for small dialogs.
        if order=='oldest':
            # More efficient oldest pagination needs a dedicated SQL function.
            raise BridgeError('invalid_dialog_page')
        page=self.store.view_dialog(tid,offset,limit)
        msg=[]
        page_bytes=0
        for x in page['messages']:
            m=_message(x,tid)
            size=len(json.dumps(m,ensure_ascii=False).encode('utf-8'))
            if msg and page_bytes+size>220_000:break
            msg.append(m);page_bytes+=size
        total=int(page['last_seq'])
        end=offset+len(msg)
        last=msg[0] if offset==0 and msg else None
        last_at=last['created_at_utc'] if last else None
        age=None
        if last_at:
            age=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(last_at.replace('Z','+00:00'))).total_seconds()))
        return {'task_id':tid,'title':d['title'],'head':version_token(tid,d['version']),
          'status':'working' if total else 'waiting','direction':'to_'+_actor_external(d['next_actor']),
          'waiting_for':_actor_external(d['next_actor']),'history':msg,
          'total_messages':total,'next_offset':end if end<total else None,
          'offset':offset,'limit':limit,'order':order,'branch':None,
          'lifecycle':'active','archived':False,
          'awaiting_replacement':bool(last and last.get('returned')),
          'active_message_id':last['message_id'] if last and not last.get('returned') else None,
          'stalled':False,'seconds_since_message':age,'last_message_at':last_at}

    def index(self,task_id: str,offset:int=0,limit:int=20,order:str='newest') -> dict:
        d=self.view(task_id,offset,limit,order)
        return {'task_id':d['task_id'],'head':d['head'],'seq':d['total_messages'],
          'status':d['status'],'waiting_for':d['waiting_for'],
          'messages':[{'seq':m['seq'],'message_id':m['message_id'],'kind':m['kind'],
                       'from':m['from'],'to':m['to']} for m in d['history']],
          'order':order,'next_offset':d['next_offset']}

    def health(self,task_id:str) -> dict:
        row=self.view(task_id,0,1)
        return {k:row.get(k) for k in ('task_id','head','waiting_for','status',
          'stalled','seconds_since_message','last_message_at')} | {'last_error_code':None}

    def send(self,actor:str,task_id:str,message_id:str,kind:str,body:str) -> dict:
        _checked_id(task_id);_checked_mid(message_id)
        if kind not in KIND: raise BridgeError('invalid_request')
        result=self.store.send_message(task_id,message_id,_actor_internal(actor),kind,body)
        return {'task_id':task_id,'message_id':message_id,'seq':result['seq'],
                'created':result['created'],'version':result['version']}

    def send_status(self,actor:str,task_id:str,message_id:str) -> dict:
        _checked_id(task_id);_checked_mid(message_id)
        result=self.store.send_status(task_id,message_id)
        if not result['found']:
            return {'task_id':task_id,'message_id':message_id,'attempted':False,
                    'confirmed_seq':None,'phase':'pre_journal'}
        if result['sender'] != _actor_internal(actor):
            raise BridgeError('actor_denied')
        return {'task_id':task_id,'message_id':message_id,'attempted':True,
                'confirmed_seq':result['seq'],'phase':'committed'}

    def read_message(self,task_id:str,seq:int,message_id:str) -> dict:
        _checked_id(task_id);_checked_mid(message_id)
        if type(seq) is not int or seq<1: raise BridgeError('invalid_request')
        row=self._one('SELECT bridge.get_message(%s,%s,%s) AS result',
                      (task_id,seq,message_id))
        if row is None: raise BridgeError('message_not_found')
        return _message(row,task_id)

    def return_turn(self,task_id:str,head:str,last_message_id:str) -> dict:
        _checked_id(task_id);_checked_mid(last_message_id)
        current=self.view(task_id,0,1)
        if head!=current['head']: raise BridgeError('version_conflict')
        latest=current['history'][0] if current['history'] else None
        if latest is None or latest['message_id']!=last_message_id or latest['returned']:
            raise BridgeError('return_not_allowed')
        result=self.store.return_turn(task_id,last_message_id,_actor_internal(latest['from']),
                                      self.metadata(task_id)['version'])
        return {'task_id':task_id,'head':head,'returned_message_id':last_message_id,
                'returned':True,'next_actor':_actor_external(result['next_actor']),
                'new_head':version_token(task_id,result['version'])}

    def delivery_status(self,task_id:str,seq:int) -> dict|None:
        return self._one('SELECT bridge.delivery_status(%s,%s) AS result',(task_id,seq))

    def claim(self,task_id:str,seq:int,message_id:str,consumer:str) -> dict:
        return self._one('SELECT bridge.claim_message(%s,%s,%s,%s,%s) AS result',
                         (task_id,seq,message_id,consumer,3600))

    def ack(self,delivery_id:int,lease_token:str,consumer:str) -> bool:
        return self.store.ack_outbox(delivery_id,lease_token,consumer)
