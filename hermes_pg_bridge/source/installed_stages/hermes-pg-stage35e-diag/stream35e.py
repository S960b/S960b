#!/usr/bin/env python3
"""Bounded stream-safe MCP JSON-RPC reader; never reads until EOF for SSE.

No network access beyond caller-selected localhost. No secrets printed.
"""
from __future__ import annotations
import http.client, json, time

class ProbeError(Exception):
    pass

MAX_RESPONSE = 262144
HOST = '127.0.0.1'
PORT = 18777


def parse_event_stream(raw: bytes, request_id):
    """Parse complete SSE events, not the end of a persistent HTTP stream."""
    # The data comes in as a prefix of a possibly never-ending SSE stream.
    # Preserve incomplete lines/events across read1 calls.
    events = raw.split(b'\n\n')
    for event in events[:-1]:
        lines = event.replace(b'\r\n', b'\n').split(b'\n')
        data = [line[5:].lstrip(b' ') for line in lines if line.startswith(b'data:')]
        if not data:
            continue
        try:
            parsed = json.loads(b'\n'.join(data))
        except (ValueError, UnicodeError):
            raise ProbeError('SSE_INVALID_JSON') from None
        if isinstance(parsed,dict) and parsed.get('id') == request_id and ('result' in parsed or 'error' in parsed):
            return parsed
    # More generally support CRLF CRLF, including partial streamed fragments.
    return None


def parse_prefix(raw:bytes, content_type:str, request_id):
    if content_type.startswith('text/event-stream'):
        return parse_event_stream(raw.replace(b'\r\n', b'\n'),request_id)
    if content_type.startswith('application/json'):
        try:
            obj = json.loads(raw)
        except (ValueError,UnicodeError):
            return None
        if not isinstance(obj,dict):raise ProbeError('JSONRPC_RESPONSE_NOT_OBJECT')
        if obj.get('id')!=request_id:raise ProbeError('JSONRPC_ID_MISMATCH')
        return obj
    raise ProbeError('UNEXPECTED_CONTENT_TYPE')


def request(method, body, headers, *, notification=False, request_id=None, timeout=16):
    """Read first complete JSON-RPC response, never wait for SSE connection close."""
    conn=http.client.HTTPConnection(HOST,PORT,timeout=timeout)
    r=None
    try:
        conn.request(method,'/mcp',body=body,headers=headers)
        r=conn.getresponse()
        info={'status':r.status,'content_type':(r.getheader('content-type') or '').split(';')[0].strip().lower(),
              'session':r.getheader('mcp-session-id')}
        if notification:
            if r.status not in (200,202,204):raise ProbeError('MCP_NOTIFICATION_HTTP_'+str(r.status))
            return None,info
        if r.status!=200:raise ProbeError('MCP_RPC_HTTP_'+str(r.status))
        raw=bytearray()
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            # read1 returns buffered bytes even when SSE remains open indefinitely.
            part=r.read1(4096)
            if not part:break
            raw+=part
            if len(raw)>MAX_RESPONSE:raise ProbeError('MCP_REPLY_TOO_LARGE')
            parsed=parse_prefix(bytes(raw),info['content_type'],request_id)
            if parsed is not None:
                if 'error' in parsed:raise ProbeError('MCP_JSONRPC_ERROR')
                if not isinstance(parsed.get('result'),dict):raise ProbeError('MCP_RESULT_NOT_OBJECT')
                return parsed['result'],info
        raise ProbeError('MCP_REPLY_INCOMPLETE_OR_TIMEOUT')
    except (TimeoutError,ConnectionError,OSError) as exc:
        raise ProbeError('MCP_NETWORK_'+type(exc).__name__) from None
    finally:
        if r is not None:r.close()
        conn.close()


def rpc(method, params=None, *, request_id=1, secret, session=None, protocol='2025-06-18', notification=False):
    headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json',
             'MCP-Protocol-Version':protocol,'X-Bridge-Local-Key':secret,'Connection':'close'}
    if session:headers['Mcp-Session-Id']=session
    payload={'jsonrpc':'2.0','method':method}
    if not notification:payload['id']=request_id
    if params is not None:payload['params']=params
    body=json.dumps(payload,separators=(',',':')).encode('utf-8')
    return request('POST',body,headers,notification=notification,request_id=request_id)


def tool_payload(result):
    if not isinstance(result,dict) or result.get('isError') is True:
        raise ProbeError('MCP_TOOL_ERROR')
    structured=result.get('structuredContent')
    if isinstance(structured,dict) and 'ok' in structured:return structured
    for item in result.get('content',[]):
        if isinstance(item,dict) and item.get('type')=='text' and isinstance(item.get('text'),str):
            try:value=json.loads(item['text'])
            except ValueError:continue
            if isinstance(value,dict) and 'ok' in value:return value
    raise ProbeError('MCP_TOOL_RESULT_UNEXPECTED')
