# NeuroPet: the path to the solution

## 0. The whole plan

Why this section: so the reader sees the chain down to the details and understands why every next
step is needed.

Given: a file `neuropet.ab` of 359 458 bytes and a task text that says "a neuro-tamagotchi in every
home, password 0000". Nothing else.

Goal: find the flag.

The plan came out like this:

1. Understand the `.ab` format and unpack it.
2. See what is inside: user settings or the application itself.
3. Find everything that looks like hidden data: resources and the native library.
4. Read the strings of the library to understand what it does.
5. Parse the private container format and decrypt it.
6. Work out what kind of program sits inside and run it offline.
7. Assemble the flag from what that program emits.

## 1. What is given

Why this step: to pin down the input file and its hash, so every later number belongs to a verified
copy.

```
$ file neuropet.ab
neuropet.ab: Android Backup, version 1, Compressed, Not-Encrypted
$ sha256sum neuropet.ab
698a730af816cf91f324aeccbdde8ce66d5ca9acb2e367cb8f65d7b26a27eae9
```

One important thing is visible already: the backup is **not encrypted**. So the hint about the
password `0000` refers to logic inside the application, not to the file.

## 2. Android Backup: format and unpacking

Why this step: to get what is inside the backup without fighting a password that is not needed here.

I googled the `.ab` format, it is described in the Android documentation. The file starts with four
text lines, and they explain everything without guessing:

```
$ head -c 64 neuropet.ab | xxd
00000000: 414e 4452 4f49 4420 4241 434b 5550 0a31  ANDROID BACKUP.1
00000010: 0a31 0a6e 6f6e 650a 78da ecbb 7554 d64f  .1.none.x...uT.O
```

The lines mean: the signature `ANDROID BACKUP`, format version `1`, content compressed `1`, that is
zlib, and `none` instead of an encryption password. After the fourth newline the zlib stream
starts, and it unpacks into an ordinary TAR.

My script `scripts/01_unpack_android_backup.py` does that:

```
$ python3 scripts/01_unpack_android_backup.py neuropet.ab --out work
Android Backup: {'version': '1', 'compressed': True, 'encryption': 'none'}
TAR size: 378880
TAR: 1672 apps/com.solarlabs.neuropet/_manifest
TAR: 364193 apps/com.solarlabs.neuropet/a/base.apk
TAR: 71 apps/com.solarlabs.neuropet/f/rList
```

The archive has three entries, and the main one is `base.apk` of 364 193 bytes, that is the
application itself, not user settings. The hash of the resulting APK:

```
$ sha256sum work/base.apk
5c2f61f102bda136956e25867b4a4fa6b2f65592251e4c978ed86d9fbff9ee49
```

## 3. What the APK contains

Why this step: to know where to look for logic, in the Java part or in native code.

An APK is an ordinary zip, so I listed its contents and wrote down what matters:

```
$ unzip -l work/base.apk
    24764  classes.dex
    11320  lib/arm64-v8a/libneurocore.so
    11960  lib/x86_64/libneurocore.so
     1865  assets/neuro/brain.bin
     5342  assets/neuro/skin.dat
   262154  assets/neuro/weights.bin
```

What this means. `classes.dex` is compiled Java code for Android. Files with the `.so` extension are
native libraries in C or C++, that is ordinary ELF files for ARM64 and x86-64; the x86-64 build is
convenient on a laptop, because it matches the familiar `objdump`. The resources in `assets/neuro`
are named like parts of a neural network: brain, skin, weights.

Here comes the first fork. The names hint at a neural network, but for the analysis something else
matters more: next to the resources lies a native library of its own, and non-standard resource
formats almost always mean custom reading logic. I followed the native part.

## 4. Strings of the native library

Why this step: the cheapest way to learn what a library does before disassembling it.

```
$ readelf -Ws libneurocore.so | grep Java_com_solarlabs_neuropet
$ strings -a -t x libneurocore.so | grep -Ei 'NEUROC|frida|xposed|maps'
     b60 NEUROC1!NEUROC2!NEUROC3!NEUROC4!xposed
     b87 frida
     b8d gum-js
     b9b substrate
    bac /data/local/tmp/re.frida.server
    bcc /proc/self/maps
   1b60 NEUROC1!I
```

What this gives. Four JNI functions are exported, that is entry points called from Java: `nInit`,
`nMoodTick`, `nRun`, `nQuery`. The string `NEUROC1!NEUROC2!NEUROC3!NEUROC4!` is a template for
four markers that something is glued with. The remaining strings are names of dynamic analysis
tools and the path to the process memory map, so the library contains anti-debug checks.

