import json
A=json.load(open('archive.json'))
p=A['prime']; deg=A['degree']; tr=A['trace']
rows=[]
for u,r in tr:
    ui=pow(u,-1,p); row=[1]; up=1; uip=1
    for d in range(1,deg+1):
        up=up*u%p; uip=uip*ui%p; row.append((up+uip)%p)
    rows.append(row+[r%p])
m=len(rows); n=deg+1
piv=[]; r=0
for c in range(n):
    pr=next((i for i in range(r,m) if rows[i][c]%p),None)
    if pr is None: continue
    rows[r],rows[pr]=rows[pr],rows[r]
    inv=pow(rows[r][c],-1,p)
    rows[r]=[x*inv%p for x in rows[r]]
    for i in range(m):
        if i!=r and rows[i][c]%p:
            fac=rows[i][c]
            rows[i]=[(a-fac*bb)%p for a,bb in zip(rows[i],rows[r])]
    piv.append(c); r+=1
    if r==m: break
print("rank",r,"of",n)
free=[c for c in range(n) if c not in piv]
print("free cols:",free)
xp=[0]*n
for i,c in enumerate(piv): xp[c]=rows[i][n]
ns=[]
for fc in free:
    v=[0]*n; v[fc]=1
    for i,c in enumerate(piv): v[c]=(-rows[i][fc])%p
    ns.append(v)
print("nullspace dim",len(ns))
json.dump({"xp":xp,"ns":ns,"p":p,"deg":deg},open('lin.json','w'))
bad=0
for u,r_ in tr:
    ui=pow(u,-1,p); s=xp[0]; up=1; uip=1
    for d in range(1,deg+1):
        up=up*u%p; uip=uip*ui%p
        s=(s+xp[d]*((up+uip)%p))%p
    if s!=r_%p: bad+=1
print("verify bad:",bad)
