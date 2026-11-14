# Unholy Trinity: solving notes

[По-русски](solve.ru.md)

## 1. Task and source material

Two independent weaknesses in a custom RSA-based login service: a modular password hash and a short-secret identification protocol. Source files: `server.py`, Dockerfile, compose; saved solver invocation (solver file itself absent).

## 2. Analysis

The server sets `e=17`, generates a ~2674-bit RSA modulus `N`, and stores `hash_pw(pw) = int.from_bytes(pw)^17 mod N`. Registration checks for duplicate hash values; therefore distinct valid fixed-length passwords whose integers are congruent modulo N collide. For the admin account the 19-byte secret `m` is far shorter than N: `m^17 < N`. The server prints the admin's committed `pw_hash` before authentication, so its exact integer 17th root reveals `m`, without factoring N.

## 3. Solving procedure

1. Read the public `N` and `e` from the welcome line.
2. Construct two distinct `NLEN`-byte passwords represented by integers `x+N` and `x+2N` (or another pair differing by N), taking care that both satisfy the unusual password-policy test on Python's `str(bytes)`.
3. Register the first user and then the second; the duplicate modular hash path releases the first flag.
4. Start admin login and read `pw_hash = m^17 mod N`. Because there is no modular wrap, compute the exact integer 17th root to recover the 19-byte admin secret.
5. Across the authentication rounds, use a random nonzero `r`: send `announcement = r^17 mod N`, read challenge `c`, and send `response = r * m^c mod N`. This satisfies `response^17 = announcement * pw_hash^c mod N`.
6. Finish all rounds for the second flag.

## 4. Evidence and result

These two mathematical conditions are explicit in the bundled `server.py`. The original solver is now recovered under scripts/; the transferred archive itself contained only the invocation, not the full terminal output. A separate solved-task report confirms both flags.

Result: **2 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

The particular values of `x`, `r`, and transcript of all rounds were not preserved; the protocol steps here are reconstructed from the source and the agent's result.

## 6. Fix and lesson

Hash passwords with a password KDF (Argon2id, scrypt, PBKDF2), not raw RSA exponentiation. Avoid home-grown identification schemes; use a reviewed standard and a challenge proof that does not expose low-exponent short secrets.


## Recovered original working solver

Recovered the original two-flag solver. It generates two fixed-length modular-hash-colliding passwords, extracts the exact 17th root of the committed admin hash, and completes the 52 authentication rounds.

```bash
python3 scripts/solve_unholytrinity.py <host> <port>
```

Code: [scripts/solve_unholytrinity.py](scripts/solve_unholytrinity.py). The file contains the algorithm and checks, not a hardcoded live service address.


## 7. Exact solver mathematics

There are two independent vulnerabilities in the actual `server.py`.

**Registration.** The public modulus `N=pq` is approximately 2674 bits and `e=17`. The service accepts passwords of exactly `NLEN=ceil(bit_length(N)/8)` bytes, checks raw-byte uniqueness and hashes a password as `h(x)=x^17 mod N`. For a suitable integer `x`:

```text
a = N + x
b = 2N + x
a != b
a mod N = b mod N = x
a^17 mod N = b^17 mod N
```

Thus distinct byte strings have equal RSA hashes. The recovered `scripts/solve_unholytrinity.py` initializes `x` with `b"Aa1+"` and increments it until both numbers fit into `NLEN` bytes and pass the server's policy. A subtlety: the policy searches for character classes in `str(password)`, a Python representation of the byte string, not in the raw bytes. The solver exactly reproduces that faulty check.

The registration code prints `FLAG0` when the hash collides with an earlier registration. It does not return afterward. The script uses `player0` and `player1` and does not try to register a forbidden `admin` account.

**Administrator recovery.** The admin secret is 19 bytes, at most 152 bits. Raising it to the 17th power produces at most 2584 bits, smaller than the 2674-bit modulus. Therefore `m^17 < N` and the published hash is the ordinary, unreduced integer power:

```text
admin_hash = m^17
m = exact_integer_17th_root(admin_hash)
```

The script computes the exact root using binary search and validates it by exponentiation. This is **not RSA factorization**.

**52 identification rounds.** The source computes `ROUNDS = 256 // e.bit_length() + 1 = 52` and asks for:

```text
response^17 == announcement * admin_hash^challenge  (mod N)
challenge ∈ {0,...,16}
```

The original successful solver uses `r=1`, sends `announcement=1`, and replies `response=m^challenge mod N`. A general randomized proof could use `r^17` and `r*m^challenge`, but the original code really did use 1. The documented script is therefore faithful to the execution path, not just algebraically plausible.

## 8. Reproduction and sanity checks

```bash
python3 scripts/solve_unholytrinity.py <host> <port>
```

The script prints the modulus size, both registration outcomes and `FLAG0`; it checks `pow(m,e,N)==admin_hash` before the admin rounds and extracts `FLAG1` only after the final response. A standalone modular-collision test can validate the first flaw without network access.

## 9. Why the design failed

1. `pow(password,17,N)` is not a password hash; it identifies residue classes modulo N.
2. Fixed-length input does not make the map injective.
3. Publishing `pw_hash` of a short secret with a small public exponent reveals the secret by exact root extraction.
4. More zero-knowledge-style protocol rounds cannot restore secrecy once the witness itself has been exposed.
