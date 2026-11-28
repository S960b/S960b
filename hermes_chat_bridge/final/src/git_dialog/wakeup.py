"""Opt-in WSGI receiver for existing Hermes event envelopes; no listener.

Only schedules a fixed SnapshotReader GET refresh. No bridge calls, claims,
Git writes, execution, credentials discovery or subscription changes. A trusted
host must supply a dedicated 32-byte HMAC key and drive drain(); not deployed
by the P1 CLI. Replay state is bounded/in-memory, NOT durable exactly-once.
"""
from datetime import datetime
import hashlib
import hmac
from http import HTTPStatus
import json
import re
import threading
import time

from . import DialogError
from .github import decode_json
from .panel import security_headers
from .preflight import TASK_ID
from .service import SnapshotReader

MAX_BODY = 4096


def _stamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise ValueError()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class WakeupGate:
    def __init__(self, key):
        if type(key) is not bytes or len(key) != 32:
            raise DialogError("wakeup_key_rejected")
        self._key = key
        self._seen = {}
        self._last = None
        self._pending = False
        self._lock = threading.Lock()
        self._draining = threading.Lock()

    def accept(self, raw, signature, *, now=None, monotonic=None):
        if (type(raw) is not bytes or not 1 <= len(raw) <= MAX_BODY or
                not isinstance(signature, str) or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature)):
            return 401, "denied"
        expected = "sha256=" + hmac.new(self._key, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return 401, "denied"
        now = time.time() if now is None else now
        monotonic = time.monotonic() if monotonic is None else monotonic
        try:
            obj = decode_json(raw.decode("utf-8"))
            if type(obj) is not dict or set(obj) != {"name", "eventId", "timestamp", "data"}:
                raise ValueError()
            data = obj["data"]
            if (obj["name"] != "hermes.message.created" or
                    not isinstance(obj["eventId"], str) or not re.fullmatch(r"evt_[0-9a-f]{32}", obj["eventId"]) or
                    type(data) is not dict or set(data) != {"created_at_utc", "direction", "job_id", "queue", "status", "task_id"} or
                    not isinstance(data["job_id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}\.job", data["job_id"]) or
                    any(type(data[k]) is not str or len(data[k]) > 128 for k in ("direction", "queue", "status", "task_id"))):
                raise ValueError()
            stamp, created = _stamp(obj["timestamp"]), _stamp(data["created_at_utc"])
            # Producer publication can occur long after job creation. Freshness
            # applies to this authenticated event, not the original job's age.
            if not -30 <= now - stamp <= 120 or created > stamp + 5:
                raise ValueError()
        except (DialogError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return 400, "invalid_event"
        if (data["task_id"] != TASK_ID or data["direction"] != "to_chatgpt" or
                data["queue"] != "test" or data["status"] != "pending"):
            return 200, "ignored"
        key = hashlib.sha256(obj["eventId"].encode()).digest()
        with self._lock:
            self._seen = {k: expiry for k, expiry in self._seen.items() if expiry > monotonic}
            if key in self._seen:
                return 200, "duplicate"
            if len(self._seen) >= 256 or (self._last is not None and monotonic - self._last < 30):
                return 429, "limited"
            self._seen[key] = monotonic + 180
            self._last, self._pending = monotonic, True
            return 202, "scheduled"

    def drain(self, reader):
        """Trusted host call, never a callback selected by event data.

        Gate requests coalesce. Refresh errors retain SnapshotReader's fail-closed
        semantics. No auto-retry; ordinary periodic GET polling remains fallback.
        """
        if type(reader) is not SnapshotReader:
            raise DialogError("wakeup_reader_rejected")
        with self._draining:
            with self._lock:
                if not self._pending:
                    return False
                self._pending = False
            reader.refresh()
            return True


class WakeupApplication:
    """Composition with P1 PanelApplication. Disabled unless gate supplied.

    Dedicated HMAC is NOT the owner password/PAT and gives no panel-read role.
    No CORS; no body text becomes authority. HTTP parser/framing and timeouts
    remain the reviewed host's responsibility (WSGI cannot inspect duplicates).
    """
    def __init__(self, panel, gate=None):
        if gate is not None and type(gate) is not WakeupGate:
            raise DialogError("wakeup_gate_rejected")
        self.panel, self.gate = panel, gate

    def __call__(self, env, start_response):
        if env.get("PATH_INFO") != "/bridge/wakeup":
            return self.panel(env, start_response)
        code, status = 404, "disabled"
        if self.gate is not None:
            if (env.get("HTTP_HOST") != self.panel.host or env.get("REMOTE_ADDR") != "127.0.0.1" or
                    env.get("HTTP_ORIGIN") is not None or env.get("QUERY_STRING") or
                    any(k.startswith("HTTP_X_FORWARDED_") or k == "HTTP_FORWARDED" for k in env)):
                code, status = 403, "denied"
            elif env.get("REQUEST_METHOD") != "POST":
                code, status = 405, "post_only"
            elif (env.get("CONTENT_TYPE") != "application/json" or env.get("HTTP_TRANSFER_ENCODING") or
                    env.get("HTTP_EXPECT") or not re.fullmatch(r"[1-9][0-9]{0,3}", env.get("CONTENT_LENGTH", "")) or
                    int(env["CONTENT_LENGTH"]) > MAX_BODY):
                code, status = 400, "invalid_request"
            else:
                try:
                    size = int(env["CONTENT_LENGTH"])
                    raw = env["wsgi.input"].read(size)
                    if type(raw) is not bytes or len(raw) != size:
                        code, status = 400, "invalid_request"
                    else:
                        code, status = self.gate.accept(raw, env.get("HTTP_X_HERMES_SIGNATURE"))
                except Exception:
                    code, status = 400, "invalid_request"
        headers = security_headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        if code == 405:
            headers["Allow"] = "POST"
        if code == 429:
            headers["Retry-After"] = "30"
        body = json.dumps({"status": status, "read_only": True}, sort_keys=True).encode()
        headers["Content-Length"] = str(len(body))
        start_response(str(code) + " " + HTTPStatus(code).phrase, list(headers.items()))
        return [body]
