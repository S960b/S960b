# Weird RSA

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** crypto
- **Task:** https://game.ctfcup.online/tasks/weird-rsa
- **Attachments:** `chall.sage`, `output.txt`
- **Flag:** `ctfcup{...}` (masked)

Textbook RSA in the ring `GF(257)[x]`: the flag is read as polynomial coefficients, the modulus is a product of irreducibles of degree 58 and 59, and the public exponent is 65537.

The published degrees make factorization unnecessary. The Euler function is `(257^58 - 1)(257^59 - 1)`, so the private exponent is one modular inverse, and decryption is polynomial exponentiation modulo `N`. Coefficients must be read constant-term first, as Sage writes them.

`scripts/solve_weird_rsa.py assets/output.txt` implements `GF(257)` polynomial arithmetic without Sage, decrypts, and re-encrypts to verify.
