#!/usr/bin/env python3
"""Recovery from second-order pairwise proof leakage (GlacierCTF Decipher Stage 2)."""
import sys
from pathlib import Path
from hashlib import sha256
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
sys.path.insert(0, str(Path(__file__).resolve().parent / 'decipher-1'))
from decipher import SBOX, DeCipher, key_schedule

f = np.load(Path(__file__).parent / 'decipher-1' / 'output_stage2.npz')
pts, cts, proofs = f['plaintexts'], f['ciphertexts'], f['proofs']
assert np.all(np.bitwise_xor(pts[:,None,:], pts[None,:,:]) == proofs[:,:,0,:])

# SBOX[x] = SBOX[0] ^ L(x) ^ 0xe7*((bit1(x)^bit5(x)) & bit6(x)).
SBOX_ZERO=SBOX[0]
SBOX_L=[SBOX[1<<i]^SBOX_ZERO for i in range(8)]

def a(x): return ((x>>1)^(x>>5))&1
def b(x): return (x>>6)&1
def nonlin(x): return a(x)&b(x)
def L_byte(x):
    y=0
    for k in range(8):
        if x & (1<<k): y ^= SBOX_L[k]
    return y
assert all(SBOX[x] == (SBOX_ZERO ^ L_byte(x) ^ (0xe7 if nonlin(x) else 0)) for x in range(256))

def reverse_linear(state):
    d=bytearray(state)
    DeCipher.mix_columns_inv(d)
    DeCipher.shift_rows_inv(d)
    return d

# Recover a=bit1^bit5 and b=bit6 of reference input (U_t ^ round_key_t)
# for all 10 rounds using the observed difference between all 16 plaintext executions.
classes=[]
for r in range(10):
    in_diffs=[proofs[0,j,r].tolist() for j in range(1,16)]
    out_diffs=[reverse_linear(proofs[0,j,r+1,:].tolist()) for j in range(1,16)]
    cs=[]
    for idx in range(16):
        candidates=[]
        for A in range(2):
            for B in range(2):
                if all((L_byte(di[idx]) ^ (0xe7 if (A*b(di[idx]) ^ B*a(di[idx]) ^ nonlin(di[idx])) else 0)) == do[idx]
                    for di,do in zip(in_diffs,out_diffs)):
                    candidates.append((A,B))
        if len(candidates)!=1:
            raise RuntimeError(f'round={r}, byte={idx}, class={candidates}')
        cs.append(candidates[0])
    classes.append(cs)
print('[+] 320 round input-class bits extracted from proofs')

N=168 # 128 for base AES-like key bits + 40 key schedule SBOX nonlinearity bits
C=1<<N
V=[1<<i for i in range(N)]
def num_byte(value): return [C if ((value>>j)&1) else 0 for j in range(8)]
def var_byte(n): return V[n:n+8]
def sym_xor(*args):
    return [v for v in map(lambda ar: __import__('functools').reduce(lambda x,y:x^y,ar,0),zip(*args))]

def l_map(x, bas):
    out=[]
    for bit in range(8):
        e=0
        for j in range(8):
            if bas[j] & (1<<bit): e ^= x[j]
        out.append(e)
    return out

def sym_sbox(x,q):
    y=l_map(x,SBOX_L)
    for bit in range(8):
        if SBOX_ZERO & (1<<bit): y[bit] ^= C
        if 0xe7 & (1<<bit): y[bit] ^= q
    return y

def sym_gf_mul(x,v):
    from decipher import galois_multiply
    return l_map(x,[galois_multiply(1<<j,v) for j in range(8)])

def sym_mix_shift(s):
    # ShiftRows identical to cipher definition, then MixColumns
    s=list(s)
    s[1], s[5], s[9],s[13] = s[5],s[9],s[13],s[1]
    s[2], s[6],s[10],s[14] = s[10],s[14],s[2],s[6]
    s[3], s[7],s[11],s[15] = s[15],s[3],s[7],s[11]
    for i in range(4):
        j=i*4
        v0,v1,v2,v3=s[j:j+4]
        s[j  ]=sym_xor(sym_gf_mul(v0,2),sym_gf_mul(v1,3),v2,v3)
        s[j+1]=sym_xor(v0,sym_gf_mul(v1,2),sym_gf_mul(v2,3),v3)
        s[j+2]=sym_xor(v0,v1,sym_gf_mul(v2,2),sym_gf_mul(v3,3))
        s[j+3]=sym_xor(sym_gf_mul(v0,3),v1,v2,sym_gf_mul(v3,2))
    return s

