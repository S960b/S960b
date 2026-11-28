"""Concrete WSGI boundary for the existing shared dispatcher.

Not a login/bootstrap service. A trusted host supplies authenticated grants via
AccessAuthority; request bodies cannot issue grants or select identities. Use
the existing StrictChannel before WSGI normalization. Construct this object on
the SINGLE serving thread that owns the SQLite connections, not the acceptor
thread. Other threads fail closed. No listener/service is started on import.
"""
from http import HTTPStatus
import json
import re
import threading

from . import DialogError
from .append_store import AppendStore
from .ingress import Dispatcher
from .panel import security_headers

LIMIT = 8192
PUBLIC_ERRORS = frozenset({
    'access_denied', 'rpc_mutations_disabled', 'rpc_role_denied',
    'dialog_not_writable', 'message_id_conflict', 'publication_unknown',
    'gateway_publication_unresolved', 'stale_head', 'wrong_actor',
})


class GatewayApplication:
    """All MCP/CLI append requests reach the SAME durable gateway instance.

    Close/cancel/confirm remain unavailable at this deployment boundary, even
    if an offline Dispatcher was constructed with mutations enabled.
    """
    def __init__(self, dispatcher):
        if (type(dispatcher) is not Dispatcher or
                type(dispatcher.gateway.append_store) is not AppendStore or
                not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}', dispatcher.origin)):
            raise DialogError('gateway_host_binding_rejected')
        port = int(dispatcher.origin.rsplit(':', 1)[1])
        if not 1024 < port <= 65535 or port in (8765, 8787, 8789, 8790):
            raise DialogError('gateway_host_port_rejected')
        self.dispatcher = dispatcher
        self._thread = threading.current_thread()

    def __call__(self, env, start_response):
        code, result = 400, {'error': 'gateway_request_rejected'}
        try:
            if threading.current_thread() is not self._thread:
                raise DialogError('gateway_thread_rejected')
            allowed = {'HTTP_HOST', 'HTTP_ORIGIN', 'HTTP_AUTHORIZATION',
                       'HTTP_X_CSRF_TOKEN', 'HTTP_CONNECTION'}
            if (env.get('REMOTE_ADDR') != '127.0.0.1' or
                    env.get('REQUEST_METHOD') != 'POST' or
                    env.get('PATH_INFO') != '/api/task' or env.get('QUERY_STRING') or
                    env.get('SCRIPT_NAME', '') or
                    any(k.startswith('HTTP_') and k not in allowed for k in env) or
                    env.get('HTTP_CONNECTION', 'close').lower() != 'close' or
                    env.get('CONTENT_TYPE') != 'application/json' or
                    not re.fullmatch(r'[1-9][0-9]{0,3}', env.get('CONTENT_LENGTH', ''))):
                raise DialogError('gateway_request_rejected')
            size = int(env['CONTENT_LENGTH'])
            if size > LIMIT:
                raise DialogError('gateway_request_rejected')
            headers = [('host', env.get('HTTP_HOST', '')), ('origin', env.get('HTTP_ORIGIN', '')),
                       ('authorization', env.get('HTTP_AUTHORIZATION', '')),
                       ('x-csrf-token', env.get('HTTP_X_CSRF_TOKEN', '')),
                       ('content-type', 'application/json'), ('content-length', str(size))]
            # Authenticate before touching the input stream; Dispatcher repeats
            # checks immediately before dispatch (including grant expiry).
            if headers[0][1] != self.dispatcher.host or headers[1][1] != self.dispatcher.origin:
                raise DialogError('gateway_request_rejected')
            authorization = headers[2][1]
            if not authorization.startswith('Bearer '):
                raise DialogError('access_denied')
            self.dispatcher.authority.resolve(authorization[7:], headers[3][1])
            raw = env['wsgi.input'].read(size)
            if type(raw) is not bytes or len(raw) != size:
                raise DialogError('gateway_request_rejected')
            from .github import decode_json
            obj = decode_json(raw.decode('utf-8'))
            if type(obj) is not dict or obj.get('op') not in ('view', 'append'):
                raise DialogError('gateway_request_rejected')
            result = self.dispatcher.dispatch('POST', '/api/task', headers, raw)
            code = 200
        except DialogError as exc:
            error = exc.code if exc.code in PUBLIC_ERRORS else 'gateway_request_rejected'
            code = 401 if error == 'access_denied' else 409 if error in (
                'message_id_conflict', 'publication_unknown', 'gateway_publication_unresolved',
                'dialog_not_writable', 'stale_head', 'wrong_actor') else 403
            result = {'error': error}
        except Exception:
            # Never forward exception text, request bodies, tokens or paths.
            code, result = 503, {'error': 'gateway_unavailable'}
        body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(body) > 512 * 1024:
            code, body = 503, b'{"error":"gateway_response_too_large"}'
        headers = security_headers()
        headers.update({'Content-Type': 'application/json; charset=utf-8',
                        'Content-Length': str(len(body))})
        start_response(str(code) + ' ' + HTTPStatus(code).phrase, list(headers.items()))
        return [body]
