"""Hermes Bridge — MCP-сервер для обмена Hermes<->ChatGPT (план hermes_chat_bridge_task.md).

Один Starlette-процесс, loopback по умолчанию, снаружи — Cloudflare Quick Tunnel.
- GET  /ping                      — публично: {ok, version} (без сообщений/настроек)
- POST /mcp                       — MCP 2.0 Streamable HTTP (tools + events)
- GET  /.well-known/oauth-authorization-server — OAuth metadata (SDK)
- GET  /.well-known/oauth-protected-resource/mcp — RFC 9728 resource metadata (SDK)
- GET  /authorize, POST /token, POST /register (DCR), POST /revoke — OAuth (SDK)
- GET  /login, POST /login/callback — форма входа владельца (обёртка SimpleAuth)
- events/list, events/subscribe, events/unsubscribe — кастомные MCP-методы
  поверх SDK (в SDK их нет — это документированный разрыв, реализуем сами).
- server/discover — кастомный: supportedVersions 2026-07-28, capabilities.tools
  и capabilities.events (план требует events на верхнем уровне).
- webhook-доставка: Standard Webhooks, верификация callback challenge,
  блокировка private/loopback адресов и редиректов, retry с backoff.

Секреты (пароль входа, подписки) — в SQLite, права 600, вне git.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Any
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams,
    OAuthAuthorizationServerProvider, RefreshToken, construct_redirect_uri,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import RequestParams as MCPRequestParams
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from bridge_queue import Queue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('bridge')

VERSION = '0.1.0'
BRIDGE_HOST = os.environ.get('BRIDGE_HOST', '127.0.0.1')
BRIDGE_PORT = int(os.environ.get('BRIDGE_PORT', '8765'))
STATE_DIR = os.path.expanduser('~/hermes-chat-bridge/data')
DB_PATH = os.path.join(STATE_DIR, 'bridge.db')
SCOPE = 'bridge'
# Логин владельца (env; дефолт только для локальной проверки — сменить на продакшне)
OWNER_USER = os.environ.get('BRIDGE_USER', 'owner')
OWNER_PASS = os.environ.get('BRIDGE_PASS', secrets.token_urlsafe(12))
SUBSCRIPTION_TTL_S = int(os.environ.get('BRIDGE_SUB_TTL_S', str(7 * 24 * 3600)))
MAX_WEBHOOK_BYTES = 256 * 1024


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Store:
    """SQLite-хранилище: клиенты, auth-коды, токены, подписки, состояния."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS clients(
          client_id TEXT PRIMARY KEY, info TEXT NOT NULL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS auth_codes(
          code TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT,
          redirect_uri_explicit INTEGER, expires_at REAL, scopes TEXT,
          code_challenge TEXT, resource TEXT, subject TEXT);
        CREATE TABLE IF NOT EXISTS refresh_tokens(
          token TEXT PRIMARY KEY, client_id TEXT, scopes TEXT,
          expires_at REAL, resource TEXT, subject TEXT);
        CREATE TABLE IF NOT EXISTS access_tokens(
          token TEXT PRIMARY KEY, client_id TEXT, scopes TEXT,
          expires_at REAL, resource TEXT, subject TEXT);
        CREATE TABLE IF NOT EXISTS state_map(
          state TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL);
        CREATE TABLE IF NOT EXISTS subs(
          sub_id TEXT PRIMARY KEY, owner TEXT, event TEXT, arguments TEXT,
          callback_url TEXT, secret TEXT, expires_at REAL, active INTEGER,
          created_at TEXT);
        CREATE TABLE IF NOT EXISTS deliveries(
          id INTEGER PRIMARY KEY AUTOINCREMENT, sub_id TEXT, event_id TEXT,
          payload TEXT, attempts INTEGER DEFAULT 0, next_attempt REAL,
          last_status INTEGER, done INTEGER DEFAULT 0);
        ''')
        self.conn.commit()

    def save_client(self, client: OAuthClientInformationFull):
        self.conn.execute(
            'INSERT OR REPLACE INTO clients(client_id, info, created_at) VALUES(?,?,?)',
            (client.client_id, json.dumps(client.model_dump(mode='json')), utc_iso()))
        self.conn.commit()

    def load_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self.conn.execute('SELECT info FROM clients WHERE client_id=?', (client_id,)).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(json.loads(row['info']))

    def save_code(self, c: AuthorizationCode):
        self.conn.execute(
            'INSERT OR REPLACE INTO auth_codes(code, client_id, redirect_uri,'
            ' redirect_uri_explicit, expires_at, scopes, code_challenge, resource, subject)'
            ' VALUES(?,?,?,?,?,?,?,?,?)',
            (c.code, c.client_id, str(c.redirect_uri) if c.redirect_uri else None,
             1 if c.redirect_uri_provided_explicitly else 0, c.expires_at,
             json.dumps(c.scopes), c.code_challenge, c.resource, c.subject))
        self.conn.commit()

    def load_code(self, code: str) -> AuthorizationCode | None:
        row = self.conn.execute('SELECT * FROM auth_codes WHERE code=?', (code,)).fetchone()
        if row is None:
            return None
        return AuthorizationCode(
            code=row['code'], client_id=row['client_id'],
            redirect_uri=row['redirect_uri'], redirect_uri_provided_explicitly=bool(row['redirect_uri_explicit']),
            expires_at=row['expires_at'], scopes=json.loads(row['scopes']),
            code_challenge=row['code_challenge'], resource=row['resource'], subject=row['subject'])

    def del_code(self, code: str):
        self.conn.execute('DELETE FROM auth_codes WHERE code=?', (code,)); self.conn.commit()

    def save_refresh(self, t: RefreshToken):
        self.conn.execute(
            'INSERT OR REPLACE INTO refresh_tokens(token, client_id, scopes, expires_at, resource, subject)'
            ' VALUES(?,?,?,?,?,?)',
            (t.token, t.client_id, json.dumps(t.scopes), t.expires_at, t.resource, t.subject))
        self.conn.commit()

    def load_refresh(self, token: str) -> RefreshToken | None:
        row = self.conn.execute('SELECT * FROM refresh_tokens WHERE token=?', (token,)).fetchone()
        if row is None:
            return None
        return RefreshToken(token=row['token'], client_id=row['client_id'],
                            scopes=json.loads(row['scopes']), expires_at=row['expires_at'],
                            resource=row['resource'], subject=row['subject'])

    def del_refresh(self, token: str):
        self.conn.execute('DELETE FROM refresh_tokens WHERE token=?', (token,)); self.conn.commit()

    def save_access(self, t: AccessToken):
        self.conn.execute(
            'INSERT OR REPLACE INTO access_tokens(token, client_id, scopes, expires_at, resource, subject)'
            ' VALUES(?,?,?,?,?,?)',
            (t.token, t.client_id, json.dumps(t.scopes), t.expires_at, t.resource, t.subject))
        self.conn.commit()

    def load_access(self, token: str) -> AccessToken | None:
        row = self.conn.execute('SELECT * FROM access_tokens WHERE token=?', (token,)).fetchone()
        if row is None:
            return None
        return AccessToken(token=row['token'], client_id=row['client_id'],
                           scopes=json.loads(row['scopes']), expires_at=row['expires_at'],
                           resource=row['resource'], subject=row['subject'])

    def save_state(self, state: str, data: dict, ttl: float = 600):
        self.conn.execute('INSERT OR REPLACE INTO state_map(state, data, created_at) VALUES(?,?,?)',
                          (state, json.dumps(data), time.time()))
        self.conn.execute('DELETE FROM state_map WHERE created_at < ?', (time.time() - ttl,))
        self.conn.commit()

    def load_state(self, state: str) -> dict | None:
        row = self.conn.execute('SELECT data FROM state_map WHERE state=?', (state,)).fetchone()
        return json.loads(row['data']) if row else None

    def del_state(self, state: str):
        self.conn.execute('DELETE FROM state_map WHERE state=?', (state,)); self.conn.commit()

    # --- подписки и доставки ---
    def save_sub(self, sub: dict):
        self.conn.execute(
            'INSERT OR REPLACE INTO subs(sub_id, owner, event, arguments, callback_url, secret,'
            ' expires_at, active, created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (sub['id'], sub['owner'], sub['event'], json.dumps(sub['arguments']),
             sub['callback_url'], sub['secret'], sub['expires_at'], 1 if sub['active'] else 0,
             sub.get('created_at', utc_iso())))
        self.conn.commit()

    def load_sub(self, sub_id: str) -> dict | None:
        row = self.conn.execute('SELECT * FROM subs WHERE sub_id=?', (sub_id,)).fetchone()
        if row is None:
            return None
        return {'id': row['sub_id'], 'owner': row['owner'], 'event': row['event'],
                'arguments': json.loads(row['arguments']), 'callback_url': row['callback_url'],
                'secret': row['secret'], 'expires_at': row['expires_at'], 'active': bool(row['active'])}

    def find_sub(self, owner: str, event: str, arguments: dict, callback_url: str) -> dict | None:
        rows = self.conn.execute(
            'SELECT * FROM subs WHERE owner=? AND event=? AND callback_url=? AND active=1',
            (owner, event, callback_url)).fetchall()
        for r in rows:
            if json.loads(r['arguments']) == arguments:
                return {'id': r['sub_id'], 'owner': r['owner'], 'event': r['event'],
                        'arguments': json.loads(r['arguments']), 'callback_url': r['callback_url'],
                        'secret': r['secret'], 'expires_at': r['expires_at'], 'active': True}
        return None

    def active_subscriptions(self) -> list[dict]:
        rows = self.conn.execute(
            'SELECT * FROM subs WHERE active=1 AND expires_at > ?', (time.time(),)).fetchall()
        return [{'id': r['sub_id'], 'owner': r['owner'], 'event': r['event'],
                 'arguments': json.loads(r['arguments']), 'callback_url': r['callback_url'],
                 'secret': r['secret'], 'expires_at': r['expires_at'], 'active': True} for r in rows]

    def deactivate_sub(self, sub_id: str):
        self.conn.execute('UPDATE subs SET active=0 WHERE sub_id=?', (sub_id,)); self.conn.commit()

    def enqueue_delivery(self, sub_id: str, event_id: str, payload: dict):
        self.conn.execute(
            'INSERT INTO deliveries(sub_id, event_id, payload, next_attempt) VALUES(?,?,?,?)',
            (sub_id, event_id, json.dumps(payload), time.time()))
        self.conn.commit()

    def pending_deliveries(self) -> list[dict]:
        rows = self.conn.execute(
            'SELECT * FROM deliveries WHERE done=0 AND next_attempt <= ? ORDER BY id LIMIT 20',
            (time.time(),)).fetchall()
        return [dict(r) for r in rows]

    def mark_delivery(self, d_id: int, status: int, done: bool, next_attempt: float):
        self.conn.execute(
            'UPDATE deliveries SET last_status=?, done=?, attempts=attempts+1, next_attempt=?'
            ' WHERE id=?', (status, 1 if done else 0, next_attempt, d_id))
        self.conn.commit()


# ---------------------------------------------------------------------------
# OAuth provider (контракт SDK; хранение в SQLite; криптография — на SDK)
# ---------------------------------------------------------------------------
class BridgeOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, store: Store, base_url: str):
        self.store = store
        self.base = base_url  # например https://<tunnel>/ or http://127.0.0.1:8765

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.store.load_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.save_client(client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state = params.state or secrets.token_hex(16)
        self.store.save_state(state, {
            'redirect_uri': str(params.redirect_uri),
            'code_challenge': params.code_challenge,
            'redirect_uri_provided_explicitly': params.redirect_uri_provided_explicitly,
            'client_id': client.client_id,
            'resource': params.resource,
        })
        return f'{self.base}/login?state={state}&client_id={client.client_id}'

    async def load_authorization_code(self, client, code: str) -> AuthorizationCode | None:
        c = self.store.load_code(code)
        return c if c and c.client_id == client.client_id else None

    async def exchange_authorization_code(self, client, auth_code: AuthorizationCode) -> OAuthToken:
        at = f'at_{secrets.token_urlsafe(24)}'
        rt = f'rt_{secrets.token_urlsafe(24)}'
        now = int(time.time())
        self.store.save_access(AccessToken(
            token=at, client_id=client.client_id, scopes=auth_code.scopes,
            expires_at=now + 3600, resource=auth_code.resource, subject=auth_code.subject))
        self.store.save_refresh(RefreshToken(
            token=rt, client_id=client.client_id, scopes=auth_code.scopes,
            expires_at=now + 30 * 86400, resource=auth_code.resource, subject=auth_code.subject))
        self.store.del_code(auth_code.code)
        return OAuthToken(access_token=at, token_type='Bearer', expires_in=3600,
                          scope=' '.join(auth_code.scopes), refresh_token=rt)

    async def load_refresh_token(self, client, refresh_token: str) -> RefreshToken | None:
        t = self.store.load_refresh(refresh_token)
        return t if t and t.client_id == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        now = int(time.time())
        at = f'at_{secrets.token_urlsafe(24)}'
        rt = f'rt_{secrets.token_urlsafe(24)}'
        self.store.save_access(AccessToken(token=at, client_id=client.client_id, scopes=scopes,
                                           expires_at=now + 3600, resource=refresh_token.resource,
                                           subject=refresh_token.subject))
        self.store.save_refresh(RefreshToken(token=rt, client_id=client.client_id, scopes=scopes,
                                              expires_at=now + 30 * 86400, resource=refresh_token.resource,
                                              subject=refresh_token.subject))
        self.store.del_refresh(refresh_token.token)
        return OAuthToken(access_token=at, token_type='Bearer', expires_in=3600,
                          scope=' '.join(scopes), refresh_token=rt)

    async def load_access_token(self, token: str) -> AccessToken | None:
        t = self.store.load_access(token)
        if t and t.expires_at and t.expires_at < time.time():
            return None
        return t

    async def revoke_token(self, token: str, token_type_hint: str | None = None) -> None:
        self.store.conn.execute('DELETE FROM access_tokens WHERE token=?', (token,))
        self.store.conn.execute('DELETE FROM refresh_tokens WHERE token=?', (token,))
        self.store.conn.commit()


# ---------------------------------------------------------------------------
# Login-форма владельца (обёртка SimpleAuth: form -> auth code)
# ---------------------------------------------------------------------------
def login_page(state: str, server_base: str) -> HTMLResponse:
    return HTMLResponse(f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Hermes Bridge — вход</title>
<style>body{{font-family:system-ui;max-width:420px;margin:40px auto;padding:0 16px}}
input{{width:100%;padding:8px;margin-top:6px;box-sizing:border-box}}button{{margin-top:14px;padding:8px 16px}}</style>
</head><body><h2>Hermes Bridge</h2>
<p>Вход владельца для подключения ChatGPT к очереди сообщений Hermes.
Это OAuth-авторизация доступа к мосту (не к торговым ключам).</p>
<form action="{server_base}/login/callback" method="post">
<input type="hidden" name="state" value="{state}">
<label>Пользователь</label><input name="username" required autocomplete="username">
<label>Пароль</label><input type="password" name="password" required autocomplete="current-password">
<button type="submit">Войти</button></form></body></html>''')


