# ker quest

[Russian](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier, October 31
- **Points:** 400
- **Challenge page:** https://game.ctfcup.online/tasks/ker_quest
- **Type:** multi-stage misc, crypto, forensics, reversing and OSINT
- **Flag:** `ctfcup{...}` (masked)

The challenge starts with a question about the author's first CTF. Each completed task provides the password or clue needed to open the next archive. Later stages move between corrupted images, a jigsaw puzzle, a MIPS program, recorded audio, an ELF, packet capture, a real-world location, JavaScript and a final substitution cipher.

It is worth documenting each password and the evidence for it. Blind ZIP brute force would hide the reason these different artifacts exist. The full progression is in [solve.md](solve.md).

The research archive from the event was named `ker_quest_compact.zip`; it contains a PNG repair result and experimental jigsaw assembly tools. A later handoff included more of the puzzle material. The remaining final stages are reconstructed from recorded solution notes rather than repackaged as original challenge files.
