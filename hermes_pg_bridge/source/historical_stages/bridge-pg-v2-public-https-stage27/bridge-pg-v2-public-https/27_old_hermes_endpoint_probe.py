#!/usr/bin/env python3
"""Read-only reachability check: can existing Hermes custom app URL be reused?"""
from __future__ import annotations
import json, urllib.request, urllib.error
from urllib.parse import urlsplit
URL='https://s960b-bridge.is-a.dev'
for path in ('/ping','/.well-known/oauth-authorization-server','/.well-known/oauth-protected-resource/mcp'):
    code=None; issuer_matches=None
    try:
        req=urllib.request.Request(URL+path,headers={'User-Agent':'PGV2-preflight','Accept':'application/json'})
        with urllib.request.urlopen(req,timeout=9) as r:
            code=r.status
            if path.endswith('oauth-authorization-server'):
                obj=json.loads(r.read(16384))
                issuer_matches=(obj.get('issuer')==URL)
    except urllib.error.HTTPError as e: code=e.code
    except (OSError,ValueError):pass
    print('EXISTING_HERMES_'+('PING' if path=='/ping' else 'OAUTH_ISSUER' if path.endswith('oauth-authorization-server') else 'RESOURCE_METADATA')+'_HTTP='+str(code))
    if issuer_matches is not None:print('EXISTING_HERMES_ISSUER_MATCH='+str(issuer_matches))
print('OLD_HERMES_ENDPOINT_PROBE_READ_ONLY_OK')
