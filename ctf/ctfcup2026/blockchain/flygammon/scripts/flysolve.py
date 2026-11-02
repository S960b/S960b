#!/usr/bin/env python3
"""Flygammon deterministic offline planner: predicts dice, simulates exact engine
and source-ported FlyNet/2-ply oracle. No RPC required; never sends transactions.

Examples:
 OPENBLAS_NUM_THREADS=1 python3 flysolve.py test --model ../handout/model.fly
 OPENBLAS_NUM_THREADS=1 python3 flysolve.py game --id 0 --model ../handout/model.fly --player 2ply
 OPENBLAS_NUM_THREADS=1 python3 flysolve.py plan --start 0 --wins 15 --window 100 --shortlist 8 \
   --model ../handout/model.fly --out plan.json
"""
import argparse
import json
import os
import sys
import time
import subprocess
import tempfile
from pathlib import Path
from flyengine import START, FlyNet, legal_paths, flip, eq_greedy, invariants, pack_board, unpack_board, pip_counts

P=88322446169367629588592423935113078568751125482404658974766148135586794479379
G=2
H=87254117696905147968673179739518195695690092761394591924861171695762430084471
S=357653648880297186077052514432982755682505315306081217084424960751681404340


from keccak_ref import keccak256

def keccak(d):return keccak256(d)

def word(n):return int(n).to_bytes(32,'big')


def vrf(gid,turn):
    u=int.from_bytes(keccak(b'flygammon-vrf-u'+word(gid)+word(turn)),'big')%P
    if u<2:u+=2
    y=pow(u,S,P)
    hh=int.from_bytes(keccak(b'flygammon-vrf-dice'+word(y)),'big')
    return (hh%6+1,(hh>>8)%6+1),y


def pips(d):
    a,b=d;return 4*a if a==b else a+b


def score(gid,n=44):
    """Prerank by player pip gain over next n fixed plies; not a proof of win."""
    score=0.
    for turn in range(n):
        d,_=vrf(gid,turn)
        # Opening and early-midgame dice favored; near-end bias weighs less.
        weight=1.0 if turn<28 else (0.60 if turn<44 else 0.35)
        score+=(1 if turn%2==0 else -1)*pips(d)*weight
    return round(score,4)


def simulate(gid,net,player='2ply',max_turns=250):
    """Canonical mover-perspective engine; oracle uses EXACT source algorithm port.

    Player policy may differ; bot's Rust/Burn numerical ties must be parity checked
    against native Rust executable before assuming the transcript is authoritative.
    """
    b=START; steps=[]
    for turn in range(max_turns):
        dice,y=vrf(gid,turn)
        if os.getenv('FLY_TRACE') and turn%10==0:print(f'[trace] gameId={gid} turn={turn}',flush=True)
        if turn%2==0:
            next_dice=vrf(gid,turn+1)[0]
            move=eq_greedy(b,dice,net,next_dice,player)
            who='player'
        else:
            move=net.choose2(b,dice)
            who='oracle'
        if move is None:
            e=b; path=()
        else:
            e,path=move
        assert invariants(e),f'Invariant failed gameId={gid} turn={turn}'
        steps.append({'turn':turn,'who':who,'dice':list(dice),
                      'froms':[v[0] for v in path],'dies':[v[1] for v in path],
                      'startPacked':hex(pack_board(b)),'endPacked':hex(pack_board(e)),
                      'y':str(y)})
        if e[26]==15:
            return {'gameId':gid,'win':who=='player','winner':who,'turns':turn+1,
                    'score':score(gid),'steps':steps,'playerPolicy':player}
        b=flip(e)
    return {'gameId':gid,'win':False,'winner':'timeout','turns':max_turns,
            'score':score(gid),'steps':steps,'playerPolicy':player}


