# CSI-HASH — a 32-byte second preimage from a quadratic-order class-group relation

[Русская версия](solve.ru.md)

## 1. The hash construction

The organizer supplies `challenge.sage`: a Sage implementation of a CSIDH-like group action. There are 32 small odd primes

```python
PRIMES = [3,5,7,11,13,17,19,23,29,31,37,41,43,47,53,59,
          61,67,71,73,79,83,89,97,101,103,107,109,113,127,131,179]
p = 4 * prod(PRIMES) - 1
# 188270939500840731196151103351923464818418726348041539 (177 bits)
```

The main routine selects a random **32-character ASCII-letter string** and asks for a distinct message with the same digest. The service verifies `challenge != message` and `hash.digest(challenge.encode()) == hash.digest(message.encode())`; success prints the flag.

For one 32-byte block `m`, `process_block` sets `es = list(m)` and performs prime-degree isogenies on the Montgomery curve `y²=x³+Ax²+x` over `GF(p²)`. Each exponent is the numeric byte value (0–255). The output is a Montgomery coefficient identifying the resulting curve. As an ideal-class action, it depends on the **class of the product** of 32 ideals to those exponents, rather than the order of all individual isogenies.

This is fundamentally a *second-preimage/collision* problem in the class-group action, not a SHA or AES attack.

## 2. The precise quadratic order matters

The endomorphism order used by this action is

```text
O = Z[sqrt(-p)]         discriminant D = -4p
K = Q(sqrt(-p))         maximal-order discriminant D_K = -p
```

Since `p ≡ 3 (mod 8)`, `2` is inert and `O` has conductor 2. The kernel of the natural map

```text
Cl(O) → Cl(O_K)
```

has order **3**, so an integer relation in the maximal class group must be **multiplied by 3** before being used as an identity in the actual order's class group. Omitting this multiplication produced relations that failed exact order checks in the toy model.

PARI measured:

```text
h(-p)  =  407420844431758343694144149
h(-4p) = 1222262533295275031082432447 = 3*h(-p)
```

Both groups were cyclic. The maximal class number is an 89-bit prime, making direct baby-step giant-step or Pohlig–Hellman unattractive.

## 3. Computing 32 ideal class logarithms

The expensive research stage, run **offline before connecting** to the 180-second CTF service, uses PARI `quadclassunit(-p)` for the cyclic invariant factor, then `bnfinit(x²+p)` to compute ideal-class logarithms.

Each prime ideal is `(ell, sqrt(-p)-1)`; its ideal class logarithm is computed with `bnfisprincipal(bnf, ideal, 0)`. The result for all 32 primes, and the class order, is preserved in `scripts/csi_logs.json` in the private evidence ZIP.

Actual agent measurements:

```text
quadclassunit(-p)  ~16 seconds
bnfinit           3164 seconds (~53 minutes)
LLL matrix         32 x 32
```

`bnfisprincipal(..., flag=0)` was intentional: generating full principal generators with flag 1 caused severe stack pressure/overflow. No online request should be started until this precomputation is complete.

## 4. Relation lattice and LLL

Let `g_i` be the discrete log of the `i`-th prime ideal in the cyclic maximal class group of order `h`. A relation vector `v∈Z^32` must satisfy

```text
sum(v_i * g_i) = 0 (mod h).
```

More generally, if the class group has invariant factors `D = diag(d_j)`, form the integer matrix `M = [C | -D]` with `C_{j,i} = g_{i,j}`. The integer right kernel of `M`, projected to its first 32 coordinates, is the lattice of relations. Run LLL, **then multiply each basis vector by 3** to obtain safe relations in `Cl(O)`.

The successful implementation uses `cypari2` and `fpylll`, with two important API details:

- `Pari.matrix(rows, cols, flat)` consumes entries in **column-major** order, not Python's usual row-major layout.
- `matkerint(M)` returns **basis vectors as columns**. Incorrectly reading its rows was the major bug in the first script.

The fixed solver verified all **32** LLL relation rows in the exact `D=-4p` order via PARI's `Qfb`, `qfbcomp`, `qfbpow`, `qfbred`. The LLL basis had maximum entry magnitude 21 after multiplication by 3.

## 5. The unexpectedly simple universal relation

The shortest lattice relation in the maximal order is

```text
(1,1,1,...,1).
```

Its complete class-log sum vanishes modulo `h`. In the non-maximal order, the corresponding safe relation is `(3,3,...,3)`. Choosing its negative gives

```text
m'_i = m_i - 3  for all 32 bytes i.
```

This is a **valid second preimage for every 32-letter ASCII challenge**: uppercase letters range 65–90 and lowercase 97–122, so subtracting 3 leaves bytes in printable 33–126. The two messages are distinct, both exactly 32 bytes, and preserve the same ideal-class action.

This is why the final online exploit does not need Sage, PARI, LLL, or precomputed caches: the expensive mathematics was needed to **discover and validate** the relation, not to apply it.

## 6. Successful remote exchange

The preserved original remote transcript shows:

```text
Welcome to CSI-HASH
I bet you aren't able to find a preimage!
Here is your challenge: PBERSLhMGAMlyzVagLFVVXyYMwdKGIml
> Let me compute the hashes ... this might take some time
Wow, you fund a preimage?
Here, take this: gctf{...}
```

The final solver's separate log records the submitted string:

```text
challenge: PBERSLhMGAMlyzVagLFVVXyYMwdKGIml
preimage:  M?BOPIeJD>JivwS^dICSSUvVJtaHDFji
delta:     [-3] * 32
```

**The server returned the real flag**, and the participant confirms acceptance on the CTF platform. The full flag remains in `FLAG.txt`, `evidence/csi_remote.log`, `evidence/csi_fixed.log` inside the private bundle, not in this repository.

The code in [scripts/solve.py](scripts/solve.py) reproduces the final online transformation without the expensive precomputation.

## 7. Failed paths and practical lessons

The earlier attempts matter:

1. Installing SageMath via apt on Kali failed because of inconsistent Python 3.13/3.14 and other packages. Pulling `sagemath/sagemath:10.6` via Docker stalled. The successful solver used **PARI/GP through cypari2**, plus fpylll.
2. `bnfinit` with principal-generator output overflowed PARI's stack. Recomputing the class group with Sage would still rely on PARI and not remove this expense.
3. The first log collector called nonexistent `pari.bnf_get_cyc` after the very expensive `bnfinit`; this was corrected to use `quadclassunit` and `bnfisprincipal(..., 0)`.
4. A first lattice script incorrectly built the matrix in row-major order and read `matkerint` by rows. A small **8-prime, 29-bit toy model** exposed the mistakes before another long computation.
5. The original Sage `digest()` has a missing `i += 1` in its multi-block loop and incorrectly compares last-block length against 4 instead of 32. We therefore submit a single **exactly 32-byte** message; using more than one block risks a timeout, and other lengths alter padding.

This is a class-group relation attack, not a claim that LLL universally breaks properly parameterized isogeny cryptography. The very particular message encoding and easy printable relation make this instance vulnerable.

## 8. Reproduction boundaries

Original research scripts are retained by the user in `glacierctf_csi-hash_materials.zip`: `get_logs2.py`, `csi_logs.json`, `csi_lll_fixed.py`, `toy2.py`, earlier broken variants and all logs. The Sage baseline `solve_csi_hash.sage` was **never executed** due to environment setup failure.

No local complete Sage isogeny hash comparison was performed on Kali; the exact-order PARI quadratic-form check and the **live server's accepted preimage** establish the relevant result. No CTF network traffic was generated during writing.
