#!/usr/bin/env python3
"""Recover the kallsyms table from the supplied Book of Eclipse bzImage.

This script uses the structural offsets verified against the challenge's
specific 6.12.112 vmlinux, rather than assuming a generic distro kernel.
"""
import argparse
import struct
import zlib
from pathlib import Path

TOKENS = 0x17D3238
MARKERS = 0x17D2778
NAMES = 0x157A888
OFFSETS = 0x17D37C0
SYMBOL_COUNT = 175617

def extract_kernel(data):
    if data[:4] == b'\x7fELF':
        return data
    index = -1
    while True:
        index = data.find(b'\x1f\x8b\x08', index+1)
        if index < 0:
            raise ValueError('No gzip-compressed ELF found in bzImage')
        try:
            content = zlib.decompressobj(31).decompress(data[index:])
            if content[:4] == b'\x7fELF':
                return content
        except zlib.error:
            pass

def recover_symbols(image):
    assert struct.unpack_from('<I', image, NAMES-8)[0] == SYMBOL_COUNT
    position = TOKENS
    tokens = []
    for i in range(256):
        end = image.index(0, position)
        tokens.append(image[position:end].decode('latin1'))
        position = end + 1
    names_position = NAMES
    base_pos = (OFFSETS + SYMBOL_COUNT*4 + 7) & ~7
    base = struct.unpack_from('<Q', image, base_pos)[0]
    if base != 0xffffffff81000000:
        raise ValueError('Unexpected kernel relative base: ' + hex(base))
    result=[]
    for i in range(SYMBOL_COUNT):
        if i % 256 == 0:
            expected=struct.unpack_from('<I',image,MARKERS+(i//256)*4)[0]
            if names_position - NAMES != expected:
                raise ValueError('kallsyms marker mismatch at %d' % i)
        count=image[names_position]; names_position+=1
        if count&0x80:
            count=(count&0x7f)|(image[names_position]<<7)
            names_position+=1
        raw=image[names_position:names_position+count];names_position+=count
        decoded=''.join(tokens[x] for x in raw)
        off=struct.unpack_from('<i',image,OFFSETS+i*4)[0]
        address=off if off>=0 else (base-1-off)
        result.append((address,decoded[0],decoded[1:]))
    if names_position > MARKERS:
        raise ValueError('Names overlap marker table')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('image',help='challenge bzImage or extracted vmlinux')
    parser.add_argument('-o','--output',default='System.map')
    args=parser.parse_args()
    syms=recover_symbols(extract_kernel(Path(args.image).read_bytes()))
    Path(args.output).write_text(''.join(f'{a:016x} {t} {n}\n' for a,t,n in syms))
    print(f'[+] Extracted {len(syms)} symbols to {args.output}')
    for a,t,n in syms:
        if n in ('_text','_copy_from_user','_copy_to_user','commit_creds',
                 'prepare_kernel_cred','init_cred','modprobe_path'):
            print(f'    {a:016x} {n}')
