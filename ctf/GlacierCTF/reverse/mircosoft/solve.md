# mircosoft — decrypting a machine-bound Windows license from an administrator MiniDump

[Русская версия](solve.ru.md)

**GlacierCTF 2026 · Reverse engineering / Windows · 50 points · Solved**

## 1. The actual organizer handout

The attached `mircosoft (1).tar.gz` contains precisely:

```text
mircosoft/mircosoft.exe       27136 bytes
mircosoft/license.bin          104 bytes
mircosoft/admin_machine.dmp  50328 bytes
mircosoft/sha256sum             248 bytes
```

All three recorded organizer SHA-256 hashes were checked successfully. The launcher is a **PE32+ / x86-64 Windows console executable**, built as an obfuscated/protected loader. The earlier successful solution recovered a separately encrypted PE component and analyzed its license-verification routines; anti-debug checks were also noted. The file `unpacked_pe.bin` and the helper that extracted it **were generated in the prior working directory, not supplied in the handout**.

The flag was recovered in the November 20 solve conversation and accepted by the CTF platform. We retain the *original final decryption script* as [scripts/solve.py](scripts/solve.py) rather than guessing missing earlier unpacking steps.

## 2. License layout

The 104-byte `license.bin` begins with `MSLC`. The preserved solver treats the important fields as:

| Byte offset | Length | Function |
| --- | ---: | --- |
| `0x00` | 4 | `MSLC` magic |
| `0x04` | 4 | Version/metadata |
| `0x08` | 16 | Stored fingerprint MAC |
| `0x18` | 8 | Stream nonce |
| `0x20` | 64 | Eight encrypted 8-byte blocks |
| `0x60` | 8 | Trailing data, unused by retained final script |

The 16-byte stored MAC of the authentic file is `8305d5d5c5316f76bd27ddbbc5221f56`. It was matched by the reconstructed fingerprint MAC in the **historically successful solve**, not independently recomputed in this publication (the unpacked PE keys were not retained).

## 3. Reconstructing MiniDump regions

The administrator dump is a real **MiniDump** (`MDMP` magic), containing five streams. The **MemoryListStream (type 5)** describes seven physical file spans mapped into virtual memory. We wrote [scripts/extract_minidump.py](scripts/extract_minidump.py) to parse the MiniDump header, walk the stream directory, validate span bounds and save `region0.bin` through `region6.bin`. That part was independently reproduced from the current handout.

| Region | Base virtual address | Size |
| --- | --- | ---: |
| `region0.bin` | `0x7ffe0000` | 4,096 |
| `region1.bin` | `0x1c000000000` | 8,192 |
| `region2.bin` | `0x20000000000` | 16,384 |
| `region3.bin` | `0x21000000000` | 8,192 |
| `region4.bin` | `0x22000000000` | 4,096 |
| `region5.bin` | `0x7ff000000000` | 4,096 |
| `region6.bin` | `0x7ff000001000` | 4,096 |

Only the first five regions contribute to the hardware fingerprint; the final two are nevertheless preserved.

## 4. Five fingerprint elements

The original `solve.py` selects the following spans, in the **exact concatenation order** shown:

| Component | Source | Length | Observed value |
| --- | --- | ---: | --- |
| CPUID brand string | `region0+0x274` | 48 | `Intel(R) Core(TM) Ultra 7 155H`, NUL-padded |
| Volume serial | `region4+0x48` | 4 | `914c8dd4` (raw hex bytes) |
| Registry MachineGuid | `region1+0x500` | 36 | `658c05ff-e49e-40aa-b041-9a5f56886dea` |
| SMBIOS UUID | `region2+0x55` | 16 | `3c31a857b2b700488ce185385968c8a8` |
| NIC MAC | `region3+0x198` | 6 | `52540036e664` |

Total: **110 bytes**. The assembled fingerprint has SHA-256

```text
9887f856a75c76842491cd7fff0ee47410c3dc97cebeb71c8cc6db624b24c238
```

This hash was computed independently against the actual `admin_machine.dmp`. The CPUID field includes its NUL bytes; dropping padding or treating raw UUID/MAC bytes as formatted text would change the license MAC.

## 5. The TEA-like block primitive

The earlier script extracts **four distinct 16-byte secrets** from the *unpacked* PE's `.data` segment, at RVA `0x6000` / raw file offset `0x3e00`. These are `keys[0]` through `keys[3]` in [scripts/solve.py](scripts/solve.py).

