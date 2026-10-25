#!/usr/bin/env python3
import struct,sys,time
N=624;M=397;A=0x9908b0df
DIM=N*32; WB=(DIM+1+7)//8
obs=[int(l.split()[1]) for l in open('/mnt/data/voteme_work/observed_lowbits.txt')]
# 624 symbolic untempered MT words at output position zero (32 basis vectors each)
state=[[1<<(32*i+j) for j in range(32)] for i in range(N)]

def twist():
 global state
 # original algorithm updates in-place; m=397, n=624
 for i in range(N):
  cur=state[i]; nex=state[(i+1)%N]; ori=state[(i+M)%N]
  lo=nex[0]
  v=[]
  for j in range(32):
   y=(nex[j+1] if j<30 else cur[31] if j==30 else 0)
   v.append(ori[j] ^ y ^ (lo if (A>>j)&1 else 0))
  state[i]=v

def temper(v):
 v=[v[j]^(v[j+11] if j+11<32 else 0) for j in range(32)]
 v=[v[j]^(v[j-7] if j>=7 and ((0x9d2c5680>>j)&1) else 0) for j in range(32)]
 v=[v[j]^(v[j-15] if j>=15 and ((0xefc60000>>j)&1) else 0) for j in range(32)]
 v=[v[j]^(v[j+18] if j+18<32 else 0) for j in range(32)]
 return v

st=time.time();o=open('/mnt/data/voteme_work/mt_matrix.bin','wb');o.write(struct.pack('<III',DIM, len(obs)*16, WB))
for sample,low in enumerate(obs):
 if sample*8 and (sample*8)%N==0:twist()
 elif (sample*8)%N==0 and sample:twist()
 # N=624 multiple of 8 so no boundary between samples
 bits=temper(state[(sample*8)%N])
 for j in range(16):
  v=bits[j]|(((low>>j)&1)<<DIM)
  o.write(v.to_bytes(WB,'little'))
 if sample%200==0:
  print('sample',sample,'sec',round(time.time()-st,1),flush=True)
o.close();print('Matrix',round(time.time()-st,2),'seconds, bytes',__import__('os').path.getsize(o.name))