class BridgeApp:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.queue = Queue(DB_PATH)
        self.store = Store(DB_PATH)
        os.chmod(DB_PATH, 0o600) if os.path.exists(DB_PATH) else None
        self.provider = BridgeOAuthProvider(self.store, self.base_url)

    # --- login flow ---
    async def login_handler(self, request: Request) -> Response:
        state = request.query_params.get('state')
        if not state:
            raise HTTPException(400, 'Missing state')
        return login_page(state, self.base_url)

    async def login_callback(self, request: Request) -> Response:
        form = await request.form()
        user = str(form.get('username') or '')
        pwd = str(form.get('password') or '')
        state = str(form.get('state') or '')
        if user != OWNER_USER or pwd != OWNER_PASS:
            raise HTTPException(401, 'Invalid credentials')
        sd = self.store.load_state(state)
        if not sd:
            raise HTTPException(400, 'Invalid state')
        code = f'code_{secrets.token_urlsafe(24)}'
        self.store.save_code(AuthorizationCode(
            code=code, client_id=sd['client_id'],
            redirect_uri=sd['redirect_uri'] or None,
            redirect_uri_provided_explicitly=sd['redirect_uri_provided_explicitly'],
            expires_at=time.time() + 300, scopes=[SCOPE],
            code_challenge=sd['code_challenge'], resource=sd.get('resource'), subject=user))
        self.store.del_state(state)
        return RedirectResponse(
            url=construct_redirect_uri(sd['redirect_uri'], code=code, state=state), status_code=302)

    # --- публичный ping ---
    async def ping(self, request: Request) -> Response:
        return JSONResponse({'ok': 'ok', 'version': VERSION})

    # --- events: стандартная верификация callback и доставка (Standard Webhooks) ---
    def _nonces(self, timestamp: str, body: bytes) -> str:
        return f'{timestamp}.{body.decode()}'

    def _sign_body(self, secret: str, ts: str, body: bytes) -> str:
        """Standard Webhooks: v1,<base64(HMAC-SHA256(secret, ts+'.'+body))>."""
        key = base64.b64decode(secret.removeprefix('whsec_'))
        msg = f'{ts}.{body.decode()}'.encode()
        sig = hmac.new(key, msg, hashlib.sha256).digest()
        return 'v1,' + base64.b64encode(sig).decode()

    def _validate_callback_url(self, url: str):
        p = urlparse(url)
        if p.scheme != 'https':
            raise ValueError('callback must be HTTPS')
        host = p.hostname
        try:
            addr = ip_address(host)
            is_private = not addr.is_global
        except ValueError:
            try:
                import socket
                addrs = {ip_address(a[4][0]) for a in socket.getaddrinfo(host, None)}
                is_private = any(not a.is_global for a in addrs)
            except Exception:
                is_private = True
        if is_private:
            raise ValueError('callback must not be a private/local address')

    async def _http_post(self, url: str, body: bytes, headers: dict, timeout: float = 10.0):
        """POST без редиректов (redirect=error), HTTPS-only, блокировка private."""
        import urllib.request
        self._validate_callback_url(url)
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json', **headers})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                raise urllib.error.HTTPError(req.full_url, 3, 'redirect blocked', {}, None)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    async def verify_callback(self, sub: dict) -> bool:
        """Верификация callback (план §«Событие»): signed challenge, 2xx + echo."""
        challenge = 'ch_' + secrets.token_urlsafe(16)
        payload = {'type': 'verification', 'challenge': challenge}
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = self._sign_body(sub['secret'], ts, body)
        headers = {
            'webhook-id': f'msg_verification_{sub["id"]}',
            'webhook-timestamp': ts,
            'webhook-signature': sig,
            'X-MCP-Subscription-Id': sub['id'],
        }
        try:
            status, resp = await self._http_post(sub['callback_url'], body, headers)
        except Exception as e:
            logger.info('verify_callback net %s', type(e).__name__)
            return False
        if status < 200 or status >= 300:
            return False
        try:
            echoed = json.loads(resp.decode())
            return hmac.compare_digest(str(echoed.get('challenge', '')), challenge)
        except Exception:
            return False

    # --- события: подписки (вызываются из MCP handlers после проверки токена SDK) ---
    def _sub_id(self, owner: str, event: str, arguments: dict, callback_url: str) -> str:
        h = hashlib.sha256()
        for part in (owner, event, json.dumps(arguments, sort_keys=True), callback_url):
            h.update(part.encode())
        return 'sub_' + h.hexdigest()[:24]

    async def events_subscribe(self, ctx, params) -> dict:
        """events/subscribe: проверить аргументы, whsec, callback URL; верифицировать."""
        p = _ev_params(params)
        name = p.get('name') or p.get('event') or ''
        arguments = p.get('arguments') or {}
        delivery = p.get('delivery') or {}
        if name != 'hermes.message.created':
            return {'resultType': 'error', 'error': {'code': -32602,
                    'message': f'unknown event {name}'}}
        if arguments.get('queue') != 'test':
            return {'resultType': 'error', 'error': {'code': -32602,
                    'message': 'filter queue=test required for this stage'}}
        url = delivery.get('url', '')
        secret = delivery.get('secret', '')
        if delivery.get('mode') != 'webhook' or not url or not secret.startswith('whsec_'):
            return {'resultType': 'error', 'error': {'code': -32602,
                    'message': 'delivery mode=webhook with whsec_ secret required'}}
        try:
            b64 = secret.removeprefix('whsec_')
            raw = base64.b64decode(b64)
            if not (24 <= len(raw) <= 64):
                raise ValueError('bad secret length')
            self._validate_callback_url(url)
        except Exception as e:
            return {'resultType': 'error', 'error': {'code': -32015,
                    'message': f'CallbackEndpointError', 'data': {'reason': f'invalid_callback: {type(e).__name__}'}}}
        owner = 'owner'  # SDK проверил токен; principal резолвим из ctx (см. ниже)
        try:
            subj = getattr(ctx, 'request_state', None)
            if subj is not None:
                owner = str(getattr(subj, 'subject', None) or 'owner')
        except Exception:
            pass
        sub_id = self._sub_id(owner, name, arguments, url)
        existing = self.store.load_sub(sub_id)
        sub = {'id': sub_id, 'owner': owner, 'event': name, 'arguments': arguments,
               'callback_url': url, 'secret': secret,
               'expires_at': time.time() + SUBSCRIPTION_TTL_S, 'active': True}
        self.store.save_sub(sub)
        if existing is None:
            ok = await self.verify_callback(sub)
            if not ok:
                return {'resultType': 'error', 'error': {'code': -32015,
                        'message': 'CallbackEndpointError', 'data': {'reason': 'challenge_failed'}}}
        return {'resultType': 'complete', 'id': sub_id, 'refreshBefore': sub['expires_at'],
                'cursor': None, 'truncated': False}

    async def events_unsubscribe(self, ctx, params) -> dict:
        p = _ev_params(params)
        name = p.get('name') or p.get('event') or ''
        arguments = p.get('arguments') or {}
        delivery = p.get('delivery') or {}
        url = delivery.get('url', '')
        owner = 'owner'
        try:
            subj = getattr(ctx, 'request_state', None)
            if subj is not None:
                owner = str(getattr(subj, 'subject', None) or 'owner')
        except Exception:
            pass
        sub_id = self._sub_id(owner, name, arguments, url)
        if self.store.load_sub(sub_id):
            self.store.deactivate_sub(sub_id)
        return {'resultType': 'complete'}

    def emit_test_event(self, job_id: str, queue: str = 'test'):
        """Локальный триггер: проверочное сообщение в очереди -> событие подписчикам."""
        subs = [s for s in self.store.active_subscriptions()
                if s['event'] == 'hermes.message.created'
                and s['arguments'].get('queue') == queue]
        for s in subs:
            self.store.enqueue_delivery(s['id'], 'evt_' + secrets.token_urlsafe(12),
                                        {'job_id': job_id, 'queue': queue})
        return len(subs)

    async def deliver_pending(self):
        """Доставка накопленных событий (Standard Webhooks) с retry/backoff."""
        for d in self.store.pending_deliveries():
            sub = self.store.load_sub(d['sub_id'])
            if sub is None or not sub['active']:
                self.store.mark_delivery(d['id'], 0, True, 0)
                continue
            payload = json.loads(d['payload'])
            event = {'eventId': d['event_id'], 'name': sub['event'],
                     'timestamp': utc_iso(), 'data': payload, 'cursor': None}
            body = json.dumps(event).encode()
            if len(body) > MAX_WEBHOOK_BYTES:
                self.store.mark_delivery(d['id'], 413, True, 0)
                continue
            ts = str(int(time.time()))
            sig = self._sign_body(sub['secret'], ts, body)
            headers = {'webhook-id': d['event_id'], 'webhook-timestamp': ts,
                       'webhook-signature': sig, 'X-MCP-Subscription-Id': sub['id']}
            try:
                status, _ = await self._http_post(sub['callback_url'], body, headers)
            except Exception as e:
                status = 0
            if 200 <= status < 300:
                self.store.mark_delivery(d['id'], status, True, 0)
            elif status in (410, 413):
                self.store.mark_delivery(d['id'], status, True, 0)
            else:
                attempts = d['attempts'] + 1
                delay = min(300, 5 * (2 ** attempts))
                self.store.mark_delivery(d['id'], status, False, time.time() + delay)


