# Print Print Revolution - custom format string + GOT overwrite

- **Platform:** SunshineCTF 2026
- **Category:** Pwn
- **Date:** 2026
- **Remote:** `chal.<...>.games:26002` (challenge instance, closed after the event)
- **Flag:** `sun{...}` (masked)
- **Binary:** `revolution`
- **Mitigations:** No canary, no PIE, NX on, Partial RELRO

`revolution` is a tiny no-canary / no-PIE ELF. It reads a "score card template" and renders it with a **hand-written printf-like** function. That renderer supports `%N$p` (print an argument), `%N$s` (print a string) and - the interesting one - `%N$w`, which performs an **8-byte arbitrary write**: `*(arg[N]) = arg[N+1]`.

The exploit uses the `%N$w` primitive to overwrite `strcspn@got` with `system`, then submits a normal command line, which main() then runs through `strcspn() == system()`.

Files:

- `solve.md` - the full write-up, including the dead ends
- `scripts/solve.py` - the working remote exploit