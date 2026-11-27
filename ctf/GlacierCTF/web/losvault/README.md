# LosVault — GlacierCTF 2026

[Русская версия](README.ru.md) · [Detailed technical write-up](solve.md)

**Author:** blissnox · **Category:** web · **Points:** 50  
**Status:** Solved — flag accepted by the platform (participant confirmation).

> Ever wanted a digital vault to store your valuable documents? Now you can, with LosVault! We even have a transparency report and review your files! Can you crack the vault to uncover its secret?

Challenge: https://losvault.web.glacierctf.com

## Attack overview

The vault's review bot communicates with an internal HTTPS recovery service on `127.0.0.1:9446` and exposes a TLS capture to the evidence owner. A leaked note contains the internal service's RSA key generation code: it selects `q` near `p + 2^240`, instead of choosing independent RSA primes.

The actual **1024-bit internal RSA certificate** can be factored using Fermat's method in **one iteration**. Because the recorded TLS session negotiated `TLS_RSA_WITH_AES_128_GCM_SHA256` (RSA key exchange, no forward secrecy), the private key can decrypt the captured recovery request and response. The recovered `proof` is the vault's integrity code; submitting it to `POST /vault/evidence/<id>/verify` returns the flag.

See [solve.md](solve.md) or [solve.ru.md](solve.ru.md) for the complete chain, checked observations, archived commands and historical limitations.

**Source provenance:** The uploaded `glacierctf_losvault_materials.zip` contains the original previous-session research note, leaked RSA source, internal certificate, three PCAPs, API evidence JSON, saved proof and several scripts. The RSA factoring step was independently verified again during packaging. The original TLS plaintext and final verification HTTP response were documented in that previous session, **not independently rerun** during packaging.

The accepted flag is redacted as `gctf{...}` in this public repository; the user's evidence archive retains the original.
