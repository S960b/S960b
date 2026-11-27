#!/usr/bin/env python3
"""Exact DejaHeap v10 MAC recovered from ELF 0x408130 and checked vs native.
Usage: python3 dh_mac_exact.py
"""
import struct
MASK=(1<<64)-1
R=lambda x,n:((x<<n)|(x>>(64-n)))&MASK

def mac(op,kind,fd,counter,key,length):
    block=struct.pack('<IIIIQQ',op,kind,fd,counter,key,length)
    b=0xb7b78592fee2c0c7
    v1=0xc912cca87c519df1
    v2=0xa7bd9391e9f2cad9
    v3=0xd604c6a862478ae5
    for idx in range(4):
        m=int.from_bytes(block[idx*8:idx*8+8],'little')
        b ^= m
        a = (v2+v3)&MASK
        v2=R(v2,13)^a
        x=b
        v1=(v1+b)&MASK
        a=R(a,32)
        x=R(x,16)^v1
        v1=(v1+v2)&MASK
        v2=R(v2,17)
        a=(a+x)&MASK
        x=R(x,21)
        v2^=v1
        x^=a             # NOTE missing before in older python
        v1=R(v1,32)
        a=(a+v2)&MASK
        v1=(v1+x)&MASK
        v2=R(v2,13)^a
        x=R(x,16)^v1
        a=R(a,32)
        v1=(v1+v2)&MASK
        v2=R(v2,17)
        a=(a+x)&MASK
        x=R(x,21)
        v2^=v1
        b=x^a
        v3=m^a
        v1=R(v1,32)
    b ^=0x2000000000000000
    a=v2
    c=(v3+v2)&MASK
    a=R(a,13)
    d=(b+v1)&MASK
    b=R(b,16)^d
    a ^=c
    c=R(c,32)
    d=(d+a)&MASK
    a=R(a,17)
    x=b
    a^=d
    c=(c+b)&MASK
    d=R(d,32)
    x=R(x,21)^c
    c=(c+a)&MASK
    a=R(a,13)^c
    d=(d+x)&MASK
    x=R(x,16)^d
    c=R(c,32)
    d=(d+a)&MASK
    a=R(a,17)
    c=(c+x)&MASK
    x=R(x,21)
    a^=d
    d=R(d,32)
    x^=c
    c ^=0x2000000000000000
    s=d^0xff
    for j in range(4):
        c=(c+a)&MASK
        s=(s+x)&MASK
        a=R(a,13)^c
        c=R(c,32)
        x=R(x,16)^s
        s=(s+a)&MASK
        a=R(a,17)^s
        c=(c+x)&MASK
        x=R(x,21)^c
        s=R(s,32)
    return (a^x^c^s)&MASK


if __name__ == '__main__':
    k=0x1122334455667788
    tests=((1,1,0,0,k,0,0xc978e7a6ee7de9c7),
           (2,1,3,0,k,256,0xb92f335964a4c1c8))
    for op,kind,fd,ctr,key,length,expected in tests:
        actual=mac(op,kind,fd,ctr,key,length)
        print(f'op={op}, expected={expected:#018x}, actual={actual:#018x}, match={actual==expected}')
        assert actual==expected
