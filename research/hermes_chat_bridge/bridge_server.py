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
import html
import http.client
import socket
import ssl
import threading
import fcntl
import subprocess
from pathlib import Path
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
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
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import RequestParams as MCPRequestParams, ToolAnnotations
from mcp.server.auth.middleware.auth_context import get_access_token
from discover_events_compat_poc import install_discover_events_compat
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from bridge_queue import Queue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('bridge')

VERSION = '0.2.0'
PILOT_SCHEMA_VERSION = 3
BRIDGE_HOST = os.environ.get('BRIDGE_HOST', '127.0.0.1')
BRIDGE_PORT = int(os.environ.get('BRIDGE_PORT', '8765'))
STATE_DIR = os.path.expanduser('~/hermes-chat-bridge/data')
DB_PATH = os.environ.get('BRIDGE_DB_PATH')
SCOPE = 'bridge'
# Логин владельца (env; дефолт только для локальной проверки — сменить на продакшне)
OWNER_USER = os.environ.get('BRIDGE_USER', 'owner')
OWNER_PASS = os.environ.get('BRIDGE_PASS')
SUBSCRIPTION_TTL_S = int(os.environ.get('BRIDGE_SUB_TTL_S', str(7 * 24 * 3600)))
MAX_WEBHOOK_BYTES = 256 * 1024
LOGIN_TTL_S = 600
LOGIN_MAX_ATTEMPTS = 5
LOGIN_GLOBAL_MAX_ATTEMPTS = 20
LOGIN_GLOBAL_WINDOW_S = 600
MAX_DELIVERY_ATTEMPTS = 8
MAX_DELIVERY_AGE_S = 86400
CALLBACK_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bridge-callback')
CALLBACK_SLOTS = threading.BoundedSemaphore(4)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """No proxy, no second DNS lookup; TLS verifies the original hostname."""
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        address = ip_address(self.address)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        try:
            raw.connect((str(address), self.port))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise



def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def process_build() -> dict:
    """Capture build identity once per app; never guess a revision from a report."""
    revision = 'unknown'
    try:
        root = str(Path(__file__).resolve().parent)
        git = subprocess.run(['git', '-C', root, 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=2)
        dirty = subprocess.run(['git', '-C', root, 'status', '--porcelain'], capture_output=True, text=True, timeout=2)
        candidate = git.stdout.strip()
        if git.returncode == dirty.returncode == 0 and not dirty.stdout.strip() and len(candidate) == 40:
            revision = candidate
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'version': VERSION, 'git_revision': revision, 'process_mode': 'single-process-pilot'}


def pilot_database_lock(path):
    """Explicit fresh pilot only. Lock precedes schema inspection/creation."""
    if not path:
        raise RuntimeError('BRIDGE_DB_PATH must explicitly select a new pilot database')
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts or str(p) != str(path):
        raise RuntimeError('unsafe database path: require canonical absolute path')
    if any(part.is_symlink() for part in (p, *p.parents)):
        raise RuntimeError('unsafe database path: symlinks forbidden')
    if p.exists() and not p.is_file():
        raise RuntimeError('unsafe database path: regular file required')
    p.parent.mkdir(parents=True, exist_ok=True)
    lockpath = str(p) + '.pilot.lock'
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError('only one pilot process may use this database') from None
    lock = os.fdopen(fd, 'a')
    try:
        if p.exists():
            # Read-only inspection: never mutate an old/unversioned database.
            conn = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
            try:
                version = conn.execute('PRAGMA user_version').fetchone()[0]
            finally:
                conn.close()
            if version != PILOT_SCHEMA_VERSION:
                raise RuntimeError('unversioned/unsupported database: select a NEW pilot path; legacy migration is not implemented')
        return lock
    except BaseException:
        lock.close()
        raise


