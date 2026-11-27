import json, random
d=json.load(open('S.json')); p=d['p']
h=[x%p for x in json.load(open('h.json'))['h']]
def trim(a):
    a=[x%p for x in a]
    while a and a[-1]==0: a.pop()
    return a
def pmul(a,b):
    if not a or not b: return []
    r=[0]*(len(a)+len(b)-1)
    for i,ai in enumerate(a):
        if ai:
            for j,bj in enumerate(b):
                if bj: r[i+j]=(r[i+j]+ai*bj)%p
    return trim(r)
def pdivmod(a,b):
    a=trim(a[:]); inv=pow(b[-1],-1,p)
    q=[0]*max(0,len(a)-len(b)+1)
    while a and len(a)>=len(b):
        coef=a[-1]*inv%p; sh=len(a)-len(b); q[sh]=coef
        for i,bi in enumerate(b): a[i+sh]=(a[i+sh]-coef*bi)%p
        a=trim(a)
    return trim(q),a
def pmod(a,m): return pdivmod(a,m)[1]
def pgcd(a,b):
    a=trim(a); b=trim(b)
    while b: a,b=b,pmod(a,b)
    if a:
        inv=pow(a[-1],-1,p); a=[x*inv%p for x in a]
    return a
def ppowmod(base,e,m):
    r=[1]; base=pmod(base,m)
    while e:
        if e&1: r=pmod(pmul(r,base),m)
        base=pmod(pmul(base,base),m); e>>=1
    return r
def split(f):
    if len(f)-1<=1: return [f] if f else []
    for _ in range(300):
        a=random.randrange(p); g=[a,1]
        t=ppowmod(g,(p-1)//2,f)
        t=(t+[0]*max(0,len(f)-len(t)))[:len(f)]
        t=[(t[i]-(1 if i==0 else 0))%p for i in range(len(f))]
        gg=pgcd(f,t); n=len(gg)-1
        if 0<n<len(f)-1:
            q,r=pdivmod(f,gg)
            assert not r
            return split(gg)+split(q)
    raise RuntimeError("split failed deg %d"%(len(f)-1))
roots=[]; stack=[h]
while stack:
    f=stack.pop()
    if not f or len(f)-1==0: continue
    if len(f)-1==1:
        roots.append((-f[0]*pow(f[1],-1,p))%p)
    else: stack.extend(split(f))
Sfull=trim([x%p for x in d['S']])
def ev(a,x):
    s=0
    for co in reversed(a): s=(s*x+co)%p
    return s
print("roots:",len(roots),"unique:",len(set(roots)),
      "bad:",sum(1 for r in roots if ev(Sfull,r)!=0))
json.dump(sorted(set(roots)),open('roots.json','w'))
