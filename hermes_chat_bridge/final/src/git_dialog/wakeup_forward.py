"""Opt-in metadata-only producer adapter. No subscription, job reads or writes.

The reviewed producer calls offer(payload), never a handler selected by body.
No background work until start(). Queue bounded to one hint; polling fallback.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import queue
import re
import socket
import sys
import threading
import time

from . import DialogError
from .wakeup import WakeupGate


def _notice(accepted=False):
    # Fixed, nonsecret deployment proof only. Never include request/signature,
    # job identifiers, key/path, exception or response contents in service logs.
    try:
        print('{"beta_wakeup":"receiver_accepted"}' if accepted else
              '{"beta_wakeup":"forwarder_started"}', file=sys.stderr, flush=True)
    except Exception:
        pass  # Diagnostics must not affect original event delivery or retries.


def _post(port, raw, signature):
    """Literal loopback, fixed route, total 2s deadline, bounded headers only."""
    deadline = time.monotonic() + 2
    request = (f"POST /bridge/wakeup HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
        f"X-Hermes-Signature: {signature}\r\nConnection: close\r\n\r\n").encode() + raw
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(max(0.001, deadline - time.monotonic()))
        conn.connect(('127.0.0.1', port))
        conn.settimeout(max(0.001, deadline - time.monotonic()))
        conn.sendall(request)
        received = b''
        while b'\r\n\r\n' not in received:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(received) >= 8192: return False
            conn.settimeout(remaining)
            data = conn.recv(min(1024, 8192 - len(received)))
            if not data: return False
            received += data
        return bool(re.fullmatch(rb'HTTP/1\.[01] (?:200|202) [^\r\n]*', received.split(b'\r\n', 1)[0]))


class WakeupForwarder:
    def __init__(self, key, *, port=8790):
        if type(port) is not int or not 1024 < port <= 65535 or port in (8787, 8789):
            raise DialogError('forward_port_rejected')
        self._gate = WakeupGate(key)  # Exact schema/auth bounds + scoped replay/rate filter.
        self._key, self._port = key, port
        self._queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        with self._lock:
            if self._stop.is_set(): raise DialogError('forward_stopped')
            if self._thread is not None: return
            self._thread = threading.Thread(target=self._run, name='beta-wakeup-forwarder', daemon=True)
            self._thread.start()

    def offer(self, payload):
        """Nonblocking, best-effort. Errors must not break the original producer."""
        try:
            with self._lock:
                if self._stop.is_set() or self._thread is None: return False
                if type(payload) is not dict or set(payload) != {
                        'created_at_utc', 'direction', 'job_id', 'queue', 'status', 'task_id'}:
                    return False
                # Copy only fixed metadata; no arbitrary envelope/cursor/job body.
                data = dict(payload)
                if any(type(v) is not str or len(v) > 128 for v in data.values()): return False
                event = {'name': 'hermes.message.created', 'eventId': 'evt_' +
                    hashlib.sha256((data['queue'] + ':' + data['job_id']).encode()).hexdigest()[:32],
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'data': data}
                raw = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode()
                signature = 'sha256=' + hmac.new(self._key, raw, hashlib.sha256).hexdigest()
                if self._gate.accept(raw, signature)[0] != 202: return False
                self._queue.put_nowait((raw, signature))
                return True
        except Exception:
            return False

    def _run(self):
        _notice()
        while not self._stop.is_set():
            try: raw, signature = self._queue.get(timeout=0.1)
            except queue.Empty: continue
            if self._stop.is_set(): break
            try:
                if _post(self._port, raw, signature): _notice(accepted=True)
            except Exception: pass  # No retry, no secret/response/error output.
            finally: self._queue.task_done()

    def stop(self):
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
            if thread.is_alive(): raise DialogError('forward_shutdown_failed')
