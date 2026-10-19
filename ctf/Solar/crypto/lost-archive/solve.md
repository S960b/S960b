# Lost Archive: solving path

Flag at the end: `SOLARCTF{...}`.

## 1. What is given

The challenge came as two files, `generate.py` and `output.txt`. The generator is short, so
I read it fully first instead of starting with guesses.

The scheme is clear from the code:

- `E = 65537`, four independent RSA instances, 1024-bit primes, modulus about 2048 bits.
- The flag is 32 bytes and is split into four chunks of exactly 8 bytes.
- Each chunk is padded on the left with 24 random bytes, giving a 32-byte message encrypted
  as `c = m^e mod n`.
- For each instance the output contains `h = p >> 506`, the 518 most significant bits of one
  factor. The file does not say which factor leaked.
- Moduli are checked for pairwise coprimality, so the shared-prime attack is ruled out by
  construction.

`output.txt` starts with `4`, `65537`, `32`, followed by four triples `(n, c, h)`.

Why this step: the fact that high bits of a factor leak puts the challenge into partial key
exposure territory, where the right tool is a lattice, not a search.

## 2. Reducing to a small root problem

Let `P = h << 506`. The unknown factor is `p = P + x` with `x < 2^506`. Since `p` divides
`n`, the polynomial `f(x) = P + x` has a root modulo `p`.

For Coppersmith with a divisor, the root is found when `x` is below `n^(beta^2)`, where
`beta` is the divisor share of the modulus. Here `beta = 0.5`, so the bound is `n^0.25`,
about `2^512` for a 2048-bit modulus.

Our `x` is bounded by `2^506`. There is headroom, but only about 6 bits.

Why this step: 6 bits of headroom explains why the method applies at all yet demands a
careful lattice rather than brute force.

## 3. First approach, and why it drowned

I built the lattice in the Howgrave-Graham form: rows `g_i = N^(m-i) f(xX)^i` for `i` from
0 to `m-1`, and rows `h_j = x^j f(xX)^m` for `j` from 0 to `t-1`, dimension `m+t`.

To cover 506 unknown bits with 6 bits of margin, the dimension has to be around 131, that is
`m` about 65. At that size the entries reach roughly 120 thousand bits and fplll runs for
hours.

I tried to lower the reduction parameter: with `delta = 0.75` at dimensions 80 and 95 no
root appeared, the reduction is too weak. With `delta = 0.99` the quality is sufficient but
the time is unacceptable.

Why this step: it showed that one large lattice is the wrong shape, and the fix is to cut
the dimension rather than tune parameters.

## 4. The idea that worked: guess a few high bits

If `g` additional high bits of the factor are guessed, the unknown part shrinks to
`506 - g` bits and `X = 2^(506-g)`. The margin against the `n^0.25` bound grows to `6 + g`
bits.

One lattice of dimension 131 becomes `2^g` independent lattices of much smaller size. With
`g = 5` that is 32 candidates, dimension 59 (`k = 29`, `t = 30`), and an 11-bit margin. The
starting point for a candidate is:

```
P = ((h << g) | guess) << (506 - g)
```

Why this step: guessing 5 bits costs 32 runs, but each run becomes several times cheaper,
and the total is far below one huge lattice.

## 5. Tools and why these ones

Sage was not available, and neither was sudo. I looked for a CPU LLL implementation that
installs into a local directory and picked `flatn` 0.3.5: it is a Python wrapper over
flatter, and flatter ships as a binary inside the wheel. `sympy` was needed for polynomial
and modular arithmetic.

Local install without root:

```
python3 -m pip install --target ./deps 'flatn==0.3.5' 'sympy>=1.12'
export PYTHONPATH="$PWD/deps${PYTHONPATH:+:$PYTHONPATH}"
```

Threads are pinned to one per worker, otherwise BLAS and OpenMP oversubscribe the cores and
everything slows down:

```
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
```

Why this step: without a fast CPU reduction and without thread control the full run stretches
into hours.

## 6. Extracting the root without Sage

After the reduction two short vectors `v` are used to build polynomials

```
H(x) = sum v_d * X^(n-1-d) * x^d
```

Then `gcd(H1, H2)` is computed over `F_q` for several 31-bit primes, and the root is
reassembled by CRT until the product exceeds `X`. Finally `p = P + x`, checked by exact division of the modulus.

Why this step: this replaces the Sage `small_roots` and `roots` helpers with plain Python,
which is what made the no-Sage environment workable.

## 7. Measurements and the full run

First measurement on `instance 0`, dimension 59, two workers: two reductions of 29.77 and
30.92 seconds, `certified_rows = 8`, Howgrave-Graham margin 45 bits (across the full run it
ranged from 45 to 61 bits).

Full run:

```
python3 solve_rsa_partial.py --input output.txt --guess-bits 5 --workers 8 --delta 0.99
```

Result: 126 reductions, 454.8 seconds. The factors were found at guessed prefixes of 12, 12,
16 and 24 bits, one per instance. State is written to `rsa_progress.json`, so the run can be
interrupted and resumed.

Why this step: the numbers show the cost is predictable rather than lucky, and that the task
is solvable on an ordinary machine without a GPU.

## 8. Decryption and assembly

Once a factor is found, the other is `q = n // p`, then `d = e^(-1) mod phi(n)` and
`m = c^d mod n`. The chunk sits in the low bytes: with a 32-byte flag every chunk is exactly
8 bytes, so it is `m & (2^64 - 1)`.

The four chunks are concatenated in instance order. The first gave the prefix `SOLARCTF`, the
other three gave the body and the closing brace. Final string: `SOLARCTF{...}`.

Why this step: this is a direct check that the factors are genuine, because decryption yields
readable text in the flag format.

## 9. What did not work

- A single large lattice of dimension around 131. fplll on 120-thousand-bit entries runs for
  hours and gave no practical result.
- `delta = 0.75` at dimensions 80 and 95. The reduction is too weak, no root.
- `delta = 0.99` at large dimension. Quality is enough, time is not, hence guessing high bits.
- Shared-prime attack. Ruled out by the generator, moduli are checked for pairwise coprimality.
- GPU. There are no mature LLL implementations for big integers on GPU, the computation is
  almost sequential, so a video card does not help here.

## 10. In one paragraph

The generator leaks the 518 most significant bits of one factor, so `p = P + x` with 506
unknown low bits against a Coppersmith bound of about `2^512`. A single lattice is too slow,
so I guess 5 more high bits and get 32 lattices of dimension 59 instead of one of dimension
131. The reduction runs on flatter through `flatn`, the root is recovered by gcd of
polynomials over small fields plus CRT, and the factor is verified by dividing the modulus.
Decryption yields four chunks that assemble into `SOLARCTF{...}`.

## 11. Lessons

- Estimate the margin before choosing an attack: 6 bits above the bound means the method
  works but needs dimension control.
- Splitting one large lattice into many small ones by guessing a few bits is often better
  than tuning the reduction parameter.
- Memory and thread limits matter more than core count: one thread per worker gives a larger
  total win.
- An exact division of the modulus plus readable plaintext is a sufficient criterion that a
  real factor was found and not a lattice artifact.
