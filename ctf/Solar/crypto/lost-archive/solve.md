# Lost Archive: solving path

Flag at the end: `SOLARCTF{...}`.

## 0. The whole plan, before any details

So that the reader knows where this is going, here is the chain first. The details follow.

Given: four moduli `n`, four ciphertexts `c`, and one piece of key information per instance.
We need the flag bytes.

My plan was:

1. Learn from the code what exactly is encrypted and what is known about the key.
2. Recover one prime factor `p` of each modulus from that information.
3. From `p` get `q = n / p`, then the private exponent `d`, and decrypt `c`.
4. Take the low 8 bytes of the decrypted message, that is one flag chunk.
5. Concatenate the four chunks in order to get `SOLARCTF{...}`.

The hard part is step 2, and almost the whole text below is about it. The rest is mechanical.

## 1. Running the generator: what the output looks like

Why this step: before reading the code I wanted to see the data itself, to learn its shape and
to check that I map the numbers to their roles correctly.

I opened a terminal in the challenge folder and ran the generator on a test flag of the right
length:

```
FLAG='SOLARCTF{0123456789abcdefghijkl}' python3 generate.py > demo_out.txt
```

The file contained 15 lines. The first three numbers:

```
4
65537
32
```

Then came twelve large numbers, that is three numbers per instance, four instances.

Then I measured the lengths instead of trusting the eye:

```python
vals=[int(x) for x in open('demo_out.txt').read().split()]
ns, cs, hs = vals[3::3], vals[4::3], vals[5::3]
# bits(n): 2047 2047 2048 2048
# bits(h): 518  518  518  518
```

So the moduli are 2047 or 2048 bits, and the third number of each triple is exactly 518 bits.

How I knew those three numbers mean this and not something else: I checked the code. Line 90
of `generate.py` builds the header:

```python
values = [N_INSTANCES, E, len(flag)]
```

and line 87 adds one tuple per instance:

```python
records.append((n, pow(message, E, n), h))
```

So the order is: instance count, exponent, flag length, then four repetitions of the triple
modulus, ciphertext, leak. The measured lengths confirm this.

## 2. What exactly is encrypted: reading make_messages

Why this step: we need to know precisely where in the decrypted number the flag bytes sit,
otherwise after recovering the key we will not know what to take from the result.

I look at lines 60 to 67:

```python
base, extra = divmod(len(flag), N_INSTANCES)
for index in range(N_INSTANCES):
    chunk_size = base + (index < extra)
    chunk = flag[offset:offset + chunk_size]
    offset += chunk_size
    messages.append(secrets.token_bytes(SHARE_BYTES - chunk_size) + chunk)
```

In plain words. The flag is split into four parts as evenly as possible: if the length is not
divisible by four, the first parts take one extra byte. Our length is 32, divisible exactly,
so every part is 8 bytes.

Each part is padded on the left with random bytes up to 32 bytes. The order of concatenation
makes the random bytes the most significant and the flag part the least significant. In the
code this is the `token_bytes(...) + chunk` order.

A direct consequence for decryption: once we get the number `m`, the low 8 bytes of that
number are the flag chunk. In Python that is `m & (2**64 - 1)`.

Check that the length really is 32: the third number in the output, which line 90 defines as
`len(flag)`, was 32.

## 3. Where the key information comes from: lines 84 to 85

Why this step: this information determines which method can work. Without the leak the task
would be unsolvable, and if a different field leaked the method would be different.

Two lines decide everything:

```python
leaked = p if secrets.randbelow(2) == 0 else q
h = leaked >> (PRIME_BITS - LEAK_BITS)
```

Breaking it down. First one of the two primes, `p` or `q`, is chosen at random. So the file
does not tell which factor we got, and the algorithm has to work for either.

Then the number is shifted right by `PRIME_BITS - LEAK_BITS`, that is by `1024 - 518 = 506`
bits.

