# Litter: solving path

## 0. The whole plan

The handout is one file, `flag.png.ltr`: the ransomed pudding picture. The prompt
says it was locked and the key is somewhere in a video. The plan was:

1. Find the hidden data in the video (subtitles, not frames).
2. Follow the URL that the hidden data reveals; leak the older Git history from the
   web part and get the encryptor binary.
3. Reverse the ARM64 encryptor and build an exact numeric model of the cipher.
4. Notice the cipher flaw (damaged S-box, round-key cancellation) and turn known PNG
   structure into key recovery.
5. Verify with the GCM tag, restore the PNG and read the flag.

Everything except the visual "recognize the video" step was reproduced with plain
Python; the solver output in `evidence/VERIFY_RUN.txt` is from a real run.

## 1. The video: subtitles carry the hint, not the pixels

Why this step: the delivered `flag.png.ltr` contains a short visible header and then
random-looking data, so the first question is where the key material lives.

`flag.png.ltr` starts with:

```text
LITTER/5
https://www.youtube.com/watch?v=652tk3UnO4E
```

followed by 12 bytes of nonce, 1,621,331 bytes of ciphertext and a 16-byte tag. The
linked video plays as near-black frames, but the browser shows an orange pudding made of
dots with flickering 0/1 and X/O marks - which is exactly what YouTube renders from
**caption text**, not from the video track. Fetching the subtitle files
(`pudding.en.json3` and friends) gives structured events instead of pixels.

`decode_captions.py` does two extractions:

- from all events, the first 0/1 digit in the text, in chronological order: 168 bits,
  grouped by 8, decode to `Think outside the box`;
- the 432 events ending in X or O, sorted by their horizontal position
  (`ahHorPos` = 1000, 2000, ..., 432000), with X=1 and O=0, decode to a URL of the
  challenge's web part (masked as `<host>`).

Two independent hidden messages, no OCR involved.

## 2. Web part: the .git leak and the legacy repository

