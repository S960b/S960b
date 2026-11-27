#!/usr/bin/env python3
"""Pure-Python unpacker for GlacierCTF 2026 mircosoft.exe.

Derived from original x86-64 loader routines at 0x140001144,
0x1400010f0, 0x1400010a4 and 0x140001068.
No Windows execution or external dependencies needed.
"""
from __future__ import annotations
import argparse
import hashlib
import struct
from pathlib import Path

U64 = (1 << 64) - 1

def read_section(pe: bytes, name: str) -> tuple[int, bytes]:
    if pe[:2] != b"MZ" or len(pe) < 0x100:
        raise ValueError("Not an MZ executable")
    header = struct.unpack_from("<I", pe, 0x3c)[0]
    if pe[header:header+4] != b"PE\0\0":
        raise ValueError("PE signature is missing")
    count = struct.unpack_from("<H", pe, header+6)[0]
    optsize = struct.unpack_from("<H", pe, header+20)[0]
    for n in range(count):
        pos = header + 24 + optsize + 40*n
        secname, _, rva, size, offset = struct.unpack_from("<8sIIII", pe, pos)
        if secname.rstrip(b"\0").decode("ascii") == name:
            if offset + size > len(pe):
                raise ValueError(f"Truncated section {name}")
            return rva, pe[offset:offset+size]
    raise ValueError(f"Missing PE section {name}")

def unpack(loader: bytes) -> bytes:
    rva, data = read_section(loader, ".rdata")
    if rva != 0x3000 or len(data) < 0x4860:
        raise ValueError("Unexpected loader .rdata layout")

    # Decode three embedded constant tables.
    def decrypt_constant(at_rva: int, size: int) -> bytes:
        pos = at_rva - rva
        return bytes(v ^ ((i*0x5a + 0x3c) & 255)
                     for i, v in enumerate(data[pos:pos+size]))

    xor_key = decrypt_constant(0x7800, 16)
    subtract_key = decrypt_constant(0x7810, 32)
    rotation_key = decrypt_constant(0x7830, 32)

    # Stage 1: reverse shuffled 256-byte blocks, using the loader's LCG.
    number_of_blocks = 0x48
    permutation = list(range(number_of_blocks))
    rng_state = 0xDEAD1337CAFE4269
    for idx in range(number_of_blocks-1, 0, -1):
        rng_state = (rng_state*0x5851F42D4C957F2D + 0x14057B7EF767814F) & U64
        other = rng_state % (idx+1)
        permutation[idx], permutation[other] = permutation[other], permutation[idx]
    inverse = [0] * number_of_blocks
    for idx, item in enumerate(permutation):
        inverse[item] = idx
    stage = bytearray(0x4800)
    source = data[:0x4800]
    for src_index, target_index in enumerate(inverse):
        stage[256*target_index:256*(target_index+1)] = source[256*src_index:256*(src_index+1)]

    # Stage 2: rotate right and subtract a periodic constant.
    for i, v in enumerate(stage):
        amount = rotation_key[i % 32] & 7
        rotated = (v >> amount) | ((v << (8-amount)) & 255) if amount else v
        stage[i] = (rotated - subtract_key[i % 32]) & 255

    # Stage 3: XOR with a 16-byte key and feedback from original input byte.
    feedback = 0
    for i, encrypted_byte in enumerate(stage):
        stage[i] = encrypted_byte ^ xor_key[i % 16] ^ feedback
        feedback = (encrypted_byte + xor_key[(i+3) % 16]) & 255

    inner = bytes(stage)
    read_section(inner, ".data")
    if struct.unpack_from("<I", inner, 0x3c)[0] != 0xd8:
        raise ValueError("Unexpected PE layout after deobfuscation")
    return inner

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract unpacked_pe.bin from original mircosoft.exe")
    parser.add_argument("exe", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("unpacked_pe.bin"))
    args = parser.parse_args()
    inner = unpack(args.exe.read_bytes())
    args.output.write_bytes(inner)
    print(f"Unpacked PE: {args.output} ({len(inner)} bytes)")
    print(f"SHA256: {hashlib.sha256(inner).hexdigest()}")
    _, data = read_section(inner, ".data")
    print("Four 16-byte keys in .data:", *(data[i:i+16].hex() for i in range(0, 64, 16)), sep="\n  ")

if __name__ == "__main__":
    main()
