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

My script `scripts/01_unpack_android_backup.py` does that. Mine rather than the ready `abe` because
the format is simple and the script also checks the header, refuses an encrypted backup, unpacks the
TAR with a path check, finds `base.apk` inside, unpacks it and prints the sizes of the interesting
entries and the APK hash. The ready `abe` is Java, does only the first part and asks for a password
even where there is none.

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

Why this step: to learn what the library does and what to grab onto before disassembling it.

The command that lists exported functions:

```
$ readelf -Ws libneurocore.so | grep Java_com_solarlabs_neuropet
```

I take it apart, because commands like this appear all the way through. `readelf` reads the headers
of an ELF file, that is of the native library. `-W` turns on the wide output format without wrapping
long lines. `-s` prints the symbol table, that is the list of functions and variables with
addresses. Then `grep` keeps only lines whose name contains `Java_com_solarlabs_neuropet`.

What JNI is. It is the Java Native Interface, the mechanism by which Java code calls code written in
C or C++. The convention is simple: if Java declares a method that has to run natively, the library
must contain a function whose name is built from `Java_`, the package name, the class name and the
method name. So the function name immediately shows where it is called from, and there is no need to
match it by hand in the Java code.

The library has four such functions: `nInit`, `nMoodTick`, `nRun`, `nQuery`. They are methods of the
class `com.solarlabs.neuropet.core.Q`: initialisation, a mood tick, a script run and a value query.

The second pass is the string search:

```
$ strings -a -t x libneurocore.so | grep -Ei 'NEUROC|frida|xposed|maps'
     b60 NEUROC1!NEUROC2!NEUROC3!NEUROC4!xposed
     b87 frida
     b8d gum-js
     b9b substrate
    bac /data/local/tmp/re.frida.server
    bcc /proc/self/maps
   1b60 NEUROC1!I
```

`strings` pulls printable character sequences out of a binary, `-a` scans the whole file rather than
data sections only, and `-t x` prints the offset of every found piece in hexadecimal, so the place
can be looked up in the disassembly later.

What this gives. The line `NEUROC1!NEUROC2!NEUROC3!NEUROC4!` is a template for four markers at once,
so somewhere in the code results will be labelled with those four prefixes. The remaining strings
are names of dynamic analysis tools and the path to the process memory map, so the library contains
anti-debug checks.

An important caveat: the presence of such strings alone does not prove the checks fire. So instead
of booting an emulator and fighting the detection, I took the offline path: read the formats and run
the necessary part myself.

## 5. Finding the generator in native code

Why this step: to learn how the resources are encrypted, because without that they cannot be read.

There was a guess that resources named `brain` and `skin` are encoded somehow, but by what was
unknown. So I dumped the whole disassembly of the
library into a text file and looked for things unusual for plain code: rare constants, references to
resource names, byte loops.

```
$ objdump -d -M intel libneurocore.so > neurocore_x86.asm
```

Around `0x11a0` there was a place where a multiplication by an unusual constant, an addition of a
second constant, a shift right by 16 and an exclusive or with a byte follow each other:

```
$ objdump -d -M intel --start-address=0x11a0 --stop-address=0x11d0 libneurocore.so
    11a0: 69 f5 6d 4e c6 41   imul   esi,ebp,0x41c64e6d
    11a6: 81 c6 39 30 00 00   add    esi,0x3039
    11ac: 69 ee 6d 4e c6 41   imul   ebp,esi,0x41c64e6d
    11b2: c1 ee 10            shr    esi,0x10
    11b5: 41 32 74 0d ff      xor    sil,BYTE PTR [r13+rcx*1-0x1]
    11ba: 40 88 34 08         mov    BYTE PTR [rax+rcx*1],sil
```

Such a sequence means a stream cipher: the number is updated, a byte is taken from it, and that byte
is XORed with a byte of the input data. So the resources can be decrypted by repeating the same
arithmetic in our own code. That is the moment it became clear the resources would give way.

What the generator is. The formula `state = (state * a + c) mod m` is called a linear congruential
generator. The word "congruent" comes from Latin congruens, "commensurate": in arithmetic two numbers
are called congruent modulo something if they give the same remainder when divided by it. The
generator is linear because the formula is linear, and congruential because a remainder is taken at
every step. Here `a` is `0x41C64E6D`, `c` is `0x3039`, and `m` is `2^32`.

