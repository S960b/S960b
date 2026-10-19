#!/usr/bin/env python3
"""RSA high bits known, Coppersmith/Howgrave-Graham, no Sage.

Default: guess top 5 of the 506 unknown prime bits, reduce 59-dim lattices
via bundled flatter from `pip install flatn`, recover roots using modular gcd+CRT.

For each candidate prefix, all calculations are exact integers; factor verification
is exact. This script is research software: no claimed guarantee or runtime.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import json
import math
import os
from pathlib import Path
import sys
import time
from functools import lru_cache

if hasattr(sys, 'set_int_max_str_digits'):
    sys.set_int_max_str_digits(0)

# Flatter's reference optimal lattice dimensions for 2048 bit N.
# Table column is an unknown-bit threshold; request one spare bit by default.
PARAM_TABLE = [
    (340,1,2), (408,2,3), (437,3,4), (454,4,5),
    (464,5,6), (471,6,7), (476,7,8), (480,8,9),
    (484,9,11), (486,10,11), (488,11,12), (490,12,13),
    (492,13,15), (493,14,15), (494,15,16), (495,16,17),
    (496,17,18), (497,18,19), (498,20,21), (499,21,22),
    (500,23,24), (501,26,27), (502,29,30), (503,32,33),
    (504,37,38), (505,43,44), (506,52,53), (507,65,66),
    (508,87,88), (509,132,133), (510,271,273),
]

def choose_params(unknown, safety=1):
    for threshold,k,t in PARAM_TABLE:
        if threshold >= unknown + safety:
            return (k,t)
    raise ValueError(f'Unknown={unknown}: reduce guess bits or add explicit parameters')

def load_data(path):
    vals = list(map(int, Path(path).read_text().split()))
    assert len(vals) >= 3
    ncases, e, flag_len = vals[:3]
    if not (1 <= ncases <= 100 and len(vals)==3+3*ncases):
        raise ValueError(f'Expected header + 3*instances numbers; got {len(vals)}')
    cases = []
    for i in range(ncases):
        n,c,h=vals[3+i*3:6+i*3]
        if not (n>0 and 0<=c<n and h>0):
            raise ValueError('Malformed instance')
        cases.append((n,c,h))
    return e,flag_len,cases

def lattice(n, prefix, unknown, k, t):
    """Row lattice: N^(k-i)(prefix+x)^i, x^j(prefix+x)^k, at x=X*z."""
    d=k+t
    X=1<<unknown
    xpow=[1]*d
    for j in range(1,d): xpow[j]=xpow[j-1]*X
    Npow=[1]*(k+1)
    Ppow=[1]*(k+1)
    for i in range(1,k+1):
        Npow[i]=Npow[i-1]*n
        Ppow[i]=Ppow[i-1]*prefix
    rows=[]
    for i in range(k):
        row=[0]*d
        scale=Npow[k-i]
        for j in range(i+1):
            row[j]=scale*math.comb(i,j)*Ppow[i-j]*xpow[j]
        rows.append(row)
    for i in range(t):
        row=[0]*d
        for j in range(k+1):
            row[i+j]=math.comb(k,j)*Ppow[k-j]*xpow[i+j]
        rows.append(row)
    return rows

def reduce_basis(rows, backend, delta, precision, bkz):
    if backend == 'flatn':
        import flatn
        # flatter quality is approximate to delta, not identical to classical LLL.
        return flatn.reduce(rows, delta=delta)
    if backend == 'fpylll':
        from fpylll import IntegerMatrix, LLL, BKZ, FPLLL
        d=len(rows)
        mat=IntegerMatrix.from_matrix(rows)
        # mpfr for GSO reliability; proved reduction can be vastly slower.
        FPLLL.set_precision(precision)
        LLL.reduction(mat, delta=delta, eta=0.51,
                      method='heuristic', float_type='mpfr', precision=precision)
        if bkz:
            BKZ.reduction(mat, BKZ.Param(block_size=bkz, max_loops=2,
                                         flags=BKZ.MAX_LOOPS))
        return [[int(mat[i,j]) for j in range(d)] for i in range(d)]
    if backend == 'sympy':  # small synthetic self test only
        import sympy as s
        return [list(map(int,row)) for row in s.Matrix(rows).lll(delta=s.Rational(3,4)).tolist()]
    raise ValueError('Unknown backend')

def trim(a):
    while a and a[-1]==0:
        a.pop()
    return a

def poly_rem(a,b,p):
    a=trim(a[:]); b=trim(b[:]);
    if not b: raise ZeroDivisionError('polynomial division by zero')
    inv=pow(b[-1],-1,p)
    while len(a)>=len(b) and a:
        fac=a[-1]*inv%p
        if fac:
            off=len(a)-len(b)
            for i in range(len(b)-1):
                a[off+i]=(a[off+i]-fac*b[i])%p
        a.pop()   # highest coefficient becomes 0
        trim(a)
    return a

def poly_gcd(a,b,p):
    a=trim([v%p for v in a]);b=trim([v%p for v in b])
    while b:
        a,b=b,poly_rem(a,b,p)
    if not a: return []
    inv=pow(a[-1],-1,p)
    return [(v*inv)%p for v in a]

def prime_stream():
    import sympy as sp
    base=(1<<61)+1048576
    j=0
    while True:
        yield int(sp.nextprime(base+j*10000019))
        j+=1

def try_roots(red, n, prefix, unknown, top=6):
    X=1<<unknown
    d=len(red)
    xp=[1]*d
    for i in range(1,d):xp[i]=xp[i-1]*X
    candidates=[]
    for i,row in enumerate(red[:min(top,d)]):
        # basis scaled x->X*x, descaling must be exact for every reduced row
        if any(row[j]%xp[j] for j in range(d)):
            raise AssertionError('LLL output does not belong to scaled lattice')
        candidates.append([int(row[j]//xp[j]) for j in range(d)])
    # Some rows might be multiples of the same polynomial; use all gcds.
    for ia in range(len(candidates)):
        for ib in range(ia+1,len(candidates)):
            a,b=candidates[ia],candidates[ib]
            crt_x,crt_mod=0,1
            okay=True
            for iteration,q in enumerate(prime_stream()):
                if iteration >= 12:
                    okay=False; break
                g=poly_gcd(a,b,q)
                if len(g)!=2:
                    okay=False; break
                residue=(-g[0])%q  # gcd normalized to x+constant
                crt_x += crt_mod*((residue-crt_x)*pow(crt_mod,-1,q)%q)
                crt_mod *= q
                if crt_mod>X:
                    if crt_x < X:
                        fact=prefix+crt_x
                        if fact>1 and n%fact==0:
                            return fact,crt_x,(ia,ib)
                    okay=False
                    break
            if okay:
                continue
    return None

def work(job):
    index,guess,n,h,unknown,shift,k,t,backend,delta,precision,bkz,top=job
    start=time.monotonic()
    base=(h<<shift)+(guess<<unknown)
    try:
        R=lattice(n,base,unknown,k,t)
        red=reduce_basis(R,backend,delta,precision,bkz)
        # Sufficient Howgrave-Graham bound: |H(x0)| <= sum |row_j| < p^k.
        bound=base**k
        cert=sum(1 for row in red[:min(top,len(red))] if sum(map(abs,row))<bound)
        short_bits=min(sum(map(abs,row)).bit_length() for row in red[:min(top,len(red))])
        result=try_roots(red,n,base,unknown,top=top)
        return {'instance':index,'guess':guess,'p':result[0] if result else None,
                'root':result[1] if result else None,
                'pair':result[2] if result else None,
                'seconds':round(time.monotonic()-start,2),'error':None,
                'certified_rows':cert,'margin_bits':bound.bit_length()-short_bits}
    except Exception as e:
        return {'instance':index,'guess':guess,'p':None,'root':None,'pair':None,
                'seconds':round(time.monotonic()-start,2),
                'error':f'{type(e).__name__}: {e}'}

def decrypt_case(n,c,p,e):
    assert n%p==0
    q=n//p
    phi=(p-1)*(q-1)
    d=pow(e,-1,phi)
    msg=pow(c,d,n)
    # The payload is 32 bytes: 24 random bytes followed by 8 flag bytes.
    chunk=(msg&((1<<64)-1)).to_bytes(8,'big')
    return q,chunk

def save_results(path, e,flag_len,records):
    result={'e':e,'flag_len':flag_len, 'instances':records}
    tmp=Path(str(path)+'.tmp')
    tmp.write_text(json.dumps(result,indent=2,ensure_ascii=False))
    tmp.replace(path)

def selftest():
    import sympy as sp
    print('Synthetic self-test (48-bit factors, 19 unknown bits)',flush=True)
    p=int(sp.nextprime((1<<47)+(1<<44)+7654321))
    q=int(sp.nextprime((1<<47)+(1<<45)+23456789))
    n=p*q; shift=19; prefix=(p>>shift)<<shift
    k,t=3,4
    B=lattice(n,prefix,shift,k,t)
    red=reduce_basis(B,'sympy',0.75,128,0)
    result=try_roots(red,n,prefix,shift,top=6)
    assert result and result[0]==p, ('small-root recovery failed',result,p)
    payload=int.from_bytes(b'FLAGTEST','big')
    e=65537
    ciphertext=pow(payload,e,n)
    q2,chunk=decrypt_case(n,ciphertext,result[0],e)
    assert q2==q and chunk==b'FLAGTEST'
    print(f'SELFTEST PASS: factor recovery + RSA decrypt, p={p}, q={q}, dim={k+t}',flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--input',default='output.txt')
    parser.add_argument('--backend',choices=['flatn','fpylll'],default='flatn')
    parser.add_argument('--guess-bits',type=int,default=5,help='Guess most significant missing prime bits (2^g candidates/instance)')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--delta',type=float,default=0.99)
    parser.add_argument('--precision',type=int,default=384,help='fpylll MPFR precision')
    parser.add_argument('--bkz',type=int,default=0,help='fpylll BKZ block size after LLL; optional')
    parser.add_argument('--m',type=int,default=0,help='override number of N/f powers')
    parser.add_argument('--t',type=int,default=0,help='override number of shifted top-power polys')
    parser.add_argument('--safety',type=int,default=1,choices=[0,1])
    parser.add_argument('--instance',default='all',help='all or index 0..3')
    parser.add_argument('--first-guess',type=int,default=0)
    parser.add_argument('--last-guess',type=int,default=-1,help='inclusive; -1 means all')
    parser.add_argument('--out',default='rsa_progress.json')
    parser.add_argument('--selftest',action='store_true')
    parser.add_argument('--top-rows',type=int,default=8)
    args=parser.parse_args()
    if args.selftest:
        selftest();return
    if not (0<=args.guess_bits<=12):parser.error('guess-bits in [0,12]')
    if not(0.25<args.delta<=1):parser.error('delta in (0.25,1]')
    if args.backend=='flatn':
        try:import flatn
        except ImportError: parser.error('Install faster reducer: python3 -m pip install --user flatn==0.3.5')
    else:
        try:import fpylll
        except ImportError:parser.error('fpylll not importable')
    e,flag_len,cases=load_data(args.input)
    ncases=len(cases)
    chunk_lens=[(flag_len//ncases)+(i<flag_len%ncases) for i in range(ncases)]
    if any(sz>8 for sz in chunk_lens):
        parser.error('This challenge-specific script assumes each chunk at most 8 bytes')
    unknown=506-args.guess_bits
    if unknown<1: parser.error('Too many guessed bits')
    k,t=choose_params(unknown,args.safety)
    if args.m:k=args.m
    if args.t:t=args.t
    candidates=1<<args.guess_bits
    stop=args.last_guess if args.last_guess>=0 else candidates-1
    indices=list(range(ncases)) if args.instance=='all' else [int(args.instance)]
    if any(i<0 or i>=ncases for i in indices):parser.error('Bad instance')
    config=f'guess{args.guess_bits}-k{k}-t{t}-{args.backend}-delta{args.delta}-prec{args.precision}-bkz{args.bkz}'
    path=Path(args.out)
    rec=[{'p':None,'chunk_hex':None,'tried':{}} for _ in cases]
    if path.exists():
        try:
            old=json.loads(path.read_text())
            if old['e']==e and old['flag_len']==flag_len and len(old['instances'])==ncases:
                rec=old['instances']
        except (ValueError,KeyError,TypeError):pass
    for i,(n,c,h) in enumerate(cases):
        if rec[i].get('p'):
            p=int(rec[i]['p']);
            if n%p: rec[i]['p']=None
            else:
                _,chunk=decrypt_case(n,c,p,e)
                rec[i]['chunk_hex']=chunk[-chunk_lens[i]:].hex()
    pending_jobs=[]
    for i in indices:
        if rec[i].get('p'):continue
        tried=set(rec[i].get('tried',{}).get(config,[]))
        for guess in range(args.first_guess,min(stop,candidates-1)+1):
            if guess not in tried:
                n,c,h=cases[i]
                pending_jobs.append((i,guess,n,h,unknown,506,k,t,args.backend,
                                     args.delta,args.precision,args.bkz,args.top_rows))
    # Round-robin over instances; one candidate per instance first.
    pending_jobs.sort(key=lambda a:(a[1],a[0]))
    print(f'RSA 2048x{ncases}: unknown={unknown}, prefix-guess={args.guess_bits}, '
          f'dim={k+t}(k={k},t={t}), backend={args.backend}, jobs={len(pending_jobs)}, '
          f'workers={args.workers}',flush=True)
    start=time.monotonic()
    done=0; failures=0
    os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
    os.environ['MKL_NUM_THREADS']='1'
    def log_completed(res):
        nonlocal done,failures
        done+=1
        i=int(res['instance']);guess=int(res['guess'])
        if res.get('error'):
            failures+=1
            print(f'[{done}/{len(pending_jobs)}] instance {i} guess {guess}: '
                  f'ERROR {res["error"]} ({res["seconds"]} s)',flush=True)
            return
        print(f'[{done}/{len(pending_jobs)}] instance {i} guess {guess}: '
              f'{"FACTOR" if res["p"] else "no root"} ({res["seconds"]} s), '
              f'cert={res["certified_rows"]}, HG margin={res["margin_bits"]} bits',flush=True)
        rec[i].setdefault('tried',{}).setdefault(config,[]).append(guess)
        if res['p'] and not rec[i].get('p'):
            p=int(res['p']);n,c,_=cases[i]
            q,chunk=decrypt_case(n,c,p,e)
            rec[i]['p']=str(p);rec[i]['q']=str(q)
            rec[i]['chunk_hex']=chunk[-chunk_lens[i]:].hex()
            print(f'  FACTOR VERIFIED n%p=0, instance={i}, q_bits={q.bit_length()}, '
                  f'chunk={chunk!r} (prefix bits guess={guess})',flush=True)
        save_results(path,e,flag_len,rec)
    if pending_jobs:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            jobs_iter=iter(pending_jobs)
            active={}
            for _ in range(min(args.workers,len(pending_jobs))):
                x=next(jobs_iter)
                active[ex.submit(work,x)]=x
            while active:
                ready,_=wait(active,return_when=FIRST_COMPLETED)
                for future in ready:
                    job=active.pop(future)
                    res=future.result()
                    log_completed(res)
                    # If factor found, do not schedule another guess for that instance.
                    while True:
                        x=next(jobs_iter,None)
                        if x is None:break
                        if not rec[x[0]].get('p'):
                            active[ex.submit(work,x)]=x
                            break
    save_results(path,e,flag_len,rec)
    chunks=[]
    for i,r in enumerate(rec):
        if not r.get('p'):
            print(f'UNSOLVED instance {i}: try --guess-bits 6 or --delta 0.995',flush=True)
            continue
        chunks.append((i, bytes.fromhex(r['chunk_hex'])))
    if len(chunks)==ncases:
        flag=b''.join(b for _,b in chunks)
        print('FLAG bytes:',repr(flag))
        print('FLAG:',flag.decode('utf-8','replace'))
    else:
        print('Partial chunks:',[(i,repr(c)) for i,c in chunks])
    print(f'Elapsed {round(time.monotonic()-start,1)} sec, jobs failed={failures}; state: {path}')

if __name__=='__main__':
    main()
