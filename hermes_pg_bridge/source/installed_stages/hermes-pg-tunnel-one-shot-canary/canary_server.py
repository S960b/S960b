"""Ephemeral secure-tunnel proof. One idempotent fixed-message DB write.

No arbitrary inputs, SQL, task creation, message bodies, command execution or file access.
No OAuth here is acceptable ONLY because the write is fixed, harmless and one-shot.
DO NOT adapt this server to general task writes; production requires authorization.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, '/home/kali/bridge-pg-v2')
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from bridge_pg.compat import Catalog
from bridge_pg.store import Store, BridgeError
from logic31 import (CANARY_ID, WRITE_MESSAGE_ID, WRITE_BODY,
                     start_state, message_is_ours, compact_status)

HOST = '127.0.0.1'
PORT = 18769
logging.basicConfig(level=logging.WARNING)


def _read_exact(cat: Catalog):
    """Never return original message bodies via MCP."""
    view = cat.view(CANARY_ID, 0, 6, 'newest')
    receipt = cat.store.send_status(CANARY_ID, WRITE_MESSAGE_ID)
    return view, receipt


def send_once(cat: Catalog) -> dict:
    """Fixed task ID, fixed message ID, fixed body. Database enforces idempotency."""
    receipt = cat.store.send_status(CANARY_ID, WRITE_MESSAGE_ID)
    if receipt.get('found'):
        # Reconcile ambiguous result safely. Never send a second command.
        msg = cat.read_message(CANARY_ID, int(receipt['seq']), WRITE_MESSAGE_ID)
        if not message_is_ours(msg):
            return {'ok': False, 'code': 'canary_message_collision'}
        return {'ok': True, 'task_id': CANARY_ID, 'send_seq': int(receipt['seq']),
                'already_committed': True, 'bounded_canary_only': True}
    current = cat.view(CANARY_ID, 0, 3, 'newest')
    if not start_state(current):
        return {'ok': False, 'code': 'canary_precondition_failed'}
    # Guaranteed constant literals; caller cannot specify actor/body/task ID.
    result = cat.send('chatgpt', CANARY_ID, WRITE_MESSAGE_ID, 'task', WRITE_BODY)
    if int(result.get('seq', -1)) != 3:
        return {'ok': False, 'code': 'unexpected_seq_after_commit'}
    return {'ok': True, 'task_id': CANARY_ID, 'send_seq': 3,
            'already_committed': not bool(result.get('created')), 'bounded_canary_only': True}


def receipt_status(cat: Catalog) -> dict:
    view, receipt = _read_exact(cat)
    return compact_status(view, receipt)


def create_app():
    if Store().health().get('ok') is not True:
        raise RuntimeError('postgres_not_ready')
    mcp = MCPServer(
        name='Hermes PG Secure Tunnel One-Shot Canary',
        version='0.1.0',
        instructions=(
            'Temporary canary only. pg_canary_send_once sends exactly one fixed harmless '
            'task to a fixed prior canary dialogue; no caller-supplied parameters. '
            'Never use as a production writable MCP.'),
        debug=False,
    )
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)
    wo = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)

    @mcp.tool(annotations=ro)
    async def pg_health() -> dict:
        """Only report connectivity to existing PostgreSQL; no details or secrets."""
        try:
            return {'pg_connected': bool((await asyncio.to_thread(lambda: Store().health().get('ok'))) is True),
                    'backend': 'postgresql'}
        except Exception:
            return {'pg_connected': False, 'backend': 'postgresql'}

    @mcp.tool(annotations=ro)
    async def pg_canary_status() -> dict:
        """Read only fixed test send/ACK status; never disclose bodies."""
        try:
            return await asyncio.to_thread(receipt_status, Catalog())
        except Exception:
            return {'task_id': CANARY_ID, 'error': 'unavailable'}

    @mcp.tool(annotations=wo)
    async def pg_canary_send_once() -> dict:
        """Submit one preapproved harmless ACK request; immutable message ID/body."""
        try:
            return await asyncio.to_thread(send_once, Catalog())
        except BridgeError as e:
            return {'ok': False, 'code': e.code}
        except Exception:
            return {'ok': False, 'code': 'database_unavailable'}

    @mcp.custom_route('/ping', methods=['GET'])
    async def ping(request: Request):
        return JSONResponse({'ok': True, 'service': 'hermes-pg-one-shot-canary'})

    return mcp.streamable_http_app(
        streamable_http_path='/mcp', host=HOST,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:18769', 'localhost:18769'],
            allowed_origins=[],
        ),
    )


def main():
    import uvicorn
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
