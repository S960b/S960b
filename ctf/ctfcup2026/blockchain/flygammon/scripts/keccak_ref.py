"""Ethereum Keccak-256. Uses pycryptodome if present, else pure Python reference.
Pure-Python path is slower; install pycryptodome for bulk ID scans.
"""
try:
    from Crypto.Hash import keccak as _fast
except ImportError:
    _fast=None
RC=(0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,
0x000000000000808B,0x0000000080000001,0x8000000080008081,0x8000000000008009,
0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,
0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,
0x8000000000008002,0x8000000000000080,0x000000000000800A,0x800000008000000A,
0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008)
ROT=(0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14)
MASK=(1<<64)-1

def _rot(x,n):return ((x<<n)|(x>>(64-n)))&MASK if n else x

def _f(a):
    for rc in RC:
        c=[a[x]^a[x+5]^a[x+10]^a[x+15]^a[x+20] for x in range(5)]
        d=[c[(x-1)%5]^_rot(c[(x+1)%5],1) for x in range(5)]
        for i in range(25):a[i]^=d[i%5]
        b=[0]*25
        for x in range(5):
            for y in range(5):
                v=x+5*y
                b[y+5*((2*x+3*y)%5)]=_rot(a[v],ROT[v])
        for x in range(5):
            for y in range(5):
                v=x+5*y
                a[v]=b[v]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y])
        a[0]^=rc

def keccak256(data):
    if _fast is not None:
        x=_fast.new(digest_bits=256); x.update(data);return x.digest()
    padded=bytearray(data);padded.append(1)
    while len(padded)%136!=135:padded.append(0)
    padded.append(0x80)
    a=[0]*25
    for off in range(0,len(padded),136):
        block=padded[off:off+136]
        for j in range(17):a[j]^=int.from_bytes(block[j*8:j*8+8],'little')
        _f(a)
    return b''.join(a[i].to_bytes(8,'little') for i in range(4))

assert keccak256(b'').hex()=='c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'
