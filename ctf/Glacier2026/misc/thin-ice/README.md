# thin-ice (Git protocol oracle)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Author:** ansa
- **Points:** 50 (task card supplied for this write-up; an older agent report listed 136)
- **Tags:** git, protocol, oracle
- **Service:** `challs.glacierctf.com:13372` (historical)
- **Handout:** `thin_ice.tar.gz`
- **Status:** solved; the participant confirmed platform acceptance.

> The ice is thin - pack light.

## Overview

The challenge serves a bare Git repository with the flag stored in `flag.txt`. The server explicitly disables fetch operations (`upload-pack` and `upload-archive`), permits `receive-pack`, and installs an `update` hook that rejects all ref updates. This initially appears to prevent reading the repository.

A crafted **thin Git pack** turns `git-receive-pack` into an information oracle. When Git resolves `REF_DELTA` bases, its diagnostic count of unresolved deltas reveals one byte of a secret object at a time. A successful solver followed the chain **commit → tree → blob → flag** within one 600-second instance.

The recovered 27-byte flag was accepted by the platform. In the public repository it is masked as `gctf{...}` in accordance with the existing write-up policy.

## Files

- [solve.md](solve.md): complete Git protocol attack and the counted-delta oracle.
- [solve.ru.md](solve.ru.md): Russian technical write-up.
- [scripts/verify_git_chain.py](scripts/verify_git_chain.py): offline proof that the recovered flag reproduces the advertised Git object IDs (the flag is passed privately at runtime).
- [scripts/README.md](scripts/README.md): provenance and inventory of the archived working solver and transport modules.
- [assets/README.md](assets/README.md): original handout and relevant configuration.
- [evidence/README.md](evidence/README.md): preserved remote log, checkpoint and validation limits.

No live Git service was contacted for the write-up.
