"""Offline RPC boundary; no listener, login endpoint, credentials or deployment.

Opaque grants are issued ONLY by a trusted host after real authentication. A
grant is not evidence of human presence. Role/actor/principal never come from
RPC JSON. No proxy headers, CORS wildcard, public bootstrap or delete operation.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import DialogError
from .gateway import panel_view
from .github import decode_json


def _now(now):
    now = int(time.time()) if now is None else now
    if type(now) is not int or now < 0:
        raise DialogError("invalid_access_time")
    return now


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
        raise DialogError("access_denied")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class AccessAuthority:
    """Trusted in-process issuer; NOT a user authentication/login service.

    Lost on restart => all grants invalid. Secrets only returned once; no files,
    env discovery, token logging or API for changing roles using request data.
    """
    def __init__(self, bindings=None):
        bindings = {} if bindings is None else bindings
        if not isinstance(bindings, dict):
            raise DialogError("invalid_access_policy")
        self._bindings = {}
        for principal, policy in bindings.items():
            if (not isinstance(principal, str) or not 1 <= len(principal) <= 128 or
                    not isinstance(policy, dict) or set(policy) != {"role", "actor"} or
                    policy["role"] not in ("owner", "agent") or
                    (policy["role"] == "owner" and policy["actor"] is not None) or
                    (policy["role"] == "agent" and policy["actor"] not in ("chatgpt", "mt"))):
                raise DialogError("invalid_access_policy")
            self._bindings[principal] = dict(policy)
        self._grants = {}
        self._lock = threading.Lock()

    def issue(self, principal, *, now=None, ttl=300):
        now = _now(now)
        if not isinstance(principal, str) or principal not in self._bindings:
            raise DialogError("access_denied")
        if type(ttl) is not int or not 1 <= ttl <= 900:
            raise DialogError("invalid_access_ttl")
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            self._grants = {k: v for k, v in self._grants.items() if now < v["expires"]}
            if len(self._grants) >= 128:
                raise DialogError("access_capacity")
            self._grants[_hash(session)] = {**self._bindings[principal], "principal": principal,
                "issued": now, "expires": now + ttl, "csrf_hash": _hash(csrf)}
        return {"session": session, "csrf": csrf, "expires_at": now + ttl}

    def resolve(self, session, csrf, *, now=None):
        now = _now(now)
        key, csrf_hash = _hash(session), _hash(csrf)
        with self._lock:
            row = self._grants.get(key)
            if (row is None or not row["issued"] <= now < row["expires"] or
                    not hmac.compare_digest(row["csrf_hash"], csrf_hash)):
                raise DialogError("access_denied")
            return {k: row[k] for k in ("role", "actor", "principal")}

    def revoke(self, session):
        with self._lock:
            self._grants.pop(_hash(session), None)


class Dispatcher:
    """One fixed RPC route; mutations disabled by default.

    A future HTTP adapter must reject malformed framing before calling this,
    redact Authorization/CSRF/confirmation values, set no-store/security headers,
    and never expose AccessAuthority.issue() as an unauthenticated route.
    """
    def __init__(self, gateway, confirmation, authority, *, origin, mutations=False):
        try:
            parsed = urlsplit(origin)
            if (not isinstance(origin, str) or parsed.username is not None or parsed.password is not None or
                    parsed.path or parsed.query or parsed.fragment or not parsed.hostname or
                    parsed.scheme not in ("http", "https") or
                    (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1")) or
                    origin != parsed.scheme + "://" + parsed.netloc or parsed.port == 0):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise DialogError("invalid_ingress_origin") from None
        if type(mutations) is not bool or (confirmation is not None and gateway.task_id != confirmation.task_id):
            raise DialogError("invalid_ingress_binding")
        self.gateway, self.confirmation, self.authority = gateway, confirmation, authority
        self.origin, self.host, self.mutations = origin, parsed.netloc, mutations

    def dispatch(self, method, path, headers, body, *, now=None):
        if method != "POST" or path != "/api/task":
            raise DialogError("rpc_route_denied")
        if not isinstance(headers, (list, tuple)) or len(headers) > 32:
            raise DialogError("rpc_headers_denied")
        checked = {}
        allowed = {"host", "origin", "authorization", "x-csrf-token", "content-type", "content-length"}
        for pair in headers:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise DialogError("rpc_headers_denied")
            key, value = pair
            if (not isinstance(key, str) or not isinstance(value, str) or len(value) > 256 or
                    any(ord(c) < 32 or ord(c) == 127 for c in key + value)):
                raise DialogError("rpc_headers_denied")
            key = key.lower()
            if key not in allowed or key in checked:
                raise DialogError("rpc_headers_denied")
            checked[key] = value
        if checked.get("host") != self.host or checked.get("origin") != self.origin:
            raise DialogError("rpc_origin_denied")
        auth = checked.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise DialogError("access_denied")
        identity = self.authority.resolve(auth[7:], checked.get("x-csrf-token"), now=now)
        if (checked.get("content-type") != "application/json" or not isinstance(body, bytes) or
                len(body) > 8192 or checked.get("content-length", str(len(body))) != str(len(body))):
            raise DialogError("rpc_body_denied")
        try:
            obj = decode_json(body.decode("utf-8"))
        except (UnicodeError, RecursionError):
            raise DialogError("rpc_body_denied") from None
        if not isinstance(obj, dict) or not isinstance(obj.get("op"), str):
            raise DialogError("rpc_schema_denied")
        op = obj["op"]
        fields = {"view": {"op", "offset", "limit"},
                  "append": {"op", "head", "message_id", "kind", "body"},
                  "request_close": {"op", "head"}, "cancel_close": {"op", "nonce"},
                  "confirm_close": {"op", "nonce"}}
        if op not in fields or set(obj) != fields[op]:
            raise DialogError("rpc_schema_denied")
        if op == "view":
            return panel_view(self.gateway.load(self.gateway.task_id), offset=obj["offset"], limit=obj["limit"])
        if not self.mutations:
            raise DialogError("rpc_mutations_disabled")
        if op == "append":
            if identity["role"] != "agent":
                raise DialogError("rpc_role_denied")
            receipt = self.gateway.port(identity["actor"]).append(self.gateway.task_id, obj["head"],
                message_id=obj["message_id"], actor=identity["actor"], kind=obj["kind"], body=obj["body"])
            return {"message_id": receipt.message.message_id, "commit": receipt.commit_sha, "created": receipt.created}
        if self.confirmation is None:
            raise DialogError("rpc_lifecycle_disabled")
        if identity["role"] != "owner":
            raise DialogError("rpc_role_denied")
        principal = identity["principal"]
        if op == "request_close":
            return self.confirmation.request(principal, obj["head"], now=now)
        if op == "cancel_close":
            self.confirmation.cancel(principal, obj["nonce"], now=now)
            return {"status": "pending_intent_cancelled"}
        return self.confirmation.confirm(principal, obj["nonce"], now=now)