Its block cipher reads 8-byte blocks as two little-endian 32-bit words and applies 32 rounds with the TEA-family delta `0x9e3779b9`. Unlike ordinary TEA, the two half-rounds use key indices controlled by the running round sum:

```python
v0 = u32(v0 + (((u32(v1 << 4) ^ (v1 >> 5)) + v1) ^
                 (running_sum + k[running_sum & 3])))
running_sum = u32(running_sum + 0x9e3779b9)
v1 = u32(v1 + (((u32(v0 << 4) ^ (v0 >> 5)) + v0) ^
                 (running_sum + k[(running_sum >> 11) & 3])))
```

Each `k` has four little-endian 32-bit words. The original helper is named `tea()` in the saved script.

## 6. Custom 128-bit MAC

The `mac(state, data, k0, k1)` function treats a 16-byte state as four little-endian words and processes data in 4-byte chunks, NUL-padding its final chunk.

For each chunk it XORs the first word of the state with the input word, encrypts two 8-byte halves with `tea(..., k0)` and `tea(..., k1)`, then mixes the halves:

```python
vals[3] ^= vals[1]
vals[0] ^= vals[2]
```

The first check starts at the fixed IV:

```python
IV=struct.pack("<4I",0x67452301,0xefcdab89,0x98badcfe,0x10325476)
expected = mac(IV, fingerprint, keys[2], keys[3])
assert expected == license[8:24]
```

The earlier solve printed the computed MAC and the header MAC and confirmed equality. The *saved code* prints the comparison but does not enforce this `assert`, so it is advisable to verify the printed values before trusting the decrypted result.

## 7. Deriving the license decryption key

The stream key is **not** just the MAC from the header. Starting from 16 zero bytes, the solver applies the same stateful MAC separately to three groups with `keys[0]` and `keys[1]`:

```python
state=bytes(16)
for part in (cpuid, volume_serial + machine_guid, smbios_uuid + mac_address):
    state=mac(state, part, keys[0], keys[1])
```

The resulting 128-bit state is transformed by word-level rotations and XOR/add operations (see exact `transform()` in the source). This is the 16-byte key used to generate the final ciphertext keystream.

## 8. Decrypting eight 8-byte blocks

The two 32-bit little-endian words `v0,v1` in `license[24:32]` initialize a counter. For `j=0..7`, the original script calculates:

```python
stream = tea(pack_u32(v0+j) + pack_u32(v1), derived_key)
plaintext_block = license[32+8*j:40+8*j] XOR stream
```

Concatenating the eight blocks yields the decrypted 64-byte license payload. The historically saved `solve.py` printed the plaintext, containing the accepted `gctf{...}` answer. The public Markdown masks the literal flag while preserving the algorithm and input offsets.

## 9. Reproduction commands

Unpack the official archive and extract the dump regions:

```sh
tar -xzf mircosoft.tar.gz
python3 scripts/extract_minidump.py mircosoft/admin_machine.dmp --out-dir /mnt/data/mircosoft_work
```

Copy `license.bin` into `/mnt/data/mircosoft_work/mircosoft/` and place the *previously unpacked* executable at `/mnt/data/mircosoft_work/unpacked_pe.bin`; then run the retained original:

```sh
python3 scripts/solve.py
```

**Note:** `solve.py` contains a historical absolute working directory `/mnt/data/mircosoft_work`. It is purposely retained as the original artifact; adjust `B` in the first lines for other systems. Its reference to `unpacked_pe.bin` means **the supplied organizer archive alone cannot run this final stage without PE unpacking**. No unsupported automatic unpacker is claimed.

## 10. Source integrity and reproducibility limits

Original file SHA-256:

```text
mircosoft.exe      5412f535d238bdf2418f179044dcd2ee25c6cc3d60d2669196bc9e5131f99712
license.bin        56b71ff7a851d4b7c074637cd1a96f458b32e2e306754b788c01af8fe8b89891
admin_machine.dmp  a51edf96a395692dabfb67b654f4eb3f4742dd483da8d7d3d49579a369d2d5e4
```

The accepted result, matching integrity MAC and original final solver are established by the prior solve conversation (20 November 2026). The current handout and MiniDump regions are independently checked. **The unprotected PE extraction, precise intermediate state values and final decryption were not rerun in this turn** because the separately generated unprotected PE is absent. We clearly distinguish those historical observations from the new validations.