class Store:
    """SQLite-хранилище: клиенты, auth-коды, токены, подписки, состояния."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        fresh = not self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone()
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
        CREATE TABLE IF NOT EXISTS login_budget(
          owner TEXT PRIMARY KEY, window_start REAL NOT NULL, attempts INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS subs(
          sub_id TEXT PRIMARY KEY, owner TEXT, event TEXT, arguments TEXT,
          callback_url TEXT, secret TEXT, expires_at REAL, active INTEGER,
          created_at TEXT);
        CREATE TABLE IF NOT EXISTS deliveries(
          id INTEGER PRIMARY KEY AUTOINCREMENT, sub_id TEXT, event_id TEXT,
          payload TEXT, attempts INTEGER DEFAULT 0, next_attempt REAL,
          last_status INTEGER, done INTEGER DEFAULT 0);
        ''')
        # Schema upgrade runs only when the new application is explicitly started.
        for table, column, definition in [
            ('subs', 'generation', 'INTEGER NOT NULL DEFAULT 0'),
            ('subs', 'pending_generation', 'INTEGER'),
            ('state_map', 'attempts', 'INTEGER NOT NULL DEFAULT 0'),
            ('state_map', 'expires_at', 'REAL'),
            ('deliveries', 'created_at', 'REAL'),
            ('deliveries', 'body', 'BLOB'),
            ('deliveries', 'terminal_reason', 'TEXT')]:
            columns = {r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}
            if column not in columns:
                self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        # Old duplicate outbox rows are explicitly terminated, not silently retried.
        self.conn.execute("UPDATE deliveries SET done=1, terminal_reason='duplicate' WHERE id NOT IN (SELECT min(id) FROM deliveries GROUP BY sub_id,event_id)")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS delivery_event_unique ON deliveries(sub_id,event_id) WHERE terminal_reason IS NULL OR terminal_reason != 'duplicate'")
        self.conn.execute('UPDATE deliveries SET created_at=? WHERE created_at IS NULL', (time.time(),))
        if fresh:
            self.conn.execute(f'PRAGMA user_version={PILOT_SCHEMA_VERSION}')
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

    def save_state(self, state: str, data: dict, ttl: float = LOGIN_TTL_S):
        now = time.time()
        self.conn.execute('INSERT INTO state_map(state,data,created_at,expires_at,attempts) VALUES(?,?,?,?,0)',
                          (state, json.dumps(data), now, now + min(ttl, LOGIN_TTL_S)))
        self.conn.execute('DELETE FROM state_map WHERE created_at < ?', (now - LOGIN_TTL_S,))
        self.conn.commit()

    def load_state(self, state: str) -> dict | None:
        row = self.conn.execute('SELECT data FROM state_map WHERE state=? AND created_at>? AND expires_at>? AND attempts<?',
                                (state, time.time()-LOGIN_TTL_S, time.time(), LOGIN_MAX_ATTEMPTS)).fetchone()
        return json.loads(row['data']) if row else None

    def reserve_login_attempt(self, owner: str) -> bool:
        """Persistent account-wide budget; fresh OAuth states cannot reset it."""
        now = time.time()
        cutoff = now - LOGIN_GLOBAL_WINDOW_S
        row = self.conn.execute(
            'INSERT INTO login_budget(owner,window_start,attempts) VALUES(?,?,1) '
            'ON CONFLICT(owner) DO UPDATE SET '
            'attempts=CASE WHEN login_budget.window_start<=? THEN 1 ELSE login_budget.attempts+1 END, '
            'window_start=CASE WHEN login_budget.window_start<=? THEN excluded.window_start ELSE login_budget.window_start END '
            'WHERE login_budget.window_start<=? OR login_budget.attempts<? RETURNING attempts',
            (owner, now, cutoff, cutoff, cutoff, LOGIN_GLOBAL_MAX_ATTEMPTS)).fetchone()
        self.conn.commit()
        return row is not None

    def attempt_state(self, state: str, valid: bool) -> dict | None:
        # One atomic statement, safe even across distinct SQLite connections.
        now = time.time()
        if valid:
            row = self.conn.execute('DELETE FROM state_map WHERE state=? AND created_at>? AND expires_at>? AND attempts<? RETURNING data',
                                    (state, now-LOGIN_TTL_S, now, LOGIN_MAX_ATTEMPTS)).fetchone()
        else:
            row = self.conn.execute('UPDATE state_map SET attempts=attempts+1 WHERE state=? AND created_at>? AND expires_at>? AND attempts<? RETURNING data',
                                    (state, now-LOGIN_TTL_S, now, LOGIN_MAX_ATTEMPTS)).fetchone()
        self.conn.commit()
        return json.loads(row['data']) if row else None

    def del_state(self, state: str):
        self.conn.execute('DELETE FROM state_map WHERE state=?', (state,)); self.conn.commit()

    # --- подписки и доставки ---
    def save_sub(self, sub: dict):
        self.conn.execute(
            'INSERT OR REPLACE INTO subs(sub_id, owner, event, arguments, callback_url, secret,'
            ' expires_at, active, created_at, generation) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (sub['id'], sub['owner'], sub['event'], json.dumps(sub['arguments']),
             sub['callback_url'], sub['secret'], sub['expires_at'], 1 if sub['active'] else 0,
             sub.get('created_at', utc_iso()), sub.get('generation', 0)))
        self.conn.commit()

    def begin_sub(self, sub: dict) -> int:
        existing = self.load_sub(sub['id'])
        if existing is None:
            self.save_sub(sub)
        row = self.conn.execute(
            'UPDATE subs SET generation=generation+1,pending_generation=generation+1 '
            'WHERE sub_id=? RETURNING generation', (sub['id'],)).fetchone()
        self.conn.commit()
        return row[0]

    def activate_sub(self, sub: dict, generation: int) -> bool:
        cur = self.conn.execute(
            'UPDATE subs SET owner=?,event=?,arguments=?,callback_url=?,secret=?,expires_at=?,active=1,pending_generation=NULL '
            'WHERE sub_id=? AND generation=? AND pending_generation=?',
            (sub['owner'], sub['event'], json.dumps(sub['arguments']), sub['callback_url'], sub['secret'], sub['expires_at'],
             sub['id'], generation, generation))
        self.conn.commit()
        return cur.rowcount == 1

    def fail_sub(self, sub_id: str, generation: int):
        self.conn.execute('UPDATE subs SET pending_generation=NULL WHERE sub_id=? AND generation=? AND pending_generation=?',
                          (sub_id, generation, generation))
        self.conn.commit()

    def load_sub(self, sub_id: str) -> dict | None:
        row = self.conn.execute('SELECT * FROM subs WHERE sub_id=?', (sub_id,)).fetchone()
        if row is None:
            return None
        return {'id': row['sub_id'], 'owner': row['owner'], 'event': row['event'],
                'arguments': json.loads(row['arguments']), 'callback_url': row['callback_url'],
                'secret': row['secret'], 'expires_at': row['expires_at'], 'active': bool(row['active']), 'generation': row['generation']}

    def find_sub(self, owner: str, event: str, arguments: dict, callback_url: str) -> dict | None:
        rows = self.conn.execute(
            'SELECT * FROM subs WHERE owner=? AND event=? AND callback_url=? AND active=1',
            (owner, event, callback_url)).fetchall()
        for r in rows:
            if json.loads(r['arguments']) == arguments:
                return {'id': r['sub_id'], 'owner': r['owner'], 'event': r['event'],
                        'arguments': json.loads(r['arguments']), 'callback_url': r['callback_url'],
                        'secret': r['secret'], 'expires_at': r['expires_at'], 'active': True, 'generation': r['generation']}
        return None

    def active_subscriptions(self) -> list[dict]:
        rows = self.conn.execute(
            'SELECT * FROM subs WHERE active=1 AND expires_at > ?', (time.time(),)).fetchall()
        return [{'id': r['sub_id'], 'owner': r['owner'], 'event': r['event'],
                 'arguments': json.loads(r['arguments']), 'callback_url': r['callback_url'],
                 'secret': r['secret'], 'expires_at': r['expires_at'], 'active': True, 'generation': r['generation']} for r in rows]

    def deactivate_sub(self, sub_id: str):
        self.conn.execute('UPDATE subs SET active=0,generation=generation+1,pending_generation=NULL WHERE sub_id=?', (sub_id,)); self.conn.commit()

    def enqueue_delivery(self, sub_id: str, event_id: str, payload: dict):
        sub = self.load_sub(sub_id)
        event = {'eventId': event_id, 'name': sub['event'], 'timestamp': utc_iso(), 'data': payload, 'cursor': None}
        body = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        cur = self.conn.execute(
            'INSERT OR IGNORE INTO deliveries(sub_id,event_id,payload,next_attempt,created_at,body) VALUES(?,?,?,?,?,?)',
            (sub_id, event_id, json.dumps(payload), time.time(), time.time(), body))
        self.conn.commit()
        return cur.rowcount

    def pending_deliveries(self) -> list[dict]:
        rows = self.conn.execute(
            'SELECT * FROM deliveries WHERE done=0 AND next_attempt <= ? ORDER BY id LIMIT 20',
            (time.time(),)).fetchall()
        return [dict(r) for r in rows]

    def mark_delivery(self, d_id: int, status: int, done: bool, next_attempt: float, reason=None):
        self.conn.execute(
            'UPDATE deliveries SET last_status=?,done=?,attempts=attempts+1,next_attempt=?,terminal_reason=? WHERE id=? AND done=0',
            (status, int(done), next_attempt, reason, d_id))
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
        state = secrets.token_urlsafe(32)
        self.store.save_state(state, {
            'redirect_uri': str(params.redirect_uri),
            'code_challenge': params.code_challenge,
            'redirect_uri_provided_explicitly': params.redirect_uri_provided_explicitly,
            'client_id': client.client_id,
            'resource': params.resource,
            'client_state': params.state,
        })
        return f'{self.base}/login?state={state}'

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
        return t if t and t.subject == OWNER_USER else None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        token_value = token.token
        principal = self.store.load_access(token_value) or self.store.load_refresh(token_value)
        if principal and principal.client_id != token.client_id:
            return
        if principal:
            # Single-owner bridge: conservatively revoke all this subject's subscriptions.
            self.store.conn.execute('UPDATE subs SET active=0,generation=generation+1,pending_generation=NULL WHERE owner=?', (principal.subject,))
            self.store.conn.execute("UPDATE deliveries SET done=1,terminal_reason='access_revoked' WHERE done=0 AND sub_id IN (SELECT sub_id FROM subs WHERE owner=?)", (principal.subject,))
            self.store.conn.execute('DELETE FROM access_tokens WHERE subject=? AND client_id=?', (principal.subject, principal.client_id))
            self.store.conn.execute('DELETE FROM refresh_tokens WHERE subject=? AND client_id=?', (principal.subject, principal.client_id))
        self.store.conn.execute('DELETE FROM access_tokens WHERE token=?', (token_value,))
        self.store.conn.execute('DELETE FROM refresh_tokens WHERE token=?', (token_value,))
        self.store.conn.commit()


# ---------------------------------------------------------------------------
# Login-форма владельца (обёртка SimpleAuth: form -> auth code)
# ---------------------------------------------------------------------------
def login_page(state: str, server_base: str) -> HTMLResponse:
    state = html.escape(state, quote=True)
    server_base = html.escape(server_base, quote=True)
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
        self.store = Store(DB_PATH)
        self.queue = Queue(DB_PATH)
        self._delivery_lock = threading.Lock()
        self.build = process_build()
        os.chmod(DB_PATH, 0o600) if os.path.exists(DB_PATH) else None
        self.provider = BridgeOAuthProvider(self.store, self.base_url)

    # --- login flow ---
    async def login_handler(self, request: Request) -> Response:
        state = request.query_params.get('state')
        if not state or self.store.load_state(state) is None:
            raise HTTPException(400, 'Invalid state')
        return login_page(state, self.base_url)

    async def login_callback(self, request: Request) -> Response:
        form = await request.form()
        user = str(form.get('username') or '')
        pwd = str(form.get('password') or '')
        state = str(form.get('state') or '')
        if self.store.load_state(state) is None:
            raise HTTPException(400, 'Invalid state')
        if not self.store.reserve_login_attempt(OWNER_USER):
            raise HTTPException(429, 'Login attempt budget exhausted', headers={'Retry-After': str(LOGIN_GLOBAL_WINDOW_S)})
        valid = hmac.compare_digest(user.encode(), OWNER_USER.encode()) and bool(OWNER_PASS) and hmac.compare_digest(pwd.encode(), OWNER_PASS.encode())
        sd = self.store.attempt_state(state, bool(valid))
        if sd is None:
            raise HTTPException(400, 'Invalid state')
        if not valid:
            raise HTTPException(401, 'Invalid credentials')
        code = f'code_{secrets.token_urlsafe(24)}'
        self.store.save_code(AuthorizationCode(
            code=code, client_id=sd['client_id'],
            redirect_uri=sd['redirect_uri'] or None,
            redirect_uri_provided_explicitly=sd['redirect_uri_provided_explicitly'],
            expires_at=time.time() + 300, scopes=[SCOPE],
            code_challenge=sd['code_challenge'], resource=sd.get('resource'), subject=user))
        self.store.del_state(state)
        return RedirectResponse(
            url=construct_redirect_uri(sd['redirect_uri'], code=code, state=sd.get('client_state')), status_code=302)

    # --- публичный ping ---
    async def ping(self, request: Request) -> Response:
        return JSONResponse({'ok': 'ok', **self.build})

    # --- events: стандартная верификация callback и доставка (Standard Webhooks) ---
    def require_owner(self) -> str:
        token = get_access_token()
        if (token is None or token.subject != OWNER_USER or SCOPE not in token.scopes
                or token.resource != self.base_url + '/mcp'
                or (token.expires_at is not None and token.expires_at <= time.time())
                or self.store.load_access(token.token) is None):
            raise PermissionError('Authenticated owner required')
        return token.subject

    def _sign_body(self, secret: str, webhook_id: str, ts: str, body: bytes) -> str:
        key = base64.b64decode(secret.removeprefix('whsec_'), validate=True)
        msg = webhook_id.encode() + b'.' + ts.encode() + b'.' + body
        return 'v1,' + base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()

    def _validate_callback_url(self, url: str):
        p = urlparse(url)
        if p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None or p.fragment:
            raise ValueError('callback must be HTTPS without credentials or fragment')
        host = p.hostname
        try:
            addresses = [ip_address(host)]
        except ValueError:
            addresses = [ip_address(a[4][0]) for a in socket.getaddrinfo(host, p.port or 443, type=socket.SOCK_STREAM)]
        if not addresses or any(not a.is_global for a in addresses):
            raise ValueError('callback must not be a private/local address')
        return host, str(addresses[0]), p.port or 443

    def _post_pinned(self, url: str, body: bytes, headers: dict, timeout: float):
        host, address, port = self._validate_callback_url(url)
        def allowed():
            # Recheck after DNS and again immediately before writing the request.
            # Verification challenges intentionally target pending subscriptions.
            if headers.get('webhook-id', '').startswith('msg_verification_'):
                return True
            sub_id = headers.get('X-MCP-Subscription-Id')
            if not sub_id:
                return True
            sub = self.store.load_sub(sub_id)
            return bool(sub and sub['active'] and sub['expires_at'] > time.time()
                        and (headers.get('X-MCP-Subscription-Generation') is None
                             or str(sub['generation']) == headers['X-MCP-Subscription-Generation']))
        if not allowed():
            return 410, b'{}'
        conn = PinnedHTTPSConnection(host, address, port, timeout)
        p = urlparse(url)
        target = (p.path or '/') + ('?' + p.query if p.query else '')
        try:
            conn.connect()  # TCP/TLS first; request() must not hide a slow connect after the fence.
            if not allowed():
                return 410, b'{}'
            conn.request('POST', target, body=body, headers={'Content-Type': 'application/json', **headers})
            response = conn.getresponse()
            # Never follow redirects. http.client does not consult environment proxies.
            data = response.read(MAX_WEBHOOK_BYTES + 1)
            if len(data) > MAX_WEBHOOK_BYTES:
                raise ValueError('callback response too large')
            return response.status, data
        finally:
            conn.close()

    async def _callback_worker(self, function, *args):
        # No unbounded executor queue. Slot remains held until worker really finishes.
        if not CALLBACK_SLOTS.acquire(blocking=False):
            raise RuntimeError('callback workers busy')
        def work():
            try:
                return function(*args)
            finally:
                CALLBACK_SLOTS.release()
        try:
            future = CALLBACK_POOL.submit(work)
        except BaseException:
            CALLBACK_SLOTS.release()
            raise
        return await asyncio.shield(asyncio.wrap_future(future))

    async def _http_post(self, url: str, body: bytes, headers: dict, timeout: float = 10.0):
        return await self._callback_worker(self._post_pinned, url, body, headers, min(timeout, 10.0))

    async def verify_callback(self, sub: dict) -> bool:
        """Верификация callback (план §«Событие»): signed challenge, 2xx + echo."""
        challenge = 'ch_' + secrets.token_urlsafe(16)
        payload = {'type': 'verification', 'challenge': challenge}
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        webhook_id = 'msg_verification_' + secrets.token_urlsafe(16)
        sig = self._sign_body(sub['secret'], webhook_id, ts, body)
        headers = {
            'webhook-id': webhook_id,
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
        owner = self.require_owner()
        p = _ev_params(params)
        name = p.get('name') or p.get('event') or ''
        arguments = p.get('arguments') or {}
        delivery = p.get('delivery') or {}
        if name != 'hermes.message.created':
            raise MCPError(code=-32602, message=f'unknown event {name}')
        if not isinstance(arguments, dict) or arguments != {'queue': 'test'}:
            raise MCPError(code=-32602, message='arguments must match queue=test schema without extra fields')
        if not isinstance(delivery, dict):
            raise MCPError(code=-32602, message='delivery must be an object')
        url = delivery.get('url', '')
        secret = delivery.get('secret', '')
        if (delivery.get('mode') != 'webhook' or not isinstance(url, str) or not url
                or not isinstance(secret, str) or not secret.startswith('whsec_')):
            raise MCPError(code=-32602, message='delivery mode=webhook with whsec_ secret required')
        try:
            b64 = secret.removeprefix('whsec_')
            raw = base64.b64decode(b64, validate=True)
            if not (24 <= len(raw) <= 64):
                raise ValueError('bad secret length')
            await self._callback_worker(self._validate_callback_url, url)
        except Exception as e:
            raise MCPError(code=-32015, message='CallbackEndpointError', data={'reason': f'invalid_callback: {type(e).__name__}'})
        sub_id = self._sub_id(owner, name, arguments, url)
        existing = self.store.load_sub(sub_id)
        ttl_ms = p.get('ttlMs', SUBSCRIPTION_TTL_S * 1000)
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, (int, float)) or not 0 < ttl_ms <= SUBSCRIPTION_TTL_S * 1000:
            raise MCPError(code=-32602, message='invalid ttlMs')
        sub = {'id': sub_id, 'owner': owner, 'event': name, 'arguments': arguments,
               'callback_url': url, 'secret': secret,
               'expires_at': time.time() + ttl_ms / 1000, 'active': False}
        verified = existing and existing['active'] and existing['expires_at'] > time.time() and existing['secret'] == secret
        generation = self.store.begin_sub(sub)
        sub['generation'] = generation
        if not verified:
            if not await self.verify_callback(sub):
                self.store.fail_sub(sub_id, generation)
                raise MCPError(code=-32015, message='CallbackEndpointError', data={'reason': 'challenge_failed'})
        self.require_owner()  # Revocation while challenge was awaiting must still win.
        # A stale success is acknowledged but cannot overwrite cancellation/newer parameters.
        self.store.activate_sub(sub, generation)
        return {'resultType': 'complete', 'id': sub_id,
                'refreshBefore': datetime.fromtimestamp(sub['expires_at'], timezone.utc).isoformat(),
                'cursor': None, 'truncated': False}

    async def events_unsubscribe(self, ctx, params) -> dict:
        owner = self.require_owner()
        p = _ev_params(params)
        name = p.get('name') or p.get('event') or ''
        arguments = p.get('arguments') or {}
        delivery = p.get('delivery') or {}
        url = delivery.get('url', '')
        sub_id = self._sub_id(owner, name, arguments, url)
        if self.store.load_sub(sub_id):
            self.store.deactivate_sub(sub_id)
        return {'resultType': 'complete'}

    def emit_test_event(self, job_id: str, queue: str = 'test'):
        """Локальный триггер: проверочное сообщение в очереди -> событие подписчикам."""
        subs = [s for s in self.store.active_subscriptions()
                if s['event'] == 'hermes.message.created'
                and s['arguments'].get('queue') == queue]
        count = 0
        for s in subs:
            count += self.store.enqueue_delivery(s['id'], 'evt_' + hashlib.sha256((queue + ':' + job_id).encode()).hexdigest()[:32],
                                        {'job_id': job_id, 'queue': queue})
        return count

    async def deliver_pending(self):
        # Skip, do not await a held lock: unsubscribe/revoke and overlapping calls stay prompt.
        if not self._delivery_lock.acquire(blocking=False):
            return
        try:
            await self._deliver_pending()
        finally:
            self._delivery_lock.release()

    async def _deliver_pending(self):
        """Доставка накопленных событий (Standard Webhooks) с retry/backoff."""
        for d in self.store.pending_deliveries():
            sub = self.store.load_sub(d['sub_id'])
            if sub is None or not sub['active'] or sub['expires_at'] <= time.time():
                self.store.mark_delivery(d['id'], 0, True, 0, 'subscription_inactive_or_expired')
                continue
            if d['attempts'] >= MAX_DELIVERY_ATTEMPTS or time.time() - d['created_at'] >= MAX_DELIVERY_AGE_S:
                self.store.mark_delivery(d['id'], 0, True, 0, 'retry_budget_exhausted')
                continue
            body = d['body']
            if body is None:
                # Legacy outbox: freeze once on migration/first use; then reuse bytes.
                event = {'eventId': d['event_id'], 'name': sub['event'], 'timestamp': datetime.fromtimestamp(d['created_at'], timezone.utc).isoformat(), 'data': json.loads(d['payload']), 'cursor': None}
                body = json.dumps(event).encode()
                self.store.conn.execute('UPDATE deliveries SET body=? WHERE id=? AND body IS NULL', (body, d['id']))
                self.store.conn.commit()
            if len(body) > MAX_WEBHOOK_BYTES:
                self.store.mark_delivery(d['id'], 413, True, 0, 'payload_too_large')
                continue
            ts = str(int(time.time()))
            sig = self._sign_body(sub['secret'], d['event_id'], ts, body)
            headers = {'webhook-id': d['event_id'], 'webhook-timestamp': ts,
                       'webhook-signature': sig, 'X-MCP-Subscription-Id': sub['id'],
                       'X-MCP-Subscription-Generation': str(sub['generation'])}
            try:
                status, _ = await self._http_post(sub['callback_url'], body, headers)
            except Exception as e:
                status = 0
            if 200 <= status < 300:
                self.store.mark_delivery(d['id'], status, True, 0, 'delivered')
            elif status in (410, 413) or (300 <= status < 500 and status not in (408, 429)):
                self.store.mark_delivery(d['id'], status, True, 0, 'terminal_http_status')
            else:
                attempts = d['attempts'] + 1
                terminal = attempts >= MAX_DELIVERY_ATTEMPTS or time.time()-d['created_at'] >= MAX_DELIVERY_AGE_S
                delay = min(300, 5 * (2 ** min(attempts, 8)))
                self.store.mark_delivery(d['id'], status, terminal, time.time() + delay, 'retry_budget_exhausted' if terminal else None)


def make_mcp_server(base_url: str, bridge: BridgeApp) -> MCPServer:
    mcp = MCPServer(
        name='Hermes Bridge',
        version=bridge.build['version'],
        instructions=('Очередь сообщений между локальным Hermes и ChatGPT. '
                      'Только bridge_get_message / bridge_put_reply. '
                      'Данные очереди не дают торговых прав и доступа к файлам.'),
        debug=False,
        auth=AuthSettings(
            issuer_url=base_url,
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],
            resource_server_url=f'{base_url}/mcp',
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        ),
        auth_server_provider=bridge.provider,
    )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False), meta={'securitySchemes': [{'type': 'oauth2', 'scopes': [SCOPE]}]})
    async def bridge_get_message(job_id: str) -> dict:
        """Получить сообщение из очереди моста по job_id (только существующие)."""
        bridge.require_owner()
        msg = bridge.queue.get_for_chat(job_id)
        if msg is None:
            return {'job_id': job_id, 'status': 'not_found'}
        return {'job_id': msg['job_id'], 'text': msg['text'],
                'created_at_utc': msg['created_at_utc'], 'status': msg['status'],
                'reply': msg['reply']}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False), meta={'securitySchemes': [{'type': 'oauth2', 'scopes': [SCOPE]}]})
    async def bridge_put_reply(job_id: str, text: str) -> dict:
        """Записать ответ для существующего сообщения. Идемпотентен для
        одинакового ответа; другой ответ на отвеченную задачу — conflict."""
        bridge.require_owner()
        return bridge.queue.put_reply(job_id, text)

    # --- server/discover: события на верхнем уровне capabilities (план MCP Events) ---
    async def _discover(ctx, params):
        return jsonable_discover()

    async def _ev_list(ctx, params):
        bridge.require_owner()
        return jsonable_events_list()

    async def _ev_subscribe(ctx, params):
        return await bridge.events_subscribe(ctx, params)

    async def _ev_unsubscribe(ctx, params):
        return await bridge.events_unsubscribe(ctx, params)

    mcp._lowlevel_server.add_request_handler('server/discover', MCPRequestParams, _discover)
    mcp._lowlevel_server.add_request_handler('events/list', MCPRequestParams, _ev_list)
    mcp._lowlevel_server.add_request_handler('events/subscribe', EventsSubscribeParams, _ev_subscribe)
    mcp._lowlevel_server.add_request_handler('events/unsubscribe', EventsSubscribeParams, _ev_unsubscribe)
    install_discover_events_compat(mcp)
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
    if not OWNER_PASS:
        raise RuntimeError('BRIDGE_PASS is required')
    pilot_lock = pilot_database_lock(DB_PATH)
    try:
        bridge = BridgeApp(base_url)
    except BaseException:
        pilot_lock.close()
        raise
    mcp = make_mcp_server(base_url, bridge)

    @mcp.custom_route('/ping', methods=['GET'])
    async def health(request: Request) -> Response:
        return JSONResponse({'ok': 'ok', **bridge.build})

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
            allowed_hosts=[_up(base_url).netloc] if th else [],
            allowed_origins=[],
        ) if th else None,
    )
    app.state.bridge = bridge
    app.state.pilot_lock = pilot_lock
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as state:
                yield state
        finally:
            pilot_lock.close()
    app.router.lifespan_context = lifespan
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