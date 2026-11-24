#!/usr/bin/env python3
"""Stage 35U: body-transparent loopback observer of first request per TCP connection.

Never logs request/response bodies, raw headers, client IPs or authentication values.
This is an ephemeral diagnostic tool, NOT a production authentication proxy.
"""
import asyncio
import hmac
import json
import os
from pathlib import Path
import re
import signal
import stat
from datetime import datetime, timezone

ROOT = Path('/home/kali/hermes-pg-stage35u-connector-observer')
SECRET = Path('/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c')
EVENTS = ROOT / 'events.ndjson'
LISTEN = ('127.0.0.1', 18779)
TARGET = ('127.0.0.1', 18777)
MAX_HEAD = 16384
MAX_BODY_CLASSIFY = 4096


# Curated MCP method names only. Never record arbitrary user-provided method strings.
COMMON_METHODS=frozenset({
    'initialize','notifications/initialized','ping','tools/list','tools/call',
    'resources/list','resources/templates/list','resources/read',
    'resources/subscribe','resources/unsubscribe','prompts/list','prompts/get',
    'logging/setLevel','completion/complete','roots/list',
    'notifications/cancelled','notifications/roots/list_changed',
    'notifications/resources/updated','notifications/resources/list_changed',
    'notifications/tools/list_changed','notifications/prompts/list_changed',
    'sampling/createMessage','elicitation/create','tasks/list','tasks/get',
    'tasks/result','tasks/cancel','tasks/subscribe'
})
GATE_METHODS=frozenset({'initialize','notifications/initialized','ping','tools/list',
                        'resources/list','prompts/list','notifications/cancelled'})
GATE_TOOLS=frozenset({'managed_health','managed_create_status','managed_list','managed_status',
                      'managed_read','managed_send_status','managed_delivery_status',
                      'managed_create','managed_set_dispatch','managed_send_task','managed_ack_report'})
ALLOWED_RPC_LABELS=COMMON_METHODS|frozenset({'other','batch','non_rpc','invalid_json','body_unobserved'})
POLICY_LABELS=frozenset({'non_mcp','bad_auth','deny_http_method','deny_rpc_method',
                         'deny_tool','invalid_rpc','allowed','body_unobserved',
                         'invalid_json','batch_denied'})

def classification(method, target, headers, secret, body=b'',body_observed=True):
    # Fixed-enum output only. Do not log body, params, IDs, arbitrary RPC method values.
    method = method if method in ('GET','POST','DELETE','OPTIONS','HEAD') else 'OTHER'
    route = 'mcp' if target.split('?',1)[0] in ('/mcp','/mcp/') else (
        'ping' if target=='/ping' else 'other')
    values=headers.get('x-bridge-local-key',[])
    if len(values)!=1:auth='missing' if not values else 'duplicate'
    else:auth='match' if hmac.compare_digest(values[0].encode('latin-1'),secret) else 'mismatch'
    accept=','.join(headers.get('accept',[])).lower()
    rpc='non_rpc';policy='non_mcp'
    if route=='mcp':
        policy='bad_auth' if auth!='match' else ('deny_http_method' if method not in ('POST','GET','DELETE') else 'allowed')
        if method=='POST':
            if not body_observed:
                rpc='body_unobserved'
                if policy=='allowed':policy='body_unobserved'
            else:
                try:
                    j=json.loads(body)
                except (UnicodeError,ValueError):
                    rpc='invalid_json'
                    if policy=='allowed':policy='invalid_json'
                else:
                    if isinstance(j,list):
                        rpc='batch'
                        if policy=='allowed':policy='batch_denied'
                    elif not isinstance(j,dict) or not isinstance(j.get('method'),str):
                        rpc='other'
                        if policy=='allowed':policy='invalid_rpc'
                    else:
                        m=j['method'];rpc=m if m in COMMON_METHODS else 'other'
                        if policy=='allowed':
                            if m=='tools/call':
                                params=j.get('params')
                                if not isinstance(params,dict) or not isinstance(params.get('name'),str) or params['name'] not in GATE_TOOLS:
                                    policy='deny_tool'
                            elif m not in GATE_METHODS:policy='deny_rpc_method'
    return {'method':method,'route':route,'auth':auth,'accept_sse':'text/event-stream' in accept,
            'rpc':rpc,'policy':policy}


def parse_head(raw):
    lines = raw.split(b'\r\n')
    if not lines or len(lines[0]) > 1024:
        raise ValueError('bad request line')
    first = lines[0].decode('latin-1').split(' ')
    if len(first) != 3 or not first[2].startswith('HTTP/1.'):
        raise ValueError('bad request line')
    headers = {}
    for line in lines[1:]:
        if not line:
            break
        if b':' not in line:
            raise ValueError('malformed header')
        k, v = line.split(b':', 1)
        try:
            key = k.decode('ascii').lower()
        except UnicodeDecodeError:
            raise ValueError('malformed header name')
        headers.setdefault(key, []).append(v.strip(b' \t').decode('latin-1'))
    return first[0], first[1], headers


