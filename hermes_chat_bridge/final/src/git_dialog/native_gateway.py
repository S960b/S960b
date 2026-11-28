"""Existing authenticated MCP -> peer-bound Unix gateway. No credential reads."""
import asyncio

from . import DialogError
from .unix_authority import SOCKETS, UnixClient
from .p31_errors import error_value


def register_mcp(mcp, bridge, *, client=None):
    """Call once from reviewed native startup after make_mcp_server.

    OAuth is verified on EVERY handler invocation using the existing bridge
    hook, before any Unix connection. No model-selected role/UID/repository/path.
    Task selection is explicit and checked against this owner's registry.
    Partial registration is a startup error, never silently retried.
    """
    if getattr(bridge, '_shared_gateway_registered', False):
        raise DialogError('gateway_already_registered')
    bridge._shared_gateway_registered = True
    if client is not None and type(client) is not UnixClient:
        raise DialogError('gateway_client_binding_rejected')
    client = UnixClient(SOCKETS['chatgpt']) if client is None else client
    security = {'securitySchemes': [{'type': 'oauth2', 'scopes': ['bridge']}]}
    def sdk_annotations(fields):
        # Current Python MCP SDK expects ToolAnnotations (not an arbitrary
        # dictionary). Offline fake-MCP unit tests can run without the SDK;
        # the release gate separately tests registration with real FastMCP.
        try:
            # Import the standalone type module first: `import mcp.types` pulls
            # the whole mcp package, which fails to import on some supported
            # Python versions (win32 utilities). Same class either way.
            from mcp_types import ToolAnnotations
        except ImportError:
            try:
                from mcp.types import ToolAnnotations
            except ImportError:
                # Offline unit tests use a fake MCP and no SDK. Never treat
                # this fallback as proof that production registration works.
                return fields
        return ToolAnnotations(**fields)

    read_only = sdk_annotations({'readOnlyHint': True, 'destructiveHint': False})
    # Create is repeat-safe only for the SAME durable request_id; repeating it
    # reconciles rather than attempting another Git write.
    write_once = sdk_annotations({'readOnlyHint': False, 'destructiveHint': False,
                                  'idempotentHint': True})

    async def authorize():
        principal = bridge.require_owner()
        host = getattr(bridge, '_shared_gateway_host', None)
        if host is not None and host.notification_delivery is not None:
            # Principal comes ONLY from the existing verified OAuth guard.
            await asyncio.wrap_future(host.request_notification_owner(principal))

    async def invoke(request):
        try:
            return await asyncio.to_thread(client.call, request)
        except DialogError as exc:
            # Deliberately omit exception.detail, IPC phase and any remote text.
            return error_value(exc.code)
        except Exception:
            return error_value('authority_unavailable')

    @mcp.tool(meta=security, annotations=write_once)
    async def dialog_create(request_id: str, title: str) -> dict:
        """NEW CHAT: create one separate dialogue/branch; save returned task_id.

        Call once when starting a NEW dialogue with no task ID. Use a unique
        stable request_id for this chat, NOT per message. Do not reuse the pilot
        or another tab's task. To CONTINUE an existing dialogue, use its task_id
        with dialog_view instead. On uncertainty use dialog_creation_status with
        the SAME request_id; never create another ID or retry a Git write.
        """
        await authorize()
        return await invoke({'op': 'create_dialog',
            'request_id': request_id, 'title': title})

    @mcp.tool(meta=security, annotations=read_only)
    async def dialog_creation_status(request_id: str) -> dict:
        """GET-only reconciliation of this chat's exact creation request ID."""
        await authorize()
        return await invoke({'op': 'creation_status', 'request_id': request_id})

    @mcp.tool(meta=security, annotations=read_only)
    async def dialog_list(offset: int = 0, limit: int = 20) -> dict:
        """List registered dialogues, newest first. No message bodies loaded."""
        await authorize()
        return await invoke({'op': 'list_dialogs', 'offset': offset, 'limit': limit})

    @mcp.tool(meta=security, annotations=read_only)
    async def dialog_index(task_id: str, offset: int = 0, limit: int = 20,
                           order: str = "newest") -> dict:
        """Metadata only: message IDs, seq and current Git HEAD. NO bodies.

        Read content DIRECTLY from the private GitHub branch at the returned
        commit. Hermes MCP is a notification/index channel, not a text bus.
        """
        await authorize()
        return await invoke({"op": "index", "task_id": task_id, "offset": offset,
                             "limit": limit, "order": order})

    @mcp.tool(meta=security, annotations=read_only)
    async def dialog_health(task_id: str) -> dict:
        """Read-only bounded status for ONE explicitly selected dialogue.

        Return head, waiting_for, stalled, last_message_at and the last known
        safe in-process error code (or null). No message history, credentials,
        Git mutation, implicit task fallback or retries. Errors reset on restart.
        """
        await authorize()
        return await invoke({'op': 'health', 'task_id': task_id})

    @mcp.tool(meta=security, annotations=write_once)
    async def dialog_notice(task_id: str, head: str, message_id: str,
                            seq: int) -> dict:
        """Announce an ALREADY COMMITTED Git message. No text or code.

        The authority verifies the immutable candidate and the live branch
        before accepting its commit ID. This NEVER writes to the Git ref.
        """
        await authorize()
        return await invoke({"op": "notice", "task_id": task_id, "head": head,
                             "message_id": message_id, "seq": seq})

    return ("dialog_create", "dialog_creation_status", "dialog_list",
            "dialog_index", "dialog_health", "dialog_notice")
