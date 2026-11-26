# Cold Echo — recovering a signed polynomial from reciprocal products

[Русская версия](solve.ru.md)

## 1. Given information

The exact organizer archive supplies `README.md` and `archive.json`, which contains:
- prime `p = 17179869143`, polynomial degree `n = 42`, batch `bed64d3525599836`;
- 42 trace entries `[u_j,r_j]`, where `r_j=P(u_j)P(u_j⁻¹) mod p`;
- one direct observation `P(89)=11033196826`;
- `public_key_pem`, `receipt_der_b64` (ECDSA P-256 / SHA-256), and `flag_mask_b64`.

The polynomial is of **exact** degree 42 and normalized so that `P(1)=1`. The target is not merely any matching polynomial; it is the one whose canonical ASCII receipt verifies against the supplied ECDSA signature.

## 2. Linearize the reciprocal product

Let `P(X)=a₀+a₁X+...+a₄₂X⁴²`. Define

```text
L(X)=P(X)P(X⁻¹)=c₀+Σ_{d=1}^{42}c_d(X^d+X^(-d))
S(X)=X^42 L(X)=P(X)·rev(P)(X)
rev(P)(X)=X^42P(X⁻¹).
```

`S` is reciprocal/palindromic of degree 84. All trace readings become **linear** equations:

```text
c₀ + Σ_{d=1}^{42} c_d(u_j^d + u_j^(-d)) = r_j (mod p).
```

There are 42 rows and 43 columns. Modular Gauss–Jordan elimination gives rank **42**, one free column `c₄₂`, a particular solution `c_p` and a nullspace vector `c_n`. Avoid `sympy.Matrix.rank()` over the rationals: in the original attempt it hung for over two minutes on these large field elements. The script `lin.py` uses native Python integers and `pow(x,-1,p)`.

The missing equation is

```text
1=S(1)=P(1)^2=c₀+2Σ_{d=1}^{42}c_d.
```

Writing `c=c_p+λ c_n` and `W(v)=v₀+2Σv_d` yields:

```text
λ = (1-W(c_p)) / W(c_n) mod p
λ = 1922965990.
```

The coefficients of `S` are `S_k=c_|k-42|` for `k=0..84`. It has degree 84 and `S(1)=1`.

## 3. Factor S into inverse pairs

The saved modular polynomial checks establish:

```text
gcd(S,S') = 1
deg gcd(S,X^p-X) = 84.
```

Therefore all **84 roots are distinct and in the prime field**. `roots.py` computes the latter gcd with repeated-squaring modular exponentiation; `cz.py` uses a Cantor–Zassenhaus split (random `X+a`, exponent `(p-1)/2`, then gcd) to obtain all roots. A bug in the first version mistakenly took a remainder instead of the quotient `f/g`, collapsing the factorization; the saved code fixes it.

Because `S(X)=P(X)rev(P)(X)`, roots occur as `(r,r⁻¹)`; none is 1 or -1. `pairs.py` groups the 84 roots into **42 reciprocal pairs**.

## 4. Meet-in-the-middle against P(89)

Any possible `P` is obtained by choosing exactly one root `x_i` from every pair and normalizing it:

```text
P(X) = [∏ᵢ(X-x_i)] / [∏ᵢ(1-x_i)] mod p.
```

At `t=89`, each root contributes

```text
f_t(x)=(t-x)/(1-x) mod p
P(89)=∏ᵢ f_t(x_i)=11033196826 mod p.
```

Direct enumeration is `2^42` possibilities. Split the 42 pairs into two groups of 21; store products for the left side, then seek `f·inverse(right_product)` in the left table. This takes about `2^21` steps per half plus hashing (roughly 300 MB RAM in the recorded run).

The final `mitm.py` reports **243 solutions** and saves their 42-bit masks to `sols.json`.

## 5. Verify the signed receipt

For each mask, `solve.py` builds `P`, checks `deg P=42`, `P(1)=1`, `P(89)=11033196826`, and verifies

```text
P(X)·rev(P)(X) == S(X) mod p.
```

It constructs the exact signed ASCII message:

```text
GLACIER-COLD-ECHO-v1
bed64d3525599836
17179869143 42
<42 original u reading lines, preserving file order>
89 11033196826
<a0 a1 ... a42, decimal, ascending degree>
```

There is an LF after **every** line, including the last; `batch` is copied verbatim and integers are canonical decimal with no leading zeros. The archived ECDSA `receipt_der_b64` is checked under `public_key_pem` using `ecdsa.VerifyingKey.verify(...,hashfunc=sha256,sigdecode=ecdsa.util.sigdecode_der)`.

**Exactly one of the 243 candidates passes ECDSA**. In the saved repeated run, `found: True` is followed by the full signed receipt and recovered result.

## 6. Unmask the final flag

Use the exact signed message bytes:

```python
mask = base64.b64decode(archive['flag_mask_b64'])
pad = hashlib.sha256(b"cold-echo-flag-pad-v1\n" + message.encode('ascii')).digest()
body = bytes(a ^ b for a,b in zip(mask,pad))
flag = "gctf{" + base64.urlsafe_b64encode(body).decode().rstrip("=") + "}"
```

The verified output is `gctf{...}`; the participant confirmed the flag was accepted on the platform. We omit the literal flag and full polynomial coefficients from public GitHub, retaining them in the private evidence ZIP.

## 7. Reproducibility and unsuccessful paths

The uploaded `glacierctf_cold-echo_materials.zip` contains original handout data, eight historical Python scripts and every intermediate JSON result: `lin.json`, `S.json`, `h.json`, `roots.json`, `pairs.json`, `sols.json`, `cold_echo_result.json`. The initial full solver's stdout was not saved as a file, so the agent **reran** the deterministic solver during evidence packaging; the archived `solver_run.log` is that repeated run, not a claimed original capture.

Two main dead ends were `sympy.Matrix.rank()` (slow rational arithmetic rather than modular elimination), and a first Cantor–Zassenhaus split using a polynomial remainder where the quotient was required. A direct `2^42` brute force was infeasible; MITM solved that stage.

This task is **fully offline**: no active instance, cloud API or challenge endpoint is needed. The original organizer README and signed data establish the receipt and flag-unmasking rules. The ECDSA signature proves the final candidate matches the provided public key, not the historical origin of the signature beyond the organizer's handout.
