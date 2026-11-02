#!/usr/bin/env python3
"""Offline Flygammon engine and original FlyNet / choose_2ply port.
Board is a tuple of 28 integers: points[0:24], cur/opp bar [24:26], cur/opp off [26:28].
Original source: handout/model-src/{engine,features,agent,model}.rs.
Dependencies: numpy. No chain transactions, no network.
"""
from functools import lru_cache
import numpy as np

START = ( -2, 0,0,0,0,5,0,3,0,0,0,-5,5,0,0,0,-3,0,-5,0,0,0,0,2, 0,0,0,0)
N_INPUT = 203
WEIGHTS = (203*80, 80, 80*80, 80, 80*5, 5)
ROLLS = tuple(((a,b),np.float32((1 if a==b else 2)/36)) for a in range(1,7) for b in range(a,7))

@lru_cache(maxsize=300000)
def flip(b):
    return tuple(-b[23-i] for i in range(24)) + (b[25],b[24],b[27],b[26])

@lru_cache(maxsize=300000)
def pip_counts(b):
    return (sum(v*(i+1) for i,v in enumerate(b[:24]) if v>0) + 25*b[24],
            sum((-v)*(24-i) for i,v in enumerate(b[:24]) if v<0) + 25*b[25])

def single(b, d):
    if b[24]:
        t=24-d
        if b[t] <= -2: return
        a=list(b)
        if a[t] == -1: a[t]=0; a[25]+=1
        a[t]+=1; a[24]-=1
        yield tuple(a), 24
        return
    home = not any(x>0 for x in b[6:24])
    for p in range(24):
        if b[p] <= 0: continue
        t=p-d
        if t>=0:
            if b[t] <= -2: continue
            a=list(b)
            if a[t] == -1: a[t]=0; a[25]+=1
            a[t]+=1; a[p]-=1
            yield tuple(a),p
        elif home and not (d>p+1 and any(x>0 for x in b[p+1:6])):
            a=list(b); a[p]-=1; a[26]+=1
            yield tuple(a),p

@lru_cache(maxsize=100000)
def legal_paths(b, dice):
    """Exact path and board enumeration: max dice, high die rule, first representative retained."""
    assert len(b)==28 and len(dice)==2
    rem = (dice[0],)*4 if dice[0]==dice[1] else tuple(dice)
    leaves=[]
    def dfs(board, available, path):
        advanced=False
        done=set()
        for i,d in enumerate(available):
            if d in done: continue  # identical dice produce identical DFS branches
            done.add(d)
            rest=available[:i]+available[i+1:]
            for nxt,src in single(board,d):
                advanced=True
                dfs(nxt,rest,path+((src,d),))
        if not advanced: leaves.append((board,path))
    dfs(b,rem,())
    if not leaves: return ()
    ml=max(len(path) for _,path in leaves)
    if not ml: return ()
    kept=[(end,path) for end,path in leaves if len(path)==ml]
    if ml==1 and dice[0]!=dice[1] and any(path[0][1]==max(dice) for _,path in kept):
        kept=[(end,path) for end,path in kept if path[0][1]==max(dice)]
    seen=set(); out=[]
    for end,path in kept:
        if end not in seen:
            seen.add(end); out.append((end,path))
    return tuple(out)

def encode(b):
    v=np.zeros(N_INPUT,dtype=np.float32)
    for i,c in enumerate(b[:24]):
        if c==0: continue
        off=i*8 + (4 if c<0 else 0)
        n=abs(c)
        v[off]=1
        if n>=2: v[off+1]=1
        if n>=3: v[off+2]=1
        if n>3: v[off+3]=np.float32((n-3)/2)
    v[192:196]=(b[24]/2,b[25]/2,b[26]/15,b[27]/15)
    own,opp=pip_counts(b)
    home_own=sum(b[i]>=2 for i in range(6))
    home_opp=sum(b[i]<=-2 for i in range(18,24))
    blot_own=sum(c==1 for c in b[:24])
    blot_opp=sum(c==-1 for c in b[:24])
    v[196:]=(own/167,opp/167,(opp-own)/167,home_own/6,home_opp/6,blot_own/15,blot_opp/15)
    return v

