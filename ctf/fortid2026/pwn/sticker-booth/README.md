# Sticker Booth - heap UAF via refcount confusion, then file substitution

- **Platform:** FortiD CTF 2026
- **Category:** Pwn
- **Author:** ipaljak
- **Points / Solves:** 113 pts, 196 solves
- **Date:** 2026-10-09
- **Remote:** `sticker-booth-<...>.inst18.ctf.fortid.com:1337` (TLS/ncat, closed after the event)
- **Flag:** `FortID{...}` (masked)
- **Binary:** `chall` (x86-64, statically linked, no PIE, not stripped) + `recipe_docs.md` manual, `assets/`
- **Description (official):** "A place where you can safely customize your stickers and print them out!"

A sticker-sheet designer reading commands from stdin. Two bugs combine: the `mono` filter draws H+1 rows (a zero-only off-by-one into the next pool block), and `caption` deduplicates identical texts so two items share one buffer with a refcount. Zeroing the refcount frees the shared buffer while a stale item still points at it (UAF); re-allocating that block with a controlled upload and arranging a builtin right after leaks the random booth_ticket via a controlled fwrite. A second UAF then substitutes a forged builtin header (type 2, valid ticket, path "flag.txt"), and the dangling item hits the file-open branch of `print` - the flag is dumped.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/proof_sb.py` - local two-phase proof (leak ticket, substitute path)
- `scripts/solve.py` - the same chain over TLS for the remote instance