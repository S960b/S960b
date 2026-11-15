# Matryoshka: extracting a nested ELF and inverting four CRC32 checks

[По-русски](solve.ru.md)

## 1. Finding the next layer

The supplied `matryoshka` is a stripped x86-64 ELF that creates a new anonymous executable file with `memfd_create("stage2", MFD_CLOEXEC)`, writes **154,328 bytes** to it, and runs the nested program. This can be seen with:

```bash
strace -f -e trace=memfd_create,execve,write -s 60 ./matryoshka
# memfd_create("stage2", MFD_CLOEXEC) = 10
# write(10, "\\177ELF...", 154328) = 154328
```

The nested stage accepts a hexadecimal password and prints `Invalid hex` for a non-hexadecimal test input. The original investigation also recovered the `stage2.bin` ELF bytes from the captured `write` trace and examined its code and `.rodata`.

## 2. The four CRC32 constraints

The password is **16 bytes**; each four-byte word must match a separate CRC32 value:

```python
targets = [0x6d795f63, 0x30306c5f, 0x70343473, 0x77307264]
```

For four input bytes, `zlib.crc32` is **affine over GF(2)**. Subtract the constant output `C(0)` by XOR, then construct a 32×32 binary matrix from the effects of toggling each input bit:

```text
C0    = CRC32(00 00 00 00)
L(x)  = CRC32(x) XOR C0
cols[j] = CRC32(1 << j encoded as 4 little-endian bytes) XOR C0
XOR_j (bit[j] * cols[j]) = target XOR C0
```

Gaussian elimination over GF(2) recovers each four-byte word independently. The script [`scripts/solve_crc.py`](scripts/solve_crc.py) implements this calculation without a dictionary or brute-force search:

```bash
python3 scripts/solve_crc.py
```

The recovered key is:

```text
password hex: c667c58c3b02d6c91b8def4fdd1bc5f3
CRC[0:4]   = 6d795f63
CRC[4:8]   = 30306c5f
CRC[8:12]  = 70343473
CRC[12:16] = 77307264
```

The 16-byte secret is binary data entered as **32 hexadecimal characters**, not an ASCII phrase. The visible string `my_c00l_p44sw0rd` is associated with the CRC target bytes and should not be submitted as the password.

## 3. Verifying the password and retrieving the flag

The recorded session ran the extracted executable directly:

```bash
printf 'c667c58c3b02d6c91b8def4fdd1bc5f3\n' | ./stage2.bin
```

The original output:

```text
Enter password: Password correct!
Here is your flag: PP{th3r3_1s_4_b1n4ry_1n_my_b1n4ry?!}
```

A later reproduction package also records the same output from the supplied outer `matryoshka` executable with that password on stdin:

```bash
printf 'c667c58c3b02d6c91b8def4fdd1bc5f3\n' | ./matryoshka
```

The competition submission returned **HTTP 200, `{"status":1}`**. The challenge was solved and the full flag is confirmed.

## 4. How the XOR payload fits into the design

Disassembly of the nested stage showed a 36-byte encrypted region at **file offset `0x1b560`**. The recovered 16-byte password repeats as the XOR key:

```python
data = open("stage2.bin", "rb").read()
cipher = data[0x1b560:0x1b560 + 36]
password = bytes.fromhex("c667c58c3b02d6c91b8def4fdd1bc5f3")
flag = bytes(c ^ password[i % 16] for i, c in enumerate(cipher))
print(flag)
```

This alternate check printed the **same** flag. Running the program with the correct password already performs the necessary verification and reveals the plaintext, so manual XOR is valuable as a reverse-engineering proof, not an additional requirement for submitting the flag.

## 5. Lessons

A CRC32 condition on 32 input bits yields 32 reversible affine equations. Four such independent checks do not make the 16-byte secret cryptographically difficult to recover. The nested ELF and repeating XOR add packaging complexity, not strong secrecy.

[Source-backed session checkpoints](evidence/session-checkpoints.md) · [CRC solver](scripts/solve_crc.py)