Now in plain words, what a right shift is and what high bits are. Any number in a computer is
stored in binary, as a set of zeros and ones. The high bits are the leftmost ones, they set the
order of magnitude, while the low bits are the right tail, they set the exact value within
that magnitude. A right shift by 506 drops the 506 low bits and keeps only the high ones.

What that means in practice. The factor `p` is roughly `h * 2^506` plus an unknown tail `x`
that is smaller than `2^506`. So we know `p` roughly, up to a 506-bit tail:

```
p = h * 2^506 + x,   where 0 <= x < 2^506
```

Why this is called a leak of high bits: the generator itself publishes `h`, and that is
exactly the high bits of the factor.

Why it matters: we now have an equation for `p` with one small unknown `x`, and that is what
the method in the next section works on.

## 4. Why it cannot be solved head-on

Why this step: to confirm that simple approaches fail, and to see which properties of the task
can actually be used.

Three obvious ideas fail immediately.

Brute forcing the tail. The tail is 506 bits, so there are `2^506` options. Even at a billion
checks per second this is orders of magnitude longer than the age of the universe.

Direct division. We cannot compute `gcd(n, h * 2^506)`, because the real factor is
`h * 2^506 + x`, not `h * 2^506`. The tail is unknown and without it the number does not
divide `n`.

The shared factor attack on two moduli. It would work if two moduli shared a prime factor. The
generator rules this out, line 82:

```python
if any(math.gcd(n, old) != 1 for old in moduli):
    continue
```

Every new modulus is checked for coprimality with all previous ones, so there are no shared
factors by construction.

Conclusion: we need a method that finds the small unknown `x` in the statement `h * 2^506 + x`
divides `n`, without knowing the factor of `n`.

## 5. The class of task and Coppersmith in plain words

Why this step: to name the method that finds a small unknown modulo an unknown divisor, and to
understand where the limit on the size of that unknown comes from.

Such tasks are called partial key exposure: part of the secret is known, and the secret must
be recovered from it. Here the high bits of a factor are known.

Coppersmith's method in plain words works like this. There is a polynomial that has a root
modulo an unknown divisor of `n`. If that root is small, it can be found without knowing the
divisor, using a specially built lattice. The word small is essential: the method is
guaranteed only up to a certain bound.

The small root problem is stated like this. Given a polynomial `f` and a modulus `N` known to
have a divisor `p`, find `x0` such that `f(x0)` is divisible by `p`, under the condition that
`x0` does not exceed a known bound `X`. We look for a small root because large roots are too
numerous in the range and are indistinguishable from random numbers.

The bound for a divisor `p` that makes up a share `beta` of `N` is `N` to the power
`beta squared`. Here `p` is about the square root of `N`, so `beta = 0.5`, and the bound is
`N^0.25`.

Let me put the numbers down, because everything later rests on them:

- `N` is about `2^2048`;
- `N^0.25` is `2^512`;
- our tail `x` is below `2^506`;
- the margin is `512 - 506 = 6` bits.

That is where the 6 bits mentioned later come from. There is margin, but it is small.

What a lattice is, in the simplest terms. It is the set of points obtained by integer
combinations of given vectors. A reduction algorithm looks for the shortest vector in that
set. A short vector is valuable because its coefficients give a new polynomial for which our
`x0` is an ordinary integer root, and then the root is found without any modular arithmetic.

How this leads to the flag: we find `x0`, get `p = h * 2^506 + x0`, divide `n` by `p`, get
`q`, compute `d`, decrypt `c`, take the low 8 bytes, and concatenate the four chunks.

## 6. The first approach: one large lattice

Why this step: to try the straightforward construction, see its cost, and understand what has
to be optimised.

I searched for how the lattice is built for a Coppersmith problem with a divisor of the
modulus rather than the modulus itself, and picked the Howgrave-Graham construction. It fits
because it is the standard construction exactly for the case where the root is wanted modulo a
divisor of `N`.

