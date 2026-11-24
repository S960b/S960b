"""OAuth-bound MCP v2 tools registered on the existing verified bridge host.

This module deliberately DOES NOT create a public HTTP server or bypass the
OAuth hook. The host must supply the reviewed require_owner() guard on every
invocation. Startup is implemented separately after bootstrap inspection.
"""
from __future__ import annotations

import asyncio
from functools import partial
from .compat import Catalog, version_token
from .store import BridgeError


def _annotations(fields):
    try:
        from mcp_types import ToolAnnotations
    except ImportError:
        try:
            from mcp.types import ToolAnnotations
        except ImportError:
            return fields
    try:
        return ToolAnnotations(**fields)
    except TypeError:
        raise BridgeError('unsupported_mcp_tool_annotations')

def register_mcp(mcp, bridge, *, catalog:Catalog|None=None):
    if getattr(bridge,'_pg_v2_registered',False):
        raise BridgeError('gateway_already_registered')
    if not callable(getattr(bridge,'require_owner',None)):
        raise BridgeError('owner_guard_required')
    bridge._pg_v2_registered=True
    cat=catalog or Catalog()
    security={'securitySchemes':[{'type':'oauth2','scopes':['bridge']}]}
    read_only=_annotations({'readOnlyHint':True,'destructiveHint':False})
    write_once=_annotations({'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True})

    async def invoke(fn,*args,**kwargs):
        # Synchronous, identity-bound OAuth guard: this must not be moved into
        # a thread, deferred past the DB request, or selected by model input.
        bridge.require_owner()
        try:
            return await asyncio.to_thread(partial(fn,*args,**kwargs))
        except BridgeError as e:
            return {'ok':False,'code':e.code}
        except Exception:
            return {'ok':False,'code':'database_unavailable'}

    @mcp.tool(meta=security,annotations=write_once)
    async def dialog_create(request_id:str,title:str)->dict:
        """Create a fresh PostgreSQL dialogue. Repeating request_id is idempotent."""
        return await invoke(cat.create,'chatgpt',request_id,title)

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_creation_status(request_id:str)->dict:
        """Read the durable creation receipt for one request_id."""
        return await invoke(cat.creation_status,'chatgpt',request_id)

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_list(offset:int=0,limit:int=20)->dict:
        """List active PG dialogues, newest first (no message bodies)."""
        return await invoke(cat.list,offset,limit)

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_index(task_id:str,offset:int=0,limit:int=20,order:str='newest')->dict:
        """Read PG message metadata only, ordered and paginated."""
        return await invoke(cat.index,task_id,offset,limit,order)

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_view(task_id:str,offset:int=0,limit:int=20)->dict:
        """Read complete message bodies from PG, including MT reports. No Git access."""
        return await invoke(cat.view,task_id,offset,limit,'newest')

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_health(task_id:str)->dict:
        """Read bounded health of one dialogue, current actor and version token."""
        return await invoke(cat.health,task_id)

    @mcp.tool(meta=security,annotations=write_once)
    async def dialog_send(task_id:str,message_id:str,kind:str,body:str)->dict:
        """Commit one ChatGPT message with turn change and outbox in one transaction."""
        return await invoke(cat.send,'chatgpt',task_id,message_id,kind,body)

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_send_status(task_id:str,message_id:str)->dict:
        """Read committed seq after an ambiguous send; never generate a new ID."""
        return await invoke(cat.send_status,'chatgpt',task_id,message_id)

    @mcp.tool(meta=security,annotations=read_only)
    async def dialog_notice(task_id:str,head:str,message_id:str,seq:int)->dict:
        """Compatibility read-only check of persisted PG message, not a Git notice."""
        async def check():
            v=cat.view(task_id,0,1)
            if v['head']!=head:raise BridgeError('version_conflict')
            m=cat.read_message(task_id,seq,message_id)
            if m['from']!='chatgpt':raise BridgeError('actor_denied')
            return {'task_id':task_id,'head':head,'message_id':message_id,
                    'seq':seq,'observed':True}
        # Avoid calling async function in an executor. PG calls below remain
        # synchronous inside the small helper, with the OAuth guard first.
        bridge.require_owner()
        try:
            return await check()
        except BridgeError as e:
            return {'ok':False,'code':e.code}
        except Exception:
            return {'ok':False,'code':'database_unavailable'}

    return ('dialog_create','dialog_creation_status','dialog_list','dialog_index',
            'dialog_view','dialog_health','dialog_send','dialog_send_status','dialog_notice')
