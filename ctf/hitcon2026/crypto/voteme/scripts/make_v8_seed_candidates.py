from v8_uuid import recover,seed_bytes
import json,struct,sys,time
votes=json.load(open('/mnt/data/voteme_work/votes.json'))['votes']
N=int(sys.argv[1]) if len(sys.argv)>1 else 423
with open('/mnt/data/voteme_work/seed_candidates.bin','wb') as f:
 f.write(struct.pack('<I',N))
 for i,v in enumerate(votes[:N]):
  b,free=recover(v['uuid'])
  assert len(free)==6
  for guess in range(64):
   state=b
   for j in range(6):
    if (guess>>j)&1: state^=free[j]
   seed,bytes_ = seed_bytes(state)
   f.write(seed)
print('generated',N*64,'candidates')