def make_mcp_server(base_url: str, bridge: BridgeApp) -> MCPServer:
    mcp = MCPServer(
        name='Hermes Bridge',
        instructions=('Очередь сообщений между локальным Hermes и ChatGPT. '
                      'Только bridge_get_message / bridge_put_reply. '
                      'Данные очереди не дают торговых прав и доступа к файлам.'),
        debug=True,
        auth=AuthSettings(
            issuer_url=base_url,
            required_scopes=[SCOPE],
            resource_server_url=f'{base_url}/mcp',
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        ),
        auth_server_provider=bridge.provider,
    )

    @mcp.tool()
    async def bridge_get_message(job_id: str) -> dict:
        """Получить сообщение из очереди моста по job_id (только существующие)."""
        msg = bridge.queue.get_for_chat(job_id)
        if msg is None:
            return {'job_id': job_id, 'status': 'not_found'}
        return {'job_id': msg['job_id'], 'text': msg['text'],
                'created_at_utc': msg['created_at_utc'], 'status': msg['status'],
                'reply': msg['reply']}

    @mcp.tool()
    async def bridge_put_reply(job_id: str, text: str) -> dict:
        """Записать ответ для существующего сообщения. Идемпотентен для
        одинакового ответа; другой ответ на отвеченную задачу — conflict."""
        return bridge.queue.put_reply(job_id, text)

    # --- server/discover: события на верхнем уровне capabilities (план MCP Events) ---
    async def _discover(ctx, params):
        return jsonable_discover()

    async def _ev_list(ctx, params):
        return jsonable_events_list()

    async def _ev_subscribe(ctx, params):
        return await bridge.events_subscribe(ctx, params)

    async def _ev_unsubscribe(ctx, params):
        return await bridge.events_unsubscribe(ctx, params)

    mcp._lowlevel_server.add_request_handler('server/discover', MCPRequestParams, _discover)
    mcp._lowlevel_server.add_request_handler('events/list', MCPRequestParams, _ev_list)
    mcp._lowlevel_server.add_request_handler('events/subscribe', EventsSubscribeParams, _ev_subscribe)
    mcp._lowlevel_server.add_request_handler('events/unsubscribe', EventsSubscribeParams, _ev_unsubscribe)
    return mcp