An important caveat: the presence of such strings alone does not prove the checks fire. So instead
of booting an emulator and fighting the detection, I took the offline path: read the formats and run
the necessary part myself.

## 5. Finding the generator in native code

Why this step: to learn how the resources are encrypted, so the decryption can be done in my own
code.

I disassemble the loader part:

```
$ objdump -d -M intel --start-address=0x11a0 --stop-address=0x11d0 libneurocore.so
    11a0: 69 f5 6d 4e c6 41   imul   esi,ebp,0x41c64e6d
    11a6: 81 c6 39 30 00 00   add    esi,0x3039
    11ac: 69 ee 6d 4e c6 41   imul   ebp,esi,0x41c64e6d
    11b2: c1 ee 10            shr    esi,0x10
    11b5: 41 32 74 0d ff      xor    sil,BYTE PTR [r13+rcx*1-0x1]
    11ba: 40 88 34 08         mov    BYTE PTR [rax+rcx*1],sil
```

It reads like this: a number is multiplied by `0x41C64E6D`, `0x3039` is added to it, then one byte
is taken by shifting right by 16, and a ciphertext byte is XORed with it. That is the same linear
congruential generator as in old-program tasks: the state updates by
`state = (state * a + c) mod 2^32`, and one byte of the state is the key byte.

The modulus matters: `imul` and `add` here are 32-bit, the extra bits are lost by themselves. In
Python that has to be repeated explicitly with the mask `& 0xFFFFFFFF`, otherwise the numbers become
arbitrary precision and the result does not match.

## 6. The brain.bin container

Why this step: to understand what is encrypted and where the keys live.

```
$ xxd -g 1 -l 48 brain.bin
00000000: 4e 52 42 43 01 00 02 00 04 6d 6f 6f 64 ad a6 db  NRBC.....mood...
00000010: 86 3e 00 00 00 3e 93 13 ad c3 6a 90 0c 0f 92 85  .>...>....j.....
```

The first bytes give the format. `4e 52 42 43` is the text `NRBC`, then version `01 00` and record
count `02 00`, all little-endian, that is low byte first. Then a record follows: name length byte
`04`, the name `mood`, then the seed `ad a6 db 86`, that is `0x86DBA6AD`, then the size
`3e 00 00 00`, that is 62, and 62 bytes of ciphertext.

Parsing the whole file gives two records: `mood` with seed `0x86DBA6AD` and length 62 bytes, and
`devroom` with seed `0xD2020289` and length 1766 bytes. `mood` is the ordinary pet behaviour,
`devroom` looks like a hidden developer room, so I work with that one.

## 7. Removing the encryption

Why this step: to get readable bytecode that can be worked with.

The scheme from section 5 looks like this in code:

```python
state = seed
plain = bytearray()
for x in encrypted:
    state = (state * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
    plain.append(x ^ ((state >> 16) & 0xFF))
```

The full solver is `scripts/02_solve_neurovm.py`. Run:

```
$ python3 scripts/02_solve_neurovm.py work/base.apk --out solved
NRBC[0]: name=mood seed=0x86dba6ad length=62
NRBC[1]: name=devroom seed=0xd2020289 length=1766
```

The decrypted `devroom` starts with the bytes
`6f 14 00 00 00 14 8c 2d 46 a3 5a 53 05 14 63 93`. That is not text and not an executable file but
a sequence of instructions for an internal virtual machine. This is exactly why `strings` on
`brain.bin` found nothing: before decryption there are no readable strings there.

## 8. What the virtual machine is and how it was found

Why this step: to understand the rules that the bytecode follows.

A virtual machine is a program that imitates a processor: it has its own instruction set, its own
stack and its own instruction counter. Bytecode for such a machine cannot run on the processor
directly, it has to be executed by an interpreter.

The interpreter was found through a call in the JNI run function:

```
$ objdump -d -M intel --start-address=0x21f0 --stop-address=0x2210 libneurocore.so
    21fc: b9 80 f0 fa 02   mov    ecx,0x2faf080
    2201: e8 fa f0 ff ff   call   0x1300
```

The argument `0x2faf080` is a step limit, and `0x1300` is the interpreter itself. Inside it an
opcode is first transformed affinely, that is by a multiply and an add, and only then reaches the
dispatch table:

```
$ objdump -d -M intel --start-address=0x1408 --stop-address=0x1420 libneurocore.so
    140f: 6b c0 b7         imul   eax,eax,0xffffffb7
    1412: 0f b6 c0         movzx  eax,al
    1415: 83 c0 b5         add    eax,0xffffffb5
    1418: 83 f8 11         cmp    eax,0x11
```

