# CTFCUP 2026, Russian CTF Cup X Qualifier

[Русская версия](README.ru.md)

Thirteen challenge writeups from the team submissions on October 31, 2026, in the same layout as [Solar CTF](../Solar/README.md). Main texts are English; Russian translations end with `.ru.md`.

The competition used a Jeopardy-style board on [CTFCUP](https://game.ctfcup.online/). Flags are wrapped in `ctfcup{...}`; the inner alphabet is not fixed, most tasks use lowercase hex, but at least one uses punctuation as well. Some deployments issue the hex suffix per instance, so the value has to be read from the instance being solved. This collection covers the October 31 qualifier only.

## Challenges

| Challenge | Points | Category | Notes |
| --- | ---: | --- | --- |
| [Rovny robot (flygammon)](blockchain/flygammon/README.md) | 642 | crypto / blockchain | smooth-order VRF, predicted dice, `skip()` keeps the streak |
| [filter](pwn/filter/README.md) | 100 | pwn | neural bigram check in front of writable executable memory |
| [compuuuter](web/compuuuter/README.md) | 100 | web | time-of-check race, hash collisions as a delay |
| [ker quest](misc/ker-quest/README.md) | 400 | misc | nine layers: broken PNG header, jigsaw, MIPS, NATO audio, pcap, OSINT, JavaScript, ROT13 |
| [Russian Post: Trust Level](mobile/russian-post/README.md) | 337 | mobile | decoy request builder, chained XOR container |
| [Chik Chirik](crypto/chik-chirik/README.md) | 229 | crypto / audio | chirps, RS(7,3) over GF(8), linear xorshift128 |
| [Traverse](reverse/traverse/README.md) | 207 | reverse | iCE40 bitstream, eight-bit state machine, BFS over 139 states |
| [Codex (Святой Кодекс)](web/codex/README.md) | 115 | web | bot plants the flag in a scope named by a hardcoded default |
| [MMM](reverse/mmm/README.md) | 100 | reverse / math | 40 by 40 matrix, eigenvector for 1337 |
| [MeowSim: Schrodinger Edition](mobile/meowsim-schrodinger/README.md) | 100 | mobile | state gate plus SHA-256 key and XOR |
| [Snake](misc/snake/README.md) | 100 | misc | 61-cell hex board, Hamiltonian cycle |
| [Weird RSA](crypto/weird-rsa/README.md) | 100 | crypto | textbook RSA in `GF(257)[x]`, no factorization needed |
| [My Own FS](misc/my-own-fs/README.md) | 100 | misc | toy filesystem, deleted inode still holds the file |

## How the writeups are organized

Each directory has a short `README.md` describing the challenge and a `solve.md` explaining why each step was taken. Both have Russian companions. `scripts/` holds the solvers and helpers that were actually used, `assets/` and `evidence/` only where the material can be redistributed.

Flags are masked as `ctfcup{...}`, the same rule as in the Solar writeups. Live instance addresses, wallet private keys and session secrets are not published.

Every task in this collection was solved and accepted during the event. Where a step can be replayed from a surviving artifact, the text says how; where an artifact no longer exists (a live instance, a wallet key, a deleted working directory), the text says so instead of inventing it.
