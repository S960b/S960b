#!/usr/bin/env python3
"""Stage34B read-only LOCAL shadow MCP. Never expose through Tunnel.

Future dynamic managed-write tools require a separate authenticated endpoint.
"""
from __future__ import annotations
import asyncio
import logging
import sys
sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
from actor_import33e import actor_classes

HOST='127.0.0.1'
PORT=18775
logging.basicConfig(level=logging.WARNING)

def connect_cg():
    ChatGPT,_=actor_classes()
    return ChatGPT()

def safe_status():
    cg=connect_cg()
    health=cg.health()
    managed=cg._one('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS result',(0,50))
    return {'pg_connected':health.get('ok') is True,
            'actor_role':'bridge_chatgpt',
            'managed_dialogs_page_count':len(managed),
            'managed_dispatch_enabled_on_page':sum(x.get('dispatch_enabled') is True for x in managed),
            'read_only':True,'tunnel_attached':False}

def create_app():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    app=MCPServer(name='Hermes PG Stage34B Read-Only Shadow',version='34B',
       instructions='Local, read only; NOT authenticated for public/tunnel use.')
    ro=ToolAnnotations(readOnlyHint=True,destructiveHint=False,
                       idempotentHint=True,openWorldHint=False)
    @app.tool(annotations=ro)
    async def stage34b_managed_api_health() -> dict:
        """Verify managed-dialog list under true PostgreSQL bridge_chatgpt role."""
        try:
            return await asyncio.to_thread(safe_status)
        except Exception:
            return {'pg_connected':False,'error':'actor_api_unavailable','read_only':True}
    @app.custom_route('/ping',methods=['GET'])
    async def ping(_:Request):
        return JSONResponse({'service':'stage34b-shadow','local_only':True,'read_only':True})
    return app.streamable_http_app(streamable_http_path='/mcp',host=HOST,
      transport_security=TransportSecuritySettings(
          enable_dns_rebinding_protection=True,
          allowed_hosts=['127.0.0.1:18775','localhost:18775'],allowed_origins=[]))

def main():
    import uvicorn
    uvicorn.run(create_app(),host=HOST,port=PORT,log_level='warning',access_log=False)

if __name__=='__main__':main()
