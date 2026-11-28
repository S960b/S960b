"""One bounded synthetic forwarding hop, no bridge subscription or real data."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import threading

from .wakeup import WakeupGate
from .wakeup_forward import WakeupForwarder
from .wakeup_smoke import fixture_event


def run():
    key = secrets.token_bytes(32)
    gate, accepted = WakeupGate(key), threading.Event()
    statuses = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.connection.settimeout(1)
            size = int(self.headers.get('Content-Length', '0'))
            if self.path != '/bridge/wakeup' or not 1 <= size <= 4096:
                self.send_error(400); return
            code, _ = gate.accept(self.rfile.read(size), self.headers.get('X-Hermes-Signature'))
            statuses.append(code)
            self.send_response(code); self.send_header('Content-Length', '0'); self.end_headers()
            if code == 202: accepted.set()
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = sender = None
    try:
        port = server.server_address[1]
        if port <= 1024 or port in (8787, 8789): raise RuntimeError('port')
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        sender = WakeupForwarder(key, port=port); sender.start()
        data = fixture_event()['data']; data['created_at_utc'] = '2020-01-01T00:00:00Z'
        if not sender.offer(data) or not accepted.wait(2): raise RuntimeError('delivery')
    finally:
        if sender is not None: sender.stop()
        if thread is not None:
            server.shutdown(); thread.join(timeout=2)
        server.server_close()
    if thread.is_alive() or statuses != [202]: raise RuntimeError('shutdown')
    return {'status': 'passed', 'http_statuses': statuses, 'delayed_job_supported': True,
            'shutdown': 'threads_joined_socket_closed', 'real_bridge_git_credentials_used': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', required=True, action='store_true')
    parser.parse_args(argv)
    try: result = run()
    except Exception: result = {'error': 'forward_smoke_failed'}
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
