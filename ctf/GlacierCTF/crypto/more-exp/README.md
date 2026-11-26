# More EXP (crypto)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- Author: LosFuzzys
- Points: 50 in the supplied final task description; 103 was reported at solving time (dynamic scoring).
- Tags: Beginnerfriendly, RSA, Math
- Original attachment: `more_power.tar.gz`

## Task

> FuzzyGmbH built a Encryption Service.
> It has not been audited yet, because currently decryption is not supported.
>
> However, one of the interns already raised some concerns.
> But he is just the intern, that does he know

The service accepts a chosen public exponent and an optional plaintext in JSON. If plaintext is omitted it encrypts the flag. The RSA modulus is hidden and freshly generated per connection.

Goal: recover the flag without factoring the modulus. The solution first recovers the modulus using GCDs of known-plaintext differences, then obtains the flag using consecutive exponents 65537 and 65538. The original first attempt encountered a spurious factor in the GCD; removing it fixed the solver.

## Files

- [solve.md](solve.md): detailed derivation, failure analysis, and reproduction.
- [scripts/glacier_more_exp.py](scripts/glacier_more_exp.py): successful competition solver, with only the destination changed to localhost.
- [assets/more_power/](assets/more_power/): original unpacked challenge files (including the organizer's dummy flag).
- [evidence/competition-redacted.log](evidence/competition-redacted.log): successful competition run, actual flag masked.
- [evidence/local-redacted.log](evidence/local-redacted.log): independent local reproduction with the supplied dummy flag.

The server flag was recovered and both RSA checks passed. The real flag is not published.
