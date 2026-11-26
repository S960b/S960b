#!/usr/bin/env python3
"""Pure-Python synthetic test for the Cold Storage nonce-reuse mathematics.

This checks the GHASH key recovery and forgery logic using two controlled
32-byte plaintexts, a single arbitrary GCM keystream and authentication mask.
No real flag, server interaction or external crypto package is needed.
"""
from solve import derive, forge, ghash

def xor(a,b): return bytes(x^y for x,y in zip(a,b))

def main():
    p1 = ("open".ljust(10)+"|"+"ice-eaaa".ljust(21)).encode()
    p2 = ("open".ljust(10)+"|"+"ice-e1512db7".ljust(21)).encode()
    assert len(p1) == len(p2) == 32
    assert p1[:16] == p2[:16]
    h = int("8ac7c6018c46310301929f354247b6f0",16)
    mask = int("f00dcafe01234567abcdefabcdef1234",16)
    keystream = bytes((i*71+3)%256 for i in range(32))
    c1,c2=xor(p1,keystream),xor(p2,keystream)
    t1=(ghash(h,c1)^mask).to_bytes(16,"big")
    t2=(ghash(h,c2)^mask).to_bytes(16,"big")
    recovered_h,recovered_mask,recovered_keystream=derive(p1,c1,t1,c2,t2)
    assert recovered_h == h and recovered_mask == mask and recovered_keystream == keystream
    target=("restricted"+"|"+"Ice-VII".ljust(21)).encode()
    ct,tag=forge(recovered_keystream,recovered_h,recovered_mask,target)
    assert xor(ct,keystream)==target
    assert int.from_bytes(tag,"big")==ghash(h,ct)^mask
    print("PASS: repeated-nonce H recovery and chosen-record forgery")

if __name__ == "__main__":
    main()
