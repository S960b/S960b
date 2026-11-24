#!/usr/bin/env python3
"""Isolated local MCP server using identical guards except for real ASGI receive forwarding."""
from __future__ import annotations
import faulthandler
import signal
import sys
sys.path.insert(0,'/home/kali/hermes-pg-stage35c-static-guard')

def main():
    faulthandler.enable(file=sys.stderr,all_threads=True)
    if hasattr(signal,'SIGUSR1'):
        faulthandler.register(signal.SIGUSR1,file=sys.stderr,all_threads=True,chain=False)
    from managed_mcp35c import create_app
    from guard35f import MCPStaticGate
    import uvicorn
    print('STAGE35F_DIAG_APP_BUILD_BEGIN',flush=True)
    original=create_app()
    assert type(original).__name__=='MCPStaticGate'
    # Replacement wrapper only in this diagnostic process. The installed
    # Stage35C code, systemd unit and source pins remain unchanged.
    app=MCPStaticGate(original.app,secret_path=original.secret_path)
    print('STAGE35F_DIAG_PATCHED_GATE_ACTIVE_IN_SUBPROCESS_ONLY',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=18777,log_level='info',access_log=False,
                lifespan='on',timeout_graceful_shutdown=5)
    print('STAGE35F_DIAG_UVICORN_RETURNED',flush=True)

if __name__=='__main__':main()
