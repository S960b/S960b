# MMM: the flag hidden in an eigenvector

[По-русски](solve.ru.md)

## 0. Outline

The goal was to find the input accepted by a 64-bit ELF. The unusual clue was that the checking code was built around a large numerical table, not a straightforward byte-by-byte password comparison.

I used the following path:

1. Inspect the ELF and its read-only data.
2. Identify the table as a square 40 by 40 matrix.
3. Work out the relation tested by the binary.
4. Recover the eigenspace associated with 1337.
5. Interpret the vector as character data.
6. Check the candidate hash and test it against the binary.

## 1. Starting from the binary

```bash
file mmm
readelf -S mmm
objdump -s -j .rodata mmm | head -80
strings -a mmm | head -50
```

An ordinary `strings` search was not the solution. The interesting values lived in `.rodata` in a structure suitable for numeric operations. This is where it helped to follow references from the validation routine back to its data, rather than searching the entire file for the flag prefix.

The extraction gave **1600 matrix elements**, arranged as **40 rows and 40 columns**. The important extra constant was **1337**. The core linear algebra relation can be written without guessing individual matrix entries:

```text
M * v = 1337 * v
(M - 1337 * I) * v = 0
```

If the program encodes a particular vector `v` in its nullspace, reconstructing it is easier than guessing the input one character at a time.

## 2. Why a nullspace is useful

Let `I` be the 40 by 40 identity matrix. Subtracting 1337 from the diagonal of `M` creates `A = M - 1337 I`. The target is a nonzero vector with `A v = 0`.

For integer-valued matrices, exact arithmetic is preferable to numerical eigenvectors. Floating point routines can produce tiny errors and scale a result arbitrarily. A symbolic nullspace solver gives rational components that can be normalized to integers.

Given a correctly extracted `matrix.json` containing a list of 40 rows, an exploratory check is:

```python
import json
from sympy import Matrix, eye

rows = json.load(open("matrix.json"))
assert len(rows) == 40 and all(len(r) == 40 for r in rows)
M = Matrix(rows)
basis = (M - 1337 * eye(40)).nullspace()
print("nullity:", len(basis))
for vec in basis:
    print(list(vec))
```

The basis is a space of candidate vectors, not automatically a ready-made ASCII string. If the nullspace has more than one dimension, the binary's other checks determine which combination to choose. If it has one dimension, the sign and scale still need normalization.

The original `.rodata` offsets and all 1600 numeric values were not preserved in the chat transcript. This snippet is a reproducible analysis method, not a claim that I have recreated the precise input data.

## 3. Turning linear algebra back into text

The successful investigation treated the recovered eigenvector as ASCII-oriented data. A useful verification discipline is:

- Normalize the rational basis consistently.
- Check the chosen integer components are in a sensible byte range.
- Try the sequence in the order the binary reads it, not an arbitrary sorted order.
- Confirm the vector still satisfies `M v = 1337 v` exactly after normalization.
- Check the resulting candidate with the hash routine used by the program.

The genuine output was a flag-format candidate, not a generic message. The final SHA-256 check agreed with the verifier in the challenge binary.

## 4. Dynamic confirmation

The decisive check was running the target ELF with the recovered candidate. It printed:

```text
The seal breaks. Carry what you brought to the gate.
```

The process exited with status **0**. The team scoreboard later included MMM as a submitted 100-point task.

The accepted flag is masked as `ctfcup{...}`. Publishing the exact hash-shaped characters adds little to the explanation; the interesting trick is recognizing the eigenvector and reconstructing it.

## 5. Why this approach worked

Many reversing problems encourage patching jumps or brute-forcing bytes. Here the code itself exposed the algebraic invariant. Once `1337` was identified as an eigenvalue and the 40 by 40 array was extracted, the work reduced to exact linear algebra. The binary's own hash and success message provided independent checks.

What is missing for a byte-for-byte rerun is the original `mmm` ELF and its extracted matrix. No fake matrix or hardcoded flag generator is included.
