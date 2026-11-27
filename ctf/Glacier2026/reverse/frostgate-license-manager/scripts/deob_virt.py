#!/usr/bin/env python3
"""Offline inspection of .frost0's RIP-relative self-patch instructions.
   It does not create a runnable EXE, and is not needed by solve_virt.c.
"""
import re, struct, sys
from pathlib import Path

def u16(b, i): return struct.unpack_from('<H', b, i)[0]
def u32(b, i): return struct.unpack_from('<I', b, i)[0]

p = Path(sys.argv[1] if len(sys.argv)>1 else 'virt/virt.exe')
b = p.read_bytes()
pe=u32(b, 0x3c)
sects=pe+24+u16(b, pe+20)
section=None
for i in range(u16(b,pe+6)):
    h=sects+i*40
    name=b[h:h+8].split(b'\0',1)[0].decode('ascii')
    if name=='.frost0':
        vsize,va,rawsz,rawptr=struct.unpack_from('<4I',b,h+8)
        section=bytearray(b[rawptr:rawptr+rawsz])
        break
assert section is not None
patterns = [
    (rb'\x9c\xc7\x05\x01\x00\x00\x00(.{4})\x9d', 4),
    (rb'\x9c\x66\xc7\x05\x01\x00\x00\x00(.{2})\x9d', 2),
    (rb'\x9c\xc6\x05\x01\x00\x00\x00(.)\x9d', 1),
]
counts={1:0,2:0,4:0}
for regex, n in patterns:
    for match in re.finditer(regex, bytes(section), flags=re.DOTALL):
        section[match.end():match.end()+n]=match.group(1)
        counts[n]+=1
print('section RVA:', hex(va))
print('counts by patch width:', counts)
print('total patches:', sum(counts.values()))
Path('frost_patched.bin').write_bytes(section)
print('saved: frost_patched.bin (inspection only)')
