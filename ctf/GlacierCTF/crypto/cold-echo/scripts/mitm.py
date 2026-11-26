import json
A=json.load(open('archive.json'))
d=json.load(open('S.json')); p=d['p']
pairs=json.load(open('pairs.json'))
t=A['forward'][0]; f=A['forward'][1]
def fac(x): return (t-x)%p*pow((1-x)%p,-1,p)%p
opts=[(fac(a),fac(b)) for a,b in pairs]
half=len(opts)//2; L=opts[:half]; R=opts[half:]
def enum(lst):
    prods={1:0}
    for i,(o0,o1) in enumerate(lst):
        new={}
        for pr,mask in prods.items():
            new[(pr*o0)%p]=mask
            new[(pr*o1)%p]=mask|(1<<i)
        prods=new
    return prods
print("enumerating left",2**len(L))
Lp=enum(L); print("left size",len(Lp))
sols=[]
for pr,mask in enum(R).items():
    need=f*pow(pr,-1,p)%p
    if need in Lp: sols.append(Lp[need] | (mask<<len(L)))
print("solutions:",len(sols))
json.dump(sols,open('sols.json','w'))
