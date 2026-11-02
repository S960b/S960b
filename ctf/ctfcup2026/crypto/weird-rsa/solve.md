# Weird RSA: RSA over a polynomial ring

[По-русски](solve.ru.md)

## 0. What is different

The handout is a Sage script and one output file. The script encrypts the flag with textbook RSA, but not over the integers: the ring is `GF(257)[x]`, the modulus is a product of two irreducible polynomials, and the message is the flag read as coefficients.

So the attack is the standard RSA decryption, applied to polynomials. The only real question is whether factorization is needed.

## 1. Reading the parameters

`chall.sage` fixes `p = 257` and `e = 65537`, builds `m` from the flag bytes, picks `P` of degree 58 and `Q` of degree 59, sets `N = P * Q`, and writes `N.list()` and `c.list()` to the output.

The output gives `N` with degree 117, which is exactly `58 + 59`, and `c` with degree 116. Nothing else is published, and `P`, `Q` are gone.

Why this step: the degrees are the crucial leak. Without them the modulus would have to be factored over `GF(257)[x]`, which is doable but slower and easy to get wrong by hand.

## 2. The Euler function without factoring

For `R = GF(p)[x]` and an irreducible polynomial `f` of degree `d`, the quotient `R/(f)` is a field with `p^d` elements, so the unit group has order `p^d - 1`. For `N = P * Q` with coprime `P` and `Q`, the Chinese remainder theorem gives

```text
phi(N) = (p^58 - 1) * (p^59 - 1)
```

That is a plain integer. No polynomial factorization is involved, and the script itself computes exactly this value before choosing `e`, which is a strong hint that the intended path is the same.

Why this step: with `phi` known, the private exponent is one modular inverse away.

## 3. Polynomial exponentiation

The private exponent is `d = e^{-1} mod phi(N)`. Decryption is `m = c^d mod N`, and both operations live in `GF(257)[x]`.

Two implementation details decide whether this works:

- reduce every coefficient modulo 257 after each multiplication, otherwise the numbers grow without bound;
- do modular reduction with polynomial division, and do it after every multiplication in the square-and-multiply loop, not once at the end.

`scripts/solve_weird_rsa.py` implements dense polynomial arithmetic over `GF(257)` directly: multiplication by convolution, division by repeated leading-term elimination with a modular inverse, and binary exponentiation on top. No Sage, no SymPy.

Why this step: a generic `pow(base, exp, mod)` call does integer arithmetic and silently produces garbage here, which is exactly the trap that wastes an hour.

## 4. Byte order

Sage's `.list()` returns coefficients starting from the constant term, and `R([GF(p)(b) for b in flag])` also treats the list as constant-term first. A solver that feeds these lists into a library expecting highest-degree-first will decrypt to non-ASCII bytes, which looks like a wrong key but is a wrong orientation.

The check is simple: after decryption the coefficients must be printable ASCII and start with `ctfcup{`. If they do not, reverse the coefficient order and try again.

Why this step: this single orientation issue is the difference between "the method does not work" and a correct flag.

## 5. Verification

The script re-encrypts the recovered polynomial and compares it with `c`. That is a complete check: if `m^e mod N` equals the ciphertext, the decryption is right regardless of how the text looks.

The recovered flag is masked here as `ctfcup{...}`.

```bash
python3 scripts/solve_weird_rsa.py assets/output.txt
```

## 6. Dead ends

- Factoring `N` over `GF(257)[x]`: unnecessary once the degrees are known, and slow to implement correctly.
- Using integer `pow` with a polynomial modulus: silently wrong, as described above.
- Reading the coefficient lists in the wrong order: produces plausible-length but unprintable bytes, which is the most misleading failure mode here.

## 7. Lessons

- RSA is a scheme, not an integer algorithm: the same equations hold in any ring where the unit group order is known.
- Published degrees are as good as a factorization when the modulus is a product of two known-degree irreducibles.
- Coefficient orientation is the classic silent bug in polynomial crypto; always validate against the expected plaintext prefix.

One paragraph: the challenge is textbook RSA in `GF(257)[x]` with a modulus built from irreducible polynomials of degree 58 and 59, so the Euler function is `(257^58 - 1)(257^59 - 1)` and no factorization is needed; computing `d` and raising the ciphertext polynomial to `d` modulo `N`, with coefficients read constant-term first, returns the flag, and re-encryption confirms it.
