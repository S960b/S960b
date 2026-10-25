#!/usr/bin/env python3
"""HITCON 2026 litter full offline solver (no seeded key hardcoded).

Find repeated PNG IDAT chunk headers through damaged AES S-box leakage,
recover 24-byte derived key by statistics, invert derivation, authenticate GCM,
and save original PNG. Requires only Python 3 + numpy and bundled files.
"""
from collections import Counter,defaultdict
from pathlib import Path
import struct,sys,zlib
import numpy as np
from litter_crypto_verified import (parse_litter, rol, ror, derive_key,
                                    damaged_sbox, sbox_from_elf,
                                    decrypt_with_seed,verify)

HERE=Path(__file__).resolve().parent
elf=(HERE/'encryptor').read_bytes()
blob=(HERE/'flag.png.ltr').read_bytes()
aad,nonce,ct,tag=parse_litter(blob)
C=np.frombuffer(ct,dtype=np.uint8)
N=len(C)
print('LITTER/5 nonce',nonce.hex(),'length',N,'tag',tag.hex(),flush=True)

# Known plaintext supplied by the PNG standard, not guessed dimensions.
known_start=dict(enumerate(bytes.fromhex('89504e470d0a1a0a0000000d49484452')))
known_start.update({26:0,27:0})   # compression/filter method only; interlace can be 0 or 1
known_end={N-12+i:v for i,v in enumerate(bytes.fromhex('0000000049454e44ae426082'))}
observations=defaultdict(list)
for p,v in {**known_start,**known_end}.items():
    observations[p%24].append(ct[p]^rol(v^((167+29*p)&255),(p%7)+1))

# Only retain very strong repeated-vote proposals; others could be false positives.
K0={}
for k,vals in observations.items():
    v,count=Counter(vals).most_common(1)[0]
    if count>=2: K0[k]=v
print('Initial strong byte proposals', {i:f'{b:02x}' for i,b in sorted(K0.items())},flush=True)

# For each candidate PNG IDAT chunk size and initial offset, compare predicted
# eight bytes: big-endian chunk length and literal ASCII 'IDAT'.
# This scan performs NO trial decryption and does NOT assume 64-KB chunks.
def match_headers(length,start):
    nfull=max(0,(N-start)//(length+12))
    if nfull<3:return (0,0)
    positions=start+(length+12)*np.arange(nfull,dtype=np.int64)[:,None]+np.arange(8,dtype=np.int64)[None,:]
    if positions[-1,-1]>=N:return (0,0)
    residue=positions%24
    active=np.zeros_like(positions,dtype=bool)
    guess=np.zeros_like(positions,dtype=np.uint16)
    for k,v in K0.items():
        z=residue==k
        active|=z
        guess[z]=v
    count=int(active.sum())
    if count<8:return (0,0)
    plaintext=np.array(list(length.to_bytes(4,'big')+b'IDAT'),dtype=np.uint16)[None,:]
    rot=positions%7+1
    x=plaintext^((167+29*positions)&255)
    transformed=(((x<<rot)|(x>>(8-rot)))&255)
    measured=C[positions].astype(np.uint16)^transformed
    hits=int(np.count_nonzero(active&(measured==guess)))
    return hits,count

choices=[]
for length in [1024,2048,4096,8192,16384,32768,65536,131072]:
    for start in range(33,1025):
        hit,num=match_headers(length,start)
        # Require strong evidence with at least 20 matches to avoid cherry-picking.
        if hit>=20 and hit>num*0.20:
            choices.append((hit/num,hit,num,length,start))
choices.sort(reverse=True)
if not choices:
    raise SystemExit('NO_REPEATED_IDAT_FOUND: expand the search over lengths or offsets')
rate,hits,checked,L,S=choices[0]
print(f'IDAT found: first_offset={S}, chunk_length={L}; score={hits}/{checked} ({rate:.1%})',flush=True)
print('Other high-scoring candidates:',choices[1:3],flush=True)

# One header every L+12 bytes. Last IDAT is usually shorter and is excluded.
samples=defaultdict(list)
nfull=(N-S)//(L+12)
for j in range(nfull):
    base=S+j*(L+12)
    for k,v in enumerate(L.to_bytes(4,'big')+b'IDAT'):
        pos=base+k
        observed=ct[pos]^rol(v^((167+29*pos)&255),(pos%7)+1)
        samples[pos%24].append(observed)

box=damaged_sbox(sbox_from_elf(elf))
Sbox_outputs=set(box)
key=[]
for index in range(24):
    vals=samples[index]
    counts=Counter(vals)
    valid=[v for v in range(256) if all(((v^t) in Sbox_outputs) for t in vals)]
    if len(valid)!=1:
        raise SystemExit(f'KEY_BYTE_UNCERTAIN index={index}: possible={valid}')
    value=valid[0]
    key.append(value)
    print(f'K[{index:02}] = {value:02x} / zero-S-box votes {counts[value]}/{len(vals)}',flush=True)
key=bytes(key)
print('Recovered 24-byte derived key:',key.hex(),flush=True)

# Derivation seed[(5*i)%24] -> key[i] is a permutation + reversible rotate/XOR.
seed=bytearray(24)
for i,k in enumerate(key):seed[(5*i)%24]=ror(k,(i%7)+1)^((0x71+0x3d*i)&255)
assert derive_key(seed)==key
print('Recovered original 24-byte seed:',seed.hex(),flush=True)

ok,actual,expected=verify(blob,bytes(seed),elf)
print('GCM tag computed:',actual)
print('GCM tag expected:',expected)
if not ok:raise SystemExit('BAD_GCM_TAG — refusing to write unauthenticated PNG')
plain=decrypt_with_seed(blob,bytes(seed),elf)
assert plain.startswith(b'\x89PNG\r\n\x1a\n')
p=8
count=0
while p+12<=len(plain):
    size=struct.unpack_from('>I',plain,p)[0]
    typ=plain[p+4:p+8]
    assert p+12+size<=len(plain)
    crc=struct.unpack_from('>I',plain,p+8+size)[0]
    assert zlib.crc32(plain[p+4:p+8+size])&0xffffffff==crc
    count+=1
    p+=size+12
    if typ==b'IEND':break
assert typ==b'IEND' and p==len(plain)
path=HERE/'flag_recovered.png'
path.write_bytes(plain)
print('FLAG_PNG_RECOVERED',path,'bytes',len(plain),'PNG_CRC_ALL_PASS',count,'chunks',flush=True)