Now the word size, and first why it is worth discussing at all. If the arithmetic is repeated in
Python "as is", the next key byte comes out different and the decryption turns into garbage. The
reason is that the processor computes in limited registers, and Python does not.

How it works. The processor has 32-bit registers, that is they hold a number from zero to
`0xFFFFFFFF`. When two 32-bit numbers are multiplied, the exact result needs up to 64 bits, but the
processor keeps only the low 32 bits and simply drops the high ones. That is what overflow means. For
example `0xFFFFFFFF + 1` in 32 bits gives `0x00000000`, because the unit carries out of the register.

In Python integers are unbounded: `0xFFFFFFFF + 1` honestly gives `0x100000000`. As an illustration
with our numbers: `0x41C64E6D * 0x19870419` in 64 bits is `0x68F0E4986125CA5`, while the processor
keeps the low 32 bits `0x86125CA5`. That is why in Python every multiplication and addition carries
the mask `& 0xFFFFFFFF`: it drops everything beyond 32 bits and repeats the processor behaviour.

## 6. The brain.bin container

Why this step: to understand what is encrypted and where the keys live.

The `brain.bin` format was invented by the task authors, so it cannot be "looked up" in
documentation, it is read from the bytes. I look at the start of the file:

```
$ xxd -g 1 -l 48 brain.bin
00000000: 4e 52 42 43 01 00 02 00 04 6d 6f 6f 64 ad a6 db  NRBC.....mood...
00000010: 86 3e 00 00 00 3e 93 13 ad c3 6a 90 0c 0f 92 85  .>...>....j.....
```

How this reads. The first four bytes `4e 52 42 43` are the printable characters `NRBC`, that is the
format signature, as in any container. Then `01 00` is version 1 and `02 00` is the record count 2,
both written low byte first, exactly as in TAP and other formats.

Then the first record follows. The byte `04` is too small for a number but looks like a name length,
and four printable bytes `mood` really follow. Then four bytes `ad a6 db 86` look like nothing
readable, but by meaning they are a key, that is a seed. The next four bytes `3e 00 00 00` give 62,
and after them lie exactly 62 bytes that do not read as text. So the record layout is: name length,
name, seed, size, ciphertext.

Checking the guess is simple: I wrote a parser for this layout and looked at where the pointer ends up
after two records. It stopped exactly at the end of the file, with no byte left over. If the layout
were wrong, there would be either extra bytes or a shortage. A second sign: the bytes after the size
read neither as text nor as numbers, so they are ciphertext rather than data.

The parse gives two records: `mood` with seed `0x86DBA6AD` and length 62 bytes, and `devroom` with
seed `0xD2020289` and length 1766 bytes. `mood` is the ordinary pet behaviour, and `devroom` looks
like a hidden developer room, so I work with that one.

## 7. Removing the encryption

Why this step: to get the bytecode, that is the program that is left to run.

The essence of the task in two words: the container holds not text but a program for an internal
machine, and to reach it the encryption has to be removed with the same generator found in the
library.

What the solver `scripts/02_solve_neurovm.py` does. It opens the APK, reads `brain.bin` from it,
parses the container by the layout from section 6, decrypts both records and saves them. The
decryption is the very loop from the machine code:

```python
state = seed
plain = bytearray()
for x in encrypted:
    state = (state * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
    plain.append(x ^ ((state >> 16) & 0xFF))
```

Run on our build:

```
$ python3 scripts/02_solve_neurovm.py work/base.apk --out solved
NRBC[0]: name=mood seed=0x86dba6ad length=62
NRBC[1]: name=devroom seed=0xd2020289 length=1766
```

The decrypted `devroom` starts with the bytes
`6f 14 00 00 00 14 8c 2d 46 a3 5a 53 05 14 63 93`. That is not text and not an executable file but
a sequence of instructions for an internal machine. This is exactly why `strings` on `brain.bin`
found nothing: before decryption there are no readable strings there.

## 8. What the virtual machine is and how it was found

Why this step: to understand the rules the bytecode follows, otherwise it stays a set of numbers.

A virtual machine is a program that imitates a processor: it has its own instruction set, its own
stack and its own instruction counter. Bytecode for such a machine cannot run on a real processor, it
is executed by an interpreter.

The natural place to look for the interpreter is the entry point Java uses, that is `nRun`. In it the
preparation and the call are visible:

