import contextlib
import hashlib
import http.client
import io
import json
import os
import stat
import sys
from pathlib import Path
from policy35v import install

HOME = Path('/home/kali')
G = HOME / 'hermes-pg-stage35g-managed-runtime'
C = HOME / 'hermes-pg-stage35c-static-guard'
PYTHON = HOME / 'bridge-pg-v2/.venv/bin/python'
PERMIT = HOME / '.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
PINS = {
    'service35g.py':'ddaaf1788a515a152d565a74a01a30a2d625c291899629109537b98c9e5d8889',
    'stage35g.py':'ee62f7a6d0ed1ed301c18da2afd05579f13344827f136babb72c8bed7e07bdc5',
    'stream35g.py':'26f662f521f378f4d7d8009931737ad5f34f8df61c3503abbec2823e2c778f22',
}

def need(ok, why):
    if not ok:
        raise ValueError(why)

def setup():
    for name, digest in PINS.items():
        p = G / name
        s = p.lstat()
        need(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
             and not s.st_mode & 0o022 and hashlib.sha256(p.read_bytes()).hexdigest() == digest,
             'original_source_drift')
    sys.path.insert(0, str(G))
    import service35g
    service35g.verified_source()
    return service35g

def serve(extra):
    svc = setup()
    app = svc.create_patched_app()
    import guard35g
    need(type(app) is guard35g.MCPStaticGate, 'unexpected_gate')
    install(guard35g, extra)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18777, log_level='info', access_log=False,
                lifespan='on', timeout_graceful_shutdown=5)

def modern(method, key):
    from stream35g import ReplyParser
    ver = '2026-07-28'
    body = {'jsonrpc':'2.0', 'id':351, 'method':method, 'params':{'_meta':{
        'io.modelcontextprotocol/protocolVersion':ver,
        'io.modelcontextprotocol/clientInfo':{'name':'stage35v', 'version':'1'},
        'io.modelcontextprotocol/clientCapabilities':{}}}}
    headers = {'MCP-Protocol-Version':ver,'Mcp-Method':method,'X-Bridge-Local-Key':key,
               'Content-Type':'application/json','Accept':'application/json, text/event-stream',
               'Connection':'close'}
    conn = http.client.HTTPConnection('127.0.0.1', 18777, timeout=5)
    try:
        conn.request('POST', '/mcp', body=json.dumps(body), headers=headers)
        with conn.getresponse() as response:
            need(response.status == 200, 'modern_rpc_http_failed')
            parser = ReplyParser(response.getheader('content-type','').split(';')[0].strip(), 351)
            while chunk := response.read1(4096):
                result = parser.feed(chunk)
                if result is not None:
                    return result
            raise ValueError('missing_modern_reply')
    finally:
        conn.close()

def probe(extra):
    setup()
    sys.path.insert(0,str(C))
    import stage35g, stream35g, guard35g
    need(not os.path.lexists(PERMIT), 'write_permit_present')
    # This original probe only calls managed_health and managed_list. It checks both auth failures.
    with contextlib.redirect_stdout(io.StringIO()):
        stage35g.probe()
    if not extra:
        return
    key = guard35g.local_secret().decode('ascii')
    caps = {}
    if 'server/discover' in extra:
        result = modern('server/discover', key)
        caps = result.get('capabilities')
        need(isinstance(caps, dict) and 'tools' in caps and '2026-07-28' in result.get('supportedVersions', []),
             'sdk_discovery_not_supported')
        listing = modern('tools/list', key).get('tools')
        expected = guard35g.READ_TOOLS | guard35g.WRITE_TOOLS
        need(isinstance(listing, list) and len(listing) == len(expected)
             and {t['name'] for t in listing} == expected, 'modern_tools_mismatch')
    if 'skills/list' not in extra:
        return
    extension = 'io.modelcontextprotocol/skills'
    if extension in caps.get('extensions', {}):
        result = modern('skills/list', key)
    else:
        result, meta = stream35g.rpc('initialize', {'protocolVersion':'2025-11-25','capabilities':{},
            'clientInfo':{'name':'stage35v', 'version':'1'}}, secret=key, protocol='2025-11-25')
        session = meta['session']
        protocol = result['protocolVersion']
        try:
            need(extension in result['capabilities'].get('extensions', {}), 'skills_not_advertised')
            stream35g.rpc('notifications/initialized', secret=key, session=session, protocol=protocol, notification=True)
            result, _ = stream35g.rpc('skills/list', secret=key, session=session, protocol=protocol)
        finally:
            if session:
                stream35g.delete_session(secret=key, session=session, protocol=protocol)
    # No skill corpus is configured for this managed bridge.
    need(result.get('skills') == [], 'unexpected_skill_catalog')
