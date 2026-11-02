#!/usr/bin/env python3
"""Pull the 40x40 matrix out of the challenge ELF and solve it.

Usage:
    python3 extract_matrix.py path/to/mmm                  # write matrix.json
    python3 extract_matrix.py --matrix scripts/matrix.json  # solve only

The matrix sits in .rodata at file offset 0x3220 as 1600 little-endian
signed 64-bit integers, row-major. The checker requires
M * v = 1337 * v, so the flag is the integer eigenvector normalized to
printable bytes.
"""
import json
import struct
import sys
from math import gcd
from pathlib import Path

try:
    from sympy import Matrix, eye
except ImportError as exc:
    raise SystemExit("Install SymPy with: python3 -m pip install sympy") from exc

MATRIX_OFFSET = 0x3220
SIDE = 40
EIGENVALUE = 1337


def read_matrix(elf_path):
    data = Path(elf_path).read_bytes()
    values = struct.unpack_from("<%dq" % (SIDE * SIDE), data, MATRIX_OFFSET)
    return [list(values[i * SIDE:(i + 1) * SIDE]) for i in range(SIDE)]


def solve(rows):
    matrix = Matrix(rows)
    if matrix.shape != (SIDE, SIDE):
        raise SystemExit("Expected a %dx%d matrix" % (SIDE, SIDE))
    kernel = (matrix - EIGENVALUE * eye(SIDE)).nullspace()
    print("nullspace dimension:", len(kernel))
    if len(kernel) != 1:
        raise SystemExit("expected a one dimensional eigenspace")
    vector = kernel[0]
    assert matrix * vector == EIGENVALUE * vector
    # Components are rationals that share a small denominator; scale to bytes.
    scale = 1
    for value in vector:
        scale = scale * value.q // gcd(scale, value.q)
    components = [int(value * scale) for value in vector]
    if any(c < 32 or c > 126 for c in components):
        raise SystemExit("components are not printable")
    candidate = "".join(chr(c) for c in components)
    assert matrix * Matrix(components) == EIGENVALUE * Matrix(components)
    print("candidate:", candidate)
    return candidate


def main(argv):
    if len(argv) == 2 and argv[1] == "--matrix":
        raise SystemExit("pass a matrix path with --matrix <file>")
    if len(argv) == 3 and argv[1] == "--matrix":
        rows = json.loads(Path(argv[2]).read_text())
        solve(rows)
        return 0
    if len(argv) != 2:
        raise SystemExit(__doc__)
    rows = read_matrix(argv[1])
    out = Path(__file__).with_name("matrix.json")
    out.write_text(json.dumps(rows))
    print("wrote", out)
    solve(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