The construction in plain words. We build a family of polynomials, each of which is divisible
by a high enough power of `p` when `x0` is substituted. Then the coefficients of these
polynomials are written into a matrix, and the reduction looks for a short integer combination
of the rows. The rows are:

```
g_i = N^(m-i) * f(x*X)^i,   i = 0 .. m-1
h_j = x^j * f(x*X)^m,       j = 0 .. t-1
```

The matrix dimension is `m + t`. The factor `X` keeps the coefficients integral while moving
the root into the integers.

Then the arithmetic, and it is not encouraging. To cover 506 unknown bits with only 6 bits of
margin, the dimension has to be around 131, that is `m` about 65. The matrix entries then grow
to roughly 120 thousand bits.

The reduction is performed by LLL, short for Lenstra, Lenstra and Lovasz. It finds short
vectors in a lattice in polynomial time, but polynomial does not mean fast: at this size a
single reduction takes hours. I used the fplll library for it.

I did try it. With quality parameter `delta = 0.75` at dimensions 80 and 95 no root appeared,
the reduction is too weak. With `delta = 0.99` the quality became sufficient but the time was
unacceptable.

Conclusion: the dimension has to be cut, not the parameters tuned.

## 7. The idea that worked: guess a few high bits

Why this step: to bring the lattice dimension down to values where a reduction takes seconds,
at the price of a small enumeration.

I arrived at it from the arithmetic of the previous section. The margin was only 6 bits, and
the dimension was large because of that. So reducing the number of unknown bits reduces the
dimension.

How to reduce it if the tail is unknown: guess a few more high bits of the tail. If `g` bits
are guessed, `506 - g` unknowns remain, the bound becomes `2^(506-g)`, and the margin against
`N^0.25` grows to `6 + g` bits.

The price is enumerating `2^g` variants. At `g = 5` that is 32 variants, which is affordable.

The gain: instead of one lattice of dimension 131 we get 32 lattices of dimension 59 (`k = 29`,
`t = 30`) with an 11-bit margin. Each such reduction takes seconds.

The starting point for a specific variant:

```
P = ((h << g) | guess) << (506 - g)
```

Here `h << g` appends `g` zeros, `| guess` inserts the guessed bits, and the shift left by
`506 - g` puts them in their place in the number.

I tested the hypothesis on a synthetic example first, not on the real file: I generated small
factors with a partial leak and ran the solver in self-test mode. It recovered the factor and
decrypted the message at dimension 7. Only then did I run it on the real data.

## 8. Tools and environment setup

Why this step: we need a fast lattice reduction and predictable core usage, otherwise the full
run stretches into hours.

LLL was named above, it is the short vector algorithm. Its fast implementation is fplll, an
open source library that also offers the stronger BKZ. Installing it directly is awkward, so I
took `flatn` version 0.3.5: it is a thin Python wrapper over flatter, and flatter is an
optimised implementation shipped as a ready binary inside the wheel. Plus `sympy` for
polynomial work.

Local install:

```
python3 -m pip install --target ./deps 'flatn==0.3.5' 'sympy>=1.12'
export PYTHONPATH="$PWD/deps${PYTHONPATH:+:$PYTHONPATH}"
```

Now about threads, this is an important detail. BLAS is a set of basic linear algebra
operations, and OpenBLAS is one implementation of it. OpenMP is a mechanism for parallelising
loops. By default both create as many threads as there are cores, and they do it in every
process separately. When I run eight workers, each raises eight threads, so eight cores get
sixty-four threads. This is oversubscription: threads keep evicting each other, the cache
misses, and the total becomes slower rather than faster.

So I left one thread per worker:

```
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
```

I learned this from the flatn and OpenBLAS documentation and checked it by timing: with the
limit the run gets faster.

I did not use a GPU. LLL on big integers is almost sequential, there are no mature GPU
implementations, so there is no gain there.

