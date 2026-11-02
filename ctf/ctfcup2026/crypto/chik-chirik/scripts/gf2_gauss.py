#!/usr/bin/env python3
"""Small exact GF(2) linear system solver for PRNG state experiments.

Rows are represented as Python integers. Input is a JSON document containing:
  {"variables": 3, "rows": [[5, 1], [3, 0], [6, 1]]}
Each pair contains the coefficient bitmask and the right-hand-side bit.
For a 128-bit xorshift state, supply equations derived from the actual observed
symbol mixer. This tool does not claim to generate those equations.
"""
import json
import sys
from pathlib import Path


def solve(rows, n):
    basis = {}
    for mask, rhs in rows:
        mask = int(mask)
        rhs = int(rhs) & 1
        if mask < 0 or mask >> n:
            raise ValueError("Coefficient outside variable range")
        while mask:
            pivot = mask.bit_length() - 1
            if pivot not in basis:
                basis[pivot] = (mask, rhs)
                break
            m, r = basis[pivot]
            mask ^= m
            rhs ^= r
        else:
            if rhs:
                raise ValueError("Inconsistent equations")
    if len(basis) != n:
        raise ValueError(f"Rank {len(basis)}, expected {n}; state not unique")
    x = 0
    for pivot in sorted(basis):
        mask, rhs = basis[pivot]
        other = (mask & x).bit_count() & 1
        if rhs ^ other:
            x |= 1 << pivot
    for mask, rhs in rows:
        if ((int(mask) & x).bit_count() & 1) != (int(rhs) & 1):
            raise ValueError("Solution failed row verification")
    return x


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 gf2_gauss.py equations.json")
    doc = json.loads(Path(sys.argv[1]).read_text())
    value = solve(doc["rows"], int(doc["variables"]))
    print(f"state = 0x{value:x}")


if __name__ == "__main__":
    main()
