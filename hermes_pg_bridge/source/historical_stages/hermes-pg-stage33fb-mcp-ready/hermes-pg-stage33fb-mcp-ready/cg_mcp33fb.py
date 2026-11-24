#!/usr/bin/env python3
"""Loopback-only role-bound canary MCP for NEXT stage; NEVER tunneled by installer.

The write tool accepts no parameters, uses only bridge_chatgpt PG identity,
only one fixed test message. Temporary, not production authorization.
"""
from __future__ import annotations
import asyncio
import logging
import sys
sys.path.insert(0, '/home/kali/hermes-pg-stage33e-shadow-clients')
from actor_import33e import actor_classes
from logic33fb import status, send_once, CANARY, is_armed

HOST, PORT = '127.0.0.1', 18773
logging.basicConfig(level=logging.WARNING)


def chatgpt_db():
    ChatGPT, _ = actor_classes()
    return ChatGPT()


def create_app():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    server = MCPServer(
        name='Hermes PG Stage 33F Role-Bound Fixed Canary', version='33F-B',
        instructions='Private one-shot canary only, no parameters. NOT production.')
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)
    wo = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=ro)
    async def stage33f_actor_health() -> dict:
        """Check PostgreSQL under the real bridge_chatgpt login, no messages."""
        try:
            h = await asyncio.to_thread(lambda: chatgpt_db().health())
            return {'pg_connected': h.get('ok') is True,
                    'actor_role': 'bridge_chatgpt', 'bounded_canary_only': True}
        except Exception:
            return {'pg_connected': False, 'error': 'db_unavailable'}

    @server.tool(annotations=ro)
    async def stage33f_canary_status() -> dict:
        """Inspect seq 4-6 of the fixed canary only; no bodies returned."""
        try:
            return await asyncio.to_thread(status, chatgpt_db())
        except Exception:
            return {'ok': False, 'error': 'canary_unavailable'}

    @server.tool(annotations=wo)
    async def stage33f_send_once() -> dict:
        """One immutable harmless SDK canary task. Never caller-selected."""
        if not is_armed():
            return {'ok': False, 'error': 'stage33fc_exclusive_dispatcher_not_armed'}
        try:
            return await asyncio.to_thread(send_once, chatgpt_db())
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        except Exception:
            return {'ok': False, 'error': 'db_unavailable_or_commit_uncertain'}

    @server.custom_route('/ping', methods=['GET'])
    async def ping(_: Request):
        return JSONResponse({'ok': True, 'service': 'stage33fb',
                             'tunnel_attached': False})

    return server.streamable_http_app(
        streamable_http_path='/mcp', host=HOST,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:18773', 'localhost:18773'],
            allowed_origins=[]))


def main():
    import uvicorn
    uvicorn.run(create_app(), host=HOST, port=PORT,
                log_level='warning', access_log=False)

if __name__ == '__main__':
    main()
