# Platypwn 2026

[Русская версия](README.ru.md)

- **Dates:** November 13–14, 2026
- **Format:** online task-based CTF
- **Platform:** https://platypwn.ctf.platypwnies.de/
- **Team:** SOC²
- **Final rank:** 49th (team's final result). An earlier API snapshot reported rank 54, 3,957 points and 24 flags; it is not the final leaderboard position.
- **Flag format:** `PP{...}`

## Writeups

The directory contains **18 technical reports** written from the original challenge materials, stored evidence, recovered solver scripts and preserved lab notes. In Old Trusty both flags were recovered; the preserved API snapshot does not list the challenge as completed, so no platform-submission claim is made here. Flacksafety was a partial solve: five of six backup tiers destroyed and the governance flag obtained. These statuses are distinguished in their individual writeups.

Each task uses the same structure as the other CTF reports in this portfolio:

- `README.md` and `solve.md`: English overview and step-by-step writeup
- `README.ru.md` and `solve.ru.md`: Russian counterparts
- `scripts/`: verified or reconstructed reproductions, labelled accordingly
- `assets/` and `evidence/`: available source code, challenge materials and redacted logs

Complete flags, passwords, NT hashes and live instance addresses are not published. Missing source files or execution traces are clearly identified. No stage is presented as succeeded unless the available evidence supports it.

## Challenges

| Category | Challenge | Notes |
|---|---|---|
| Reverse | [Matryoshka](rev/matryoshka/solve.md) | CRC32 linear algebra and XOR |
| Reverse | [FSenc](rev/fsenc/solve.md) | ChaCha20-Poly1305 / Argon2id experiments |
| Crypto | [Unholy Trinity](crypto/unholytrinity/solve.md) | RSA modular-hash collision and integer root |
| Crypto | [Ron's Revenge](crypto/ronsrevenge/solve.md) | Chosen-IV RC4-2048 recovery |
| Crypto | [DEStruction](crypto/destruction/solve.md) | GHASH/short-tag forgery, two flags |
| Web | [Horse Racing](web/horseracing/solve.md) | Delayed HTTP body / TOCTOU |
| Web | [Fishies](web/fishies/solve.md) | XSS, prototype pollution, Puppeteer and LD_PRELOAD |
| Blockchain | [Just in Time](blockchain/justintime/solve.md) | Public deployment entropy |
| Blockchain | [PlatyLocker](blockchain/platylocker/solve.md) | Public storage of a private RSA exponent |
| Blockchain | [Not My Type](blockchain/notmytype/solve.md) | Floating-point validation mismatch |
| Blockchain | [Referral](blockchain/referral/solve.md) | Proxy storage-slot collision |
| Blockchain | [Card Counting](blockchain/cardcounting/solve.md) | Nonuniform deck / PRNG |
| Blockchain | [PlatyPals](blockchain/platypals/solve.md) | Callback reverts and rerolls |
| Active Directory | [Legacy IT](ad/legacyit/solve.md) | Final SMB pass-the-hash read |
| Active Directory | [Old Trusty](ad/oldtrusty/solve.md) | LEGACY → CORE, two recovered flags |
| Fullpwn | [Carbon](fullpwn/carbon/solve.md) | Portal investigation; user and root flags |
| Fullpwn | [Flacksafety](fullpwn/flacksafety/solve.md) | Five of six backup tiers, partial |
| Misc | [Bish Bash Bosh](misc/bishbashbosh/solve.md) | Bash arithmetic evaluation |

These reports were written after the competition.
