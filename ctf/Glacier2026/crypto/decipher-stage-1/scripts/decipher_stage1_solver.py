#!/usr/bin/env python3
import argparse
import sys
import tarfile
import tempfile
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

parser = argparse.ArgumentParser(description='Recover the GlacierCTF DeCipher Stage 1 master key using GF(2) linear algebra.')
parser.add_argument('challenge', nargs='?', default='.', help='Path to decipher-1 directory or original .tar.gz archive')
args = parser.parse_args()
source = Path(args.challenge).expanduser().resolve()
temporary = None
if source.is_file() and source.name.endswith('.tar.gz'):
    temporary = tempfile.TemporaryDirectory(prefix='decipher_stage1_')
    with tarfile.open(source, 'r:gz') as archive:
        archive.extractall(temporary.name, filter='data')
    source = Path(temporary.name)
if source.is_dir():
    found = list(source.rglob('decipher.py'))
    if len(found) != 1:
        parser.error(f'Expected exactly one decipher.py under {source}; found {len(found)}')
    source = found[0].parent
else:
    parser.error('Argument must be a challenge directory or .tar.gz archive')
sys.path.insert(0, str(source))
from decipher import SBOX, DeCipher, gf_mul_by_2, gf_mul_by_3, key_schedule
D = np.load(source / 'output_stage1.npz')
pt=D['plaintexts'];cts=D['ciphertexts'];proof=D['proofs'];
NKEY=128;NSCHED=40;N=NKEY+NSCHED;CONST=1<<N

def L(b):
 return s0 ^ xor_all((linear_bases[i] for i in range(8) if b>>i&1))
def xor_all(vals):
 r=0
 for v in vals:r^=v
 return r
s0=SBOX[0]
linear_bases=[SBOX[1<<i]^s0 for i in range(8)]
def l0(b):return xor_all(linear_bases[i] for i in range(8) if b>>i&1)
errors=set()
for x in range(256):
 e=SBOX[x] ^ s0 ^ l0(x)
 errors.add(e)
print('Sbox linear residual unique',len(errors),[hex(v) for v in sorted(errors)],flush=True)
assert len(errors)==2
q=next(v for v in errors if v)
f=lambda b: int((SBOX[b]^s0^l0(b))!=0)
V=[v for v in range(256) if all(SBOX[x]^SBOX[x^v]==SBOX[0]^SBOX[v] for x in range(256))]
print('V dim size',len(V),[hex(v) for v in V[:12]])
assert len(V)==64
masks=[u for u in range(1,256) if all((u&v).bit_count()%2==0 for v in V)]
print('orthogonal basis candidates', [hex(u) for u in masks]);assert len(masks)==3
m1,m2=masks[:2]

# obtain coset information per round/byte
rep=[]; sel=[]
for r in range(10):
 arr=[]; ts=[]
 for j in range(16):
  obs=[]
  for i in range(1,16):
   dz=bytearray(proof[0,i,r+1].tobytes())
   DeCipher.mix_columns_inv(dz)
   DeCipher.shift_rows_inv(dz)
   d=int(proof[0,i,r,j]); o=dz[j]
   obs.append((d,o))
  cands=[w for w in range(256) if all(SBOX[w]^SBOX[w^d]==o for d,o in obs)]
  if len(cands)!=64:
   print('ERROR CANDS',r,j,len(cands));quit(1)
  if {x^y for x in cands for y in cands}!=set(V):print('not coset');quit(1)
  tset={f(w) for w in cands}
  assert len(tset)==1
  arr.append(cands[0]);ts.append(tset.pop())
 rep.append(arr);sel.append(ts)
print('reps row0',bytes(rep[0]).hex())
print('t row0',sel[0]);print('t row1',sel[1]);

# expression byte = 8 int bitmasks

def xor_byte(a,b):return [x^y for x,y in zip(a,b)]
def const_byte(c):return [CONST if (c>>i)&1 else 0 for i in range(8)]
def var_byte(k):return [1<<(k*8+i) for i in range(8)]
def bmask(b,mask):return xor_all(b[i] for i in range(8) if mask>>i&1)
def lin_bytes(expr,coeff):
 out=[]
 for bit in range(8):
  out.append(xor_all(expr[i] for i in range(8) if (coeff[i]>>bit)&1))
 return out