Why this step: the URL points to a fake ransomware page ("Litter File Recovery
Center", case LTR-24 / INTAKE-7C, 0.314 "LTR credits") whose promises are irrelevant;
what matters is what the server accidentally serves.

The public files exposed Git metadata:

```text
/.git/HEAD          -> ref: refs/heads/main
/.git/config        -> [extensions] objectformat = sha256
                       [migration] legacyArchive = migration/legacy.tar.gz
/.git/migration/legacy.tar.gz -> valid gzip/tar
```

`legacy.tar.gz` is an older SHA-1 repository. `git fsck --unreachable` finds the
commit `3f942f5db43c910ae637c41c3fdb13a7da19d923` ("Capture release smoke-test
output"), which saves the parts of a signed link to the release archive, and the
`get_release.sh` script rebuilds and fetches it.

## 3. The encryptor and why its stored seed is useless

Why this step: before attacking the cipher, verify that the seed inside the ELF is
actually the one that locked the picture - it is not.

`release.tar.gz` contains one ARM64 ELF, `encryptor` (72,074 bytes, SHA-256
`a0010e09...874`). A 24-byte seed is embedded and marked by the string `LITTERKEY2`.
But after encrypting, the program reads new random bytes from `/dev/urandom` and
overwrites its own seed, so the released binary keeps a **rotated** secret. Testing the
stored seed against the delivered file fails the tag check.

The ELF resists static reading: `SIGTRAP` handling (anti-debug), a self-integrity check,
and crypto functions that are only present in an executed branch. The working approach
was to run controlled reference PNGs under QEMU/GDB and match the bytes, which produced
the exact numeric model in `litter_crypto_verified.py`.

## 4. The cipher: damaged AES-192 S-box and round-key cancellation

Why this step: the actual weakness is in the cipher itself, and it is what makes a
192-bit key recoverable from structure alone.

The 24-byte seed is expanded into a 24-byte derived key by a permutation, XOR with a
linear pattern and rotation:

```python
K[i] = ROL8(seed[(5*i) % 24] ^ ((0x71 + 0x3d*i) & 0xff), (i % 7) + 1)
```

This K is the AES-192 key (12 rounds). The substitution table in the executed
implementation is damaged:

```python
S_damaged(x) = S_AES(x) if 0 <= x < 128 else 0
```

so at most 128 distinct output bytes exist and half of the inputs destroy information.
The same table is used in key scheduling.

For each image byte, before AES-CTR, the plaintext is masked and rotated and mixed with
two key streams:

```python
mask_i = (167 + 29*i) & 255
rot_i  = (i % 7) + 1
Pprime_i = ROL8(P_i ^ mask_i, rot_i) ^ K[i%24] ^ RK_last[i%16]
C_i = Pprime_i ^ AES_K(nonce || uint32_be(2 + i//16))[i%16]
```

The AES counter starts at 2; the final round of AES-192 has no MixColumns, so:

```text
AES_output_byte = S_damaged(X_i) XOR RK_last[i % 16]
```

Substituting into C_i, the two copies of `RK_last` cancel:

```text
C_i XOR ROL8(P_i XOR mask_i, rot_i)  =  K[i % 24] XOR S_damaged(X_i)
```

The left side is computable for every known plaintext byte, and since the S-box maps
the upper half of the domain to 0, about half of the observations are already exactly
`K[i%24]`. Candidates for each key byte are filtered by the requirement that
`observation ^ candidate` belongs to the 128-value image of the damaged S-box.

## 5. Known bytes: PNG signature, IEND and repeated IDAT headers

Why this step: the equation above needs known plaintext, and a PNG gives plenty.

The fixed PNG prefix (signature + IHDR header) and the fixed IEND tail give the first
strong candidate key bytes, used only as a detector:

```text
K[2]=bf  K[3]=b8  K[5]=36  K[8]=31  K[10]=44
```

The image was written by an encoder that splits the compressed stream into fixed-size
IDAT chunks of 16,384 data bytes, each preceded by the same 8 plaintext bytes:

```text
00 00 40 00 49 44 41 54
| 16384   | | IDAT    |
```

The solver tries typical chunk sizes and a start offset; on the delivered file it finds
offset 495, chunk length 16384, stride 16396, 98 full IDAT headers (plus a shorter
last chunk, 14004 bytes), 104 chunks total. 98 headers give 784 exact plaintext bytes,
roughly 32-34 observations per `i % 24` class.

## 6. Key recovery and verification

Why this step: this is where the attack closes, and the GCM tag is the proof that the
result is right rather than merely plausible.

For each position j in 0..23, collect the observations whose position has remainder
`j` when taken mod 24 and keep the
key bytes compatible with the damaged S-box image for all of them. In the delivered
file every position leaves exactly one value. The recovered derived key:

```text
a5cebfb829368d1e31f8445e4d5301392083e54ed2a23a96
```

and inverting the seed expansion (multiply-by-5 permutes positions mod 24, `gcd(5,24)=1`):

```text
seed = a37a974a901dc4e245231c13784621a3152c943e2c8947c1
```

The GCM tag computed with this seed equals the stored 16 bytes
(`5336b376...31809c`), the restored 1,621,331-byte PNG passes every chunk CRC, ends
with IEND, and decodes as 1254x1254 RGBA. The flag is written at the bottom of the
image. SHA-256 of `flag.png` and `flag_recovered.png` match
(`32a0cdfb...77207`).

`recover.py` refuses to output anything when the tag or CRC checks fail, so the
recovered picture is never a guess.

## 7. Reproduction

Why this step: the deliverable is a single command that turns the shipped files back
into the flag picture.

```bash
python3 -m pip install numpy
cd scripts
python3 recover.py        # expects ../assets/flag.png.ltr and ../assets/encryptor
```

Expected lines (see `evidence/VERIFY_RUN.txt` for the full output):

```text
Initial strong byte proposals {2: 'bf', 3: 'b8', 5: '36', 8: '31', 10: '44'}
IDAT found: first_offset=495, chunk_length=16384; score=77/161 (47.8%)
Recovered 24-byte derived key: a5cebfb829368d1e31f8445e4d5301392083e54ed2a23a96
Recovered original 24-byte seed: a37a974a901dc4e245231c13784621a3152c943e2c8947c1
GCM tag computed: 5336b3766e2e6c0ab825cea36231809c
GCM tag expected: 5336b3766e2e6c0ab825cea36231809c
FLAG_PNG_RECOVERED ... PNG_CRC_ALL_PASS 104 chunks
```

## 8. Dead ends (do not repeat)

- **Plain AES-256 / AES-CCM reading of the ELF.** The first functions believed in give
  a model that does not verify; the executed branch is the damaged AES-192-GCM. Do not
  carry a "key mismatch" conclusion from a wrong crypto model.
- **Web Archive / Git for the early binary.** No snapshots of the temporary domain
  exist; the git leak gives the release, but not the pre-rotation seed.
- **Full-QEMU verification of the big file.** Encrypting 1.6 MB under emulation is
  extremely slow; all model checks were done on short and ~8 KB references.
- **Patching the ELF.** The self-integrity check aborts with exit 125; under GDB the
  expected SIGTRAP must be handled first.
- **48-byte keystream repetition.** The AES blocks collapse over isolated 2048-byte
  windows, but not across the whole file; top-bit dependence shifts the collapse.
- **Full symbolic AES in Z3.** With the correct known key the model is SAT, with a
  wrong one UNSAT, but searching an unknown 192-bit key returns unknown / times out.
  The answer is more known plaintext, not a better solver.
- **64-KB IDAT assumption.** Wrong; the real chunk size is 16 KB, found together with
  the start offset by the joint search.

## One-paragraph version

The hidden messages in the video subtitles (`Think outside the box` and a URL) lead to
a fake ransom site whose exposed Git history leaks a signed release URL; the release is
the ARM64 `encryptor`. The encryptor uses AES-192-GCM with a damaged S-box for inputs
>=128 and rotates its own seed away, but the final-round key cancels in the CTR stream,
so the fixed PNG structure (98 repeated 16 KiB IDAT headers) yields 784 known bytes
and the whole 24-byte key. The GCM tag verifies, the PNG restores, and the flag is at
the bottom of the picture.

## Lessons learned

- Subtitles are data: any text channel rendered by a client can carry the message, and
  json3 gives exact positions without OCR.
- A seeded self-destructive encryptor makes the embedded key a red herring; the cipher
  flaw is the real oracle.
- Damaged S-box + canceled final round key = known-plaintext key recovery with a
  maybe-30-lines check; verify with an independent authenticator (GCM tag), never with
  "it looks like a PNG".

Flag: `hitcon{too_many..._pudding}`
