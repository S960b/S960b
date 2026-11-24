#!/usr/bin/env python3
"""Stage 35G: permanently pinned ASGI receive-fix, no writes and no tunnel changes.

The Stage35C source is left byte-identical. This runner replaces only its
MCPStaticGate wrapper by the Stage35F verified one-line repair.
"""
from __future__ import annotations
import hashlib
import os
import sys
from pathlib import Path

C=Path('/home/kali/hermes-pg-stage35c-static-guard')
G=Path('/home/kali/hermes-pg-stage35g-managed-runtime')
PINS={
 C/'static_guard35c.py':'643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c',
 C/'managed_mcp35c.py':'1f3c70f1f06d2e32b8e515cf630d57e319786c82ea756cb4272d21a46d3db508',
 C/'runtime35c.py':'07f5a6a909a954913843491a3ed750361bc4f66826144719f1aac6328d093641',
 G/'guard35g.py':'695622407566b316482f676da0bf3c5a63a12b3f4b6e6d706c92130a760aaa88',
}

def verified_source():
    import stat
    for path,digest in PINS.items():
        s=path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_nlink!=1 or (s.st_mode & (0o022 if path.parent==C else 0o077)):
            raise RuntimeError('STAGE35G_RUNTIME_SOURCE_UNSAFE')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('STAGE35G_RUNTIME_SOURCE_DRIFT')
    original=(C/'static_guard35c.py').read_text('utf8')
    patched=(G/'guard35g.py').read_text('utf8')
    old="if used:return {'type':'http.request','body':b'','more_body':False}"
    new='if used:return await receive()  # Stage35F: forward the real disconnect; never spin'
    if original.count(old)!=1 or original.replace(old,new)!=patched:
        raise RuntimeError('STAGE35G_RUNTIME_PATCH_NOT_EXACT')


def create_patched_app():
    verified_source()
    sys.path.insert(0,str(C))
    from managed_mcp35c import create_app
    from guard35g import MCPStaticGate
    unpatched=create_app()
    if type(unpatched).__name__!='MCPStaticGate' or not hasattr(unpatched,'secret_path'):
        raise RuntimeError('STAGE35G_RUNTIME_UNEXPECTED_APP_SHAPE')
    return MCPStaticGate(unpatched.app,secret_path=unpatched.secret_path)


def main():
    if os.getuid()!=os.geteuid() or os.getuid()==0:
        raise SystemExit('STAGE35G_RUNTIME_BAD_IDENTITY')
    import uvicorn
    app=create_patched_app()
    uvicorn.run(app,host='127.0.0.1',port=18777,log_level='info',access_log=False,
                lifespan='on',timeout_graceful_shutdown=5)

if __name__=='__main__':main()
