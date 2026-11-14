#!/usr/bin/env python3
"""Recover four CRC32-constrained words from the archived Matryoshka notes.

The script verifies the recovered password independently. If a separately
extracted stage2.bin is available, it applies the recorded XOR to that file.
No flag or competition host is embedded in this script.
"""
import argparse
import struct
import zlib
from pathlib import Path

TARGETS = [0x6D795F63, 0x30306C5F, 0x70343473, 0x77307264]
OFFSET = 0x1B560
SIZE = 36


def crc(x):
    return zlib.crc32(x) & 0xFFFFFFFF


def solve_word(target):
    base = crc(bytes(4))
    cols = []
    for j in range(32):
        bit = bytearray(4)
        bit[j // 8] = 1 << (j % 8)
        cols.append(crc(bit) ^ base)

    # Row i: 32 coefficient bits, then bit 32 as right-hand side.
    rows = []
    for i in range(32):
        coeff = sum(((cols[j] >> i) & 1) << j for j in range(32))
        rhs = ((target ^ base) >> i) & 1
        rows.append(coeff | (rhs << 32))

    pivots = []
    rank = 0
    for j in range(32):
        k = next((i for i in range(rank, 32) if (rows[i] >> j) & 1), None)
        if k is None:
            continue
        rows[rank], rows[k] = rows[k], rows[rank]
        for i in range(32):
            if i != rank and ((rows[i] >> j) & 1):
                rows[i] ^= rows[rank]
        pivots.append(j)
        rank += 1

    if rank != 32:
        raise RuntimeError(f"Unexpected rank {rank}; not a unique CRC inverse")
    value = sum(((rows[i] >> 32) & 1) << j for i, j in enumerate(pivots))
    raw = struct.pack("<I", value)
    assert crc(raw) == target
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage2", type=Path, help="separately extracted stage2.bin")
    args = ap.parse_args()
    password = b"".join(solve_word(t) for t in TARGETS)
    print("password (hex):", password.hex())
    for i, target in enumerate(TARGETS):
        print(f"CRC[{i*4}:{i*4+4}]   = {crc(password[i*4:i*4+4]):08x}")
        assert crc(password[i*4:i*4+4]) == target
    if args.stage2:
        data = args.stage2.read_bytes()
        if len(data) < OFFSET + SIZE:
            raise ValueError("stage2 is shorter than the recorded XOR slice")
        blob = data[OFFSET:OFFSET+SIZE]
        decoded = bytes(c ^ password[i % 16] for i, c in enumerate(blob))
        print("decoded bytes:", decoded)


if __name__ == "__main__":
    main()