def for_upstream_host(raw):
    """Rewrite HTTP authority to the actual FastMCP origin, not the observer port.

    MCP server may reject mismatched Host with HTTP 421. Reject ambiguous
    authority and control connection reuse so first-request-only observation
    covers each HTTP request. Never touch auth, body, Accept or MCP headers.
    """
    lines=raw.split(b'\r\n')
    if not lines[-2:]==[b'',b'']:
        raise ValueError('header terminator invalid')
    hosts=[i for i,line in enumerate(lines[1:-2],1) if line.lower().startswith(b'host:')]
    if len(hosts)!=1:
        raise ValueError('host ambiguous')
    i=hosts[0]
    # Only the loopback observer's Host is expected, for both local probes
    # and the selected tunnel main channel.
    authority=lines[i].split(b':',1)[1].strip(b' \t').lower()
    if authority not in (b'127.0.0.1:18779',b'localhost:18779'):
        raise ValueError('unexpected authority')
    lines[i]=b'Host: 127.0.0.1:18777'
    # Force one request per connection. The observer classifies only the first
    # request of each accepted TCP session; without close, later auth could be
    # miscounted and an unmodified Host could recur on a reused connection.
    lines=[line for line in lines if not line.lower().startswith((b'connection:',b'proxy-connection:'))]
    lines.insert(-2,b'Connection: close')
    return b'\r\n'.join(lines)


def status_from_head(raw):
    line = raw.split(b'\r\n', 1)[0]
    match = re.match(rb'^HTTP/1\.[01] ([0-9]{3})(?:\s|$)', line)
    return int(match.group(1)) if match else 0


def emit_event(data):
    # Bounded, fixed-schema, non-sensitive JSON. No raw strings from the network.
    row = {'time_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'method':data['method'], 'route':data['route'], 'auth':data['auth'],
           'accept_sse': bool(data['accept_sse']), 'rpc':data['rpc'], 'policy':data['policy'],
           'http':int(data.get('http', 0))}
    encoded = (json.dumps(row, sort_keys=True, separators=(',',':'))+'\n').encode()
    fd = os.open(EVENTS, os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0), 0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600 or st.st_uid!=os.geteuid() or st.st_nlink!=1:
            raise RuntimeError('events file not private')
        if st.st_size < 65536:
            os.write(fd, encoded)
    finally:
        os.close(fd)


async def relay(reader, writer):
    try:
        while True:
            data = await reader.read(32768)
            if not data:
                try: writer.write_eof()
                except (AttributeError, OSError): pass
                break
            writer.write(data)
            await writer.drain()
    except (OSError, ConnectionError, asyncio.CancelledError):
        pass


async def handle(reader, writer):
    downstream = None
    to_upstream = None
    event = None
    status = 0
    try:
        # A body-transparent first-request capture; subsequent keep-alive data
        # passes unchanged and is not classified. This limitation is reported.
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 8)
        if len(raw) > MAX_HEAD:
            return
        method, path, headers = parse_head(raw)
        length_str = headers.get('content-length', [''])
        body=b''
        observed_body=False
        if (method == 'POST' and len(length_str)==1 and length_str[0].isdecimal()
                and int(length_str[0]) <= MAX_BODY_CLASSIFY):
            body=await asyncio.wait_for(reader.readexactly(int(length_str[0])), 8)
            observed_body=True
        secret = SECRET.read_bytes()
        if not re.fullmatch(rb'[A-Za-z0-9_-]{64}', secret):
            raise RuntimeError('unexpected secret shape')
        event = classification(method,path,headers,secret,body,body_observed=observed_body)
        downstream_reader, downstream=await asyncio.wait_for(asyncio.open_connection(*TARGET), 5)
        downstream.write(for_upstream_host(raw)+body)
        await downstream.drain()
        # Forward any remaining body BEFORE awaiting response headers. Without
        # this task, chunked requests or bodies >MAX_BODY_CLASSIFY deadlock.
        to_upstream=asyncio.create_task(relay(reader,downstream))
        # First response status sampled, all response bytes forwarded unchanged.
        first = await asyncio.wait_for(downstream_reader.readuntil(b'\r\n\r\n'), 15)
        status = status_from_head(first)
        writer.write(first)
        await writer.drain()
        event['http'] = status
        emit_event(event)
        await relay(downstream_reader,writer)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError,
            OSError, ConnectionError, RuntimeError):
        if event is not None and not status:
            event['http']=0
            emit_event(event)
    finally:
        if to_upstream is not None:
            to_upstream.cancel()
            await asyncio.gather(to_upstream,return_exceptions=True)
        if downstream is not None:
            downstream.close()
        writer.close()
        try: await writer.wait_closed()
        except (OSError, ConnectionError): pass


async def main():
    server=await asyncio.start_server(handle,*LISTEN,limit=65536)
    async with server:
        await server.serve_forever()

if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
