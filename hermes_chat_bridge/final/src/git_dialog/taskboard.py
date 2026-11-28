"""P32 Git taskboard: localhost-only authenticated UI backed by P31 Unix authority.

Does NOT read pilot.db, PATs or repository files. Deletion uses the trusted authority's two-stage lifecycle ticket;
no unguarded Git ref changes from the HTTP process.
"""
import argparse
import getpass
import json
from http import HTTPStatus
from pathlib import Path
import re
import secrets
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from urllib.parse import parse_qs

from . import DialogError, validate_task_id
from .service import AuthAuthority, initialize
from .unix_authority import SOCKETS, UnixClient

HOST, PORT = "127.0.0.1", 8787
STATE = Path("/home/kali/agent-dialog-beta/taskboard-auth")
STATIC = Path(__file__).parent
ASSETS = {"/": ("text/html; charset=utf-8", "taskboard.html"),
          "/taskboard.js": ("text/javascript; charset=utf-8", "taskboard.js"),
          "/taskboard.css": ("text/css; charset=utf-8", "taskboard.css")}


def _int(value, lo, hi):
    if type(value) is not str or not re.fullmatch(r"0|[1-9][0-9]{0,5}", value):
        raise DialogError("taskboard_query_rejected")
    n = int(value)
    if n < lo or n > hi:
        raise DialogError("taskboard_query_rejected")
    return n


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")



MT_STATE = Path("/home/kali/.local/state/mt-git-inbox/state.json")


def mt_delivery_snapshot(task_ids, path=None):
    """Sanitized read-only MT inbox state. 'inflight' is NOT a receipt."""
    import os
    import stat
    source = MT_STATE if path is None else Path(path)
    task_ids = [task for task in task_ids if type(task) is str]
    unavailable = {task: {"source": "unavailable", "seen_seq": None,
                          "inflight_seq": None} for task in task_ids}
    fd = None
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_nlink != 1 or st.st_mode & 0o022 or st.st_size > 1048576):
            return unavailable
        raw = os.read(fd, 1048577)
        if len(raw) > 1048576:
            return unavailable
        data = json.loads(raw.decode("utf-8"))
        if (type(data) is not dict or type(data.get("seen")) is not dict or
                type(data.get("inflight")) is not dict):
            return unavailable
    except (OSError, ValueError, UnicodeError):
        return unavailable
    finally:
        if fd is not None:
            os.close(fd)

    def checked_seq(value):
        return value if type(value) is int and 0 <= value <= 1000000000 else None

    result = {}
    for task in task_ids:
        pending = data["inflight"].get(task)
        result[task] = {
            "source": "ok",
            "seen_seq": checked_seq(data["seen"].get(task)),
            "inflight_seq": checked_seq(pending.get("seq")) if type(pending) is dict else None,
        }
    return result