def save(path, data):
    tmp=str(path)+'.tmp'; Path(tmp).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    os.replace(tmp,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=parser.add_subparsers(dest='cmd',required=True)
    for cmd in ['test','rank','game','plan']:
        a=sub.add_parser(cmd)
        a.add_argument('--model',default='../handout/model.fly')
        if cmd=='test': continue
        a.add_argument('--start',type=int,default=0)
        a.add_argument('--player',choices=['1ply','2ply','pip','future'],default='2ply')
        a.add_argument('--max-turns',type=int,default=250)
        a.add_argument('--out',default='plan.json' if cmd=='plan' else None)
        if cmd=='game':a.add_argument('--id',type=int,required=True)
        else:
            a.add_argument('--window',type=int,default=100)
            a.add_argument('--shortlist',type=int,default=8)
            a.add_argument('--score-turns',type=int,default=44)
        if cmd=='plan':
            a.add_argument('--wins',type=int,default=15)
            a.add_argument('--max-skip',type=int,default=20000,
                          help='Abort before more than this many total skipped IDs')
            a.add_argument('--timeout-game',type=int,default=20,
                          help='Hard process timeout per candidate (0 disables)')
    args=parser.parse_args()
    if pow(G,S,P)!=H:sys.exit('VRF secret / public H mismatch')
    if args.cmd=='test':
        assert len(START)==28 and invariants(START)
        assert flip(flip(START))==START
        assert unpack_board(pack_board(START))==START
        assert len(legal_paths(START,(3,1)))==16
        assert len(legal_paths(START,(6,6)))==11
        for i in range(6):
            for j in range(1,7):
                assert all(invariants(b) for b,_ in legal_paths(START,(i+1,j)))
        net=FlyNet(args.model)
        move=net.choose2(START,(3,1))
        assert move is not None and len(move[1])==2
        print('PASS: VRF H, initial board, 36 opening rolls, NN and original 2-ply port')
        print('WARNING: no Rust/Burn numerical parity or on-chain oracle replay has been checked')
        return
    if args.cmd=='rank':
        ranked=sorted(((score(gid,args.score_turns),gid) for gid in range(args.start,args.start+args.window)),reverse=True)
        for sc,gid in ranked[:args.shortlist]:print(f'{gid:8d}  score={sc:+.2f}')
        return
    net=FlyNet(args.model)
    if args.cmd=='game':
        t=time.monotonic()
        result=simulate(args.id,net,args.player,args.max_turns)
        print(f'gameId={args.id}, {result["winner"]}, turns={result["turns"]}, sec={time.monotonic()-t:.2f}',flush=True)
        if args.out:save(args.out,result);print('saved',args.out)
        else:
            for move in result['steps']:
                if move['who']=='player':print(f"t{move['turn']:3}: dice={move['dice']} path={list(zip(move['froms'],move['dies']))}")
        return
    # Persist after each locally validated win, so aborts don't discard work.
    out=Path(args.out); start=args.start; wins=[]
    if out.exists():
        old=json.loads(out.read_text())
        if old.get('P')==str(P) and old.get('s')==str(S) and old.get('playerPolicy')==args.player:
            wins=old.get('wins',[])
            if wins:
                start=wins[-1]['gameId']+1
                print(f'[*] Resuming at {start}, {len(wins)} games already planned',flush=True)
    remaining=args.wins-len(wins)
    while remaining>0:
        if start-args.start > args.max_skip:
            print(f'[!] Max skip bound reached after {len(wins)} planned wins');break
        ranked=sorted(((score(gid,args.score_turns),gid) for gid in range(start,start+args.window)),reverse=True)
        found=None
        for sc,gid in ranked[:args.shortlist]:
            t=time.monotonic()
            if args.timeout_game:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp=Path(tmpdir)/'result.json'
                    command=[sys.executable,str(Path(__file__).resolve()),'game',
                             '--id',str(gid),'--model',args.model,'--player',args.player,
                             '--max-turns',str(args.max_turns),'--out',str(tmp)]
                    try:
                        subprocess.run(command, stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                       check=True,timeout=args.timeout_game)
                        r=json.loads(tmp.read_text())
                    except subprocess.TimeoutExpired:
                        print(f'[!] id={gid} exceeded {args.timeout_game}s; skipping expensive candidate',flush=True)
                        continue
                    except subprocess.CalledProcessError as ex:
                        print(f'[!] id={gid} simulation crashed: {ex.stderr.decode(errors="replace")}',flush=True)
                        continue
            else:
                r=simulate(gid,net,args.player,args.max_turns)
            print(f'[*] id={gid} luck={sc:+.1f} => {r["winner"]} in {r["turns"]} turns ({time.monotonic()-t:.1f}s)',flush=True)
            if r['win']:
                found=r;break
        if found:
            wins.append(found);remaining-=1;start=found['gameId']+1
            save(out,{'P':str(P),'G':str(G),'H':str(H),'s':str(S),
                      'playerPolicy':args.player,'wins':wins,
                      'nextId':start,'complete':remaining==0})
            print(f'[+] planned streak {len(wins)}/{args.wins}; accepted gameId={found["gameId"]}',flush=True)
        else:
            start+=args.window
            print(f'[-] No wins in best {args.shortlist} of previous window; next window starts {start}',flush=True)
    print('[*] Plan file:',out,' wins:',len(wins),' next game:',start)

if __name__=='__main__':main()
