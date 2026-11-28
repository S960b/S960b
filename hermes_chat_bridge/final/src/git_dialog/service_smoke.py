"""Opt-in bounded real Waitress/auth smoke; ephemeral synthetic data only."""
import base64
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets
import tempfile
import threading

from . import DialogError
from .service import AuthAuthority, PanelApplication, SnapshotReader, initialize


def run():
    from waitress.server import create_server
    with tempfile.TemporaryDirectory(prefix="git-dialog-p1-smoke-") as temp:
        password = secrets.token_urlsafe(24)
        initialize(Path(temp) / "state", password)
        app = None
        channels = {}
        server = create_server(lambda env, start: app(env, start), host="127.0.0.1", port=0,
            threads=2, map=channels, connection_limit=8, channel_timeout=2, cleanup_interval=1,
            max_request_header_size=8192, max_request_body_size=1024, expose_tracebacks=False,
            log_socket_errors=False, clear_untrusted_proxy_headers=True)
        port = int(server.effective_port)
        app = PanelApplication(AuthAuthority(Path(temp) / "state"), SnapshotReader(), port)
        thread = threading.Thread(target=server.run, name="p1-auth-http-smoke", daemon=True)
        def close():
            for channel in list(channels.values()):
                try: channel.close()
                except Exception: pass
        watchdog = threading.Timer(10, close)
        watchdog.daemon = True
        thread.start(); watchdog.start()
        outcomes = []
        page_hash = None
        try:
            basic = "Basic " + base64.b64encode(("owner:" + password).encode()).decode()
            for method, path, authorization, expected in (
                ("GET", "/", None, 401), ("GET", "/", basic, 200),
                ("GET", "/api/status", basic, 200), ("GET", "/bridge/status", basic, 200),
                ("POST", "/", basic, 405), ("DELETE", "/", basic, 405)):
                connection = HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    headers = {"Connection": "close"}
                    if authorization: headers["Authorization"] = authorization
                    connection.request(method, path, headers=headers)
                    response = connection.getresponse(); body = response.read(256 * 1024 + 1)
                    if response.status != expected or len(body) > 256 * 1024 or response.getheader("Cache-Control") != "no-store":
                        raise DialogError("p1_http_smoke_failed")
                    if path == "/" and expected == 200:
                        if b"FIXTURE_PONG_NO_EXECUTION" not in body: raise DialogError("p1_fixture_missing")
                        page_hash = hashlib.sha256(body).hexdigest()
                    if path.endswith("status"):
                        status = json.loads(body)
                        if status["mode"] != "synthetic" or not status["read_only"]:
                            raise DialogError("p1_status_invalid")
                    outcomes.append(response.status)
                finally: connection.close()
        finally:
            close(); server.task_dispatcher.shutdown(); thread.join(timeout=2)
            watchdog.cancel(); watchdog.join(timeout=1)
        if thread.is_alive(): raise DialogError("p1_smoke_shutdown_failed")
        return {"status": "passed", "http_statuses": outcomes, "auth": "required_and_verified",
                "source": "synthetic", "read_only": True, "temporary_database": True,
                "old_services_changed": False, "page_sha256": page_hash, "shutdown": "closed"}


def main():
    try: result = run()
    except DialogError as exc: result = {"error": exc.code}
    except Exception: result = {"error": "p1_smoke_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
