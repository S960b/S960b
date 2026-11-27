import json
L=json.load(open('lin.json')); p=L['p']; deg=L['deg']
xp=L['xp']; ns=L['ns'][0]
def W(v): return (v[0]+2*sum(v[1:]))%p
lam=(1-W(xp))*pow(W(ns)%p,-1,p)%p
c=[(xp[i]+lam*ns[i])%p for i in range(deg+1)]
print("lambda=",lam)
print("S(1) check:",W(c)%p)
S=[c[abs(k-deg)] for k in range(2*deg+1)]
print("len S",len(S),"leading",S[-1],"trailing",S[0])
json.dump({"c":c,"S":S,"p":p,"deg":deg},open('S.json','w'))
