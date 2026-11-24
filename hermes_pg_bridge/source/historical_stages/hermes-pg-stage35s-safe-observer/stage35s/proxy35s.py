#!/usr/bin/env python3
"""Stage 35S: byte-transparent loopback observer of first request per TCP connection.

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

ROOT = Path('/home/kali/hermes-pg-stage35s-connector-observer')
SECRET = Path('/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c')
EVENTS = ROOT / 'events.ndjson'
LISTEN = ('127.0.0.1', 18779)
TARGET = ('127.0.0.1', 18777)
MAX_HEAD = 16384
MAX_BODY_CLASSIFY = 4096


def classification(method, target, headers, secret, body=b''):
    """Return only fixed-category information; never include untrusted values."""
    method = method if method in ('GET', 'POST', 'DELETE', 'OPTIONS', 'HEAD') else 'OTHER'
    route = 'mcp' if target.split('?', 1)[0] in ('/mcp', '/mcp/') else (
        'ping' if target == '/ping' else 'other')
    values = headers.get('x-bridge-local-key', [])
    if len(values) != 1:
        auth = 'missing' if not values else 'duplicate'
    else:
        auth = 'match' if hmac.compare_digest(values[0].encode('latin-1'), secret) else 'mismatch'
    accept = ','.join(headers.get('accept', [])).lower()
    kind = 'other'
    if method == 'POST' and route == 'mcp' and body:
        try:
            j = json.loads(body)
            k = j.get('method') if isinstance(j, dict) else None
            kind = k if k in ('initialize','notifications/initialized','tools/list','tools/call','ping') else 'other'
        except (UnicodeError, ValueError):
            kind = 'other'
    return {'method': method, 'route': route, 'auth': auth,
            'accept_sse': 'text/event-stream' in accept, 'rpc': kind}


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


def status_from_head(raw):
    line = raw.split(b'\r\n', 1)[0]
    match = re.match(rb'^HTTP/1\.[01] ([0-9]{3})(?:\s|$)', line)
    return int(match.group(1)) if match else 0


def emit_event(data):
    # Bounded, fixed-schema, non-sensitive JSON. No raw strings from the network.
    row = {'time_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'method':data['method'], 'route':data['route'], 'auth':data['auth'],
           'accept_sse': bool(data['accept_sse']), 'rpc':data['rpc'],
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
    event = None
    status = 0
    try:
        # A byte transparent first-request capture; subsequent keep-alive data
        # passes unchanged and is not classified. This limitation is reported.
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 8)
        if len(raw) > MAX_HEAD:
            return
        method, path, headers = parse_head(raw)
        length_str = headers.get('content-length', [''])
        body=b''
        if (method == 'POST' and len(length_str)==1 and length_str[0].isdecimal()
                and int(length_str[0]) <= MAX_BODY_CLASSIFY):
            body=await asyncio.wait_for(reader.readexactly(int(length_str[0])), 8)
        secret = SECRET.read_bytes()
        if not re.fullmatch(rb'[A-Za-z0-9_-]{64}', secret):
            raise RuntimeError('unexpected secret shape')
        event = classification(method,path,headers,secret,body)
        downstream_reader, downstream=await asyncio.wait_for(asyncio.open_connection(*TARGET), 5)
        downstream.write(raw+body)
        await downstream.drain()
        # First response status sampled, all bytes forwarded unchanged.
        first = await asyncio.wait_for(downstream_reader.readuntil(b'\r\n\r\n'), 15)
        status = status_from_head(first)
        writer.write(first)
        await writer.drain()
        event['http'] = status
        emit_event(event)
        await asyncio.gather(relay(reader,downstream),relay(downstream_reader,writer))
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError,
            OSError, ConnectionError, RuntimeError):
        if event is not None and not status:
            event['http']=0
            emit_event(event)
    finally:
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
