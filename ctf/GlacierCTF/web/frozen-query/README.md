# Frozen Query — GlacierCTF 2026

[Русская версия](README.ru.md) · [Technical details](solve.md)

**Category:** web · **Points:** 50 · **Status:** solved, flag accepted on the platform (participant's solved list).

**Historical challenge:** Glacier Avalanche Service, a restricted summit bulletin behind the HTTP QUERY method and an edge cache.

The recovered technique is a **double gzip WAF bypass combined with a cache-key authorization flaw**. The five-second auditor process continually primes a 30-second response cache with a private summit bulletin. The cache key omits authorization and hashes only the path, normalized Content-Type, Accept and canonical JSON body. A non-auditor can supply the same body under `Content-Encoding: gzip, gzip`: the WAF decompresses just once (so it never sees `flag`), whereas the request handler decompresses twice and hits the auditor's cache entry.

The previous personal dialogue did not retain the original accepted response or exploit. This write-up **credits an independently published November 2026 solution** as the source of the reconstructed technical chain, without falsely claiming it is the participant's original trace: [D23R's Frozen Query walkthrough](https://dua2z3rr.github.io/posts/Frozen-Query-Walkthrough/). The accepted flag text is not available in the recovered historical material and is not invented here.
