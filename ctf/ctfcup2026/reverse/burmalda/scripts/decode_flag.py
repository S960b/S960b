#!/usr/bin/env python3
"""Decode the Burmalda flag from the data embedded in main.gdc.

The array and the four constants below are copied verbatim from
``main.gd``, recovered from ``Burmalda.pck`` with GDRE Tools. The routine is
the same LCG the game uses: 32-bit state, key byte taken from bits 13..20,
and a subtract-by-index step after the XOR.

Usage: python3 decode_flag.py
"""
SA = 3405691582   # 0xCAFEBABE
SB = 322376503   # 0x13371337
LC = 1664525
LA = 1013904223

EF = [178, 199, 143, 9, 35, 45, 95, 158, 200, 127, 167, 0, 20, 238, 84, 200, 251, 172, 224, 227, 212, 76, 173, 101, 77, 236, 54, 189, 222, 108, 228, 71, 93, 47, 60, 201, 255, 200, 138, 123, 162, 155, 203, 232, 48, 92, 106, 244, 79, 24, 128, 230, 123, 116, 105, 40, 157, 42, 191, 155, 190, 119, 210, 197, 88, 9, 147, 13, 207, 28, 244, 105, 105, 178, 223, 103, 49, 191, 137, 219, 53, 155, 239, 122, 255, 144, 203, 76, 27, 20, 167, 167, 198, 19, 196, 99, 189, 118, 60, 110, 198, 181, 238, 9, 139, 115, 30, 102, 14, 158, 197, 192, 201, 210, 98, 222, 219, 122, 61, 229, 164, 254, 127, 114, 122, 246, 129, 253, 88, 50, 161, 48, 17, 121, 104, 216, 202, 167, 35, 50, 229, 70, 55, 102, 170, 125, 137, 147, 66, 135, 155]

MASK = 0xFFFFFFFF


def decode():
    state = (SA ^ SB) & MASK
    out = bytearray()
    for i, byte in enumerate(EF):
        state = (LC * state + LA) & MASK
        key = (state >> 13) & 0xFF
        out.append(((byte ^ key) - i) & 0xFF)
    return out.decode("utf-8")


if __name__ == "__main__":
    text = decode()
    if not text.startswith("ctfcup{"):
        raise SystemExit("unexpected output, check the constants")
    print(text)
