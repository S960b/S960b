#!/usr/bin/env python3
"""Verify saved plan without network, model, or Rust. Checks dice, all moves and board transitions."""
import argparse,json
from flysolve import vrf,P,G,H,S
from flyengine import START, flip, legal_paths, pack_board, unpack_board, invariants

def verify(obj):
    wins=obj['wins']
    assert obj['P']==str(P) and obj['G']==str(G) and obj['H']==str(H) and obj['s']==str(S)
    previous=-1; steps=0; player_moves=0
    for game in wins:
        assert game['gameId']>previous
        previous=game['gameId']
        assert game['win'] and game['winner']=='player'
        assert game['steps'][-1]['who']=='player'
        board=START
        for turn,step in enumerate(game['steps']):
            assert turn==step['turn']
            assert step['who']==('player' if turn%2==0 else 'oracle')
            assert tuple(step['dice'])==vrf(game['gameId'],turn)[0]
            assert step['y']==str(vrf(game['gameId'],turn)[1])
            assert int(step['startPacked'],16)==pack_board(board)
            ending=int(step['endPacked'],16)
            b2=unpack_board(ending)
            assert invariants(b2)
            path=tuple(zip(step['froms'],step['dies']))
            options=legal_paths(board,tuple(step['dice']))
            if options:
                assert any(b2==b and path==p for b,p in options),f"Illegal move {game['gameId']} t{turn}"
            else:
                assert b2==board and path==()
            assert (b2[26]==15)==(turn==len(game['steps'])-1)
            board=flip(b2)
            steps+=1
            if step['who']=='player':player_moves+=1
    return len(wins),steps,player_moves,previous

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('plan');a=ap.parse_args()
    n,steps,player_moves,last=verify(json.load(open(a.plan)))
    print(f'PASS: {n} consecutive locally simulated wins, {steps} checked plies, '+
          f'{player_moves} player moves, {last} last gameId; no RPC parity claimed')
