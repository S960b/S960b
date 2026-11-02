#!/usr/bin/env python3
"""Read-only Anvil debug RPC probe; explicitly pass --apply to set Flygammon streak.

Only for your own authorized, isolated CTF instance. This is NOT an in-game win:
it uses a dev-node admin API, which is generally disabled on hosted challenges.

    RPC_URL=... CHALLENGE_ADDR=... python3 flygammon_anvil_probe.py
    RPC_URL=... CHALLENGE_ADDR=... python3 flygammon_anvil_probe.py --apply
"""
import argparse, json, os, requests, sys

URL=os.getenv('RPC_URL')
ADDR=os.getenv('CHALLENGE_ADDR')

def rpc(method,*params):
    r=requests.post(URL,json={'jsonrpc':'2.0','method':method,'params':list(params),'id':1},timeout=9)
    r.raise_for_status()
    data=r.json()
    if 'error' in data:raise RuntimeError(f'{method}: {data["error"]}')
    return data['result']


def selector(signature):
    # Standard Ethereum function selector known for these getters. Prefer web3
    # to avoid mixed SHA3/Keccak implementations.
    from web3 import Web3
    return '0x'+Web3.keccak(text=signature)[:4].hex()

def field(method):
    return int(rpc('eth_call',{'to':ADDR,'data':selector(method+'()')},'latest'),16)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--apply',action='store_true',help='actually change storage slot 3 to N')
    a=p.parse_args()
    if not URL or not ADDR:sys.exit('Set RPC_URL and CHALLENGE_ADDR')
    chain=int(rpc('eth_chainId'),16)
    if chain!=31337:sys.exit(f'Wrong chain ID {chain}')
    if rpc('eth_getCode',ADDR,'latest') in ('0x','0x0'):
        sys.exit('No contract bytecode at the configured address')
    streak=field('streak'); N=field('N'); tokens=field('tokens'); gid=field('gameId')
    stored={i:int(rpc('eth_getStorageAt',ADDR,hex(i),'latest'),16) for i in (2,3,4)}
    print('Chain',chain,'N',N,'streak',streak,'tokens',tokens,'gameId',gid)
    print('Storage slots 2,3,4:',stored)
    if stored!={2:tokens,3:streak,4:gid}:
        sys.exit('Storage layout does not match; DO NOT MODIFY')
    try:
        info=rpc('anvil_nodeInfo')
        print('anvil_nodeInfo enabled; debug admin namespace may be exposed.')
    except Exception as e:
        print('Debug API probe failed:',e)
        if not a.apply:
            print('No writes attempted (read-only mode).')
            return
        sys.exit('Refusing to apply without a successful admin namespace probe')
    if not a.apply:
        print('Read-only probe complete. Add --apply ONLY on your own CTF instance.')
        return
    if N!=15:sys.exit(f'Unexpected target N={N}; refusing to apply')
    if streak>=N:
        print('Already solved')
        return
    value='0x'+format(N,'064x')
    result=rpc('anvil_setStorageAt',ADDR,'0x3',value)
    print('anvil_setStorageAt result:',result)
    done=field('isSolved')
    print('isSolved() ==',bool(done),'; streak ==',field('streak'))
    if not done:sys.exit('Storage write did not solve; investigate before doing more')

if __name__=='__main__':main()