class FlyNet:
    def __init__(self,path):
        w=np.fromfile(path,dtype='<f4')
        if len(w)!=sum(WEIGHTS):
            raise ValueError(f'Expected {sum(WEIGHTS)} floats, got {len(w)} in {path}')
        pos=0; parts=[]
        for size in WEIGHTS:
            parts.append(np.ascontiguousarray(w[pos:pos+size]));pos+=size
        # Burn nn::Linear stores weights [d_input, d_output], forward = x @ weight + bias.
        self.w1,self.b1,self.w2,self.b2,self.w3,self.b3=(
            parts[0].reshape(203,80),parts[1],parts[2].reshape(80,80),
            parts[3],parts[4].reshape(80,5),parts[5])

    def equity(self,boards):
        if not boards: return np.zeros(0,dtype=np.float32)
        x=np.stack([encode(b) for b in boards])
        # np matmul usually accumulates f32; multiplication order may differ from burn/ndarray.
        a=np.maximum(x@self.w1+self.b1, np.float32(0))
        a=np.maximum(a@self.w2+self.b2, np.float32(0))
        o=a@self.w3+self.b3
        o=np.float32(1)/(np.float32(1)+np.exp(-o))
        return np.float32(2)*o[:,0]-np.float32(1)+o[:,1]+o[:,2]-o[:,3]-o[:,4]

    def choose1(self,b,dice):
        ends=legal_paths(b,dice)
        if not ends: return None
        for end,path in ends:
            if end[26]==15: return end,path
        e=self.equity([flip(z[0]) for z in ends])
        return ends[int(np.argmin(e))]

    def choose2(self,b,dice):
        """Source-faithful 2ply: shortlist=8, 21 rolls, min opponent's replies per roll."""
        ends=legal_paths(b,dice)
        if not ends: return None
        for end,path in ends:
            if end[26]==15: return end,path
        eq1=self.equity([flip(z[0]) for z in ends])
        order=sorted(range(len(ends)),key=lambda i:float(eq1[i]))[:8]
        allboards=[]; spans=[]
        for idx in order:
            end=ends[idx][0]
            base=len(allboards); allboards.append(end)
            rollsp=[]
            e_opp=flip(end)
            for (d,w) in ROLLS:
                replies=legal_paths(e_opp,d)
                start=len(allboards)
                allboards.extend(flip(x[0]) for x in replies)
                rollsp.append((w,start,len(allboards)))
            spans.append((idx,base,rollsp))
        eq=self.equity(allboards)
        best_idx=order[0];best_val=np.float32(-np.inf)
        for idx,base,rollsp in spans:
            val=np.float32(0)
            for w,start,stop in rollsp:
                v=eq[base] if start==stop else np.min(eq[start:stop])
                val=np.float32(val+np.float32(w*v))
            if val>best_val:
                best_idx=idx;best_val=val
        return ends[best_idx]


def eq_greedy(b, d, net, next_dice=None, mode='1ply'):
    """Player policy; original fly uses choose2, but player may see future dice."""
    options=legal_paths(b,d)
    if not options: return None
    if mode=='2ply':return net.choose2(b,d)
    if mode=='1ply':return net.choose1(b,d)
    if mode=='pip':
        def key(o):
            end=o[0]
            own,opp=pip_counts(end)
            return (end[26]==15, -(own-opp)+25*(end[25]-end[24]),end[26])
        return max(options,key=key)
    if mode=='future':
        if next_dice is None:return net.choose1(b,d)
        # Perfect foreknowledge of the next opponent roll, approximate its play by 1-ply.
        # This is a candidate *player* strategy, not exact bot behavior.
        score=[]
        for end,path in options:
            if end[26]==15:return end,path
            bopp=flip(end)
            opp_move=net.choose1(bopp,next_dice)
            bnext=flip(opp_move[0]) if opp_move else end
            score.append(float(net.equity([bnext])[0]))
        return options[int(np.argmax(score))]
    raise ValueError(mode)


def pack_board(b):
    k=0
    for i,c in enumerate(b[:24]): k |= (c & 0x3f)<<(6*i)
    for i,c in enumerate(b[24:]): k |= (c & 0x1f)<<(144+5*i)
    return k

def unpack_board(k):
    pts=tuple((v-64 if v>=32 else v) for v in ((k>>(6*i))&0x3f for i in range(24)))
    return pts+tuple((k>>(144+5*i))&0x1f for i in range(4))


def invariants(b):
    return ((sum(max(0,v) for v in b[:24])+b[24]+b[26]==15)
            and (sum(max(0,-v) for v in b[:24])+b[25]+b[27]==15)
            and min(b[24:])>=0)
