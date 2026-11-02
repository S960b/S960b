#!/usr/bin/env python3
"""Fast, resumable Flygammon replayer.

Optimizations:
  * bulk pre-sign + sequentially broadcast skip() transactions without individual
    receipts; wait for only the last receipt in each chunk.
  * one JSON-RPC batch per state poll (rather than many serial eth_call requests).
  * cache the authorized player's nonce and gas price during the whole run.

No transactions in default dry run. Uses the original verified plan unchanged.
Does NOT invoke Anvil admin/debug APIs, modify any contract storage, or skip
validations. Stops on any ambiguous transaction outcome or unexpected chain state.

Install: pip install web3 requests numpy pycryptodome
Run:
 RPC_URL=http://... CHALLENGE_ADDR=0x... PLAYER_KEY=0x... \\
 python3 resume_play_fast.py --plan plan_15.json --execute --skip-chunk 12
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
import requests
from flysolve import P, G, H, S, vrf

ABI = [
    {'type': 'function', 'name': name, 'inputs': [],
     'outputs': [{'type': kind}], 'stateMutability': 'view'}
    for name,kind in [('P','uint256'),('G','uint256'),('H','uint256'),('N','uint256'),
                      ('gameId','uint256'),('turn','uint256'),('streak','uint256'),
                      ('tokens','uint256'),('player','address'),('committed','bool'),
                      ('playerToMove','bool'),('dicePosted','bool'),('d1','uint8'),
                      ('d2','uint8'),('boardPacked','uint256'),('isSolved','bool')]
]
ABI += [{'type': 'function', 'name': name, 'inputs': [], 'outputs': [],
         'stateMutability': 'nonpayable'} for name in ['skip', 'commit']]
ABI += [{'type': 'function', 'name': 'playerMove',
         'inputs': [{'name':'froms','type':'uint8[]'}, {'name':'dies','type':'uint8[]'}],
         'outputs': [], 'stateMutability': 'nonpayable'}]

FIELDS = ('gameId','turn','streak','committed','playerToMove','dicePosted',
          'boardPacked','d1','d2','isSolved')

class RPCState:
    """Batch eth_call using one HTTP request for all fields in a polling pass."""
    def __init__(self, url, address, Web3):
        self.url = url
        self.address = address
        self.s = requests.Session()
        self.s.trust_env = False  # avoid env proxy; target is a dedicated game RPC
        self.calls = [('0x'+Web3.keccak(text=name+'()')[:4].hex()) for name in FIELDS]
        self.num = 0
    def __call__(self):
        req = [{'jsonrpc':'2.0', 'id':i+1, 'method':'eth_call',
                'params':[{'to':self.address,'data':selector}, 'latest']}
               for i,selector in enumerate(self.calls)]
        for attempt in range(3):
            try:
                resp = self.s.post(self.url, json=req, timeout=(3,8))
                resp.raise_for_status()
                body = resp.json()
                if not isinstance(body,list):
                    raise RuntimeError('RPC server did not accept JSON-RPC batch')
                indexed = {r['id']:r for r in body}
                vals = {}
                for i,name in enumerate(FIELDS):
                    part=indexed[i+1]
                    if 'error' in part:raise RuntimeError(f'{name}: {part["error"]}')
                    vals[name]=int(part['result'],16)
                self.num+=1
                return vals
            except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
                if attempt==2:raise RuntimeError(f'RPC snapshot failed: {exc}') from exc
                time.sleep(0.2*(attempt+1))
    
class Signer:
    def __init__(self,w3,acct,contract):
        self.w3=w3
        self.acct=acct
        self.contract=contract
        self.nonce=w3.eth.get_transaction_count(acct.address,'pending')
        self.gas_price=w3.eth.gas_price
        self.chain_id=w3.eth.chain_id
        self.sent=0
        self.skip_data='0x'+w3.keccak(text='skip()')[:4].hex()
    def _raw(self,data,nonce,gas):
        t={'to':self.contract,'nonce':nonce,
           'gas':gas,'gasPrice':self.gas_price,'chainId':self.chain_id,
           'data':data}
        signed=self.acct.sign_transaction(t)
        raw=getattr(signed,'raw_transaction',None)
        if raw is None:raw=signed.rawTransaction
        return raw
    def _receipt(self,hash_,timeout=40):
        receipt=self.w3.eth.wait_for_transaction_receipt(hash_,timeout=timeout,poll_latency=0.12)
        if receipt.status!=1:
            raise RuntimeError(f'TX REVERTED: {self.w3.to_hex(hash_)}')
        return receipt
    def call(self,data,gas=6500000,receipt=True):
        nonce=self.nonce
        raw=self._raw(data,nonce,gas)
        expected_hash=self.w3.keccak(raw)
        try:
            actual_hash=self.w3.eth.send_raw_transaction(raw)
        except Exception as e:
            raise RuntimeError(f'AMBIGUOUS SEND nonce={nonce} hash={expected_hash.hex()}; '
                               f'check chain nonce and receipt before restarting: {e}') from e
        if bytes(actual_hash)!=bytes(expected_hash):
            raise RuntimeError('Unexpected tx hash from RPC')
        self.nonce+=1
        self.sent+=1
        if receipt:self._receipt(actual_hash)
        return actual_hash
    def skips(self,count,chunk_state):
        # Do not wait after each transaction: sign sequential nonces in advance.
        # Still send in nonce order to avoid out-of-order txpool rejection.
        if count<=0:return
        start=self.nonce
        raws=[self._raw(self.skip_data,start+i,110000) for i in range(count)]
        last_hash=None
        for i,raw in enumerate(raws):
            exp_hash=self.w3.keccak(raw)
            try:
                sent_hash=self.w3.eth.send_raw_transaction(raw)
            except Exception as exc:
                # Stop rather than guessing whether a timed-out request was mined.
                raise RuntimeError(f'AMBIGUOUS SKIP SEND at nonce={start+i} '
                                   f'hash={exp_hash.hex()}; original gameId={chunk_state}; '
                                   f'recheck chain before resuming: {exc}') from exc
            if bytes(sent_hash)!=bytes(exp_hash):
                raise RuntimeError('RPC returned unexpected skip tx hash')
            self.nonce+=1
            self.sent+=1
            last_hash=sent_hash
        self._receipt(last_hash,timeout=60)


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan',required=True)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--skip-chunk',type=int,default=12,
                    help='Skip transaction pipeline length, usually 8-24')
    ap.add_argument('--max-skips',type=int,default=2000)
    ap.add_argument('--poll',type=float,default=0.12)
    ap.add_argument('--max-wait',type=float,default=180)
    args=ap.parse_args()
    if not 1<=args.skip_chunk<=64:ap.error('skip-chunk must be 1..64')
    if args.poll<0.05:ap.error('poll must be >= 0.05')

    plan=json.loads(Path(args.plan).read_text())
    games=sorted(plan['wins'],key=lambda g:g['gameId'])
    if plan.get('s')!=str(S) or not all(g.get('win') for g in games):
        sys.exit('Plan/secret mismatch')
    rpc=os.getenv('RPC_URL'); addr=os.getenv('CHALLENGE_ADDR'); key=os.getenv('PLAYER_KEY')
    if not rpc or not addr:sys.exit('Set RPC_URL and CHALLENGE_ADDR')

    from web3 import Web3
    # Stateful HTTP client uses keep-alive across sequential RPC calls.
    w3=Web3(Web3.HTTPProvider(rpc,request_kwargs={'timeout':10}))
    if not w3.is_connected():sys.exit('RPC unavailable (expired instance?)')
    addr=Web3.to_checksum_address(addr)
    c=w3.eth.contract(address=addr,abi=ABI)
    if w3.eth.chain_id!=31337:sys.exit('Unexpected chain ID')
    if (c.functions.P().call(),c.functions.G().call(),c.functions.H().call())!=(P,G,H):
        sys.exit('Unexpected VRF params; regenerate plan')
    if c.functions.N().call()>len(games):sys.exit('Plan has fewer wins than required')
    watch=RPCState(rpc,addr,Web3)
    state=watch()
    print(f'Connected; gameId={state["gameId"]}, streak={state["streak"]}, '
          f'turn={state["turn"]}, committed={bool(state["committed"])}; '
          f'planned={len(games)} wins',flush=True)
    if not args.execute:
        print('DRY RUN (no transactions); pass --execute to play',flush=True)
        return
    if not key:sys.exit('Set PLAYER_KEY locally; never send it to a chat')
    acct=w3.eth.account.from_key(key)
    if acct.address.lower()!=c.functions.player().call().lower():
        sys.exit('Key is not the authorized player')
    if state['streak']>len(games):sys.exit('Streak exceeds plan')
    signer=Signer(w3,acct,addr)
    latest_nonce=w3.eth.get_transaction_count(acct.address,'latest')
    if latest_nonce!=signer.nonce:
        sys.exit('Pending player transactions detected; wait until mined before resuming')
    used_skips=0

    def snapshot():
        return watch()

    def skip_to(target):
        nonlocal used_skips
        while True:
            s=snapshot()
            gid=s['gameId']
            if gid==target:return s
            if gid>target:sys.exit(f'ABORT: passed gameId target={target}, actual={gid}')
            if s['committed']:sys.exit('ABORT: unexpected committed state during skip')
            n=min(args.skip_chunk,target-gid)
            if used_skips+n>args.max_skips:sys.exit('ABORT: max skips exceeded')
            t0=time.monotonic()
            signer.skips(n,gid)
            used_skips+=n
            after=snapshot()
            if after['gameId']!=gid+n or after['committed']:
                sys.exit(f'ABORT: skip chunk mismatch: before={gid}, count={n}, '
                         f'after={after["gameId"]}, committed={after["committed"]}')
            print(f'[skip] {gid} -> {gid+n} in {time.monotonic()-t0:.2f}s '
                  f'(batch {n}, total={used_skips})',flush=True)

    def await_player(gid,turn,start,dice):
        end=time.monotonic()+args.max_wait
        while True:
            s=snapshot()
            # A current-game state mismatch might just be a cross-block batch
            # snapshot. Confirm a second time before treating it as divergence.
            if s['gameId']!=gid or s['turn']>turn:
                s2=snapshot()
                if s2['gameId']!=gid or s2['turn']>turn:
                    sys.exit(f'ABORT: expected gameId={gid}, turn={turn}, got {s2}')
                s=s2
            if (s['gameId']==gid and s['turn']==turn and
                s['playerToMove'] and s['dicePosted']):
                if s['boardPacked']!=start or (s['d1'],s['d2'])!=dice:
                    s2=snapshot()
                    if (s2['gameId']!=gid or s2['turn']!=turn or
                        s2['boardPacked']!=start or (s2['d1'],s2['d2'])!=dice):
                        sys.exit(f'ABORT: oracle/board mismatch, game={gid}, turn={turn}, state={s2}')
                return
            if time.monotonic()>end:
                raise TimeoutError(f'Waiting for bot: game={gid}, turn={turn}; state={s}')
            time.sleep(args.poll)

    start_streak=state['streak']
    remaining=games[start_streak:]
    print('Remaining winning gameIds:',[g['gameId'] for g in remaining],flush=True)
    start_time=time.monotonic()
    for index,g in enumerate(remaining):
        gid=g['gameId']; wanted_streak=start_streak+index
        current=snapshot()
        if current['isSolved']:
            print('SUCCESS isSolved() == true')
            return
        if current['streak']!=wanted_streak:
            sys.exit(f'ABORT: expected streak={wanted_streak}, actual={current["streak"]}')
        if current['gameId']<gid:
            skip_to(gid)
            current=snapshot()
        elif current['gameId']>gid:
            sys.exit(f'ABORT: past planned gameId={gid}, chain={current["gameId"]}')
        if not current['committed']:
            if current['boardPacked']!=int(g['steps'][0]['startPacked'],16):
                sys.exit('ABORT: board mismatch before commit')
            signer.call('0x'+Web3.keccak(text='commit()')[:4].hex(),gas=160000)
            first_turn=0
            print(f'[commit] game={gid}, streak={wanted_streak}',flush=True)
        else:
            first_turn=current['turn']
            print(f'[resume] game={gid}, turn={first_turn}',flush=True)
        for step in g['steps']:
            turn=step['turn']
            if step['who']!='player' or turn<first_turn:continue
            dice=tuple(step['dice'])
            if dice!=vrf(gid,turn)[0]:sys.exit('ABORT: plan dice differs from VRF')
            await_player(gid,turn,int(step['startPacked'],16),dice)
            payload=c.functions.playerMove(step['froms'],step['dies'])._encode_transaction_data()
            signer.call(payload,gas=6500000)
        final=snapshot()
        if final['streak']!=wanted_streak+1:
            sys.exit(f'ABORT: game={gid} ended with streak={final["streak"]} not {wanted_streak+1}')
        print(f'[WIN] {wanted_streak+1}/{len(games)} game={gid}; '
              f'elapsed={time.monotonic()-start_time:.1f}s; RPC snapshots={watch.num}',flush=True)
    final=snapshot()
    print('FINAL isSolved() =',bool(final['isSolved']),flush=True)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:sys.exit('\nStopped (safe to resume after pending transactions settle).')
