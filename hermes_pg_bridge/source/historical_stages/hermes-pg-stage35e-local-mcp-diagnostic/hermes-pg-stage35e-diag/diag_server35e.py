#!/usr/bin/env python3
"""Diagnostic-only loopback server. Not a systemd unit or tunnel target."""
from __future__ import annotations
import logging, sys
sys.path.insert(0, '/home/kali/hermes-pg-stage35c-static-guard')

def main():
    import managed_mcp35c
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    print('STAGE35E_ASGI_CREATE_APP_BEGIN',flush=True)
    app=managed_mcp35c.create_app()
    print('STAGE35E_ASGI_CREATE_APP_OK_WRAPPER='+type(app).__name__,flush=True)
    uvicorn.run(app,host='127.0.0.1',port=18777,log_level='info',access_log=False,
                lifespan='on',timeout_graceful_shutdown=5)
    print('STAGE35E_UVICORN_RETURNED',flush=True)

if __name__=='__main__':main()
