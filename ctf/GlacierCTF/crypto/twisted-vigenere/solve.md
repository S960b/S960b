# Twisted Vigenere: an MT19937 state-boundary attack against repeated-key XOR

[Русская версия](solve.ru.md)

**GlacierCTF 2026 · Crypto · 50 points · Solved**

## 1. Original challenge source

The handout contains `gen.py`, `output.txt`, `pyproject.toml`, `uv.lock`, and `sha256sum`. The organizer checksum check passes for every listed input file.

The essential generator code is:

```python
flag = Path("flag.txt").read_bytes().strip()
pt = random.randbytes(2536)
keystream = np.tile(np.frombuffer(flag, dtype=np.uint8),
                    (len(pt) + len(flag) - 1) // len(flag))[:len(pt)]
ct = np.frombuffer(pt, dtype=np.uint8) ^ keystream
output = base64.b64encode(ct)
```

This is not classical polyalphabetic Vigenère. Each byte satisfies

```text
C[i] = R[i] XOR K[i mod 48],
```

where `R` is a continuous Python MT19937 `randbytes` output, and `K` is the 48-byte flag.

**Do not rerun `gen.py` using the demo flag.** That creates new randomness and replaces the original evidence. Decode `output.txt` as Base64 to obtain exactly **2536 bytes**.

## 2. Why the output length matters

Python's `random.randbytes` calls `getrandbits`, producing byte output little-endian from the MT19937 32-bit words. The file comprises

```text
2536 bytes / 4 = 634 32-bit outputs
               = 624 original-state words + 10 words after the state twist.
```

MT19937 produces an output `y_j = temper(s_j)`, where `s_j` is a 32-bit internal state word. `temper` is invertible over GF(2), with the standard XOR-shift and bitmask stages:

```text
y ^= y >> 11
y ^= (y << 7)  & 0x9d2c5680
y ^= (y << 15) & 0xefc60000
y ^= y >> 18
```

Undo these stages in reverse order (iterative XOR-shift inverses). For any *hypothetical* 48-byte key, `R[i]=C[i] XOR K[i mod 48]`, and the first 624 words can therefore be untempered into a hypothetical MT state.

## 3. The twist yields linear constraints on the flag bits

The first ten outputs following the 624-word boundary are **predicted** from the first 624 untempered words. For `j=0..9`:

```python
y = (state[j] & 0x80000000) | (state[j+1] & 0x7fffffff)
next_state[j] = (state[j+397] ^ (y >> 1) ^
                 (0x9908b0df if y & 1 else 0))
predicted_word[j] = temper(next_state[j])
```

The actual last 40 ciphertext bytes, after XORing the same candidate repeating key, provide ten observed words. Let

```text
F(K) = predicted_next_10_words(K) XOR observed_next_10_words(K).
```

**F is an affine GF(2) map on the 384 key bits.** Untempering, twisting, tempering and XORing are linear/affine at the bit level. The apparent `if y&1` is linear because it conditionally XORs a fixed vector for one input bit.

For a zero key `K₀=0`, compute `b=F(K₀)`. Flip each of 384 key bits independently and construct a 320-row binary coefficient matrix:

```text
column_i = F(K₀ XOR (1<<i)) XOR b
A*K = b over GF(2).
```

This is an exact algebraic reconstruction, not a statistical guess and not a plaintext frequency attack.

## 4. Add only legitimate flag-format assumptions

For this recovered case, the accepted key has 48 bytes. We impose the conventional `gctf{` first five bytes and `}` at byte 47, then require each byte to be 7-bit ASCII (its high bit zero). This yields

```text
320 MT relations
 48 known frame bits (gctf{ + })
 48 high ASCII zero bits
--------------------------------
416 written equations / 384 key variables
rank 363; 21 degrees of freedom.
```

**The important result:** every free bit is confined to **positions 44, 45, 46** (zero based): seven bits of each of these three bytes. The other 45 characters are fixed. The exact solver output on the unmodified handout is

```text
ciphertext bytes: 2536
key length assumption: 48
equations: 416 rank: 363 free bits: 21
recovered: gctf{from_rand0m_!mpor+_n0+_s0_r4nd0m_af+3r_???}
unknown byte positions: [44, 45, 46]
```

This is more precise than claiming that 320 relation bits uniquely recover all 384 flag bits. The ten post-twist words simply do not provide information about the final three key phases, so there are **2²¹** assignments under the seven-bit assumption. Most are not plausible flag endings, but they remain mathematically consistent.

## 5. Resolve the suffix and verify the accepted value

The previously solved challenge conversation records the platform-accepted key, whose final three body characters are **`al1`**. These characters are **not independently inferred from the current ciphertext alone**; they resolve the genuine ambiguity using the historical accepted answer.

Passing that entire candidate to the new solver yields

```text
MT19937 next-state mismatched bits: 0
Candidate passes all 320 MT checks.
```

The result proves consistency of the complete accepted flag with the original organizer output and MT19937 recurrence. It does **not** prove that no other final three characters fit the ciphertext; the challenge platform's accepted submission is the external discriminator. The body contains literal `+` and `!` punctuation: replacing them with spaces would change the XOR key and fail.

## 6. Reproduce the result

Extract the official tarball and run the independent stdlib Python solver:

```sh
tar xf twisted-vigenere.tar.gz
python3 scripts/solve_mt.py twisted-vigenere/output.txt
```

Optionally supply an already-known 48-byte candidate as a second argument to check it; the script does **not** have the flag embedded. It reconstructs the GF(2) matrix using 384 basis flips, performs packed-bit Gaussian elimination, lists the fixed bytes and verifies all 10 post-twist 32-bit words for the supplied candidate.

**Only Python standard library is required** for our solver. The original `gen.py` requires NumPy but is retained for source provenance, not needed to recover the flag.

## 7. Why this works and what it does not prove

The underlying mistake is combining a repeating secret with a *linear, predictable* pseudorandom plaintext. A true one-time pad needs uniform independent bytes, not a small-state PRNG whose output admits algebraic state recovery. The 624-word MT19937 boundary is especially helpful.

Other tempting approaches—text-frequency analysis, guessing an English phrase, or fitting an ordinary Vigenère alphabet shift—do not express the real structure. MT19937 state consistency gives exact binary equations; the accepted scoreboard answer resolves the **three-byte blind spot**.

## 8. Provenance and integrity

Original `twisted-vigenere (1).tar.gz` SHA-256:
`a773b23f6b9900ff0769c3bf4b3d0f189958a0bac88d2c87f2b44ebf3099a898`.

Extracted `gen.py` SHA-256:
`f4fbecb6f174eebc5cd67f20c841855bbbe4e2ffb69a4d6a9634d0f17b2db10b`.

Extracted `output.txt` SHA-256:
`26560d98497eb305c741f33779bb7724edaa2301b3a8c80c7e6ada377a57bf77`.

All four organizer checksum entries passed. The new script was executed against the actual extracted ciphertext and the accepted key. Neither the original secret seed nor an initial `random.getstate()` was available or needed.

The full accepted flag is deliberately masked in the public repository; the user already has the exact accepted string in the solve conversation.