## 9. Turning the reduction result into the root

Why this step: the reduction returns short vectors, not `x0` itself. The vectors must be turned
into a number, closing the last gap between the mathematics and the factor `p`.

I take the two shortest vectors produced by the reduction. Each vector is a set of
coefficients, and from them I build a polynomial:

```
H(x) = sum v_d * X^(n-1-d) * x^d
```

The `X` factors keep the coefficients integral and preserve the root. Now I have two
polynomials that share `x0` as a common root modulo `p`.

To find the common root I compute the greatest common divisor of the two polynomials. I do it
not over the integers but over finite fields modulo several small primes of about 31 bits.
That is more robust: over the integers the coefficients are huge, while over a small field the
computation is fast and the result does not degenerate.

Each such computation gives the residue of `x0` modulo its prime. I combine the residues with
the Chinese remainder theorem until the product of the moduli exceeds `X`. After that `x0` is
known exactly.

Then `p = P + x0`, and the check: does `n` divide by `p`. If it does, the factor is genuine.

## 10. Measurements

Why this step: to confirm the cost is predictable rather than lucky, and that the task fits
into reasonable machine time.

First measurement on the first instance, dimension 59, two workers: two reductions of 29.77
and 30.92 seconds, eight certified rows, Howgrave-Graham margin 45 bits.

Full run:

```
python3 solve_rsa_partial.py --input output.txt --guess-bits 5 --workers 8 --delta 0.99
```

Result: 126 reductions, 454.8 seconds. The factors were found at guessed prefixes of 12, 12,
16 and 24 bits, one per instance. Progress is written to `rsa_progress.json`, so the run can be
interrupted and resumed.

## 11. Decryption and flag assembly

Why this step: to turn the recovered factor into flag bytes and check that everything agrees.

From `p` I compute `q = n / p`, then Euler's totient `phi = (p - 1) * (q - 1)`, the private
exponent `d` as the inverse of `e` modulo `phi`, and the message `m = c^d mod n`.

From the message I take the low 8 bytes, as agreed in section 2:

```python
chunk = (m & (2**64 - 1)).to_bytes(8, 'big')
```

The first 24 bytes are random, and that is a good sign: if they look like noise while the tail
looks like readable text, the key was recovered correctly.

The four chunks are concatenated in instance order. The first gave the prefix `SOLARCTF`, the
other three gave the body and the closing brace. Result: `SOLARCTF{...}`.

## 12. What did not work

- One large lattice of dimension around 131. On 120-thousand-bit entries the reduction runs for
  hours and gave no practical result.
- `delta = 0.75` at dimensions 80 and 95. The reduction is too weak, no root.
- `delta = 0.99` at large dimension. Quality is enough, time is not, hence guessing high bits.
- Shared factor attack. Ruled out by the generator at line 82.
- GPU. There are no mature LLL implementations for big integers on GPU, the computation is
  almost sequential.

## 13. In one paragraph

The generator publishes the 518 high bits of one factor, so `p` has the form `h * 2^506 + x`
with an unknown tail `x` below `2^506`, while the Coppersmith bound for such a divisor is about
`2^512`. A single lattice is too slow, so I guess 5 more high bits and get 32 lattices of
dimension 59 instead of one of dimension 131. The reduction runs on flatter through `flatn`,
the root is recovered by polynomial gcd over small fields plus the Chinese remainder theorem,
and the factor is verified by division. Decryption yields four chunks that assemble into
`SOLARCTF{...}`.

## 14. Lessons

- Estimate the margin before choosing an attack: 6 bits above the bound means the method works
  but needs dimension control.
- Splitting one large lattice into many small ones by guessing a few bits is often better than
  tuning the reduction parameter.
- Limiting threads matters more than core count: one thread per worker gives a larger total win
  than oversubscription.
- Exact division of the modulus plus readable plaintext is a sufficient criterion that a real
  factor was found and not a lattice artifact.
