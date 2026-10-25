#!/usr/bin/env python3
"""Verify the challenge source, obfuscated maintenance key and custom DFU image.
Runs with only Python stdlib, without opening sockets or executing challenge code.
"""
from pathlib import Path
import sys, hashlib, binascii, struct, zipfile, tarfile

ROOT=Path(__file__).resolve().parents[1]

def check(test, message):
    if not test:
        raise AssertionError(message)
    print('[PASS]', message)

def main():
    src = ROOT / 'original/yaju-os-e0b330b2a3740d63.tar.gz'
    sol = ROOT / 'original/yaju_os_solution.zip'
    src_hash = 'e0b330b2a3740d635fdc46b13c055a246b7b5af7a9b3f0a84d7a7d136ffc1267'
    sol_hash = 'c43f2345b72a4b0e7934cf5560c03480f53fab2ff17ae993ffd1b555b580f5b1'
    for p, h in ((src,src_hash),(sol,sol_hash)):
        check(hashlib.sha256(p.read_bytes()).hexdigest()==h, f'unchanged original: {p.name}')
    with tarfile.open(src, 'r:gz') as archive:
        m=archive.extractfile('yaju-os/artifacts/board_runtime/board_flash.bin')
        assert m is not None
        original_flash=m.read()
    flash=(ROOT/'source/yaju-os/artifacts/board_runtime/board_flash.bin').read_bytes()
    check(flash==original_flash and len(flash)==0x40000,'original 256-KiB board flash exact match')
    check(hashlib.sha256(flash).hexdigest()=='771ed881f53ce9fffeb9edcf938509ea1c2b2e83629f99be19893aafb5a9e8d0','original board flash SHA256')
    m1,m2=flash[0x1b75b:0x1b762],flash[0x1b754:0x1b75b]
    magic = bytearray()
    for i in range(7):
        x = m1[i] ^ m2[i] ^ ((0x11*i+0x3d)&255)
        r=(3*i+1)&7
        magic.append(((x>>r)|(x<<(8-r)))&255 if r else x)
    check(magic==bytes.fromhex('11451419198100'),'reverse-key from compiled ARM flash')
    image=(ROOT/'solution/yaju_payload.bin').read_bytes()
    with zipfile.ZipFile(sol) as archive:
        check(image==archive.read('yaju_payload.bin'), 'payload identical to earlier exploit zip')
        check((ROOT/'solution/yaju_solve.py').read_bytes()==archive.read('yaju_solve.py'),'solver identical to earlier exploit zip')
    check(len(image)==176 and len(image)%4==0,'DFU ARM image length and alignment')
    check(struct.unpack_from('<II',image)==(0x20007f00,0x1b009),'Cortex-M0 vector table')
    check(all(bytes((svc,0xdf)) in image for svc in [0x63,0xa0,0xa2]),'S130 SVCs in binary')
    crc=binascii.crc_hqx(image,0xffff)
    check(crc==0x11cc,'image CRC16/CCITT')
    init=struct.pack('<HHIHHH',0xffff,0xffff,0,1,0xfffe,crc)
    check(init.hex()=='ffffffff000000000100feffcc11','legacy DFU init packet')
    print('COMPLETE_PACKAGE_VERIFIED')

if __name__ == '__main__':
    try: main()
    except Exception as e:
        print('[FAIL]',e,file=sys.stderr)
        raise
