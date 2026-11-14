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

These two mathematical conditions are explicit in the bundled `server.py`. The archive records the invocation of `solve_unholytrinity.py` on the instance, but does not contain its contents or terminal output. A separate solved-task report confirms both flags.

Result: **2 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

The particular values of `x`, `r`, and transcript of all rounds were not preserved; the protocol steps here are reconstructed from the source and the agent's result.

## 6. Fix and lesson

Hash passwords with a password KDF (Argon2id, scrypt, PBKDF2), not raw RSA exponentiation. Avoid home-grown identification schemes; use a reviewed standard and a challenge proof that does not expose low-exponent short secrets.
