#!/usr/bin/env python3
"""Полный E2E-тест моста: OAuth flow -> session-less MCP методы -> инструменты.

Без реальной биржи/ключей: только локальный сервер моста.
"""
import base64
import hashlib
import http.cookiejar
import json
import re
import secrets
import sys
import os
import html as html_lib
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('BRIDGE_E2E_BASE', '').rstrip('/')
RES = BASE + '/mcp'
META = {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
        'io.modelcontextprotocol/clientCapabilities': {}}

passed = []
failed = []


def check(name, cond, detail=''):
    (passed if cond else failed).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (f' | {detail}' if detail else ''))


def main():
    password = os.environ.get('BRIDGE_E2E_PASS')
    parsed = urllib.parse.urlparse(BASE)
    if (not password or parsed.scheme != 'http'
            or parsed.hostname not in ('127.0.0.1', '::1', 'localhost')):
        print('Set explicit loopback BRIDGE_E2E_BASE and synthetic BRIDGE_E2E_PASS; never target the production database.', file=sys.stderr)
        return 2
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def post_json(url, data):
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json',
                                              'User-Agent': 'Mozilla/5.0'})
        return op.open(req).read()

    def mcp(method, params=None, token=None, headers_extra=None):
        p = dict(params or {})
        p['_meta'] = META
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': p}).encode()
        h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
             'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': method, 'User-Agent': 'Mozilla/5.0'}
        if 'name' in p:
            h['Mcp-Name'] = str(p['name'])
        if token:
            h['Authorization'] = 'Bearer ' + token
        if headers_extra:
            h.update(headers_extra)
        req = urllib.request.Request(BASE + '/mcp', data=body, headers=h)
        try:
            return op.open(req).read().decode(), None
        except urllib.error.HTTPError as e:
            return None, f'HTTP {e.code}: {e.read().decode()[:200]}'

    # 0. публичный ping
    st, _, body = op.open(urllib.request.Request(BASE + '/ping', headers={'User-Agent': 'Mozilla/5.0'})).status, None, None
    ping = json.loads(op.open(urllib.request.Request(BASE + '/ping', headers={'User-Agent': 'Mozilla/5.0'})).read())
    check('public /ping', ping.get('ok') == 'ok' and 'version' in ping, str(ping))

    # 0b. /mcp без токена -> 401
    _, err = mcp('tools/list')
    check('mcp без токена -> 401', err is not None and '401' in err, str(err))

    # 1. DCR
    client_info = {'client_name': 'chatgpt-test',
                   'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'],
                   'grant_types': ['authorization_code', 'refresh_token'],
                   'token_endpoint_auth_method': 'client_secret_post',
                   'response_types': ['code'], 'scope': 'bridge'}
    reg = json.loads(post_json(BASE + '/register', client_info))
    cid, cs = reg['client_id'], reg.get('client_secret')
    check('DCR register', bool(cid) and bool(cs), f'client_id={cid[:8]}...')

    # 2. OAuth: authorize -> login -> code -> token
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = 'st_' + secrets.token_hex(8)
    au = (BASE + '/authorize?response_type=code&client_id=' + urllib.parse.quote(cid)
          + '&redirect_uri=' + urllib.parse.quote('https://chatgpt.com/connector_platform_oauth_redirect')
          + '&scope=bridge&state=' + state
          + '&code_challenge=' + urllib.parse.quote(challenge)
          + '&code_challenge_method=S256&resource=' + urllib.parse.quote(RES))
    html = op.open(urllib.request.Request(au, headers={'User-Agent': 'Mozilla/5.0'})).read().decode()
    check('authorize -> login form', '<form' in html)

    state_match = re.search(r'name="state" value="([^"]+)"', html)
    if not state_match:
        print('Missing internal login state', file=sys.stderr)
        return 1
    internal_state = html_lib.unescape(state_match.group(1))
    check('OAuth state distinct from internal login state', internal_state != state)
    data = urllib.parse.urlencode({'username': os.environ.get('BRIDGE_E2E_USER', 'owner'), 'password': password, 'state': internal_state}).encode()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    op2 = urllib.request.build_opener(NoRedirect)
    code = None
    try:
        op2.open(urllib.request.Request(BASE + '/login/callback', data=data,
                                        headers={'Content-Type': 'application/x-www-form-urlencoded',
                                                 'User-Agent': 'Mozilla/5.0'}))
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location', '')
        m = re.search(r'[?&]code=([^&]+)', loc)
        code = m.group(1) if m else None
    check('login callback -> code', code is not None)

    tok_data = {'grant_type': 'authorization_code', 'code': code,
                'redirect_uri': 'https://chatgpt.com/connector_platform_oauth_redirect',
                'client_id': cid, 'client_secret': cs, 'code_verifier': verifier}
    req = urllib.request.Request(BASE + '/token', data=urllib.parse.urlencode(tok_data).encode(),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded',
                                          'User-Agent': 'Mozilla/5.0'})
    tok = json.loads(op.open(req).read())
    at = tok['access_token']
    check('token exchange', at.startswith('at_') and tok.get('token_type') == 'Bearer', 'scope=' + str(tok.get('scope')))

    # 3. MCP методы с токеном
    body, err = mcp('tools/list', token=at)
    if err:
        raise RuntimeError(err)
    tl = json.loads(body)
    names = [t['name'] for t in tl.get('result', {}).get('tools', [])]
    check('tools/list (6 инструментов)', 'bridge_get_message' in names
          and 'bridge_put_reply' in names and 'bridge_put_message' in names
          and 'bridge_subscribe' in names and 'bridge_subscription_status' in names
          and 'bridge_unsubscribe' in names, str(names))

    body, err = mcp('server/discover', token=at)
    d = json.loads(body).get('result', {}) if body else {}
    caps = d.get('capabilities', {})
    check('discover: 2026-07-28 + events capability',
          '2026-07-28' in d.get('supportedVersions', []) and 'events' in caps, str(caps))

    body, err = mcp('events/list', token=at)
    el = json.loads(body).get('result', {}) if body else {}
    evs = [e.get('name') for e in el.get('events', [])]
    check('events/list: hermes.message.created', 'hermes.message.created' in evs, str(evs))

    # 4. Инструменты: put reply на несуществующий job -> not_found
    body, err = mcp('tools/call', {'name': 'bridge_get_message', 'arguments': {'job_id': 'job_nonexistent'}}, token=at)
    r = json.loads(body).get('result', {}) if body else {}
    content = r.get('content', [])
    txt = content[0].get('text') if content else ''
    check('get несуществующий job -> not_found', 'not_found' in str(txt), str(txt)[:80])

    # 5. events/subscribe: приватный callback -> отказ (без сети на приватный адрес)
    bad_secret = 'whsec_' + base64.b64encode(b'y' * 32).decode()
    body, err = mcp('events/subscribe', {
        'name': 'hermes.message.created',
        'arguments': {'queue': 'test'},
        'delivery': {'mode': 'webhook', 'url': 'http://127.0.0.1:9999/hook', 'secret': bad_secret}},
        token=at)
    payload = json.loads(body) if body else {}
    check('subscribe: приватный callback заблокирован (top-level error)',
          payload.get('error', {}).get('code') == -32015 and 'result' not in payload, (body or err)[:140])

    print()
    print(f'ИТОГ: {len(passed)} passed, {len(failed)} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())