def jsonable_discover() -> dict:
    return {
        'resultType': 'complete',
        'cacheScope': 'private',
        'ttlMs': 0,
        'supportedVersions': ['2026-07-28'],
        'capabilities': {'tools': {}, 'events': {}},
        'instructions': 'Очередь сообщений Hermes<->ChatGPT (read/write через инструменты).',
    }


def jsonable_events_list() -> dict:
    return {
        'events': [{
            'name': 'hermes.message.created',
            'description': 'Новое сообщение добавлено в очередь моста.',
            'delivery': ['webhook'],
            'inputSchema': {
                'type': 'object',
                'properties': {'queue': {'type': 'string',
                                         'description': 'Очередь: test (только проверочная)'}},
                'required': ['queue'], 'additionalProperties': False,
            },
            'payloadSchema': {
                'type': 'object',
                'properties': {'job_id': {'type': 'string'},
                               'queue': {'type': 'string'}},
                'required': ['job_id', 'queue'], 'additionalProperties': False,
            },
        }]
    }


class EventsSubscribeParams(MCPRequestParams):
    """Свободные параметры events/subscribe|unsubscribe (extra='allow')."""

    model_config = ConfigDict(extra='allow')


def _ev_params(params) -> dict:
    """Преобразовать model (или None) в плоский dict параметров."""
    if params is None:
        return {}
    if isinstance(params, BaseModel):
        return params.model_dump(exclude_none=True, exclude={'meta'})
    return dict(params) if isinstance(params, dict) else {}


