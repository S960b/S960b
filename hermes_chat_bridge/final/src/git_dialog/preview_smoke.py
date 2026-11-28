"""Bounded loopback self-test of SYNTHETIC preview only, not a service.

Only CLI: --self-test. No configurable address/data/path/token, file IO, SQLite,
GitHub, sessions, RPC, arbitrary bodies, or installation. Exits after checks.
"""
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import threading
import time
import sys

from . import DialogError
from .panel import canary_snapshot, preview_response, render_panel, security_headers

NOW = datetime(2026, 10, 26, 15, 1, tzinfo=timezone.utc)


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "SyntheticPreview"
    sys_version = ""

    def setup(self):
        self.request.settimeout(0.5)
        super().setup()

    def log_message(self, *args):
        pass  # Never log request text, paths, headers, body or exceptions.

    def _reply(self, code, body, headers=None):
        self.close_connection = True
        self.send_response(code)
        for key, value in (security_headers() if headers is None else headers).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self._reply(code, b"Request rejected")  # Do not echo attacker text.

    def _handle(self):
        headers = list(self.headers.raw_items())
        hosts = self.headers.get_all("Host", [])
        lengths = self.headers.get_all("Content-Length", [])
        if (len(headers) > 16 or len(hosts) != 1 or len(lengths) > 1 or
                (lengths and lengths != ["0"]) or "Transfer-Encoding" in self.headers or
                "Expect" in self.headers or
                any(len(k) + len(v) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in k + v)
                    for k, v in headers)):
            self._reply(400, b"Invalid request")
            return
        expected = "127.0.0.1:" + str(self.server.server_address[1])
        code, response_headers, body = preview_response(canary_snapshot(), self.command,
            self.path, host=hosts[0], expected_host=expected, now=NOW)
        self._reply(code, body, response_headers)

    do_GET = _handle
    do_POST = _handle
    do_DELETE = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


class FixtureServer(HTTPServer):
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, *args, **kwargs):
        self._active = None
        self._guard = threading.Lock()
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        with self._guard:
            self._active = request
        return request, address

    def close_request(self, request):
        with self._guard:
            if self._active is request:
                self._active = None
        super().close_request(request)

    def abort_owned_connection(self):
        # Absolute watchdog handles slow trickling headers, not just per-read timeout.
        with self._guard:
            if self._active is not None:
                try:
                    self._active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._active.close()
        self.server_close()

    def handle_error(self, request, client_address):
        pass  # Timeouts/broken clients do not expose tracebacks or request data.


def run_self_test():
    server = FixtureServer(("127.0.0.1", 0), FixtureHandler)
    server.timeout = 0.2
    address, port = server.server_address
    if address != "127.0.0.1" or port <= 1024 or port == 8787:
        server.server_close()
        raise DialogError("preview_bind_rejected")
    stop = threading.Event()
    served = []
    deadline = time.monotonic() + 10
    def serve():
        try:
            while not stop.is_set() and time.monotonic() < deadline and len(served) < 12:
                server.handle_request()
                served.append(1)
        except (OSError, ValueError):
            # Watchdog can close the owned listener while select/accept unwinds.
            stop.set()
        finally:
            server.server_close()
    thread = threading.Thread(target=serve, name="synthetic-preview-bounded")
    def watchdog_stop():
        stop.set()
        server.abort_owned_connection()
    watchdog = threading.Timer(12, watchdog_stop)
    watchdog.daemon = True
    thread.start()
    watchdog.start()
    results, root_hash = [], None
    try:
        cases = [("GET", "/", {}, 200), ("GET", "/history?offset=1&limit=1", {}, 200),
                 ("POST", "/", {}, 405), ("DELETE", "/", {}, 405),
                 ("GET", "/api/task", {}, 404), ("GET", "/", {"Host": "evil.example"}, 403),
                 ("GET", "/?limit=99", {}, 400)]
        for method, path, headers, expected in cases:
            connection = HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                connection.request(method, path, headers=headers)
                response = connection.getresponse()
                body = response.read(256 * 1024 + 1)
                if response.status != expected or len(body) > 256 * 1024:
                    raise DialogError("preview_http_check_failed")
                for key, value in security_headers().items():
                    if response.getheader(key) != value:
                        raise DialogError("preview_security_header_failed")
                if len(results) == 0:
                    reference = render_panel(canary_snapshot(), now=NOW)
                    if body != reference:
                        raise DialogError("preview_body_mismatch")
                    root_hash = hashlib.sha256(body).hexdigest()
                results.append(response.status)
            finally:
                connection.close()
    finally:
        stop.set()
        server.abort_owned_connection()
        thread.join(timeout=2)
        server.server_close()
        watchdog.cancel()
        watchdog.join(timeout=1)
    if thread.is_alive():
        raise DialogError("preview_shutdown_failed")
    return {"status": "passed", "scope": "synthetic_fixture_only", "bind": "loopback_ephemeral",
            "http_statuses": results, "checks": len(results), "security_headers": "passed",
            "root_sha256": root_hash, "shutdown": "joined_and_socket_closed",
            "real_data": "not_used", "git_api": "not_used", "credentials": "not_used"}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args != ["--self-test"]:
        print('{"error":"only_self_test_supported"}')
        return 2
    try:
        result = run_self_test()
    except DialogError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "preview_self_test_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
