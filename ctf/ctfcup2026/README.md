# CTFCUP 2026 (X Кубок CTF России, qualifier)

- **Dates:** October 31 - November 2, 2026
- **Format:** Jeopardy, online, qualifier stage
- **Platform:** https://game.ctfcup.online/
- **Flag format:** `ctfcup{...}`; the inner alphabet is not fixed, and some deployments issue the suffix per instance
- **Result:** 15 challenges solved and accepted

Structure: `<category>/<challenge>/` with `README.md` (task context), `solve.md` (solving
path), `scripts/` (solver and tooling), `assets/` (files as shipped), `evidence/` (raw run
logs, masked).

Rules for this folder:

- `README.md` and `solve.md` are English and are the versions published to the remote.
  `README.ru.md` and `solve.ru.md` are Russian copies for local reading.
- Flags are masked everywhere as `ctfcup{...}`. The full flag string never appears in
  the repository, including scripts and assets.
- Instance hosts are masked as `<host>`.
- Heavy artifacts are not copied; a path reference and the generating command are given
  instead.

Writeups were prepared after the competition ended.
