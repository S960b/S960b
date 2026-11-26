# LosVault — GlacierCTF 2026

[Русская версия](README.ru.md) · [Technical notes](solve.md)

**Category:** web · **Points:** 50 · **Status:** solved/flag accepted on the participant's GlacierCTF scoreboard (as stated by the participant).

**Scope of recovered evidence:** Only the *weak internal RSA certificate* and its Fermat-factorization step can currently be reconstructed from the earlier dialogue and original Python script. The authenticated administrator access, final flag retrieval and exact accepted flag were not recoverable from those materials. This is a truthful **partial historical write-up**, not an invented full chain.

[Original Fermat solver](scripts/losvault_fermat.py) · [Technical notes](solve.md)

The challenge's internal TLS used `TLS_RSA_WITH_AES_128_GCM_SHA256` with a deliberately weak 1024-bit RSA key. Its two primes were generated unusually close together; the integer `q` was approximately `p+2^240`, resulting in essentially immediate Fermat factorization of `N=pq`.

**Important:** The ordinary public HTTPS reverse-proxy certificate was Let's Encrypt, 2048-bit, and **not** the target. One must obtain the internal certificate rather than factoring the visible front-door certificate.
