# Decipher Stage 2 — technical write-up

[Русская версия](solve.ru.md)

**GlacierCTF 2026 · Decipher Stage 2 · crypto · 50 pts**

**Status: Solved — Flag accepted by platform (participant confirmation).** The accepted flag is redacted in public text as `gctf{...}`.

## The attack

Stage 2 retains the weak DeCipher design but its `output_stage2.npz` contains pairwise proof arrays exposing inter-round state differences. The S-box is **exactly** an affine mapping plus one quadratic Boolean term:

```text
SBOX[x] = SBOX[0] XOR L(x) XOR
          (0xe7 if (((x>>1)^(x>>5))&1) & ((x>>6)&1) else 0)
```

In symbols let `a(x)=bit1(x)^bit5(x)` and `b(x)=bit6(x)`; the nonlinear term is `0xe7*a(x)*b(x)`. Differences satisfy `a(x⊕d)=a(x)⊕a(d)` and `b(x⊕d)=b(x)⊕b(d)`. For an unknown reference input, its nonlinear difference against known `d` is linear in the two reference bits `A=a(x)` and `B=b(x)`:

```text
a(x⊕d)b(x⊕d) XOR a(x)b(x)
 = A*b(d) XOR B*a(d) XOR a(d)b(d).
```

The original script inverts ShiftRows/MixColumns on the proof differences and solves for **two bits per state byte in each of 10 rounds**: **320 leaked class bits**. It then introduces **128 base-key bits and 40 unknown key-schedule S-box nonlinear bits**, symbolically computes each round and forms **448 equations in 168 unknowns** over GF(2).

A pivoted bitset Gaussian elimination recovers the key `2f3762f3f277d89957613a661802dd5c`. The script explicitly verifies all key-schedule nonlinear bits, **every ciphertext and every proof**, then derives the AES-GCM key as `sha256(key_schedule(base_key).tobytes())` and decrypts the encrypted flag using a zero nonce.

**Reproduction:** from the organizer's `decipher-1` directory containing `decipher.py` and `output_stage2.npz`, run the preserved [solve_stage2.py](scripts/solve_stage2.py). The script expects the directory named `decipher-1` alongside itself; relocate/adjust that path if necessary. Dependencies: `numpy`, `cryptography`. The recovered flag was accepted by the platform; its full value is masked in this public documentation.

