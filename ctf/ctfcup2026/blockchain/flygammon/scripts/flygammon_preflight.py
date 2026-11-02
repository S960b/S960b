#!/usr/bin/env python3
"""Flygammon offline VRF preflight (read-only). Python3 + OpenSSL3; sympy for --recover.

Usage:
  python3 flygammon_preflight.py --rpc http://host:8545 --contract 0x... --factor
  python3 flygammon_preflight.py --rpc http://host:8545 --contract 0x... --factor --recover
  python3 flygammon_preflight.py --p 337 --g 10 --h 235 --recover --turns 12
  python3 flygammon_preflight.py --p ... --g ... --h ... --secret ... --scan 2000 --top 10

IMPORTANT: No transactions are sent. "Top candidate IDs" are only a pip-roll
heuristic, not validated winning game IDs. To prove a win, replay actual bot
and legal backgammon moves with the provided Rust model and Solidity engine.
"""

import argparse
import ctypes
import ctypes.util
import json
import sys
from urllib.request import Request, urlopen


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256, NOT hashlib.sha3_256."""
    try:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(data)
        return k.digest()
    except ImportError:
        pass
    # OpenSSL 3 exposes Ethereum-compatible KECCAK-256, even without pycryptodome.
    libname = ctypes.util.find_library('crypto')
    if not libname:
        raise RuntimeError('Install pycryptodome (pip install pycryptodome)')
    crypto = ctypes.CDLL(libname)
    crypto.EVP_MD_fetch.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    crypto.EVP_MD_fetch.restype = ctypes.c_void_p
    md = crypto.EVP_MD_fetch(None, b'KECCAK-256', None)
    if not md:
        raise RuntimeError('OpenSSL lacks KECCAK-256. Install pycryptodome.')
    crypto.EVP_Digest.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p, ctypes.c_void_p]
    crypto.EVP_Digest.restype = ctypes.c_int
    crypto.EVP_MD_free.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data)
    digest = ctypes.create_string_buffer(32)
    length = ctypes.c_uint(0)
    try:
        rc = crypto.EVP_Digest(buf, len(data), digest, ctypes.byref(length), md, None)
        if rc != 1 or length.value != 32:
            raise RuntimeError('EVP_Digest failure')
        return digest.raw
    finally:
        crypto.EVP_MD_free(md)


def word(n):
    return int(n).to_bytes(32, 'big')


def u_for(p, game_id, turn):
    u = int.from_bytes(keccak256(b'flygammon-vrf-u' + word(game_id) + word(turn)), 'big') % p
    return u + 2 if u < 2 else u


def dice_from_y(y):
    hh = int.from_bytes(keccak256(b'flygammon-vrf-dice' + word(y)), 'big')
    return (hh % 6 + 1, ((hh >> 8) % 6) + 1)


def prediction(p, s, gid, turn):
    return dice_from_y(pow(u_for(p, gid, turn), s, p))


def rpc_call(rpc, addr, signature):
    selector = keccak256(signature.encode()).hex()[:8]
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call',
                       'params': [{'to': addr, 'data': '0x' + selector}, 'latest']}).encode()
    req = Request(rpc, data=body, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=8) as resp:
        obj = json.load(resp)
    if 'error' in obj:
        raise RuntimeError(f'RPC {signature}: {obj["error"]}')
    return int(obj['result'], 16)


def intauto(s):
    return int(s, 0)


def factor_info(p, limit):
    try:
        from sympy import factorint, isprime
    except ImportError:
        print('[!] Install sympy for factorization: pip install sympy')
        return None
    print('[*] Testing P primality:', bool(isprime(p)))
    print(f'[*] Trial factorization of P-1 (limit={limit}) ...', flush=True)
    factors = factorint(p - 1, limit=limit)
    for q, e in sorted(factors.items()):
        print(f'  {q}^{e}  ({int(q).bit_length()} bits; prime={bool(isprime(q))})')
    complete = all(isprime(q) for q in factors)
    print('[+] Full factorization' if complete else '[!] Partial factorization, cofactor remains')
    return factors if complete else None


def roll_pips(d):
    a, b = d
    return (4 * a) if a == b else (a + b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rpc', help='JSON-RPC URL (read-only eth_call)')
    ap.add_argument('--contract', help='Flygammon deployed contract address')
    ap.add_argument('--p', type=intauto)
    ap.add_argument('--g', type=intauto)
    ap.add_argument('--h', type=intauto)
    ap.add_argument('--secret', type=intauto, help='Known private exponent s')
    ap.add_argument('--factor', action='store_true', help='Analyze factorization of P-1')
    ap.add_argument('--factor-limit', type=int, default=30000)
    ap.add_argument('--recover', action='store_true', help='Attempt discrete logarithm H=G^s mod P')
    ap.add_argument('--max-factor-bits', type=int, default=36,
                    help='Avoid extremely expensive DLP when a factor exceeds this size')
    ap.add_argument('--game', type=int, default=0)
    ap.add_argument('--turns', type=int, default=10)
    ap.add_argument('--show-y', action='store_true', help='Show predicted oracle VRF output y for log comparison')
    ap.add_argument('--scan', type=int, default=0, help='Heuristic rank next N game IDs by opening pip advantage')
    ap.add_argument('--plies', type=int, default=20)
    ap.add_argument('--top', type=int, default=10)
    args = ap.parse_args()

    if keccak256(b'').hex() != 'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470':
        raise RuntimeError('Ethereum Keccak self-test failed')
    print('[+] Keccak256 test passed')

    p, g, h = args.p, args.g, args.h
    if args.rpc or args.contract:
        if not args.rpc or not args.contract:
            ap.error('--rpc and --contract must be provided together')
        for sig, name in [('P()', 'P'), ('G()', 'G'), ('H()', 'H'),
                          ('N()', 'N'), ('tokens()', 'tokens'), ('gameId()', 'gameId'),
                          ('streak()', 'streak'), ('turn()', 'turn')]:
            try:
                value = rpc_call(args.rpc, args.contract, sig)
                print(f'[chain] {name} = {value}')
                if name == 'P': p = value
                if name == 'G': g = value
                if name == 'H': h = value
                if name == 'gameId' and args.game == 0: args.game = value
            except Exception as ex:
                print(f'[!] Could not fetch {name}: {ex}')
    if p is None or g is None or h is None:
        ap.error('Supply --p, --g, --h or supply --rpc and --contract')
    if p <= 2:
        ap.error('P must be > 2')
    print(f'[*] Params: P ({p.bit_length()} bits)={p}, G={g}, H={h}')

    fac = None
    if args.factor or args.recover:
        fac = factor_info(p, args.factor_limit)
    s = args.secret
    if args.recover and s is None:
        if fac is None:
            print('[!] Cannot safely attempt generic DLP without complete P-1 factorization')
        elif max(int(x).bit_length() for x in fac) > args.max_factor_bits:
            print('[!] DLP skipped: large prime-order factor; consider Sage/advanced DLP')
        else:
            from sympy.ntheory import discrete_log
            print('[*] Attempting discrete log; may be expensive ...', flush=True)
            try:
                s = int(discrete_log(p, h, g))
                if pow(g, s, p) != h:
                    raise RuntimeError('recovered s failed verification')
                print(f'[+] Candidate exponent s = {s}')
                print('[!] If G is not a primitive root, H alone may NOT determine u^s. Verify on real dice!')
            except Exception as ex:
                print('[!] DLP attempt failed:', ex)
    if s is None:
        print('[*] No secret s: cannot predict future dice from the public contract alone')
        return
    if pow(g, s, p) != h:
        print('[!] Supplied secret does not match H; prediction may be wrong!')
        return
    print(f'\n[*] Predictions for gameId={args.game}:')
    for t in range(args.turns):
        y = pow(u_for(p, args.game, t), s, p)
        d1, d2 = dice_from_y(y)
        who = 'player' if t % 2 == 0 else 'oracle'
        extra = f'  y=0x{y:x}' if args.show_y else ''
        print(f'  turn={t:2} {who:6} dice={d1},{d2}{extra}')

    if args.scan > 0:
        print(f'\n[*] Ranking {args.scan} IDs by roll-pip ADVANTAGE in first {args.plies} plies')
        scores = []
        for gid in range(args.game, args.game + args.scan):
            score = 0
            for t in range(args.plies):
                score += (1 if t % 2 == 0 else -1) * roll_pips(prediction(p, s, gid, t))
            scores.append((score, gid))
        scores.sort(reverse=True)
        for score, gid in scores[:args.top]:
            print(f'  gameId={gid} score={score:+d} (HEURISTIC ONLY; NOT A PROVEN WIN)')
    print('\n[!] To win: simulate both players with the Rust engine/AI and choose legal moves.\n'
          '    This script NEVER calls commit(), skip() or playerMove().')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n[!] Interrupted', file=sys.stderr)
        sys.exit(130)
