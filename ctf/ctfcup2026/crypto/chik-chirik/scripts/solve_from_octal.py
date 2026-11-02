#!/usr/bin/env python3
"""Usage: python3 solve_from_octal.py capture.wav.octal.txt"""
import sys,zlib

def mul(a,b):
 r=0
 while b:
  if b&1:r^=a
  a<<=1
  if a&8:a^=11
  b>>=1
 return r

def encode(a,b,c):
 return tuple(a^mul(b,x)^mul(c,mul(x,x)) for x in range(1,8))

decode={encode(a,b,c)[:3]:(a,b,c) for a in range(8) for b in range(8) for c in range(8)}
assert len(decode)==512

def prng(s,n):
 a,b,c,d=s
 for _ in range(n):
  t=(b<<9)&0xffffffff
  c^=a;d^=b;b^=c;a^=d;c^=t
  d=((d<<11)|(d>>21))&0xffffffff
  yield ((d&0xa361c95b).bit_count()^(b&0x7d284e13).bit_count())&1

def solve(raw):
 # Each symbol reveals first two of three consecutive PRNG bits.
 obs=[v for q in raw for v in ((q>>2)&1,(q>>1)&1)]
 rows=[0]*len(obs)
 for j in range(128):
  s=[0]*4;s[j//32]=1<<(j%32)
  p=prng(s,3*len(raw))
  for i in range(len(raw)):
   if next(p):rows[2*i]|=1<<j
   if next(p):rows[2*i+1]|=1<<j
   next(p)
 pivots={};mask=(1<<128)-1
 for row,v in zip(rows,obs):
  x=row|(v<<128)
  while x&mask:
   k=(x&mask).bit_length()-1
   if k in pivots:x^=pivots[k]
   else:pivots[k]=x;break
  if x==(1<<128):raise ValueError('inconsistent leakage')
 print('GF2 rank',len(pivots))
 assert len(pivots)==128
 state=0
 for k in sorted(pivots):
  e=pivots[k]
  v=((e>>128)&1)^((e & ((1<<k)-1) & state).bit_count()&1)
  state|=v<<k
 s=[(state>>(32*i))&0xffffffff for i in range(4)]
 assert all((row&state).bit_count()%2==v for row,v in zip(rows,obs))
 print('recovered state:',*[f'{x:08x}' for x in s])
 return s

def main(path):
 t=open(path).read().strip(); assert all(c in '01234567' for c in t)
 o=list(map(int,t));assert (len(o)-8)%7==0 and len(o)>8
 inv=(1,3,0,2)
 code=[2*inv[q&3]+(q>>2) for q in o[8:]]
 raw=[]
 for i in range(0,len(code),7):
  w=tuple(code[i:i+7]);abc=decode[w[:3]]
  assert encode(*abc)==w, ('RS error',i//7)
  raw.extend(abc)
 blocks=len(code)//7
 lengths=[L for L in range(8,4097) if (8*(L+4)+2)//3==blocks]
 assert len(lengths)==1, lengths
 L=lengths[0];raw=raw[:8*(L+4)]
 print('blocks',blocks,'payload bytes',L,'raw data bits',len(raw))
 s=solve(raw);p=prng(s,3*len(raw));bits=[];prev=0
 for q in raw:
  a,b,c=next(p),next(p),next(p)
  assert q>>1==(a<<1|b)
  x=q&1;bits.append(x^prev^c);prev=x
 data=bytes(int(''.join(map(str,bits[i:i+8])),2) for i in range(0,len(bits),8))
 payload,crc=data[:-4],data[-4:]
 print('CRC',crc.hex(),'computed',zlib.crc32(payload).to_bytes(4,'little').hex())
 assert crc==zlib.crc32(payload).to_bytes(4,'little'),'CRC mismatch'
 print('FLAG',payload.decode('utf-8'))
 open('chirp_flag.txt','wb').write(payload+b'\n')

if __name__=='__main__':main(sys.argv[1])
