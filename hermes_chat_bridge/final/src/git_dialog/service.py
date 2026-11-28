"""Stage P1: isolated authenticated READ-ONLY loopback WSGI sidecar.

No changes to 8787/bridge/services/OAuth/cron, no Git mutation or body execution.
Owner installs/runs foreground. Synthetic default; real polling explicit opt-in.
"""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import hmac
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
import threading
import time

from . import DialogError
from .github import ApiError, GitHubDialog, JsonHttp
from .journal_probe import _main_unchanged
from .panel import canary_snapshot, preview_response, security_headers
from .preflight import TASK_ID, ReadOnlyTransport, read_configured_token

APP_ID = 0x47445031


def _private_dir(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise DialogError("service_state_directory_rejected")
    return path


def _private_file(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise DialogError("service_state_file_rejected")


def _password_bytes(password):
    try:
        raw = password.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise DialogError("password_rejected") from None
    if not 14 <= len(raw) <= 256 or any(c < 32 or c == 127 for c in raw):
        raise DialogError("password_rejected")
    return raw


def _derive(raw, salt):
    return hashlib.scrypt(raw, salt=salt, n=32768, r=8, p=1, maxmem=128 * 1024 * 1024, dklen=32)


def initialize(state_dir, password):
    """New dedicated directory/DB ONLY. Never overwrite an existing state."""
    raw = _password_bytes(password)
    directory = Path(state_dir)
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir(mode=0o700)  # Existing directory => stop, not reset password.
    except FileExistsError:
        raise DialogError("service_state_already_exists") from None
    _private_dir(directory)
    path = directory / "panel.sqlite"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    salt = os.urandom(32)
    digest = _derive(raw, salt)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(f"PRAGMA application_id={APP_ID}")
        conn.execute("CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL, digest BLOB NOT NULL)")
        conn.execute("INSERT INTO owner VALUES (1,?,?)", (salt, digest))
        conn.commit()
    return {"status": "initialized", "username": "owner", "mode": "synthetic", "database": "panel.sqlite"}


class AuthAuthority:
    """Single local owner authority for P1. No public grant/bootstrap RPC."""
    def __init__(self, state_dir):
        directory = _private_dir(state_dir)
        path = directory / "panel.sqlite"
        _private_file(path)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if conn.execute("PRAGMA application_id").fetchone()[0] != APP_ID or tables != {"owner"}:
                raise DialogError("wrong_service_database")
            rows = conn.execute("SELECT id,salt,digest FROM owner").fetchall()
        if (len(rows) != 1 or rows[0][0] != 1 or
                any(not isinstance(v, bytes) or len(v) != 32 for v in rows[0][1:])):
            raise DialogError("invalid_service_identity")
        self._salt, self._digest = rows[0][1:]
        self._lock = threading.Lock()
        self._failures, self._window = 0, time.monotonic()

    def verify(self, authorization):
        if not authorization:
            return False
        with self._lock:
            now = time.monotonic()
            if now - self._window >= 60:
                self._failures, self._window = 0, now
            if self._failures >= 5:
                raise DialogError("authentication_throttled")
            try:
                if not isinstance(authorization, str) or len(authorization) > 1024 or not authorization.startswith("Basic "):
                    raise ValueError()
                user, password = base64.b64decode(authorization[6:], validate=True).decode("utf-8").split(":", 1)
                raw = _password_bytes(password)
                digest = _derive(raw, self._salt)
                good = hmac.compare_digest(user.encode("utf-8"), b"owner") and hmac.compare_digest(digest, self._digest)
            except (ValueError, UnicodeError, DialogError):
                good = False
            if not good:
                self._failures += 1
            # Success does not reset the global failure bucket (brute-force bound).
            return good


class SnapshotReader:
    def __init__(self, transport=None, *, isolated=None):
        if isolated is not None:
            from .isolated_read import IsolatedRead
            if type(isolated) is not IsolatedRead or transport is not None:
                raise DialogError("poll_binding_rejected")
        self._isolated = isolated
        self.mode = "synthetic" if transport is None and isolated is None else "github"
        self.transport = None if transport is None else ReadOnlyTransport(transport)
        self._lock = threading.Lock()
        self._snapshot = canary_snapshot() if self.mode == "synthetic" else None
        self._error, self._checked = None, None

    def refresh(self):
        if self._isolated is not None:
            try:
                with self._lock: previous = self._snapshot
                snapshot = self._isolated.read(previous)
                with self._lock:
                    self._snapshot, self._error = snapshot, None
                    self._checked = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except DialogError as exc:
                with self._lock: self._error = exc.code
            except Exception:
                with self._lock: self._error = "poll_failed"
            return
        if self.transport is None:
            return
        try:
            dialog = GitHubDialog(self.transport)
            dialog._private()
            _main_unchanged(self.transport)
            head = dialog._head(TASK_ID)
            with self._lock:
                previous = self._snapshot
            snapshot = previous if previous is not None and head == previous.head else dialog._snapshot(TASK_ID, head)[0]
            if previous and (snapshot.task != previous.task or snapshot.messages[:len(previous.messages)] != previous.messages):
                raise DialogError("poll_history_changed")
            _main_unchanged(self.transport)
            if dialog._head(TASK_ID) != head:
                raise DialogError("poll_snapshot_raced")
            with self._lock:
                self._snapshot, self._error = snapshot, None
                self._checked = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ApiError as exc:
            with self._lock:
                self._error = "github_http_" + str(exc.status or "unavailable")
        except DialogError as exc:
            with self._lock:
                self._error = exc.code
        except Exception:
            with self._lock:
                self._error = "poll_failed"

    def cancel(self):
        if self._isolated is not None: self._isolated.cancel()

    def current(self):
        with self._lock:
            if self._error or self._snapshot is None:
                raise DialogError("source_unavailable")
            return self._snapshot

    def status(self):
        with self._lock:
            return {"mode": self.mode, "task_id": TASK_ID if self.mode == "github" else "task_preview_fixture",
                "head": self._snapshot.head if self._snapshot else None, "last_checked_utc": self._checked,
                "error": self._error, "read_only": True, "delete_enabled": False,
                "bridge": "passive_sidecar_no_bridge_changes", "authority": "local_owner_read_only"}


class PanelApplication:
    def __init__(self, authority, reader, port=8789, *, gateway=False):
        if type(port) is not int or port <= 1024 or port > 65535 or port == 8787:
            raise DialogError("service_port_rejected")
        self.authority, self.reader = authority, reader
        self.host = "127.0.0.1:" + str(port)
        if type(gateway) is not bool: raise DialogError('panel_binding_rejected')
        self.gateway = None
        if gateway:
            from .panel_gateway import GatewayPanel
            self.gateway = GatewayPanel(authority, port)

    def __call__(self, env, start_response):
        if self.gateway is not None:
            return self.gateway(env,start_response)
        code, body, headers = 500, b"Service error", security_headers()
        try:
            if (env.get("HTTP_HOST") != self.host or env.get("REMOTE_ADDR") != "127.0.0.1" or
                    any(k.startswith("HTTP_X_FORWARDED_") or k == "HTTP_FORWARDED" for k in env)):
                code, body = 403, b"Forbidden"
            elif env.get("HTTP_ORIGIN") not in (None, "http://" + self.host):
                code, body = 403, b"Forbidden"
            elif not self.authority.verify(env.get("HTTP_AUTHORIZATION")):
                code, body = 401, b"Authentication required"
                headers["WWW-Authenticate"] = 'Basic realm="Git Dialog P1", charset="UTF-8"'
            elif env.get("REQUEST_METHOD") != "GET":
                code, body = 405, b"Read-only"
                headers["Allow"] = "GET"
            elif env.get("CONTENT_LENGTH", "") not in ("", "0") or env.get("HTTP_TRANSFER_ENCODING"):
                code, body = 400, b"Invalid request"
            elif env.get("PATH_INFO") in ("/api/status", "/bridge/status") and not env.get("QUERY_STRING"):
                code, body = 200, json.dumps(self.reader.status(), sort_keys=True).encode()
                headers["Content-Type"] = "application/json; charset=utf-8"
            else:
                target = env.get("PATH_INFO", "") + ("?" + env["QUERY_STRING"] if env.get("QUERY_STRING") else "")
                code, headers, body = preview_response(self.reader.current(), "GET", target,
                    host=self.host, expected_host=self.host)
        except DialogError as exc:
            code = 429 if exc.code == "authentication_throttled" else 503
            body = b"Authentication throttled" if code == 429 else b"Source unavailable; see authenticated /api/status"
            if code == 429:
                headers["Retry-After"] = "60"
        except Exception:
            code, body = 500, b"Service error"
        if code == 200 and headers.get("Content-Type") == "text/html; charset=utf-8":
            headers["Refresh"] = "15"
        headers["Content-Length"] = str(len(body))
        start_response(str(code) + " " + HTTPStatus(code).phrase, list(headers.items()))
        return [body]


def _interactive_secret(prompt):
    if not sys.stdin.isatty():
        raise DialogError("interactive_terminal_required")
    return getpass.getpass(prompt)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Git Dialog P1: isolated loopback read-only panel")
    parser.add_argument("action", choices=("init", "serve"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--mode", choices=("synthetic", "github"), default="synthetic")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    stop, poller, lock_fd = threading.Event(), None, None
    try:
        if args.action == "init":
            password = _interactive_secret("Новый пароль панели (минимум14 байт): ")
            if password != _interactive_secret("Повторите пароль: "):
                raise DialogError("password_mismatch")
            print(json.dumps(initialize(args.state_dir, password), sort_keys=True))
            return 0
        if not 30 <= args.poll_seconds <= 3600:
            raise DialogError("poll_interval_rejected")
        authority = AuthAuthority(args.state_dir)
        app = PanelApplication(authority, SnapshotReader(), args.port)  # Validate before touching credentials/network.
        lock_path = _private_dir(args.state_dir) / "server.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        _private_file(lock_path)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DialogError("service_already_running") from None
        if args.mode == "github":
            credential_path = _interactive_secret("Локальный путь к уже настроенному PAT-файлу (скрыт): ")
            token = read_configured_token(credential_path)
            reader = SnapshotReader(JsonHttp(token, timeout=5))
            del token, credential_path
            reader.refresh()
            reader.current()  # Fail startup, never silently fall back to synthetic.
            app.reader = reader
            def poll():
                while not stop.wait(args.poll_seconds):
                    reader.refresh()
            poller = threading.Thread(target=poll, name="git-dialog-read-only-poll", daemon=True)
            poller.start()
        from waitress import serve
        def terminate(*unused):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, terminate)
        logging.getLogger("waitress").setLevel(logging.CRITICAL)
        print(json.dumps({"status": "starting", "url": "http://" + app.host, "mode": args.mode,
                          "read_only": True, "old_services_changed": False}), flush=True)
        serve(app, host="127.0.0.1", port=args.port, threads=2, connection_limit=16,
              channel_timeout=10, cleanup_interval=2, max_request_header_size=8192,
              max_request_body_size=1024, expose_tracebacks=False, log_socket_errors=False,
              clear_untrusted_proxy_headers=True, ident="GitDialogP1")
    except KeyboardInterrupt:
        return 0
    except DialogError as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        return 1
    except Exception:
        print('{"error":"service_start_failed"}', file=sys.stderr)
        return 1
    finally:
        stop.set()
        if poller is not None:
            poller.join(timeout=20)
        if lock_fd is not None:
            os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