# ---------------------------------------------------------------------------
def build_app(base_url: str) -> Starlette:
    bridge = BridgeApp(base_url)
    mcp = make_mcp_server(base_url, bridge)

    @mcp.custom_route('/ping', methods=['GET'])
    async def health(request: Request) -> Response:
        return JSONResponse({'ok': 'ok', 'version': VERSION})

    @mcp.custom_route('/login', methods=['GET'])
    async def login_page_route(request: Request) -> Response:
        return await bridge.login_handler(request)

    @mcp.custom_route('/login/callback', methods=['POST'])
    async def login_cb_route(request: Request) -> Response:
        return await bridge.login_callback(request)

    from urllib.parse import urlparse as _up
    from mcp.server.transport_security import TransportSecuritySettings as TSS

    def _tunnel_host():
        h = _up(base_url).hostname
        return h if h else None

    # Легитимный туннель (Cloudflare) — добавляем его Host в allowed_hosts,
    # DNS-rebinding защиту НЕ отключаем (включена по умолчанию).
    th = _tunnel_host()
    app = mcp.streamable_http_app(
        streamable_http_path='/mcp',
        host=BRIDGE_HOST,
        transport_security=TSS(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[th] if th else [],
            allowed_origins=[],
        ) if th else None,
    )
    app.state.bridge = bridge
    return app


def main():
    base = os.environ.get('BRIDGE_BASE') or f'http://{BRIDGE_HOST}:{BRIDGE_PORT}'
    app = build_app(base)
    # фоновый цикл доставки webhook-событий
    bridge = app.state.bridge if hasattr(app.state, 'bridge') else None

    async def delivery_loop():
        while True:
            try:
                if bridge is not None:
                    await bridge.deliver_pending()
            except Exception as e:
                logger.info('delivery loop error: %s', type(e).__name__)
            await asyncio.sleep(2.0)

    import uvicorn
    cfg = uvicorn.Config(app, host=BRIDGE_HOST, port=BRIDGE_PORT, log_level='info')
    server = uvicorn.Server(cfg)

    async def run():
        task = asyncio.create_task(delivery_loop())
        await server.serve()
        task.cancel()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()