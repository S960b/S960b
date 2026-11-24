#!/usr/bin/env python3
"""MCP client for Stage35F: strict bounded streaming JSON-RPC, no secret/output bodies logged.

This diagnostic must not make a read request wait for SSE EOF. Markers are
allowlisted protocol metadata (no response values and no local auth key).
"""
from __future__ import annotations
import http.client
import json
import socket
import time

HOST='127.0.0.1'
PORT=18779
MAX_BODY=131072
MAX_LINE=16384
TIMEOUT=12

class ProbeError(Exception):
    pass

def need(x, reason):
    if not x: raise ProbeError(reason)

def safe_prefix(raw):
    """Never print arbitrary server bytes: only fixed protocol leading markers."""
    for known in (b'event:',b'data:',b':',b'{"jsonrpc"',b'{'):
        if raw.startswith(known):
            return known[:8].hex()
    return 'SUPPRESSED'

class ReplyParser:
    def __init__(self,content_type,request_id):
        self.content_type=content_type
        self.request_id=request_id
        self.total=0
        self.buffer=bytearray()
        self.line=bytearray()
        self.event=[]

    def feed(self,part):
        self.total+=len(part)
        need(self.total<=MAX_BODY,'RESPONSE_OVER_128K')
        if self.content_type=='application/json':
            self.buffer.extend(part)
            try: val=json.loads(self.buffer)
            except (ValueError,UnicodeError):return None
            return self._validate(val)
        need(self.content_type=='text/event-stream','RESPONSE_CONTENT_TYPE_UNSUPPORTED')
        for b in part:
            if b==10:
                line=bytes(self.line).rstrip(b'\r')
                self.line.clear()
                if not line:
                    data=[x[5:].lstrip(b' ') for x in self.event if x.startswith(b'data:')]
                    self.event.clear()
                    if data:
                        try:obj=json.loads(b'\n'.join(data))
                        except (ValueError,UnicodeError):raise ProbeError('SSE_DATA_JSON_INVALID') from None
                        # A server may emit unrelated SSE events before our matching reply.
                        if isinstance(obj,dict) and obj.get('id')==self.request_id:
                            return self._validate(obj)
                else:
                    self.event.append(line)
                    need(sum(len(x) for x in self.event)<=MAX_BODY,'SSE_EVENT_TOO_LARGE')
            else:
                self.line.append(b)
                need(len(self.line)<=MAX_LINE,'SSE_LINE_TOO_LARGE')
        return None

    def _validate(self, obj):
        need(isinstance(obj,dict) and obj.get('id')==self.request_id,'REPLY_ID_OR_TYPE_WRONG')
        need('error' not in obj,'JSONRPC_ERROR_NO_BODY_EXPOSED')
        need(isinstance(obj.get('result'),dict),'RESULT_NOT_OBJECT')
        return obj['result']


def _request(verb, payload, *, secret, session=None, protocol='2025-06-18',request_id=1,notification=False):
    headers={'Accept':'application/json, text/event-stream', 'Content-Type':'application/json',
             'X-Bridge-Local-Key':secret, 'MCP-Protocol-Version':protocol,'Connection':'close'}
    if session: headers['Mcp-Session-Id']=session
    print('STAGE35U_REQUEST_ACCEPT_JSON_AND_SSE_METHOD_'+verb,flush=True)
    conn=http.client.HTTPConnection(HOST,PORT,timeout=TIMEOUT)
    response=None
    step='SEND'
    try:
        conn.request(verb,'/mcp',body=payload,headers=headers)
        step='WAIT_HEADERS'
        response=conn.getresponse()
        content_type=(response.getheader('content-type') or '').split(';')[0].strip().lower()
        session_value=response.getheader('mcp-session-id')
        if session_value is not None:
            need(len(session_value)<=256 and all(32<=ord(ch)<127 for ch in session_value),'SESSION_HEADER_INVALID')
        print('STAGE35U_RESPONSE_HEADERS_STATUS_'+str(response.status)+
              '_TYPE_'+('SSE' if content_type=='text/event-stream' else 'JSON' if content_type=='application/json' else 'OTHER'),flush=True)
        info={'status':response.status,'content_type':content_type,'session':session_value}
        if notification or verb=='DELETE':
            need(response.status in ((200,202,204,404,405) if verb=='DELETE' else (200,202,204)),
                 'NOTIFICATION_OR_DELETE_HTTP_UNEXPECTED')
            return None,info
        need(response.status==200,'RPC_HTTP_NON_200')
        parser=ReplyParser(content_type,request_id)
        step='WAIT_FIRST_BODY_BYTES'
        started=time.monotonic()
        first=True
        while time.monotonic()-started<=(TIMEOUT+1):
            part=response.read1(4096)
            if not part: raise ProbeError('MCP_RESPONSE_EOF_BEFORE_RESULT')
            if first:
                print('STAGE35U_FIRST_REPLY_BYTES_SAFE_HEX_'+safe_prefix(part),flush=True)
                first=False
            step='WAIT_COMPLETE_RPC_EVENT'
            result=parser.feed(part)
            if result is not None: return result,info
        raise ProbeError('MCP_REPLY_DURATION_LIMIT')
    except (OSError,ConnectionError,TimeoutError,socket.timeout) as ex:
        print('STAGE35U_NETWORK_FAULT_STAGE_'+step+'_TYPE_'+type(ex).__name__,flush=True)
        raise ProbeError('NETWORK_'+step+'_'+type(ex).__name__) from None
    finally:
        if response is not None:
            try:response.close()
            except Exception:pass
        conn.close()


def rpc(method,params=None,*,secret,session=None,protocol='2025-06-18',request_id=1,notification=False):
    body={'jsonrpc':'2.0','method':method}
    if params is not None:body['params']=params
    if not notification:body['id']=request_id
    return _request('POST',json.dumps(body,separators=(',',':')).encode(),secret=secret,
                    session=session,protocol=protocol,request_id=request_id,
                    notification=notification)


def delete_session(*,secret,session,protocol):
    if not session:
        print('STAGE35U_SESSION_CLEANUP_NO_SESSION_ID',flush=True)
        return
    _res,info=_request('DELETE',None,secret=secret,session=session,protocol=protocol)
    print('STAGE35U_SESSION_CLEANUP_HTTP_'+str(info['status']),flush=True)


def tool_payload(result):
    need(isinstance(result,dict) and not result.get('isError'),'TOOL_RESULT_ERROR')
    structured=result.get('structuredContent')
    if isinstance(structured,dict) and 'ok' in structured:return structured
    for item in result.get('content',[]):
        if isinstance(item,dict) and item.get('type')=='text' and isinstance(item.get('text'),str):
            try:payload=json.loads(item['text'])
            except ValueError:continue
            if isinstance(payload,dict) and 'ok' in payload:return payload
    raise ProbeError('TOOL_RESULT_FORMAT_UNEXPECTED')
