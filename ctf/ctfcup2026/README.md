# CTFCUP 2026, Russian CTF Cup X Qualifier

[Русская версия](README.ru.md)

This folder contains six challenge writeups from the team submissions on October 31, 2026. It follows the layout used in [Solar CTF](../Solar/README.md). The main texts are in English. Russian translations are in files ending with `.ru.md`.

The competition used a Jeopardy-style task board on [CTFCUP](https://game.ctfcup.online/). The observed flag syntax was `ctfcup{[A-Za-z0-9_]+}`. This collection covers the October 31 qualifier, not the final event.

## Challenges

| Challenge | Points | Notes |
| --- | ---: | --- |
| [ker quest](misc/ker-quest/README.md) | 400 | A multi-layer archive, forensics, reversing and OSINT journey |
| [Traverse](reverse/traverse/README.md) | 207 | iCE40 FPGA bitstream and Python wrapper |
| [MeowSim: Schrodinger Edition](mobile/meowsim-schrodinger/README.md) | 100 | Android static reversing and state-gated decryption |
| [MMM](reverse/mmm/README.md) | 100 | A matrix hidden in ELF data |
| [Chik Chirik](crypto/chik-chirik/README.md) | 229 | Acoustic symbols, Reed-Solomon and xorshift128 |
| [Rovny robot (flygammon)](blockchain/flygammon/README.md) | 642 | VRF prediction and an offline-tested backgammon strategy |

## How the writeups are organized

Each directory has a short `README.md` describing the challenge and a `solve.md` that explains why each step was taken. Both have Russian companions. Code examples are explained in the text. Reusable helpers are included when their behavior can be reconstructed and verified.

Flags are masked as `ctfcup{...}`, just as the Solar writeups mask theirs. No live instance addresses, wallet private keys or session secrets are published.

These are retrospectives based on the surviving chat logs and artifacts. A team scoreboard submission is evidence of acceptance, but it is not the same as a local replay. Where the final command or original attachment was not preserved, the text says so explicitly instead of inventing it.
