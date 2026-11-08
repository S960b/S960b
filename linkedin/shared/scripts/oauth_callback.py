#!/usr/bin/env python3
"""Local OAuth callback server for LinkedIn (authorized API flow only).

Listens on :8443 (IPv4+IPv6), catches the authorization code from the
redirect_uri and exchanges it for access/refresh tokens via the official
LinkedIn OAuth endpoint. Tokens are stored in ~/linkedin/.credentials.
"""
import http.server
import socketserver
import socket
import json
import urllib.parse
import urllib.request
import os

CREDS = os.path.expanduser('~/linkedin/.credentials')
REDIRECT = 'http://localhost:8444/callback'


def load():
    d = {}
    if os.path.exists(CREDS):
        for line in open(CREDS):
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                d[k.strip()] = v.strip()
    return d


def save(d):
    with open(CREDS, 'w') as f:
        for k, v in d.items():
            f.write('{}={}\n'.format(k, v))
    os.chmod(CREDS, 0o600)


def exchange(code, creds):
    body = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT,
        'client_id': creds['CLIENT_ID'],
        'client_secret': creds['CLIENT_SECRET'],
    }).encode()
    req = urllib.request.Request('https://www.linkedin.com/oauth/v2/accessToken',
                                 data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.parse_qs(u.query)
        if 'code' in p:
            try:
                creds = load()
                tok = exchange(p['code'][0], creds)
                creds.update({
                    'ACCESS_TOKEN': tok.get('access_token', ''),
                    'REFRESH_TOKEN': tok.get('refresh_token', ''),
                    'EXPIRES_IN': str(tok.get('expires_in', '')),
                    'REFRESH_EXPIRES_IN': str(tok.get('refresh_token_expires_in', '')),
                })
                save(creds)
                print('Access token obtained', flush=True)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(
                    b'<html><body><h2>Success</h2><p>LinkedIn connected. You can close this tab.</p></body></html>')
            except Exception as e:
                print('EXCHANGE ERROR:', repr(e), flush=True)
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(('Exchange failed: %r' % e).encode())
        elif 'error' in p:
            print('OAUTH ERROR:', p.get('error'), p.get('error_description'), flush=True)
            self.send_response(400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(('OAuth error: %s %s' % (
                p.get('error'), p.get('error_description'))).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'waiting for callback...')


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


if __name__ == '__main__':
    print('listening on :8444', flush=True)
    S(('::', 8444), H).serve_forever()