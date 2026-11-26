# El Clásico — GlacierCTF 2026

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Author:** Onirique
- **Points:** 50
- **Challenge:** *Can you prove the impossible?*
- **Category:** crypto / zkSNARK soundness (technical classification; original category was not captured)
- **Historical endpoint:** `https://el-clasico.web.glacierctf.com`
- **Attachment:** `el-clasico.tar.gz`
- **Status:** solved; saved prover output includes local Groth16 verification **and HTTP 200 with the real flag**.

## Summary

The challenge uses gnark v0.16.4 and a Groth16 proof over BN254 to assert a peculiar equation about `Q = [s]P` on BLS12-381 G1. A valid group element cannot satisfy the intended condition, but a prover can exploit an **unsound classic GLV scalar-multiplication gadget**. The supplied witness overrides the scalar-decomposition hint and uses an exceptional `0/0` chord-slope hint to force an off-curve output that satisfies the asserted equation.

The successful exploit builds a lattice-controlled collision in the last merged GLV window, solves a degree-12 polynomial for the free slope, constructs a proof with the organizer's genuine `pk.hex`, verifies it with the original `vk.hex`, and submits it.

The actual flag is redacted as `gctf{...}` in public files, as in other write-ups in this repository.

## Contents

- [solve.md](solve.md): full derivation, failed hypothesis, corrected simulator and proof forgery.
- [solve.ru.md](solve.ru.md): Russian technical write-up.
- [scripts/verify_parameters.py](scripts/verify_parameters.py): offline checks for the selected GLV decomposition, collision vector and off-curve challenge equation.
- [scripts/README.md](scripts/README.md): inventory of the archived **working Go prover** and Python lattice solver, with exact file names and reproduction notes.
- [assets/README.md](assets/README.md): organizer handout and dependencies.
- [evidence/README.md](evidence/README.md): original verification log, HTTP submission and provenance.

No remote request, prover execution, or key generation was performed while preparing this write-up.
