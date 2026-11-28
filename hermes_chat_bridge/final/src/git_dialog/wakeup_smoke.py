"""Bounded synthetic-only HTTP self-test. No config/keys/paths accepted."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time

from waitress.channel import HTTPChannel
from waitress.parser import HTTPRequestParser, ParsingError

from . import DialogError
from .preflight import TASK_ID
from .service import AuthAuthority, PanelApplication, SnapshotReader, initialize
from .wakeup_host import WakeupHost


class StrictParser(HTTPRequestParser):
    """Reject ambiguity before Waitress normalizes/removes framing headers.
    Local subclass only, never changes the installed P1 or global Waitress.
    """
    def parse_header(self, header):
        seen = set()
        for line in header.split(b"\r\n")[1:]:
            if not line:
                continue
            name = line.split(b":", 1)[0].lower()
            if (name in seen or b"_" in name or name.startswith(b"x-forwarded-") or
                    name in (b"transfer-encoding", b"expect", b"forwarded")):
                raise ParsingError("Request framing rejected")
            seen.add(name)
        super().parse_header(header)
        if self.proxy_scheme or self.proxy_netloc or self.fragment:
            raise ParsingError("Origin form required")
        self.connection_close = True


class StrictChannel(HTTPChannel):
    parser_class = StrictParser


def close_owned(channels):
    for channel in list(channels.values()):
        try:
            channel.close()
        except Exception:
            pass


def fixture_event():
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"name": "hermes.message.created", "eventId": "evt_" + secrets.token_hex(16),
        "timestamp": stamp, "data": {"created_at_utc": stamp, "direction": "to_chatgpt",
        "job_id": "synthetic_wakeup_probe.job", "queue": "test", "status": "pending", "task_id": TASK_ID}}


def run():
    from waitress.server import create_server
    with tempfile.TemporaryDirectory(prefix="git-dialog-wakeup-smoke-") as temp:
        password, key = secrets.token_urlsafe(24), secrets.token_bytes(32)
        initialize(Path(temp) / "state", password)
        stop, ready, drained, failed = (threading.Event() for _ in range(4))
        channels, counts = {}, {"periodic": 0, "wakeup": 0}
        host = None
        deadline, requests = time.monotonic() + 10, 0
        request_lock = threading.Lock()
        def application(env, start):
            nonlocal requests
            with request_lock:
                requests += 1
                allowed = requests <= 16 and time.monotonic() < deadline and not stop.is_set()
            if not allowed:
                start("503 Service Unavailable", [("Content-Length", "0"), ("Cache-Control", "no-store")])
                return [b""]
            return host(env, start)
        server = create_server(application, host="127.0.0.1", port=0, map=channels,
            threads=2, connection_limit=8, channel_timeout=1, cleanup_interval=1,
            max_request_header_size=8192, max_request_body_size=4096,
            expose_tracebacks=False, log_socket_errors=False, clear_untrusted_proxy_headers=True)
        server.channel_class = StrictChannel
        server_thread = runner = watchdog = None
        statuses, page_hash = [], None
        try:
            port = int(server.effective_port)
            if port <= 1024 or port in (8787, 8789):
                raise DialogError("wakeup_smoke_port_rejected")
            host = WakeupHost(PanelApplication(AuthAuthority(Path(temp) / "state"), SnapshotReader(), port), key=key)
            def reader_loop():
                try:
                    while not stop.is_set():
                        result = host.loop.tick()
                        if result["refreshed"]:
                            counts[result["reason"]] += 1
                        if result["reason"] == "wakeup":
                            drained.set()
                        ready.set()
                        stop.wait(0.02)
                except Exception:
                    failed.set(); ready.set(); stop.set()
            def shutdown():
                stop.set(); close_owned(channels)
            watchdog = threading.Timer(10, shutdown); watchdog.daemon = True
            server_thread = threading.Thread(target=server.run, name="wakeup-smoke-http", daemon=True)
            runner = threading.Thread(target=reader_loop, name="wakeup-smoke-reader", daemon=True)
            watchdog.start(); runner.start(); server_thread.start()
            if not ready.wait(1) or failed.is_set():
                raise DialogError("wakeup_smoke_runner_failed")
            basic = "Basic " + base64.b64encode(("owner:" + password).encode()).decode()
            def signed(obj):
                body = json.dumps(obj, sort_keys=True).encode()
                return body, "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()
            raw, signature = signed(fixture_event())
            other = fixture_event(); other["data"]["task_id"] = "task_other_fixture"
            foreign, foreign_sig = signed(other)
            invalid, invalid_sig = signed({"command": "never execute data"})
            cases = [("GET", "/", None, {}, 401),
                ("GET", "/", None, {"Authorization": basic}, 200),
                ("POST", "/bridge/wakeup", raw, {"X-Hermes-Signature": signature}, 202),
                ("POST", "/bridge/wakeup", raw, {"X-Hermes-Signature": signature}, 200),
                ("POST", "/bridge/wakeup", raw, {}, 401),
                ("POST", "/bridge/wakeup", foreign, {"X-Hermes-Signature": foreign_sig}, 200),
                ("POST", "/bridge/wakeup", invalid, {"X-Hermes-Signature": invalid_sig}, 400),
                ("GET", "/bridge/wakeup", None, {}, 405),
                ("DELETE", "/", None, {"Authorization": basic}, 405)]
            for method, target, body, headers, expected in cases:
                connection = HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request(method, target, body=body,
                        headers={"Connection": "close", "Content-Type": "application/json", **headers})
                    response = connection.getresponse(); data = response.read(256 * 1024 + 1)
                    if (response.status != expected or len(data) > 256 * 1024 or
                            response.getheader("Cache-Control") != "no-store" or
                            response.getheader("X-Content-Type-Options") != "nosniff"):
                        raise DialogError("wakeup_smoke_http_failed")
                    statuses.append(response.status)
                    if expected == 202:
                        if json.loads(data)["status"] != "scheduled" or not drained.wait(1):
                            raise DialogError("wakeup_smoke_not_drained")
                    if target == "/" and expected == 200:
                        if b"FIXTURE_PONG_NO_EXECUTION" not in data:
                            raise DialogError("wakeup_smoke_fixture_failed")
                        page_hash = hashlib.sha256(data).hexdigest()
                finally:
                    connection.close()
            # Raw ambiguous framing must be rejected by the parser, not WSGI.
            for extra in (("Content-Length", "3"), ("Transfer-Encoding", "chunked")):
                connection = HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.putrequest("POST", "/bridge/wakeup")
                    connection.putheader("Content-Length", "2")
                    connection.putheader(*extra)
                    connection.putheader("Connection", "close")
                    connection.endheaders(b"{}")
                    response = connection.getresponse(); data = response.read(8193)
                    if response.status != 400 or len(data) > 8192:
                        raise DialogError("wakeup_smoke_framing_failed")
                    statuses.append(400)
                finally:
                    connection.close()
            if counts != {"periodic": 1, "wakeup": 1} or failed.is_set():
                raise DialogError("wakeup_smoke_read_count_failed")
        finally:
            stop.set(); close_owned(channels)
            if runner is not None and runner.ident is not None:
                runner.join(timeout=2)
            if host is not None:
                host.loop.stop()
            server.task_dispatcher.shutdown(timeout=1)
            if server_thread is not None and server_thread.ident is not None:
                server_thread.join(timeout=2)
            if watchdog is not None:
                watchdog.cancel()
                if watchdog.ident is not None: watchdog.join(timeout=1)
        if ((runner is not None and runner.is_alive()) or
                (server_thread is not None and server_thread.is_alive()) or channels or
                server.task_dispatcher.threads):
            raise DialogError("wakeup_smoke_shutdown_failed")
        return {"status": "passed", "http_statuses": statuses, "reader_attempts": counts,
            "source": "synthetic", "temporary_database": True, "ephemeral_test_key": True,
            "real_data_git_credentials_used": False, "old_services_changed": False,
            "page_sha256": page_hash, "shutdown": "threads_joined_sockets_closed"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded synthetic-only wakeup HTTP self-test")
    parser.add_argument("--self-test", required=True, action="store_true")
    parser.parse_args(argv)
    try:
        result = run()
    except DialogError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "wakeup_smoke_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
