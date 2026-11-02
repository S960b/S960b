# MMM: the flag is an eigenvector

[По-русски](solve.ru.md)

## 0. Plan

The artifact was a 64-bit ELF from `mmm.zip`. `strings` did not show the flag, which already hints that the acceptance test is computed rather than remembered. The interesting part of the binary was not code but a large numeric table in `.rodata`.

The chain: find the table, read it as a 40 by 40 matrix, notice the constant 1337, solve the eigenvector problem, read the components as ASCII, then confirm the candidate with the binary itself.

## 1. The table in .rodata

```bash
file mmm
readelf -S mmm
objdump -s -j .rodata mmm | head -80
```

The read-only section holds 1600 signed 64-bit values, row-major, starting at file offset `0x3220`. That is exactly 40 rows by 40 columns, which matches the 40-character flag format without any guessing.

The checker multiplies the input vector by this matrix and requires the result to be proportional to the same vector. In other words the input must be an eigenvector:

```text
M * v = 1337 * v
(M - 1337 * I) * v = 0
```

The constant 1337 appears in the comparison code next to the matrix reference. Why this step: with an exact linear relation we can solve for the vector instead of brute-forcing 40 unknown bytes.

## 2. Exact, not numeric

Floating point eigenvector routines return a normalized approximation and are a poor fit for a byte-exact check. SymPy works over rationals:

```python
# scripts/extract_matrix.py --matrix scripts/matrix.json
from sympy import Matrix, eye
rows = json.load(open("scripts/matrix.json"))
M = Matrix(rows)
basis = (M - 1337 * eye(40)).nullspace()
```

The nullspace has dimension 1, so the direction is unique. The basis vector comes out with a common denominator of 125; multiplying by it gives integers in the printable range. No other normalization satisfies `M v = 1337 v` over integers, so there is no ambiguity in sign or scale.

Why this step: a one dimensional nullspace means the first eigenvectors we find are the answer, and the integer normalization doubles as a correctness check.

## 3. From vector to text

The 40 scaled components are exactly the ASCII codes of the input the binary expects, `ctfcup{...}` with 32 hex characters inside. This is consistent with the 40 by 40 size: the matrix is built for a 40-byte secret.

## 4. Dynamic confirmation

Running the target with the recovered candidate prints:

```text
The seal breaks. Carry what you brought to the gate.
```

and exits with status 0. A single changed character is rejected. The binary also stores a SHA-256 of the input and compares it in the same routine, which gives a second, independent confirmation that the recovered text is the intended one.

Why this step: the eigenvector relation alone could have a spurious solution in principle; the binary accepting the string removes that doubt.

## 5. Reproduction

```bash
python3 scripts/extract_matrix.py path/to/mmm
python3 scripts/extract_matrix.py --matrix scripts/matrix.json
```

The first form reads the ELF at offset `0x3220`, writes `scripts/matrix.json` and solves; the second form solves from the bundled table. Both print the nullspace dimension and the candidate string. The flag itself is masked in the text as `ctfcup{...}`.

## 6. What this teaches

- A reversing task can hide a linear algebra problem instead of a comparison loop.
- Exact arithmetic beats floating point when the acceptance test is byte-exact.
- The shape of the data (1600 values, 40 by 40) is often a stronger hint than any string in the binary.

One paragraph: the binary stores a 40 by 40 integer matrix in `.rodata` and accepts an input only if the matrix scales it by 1337; the nullspace of `M - 1337 I` is one dimensional, its integer normalization is printable ASCII, and the binary plus its embedded SHA-256 check confirm the recovered string.
