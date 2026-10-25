#!/usr/bin/env python3
"""Independent static evidence for the maintenance key.
Usage: python3 magic_proof.py /path/to/board_flash.bin
"""
import pathlib,sys
p = pathlib.Path(sys.argv[1])
data=p.read_bytes()
expected=bytes.fromhex('11451419198100')
assert len(data) == 0x40000
code1 = data[0x1b75b:0x1b762]
code2 = data[0x1b754:0x1b75b]
input_bytes=[]
for i in range(7):
    x=code1[i] ^ code2[i] ^ ((0x11*i+0x3d)&255)
    rot=(3*i+1)&7
    original = ((x >> rot) | (x << (8-rot))) & 255 if rot else x
    input_bytes.append(original)
assert bytes(input_bytes) == expected, bytes(input_bytes).hex()
print('MAGIC_PROOF_PASS', bytes(input_bytes).hex())
