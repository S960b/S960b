#!/usr/bin/env python3
"""Flygammon plan replayer. DOES NOT send any transaction unless --execute.
Requires: pip install web3 eth-account
Example:
  RPC_URL=http://127.0.0.1:8545 CHALLENGE_ADDR=0x... PLAYER_KEY=0x... \
      python play_live.py --plan plan.json                  # preflight only
  RPC_URL=... CHALLENGE_ADDR=... PLAYER_KEY=... \
      python play_live.py --plan plan.json --execute       # sends transactions
The plan should first be verified with compare_native.py against the original Rust agent.
"""
import argparse, json, os, time, sys, subprocess
from pathlib import Path
from flysolve import P,G,H,vrf
from flyengine import flip, pack_board, unpack_board

ABI=[
 {'type':'function','name':name,'inputs':[], 'outputs':[{'type':kind}],'stateMutability':'view'}
 for name,kind in [('P','uint256'),('G','uint256'),('H','uint256'),('N','uint256'),
                   ('gameId','uint256'),('turn','uint256'),('streak','uint256'),
                   ('tokens','uint256'),('player','address'),('committed','bool'),
                   ('playerToMove','bool'),('dicePosted','bool'),('d1','uint8'),('d2','uint8'),
                   ('boardPacked','uint256'),('isSolved','bool')]
]
ABI.extend([
 {'type':'function','name':name,'inputs':[],'outputs':[],'stateMutability':'nonpayable'}
 for name in ['skip','commit']])
ABI.append({'type':'function','name':'playerMove',
            'inputs':[{'name':'froms','type':'uint8[]'},{'name':'dies','type':'uint8[]'}],
            'outputs':[],'stateMutability':'nonpayable'})


def tx(w3,acct,fun,gas=6500000):
    args={'from':acct.address, 'nonce':w3.eth.get_transaction_count(acct.address,'pending'),
          'gas':gas,'gasPrice':w3.eth.gas_price,'chainId':w3.eth.chain_id}
    data=fun.build_transaction(args)
    signed=acct.sign_transaction(data)
    raw=getattr(signed,'raw_transaction',None)
    if raw is None:raw=signed.rawTransaction
    h=w3.eth.send_raw_transaction(raw)
    receipt=w3.eth.wait_for_transaction_receipt(h, timeout=120)
    if receipt.status!=1:
        raise RuntimeError(f'Transaction reverted: {w3.to_hex(h)}')
    return w3.to_hex(h)


