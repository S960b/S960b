#!/usr/bin/env python3
"""CTFCUP chirp receiver + GF(8) RS(7,3) + linear xorshift128 state recovery.
Usage: python3 solve_chirp.py capture.wav
       python3 solve_chirp.py capture.wav.octal.txt --octal
Dependencies for WAV: numpy, scipy. Octal mode needs only Python stdlib.
"""
import argparse, zlib, sys

P = 0xB                       # GF(8) = GF(2)[x]/(x^3+x+1)
M = 0xffffffff

def mul(a,b):
    o=0
    while b:
        if b&1: o^=a
        b>>=1
        a<<=1
        if a&8:a^=P
    return o

def rs_encoder(triple):
    a,b,c=triple
    return tuple(a ^ mul(b,x) ^ mul(c,mul(x,x)) for x in range(1,8))

# The first three evaluations at GF(8) points 1,2,3 determine the quadratic.
RS_LUT = {rs_encoder((a,b,c))[:3]:(a,b,c)
          for a in range(8) for b in range(8) for c in range(8)}
assert len(RS_LUT)==512

def unpack_fec(octals):
    assert len(octals)>=15 and (len(octals)-8)%7==0, 'wrong chirp count'
    # Only the first 8 are a fixed preamble, excluded from FEC.
    raw=[]; bad=[]
    # WAV maps encoded symbol q to tone lookup [2,0,3,1] and phase q&1.
    # Demodulator's convention is tone + 4*phase, so undo that mapping.
    inv_tone=(1,3,0,2)
    payload_octals=[2*inv_tone[o & 3]+(o>>2) for o in octals[8:]]
    for i in range((len(octals)-8)//7):
        code=tuple(payload_octals[7*i:7*i+7])
        triple=RS_LUT[code[:3]]
        if rs_encoder(triple)!=code:
            bad.append(i)
        raw.extend(triple)
    return raw,bad

def xorshift128(state, n):
    """Faithful port of 0x210f80: 4 little-endian u32 state words.
    Output is xor of the parities of two masked state words."""
    a,b,c,d = state
    for _ in range(n):
        t=(b << 9)&M
        c ^= a
        d ^= b
        b ^= c
        a ^= d
        c ^= t
        d=((d<<11)|(d>>21))&M
        yield (((d & 0xa361c95b).bit_count() ^
                (b & 0x7d284e13).bit_count()) & 1)

def recover_state(raw):
    # Each octal symbol has 2 directly exposed xorshift output bits.
    obs=[]
    for s in raw:
        obs.extend(((s>>2)&1,(s>>1)&1))
    k=len(raw)
    rows=[0]*(2*k)
    for bit in range(128):
        state=[0,0,0,0]
        state[bit//32]=1<<(bit%32)
        v=xorshift128(state,3*k)
        for i in range(k):
            if next(v): rows[2*i] |= 1<<bit
            if next(v): rows[2*i+1] |= 1<<bit
            next(v)
    pivots={}
    mask=(1<<128)-1
    for coeff,observed in zip(rows,obs):
        equation=coeff|(observed<<128)
        while equation&mask:
            j=(equation&mask).bit_length()-1
            if j in pivots:
                equation ^= pivots[j]
            else:
                pivots[j]=equation
                break
        if (equation&mask)==0 and (equation>>128)&1:
            raise ValueError('Inconsistent equations: octal demodulation or model is wrong')
    print('xorshift rank:',len(pivots),'of 128')
    if len(pivots)!=128:raise ValueError('Insufficient independent leakage to recover state')
    solution=0
    for bit in sorted(pivots):
        e=pivots[bit]
        v=((e>>128)&1) ^ ((e & (1<<bit)-1 & solution).bit_count()&1)
        solution|=v<<bit
    for eq,ob in zip(rows,obs):
        if ((eq&solution).bit_count()&1)!=ob:
            raise ValueError('GF(2) reconstructed state verification failure')
    return [(solution>>(32*j))&M for j in range(4)]

def unscramble(raw,state):
    r=xorshift128(state,3*len(raw))
    bits=[];prev=0
    for symbol in raw:
        p0=next(r);p1=next(r);p2=next(r)
        assert symbol>>1==((p0<<1)|p1)
        x=symbol&1
        bits.append(x ^ prev ^ p2)
        prev=x
    return bits

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input')
    ap.add_argument('--octal', action='store_true', help='input contains ASCII octal digits, not WAV')
    args=ap.parse_args()
    if args.octal or args.input.endswith('.txt'):
        s=open(args.input).read().strip()
        if any(c not in '01234567' for c in s):raise ValueError('non-octal character in input')
        octals=[int(c) for c in s]
    else:
        from scipy.io import wavfile
        from decode_capture import demodulate
        sr,x=wavfile.read(args.input)
        assert sr==48000 and x.ndim==1
        octals=list(map(int,demodulate(x)[0]))
    print('chirps:',len(octals),'preamble:',''.join(map(str,octals[:8])))
    raw,bad=unpack_fec(octals)
    print('FEC blocks:',(len(octals)-8)//7,'invalid:',len(bad),bad[:20])
    if bad:raise ValueError('Invalid GF(8) RS(7,3) words: check demodulation')
    print('raw symbols:',len(raw))
    N=len(raw)//3
    possible=[L for L in range(8,4097) if (8*(L+4)+2)//3==N]
    print('possible payload lengths:',possible)
    if len(possible)!=1:raise ValueError('Ambiguous payload length')
    nbits=8*(possible[0]+4)
    state=recover_state(raw[:nbits])
    print('reconstructed PRNG state:',' '.join(f'{x:08x}' for x in state))
    all_bits=unscramble(raw[:nbits],state)
    for L in possible:
        if any(all_bits[nbits:]):
            print('WARNING: nonzero final padding bits for length',L, all_bits[nbits:])
        decoded=bytes(int(''.join(str(z) for z in all_bits[i:i+8]),2)
                      for i in range(0,nbits,8))
        payload,checksum=decoded[:-4],decoded[-4:]
        print('payload bytes:',payload)
        print('payload text:',payload.decode('utf-8','replace'))
        print('checksum bytes:',checksum.hex(), 'crc32LE:',zlib.crc32(payload).to_bytes(4,'little').hex(),
              'crc32BE:',zlib.crc32(payload).to_bytes(4,'big').hex())
        with open(args.input+'.recovered.bin','wb') as out:out.write(payload)

if __name__=='__main__':main()