```
$ objdump -d -M intel --start-address=0x21f0 --stop-address=0x2210 libneurocore.so
    21fc: b9 80 f0 fa 02   mov    ecx,0x2faf080
    2201: e8 fa f0 ff ff   call   0x1300
```

Why this looks like an interpreter. First, the call goes to `0x1300`, and inside it there is a large
block of comparisons followed by a jump table, and a jump table is the usual way to lay out command
handlers. Second, before the call a number is put into a register, `0x2faf080`: that looks like a
step limit so the program cannot spin forever. Inside the loop that number is compared with the
instruction counter.

How a command is selected. The interpreter reads a byte at the counter, but transforms it
arithmetically before choosing a handler:

```
$ objdump -d -M intel --start-address=0x1408 --stop-address=0x1420 libneurocore.so
    140f: 6b c0 b7         imul   eax,eax,0xffffffb7
    1412: 0f b6 c0         movzx  eax,al
    1415: 83 c0 b5         add    eax,0xffffffb5
    1418: 83 f8 11         cmp    eax,0x11
```

So the byte is multiplied by `-0x49`, `-0x4B` is added to the result, and only then the number is
compared with `0x11`. The purpose of the transform is to make command numbers in the bytecode differ
from handler numbers, so the code cannot be read by eye.

Where the handler count comes from. The comparison with `0x11` means everything above `0x11` goes to
error handling. Exactly for those values a jump table lies in the code, and it holds eighteen
addresses, that is eighteen variants from zero to seventeen. One of them leads to the common exit
from the function, one to the error, and the remaining sixteen are command handlers.

## 9. Which commands were needed

Why this section: without understanding the commands the bytecode cannot be run, and rewriting the
whole machine is unnecessary.

We did not need to take apart every command, only to run one specific scenario. So I took only the
commands that actually occur in it and worked out for each what it does with the stack, that is with
the working list of values.

Here is what was needed. `0x14` pushes a 32-bit constant onto the stack, and that is the main way to
pass data. `0x6F` jumps to an absolute offset, which gives loops and branches. `0x29`, `0x30`, `0x37`
and `0x3E` are addition, multiplication and exclusive or. `0x22` and `0x1B` duplicate and drop a
stack value, without them an expression cannot be built. `0x5A` performs one step of the familiar
generator over the value on the stack, so the program mixes itself into the key. `0x53`, `0x45` and
`0x4C` are shifts. And `0x7D` emits a finished fragment.

## 10. What the program emits

Why this step: to see the result of the run, which is what all the work was for.

When our interpreter reaches the command `0x7D`, it takes the needed number of bytes from the stack
and puts the result into an output list. This is not printing to the screen but an internal
emission: the scenario builds pieces of a string and hands them out. My solver prints every such
emission and also saves it into a `fragment*_encoded.bin` file, so the result can be checked
separately.

On `devroom` the emission happens four times:

```
Emit type=1 length=10 bytecode_pc=0x01ce: b'NEUROC1!SOLARCTF{F'
Emit type=2 length=10 bytecode_pc=0x037e: b'NEUROC2!P3f2...'
Emit type=3 length= 9 bytecode_pc=0x053c: b'NEUROC3!i_s3...'
Emit type=4 length= 9 bytecode_pc=0x06e1: b'NEUROC4!n\x00_\x00G\x00a\x00m\x00E\x00E\x00E\x00}\x00'
```

Every emission carries a marker in front, `NEUROC1!` and so on, exactly as the string from the
library promised. It is immediately visible that the pieces cannot be concatenated as they are: the
second does not read as text, the third reads but backwards, and in the fourth zero bytes stand
between the letters.

## 11. Decoding the four fragments

Why this step: to turn four different formats into one string.

The first fragment is the simplest: after the marker there is ordinary text, the beginning of the
flag `SOLARCTF{F`.

The second fragment does not read. The only parameter nearby that could be a key is the record seed.
I assumed the key is one byte of the seed and checked: the second byte of the seed is `0x02`, and
XORing every byte of the piece with it gives readable text. The visible check is simple: after the
transform every byte falls into printable characters and forms a meaningful continuation of the
flag.

The third fragment reads, but the words look reversed: the piece starts with a letter that by
meaning should stand at the end. So I reversed the byte order and the text fell into place.

The fourth fragment looks like text with zeros between the characters. That is the sign of a
two-byte encoding: in UTF-16LE every character takes two bytes, and for Latin letters the high byte
is zero. I checked it by decoding, and a readable tail of the flag came out together with the closing
brace.

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
