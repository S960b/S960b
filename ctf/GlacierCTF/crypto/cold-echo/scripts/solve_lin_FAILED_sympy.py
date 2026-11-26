import json
A=json.load(open('archive.json'))
p=A['prime']; deg=A['degree']; tr=A['trace']; fwd=A['forward']
print("p=",p,"deg=",deg,"ntrace=",len(tr),"fwd=",fwd)
rows=[]
for u,r in tr:
    ui=pow(u,-1,p); row=[1]; up=1; uip=1
    for d in range(1,deg+1):
        up=up*u%p; uip=uip*ui%p
        row.append((up+uip)%p)
    rows.append((row,r%p))
import sympy as sp
M=sp.Matrix([[x for x in row] for row,_ in rows])
b=sp.Matrix([r for _,r in rows])
print("rank M =",M.rank(),"cols",M.cols,"rows",M.rows)
