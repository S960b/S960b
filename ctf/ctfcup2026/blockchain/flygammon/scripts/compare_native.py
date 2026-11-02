#!/usr/bin/env python3
"""Verify every oracle step in offline plan against ORIGINAL Rust source.
Usage: python compare_native.py --plan plan.json --native /path/to/flynative
(compile by ./build_native.sh /path/to/handout/model-src)
"""
import argparse, json, subprocess, sys
from flyengine import unpack_board,pack_board

ap=argparse.ArgumentParser()
ap.add_argument('--plan',required=True)
ap.add_argument('--native',required=True)
ap.add_argument('--model',required=True)
a=ap.parse_args()
obj=json.load(open(a.plan))
games=obj.get('wins',[obj] if 'steps' in obj else [])
proc=subprocess.Popen([a.native,a.model],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                      text=True,bufsize=1,stderr=sys.stderr)
try:
    ok=0; count=0
    for game in games:
        for step in game['steps']:
            if step['who']!='oracle':continue
            b=unpack_board(int(step['startPacked'],16))
            d=step['dice']
            proc.stdin.write(' '.join(map(str,d+list(b)))+'\n');proc.stdin.flush()
            line=proc.stdout.readline()
            if not line: raise RuntimeError('Native oracle ended unexpectedly')
            response=json.loads(line)
            if 'error' in response:raise RuntimeError(response['error'])
            count+=1
            end=int(step['endPacked'],16)
            true_end=pack_board(tuple(response['end']))
            # Path may differ while reaching same board; comparing end position is decisive.
            if true_end!=end:
                print(f'MISMATCH game={game["gameId"]} turn={step["turn"]}')
                print('python',step['froms'],step['dies'],step['endPacked'])
                print('native',response['froms'],response['dies'],hex(true_end))
                sys.exit(2)
            ok+=1
    print(f'PASS: {ok}/{count} oracle-turn board predictions identical to original Rust')
finally:
    proc.terminate()
