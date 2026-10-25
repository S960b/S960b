import struct,json
s=open('/mnt/data/voteme_work/mt_solution.txt').read().strip()
N=624;M=397;A=0x9908b0df
mt=[sum((ord(s[32*i+j])-48)<<j for j in range(32)) for i in range(N)]
q=1358428025549847357385264159219178433970057909696144578443833691290271744
obs=[int(l.split()[1]) for l in open('/mnt/data/voteme_work/observed_lowbits.txt')]

def twist():
 global mt
 for i in range(N):
  y=(mt[i]&0x80000000)|(mt[(i+1)%N]&0x7fffffff)
  mt[i]=(mt[(i+M)%N]^(y>>1)^(A if y&1 else 0))
  mt[i]&=0xffffffff

def temper(y):
 y^=y>>11;y^=(y<<7)&0x9d2c5680;y^=(y<<15)&0xefc60000;y^=y>>18
 return y&0xffffffff

predict=[]
for i in range(len(obs)):
 words=[]
 for j in range(8):
  index=i*8+j
  if index and index%N==0:twist()
  words.append(temper(mt[index%N]))
 assert words[0]&0xffff==obs[i],(i,words[0]&65535,obs[i])
 raw=int.from_bytes(struct.pack('<8I',*words),'little')
 predict.append(raw%q or 1)
print('all low 16 bits correctly predicted',len(predict))
print('last scalar',predict[-1]);print('next-to-last scalar',predict[-2])
with open('/mnt/data/voteme_work/scalars.txt','w') as f:
 for i,k in enumerate(predict):f.write(f'{i} {k}\n')
