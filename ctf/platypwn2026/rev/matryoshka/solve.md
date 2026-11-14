# Matryoshka: four CRC equations and an XOR-protected payload

[По-русски](solve.ru.md)

## 0. Plan and what is actually preserved

The supplied challenge file is a stripped 64-bit PIE executable. Its imported functions include `memfd_create` and `fexecve`, consistent with the reported multi-layer execution scheme. The preserved work log describes the final calculation, but it does not preserve every command used to unpack the intermediate layers.

The recorded solution is not a password dictionary attack. The final stage imposes four CRC32 conditions on a 16-byte password. Because CRC32 is affine over GF(2), each four-byte condition can be inverted by solving a **32 × 32 system of binary equations**. The recovered 16 bytes then XOR-decode a 36-byte region of the extracted second-stage executable.

The sequence is:

1. Identify the ELF and obtain the next stage (full original extraction transcript unavailable).
2. Read the four target CRC values from the preserved solver evidence.
3. Reconstruct the exact password by binary linear algebra, checking all four CRCs.
4. XOR the data slice from the extracted `stage2.bin`.
5. Verify the flag. Only the mathematical stage can be fully reproduced with the surviving archive.

## 1. Inspecting the artifact

The stripped ELF was part of the event's original downloadable archive. The public Git repository currently contains the analysis and solver, **not the binary**. To run the static-inspection commands below, first copy the downloaded binary to `assets/matryoshka`. Its file properties were checked directly:

```text
ELF 64-bit LSB pie executable, x86-64, dynamically linked, stripped
interpreter /lib64/ld-linux-x86-64.so.2
```

Relevant static inspection:

```sh
file assets/matryoshka
readelf -h assets/matryoshka
readelf -Ws assets/matryoshka | grep -E 'memfd_create|fexecve|write'
strings assets/matryoshka | grep -E 'memfd_create|fexecve'
```

The imported `memfd_create` and `fexecve` make the reported nested process execution plausible: an executable can be written into an anonymous in-memory file and then executed from its descriptor. But imports **do not prove the exact extraction method or the number of layers**. We cannot reconstruct undocumented offsets or assert that a dynamic trace was run when it was not preserved.

## 2. The four CRC targets

The surviving `evidence/solve_1.txt` explicitly contains:

```python
targets = [
    0x6d795f63,
    0x30306c5f,
    0x70343473,
    0x77307264,
]
```

Each is a CRC32 of a different four-byte word of the password. Let `C(x)` mean Python's `zlib.crc32` on a four-byte array. CRC32 is affine, not strictly linear because its initialization/finalization includes a constant. To remove that constant, calculate:

```text
C0 = C(00 00 00 00)
L(x) = C(x) XOR C0
```

Then `L` is linear over GF(2): `L(x XOR y) = L(x) XOR L(y)`.

For each bit position `j` (0–31), create a four-byte vector with **only bit j set**, and calculate its contribution:

```python
cols[j] = C(1 << j, encoded as 4 little-endian bytes) ^ C0
```

For an unknown word with bits `b[0]..b[31]`, the checksum condition becomes:

```text
XOR( b[j] * cols[j] for j=0..31 ) = target XOR C0
```

Equate all 32 output bits. This yields 32 equations in 32 Boolean unknowns. XOR is addition modulo 2. No approximate floating-point arithmetic is involved.

## 3. Solving with binary Gaussian elimination

The reproducible implementation is in [scripts/solve_crc.py](scripts/solve_crc.py). It constructs a 32-bit coefficient mask for every equation, appends the target bit as the 33rd bit, and eliminates pivots using integer XOR. The solve is repeated independently for all four targets.

```sh
python3 scripts/solve_crc.py
```

Recomputed locally from the archived targets:

```text
password (hex): c667c58c3b02d6c91b8def4fdd1bc5f3
CRC[0:4]   = 6d795f63
CRC[4:8]   = 30306c5f
CRC[8:12]  = 70343473
CRC[12:16] = 77307264
```

These CRC checks were recalculated independently and match **all four** recorded targets. The password is binary data; interpreting it as a printable ASCII phrase would be misleading.

The exact 16-byte value is the crucial recovered intermediate, not the flag itself.

## 4. The second-stage XOR

The archived command contains the following final operation:

```python
d = open('/tmp/gigatool/stage2.bin', 'rb').read()
blob = d[0x1b560:0x1b560+36]
flag = bytes(blob[i] ^ pw[i % 16] for i in range(36))
print(flag)
```

This is a repeating-key XOR. The source range is 36 bytes starting at absolute **file offset** `0x1b560` of the extracted `stage2.bin`, and the 16-byte password repeats across the range. The encrypted bytes are not in the original archive: `stage2.bin` was a working intermediate, not a supplied task attachment. Consequently, the password and CRC verification are reproducible, but the final flag decode cannot be independently rerun from the ZIP alone.

With the intermediate stage recovered, the script accepts it directly:

```sh
python3 scripts/solve_crc.py --stage2 stage2.bin
```

## 5. What is confirmed, and what is missing

**Confirmed from the preserved evidence:** four target values, full GF(2) solver logic, the XOR file offset, the extracted byte count, and the repetition period. **Recomputed in this review:** the 16-byte password and all four CRC32 checks. **Confirmed by the event report:** accepted flag, shown publicly as `PP{...}`.

**Not preserved:** the dynamic command sequence that extracted every nested ELF, `stage2.bin` itself, and the actual flag-printing stdout. The extraction method has intentionally not been filled with invented commands.

## 6. Lesson

CRC32 is useful for accidental-corruption detection. It provides no protection against a solver who can reconstruct an affine map and invert it. Splitting a secret into four CRC-checked chunks does not add cryptographic secrecy; the chunks remain independently recoverable. A repeating XOR key is equally unsuitable for encrypting an embedded secret.
