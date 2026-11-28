import io
import hashlib
import threading
import json
from types import SimpleNamespace

import pytest

from git_dialog.preview_smoke import FixtureHandler, FixtureServer, NOW, main
from git_dialog.panel import canary_snapshot, render_panel, security_headers


class MemorySocket:
    def __init__(self, raw): self.raw = raw; self.output = bytearray(); self.timeout = None
    def settimeout(self, value): self.timeout = value
    def makefile(self, mode, *args): assert mode == "rb"; return io.BytesIO(self.raw)
    def sendall(self, data): self.output.extend(data)


def request(raw):
    socket = MemorySocket(raw)
    server = SimpleNamespace(server_address=("127.0.0.1", 18878), server_name="localhost", server_port=18878)
    FixtureHandler(socket, ("127.0.0.1", 10000), server)
    assert socket.timeout == 0.5
    head, body = bytes(socket.output).split(b"\r\n\r\n", 1)
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return int(lines[0].split()[1]), headers, body


def test_memory_handler_exact_fixture_and_security_headers():
    status, headers, body = request(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:18878\r\n\r\n")
    assert status == 200 and body == render_panel(canary_snapshot(), now=NOW)
    for key, value in security_headers().items(): assert headers[key] == value
    assert headers["Connection"] == "close" and int(headers["Content-Length"]) == len(body)


@pytest.mark.parametrize("extra", [b"Host: evil.example\r\n", b"Content-Length: 1\r\n", 
    b"Content-Length: 0\r\nContent-Length: 0\r\n", b"Transfer-Encoding: chunked\r\n",
    b"Expect: 100-continue\r\n"])
def test_duplicate_and_body_framing_rejected_without_body_read(extra):
    status, headers, body = request(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:18878\r\n" + extra + b"\r\n")
    assert status == 400 and headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "PATCH", "OPTIONS", "HEAD"])
def test_mutating_and_other_methods_are_read_only(method):
    status, headers, body = request((method + " / HTTP/1.1\r\nHost: 127.0.0.1:18878\r\n\r\n").encode())
    assert status == 405 and headers["Allow"] == "GET"
    assert body == (b"" if method == "HEAD" else b"Read-only")


def test_unknown_method_error_sanitized_and_secure():
    status, headers, body = request(b"EXPLOIT /attacker HTTP/1.1\r\nHost: 127.0.0.1:18878\r\n\r\n")
    assert status == 501 and b"attacker" not in body and b"EXPLOIT" not in body
    assert headers["Cache-Control"] == "no-store"


def test_missing_host_and_wrong_host_fail_closed():
    assert request(b"GET / HTTP/1.1\r\n\r\n")[0] == 400
    assert request(b"GET / HTTP/1.1\r\nHost: evil.example\r\n\r\n")[0] == 403


def test_pipeline_connection_closes_after_first_request():
    status, headers, body = request(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:18878\r\n\r\n"
        b"DELETE / HTTP/1.1\r\nHost: 127.0.0.1:18878\r\n\r\n")
    assert status == 200 and b"HTTP/1.0" not in body


def test_cli_default_does_not_start_server(capsys):
    assert main([]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "only_self_test_supported"}


def test_watchdog_closes_only_owned_listener_and_active_connection():
    class Owned:
        def __init__(self): self.closed = 0; self.shutdowns = 0
        def shutdown(self, how): self.shutdowns += 1
        def close(self): self.closed += 1
    server = FixtureServer.__new__(FixtureServer)
    server._guard = threading.Lock()
    active, listener, unrelated = Owned(), Owned(), Owned()
    server._active, server.socket = active, listener
    server.abort_owned_connection()
    assert active.shutdowns == 1 and active.closed == 1 and listener.closed == 1
    assert not unrelated.closed and not unrelated.shutdowns


def test_fixed_fixture_root_hash_is_reproducible():
    assert hashlib.sha256(render_panel(canary_snapshot(), now=NOW)).hexdigest() == (
        "05f7537abe6f81a4ef34d1a3789c2a80dcd9fb1dc5a79ab5e78ade60fbf21e11")
