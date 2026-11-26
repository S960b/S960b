import json, hashlib, base64
A=json.load(open('archive.json'))
p=A['prime']; deg=A['degree']; tr=A['trace']; batch=A['batch']
d=json.load(open('S.json')); S=[x%p for x in d['S']]
pairs=json.load(open('pairs.json')); sols=json.load(open('sols.json'))
t,f=A['forward']
def ev(a,x):
    s=0
    for co in reversed(a): s=(s*x+co)%p
    return s
def poly_mul(a,b):
    r=[0]*(len(a)+len(b)-1)
    for i,ai in enumerate(a):
        if ai:
            for j,bj in enumerate(b):
                if bj: r[i+j]=(r[i+j]+ai*bj)%p
    return r
def build(mask):
    roots=[pairs[i][(mask>>i)&1] for i in range(len(pairs))]
    P=[1]
    for r in roots: P=poly_mul(P,[(-r)%p,1])
    den=1
    for r in roots: den=den*((1-r)%p)%p
    ain=pow(den,-1,p)
    return [c*ain%p for c in P]
def rev_poly(P):
    return [P[deg-i] for i in range(deg+1)]
def message(P):
    lines=["GLACIER-COLD-ECHO-v1",batch,f"{p} {deg}"]
    for u,r in tr: lines.append(f"{u} {r}")
    lines.append(f"{t} {f}")
    lines.append(" ".join(str(c) for c in P))
    return "\n".join(lines)+"\n"
from ecdsa import VerifyingKey,BadSignatureError
import ecdsa
vk=VerifyingKey.from_pem(A['public_key_pem'])
sig=base64.b64decode(A['receipt_der_b64'])
found=None
for mask in sols:
    P=build(mask)
    if len(P)!=deg+1: continue
    if ev(P,1)!=1 or ev(P,t)!=f: continue
    if poly_mul(P,rev_poly(P))!=S: continue
    msg=message(P)
    try:
        if vk.verify(sig,msg.encode(),hashfunc=hashlib.sha256,sigdecode=ecdsa.util.sigdecode_der):
            found=(mask,P,msg); break
    except BadSignatureError: pass
    except Exception: pass
print("found:",found is not None)
if found:
    mask,P,msg=found
    print(msg)
    maskb=base64.b64decode(A['flag_mask_b64'])
    pad=hashlib.sha256(b"cold-echo-flag-pad-v1\n"+msg.encode()).digest()
    out=bytes(a^b for a,b in zip(maskb,pad))
    flag="gctf{"+base64.urlsafe_b64encode(out).decode().rstrip("=")+"}"
    print("FLAG:",flag)
    json.dump({"msg":msg,"flag":flag,"P":P},open('cold_echo_result.json','w'))
