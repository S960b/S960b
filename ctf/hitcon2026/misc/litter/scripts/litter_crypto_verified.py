#!/usr/bin/env python3
"""Verified LITTER/5 crypto reference, recovered from ARM64 encryptor.
S-box indices 128..255 are 0, AES-192 12 rounds, 24-byte derived key.
Offline research only; no unknown flag or key embedded.
"""
import struct


def rol(b, k): return ((b << k) | (b >> (8-k))) & 255

def ror(b, k): return ((b >> k) | (b << (8-k))) & 255

# AES S-box, standard table fetched from ELF relocs (without importing third-party cryptography)
def sbox_from_elf(elf):
    return [struct.unpack_from('<Q', elf, 0x10958+8*i)[0] - 0x6bc0 for i in range(256)]

def derive_key(seed):
    assert len(seed)==24
    return bytes(rol(seed[(5*i)%24]^((0x71+0x3d*i)&255),(i%7)+1) for i in range(24))

def damaged_sbox(std):
    assert len(std)==256 and sorted(std)==list(range(256))
    return std[:128] + [0]*128

def expand_192(k,box):
    assert len(k)==24
    words=[list(k[i:i+4]) for i in range(0,24,4)]
    rc=1
    for i in range(6,52):
        t=words[-1][:]
        if i%6==0:
            t=[box[x] for x in t[1:]+t[:1]]
            t[0]^=rc
            rc=((rc<<1)^(27 if rc&128 else 0))&255
        words.append([words[i-6][j]^t[j] for j in range(4)])
    return bytes(sum(words,[]))

def mul2(x): return ((x << 1) ^ (27 if x & 128 else 0)) & 255

def encrypt_block(src,roundkeys,box):
    assert len(src)==16 and len(roundkeys)==208
    a=[v^k for v,k in zip(src,roundkeys[:16])]
    for r in range(1,13):
        a=[box[v] for v in a]
        a=[a[4*((j+c)%4)+j] for c in range(4) for j in range(4)]
        if r != 12:
            for c in range(4):
                i=4*c; x=a[i:i+4];v=[mul2(q) for q in x];total=x[0]^x[1]^x[2]^x[3]
                for j in range(4): a[i+j]=x[j]^total^v[j]^v[(j+1)%4]
        a=[v^k for v,k in zip(a,roundkeys[r*16:(r+1)*16])]
    return bytes(a)

def preprocess(p,key,roundkeys):
    rk=roundkeys[-16:]
    return bytes(rol(x ^ ((167+29*i)&255),(i%7)+1) ^ key[i%24] ^ rk[i%16] for i,x in enumerate(p))

def parse_litter(blob):
    sep=blob.index(b'\n\n')+2
    if not blob.startswith(b'LITTER/5\n'):raise ValueError('Unexpected file signature')
    return blob[:sep],blob[sep:sep+12],blob[sep+12:-16],blob[-16:]

def stream_for(nonce,count,roundkeys,box):
    assert len(nonce)==12
    return b''.join(encrypt_block(nonce+((2+i)&0xffffffff).to_bytes(4,'big'),roundkeys,box) for i in range((count+15)//16))[:count]

def gf_mul(x,y):
    z=0;v=y
    for i in range(128):
        if x & (1 << (127-i)):z^=v
        v=(v>>1)^(0xe1000000000000000000000000000000 if v&1 else 0)
    return z

def ghash(h,aad,cipher):
    y=0
    for body in (aad,cipher):
        for i in range(0,len(body),16):
            y=gf_mul(y^int.from_bytes(body[i:i+16].ljust(16,b'\0'),'big'),h)
    return gf_mul(y^int.from_bytes((len(aad)*8).to_bytes(8,'big')+(len(cipher)*8).to_bytes(8,'big'),'big'),h)

def compute_tag(aad,nonce,cipher,roundkeys,box):
    h=int.from_bytes(encrypt_block(b'\0'*16,roundkeys,box),'big')
    mask=int.from_bytes(encrypt_block(nonce+b'\0\0\0\x01',roundkeys,box),'big')
    return (ghash(h,aad,cipher)^mask).to_bytes(16,'big')

def verify(blob,seed,elf):
    aad,nonce,cipher,tag=parse_litter(blob)
    key=derive_key(seed)
    box=damaged_sbox(sbox_from_elf(elf))
    rk=expand_192(key,box)
    actual_tag=compute_tag(aad,nonce,cipher,rk,box)
    return actual_tag==tag,actual_tag.hex(),tag.hex()

def decrypt_with_seed(blob,seed,elf):
    aad,nonce,cipher,tag=parse_litter(blob)
    key=derive_key(seed);box=damaged_sbox(sbox_from_elf(elf));rk=expand_192(key,box)
    actual_tag=compute_tag(aad,nonce,cipher,rk,box)
    if actual_tag!=tag:raise ValueError(f'GCM authentication failed: calculated {actual_tag.hex()}, expected {tag.hex()}')
    pre=bytes(x^y for x,y in zip(cipher,stream_for(nonce,len(cipher),rk,box)))
    last=rk[-16:]
    return bytes(ror(pre[i]^key[i%24]^last[i%16],(i%7)+1)^((167+29*i)&255) for i in range(len(pre)))

if __name__=='__main__':
    from pathlib import Path
    import sys,zipfile
    if len(sys.argv)!=4:
        raise SystemExit('Usage: python litter_crypto_verified.py encryptor flag.png.ltr litter_seed_probe_result.zip')
    elf=Path(sys.argv[1]).read_bytes()
    target=Path(sys.argv[2]).read_bytes()
    z=zipfile.ZipFile(sys.argv[3]);plain=z.read('probe.png')
    for name,seed in [('baseline',bytes.fromhex('d1a55a3efafe7188ab5e647a253793b52f86cb7875bf7885')),('sequential_seed',bytes(range(24))),('ff_seed',b'\xff'*24)]:
        encrypted=z.read(name+'.ltr')
        decoded=decrypt_with_seed(encrypted,seed,elf)
        print(name,'full PNG verified:',decoded==plain,'bytes:',len(decoded),flush=True)
        assert decoded==plain
    print('Testing release secret against original GCM tag... ',flush=True)
    ok,calc,expected=verify(target,elf[-24:],elf)
    print('RELEASE_KEY_TAG_VALID',ok,'computed',calc,'expected',expected)
