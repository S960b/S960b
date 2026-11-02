# ker quest

[Russian](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier, October 31
- **Points:** 400
- **Challenge page:** https://game.ctfcup.online/tasks/ker_quest
- **Type:** multi-stage misc, crypto, forensics, reversing and OSINT
- **Flag:** `ctfcup{...}` (masked)

Nine layers, each one giving the password for the next archive: a question about the author's first CTF, a PNG with a wrong IHDR height, a sixty-fragment jigsaw, a MIPS crackme, a spoken NATO password, Base64 hidden in an image, one packet among ten thousand, a hotel photo from Akihabara, an obfuscated JavaScript constant, and finally ROT13.

Every archive is WinZip AES with an HMAC, so each password in the chain is confirmed cryptographically before the next layer is attempted. That property is what makes the writeup reproducible: the passwords `2015`, `myfirstsolvedforensicstask2016`, `takearesttraveller`, `easyreverseforeveryone`, `th3r34l0n3p4ssw0rd1337`, `ohnicetoseeyouin2036year`, `glinomesieforever`, `superhotelpremierakihabara` and `junkjunkjunkjunkcode` were re-tested against the original archives. Packet 1337 of the capture needs its own password `1337` before it reveals the next one.

The progression is in [solve.md](solve.md); `scripts/restore_ihdr.py` reproduces the PNG repair and checks the header CRC.
