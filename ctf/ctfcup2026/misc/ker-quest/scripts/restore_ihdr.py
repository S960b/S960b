#!/usr/bin/env python3
"""Restore the PNG IHDR height known from the original CRC.

Only use on the original challenge image. This intentionally keeps the
original IHDR CRC because that CRC already matches the corrected height.
"""
import argparse
import struct
import zlib
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()

    blob = bytearray(args.source.read_bytes())
    if blob[:8] != b"\x89PNG\r\n\x1a\n" or blob[12:16] != b"IHDR":
        raise SystemExit("Not a PNG with IHDR as the first chunk")
    original = struct.unpack_from(">I", blob, 20)[0]
    original_crc = struct.unpack_from(">I", blob, 29)[0]
    candidate = bytearray(blob)
    candidate[20:24] = struct.pack(">I", 1497)
    crc = zlib.crc32(candidate[12:29]) & 0xffffffff
    if crc != original_crc:
        raise SystemExit("Expected IHDR CRC does not match height 1497")
    args.output.write_bytes(candidate)
    print(f"restored PNG height: {original} to 1497")


if __name__ == "__main__":
    main()
