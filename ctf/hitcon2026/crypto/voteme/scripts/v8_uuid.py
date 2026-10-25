import json,itertools
M=(1<<64)-1

# V8 xorshift128, version Node24.14, random number from state0, 64 cached in forward order, consumed backwards.
def adv(s0,s1):
 a=s0;b=s1
 s0=b
 a^=(a<<23)&M
 a^=a>>17
 a^=b
 a^=b>>26
 return s0,a&M

# build symbolic equations with integer bit vectors over 128 unknown bits.
s0=[1<<i for i in range(64)]
s1=[1<<(i+64) for i in range(64)]
rows=[]
for i in range(16):
 a=s0; b=s1
 s0=b.copy()
 z=[a[j]^a[j-23] if j>=23 else a[j] for j in range(64)]
 z=[z[j]^(z[j+17] if j+17<64 else 0) for j in range(64)]
 z=[z[j]^b[j]^(b[j+26] if j+26<64 else 0) for j in range(64)]
 s1=z
 rows.append(s0[56:64]);

def recover(uuid):
 bs=bytes.fromhex(uuid.replace('-',''))
 es=[]
 for i in range(16):
  mask=(15 if i==6 else 63 if i==8 else 255)
  for j in range(8):
   if mask & (1<<j):es.append((rows[i][j],(bs[i]>>j)&1))
 piv={}
 for vec,answer in es:
  pair=vec|(answer<<128)
  while pair & ((1<<128)-1):
   p=((pair & ((1<<128)-1)).bit_length()-1)
   if p in piv:pair^=piv[p]
   else:piv[p]=pair;break
  else:
   if pair>>128:raise ValueError('contradiction')
 rank=len(piv);free=[i for i in range(128) if i not in piv]
 base=0;vectors=[]
 def backsub(freed):
  sol=freed
  for k in sorted(piv):
   rhs=piv[k]>>128
   rhs^=((piv[k]&sol).bit_count()&1)
   if rhs:sol^=1<<k
  return sol
 base=backsub(0)
 vectors=[backsub(1<<k)^base for k in free]
 return base,vectors


def seed_bytes(s):
 s0=s&M;s1=(s>>64)&M
 # Our variables denote state just BEFORE first emitted state0 from last refill
 seq=[]
 for i in range(16):
  s0,s1=adv(s0,s1)
  # debug observed bytes if wanted
  seq.append(s0>>56)
 for i in range(16,64):s0,s1=adv(s0,s1)
 # Refill after original UUID buffer consumed 256 draws, generates 64 states into cache indexed 0..63
 gen=[]
 for i in range(64):
  s0,s1=adv(s0,s1)
  gen.append(s0)
 # next 32 Math.random read cache 63 downto 32 and multiply*2**32, reduce mod256
 return bytes((gen[i]>>32)&255 for i in range(63,31,-1)),seq

if __name__=='__main__':
 vs=json.load(open('/mnt/data/voteme_work/votes.json'))['votes']
 for i in [0,1,419,420,421]:
  uuid=vs[i]['uuid'];base,basis=recover(uuid)
  print('vote',i,'uuid',uuid,'rank',128-len(basis),'free',len(basis),'first seed',seed_bytes(base)[0].hex())
  print('check',seed_bytes(base)[1],list(bytes.fromhex(uuid.replace('-',''))))
