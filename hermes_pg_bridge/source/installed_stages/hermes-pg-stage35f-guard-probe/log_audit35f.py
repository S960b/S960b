#!/usr/bin/env python3
"""Report only allowlisted diagnostics, not log contents or secrets."""
from pathlib import Path
p=Path('/home/kali/hermes-pg-stage35f-guard-probe/stage35f-uvicorn-private.log')
if not p.is_file():
    print('STAGE35F_AUDIT_LOG_MISSING')
    raise SystemExit(2)
blob=p.read_bytes()[:262144]
print('STAGE35F_PRIVATE_UVICORN_LOG_BYTES_'+str(p.stat().st_size))
for token,needle in (
 ('PATCHED_GATE_CONSTRUCTED',b'STAGE35F_DIAG_PATCHED_GATE_ACTIVE_IN_SUBPROCESS_ONLY'),
 ('SERVER_STARTED',b'Started server process'),
 ('APPLICATION_STARTED',b'Application startup complete'),
 ('LISTENER_UP',b'Uvicorn running on'),
 ('SHUTDOWN_STARTED',b'Shutting down'),
 ('APPLICATION_SHUTDOWN_COMPLETE',b'Application shutdown complete'),
 ('SERVER_FINISHED',b'Finished server process'),
 ('UVICORN_RETURNED',b'STAGE35F_DIAG_UVICORN_RETURNED'),
 ('PRIVATE_STACK_PRESENT',b'File "'),
 ('ERROR_TRACEBACK_PRESENT',b'Traceback (most recent call last)'),
):
    print('STAGE35F_AUDIT_'+token+'_'+('YES' if needle in blob else 'NO'))
