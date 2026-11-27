# brainrot — GlacierCTF 2026 (pwn)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Author:** hweissi
- **Points:** 50
- **Tags:** pwn, sweb, brainfuck, qemu
- **Challenge:** “I wrote a brainfuck interpreter! I don't know how to sandbox it properly, so i just put it into our educational OS.”
- **Service (historical):** `challs.glacierctf.com:13370`
- **Handout:** `brainrot.tar.gz`
- **Result:** recovered from the remote SWEB debug-console transcript; platform acceptance confirmed by the participant. The full flag is masked in this public repository.

## Summary

The interpreter buffers Brainfuck output in `outbuf[1024]`, then calls `printf(outbuf)` without a fixed format. A user-controlled Brainfuck program echoes a carefully constructed fake-stdin string to that buffer. Format-field widths and embedded bytes cause a stack-based control-flow hijack, followed by machine code that performs SWEB `open("/usr/flag")`, `read`, and `write` system calls.

The remote transcript records the flag emitted by the guest, followed later by a kernel panic during teardown. The panic does not negate the already observed flag output.

## Files

- [solve.md](solve.md): technical explanation and evidence.
- [solve.ru.md](solve.ru.md): Russian write-up.
- [scripts/brainrot_solve_reconstructed.py](scripts/brainrot_solve_reconstructed.py): reconstructed payload builder, adapted for offline usage.
- [assets/README.md](assets/README.md): original handout file manifest and SHA-256 hashes.
- [evidence/README.md](evidence/README.md): SHA-256 identities and limits of the retained evidence.

The original solver was lost; the solver in the evidence archive was reconstructed from the historical transcript. No exploitation was repeated while preparing this write-up.
