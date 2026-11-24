"""Temporary, read-only PG canary MCP for the existing Secure MCP Tunnel.

No OAuth is implemented HERE because the only tools disclose bounded, fixed
non-sensitive health/receipt values. THIS IS NOT A PRODUCTION MCP SERVER.
Do not add writable or arbitrary-reader tools to this endpoint.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys

sys.path.insert(0, '/home/kali/bridge-pg-v2')

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from bridge_pg.store import Store
from bridge_pg.compat import Catalog
from probe_logic import CANARY_ID, summarize_health, summarize_canary

HOST = '127.0.0.1'
PORT = 18767
logging.basicConfig(level=logging.WARNING)


def health_read() -> dict:
    try:
        return summarize_health(Store().health())
    except Exception:
        return {'pg_connected': False, 'backend': 'postgresql', 'read_only': True}


def receipt_read() -> dict:
    try:
        return summarize_canary(Catalog().view(CANARY_ID, 0, 10, 'newest'))
    except Exception:
        return {'task_id': CANARY_ID, 'mt_ack_exact': False,
                'total_messages': None, 'read_only': True, 'error': 'unavailable'}


def create_app():
    # Fail startup if PostgreSQL is unavailable; readiness must be genuine.
    if not health_read()['pg_connected']:
        raise RuntimeError('postgres_not_ready')
    mcp = MCPServer(
        name='Hermes PG Readonly Tunnel Probe',
        version='0.1.0',
        instructions='Two fixed read-only proof tools. No message bodies or writes.',
        debug=False,
    )
    safe = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                           idempotentHint=True, openWorldHint=False)

    @mcp.tool(annotations=safe)
    async def pg_health() -> dict:
        """Check whether this tunnel reaches the existing PostgreSQL bridge."""
        return await asyncio.to_thread(health_read)

    @mcp.tool(annotations=safe)
    async def pg_canary_receipt() -> dict:
        """Check only the fixed completed canary ACK; return no message bodies."""
        return await asyncio.to_thread(receipt_read)

    @mcp.custom_route('/ping', methods=['GET'])
    async def ping(request: Request):
        return JSONResponse({'ok': True, 'service': 'hermes-pg-tunnel-readonly'})

    app = mcp.streamable_http_app(
        streamable_http_path='/mcp', host=HOST,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:18767', 'localhost:18767'],
            allowed_origins=[],
        ),
    )
    return app


def main():
    import uvicorn
    app = create_app()
    uvicorn.run(app, host=HOST, port=PORT, log_level='warning', access_log=False)

if __name__ == '__main__':
    main()
