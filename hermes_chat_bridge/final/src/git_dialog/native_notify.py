"""Scoped notification of an ALREADY-created report; never a report-content writer."""
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import socket
import stat
import threading
import time

from .github import decode_json
from .preflight import TASK_ID
from .wakeup import _stamp

PATH = '/bridge/beta-notify'
DOMAIN = b'git-dialog-native-notify-v1\x00'
STATE = '/home/kali/agent-dialog-beta/state'
JOB = re.compile(r'[a-z][a-z0-9_]{0,63}\.job')
MAX_BODY = 1024


class NotifyGate:
    def __init__(self, key, bridge):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError('invalid_notify_binding')
        self.key, self.bridge = key, bridge
        self.seen, self.last, self.lock = {}, None, threading.Lock()

    def accept(self, raw, signature, *, now=None, monotonic=None):
        if (type(raw) is not bytes or not 1 <= len(raw) <= MAX_BODY or
                type(signature) is not str or not re.fullmatch(r'sha256=[0-9a-f]{64}', signature)):
            return 401, 'denied'
        expected = 'sha256=' + hmac.new(self.key, DOMAIN + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return 401, 'denied'
        now = time.time() if now is None else now
        mono = time.monotonic() if monotonic is None else monotonic
        try:
            value = decode_json(raw.decode('utf-8'))
            if (type(value) is not dict or set(value) != {'job_id', 'timestamp', 'queue', 'task_id'} or
                    type(value['job_id']) is not str or not JOB.fullmatch(value['job_id']) or
                    value['queue'] != 'test' or value['task_id'] != TASK_ID or
                    not -30 <= now - _stamp(value['timestamp']) <= 120):
                raise ValueError()
        except Exception:
            return 400, 'invalid'
        identity = hashlib.sha256(value['job_id'].encode()).digest()
        with self.lock:
            self.seen = {k: expiry for k, expiry in self.seen.items() if expiry > mono}
            if identity in self.seen:
                return 200, 'duplicate'
            if len(self.seen) >= 256 or (self.last is not None and mono - self.last < 30):
                return 429, 'limited'
            # Reserve before callback; unknown effects are never retried here.
            self.seen[identity], self.last = mono + 180, mono
        try:
            job = self.bridge.queue.get_message(value['job_id'])  # No Work-claim.
            if (type(job) is not dict or job.get('job_id') != value['job_id'] or
                    job.get('queue') != 'test' or job.get('task_id') != TASK_ID or
                    job.get('direction') != 'to_chatgpt' or job.get('status') != 'pending'):
                return 200, 'ignored'
            # Fixed existing producer only. Never use text, body-selected callback,
            # arbitrary DB paths, imports, shell, job execution or a new Queue put.
            self.bridge.emit_test_event(value['job_id'], queue='test')
            return 202, 'notified'
        except Exception:
            return 503, 'unknown'


def attach(app, bridge):
    """Called ONLY by reviewed native startup when its forwarder is configured."""
    if bridge._beta_forwarder is None:
        return False
    if any(getattr(route, 'path', None) == PATH for route in app.router.routes):
        raise ValueError('notify_route_already_present')
    gate = NotifyGate(bridge._beta_forwarder._key, bridge)
    app.router.add_route(PATH, NotifyASGI(gate))
    app.router.routes.insert(0, app.router.routes.pop())
    return True


class NotifyASGI:
    """Plain ASGI callable; original router supplies routing, no new dependency."""
    def __init__(self, gate):
        self.gate = gate

    async def __call__(self, scope, receive, send):
        code, status = 403, 'denied'
        headers = scope.get('headers', [])
        names = [key.lower() for key, _ in headers]
        values = dict(headers)
        try:
            if (scope.get('client', ('',))[0] != '127.0.0.1' or
                    scope.get('raw_path', b'') != PATH.encode() or scope.get('query_string') or
                    len(names) != len(set(names)) or
                    sum(len(k) + len(v) for k, v in headers) > 8192 or
                    any(k in (b'origin', b'forwarded', b'transfer-encoding', b'expect') or
                        k.startswith(b'x-forwarded-') for k in names) or
                    values.get(b'host') != b'127.0.0.1:8765'):
                pass
            elif scope.get('method') != 'POST':
                code, status = 405, 'post_only'
            elif (values.get(b'content-type') != b'application/json' or
                    not re.fullmatch(rb'[1-9][0-9]{0,3}', values.get(b'content-length', b'')) or
                    int(values[b'content-length']) > MAX_BODY):
                code, status = 400, 'invalid'
            else:
                raw = b''
                async with asyncio.timeout(2):
                    while True:
                        event = await receive()
                        if event.get('type') != 'http.request':
                            raise ValueError()
                        chunk = event.get('body', b'')
                        if type(chunk) is not bytes or len(raw) + len(chunk) > int(values[b'content-length']):
                            raise ValueError()
                        raw += chunk
                        if not event.get('more_body', False):
                            break
                if len(raw) != int(values[b'content-length']):
                    raise ValueError()
                signature = values.get(b'x-hermes-notify', b'').decode('ascii')
                code, status = self.gate.accept(raw, signature)
        except Exception:
            code, status = 400, 'invalid'
        body = json.dumps({'status': status}, separators=(',', ':')).encode()
        await send({'type': 'http.response.start', 'status': code, 'headers': [
            (b'content-type', b'application/json'), (b'cache-control', b'no-store'),
            (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})


def _enabled():
    try:
        fd = os.open(STATE + '/bridge-wakeup.enabled', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0):
            raise ValueError('activation_rejected')
    finally:
        os.close(fd)
    return True


def notify(job_id):
    """One bounded attempt, called AFTER existing Queue put and BEFORE legacy emit.

    False/unknown never retries or prevents the original event path. No content
    write, report body read, credential prompt/discovery, proxies or redirects.
    """
    try:
        if type(job_id) is not str or not JOB.fullmatch(job_id) or not _enabled():
            return False
        from .wakeup_key import load
        key = load(STATE)
        raw = json.dumps({'job_id': job_id, 'queue': 'test', 'task_id': TASK_ID,
                          'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
                         separators=(',', ':')).encode()
        signature = 'sha256=' + hmac.new(key, DOMAIN + raw, hashlib.sha256).hexdigest()
        request = (f'POST {PATH} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nContent-Type: application/json\r\n'
                   f'Content-Length: {len(raw)}\r\nX-Hermes-Notify: {signature}\r\nConnection: close\r\n\r\n').encode() + raw
        deadline = time.monotonic() + 2
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
            conn.settimeout(max(.001, deadline - time.monotonic()))
            conn.connect(('127.0.0.1', 8765))
            conn.settimeout(max(.001, deadline - time.monotonic()))
            conn.sendall(request)
            received = b''
            while b'\r\n\r\n' not in received:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(received) >= 8192:
                    return False
                conn.settimeout(remaining)
                data = conn.recv(min(1024, 8192 - len(received)))
                if not data:
                    return False
                received += data
            return bool(re.fullmatch(rb'HTTP/1\.[01] 202 [^\r\n]*', received.split(b'\r\n', 1)[0]))
    except Exception:
        return False
