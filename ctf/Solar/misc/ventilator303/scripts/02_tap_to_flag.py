#!/usr/bin/env python3
"""Reproduce extraction of the SOLARCTF Ventilator 303 flag from a ZX TAP.

Requires Python 3 standard library only.  It checks all four TAP XORs,
reads the BASIC/CODE headers, reverses the Z80 LCG and permutation.
"""
import argparse
from pathlib import Path
import struct

SEED_OFFSET = 0x83D4
PERM_OFFSET = 0x83D8
CIPHER_OFFSET = 0x8403
LOAD_ADDRESS = 0x8000
ALPHABET_SIZE = 43  # Z80 code: CP 0x2B, addr 0x82B6


def tap_blocks(tap):
    p = 0
    while p < len(tap):
        if p + 2 > len(tap):
            raise ValueError("Truncated TAP length")
        size, = struct.unpack_from("<H", tap, p)
        p += 2
        if not size or p + size > len(tap):
            raise ValueError("Invalid TAP block length")
        block = tap[p:p + size]
        p += size
        checksum = 0
        for byte in block:
            checksum ^= byte
        if checksum:
            raise ValueError("Bad Spectrum XOR check at block offset %d" % (p - size))
        yield block


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tap", nargs="?", default="ventilator303.tap")
    ap.add_argument("--dump-code", metavar="PATH", help="also save Z80 program bytes for z80dasm")
    args = ap.parse_args()
    raw = Path(args.tap).read_bytes()
    blocks = list(tap_blocks(raw))
    if len(blocks) != 4:
        raise ValueError("Expected 4 ZX TAP blocks; got %d" % len(blocks))
    print("TAP:", len(raw), "bytes; 4 blocks; XOR checks: OK")
    print("BASIC name:", blocks[0][2:12].decode("ascii"))
    print("BASIC auto line:", struct.unpack_from("<H", blocks[0], 14)[0])
    print("CODE name:", blocks[2][2:12].decode("ascii"))
    code_length, load = struct.unpack_from("<HH", blocks[2], 12)
    print("CODE length:", code_length, "load addr: 0x%04X" % load)
    if load != LOAD_ADDRESS or len(blocks[3]) - 2 != code_length:
        raise ValueError("Unexpected CODE block size/load address")
    code = blocks[3][1:-1]
    if args.dump_code:
        Path(args.dump_code).write_bytes(code)
        print("Z80 CODE extracted:", args.dump_code, len(code), "bytes")

    def fetch(address, size):
        start = address - load
        if not 0 <= start <= len(code) - size:
            raise ValueError("Out-of-range program address 0x%04x" % address)
        return code[start:start + size]

    seed, = struct.unpack("<I", fetch(SEED_OFFSET, 4))
    permutation = list(fetch(PERM_OFFSET, ALPHABET_SIZE))
    ciphertext = fetch(CIPHER_OFFSET, ALPHABET_SIZE)
    if sorted(permutation) != list(range(ALPHABET_SIZE)):
        raise ValueError("Not a permutation of 0..42")
    print("Z80 LCG seed @ 0x%04X: 0x%08X" % (SEED_OFFSET, seed))
    print("permutation @ 0x%04X:" % PERM_OFFSET, permutation)
    print("ciphertext @ 0x%04X:" % CIPHER_OFFSET, ciphertext.hex())

    ordered = bytearray(ALPHABET_SIZE)
    for i, encrypted in enumerate(ciphertext):
        seed = (seed * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
        key = (seed >> 16) & 0xff  # Z80: LD A, (0x83C6)
        plain = encrypted ^ key
        ordered[permutation[i]] = plain
        if i < 5:
            print("i=%d state=%08X key=%02X cipher=%02X plain=%r target_position=%d" %
                  (i, seed, key, encrypted, chr(plain), permutation[i]))
    print("FLAG:", ordered.decode("ascii"))


if __name__ == "__main__":
    main()
