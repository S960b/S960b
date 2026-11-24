#!/usr/bin/env python3
"""Read-only, loopback-only candidate MCP under bridge_chatgpt.
No general write tools, credentials or external tunnels are permitted here.
"""
from __future__ import annotations
import asyncio
import logging
from actor_import33e import actor_classes

HOST='127.0.0.1'
PORT=18772
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
logging.basicConfig(level=logging.WARNING)


def status_from_actor(actor):
    info=actor.health()
    meta=actor.dialog_metadata(CANARY)
    delivered=actor.delivery_status(CANARY,3)
    report=actor.send_status(CANARY,'pgv2-tunnel-write-ack-20261122-v1')
    if not isinstance(info,dict) or info.get('ok') is not True:
        raise ValueError('db_health_unavailable')
    if not isinstance(meta,dict) or meta.get('state')!='active' or meta.get('last_seq')!=4:
        raise ValueError('canary_state_unexpected')
    return {'pg_connected':True,'role':'bridge_chatgpt','last_seq':4,
            'seq3_delivered':delivered.get('status')=='delivered',
            'agent_report_present':bool(report and report.get('found') and report.get('sender')=='agent'),
            'read_only':True}


def create_app():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    mcp=MCPServer(name='Hermes PG Shadow ChatGPT Role', version='33E',
                  instructions='Stage 33E private role-bound read-only shadow. Never expose to a public tunnel.')
    readonly=ToolAnnotations(readOnlyHint=True,destructiveHint=False)

    @mcp.tool(annotations=readonly)
    async def shadow_actor_health()->dict:
        """Check actual bridge_chatgpt PostgreSQL login; no message bodies."""
        try:
            ChatGPT,_ = actor_classes()
            data=await asyncio.to_thread(ChatGPT().health)
            return {'pg_connected':data.get('ok') is True,'role':'bridge_chatgpt','read_only':True}
        except Exception:
            return {'pg_connected':False,'read_only':True,'error':'db_unavailable'}

    @mcp.tool(annotations=readonly)
    async def shadow_canary_receipt()->dict:
        """Read only the bounded, completed existing canary receipt, no bodies."""
        try:
            ChatGPT,_=actor_classes()
            return await asyncio.to_thread(status_from_actor,ChatGPT())
        except Exception:
            return {'ok':False,'code':'canary_unavailable','read_only':True}

    @mcp.custom_route('/ping',methods=['GET'])
    async def ping(_:Request):
        return JSONResponse({'ok':True,'service':'pg-stage33e-shadow','read_only':True})

    return mcp.streamable_http_app(
        streamable_http_path='/mcp',host=HOST,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:18772','localhost:18772'],
            allowed_origins=[]))


def main():
    import uvicorn
    uvicorn.run(create_app(),host=HOST,port=PORT,log_level='warning',access_log=False)

if __name__=='__main__':main()
