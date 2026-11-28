"""Synthetic in-memory Starlette routing proof; no listener, real bridge or keys."""
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
from types import SimpleNamespace

from .native_notify import DOMAIN, PATH, attach
from .preflight import TASK_ID


def check():
    from starlette.applications import Starlette
    from starlette.routing import Mount
    calls = []
    async def fallback(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 404, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})
    key = b'synthetic-not-a-deployed-key!!xxx'
    key = hashlib.sha256(key).digest()
    job = {'job_id': 'test_fixture.job', 'queue': 'test', 'task_id': TASK_ID,
           'direction': 'to_chatgpt', 'status': 'pending'}
    bridge = SimpleNamespace(_beta_forwarder=SimpleNamespace(_key=key),
        queue=SimpleNamespace(get_message=lambda job_id: job),
        emit_test_event=lambda job_id, **kw: calls.append((job_id, kw)))
    app = Starlette(routes=[Mount('/', app=fallback)])
    attach(app, bridge)
    raw = json.dumps({'job_id': job['job_id'], 'queue': 'test', 'task_id': TASK_ID,
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}).encode()
    signature = 'sha256=' + hmac.new(key, DOMAIN + raw, hashlib.sha256).hexdigest()
    async def invoke(path, sig):
        sent = []
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': 'POST', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
            'root_path': '', 'query_string': b'', 'client': ('127.0.0.1', 1234),
            'server': ('127.0.0.1', 8765), 'headers': [(b'host', b'127.0.0.1:8765'),
            (b'content-type', b'application/json'), (b'content-length', str(len(raw)).encode()),
            (b'x-hermes-notify', sig.encode())]}
        async def receive(): return {'type': 'http.request', 'body': raw, 'more_body': False}
        async def send(event): sent.append(event)
        await app(scope, receive, send)
        return sent[0]['status']
    async def run():
        return [await invoke(PATH, 'sha256=' + '0' * 64), await invoke(PATH, signature),
                await invoke('/unrelated', signature)]
    statuses = asyncio.run(run())
    if statuses != [401, 202, 404] or calls != [(job['job_id'], {'queue': 'test'})]:
        raise ValueError('routing_check_failed')
    return {'status': 'native_notify_asgi_checked', 'http_statuses': statuses,
            'producer_calls': 1, 'real_bridge_imported': False, 'listener_started': False}


def main():
    try:
        result = check()
        code = 0
    except Exception:
        result, code = {'error': 'native_notify_asgi_check_failed'}, 1
    print(json.dumps(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
