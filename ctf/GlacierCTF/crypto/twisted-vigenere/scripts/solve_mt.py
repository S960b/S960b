#!/usr/bin/env python3
"""GlacierCTF Twisted Vigenere: GF(2) recovery from Python MT19937 twist.

Requires only Python 3.10+ and organizer output.txt.
Usage:
  python3 solve_mt.py output.txt
  python3 solve_mt.py output.txt 'gctf{...48-byte-candidate...}'

The original data uniquely fixes positions 0..43 and 47 for a 48-byte
ASCII key; bytes 44..46 are not identifiable from the 10 post-twist
MT19937 outputs. A successful scoreboard submission resolves them.
"""
import base64
import pathlib
import sys

MASK = 0xffffffff
KEYLEN = 48
PREFIX = b"gctf{"
SUFFIX = b"}"


def undo_right(y, shift):
    x = y
    for _ in range(5):
        x = y ^ (x >> shift)
    return x & MASK


def undo_left(y, shift, mask):
    x = y
    for _ in range(6):
        x = y ^ ((x << shift) & mask)
    return x & MASK


def untemper(y):
    y = undo_right(y, 18)
    y = undo_left(y, 15, 0xefc60000)
    y = undo_left(y, 7, 0x9d2c5680)
    return undo_right(y, 11)


def temper(x):
    x ^= x >> 11
    x ^= (x << 7) & 0x9d2c5680
    x ^= (x << 15) & 0xefc60000
    return (x ^ (x >> 18)) & MASK


def residual(cipher, key):
    """Mismatch in the 10 outputs predicted by the 624 previous words."""
    stream = bytes(c ^ key[i % len(key)] for i, c in enumerate(cipher))
    words = [int.from_bytes(stream[i:i + 4], "little")
             for i in range(0, len(stream), 4)]
    state = [untemper(w) for w in words[:624]]
    out = 0
    for j in range(10):
        y = (state[j] & 0x80000000) | (state[j + 1] & 0x7fffffff)
        nxt = state[j + 397] ^ (y >> 1)
        if y & 1:
            nxt ^= 0x9908b0df
        out |= (temper(nxt) ^ words[624 + j]) << (32 * j)
    return out


def eliminate(rows, nbits):
    pivots = {}
    coefmask = (1 << nbits) - 1
    for row in rows:
        while True:
            coef = row & coefmask
            if not coef:
                if row >> nbits:
                    raise ValueError("inconsistent linear system")
                break
            pivot = (coef & -coef).bit_length() - 1
            if pivot in pivots:
                row ^= pivots[pivot]
            else:
                pivots[pivot] = row
                break
    return pivots


def backsub(pivots, freevalue, nbits):
    result = freevalue
    for pivot in sorted(pivots, reverse=True):
        row = pivots[pivot]
        rhs = (row >> nbits) & 1
        rhs ^= (row & result).bit_count() & 1
        if rhs:
            result |= 1 << pivot
    return result


def recover(cipher):
    if len(cipher) != 2536:
        raise ValueError(f"expected 2536 bytes; got {len(cipher)}")
    nbits = KEYLEN * 8
    base = residual(cipher, bytes(KEYLEN))
    cols = []
    for i in range(nbits):
        delta = bytearray(KEYLEN)
        delta[i // 8] = 1 << (i % 8)
        cols.append(residual(cipher, delta) ^ base)

    # Equation j: parity(sum of selected key bits with cols[i][j]=1)
    # equals the j-th bit of the zero-key prediction mismatch.
    equations = []
    for j in range(320):
        coeffs = sum(1 << i for i, c in enumerate(cols) if (c >> j) & 1)
        equations.append(coeffs | (((base >> j) & 1) << nbits))

    # Public flag framing, and one high ASCII bit per byte.
    for pos, char in list(enumerate(PREFIX)) + [(KEYLEN - 1, SUFFIX[0])]:
        for bit in range(8):
            equations.append((1 << (8 * pos + bit)) |
                             (((char >> bit) & 1) << nbits))
    for i in range(KEYLEN):
        equations.append(1 << (8 * i + 7))

    pivots = eliminate(equations, nbits)
    free = [j for j in range(nbits) if j not in pivots]
    particular = backsub(pivots, 0, nbits)
    homogeneous = [backsub(pivots, 1 << j, nbits) ^ particular for j in free]

    chars = []
    unresolved = []
    for i in range(KEYLEN):
        varying = 0
        for h in homogeneous:
            varying |= (h >> (8 * i)) & 255
        if varying:
            chars.append("?")
            unresolved.append(i)
        else:
            chars.append(chr((particular >> (8 * i)) & 255))

    return {
        "equations": len(equations),
        "rank": len(pivots),
        "free": len(free),
        "pattern": "".join(chars),
        "unknown_positions": unresolved,
    }


def main():
    source = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "output.txt")
    cipher = base64.b64decode("".join(source.read_text().split()), validate=True)
    result = recover(cipher)
    print("ciphertext bytes:", len(cipher))
    print("key length assumption:", KEYLEN)
    print("equations:", result["equations"],
          "rank:", result["rank"], "free bits:", result["free"])
    print("recovered:", result["pattern"])
    print("unknown byte positions:", result["unknown_positions"])
    if len(sys.argv) > 2:
        candidate = sys.argv[2].encode("ascii")
        if len(candidate) != KEYLEN:
            raise ValueError("candidate is not 48 bytes")
        errors = residual(cipher, candidate).bit_count()
        print("MT19937 next-state mismatched bits:", errors)
        if errors:
            raise SystemExit("candidate inconsistent with original ciphertext")
        print("Candidate passes all 320 MT checks.")
        print("Warning: the final unknown bytes still require an external criterion.")


if __name__ == "__main__":
    main()