So an opcode byte is multiplied by `-0x49`, `-0x4B` is added to the result, and then it is compared
with `0x11`. That means the machine has twelve handlers, and opcode numbers are hidden behind a
simple transform so the bytecode cannot be read directly.

## 9. Which commands were needed

Why this section: to explain what `devroom` does without rewriting the whole machine.

This program needed a small set of commands: `0x14` pushes a 32-bit constant onto the stack, `0x6F`
jumps to an absolute bytecode offset, `0x29`, `0x30`, `0x37` and `0x3E` are addition,
multiplication and exclusive or, `0x22` and `0x1B` duplicate and drop a stack item, `0x5A` performs
one step of the already familiar generator over the value on the stack, `0x53`, `0x45` and `0x4C` are
shifts, and `0x7D` emits a finished fragment.

The emit command is arranged like this: byte `0x7D`, then a type byte, then a two-byte length in
little-endian. The interpreter takes the needed number of bytes from the stack and wraps the result
in the way named by the type.

## 10. What the program emits

Why this step: to see that the result is not one string but four pieces.

The `devroom` scenario performs exactly four emissions in 525 instructions:

```
Emit type=1 length=10 bytecode_pc=0x01ce: b'NEUROC1!SOLARCTF{F'
Emit type=2 length=10 bytecode_pc=0x037e: b'NEUROC2!P3f2...'
Emit type=3 length= 9 bytecode_pc=0x053c: b'NEUROC3!i_s3...'
Emit type=4 length= 9 bytecode_pc=0x06e1: b'NEUROC4!n\x00_\x00G\x00a\x00m\x00E\x00E\x00E\x00}\x00'
```

Every piece carries a marker in front, `NEUROC1!` and so on. It is immediately visible that they
cannot be concatenated as they are: the second piece consists of bytes that do not read as text,
the third reads but backwards, and the fourth has zero bytes between the characters.

## 11. Decoding the four fragments

Why this step: to turn four different formats into one string.

The first fragment is the simplest: after the marker there is ordinary text, the beginning of the
flag `SOLARCTF{F`.

The second fragment is XORed with a single byte, and the key is not random: it is the second byte of
the `devroom` seed. It is computed as `(0xD2020289 >> 8) & 0xFF = 0x02`. XORing every byte of the
piece with `0x02` gives a continuation of the flag body.

The third fragment is stored backwards, so it is enough to reverse it, and after that it reads as the
next continuation of the body.

The fourth fragment is text in UTF-16LE, where each character takes two bytes and the low byte comes
first, which is why zeros are visible between the letters. After decoding, the tail of the body and
the closing brace remain, and it ends with `EEE}`.

Then the four parts are glued in marker order, and the flag is ready:

```
SOLARCTF{FR1...EEE}
```

I show the beginning and the end of the string on purpose, so it is visible that the flag was really
obtained.

## 12. What did not work and why

- Brute-forcing the backup password. The header says `none`, there is no encryption, so there is
  nothing to brute-force.
- Searching for the flag with `strings` on `brain.bin`. The scenarios are encrypted, there are no
  readable strings before decryption.
- Trying to treat `weights.bin` as a neural network that has to be run. The weights file exists, but
  the path to the flag does not go through it.
- Fighting the anti-debug and running the APK in an emulator. The strings about Frida and
  `/proc/self/maps` hint at checks, but the offline approach turned out shorter and more reliable.
- Fully decrypting `skin.dat`. The code shows it is a separate layer with dynamic loading, but
  `devroom` does not need it.

## 13. In one paragraph

`neuropet.ab` turned out to be an unencrypted backup that carried a real APK inside. In the
application resources there was an `NRBC` container with two encrypted scenarios, `mood` and
`devroom`, and the native library held both the encryption scheme on a linear congruential
generator and an interpreter for its own bytecode. I decrypted the container with the same generator
using the `2^32` mask, reconstructed the interpreter commands that were needed and ran `devroom`
offline. The program emitted four fragments with markers from `NEUROC1` to `NEUROC4`, which after
XOR, reversal and UTF-16LE decoding glued into the flag.

## 14. Conclusions

- A file format should be read, not guessed: four header lines of the `.ab` removed the password
  question at once.
- Resource names are deceptive: `brain`, `skin` and `weights` look like a neural network, while the
  logic lived in the native library and in a small container.
- The cheap string check on a `.so` saves time: it shows the purpose of the library, the presence of
  anti-debug and the template for the markers.
- A numeric model has to be repeated with the same word size: without the `2^32` mask the
  decryption does not match.
- A virtual machine does not have to be rewritten entirely: it is enough to reconstruct the commands
  that actually occur in the needed scenario.