class Taskboard:
    def __init__(self, authority, client=None):
        self.authority = authority
        self.client = UnixClient(SOCKETS["mt"]) if client is None else client
        self.csrf = secrets.token_urlsafe(32)
        # Accept both spellings of the loopback address (127.0.0.1 and
        # localhost) for Host/Origin. Anticipated DNS-rebinding is still
        # blocked because the whitelist is explicit and loopback-only; auth is
        # the next gate and is applied in both cases.
        self.hosts = {HOST + ":" + str(PORT), "localhost:" + str(PORT)}
        self.origins = {"http://" + host for host in self.hosts}
        self.host = HOST + ":" + str(PORT)
        self.origin = "http://" + self.host

    def __call__(self, env, start):
        headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'"),
        }
        code, content_type, body = 403, "text/plain; charset=utf-8", b"Forbidden"
        try:
            method, path = env.get("REQUEST_METHOD"), env.get("PATH_INFO")
            if (env.get("HTTP_HOST") not in self.hosts or
                    env.get("REMOTE_ADDR") != HOST or
                    any(k == "HTTP_FORWARDED" or k.startswith("HTTP_X_FORWARDED_")
                        for k in env) or
                    env.get("HTTP_ORIGIN") not in (None, *self.origins)):
                raise DialogError("taskboard_boundary_rejected")
            if not self.authority.verify(env.get("HTTP_AUTHORIZATION")):
                code, body = 401, b"Authentication required"
                headers["WWW-Authenticate"] = 'Basic realm="P32 Git Taskboard", charset="UTF-8"'
            elif method == "GET" and path in ASSETS and not env.get("QUERY_STRING"):
                content_type, filename = ASSETS[path]
                body = (STATIC / filename).read_bytes()
                code = 200
            elif method == "GET" and path in ("/api/dialogs", "/api/history"):
                params = parse_qs(env.get("QUERY_STRING", ""), strict_parsing=True)
                if len(params) > 3 or any(len(v) != 1 for v in params.values()):
                    raise DialogError("taskboard_query_rejected")
                obj = {k: v[0] for k, v in params.items()}
                if path == "/api/dialogs":
                    if set(obj) - {"offset", "limit"}:
                        raise DialogError("taskboard_query_rejected")
                    offset = _int(obj.get("offset", "0"), 0, 100000)
                    limit = _int(obj.get("limit", "50"), 1, 50)
                    response = self.client.call({"op": "list_dialogs", "offset": offset, "limit": limit})
                    response["csrf"] = self.csrf
                    # Read the local MT receipt once per page; never copy raw sessions.
                    response["mt_delivery"] = mt_delivery_snapshot(
                        [entry["task_id"] for entry in response["dialogs"]])
                    from .api_budget import shared_status
                    response["github_api"] = shared_status()
                else:
                    if set(obj) - {"task_id", "offset", "limit"}:
                        raise DialogError("taskboard_query_rejected")
                    task = obj.get("task_id")
                    validate_task_id(task)
                    offset = _int(obj.get("offset", "0"), 0, 100000)
                    limit = _int(obj.get("limit", "20"), 1, 20)
                    response = self.client.call({"op": "view", "task_id": task, "offset": offset,
                                                 "limit": limit, "order": "newest"})
                code, content_type, body = 200, "application/json; charset=utf-8", _json(response)
            elif method == "POST" and path in ("/api/delete_prepare", "/api/delete_confirm", "/api/return_turn"):
                if (env.get("QUERY_STRING") or env.get("HTTP_ORIGIN") not in self.origins or
                        env.get("CONTENT_TYPE") != "application/json" or
                        env.get("HTTP_X_CSRF_TOKEN") != self.csrf or
                        not re.fullmatch(r"[1-9][0-9]{0,3}", env.get("CONTENT_LENGTH", ""))):
                    raise DialogError("taskboard_post_rejected")
                size = int(env["CONTENT_LENGTH"])
                if size > 2048:
                    raise DialogError("taskboard_post_rejected")
                raw = env["wsgi.input"].read(size)
                if len(raw) != size:
                    raise DialogError("taskboard_post_rejected")
                obj = json.loads(raw.decode("utf-8"))
                required = ({"task_id", "head"} if path == "/api/delete_prepare"
                            else ({"task_id", "head", "last_message_id"} if path == "/api/return_turn"
                            else {"task_id", "head", "confirmation"}))
                if type(obj) is not dict or set(obj) != required:
                    raise DialogError("taskboard_post_rejected")
                validate_task_id(obj["task_id"])
                if type(obj["head"]) is not str or not re.fullmatch("[0-9a-f]{40}", obj["head"]):
                    raise DialogError("taskboard_post_rejected")
                if path == "/api/delete_confirm" and (
                        type(obj["confirmation"]) is not str or
                        not 20 <= len(obj["confirmation"]) <= 128):
                    raise DialogError("taskboard_post_rejected")
                if path == "/api/return_turn":
                    from . import validate_message_id
                    validate_message_id(obj["last_message_id"])
                    # Existing browser Basic Auth; no additional prompt.
                    response = self.client.call({
                        "op": "return_turn", **obj,
                        "authorization": env["HTTP_AUTHORIZATION"]})
                else:
                    op = "delete_prepare" if path == "/api/delete_prepare" else "delete_confirm"
                    response = self.client.call({"op": op, **obj})
                code, content_type, body = 200, "application/json; charset=utf-8", _json(response)
            else:
                code, body = 404, b"Not found"
        except DialogError:
            code, content_type, body = 409, "application/json; charset=utf-8", _json(
                {"ok": False, "error": "request_rejected_or_unconfirmed"})
        except Exception:
            code, content_type, body = 503, "application/json; charset=utf-8", _json(
                {"ok": False, "error": "authority_unavailable"})
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        start(str(code) + " " + HTTPStatus(code).phrase, list(headers.items()))
        return [body]


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = 16


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass  # URLs can contain task_ids; do not log authentication details.


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("init", "serve"))
    args = p.parse_args(argv)
    if args.action == "init":
        one = getpass.getpass("New Git taskboard password (14+ characters): ")
        two = getpass.getpass("Repeat password: ")
        if one != two:
            raise DialogError("password_mismatch")
        initialize(STATE, one)
        print("P32_AUTH_INITIALIZED")
        return 0
    app = Taskboard(AuthAuthority(STATE))
    server = make_server(HOST, PORT, app, server_class=ThreadedServer, handler_class=QuietHandler)
    try:
        print("P32_TASKBOARD_LISTENING 127.0.0.1:8787", flush=True)
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
