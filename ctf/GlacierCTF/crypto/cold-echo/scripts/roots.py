import json
d=json.load(open('S.json')); p=d['p']; deg=d['deg']; S=[x%p for x in d['S']]
while S and S[-1]==0: S.pop()
def trim(a):
    a=[x%p for x in a]
    while a and a[-1]==0: a.pop()
    return a
def deg_(a): return len(a)-1
def pmul(a,b):
    if not a or not b: return []
    r=[0]*(len(a)+len(b)-1)
    for i,ai in enumerate(a):
        if ai:
            for j,bj in enumerate(b):
                if bj: r[i+j]=(r[i+j]+ai*bj)%p
    return trim(r)
def pmod(a,m):
    a=trim(a[:]); dm=deg_(m); inv=pow(m[-1],-1,p)
    while len(a)-1>=dm and a:
        coef=a[-1]*inv%p; sh=len(a)-1-dm
        for i,mi in enumerate(m): a[i+sh]=(a[i+sh]-coef*mi)%p
        a=trim(a)
    return a
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
Sp=[(i*S[i])%p for i in range(1,len(S))]
g=pgcd(S,Sp); print("gcd(S,S') deg",deg_(g))
X=[0,1]
xp=ppowmod(X,p,S)
difference=[((xp[i] if i<len(xp) else 0)-(X[i] if i<len(X) else 0))%p
            for i in range(max(len(X),len(xp)))]
h=pgcd(S,difference)
print("deg gcd(S,x^p-x) =",deg_(h))
json.dump({"h":h},open('h.json','w'))
