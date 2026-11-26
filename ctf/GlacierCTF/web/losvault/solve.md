# LosVault — recovering the internal RSA private key by Fermat factorization

[Русская версия](solve.ru.md)

## Verified historical research

The previous November 20, 2026 conversation inspected LosVault's authenticated web features and determined that its inner service uses `TLS_RSA_WITH_AES_128_GCM_SHA256`. Sensitive routes include `/api/admin/capture-tls`, `/api/admin/transparency/export` and `/admin/review/<id>`; access requires the `vault-audit` administrator role. Direct export requests returned HTTP 403. The application had a review bot with a slowly processed FIFO queue; attempted XSS and directory traversal did not provide a successful bypass.

A crucial distinction was established: the **public** edge HTTPS certificate is a normal Let's Encrypt **2048-bit** key, unrelated to the attack. The relevant **internal** TLS certificate is RSA-1024.

The internal key-generation rule uses a prime candidate approximately `q ≈ p + 2^240`, rather than choosing two independently random half-size primes. As a result `p` and `q` are unusually close relative to their 512-bit sizes.

## Factorization

Write `N=pq` and

```text
a = ceil(sqrt(N))
b² = a²-N.
```

When `b²` is a perfect square, `p=a-b` and `q=a+b`. The saved [losvault_fermat.py](scripts/losvault_fermat.py) computes this directly, validates `p*q=N`, then constructs the RSA private exponent `d=e^(-1) mod lcm(p-1,q-1)`. It writes a PEM private key for use in analysis of the inner TLS session. It explicitly refuses keys that are **not 1024 bits**, protecting against confusing the reverse-proxy certificate with the target.

Invocation for the *internal* service's X.509 certificate:

```sh
pip install cryptography
python3 scripts/losvault_fermat.py internal_tls_certificate.pem --out internal.key
```

Or with an extracted modulus:

```sh
python3 scripts/losvault_fermat.py --modulus 0x... --out internal.key
```

## What is not in the available records

The previous discussion stopped at preparation and local validation of the RSA factorization code; the **internal certificate bytes**, captured session, administrator-role bypass, and successful final response are not present in the retrievable conversation or Library. The participant's later solve list establishes that the challenge was accepted, but it cannot tell us which final attack sequence or recovered flag was used.

Accordingly, this page intentionally documents the authentic **first stage** and retains the original working script; it makes **no claim** that Fermat factorization alone yielded the competition flag or completed the web exploitation. A complete historical write-up requires the omitted artifacts.
