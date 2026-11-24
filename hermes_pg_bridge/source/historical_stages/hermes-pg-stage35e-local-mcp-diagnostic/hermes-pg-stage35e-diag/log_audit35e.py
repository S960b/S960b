#!/usr/bin/env python3
"""Summarize only allowlisted, non-secret ASGI/Uvicorn log markers."""
from __future__ import annotations
import re,sys
from pathlib import Path
p=Path('/home/kali/hermes-pg-stage35e-diag/stage35e-uvicorn-info.log')
if p.is_symlink() or not p.is_file():
    print('STAGE35E_NO_PRIVATE_UVICORN_LOG');sys.exit(0)
raw=p.read_text(encoding='utf8',errors='replace')
lines=raw.splitlines()
print('STAGE35E_PRIVATE_LOG_LINE_COUNT',len(lines))
static=(
 'STAGE35E_ASGI_CREATE_APP_BEGIN',
 'STAGE35E_ASGI_CREATE_APP_OK_WRAPPER=',
 'STAGE35E_UVICORN_RETURNED',
 'Started server process',
 'Waiting for application startup',
 'Application startup complete',
 'Uvicorn running on',
 'Shutting down',
 'Waiting for application shutdown',
 'Application shutdown complete',
 'Finished server process',
 'Cancel running task',
 'ASGI lifespan protocol appears unsupported',
)
for x in lines:
    if x.startswith('STAGE35E_ASGI_CREATE_APP_OK_WRAPPER=') and re.fullmatch(r'STAGE35E_ASGI_CREATE_APP_OK_WRAPPER=[A-Za-z0-9_]{1,60}',x):print(x)
    elif x in ('STAGE35E_ASGI_CREATE_APP_BEGIN','STAGE35E_UVICORN_RETURNED'):print(x)
    elif x.startswith('INFO:'):
        for item in static[3:]:
            if item in x:print('STAGE35E_SERVER_EVENT_'+item.replace(' ','_').upper());break
    elif x.startswith('ERROR:'):
        print('STAGE35E_SERVER_LOG_ERROR_PRESENT')
    elif re.fullmatch(r'(?:[A-Za-z_][\w.]*)?(?:Exception|Error|TimeoutError|CancelledError|KeyboardInterrupt|SystemExit)(?::.*)?',x):
        # Print error TYPE only. Messages can contain private connection information.
        print('STAGE35E_EXCEPTION_CLASS_'+x.split(':')[0][:100])
print('STAGE35E_PRIVATE_LOG_PATH',str(p))
print('STAGE35E_LOG_VALUES_NOT_PRINTED')
