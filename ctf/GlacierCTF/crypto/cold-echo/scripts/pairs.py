import json
d=json.load(open('S.json')); p=d['p']
roots=json.load(open('roots.json')); rs=set(roots)
print("1 in roots:",1 in rs,"p-1 in roots:",(p-1) in rs)
seen=set(); pairs=[]
for r in roots:
    if r in seen: continue
    ir=pow(r,-1,p)
    assert ir in rs,(r,"no inverse in roots")
    pairs.append((r,ir)); seen.add(r); seen.add(ir)
print("pairs:",len(pairs))
json.dump(pairs,open('pairs.json','w'))
