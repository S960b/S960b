# VoteMe (crypto)

- **Platform:** HITCON CTF 2026
- **Category:** Crypto
- **Points / Solves:** 146 pts
- **Date:** 2026-10-24
- **Author:** whale120
- **Remote:** N/A (offline bundle with a PCAP)
- **Flag:** `hitcon{cuz_@_th..._..._...}` (masked)
- **Files:** `assets/voteme-27f2656eaea4fcf2.tar.gz` (original handout)
- **Description (official):** "Vote for me, myself and I."

A chain of six distinct crypto weaknesses in one voting service. The handout
contains a Ruby service (`record.rb` writes a PCAP), a circom circuit with a PLONK
proving key, and a traffic capture. The attack:

1. 2-adic ECDH: `Curve::ORDER` is divisible by 2^16, so each ServerKeyExchange
   leaks the low 16 bits of the private scalar.
2. Ruby `Random.new` MT19937: all ECDH scalars come from one generator, the low
   bits form a linear system over GF(2), and the last TLS scalar is predicted.
3. TLS AES-256-GCM session is decrypted with the recovered premaster; the
   plaintext is the `/votes` JSON with 1000 PLONK ballots.
4. Each ballot leaks V8 `Math.random()` outputs via the `@lukeed/uuid` v2
   vulnerability; linear algebra recovers the xorshift128+ state with few free
   bits, and the next random output is predicted.
5. PLONK blinding scalars come from `Math.random()`; recomputing the commitment
   `A` for vote in {0,1,2} identifies each ballot's vote.
6. The first 420 votes (70 flag bytes) are decoded in base 3 and read as ASCII.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/*` - the actual solvers (Python, Ruby, C++)
- `scripts/quick_verify.py` - offline check of the saved evidence, decoding to the flag
- `scripts/run_reproduce.sh` - full offline reproduction from the PCAP
- `evidence/observed_lowbits.txt` - 1741 low-16-bit ECDH scalars
- `evidence/mt_solution.txt` - recovered MT19937 state
- `evidence/votes.json` - authenticated-decrypted /votes response
- `evidence/recovered_votes.txt` - the 426 proven vote values
- `evidence/flag.txt` - the masked 70-byte flag