def wait_for(label,pred,interval=0.35,timeout=120):
    start=time.monotonic()
    while not pred():
        if time.monotonic()-start > timeout:raise TimeoutError(label)
        time.sleep(interval)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan',required=True)
    ap.add_argument('--execute',action='store_true',help='Actually sign and send transactions')
    ap.add_argument('--max-skips',type=int,default=2000)
    ap.add_argument('--native',help='Compiled original Rust flynative binary (required with --execute)')
    ap.add_argument('--model',help='Path/stem to original model checkpoint (required with --execute)')
    a=ap.parse_args()
    plan=json.loads(Path(a.plan).read_text())
    games=plan.get('wins',[])
    if not games:sys.exit('Expected plan JSON with wins[] from flysolve.py')
    if any(not g['win'] for g in games):sys.exit('Plan contains a non-winning game')
    if any(x['gameId']>=y['gameId'] for x,y in zip(games,games[1:])):
        sys.exit('GameIDs must increase strictly')
    if plan.get('s') != str(__import__('flysolve').S):sys.exit('Plan private exponent mismatch')
    rpc=os.getenv('RPC_URL');addr=os.getenv('CHALLENGE_ADDR'); key=os.getenv('PLAYER_KEY')
    if not rpc or not addr:sys.exit('Set RPC_URL and CHALLENGE_ADDR')
    try:
        from web3 import Web3
    except ImportError:
        sys.exit('pip install web3 eth-account')
    w3=Web3(Web3.HTTPProvider(rpc,request_kwargs={'timeout':12}))
    if not w3.is_connected():sys.exit('Cannot connect to JSON RPC')
    c=w3.eth.contract(address=Web3.to_checksum_address(addr),abi=ABI)
    if [c.functions.P().call(),c.functions.G().call(),c.functions.H().call()] != [P,G,H]:
        sys.exit('Wrong challenge contract/VRF parameters')
    print('RPC connected; chain ID:',w3.eth.chain_id)
    print('Contract:',c.address,'N:',c.functions.N().call(), 'token budget:',c.functions.tokens().call())
    print('Start gameId:',c.functions.gameId().call(),'streak:',c.functions.streak().call())
    print('Plans:',len(games),'IDs:',[g['gameId'] for g in games])
    if not a.execute:
        print('DRY RUN only; --execute must be specified to send transactions. Verify with compare_native.py first.')
        return
    if not a.native or not a.model:
        sys.exit('ABORT: --execute requires --native and --model to verify all oracle moves against Rust')
    print('[*] Checking exact Rust oracle predictions before broadcasting anything...',flush=True)
    subprocess.run([sys.executable,str(Path(__file__).with_name('compare_native.py')),
                    '--plan',a.plan,'--native',a.native,'--model',a.model],check=True)
    if not key:sys.exit('Set PLAYER_KEY private key')
    acct=w3.eth.account.from_key(key)
    if acct.address.lower()!=c.functions.player().call().lower():
        sys.exit('PLAYER_KEY is not the authorized player')
    if w3.eth.chain_id!=31337:sys.exit('Unexpected chain ID, refusing to send')
    if c.functions.N().call()>len(games):sys.exit('Need at least N planned wins')
    total_skipped=0
    for index,game in enumerate(games):
        if c.functions.isSolved().call():
            print('Contract isSolved() = true');return
        target=game['gameId']
        actual=c.functions.gameId().call()
        streak=c.functions.streak().call()
        if streak!=index:
            sys.exit(f'ABORT: expected streak {index} (planned games), got {streak}')
        if actual>target:sys.exit(f'ABORT: chain gameId={actual} already past planned {target}')
        if c.functions.committed().call():
            sys.exit('ABORT: already committed, cannot skip to planned ID')
        while actual<target:
            if total_skipped >= a.max_skips:sys.exit('ABORT: max skips exceeded')
            h=tx(w3,acct,c.functions.skip(),gas=280000)
            actual=c.functions.gameId().call();total_skipped+=1
            if total_skipped%20==0:print('Skipped:',total_skipped, 'chain gameId:',actual,flush=True)
        if c.functions.boardPacked().call()!=int(game['steps'][0]['startPacked'],16):
            sys.exit('ABORT: initial board mismatch')
        print(f'[{index+1}/{len(games)}] committing gameId={target}',flush=True)
        h=tx(w3,acct,c.functions.commit(),gas=200000)
        print('  committed',h,flush=True)
        for step in game['steps']:
            turn=step['turn'];who=step['who']
            if who=='oracle':continue  # daemon executes oracleTurn() autonomously
            wait_for(f'player turn={turn}',lambda: c.functions.gameId().call()!=target or (
                c.functions.turn().call()==turn and c.functions.dicePosted().call()
                and c.functions.playerToMove().call()))
            if c.functions.gameId().call()!=target:sys.exit('ABORT: game ended before planned turn')
            if c.functions.boardPacked().call()!=int(step['startPacked'],16):
                sys.exit(f'ABORT: board mismatch at game={target} turn={turn}; oracle diverged')
            d=(c.functions.d1().call(),c.functions.d2().call())
            if d!=tuple(step['dice']) or d!=vrf(target,turn)[0]:
                sys.exit(f'ABORT: dice mismatch game={target} turn={turn}: {d} != {step["dice"]}')
            froms=step['froms'];dies=step['dies']
            print(f'  t={turn} {d}: {list(zip(froms,dies))}',flush=True)
            h=tx(w3,acct,c.functions.playerMove(froms,dies),gas=6500000)
            # On last player move, gameId changes and we can move to next ID.
        new_streak=c.functions.streak().call()
        if new_streak!=index+1:
            sys.exit(f'ABORT: gameId={target} ended with unexpected streak {new_streak}')
        print(f'[+] live streak={new_streak}; gameId={c.functions.gameId().call()}',flush=True)
    print('isSolved():',c.functions.isSolved().call())

if __name__=='__main__':main()
