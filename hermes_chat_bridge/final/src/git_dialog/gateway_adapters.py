"""MCP tool handlers and stdin-JSON CLI for ONE authenticated gateway route.

These adapters hold NO Git adapter, PAT, SQLite, Queue or fallback writer.
Authenticated grants are passed by the trusted host / stdin, never discovered
in env, argv or files. The existing Hermes reporting CLI is NOT replaced here.
No retries: uncertain publication preserves the caller's exact message ID.
"""
import argparse
from http.client import HTTPConnection
import json
import re
import sys
import time

from . import DialogError
from .github import decode_json
from .ingress import _hash


def _wire(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DialogError('gateway_adapter_schema_rejected') from None
    if len(raw) > 8192:
        raise DialogError('gateway_adapter_schema_rejected')
    return raw


class GatewayClient:
    """One literal-loopback HTTP request; no redirects/proxies/retry/logging."""
    def __init__(self, origin, session, csrf):
        if type(origin) is not str or not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}', origin):
            raise DialogError('gateway_adapter_origin_rejected')
        self._port = int(origin.rsplit(':', 1)[1])
        if not 1024 < self._port <= 65535 or self._port in (8765, 8787, 8789, 8790):
            raise DialogError('gateway_adapter_origin_rejected')
        _hash(session); _hash(csrf)
        self.origin, self._session, self._csrf = origin, session, csrf

    def call(self, request):
        if type(request) is not dict or request.get('op') not in ('view', 'append'):
            raise DialogError('gateway_adapter_schema_rejected')
        raw = _wire(request)
        deadline = time.monotonic() + 2
        conn = HTTPConnection('127.0.0.1', self._port, timeout=2)
        try:
            conn.connect()
            conn.sock.settimeout(max(.001, deadline - time.monotonic()))
            conn.putrequest('POST', '/api/task', skip_accept_encoding=True)
            for key, value in {'Origin': self.origin, 'Authorization': 'Bearer ' + self._session,
                    'X-CSRF-Token': self._csrf, 'Content-Type': 'application/json',
                    'Content-Length': str(len(raw)), 'Connection': 'close'}.items():
                conn.putheader(key, value)
            conn.endheaders(raw)
            response = conn.getresponse()
            length = response.getheader('Content-Length', '')
            if (response.status != 200 or response.getheader('Transfer-Encoding') is not None or
                    response.getheader('Content-Type') != 'application/json; charset=utf-8' or
                    response.getheader('Cache-Control') != 'no-store' or
                    not re.fullmatch(r'[1-9][0-9]{0,5}', length) or int(length) > 512 * 1024):
                raise DialogError('gateway_result_unconfirmed')
            result = b''
            while len(result) < int(length):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DialogError('gateway_result_unconfirmed')
                if conn.sock is not None:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(8192, int(length) - len(result)))
                if not chunk:
                    raise DialogError('gateway_result_unconfirmed')
                result += chunk
            value = decode_json(result.decode('utf-8'))
            if type(value) is not dict or 'error' in value:
                raise DialogError('gateway_result_unconfirmed')
            return value
        except Exception:
            # Covers before/after-send ambiguity equally. Caller MUST retain
            # request/ID, never manufacture a new publication after this error.
            raise DialogError('gateway_result_unconfirmed') from None
        finally:
            conn.close()


class MCPAdapter:
    """Register these TWO handlers in the existing authenticated MCP host.

    client is a trusted host binding, not a tool argument. No role/task/session
    or arbitrary operation field is accepted from a model's tool input. Handler
    registration is explicit; importing this module never edits an MCP server.
    """
    def __init__(self, client):
        if type(client) is not GatewayClient:
            raise DialogError('gateway_adapter_binding_rejected')
        self._client = client

    def dialog_view(self, offset: int = 0, limit: int = 20):
        """Read a bounded page of the configured Git dialogue. No mutation."""
        return self._client.call({'op': 'view', 'offset': offset, 'limit': limit})

    def dialog_append(self, head: str, message_id: str, kind: str, body: str):
        """Append inert data once under the configured authenticated identity.

        On uncertainty retain these exact fields and ID; do not invent a new ID.
        """
        return self._client.call({'op': 'append', 'head': head, 'message_id': message_id,
                                  'kind': kind, 'body': body})


def register_mcp_tools(server, client):
    """Explicit SDK tool-decorator registration in a trusted authenticated host.

    The host MUST already authenticate and authorize the configured principal
    for these handlers. A shared server cannot use a global client's grant for
    arbitrary principals. This function adds no auth/bootstrap/MCP transport.
    Registration failure may be partial: STOP; do not retry on the same server.
    """
    adapter = MCPAdapter(client)
    server.tool()(adapter.dialog_view)
    server.tool()(adapter.dialog_append)
    return adapter


def main(argv=None, *, stdin=None, stdout=None):
    """One stdin envelope: session/csrf/request. Origin is the sole argv option.

    This NEW Git-dialog adapter is not bridge_cli.py and does not access the
    Hermes DB. No subprocess/wrapper/temp files/credential-file options.
    """
    parser = argparse.ArgumentParser(description='One authenticated shared-gateway request')
    parser.add_argument('--origin', required=True)
    args = parser.parse_args(argv)
    stdin, stdout = sys.stdin if stdin is None else stdin, sys.stdout if stdout is None else stdout
    try:
        raw = stdin.read(16385)
        if type(raw) is not str or len(raw.encode('utf-8')) > 16384:
            raise DialogError('gateway_adapter_schema_rejected')
        obj = decode_json(raw)
        if type(obj) is not dict or set(obj) != {'session', 'csrf', 'request'}:
            raise DialogError('gateway_adapter_schema_rejected')
        result = GatewayClient(args.origin, obj['session'], obj['csrf']).call(obj['request'])
        code = 0
    except Exception:
        result, code = {'error': 'gateway_result_unconfirmed', 'retry_authorized': False}, 1
    stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
