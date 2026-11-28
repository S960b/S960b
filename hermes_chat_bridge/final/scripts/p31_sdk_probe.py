"""Verify real SDK tool registration locally; never open a socket or Git.

Run with the interpreter used by the installed MCP bridge, PYTHONPATH=src.
This exits nonzero on unsupported SDK, missing task_id or wrong annotations.
"""
import asyncio
import json

from git_dialog.native_gateway import register_mcp

READ = {'dialog_list', 'dialog_creation_status', 'dialog_view', 'dialog_health'}
WRITE = {'dialog_create', 'dialog_append'}


def _schema(tool):
    raw = (getattr(tool, 'input_schema', None) or
           getattr(tool, 'inputSchema', None) or
           getattr(tool, 'parameters', None))
    if hasattr(raw, 'model_dump'):
        raw = raw.model_dump(by_alias=True)
    if type(raw) is not dict:
        raise RuntimeError('sdk_schema_unknown')
    return raw


def _annotations(tool):
    value = getattr(tool, 'annotations', None)
    if hasattr(value, 'model_dump'):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if type(value) is not dict:
        raise RuntimeError('sdk_annotation_missing')
    return value


def run():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        from mcp.server.mcpserver import MCPServer as FastMCP

    class Bridge:
        def require_owner(self):
            raise RuntimeError('must_not_invoke_oauth')

    server = FastMCP('p31-schema-offline-check')
    names = register_mcp(server, Bridge())
    if set(names) != READ | WRITE:
        raise RuntimeError('sdk_tools_mismatch')
    # No HTTP server, Git connection, lifespan or credentials are started.
    manager = getattr(server, '_tool_manager', None)
    if manager is not None:
        tools = manager.list_tools()
    else:
        tools = server.list_tools()
        if asyncio.iscoroutine(tools):
            tools = asyncio.run(tools)
    by_name = {t.name: t for t in tools}
    if set(by_name) != READ | WRITE:
        raise RuntimeError('sdk_registered_tool_mismatch')
    for name, tool in by_name.items():
        ann = _annotations(tool)
        if ann.get('readOnlyHint') is not (name in READ):
            raise RuntimeError('sdk_read_only_hint_mismatch')
        if ann.get('destructiveHint') is not False:
            raise RuntimeError('sdk_destructive_hint_mismatch')
        if name in WRITE and ann.get('idempotentHint') is not True:
            raise RuntimeError('sdk_idempotent_hint_mismatch')
        security = (getattr(tool, 'meta', None) or getattr(tool, '_meta', None))
        if (type(security) is not dict or
                security.get('securitySchemes') !=
                [{'type': 'oauth2', 'scopes': ['bridge']}]):
            raise RuntimeError('sdk_oauth_tool_metadata_missing')
        if name in ('dialog_view', 'dialog_append', 'dialog_health'):
            schema = _schema(tool)
            if 'task_id' not in schema.get('required', []):
                raise RuntimeError('sdk_task_id_not_required')
    return {'status': 'passed', 'registered': sorted(by_name),
            'read_only': sorted(READ), 'write': sorted(WRITE),
            'io_performed': False}


def main():
    try:
        result = run()
    except Exception:
        result = {'status': 'failed', 'code': 'sdk_registration_unconfirmed'}
    print(json.dumps(result, sort_keys=True))
    return int(result['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
