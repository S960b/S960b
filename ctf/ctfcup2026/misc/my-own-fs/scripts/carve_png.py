#!/usr/bin/env python3
"""Scan a myfs image for file chains and carve any PNG found in freed inodes."""
import struct, sys, pathlib

ISZ, HEAD, CONT = 1337, 51, 5

def load(path):
    return pathlib.Path(path).read_bytes()

def inode_off(n_inodes, i):
    return n_inodes + i * ISZ

def read_chain(img, n_inodes, i, max_inodes=100000):
    data = bytearray(); cur = i; seen = set(); size = None
    while cur and cur not in seen and len(seen) < max_inodes:
        seen.add(cur)
        off = inode_off(n_inodes, cur)
        blk = img[off:off + ISZ]
        if len(blk) < HEAD: break
        nxt, kind = struct.unpack_from('<IB', blk, 0)
        if size is None:
            size = struct.unpack_from('<Q', blk, 15)[0]
            data += blk[HEAD:]
        else:
            data += blk[CONT:]
        cur = nxt
    if size is not None: del data[size:]
    return bytes(data), len(seen)

def main():
    img = load(sys.argv[1])
    n = len(img) // 1338
    print('inodes:', n, 'size:', len(img))
    found = 0
    for i in range(n):
        off = inode_off(n, i)
        blk = img[off:off + HEAD]
        if len(blk) < HEAD: break
        kind = blk[4]
        if kind not in (1, 2): continue
        data, chain = read_chain(img, n, i)
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            print('inode', i, 'chain', chain, 'bytes', len(data))
            out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else 'carved_%d.png' % i)
            out.write_bytes(data); print('  ->', out); found += 1
    print('png files found:', found)

if __name__ == '__main__':
    main()