def gf_mult_byte(expr,k):
 if k==1:return expr
 if k==2: table=gf_mul_by_2
 elif k==3: table=gf_mul_by_3
 else:raise ValueError(k)
 coeff=[int(table[1<<i]) for i in range(8)]
 return lin_bytes(expr,coeff)

# symbols
key=[var_byte(j) for j in range(16)]
words=[key[i:i+4] for i in range(0,16,4)]
for i in range(4,44):
 temp=words[i-1][:]
 if i%4==0:
  temp=temp[1:]+temp[:1]
  temp=[xor_byte(xor_byte(lin_bytes(x,linear_bases),const_byte(s0)), [ (1 << (NKEY + (i//4-1)*4 + j)) if ((q>>bit)&1) else 0 for bit in range(8)]) for j,x in enumerate(temp)]
  temp[0]=xor_byte(temp[0],const_byte([1,2,4,8,16,32,64,128,27,54][i//4-1]))
 words.append([xor_byte(a,b) for a,b in zip(words[i-4],temp)])
rks=[[byte for word in words[r*4:r*4+4] for byte in word] for r in range(11)]
assert all(len(rk)==16 for rk in rks)

rows=[]
def eq(expr): rows.append(expr)
state=[const_byte(int(i)) for i in pt[0]]
for r in range(10):
 w=[xor_byte(a,b) for a,b in zip(state,rks[r])]
 for j in range(16):
  for m in (m1,m2):
   eq(bmask(w[j],m) ^ (CONST if (m&rep[r][j]).bit_count()%2 else 0))
 next_state=[]
 for j in range(16):
  val=xor_byte(lin_bytes(w[j],linear_bases),const_byte(s0 ^ (q if sel[r][j] else 0)))
  next_state.append(val)
 # apply shift rows and mix columns
 DeCipher.shift_rows(next_state)
 for i in range(4):
  j=4*i
  a,b,c,d=next_state[j:j+4]
  next_state[j]=xor_byte(xor_byte(gf_mult_byte(a,2),gf_mult_byte(b,3)),xor_byte(c,d))
  next_state[j+1]=xor_byte(xor_byte(a,gf_mult_byte(b,2)),xor_byte(gf_mult_byte(c,3),d))
  next_state[j+2]=xor_byte(xor_byte(a,b),xor_byte(gf_mult_byte(c,2),gf_mult_byte(d,3)))
  next_state[j+3]=xor_byte(xor_byte(gf_mult_byte(a,3),b),xor_byte(c,gf_mult_byte(d,2)))
 state=next_state
for j in range(16):
 value=xor_byte(state[j],rks[10][j])
 for bit in range(8):
  eq(value[bit] ^ (CONST if (int(cts[0,j])>>bit)&1 else 0))

print('linear system equations=',len(rows),'unknowns=',N,flush=True)
# gaussian elimination GF2
basis={};incons=[]
for row in rows:
 while row:
  i=(row&-row).bit_length()-1
  if i==N:
   incons.append(row);break
  if i not in basis:
   basis[i]=row;break
  row^=basis[i]
print('rank=',len(basis),'inconsistent=',len(incons),'free=',N-len(basis),flush=True)
if incons:
 print('ERROR inconsistency',len(incons));quit(2)
sol=0
for i in sorted(basis,reverse=True):
 row=basis[i]
 rhs=1 if row&CONST else 0
 rhs ^= ((row & sol).bit_count()&1)
 if rhs:sol |= 1<<i
key_bytes=bytes(sum(((sol>>(8*j+i))&1)<<i for i in range(8)) for j in range(16))
print('key=',key_bytes.hex(),flush=True)
c=DeCipher(key_bytes)
for i in range(16):
 cti=c.encrypt(pt[i].tobytes())
 if cti!=cts[i].tobytes():
  print('ciphertext invalid at pt',i,cti.hex(),cts[i].tobytes().hex());break
else: print('VERIFIED ALL CIPHERTEXTS',flush=True)
print('schedule',key_schedule(key_bytes)[0].tobytes().hex(), flush=True)
flag_key=c.decrypt(D['wrapped_key'].tobytes())
print('flag_key=',flag_key.hex(),flush=True)
try:
 flag=AESGCM(flag_key).decrypt(b'\0'*12,D['flag'].tobytes(),None)
 print('FLAG:',flag.decode())
except Exception as e:print('FLAG FAILURE:',repr(e))