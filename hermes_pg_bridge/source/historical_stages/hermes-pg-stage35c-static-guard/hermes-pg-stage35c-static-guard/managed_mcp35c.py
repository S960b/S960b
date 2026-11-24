#!/usr/bin/env python3
"""Private static-header protected managed MCP at 127.0.0.1:18777.

Not registered in ChatGPT, not connected to Secure Tunnel. Requires local private secret + future short-lived write permit; otherwise fail closed.
"""
from __future__ import annotations
import asyncio, logging
from static_guard35c import MCPStaticGate
import runtime35c as rt

HOST='127.0.0.1'
PORT=18777
logging.basicConfig(level=logging.WARNING)

def create_app():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    server=MCPServer(name='Hermes PostgreSQL Static-Header Managed Shadow',version='35C',
       instructions='Managed dialogues only, private tunnel client header required. Writes need local permit. No Git.')
    ro=ToolAnnotations(readOnlyHint=True,destructiveHint=False,
                       idempotentHint=True,openWorldHint=False)
    wo=ToolAnnotations(readOnlyHint=False,destructiveHint=False,
                       idempotentHint=True,openWorldHint=False)
    async def execute(func,*args):
        try:return {'ok':True,'result':await asyncio.to_thread(func,*args)}
        except (rt.Invalid,Exception) as exc:
            # Do not return DB errors/lease tokens/SQL connection details.
            code=str(exc) if isinstance(exc,rt.Invalid) else 'operation_failed_or_commit_uncertain'
            return {'ok':False,'error':code}

    @server.tool(annotations=ro)
    async def managed_health()->dict:
        """Read PostgreSQL health under bridge_chatgpt, no message bodies."""
        return await execute(rt.health)
    @server.tool(annotations=ro)
    async def managed_list(offset:int=0,limit:int=20)->dict:
        """List registered managed dialogues, bounded to 50 per page."""
        return await execute(rt.list_managed,offset,limit)
    @server.tool(annotations=ro)
    async def managed_status(task_id:str)->dict:
        """Read policy version, dialogue version, state and next actor."""
        return await execute(rt.detail,task_id)
    @server.tool(annotations=ro)
    async def managed_read(task_id:str,offset:int=0,limit:int=20)->dict:
        """Read managed dialogue history, including agent report bodies."""
        return await execute(rt.view,task_id,offset,limit)
    @server.tool(annotations=ro)
    async def managed_delivery_status(task_id:str,seq:int)->dict:
        """Read delivery receipt for a managed message by sequence."""
        return await execute(rt.delivery,task_id,seq)
    @server.tool(annotations=ro)
    async def managed_create_status(request_id:str)->dict:
        """Check durable creation after uncertain COMMIT; do not retry with new ID."""
        return await execute(rt.create_status,request_id)
    @server.tool(annotations=ro)
    async def managed_send_status(task_id:str,message_id:str)->dict:
        """Check durable send status; do not retry with a different message ID."""
        return await execute(rt.send_status,task_id,message_id)
    @server.tool(annotations=wo)
    async def managed_create(request_id:str,title:str)->dict:
        """Create one disabled managed dialogue. Local header and permit required."""
        return await execute(rt.create,request_id,title)
    @server.tool(annotations=wo)
    async def managed_set_dispatch(task_id:str,enabled:bool,policy_version:int,dialog_version:int)->dict:
        """Version-checked policy. Enable needs explicit local dispatch permit."""
        return await execute(rt.set_dispatch,task_id,enabled,policy_version,dialog_version)
    @server.tool(annotations=wo)
    async def managed_send_task(task_id:str,message_id:str,body:str,expected_version:int)->dict:
        """Send only to explicitly enabled managed dialogue, with stable message ID."""
        return await execute(rt.send,task_id,message_id,body,expected_version)
    @server.tool(annotations=wo)
    async def managed_ack_report(task_id:str,seq:int,message_id:str,body_sha256:str)->dict:
        """Identity and SHA256-bound report ACK; claim/ack in one DB transaction."""
        return await execute(rt.ack_report,task_id,seq,message_id,body_sha256)

    app=server.streamable_http_app(streamable_http_path='/mcp',host=HOST,
        transport_security=TransportSecuritySettings(
          enable_dns_rebinding_protection=True,
          allowed_hosts=['127.0.0.1:18777','localhost:18777'],allowed_origins=[]))
    return MCPStaticGate(app)

def main():
    import uvicorn
    uvicorn.run(create_app(),host=HOST,port=PORT,log_level='warning',access_log=False)

if __name__=='__main__':main()