# Key expansion (all affine except for one unknown bit in each SBOX invocation)
words=[ [var_byte(8*(i*4+j)) for j in range(4)] for i in range(4)]
from decipher import RCON
for i in range(4,44):
    temp=words[i-1]
    if i%4==0:
        temp=temp[1:]+temp[:1]
        temp=[sym_sbox(x,V[128 + 4*(i//4-1) + j]) for j,x in enumerate(temp)]
        temp[0]=sym_xor(temp[0],num_byte(RCON[i//4-1]))
    words.append([sym_xor(x,y) for x,y in zip(words[i-4],temp)])
rks=[sum(words[4*r:4*r+4],[]) for r in range(11)]

# Linearized cipher execution: nonlinear SBOX term for encryption is known from proof-derived classes.
state=[num_byte(int(x)) for x in pts[0]]
equations=[]
for r in range(10):
    effective=[sym_xor(x,k) for x,k in zip(state,rks[r])]
    for idx,byte in enumerate(effective):
        ai,bi=classes[r][idx]
        equations.append(byte[1]^byte[5]^(C if ai else 0))
        equations.append(byte[6]^(C if bi else 0))
    qbits=[C if ai & bi else 0 for ai,bi in classes[r]]
    state=sym_mix_shift([sym_sbox(e,q) for e,q in zip(effective,qbits)])
for i,(v,rk,c) in enumerate(zip(state,rks[10],cts[0])):
    for bit in range(8):
        equations.append(v[bit]^rk[bit]^(C if (c>>bit&1) else 0))

# GF(2) system (448 equations, 168 variables)
pivots={}
for row in equations:
    while True:
        coef=row & (C-1)
        if not coef:
            if row & C: raise RuntimeError('inconsistent linear equations')
            break
        pivot=(coef & -coef).bit_length()-1
        if pivot in pivots: row ^= pivots[pivot]
        else:
            pivots[pivot]=row
            break
print(f'[+] Linear system rank {len(pivots)} of {N} variables')
if len(pivots)<N:
    print('[!] Remaining unknowns:',[i for i in range(N) if i not in pivots])
    raise RuntimeError('insufficient rank; nonlinear post-processing needed')
sol=0
for pivot in sorted(pivots,reverse=True):
    r=pivots[pivot]
    known=(r&(C-1)) & ~(1<<pivot)
    v=((r & C)!=0) ^ ((known & sol).bit_count()%2==1)
    if v: sol |= (1<<pivot)
assert all((r & (C-1) & sol).bit_count()%2 == bool(r & C) for r in equations)
base_key=bytes(sum(((sol>>(8*i+j))&1)<<j for j in range(8)) for i in range(16))
print('[+] Recovered base key:',base_key.hex())
# Check that each linearization placeholder indeed equals the actual SBOX nonlinear term.
for r in range(1,11):
    rotated=words[4*r-1][1:]+words[4*r-1][:1]
    for j,symbyte in enumerate(rotated):
        val=sum((1<<bit) if ((exp&(C-1)&sol).bit_count()%2)^bool(exp&C) else 0 for bit,exp in enumerate(symbyte))
        actual=nonlin(val)
        assumed=(sol>>(128+4*(r-1)+j))&1
        assert actual==assumed,('schedule nonlinear mismatch',r,j,actual,assumed)
print('[+] Key schedule verified')

cipher=DeCipher(base_key)
for i,(pt,ct) in enumerate(zip(pts,cts)):
    assert cipher.encrypt(pt.tobytes()) == ct.tobytes(),f'failed ciphertext {i}'
assert np.array_equal(cipher.gen_proofs(),proofs)
print('[+] Ciphertexts and every proof verified')
AES_key=sha256(key_schedule(base_key).tobytes()).digest()
flag=AESGCM(AES_key).decrypt(b'\0'*12,f['flag'].tobytes(),None)
print('[+] FLAG:',flag.decode())