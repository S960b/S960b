# Sensory Box (misc) — GlacierCTF 2026

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Author:** huksys
- **Points:** 50
- **Remote:** `nc challs.glacierctf.com 13377` (historical)
- **Attachment:** `sensory_box.tar.gz`
- **Status:** complete flag recovered from live service; the participant confirms the flag was accepted by the platform.

> Can you guess all the dice correctly to get the full prize?

Six encrypted flag slices are hidden in “sensory boxes”. Every round gives the player a three-second blind sensing period. The unexpected feature is a hidden `/bin/sh` process during that period. Its stdout/stderr are discarded, but its stdin is attacker-controlled and it inherits `FLAG`.

The exploit exfiltrates the environment variable by encoding it into the **modification timestamps** of 12 box files. The integrity check hashes only file contents; the final downloadable `tar.xz` preserves timestamps. No guesses or age private keys are needed.

## Files

- [solve.md](solve.md) — technical walkthrough, shell access, integrity-check bypass and tar metadata channel.
- [solve.ru.md](solve.ru.md) — Russian write-up.
- [scripts/solve.py](scripts/solve.py) — compact solver implementing the same technique; needs `pwntools` and a kCTF PoW helper (details in [scripts/README.md](scripts/README.md)).
- [assets/README.md](assets/README.md) — original handout inventory and checksum.
- [evidence/README.md](evidence/README.md) — preserved proof and its limitations.

The live flag is masked in public files as `gctf{...}`; the real value remains in the participant's private evidence archive.
