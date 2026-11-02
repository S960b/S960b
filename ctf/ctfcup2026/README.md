# CTFCUP 2026, Russian CTF Cup X Qualifier

[Русская версия](README.ru.md)

Writeups for challenges from the CTFCUP 2026 qualifier on October 31, 2026. Main texts are in English; Russian translations end with `.ru.md`.

The competition used a Jeopardy-style board on [CTFCUP](https://game.ctfcup.online/). Flags are wrapped in `ctfcup{...}`; the inner alphabet is not fixed, most tasks use lowercase hex, but at least one uses punctuation as well. This collection covers the October 31 qualifier only.

## Challenges

| Challenge | Points | Category | Notes |
| --- | ---: | --- | --- |
| [ker quest](misc/ker-quest/README.md) | 400 | misc | nine layers: broken PNG header, jigsaw, MIPS, NATO audio, pcap, OSINT, JavaScript, ROT13 |
| [Rovny robot (flygammon)](blockchain/flygammon/README.md) | 642 | crypto / blockchain | smooth-order VRF, predicted dice, `skip()` keeps the streak |
| [Traverse](reverse/traverse/README.md) | 207 | reverse | iCE40 bitstream, eight-bit state machine, BFS over 139 states |
| [Chik Chirik](crypto/chik-chirik/README.md) | 229 | crypto / audio | chirps, RS(7,3) over GF(8), linear xorshift128 |
| [MMM](reverse/mmm/README.md) | 100 | reverse / math | 40 by 40 matrix, eigenvector for 1337 |
| [MeowSim: Schrodinger Edition](mobile/meowsim-schrodinger/README.md) | 100 | mobile | state gate plus SHA-256 key and XOR |

## How the writeups are organized

Each directory has a short `README.md` describing the challenge and a `solve.md` explaining why each step was taken. Both have Russian companions. `scripts/` holds the solvers and helpers that were actually used, `assets/` and `evidence/` are used only where the material can be redistributed.

Flags are masked as `ctfcup{...}`. Live instance addresses, wallet private keys and session secrets are not published.

Every task in this collection was solved and accepted during the event. Where a step can be replayed from a surviving artifact, the text says how; where an artifact no longer exists (a live instance, a wallet key, a deleted working directory), the text says so instead of inventing it.
