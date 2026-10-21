# Ventilator303: the path to the solution

## 0. The whole plan

Why this section: so the reader sees the chain down to the details and understands why every next
step is needed.

Given: an audio recording `ventilator303.wav` of 168 seconds and a task text about a Balkan station
that broadcast at night and passed something useful for home computers. The date in the text is
19 April 1987.

Goal: find the flag.

The plan was this:

1. Understand the hint about the station, because it decides what to look for in the sound.
2. Look at the spectrum and find the carriers instead of trying to recognise speech.
3. Turn the sound back into bytes and get a file for the Spectrum.
4. Check the structure of that file and its checksums.
5. Extract the Z80 machine code and find where the string is assembled.
6. Reconstruct the generator and the permutation, then decrypt the text.

## 1. What is given

Why this step: to pin down the recording parameters and its hash, so every later number belongs to
a verified file.

I check the type and the parameters:

```
$ file ventilator303.wav
ventilator303.wav: RIFF (little-endian) data, WAVE audio, Microsoft PCM, 16 bit, mono 44100 Hz
$ python3 -c "import wave; w=wave.open('ventilator303.wav'); n=w.getnframes(); r=w.getframerate(); print(w.getnchannels(), w.getsampwidth(), r, n, round(n/r, 4))"
1 2 44100 7408810 168.0002
$ sha256sum ventilator303.wav
65c389277d5c21bb0dba559147884053f124756e2c8e35c8c5909bf4a70a05c5
```

So this is uncompressed PCM, one channel, 16 bits per sample, 44 100 samples per second, 7 408 810
samples and 168.0002 seconds of recording. The work needs Python 3 with NumPy, `sox` for spectrograms, `xxd` to look at bytes and
`z80dasm` to disassemble. No Spectrum emulator is required.

## 2. The hint about the station

Why this step: to understand what is actually recorded, otherwise there is no way to know whether
to look for speech or for a data signal.

I googled the name `ventilator303.wav` and the text about a Balkan station. The search leads to
Ventilator 202, hosted by Zoran Modli on Radio Beograd 202: in the eighties programs for home
computers really were broadcast, listeners taped them, and among those programs there were games
and even a flight simulator. That explains the wording about the flight simulator and the night
broadcast.

Hence a working hypothesis: the recording carries a digital signal with a program, not speech. I
write the date 19 April 1987 down separately as a possible key rather than as decoration. It did
come in useful later.

## 3. Spectrum: looking for the carriers

Why this step: to find the part of the recording with stable tones, because bits are recovered
from them.

A spectrogram of a short fragment is easy to build with `sox`:

```
$ sox ventilator303.wav -n trim 15 0.25 spectrogram -x 1200 -y 512 -z 80 -o fsk_detail.png
```

The ready image is in `assets/fsk_detail.png`. On my own system I did not install `sox`, so I
checked the frequencies directly from the samples: I took 0.25-second windows in the digital part
and computed the spectrum. The peaks fall at 1200 and 2400 Hz, with side components at 1196 and
2404 Hz from the finite window.

The signal model agrees with that: at 44 100 samples per second a period of 36.75 samples is
1200 Hz, and a period of 18.375 samples is 2400 Hz.

That is a tone spacing equal to the bit rate: 1200 and 2400 Hz at 1200 baud. So this is not the
pulse recording from the Spectrum ROM but AFSK, frequency shift between two tones, with
asynchronous 8N1 frames. One byte takes ten bits: a start zero, eight data bits least significant
first, and a stop one.

## 4. Decoding the sound into bytes

Why this step: to get a file that can be read as a structure instead of noise.

I did not use ready modem decoders: the frequencies and the structure here are specific, so it was
simpler to state the rules explicitly. The working approach is this:

1. Read the samples as `int16` with the standard `wave` module.
2. Find samples where the signal crosses from negative to non-negative.
3. Use the intervals between those crossings to tell 1200 Hz from 2400 Hz.
4. Sample the recovered bits in the middle of an interval of `44100/1200 = 36.75` samples.
5. Assemble frames of the form start zero, eight bits least significant first, stop one.
6. Synchronise on a preamble of repeated `0x55` bytes.

Why `0x55`: it is alternating zeros and ones `01010101`, which makes bit boundaries easy to catch.
The implementation is in `scripts/01_wav_to_tap.py`:

```
$ python3 scripts/01_wav_to_tap.py ventilator303.wav --out recovered.tap
sample_rate: 44100 modulation: 1200/2400 Hz AFSK, 1200-baud 8N1
sync at 14.991542s; embedded CRC verifies exact TAP bytes
VENTILATOR-303 TAPE v1
NAME: GALAKSIJA-DEMO
FORMAT: ZXTAP
LEN: 16659
CSUM: 88D9

recovered: recovered.tap 16659 bytes; CRC-16/CCITT-FALSE=88D9
```

Three things matter here. First, synchronisation was found at 14.991542 seconds, before that there
is speech and signalling. Second, the signal carries a text header, not just bytes, and it states
`FORMAT: ZXTAP`, that is a ZX Spectrum TAP. Third, the header holds a checksum, `CSUM`, and it is
CRC-16/CCITT-FALSE over the whole TAP with polynomial `0x1021` and initial value `0xFFFF`. I
computed it separately over the produced file and got exactly `0x88D9`.

Checking the result against the attached TAP:

```
$ sha256sum recovered.tap
033e4ea05b182ba025468a8611c5bac7337a6388ab28b93037b4497977b45655
$ cmp recovered.tap ventilator303.tap
```

`cmp` prints nothing, so the files are byte-identical. The decoder restores the original TAP
without a single difference, so I work with those bytes below.

## 5. TAP structure and checks

Why this step: to confirm this is a real Spectrum file and to locate the code inside it.

The TAP format is simple: a two-byte length in little-endian, then a block of that length, and so
on to the end. Inside a block the first byte is the type (`0x00` for a header, `0xFF` for data) and
the last one is the XOR of all previous bytes of the block. The check is this: XOR the whole block
and the result must be zero.

The file splits into four blocks: offset `0x0000`, length 19 bytes, type `0x00`, a BASIC header.
Offset `0x0015`, length 163 bytes, type `0xFF`, BASIC data. Offset `0x00BA`, length 19 bytes, type
`0x00`, a code header. Offset `0x00CF`, length 16 450 bytes, type `0xFF`, code data. The XOR of
each of the four blocks is zero.

The headers say the program is called `GALAKSIJA`, the BASIC part is 161 bytes with autostart at
line 10, and the machine block is 16 448 bytes loaded at `0x8000`. The `file` utility confirms the
format independently:

```
$ file recovered.tap
recovered.tap: Spectrum .TAP data "GALAKSIJA " - BASIC program, autostart line 10
```

The word `GALAKSIJA` in the name is not proof of the computer format: the name was chosen by the
task authors, while the container format is confirmed by the `FORMAT: ZXTAP` header and valid TAP
blocks.

## 6. Extracting the Z80 code

Why this step: to move from the container to the program that assembles the string.

From the last block I drop the leading `0xFF` type byte and the trailing XOR byte, and 16 448 bytes
of code for loading at `0x8000` remain. In the script this is
`scripts/02_tap_to_flag.py` with the `--dump-code` key. The start of the file:

```
$ xxd -g 1 -l 32 z80_program.bin
00000000: cd 8c 80 21 0c 83 22 7b 5c cd 03 81 cd 54 81 cd
```

`CD nn nn` is `CALL`, `21 nn nn` is `LD HL,nn`, `22 nn nn` is `LD (nn),HL`, so this really is code.
I disassemble with the right base:

```
$ z80dasm -a -l -t -g 0x8000 -o z80_program.asm z80_program.bin
$ strings -a -n 6 z80_program.bin | head
VENTILATOR-303
GALAKSIJA DEMO
YOU TAPED US RIGHT
```

There is no plain flag in the strings, and that is expected. A second observation: 15 378 bytes out
of 16 448 are filled with `0x5A`, that is padding. So the interesting part is at the beginning,
not across the whole block.

A tool limitation: `z80dasm` does not know where code ends and data begins, and may decode tables
as instructions. So I look at the specific places the loop leads to, and do not trust the whole
listing as linear code.

## 7. Finding the decryption loop and the date

Why this step: to see which arrays the program reads and where the key comes from.

The file offset is the Z80 address minus `0x8000`. At the start of the loop, address `0x82A2`,
the bytes `21 D4 83`, `11 C4 83`, `01 04 00`, `ED B0` are `LD HL,0x83D4`, `LD DE,0x83C4`,
`LD BC,4` and `LDIR`, that is copying four bytes from `0x83D4` into the working state at `0x83C4`.
Then `AF` and `32 C2 83` zero the counter at `0x83C2`.

The exit condition is written out directly:

```
$ xxd -g 1 -s 0x2b1 -l 6 z80_program.bin
000002b1: 3a c2 83 fe 2b c8
```

That is `LD A,(0x83C2)`, `CP 0x2B` and `RET Z`: the loop runs over 43 characters and stops when the
counter reaches `0x2B`. Then `CD 02 82` calls the generator routine at `0x8202`, after which a
ciphertext byte comes from the table at `0x8403` and a position comes from the table at `0x83D8`.
The XOR is done by two instructions, `21 C6 83` and `AE`: a ciphertext byte is combined with the
byte at `0x83C6`.

Now the data. The first four bytes at `0x83D4` are `19 04 87 19`, that is little-endian
`0x19870419`. Compare that with the date from the task: 19.04.1987. The match is exact, so the date
works as the initial state of the generator, and it has to be written as `0x19870419`, not as the
decimal `19870419`.

## 8. Reconstructing the generator

Why this step: without the state update there is no way to get the key bytes.

The routine at `0x8202` does a multiplication and an addition over the 32-bit state. First it
zeroes four bytes of the product at `0x83C8`:

```
$ xxd -g 1 -s 0x203 -l 32 z80_program.bin
00000203: af 21 c8 83 77 23 77 23 77 23 77 21 cc 83 36 6d
00000213: 23 36 4e 23 36 c6 23 36 41 21 c4 83 11 d0 83 01
```

Next come `36 6D`, `23`, `36 4E`, `23`, `36 C6`, `23`, `36 41`: that is writing the four bytes
`6D 4E C6 41`, the multiplier `0x41C64E6D` in little-endian, at `0x83CC`. Then the state is copied
to `0x83D0` and a counter starts: `06 20` at `0x8226` is 32 iterations of shift and add, ordinary
long multiplication in code.

The added constant comes at the end and is visible in the instructions:

```
$ xxd -g 1 -s 0x279 -l 20 z80_program.bin
00000279: c6 39 32 c8 83 3a c9 83 ce 30 32 c9 83 3a ca 83
```

`C6 39` is `ADD A,0x39`, then `CE 30` is `ADC A,0x30`, and two `CE 00` carry the carry into the
higher bytes. Together that adds `0x3039` to the 32-bit result, after which the result is copied
back to `0x83C4` by the `LDIR` at `0x829F`.

So this is a plain linear congruential generator:

```python
state = (state * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
```

I verify it numerically: `0x19870419 * 0x41C64E6D + 0x3039` modulo `2^32` gives `0x86128CDE`, and
that is exactly the first state the solver shows. The formula is read correctly.

## 9. Permutation and decryption

Why this step: to assemble the string in the right order, otherwise the same letters come out
shuffled.

The key byte is not the low byte of the state but bits 16..23, that is the byte at `0x83C6`, the
third byte of the 32-bit word at `0x83C4`. The second array is the 43-byte table at `0x83D8`. I
checked that it is a permutation: sorted, its values are exactly `0..42` with no repeats. So the
program does not print characters in sequence, it places them at positions.

Hence the assembly rule: a decrypted byte goes not to the current index but to the position from
the table.

```python
result[permutation[i]] = ciphertext[i] ^ key_byte
```

The first five control steps, computed from the program bytes:

```
i=0 state=86128CDE key=12 cipher=3F plain='-' position=29
i=1 state=0E85CEBF key=85 cipher=B5 plain='0' position=12
i=2 state=5AB1698C key=B1 cipher=F6 plain='G' position=20
i=3 state=D9FAC8D5 key=FA cipher=CA plain='0' position=37
i=4 state=A2B698EA key=B6 cipher=F7 plain='A' position=3
```

The full run is done by the second script:

```
$ python3 scripts/02_tap_to_flag.py recovered.tap --dump-code z80_program.bin
TAP: 16659 bytes; 4 blocks; XOR checks: OK
BASIC name: GALAKSIJA
BASIC auto line: 10
CODE name: GALAKSIJA
CODE length: 16448 load addr: 0x8000
Z80 CODE extracted: z80_program.bin 16448 bytes
Z80 LCG seed @ 0x83D4: 0x19870419
permutation @ 0x83D8: [29, 12, 20, 37, 3, 15, 42, 19, ...]
ciphertext @ 0x8403: 3fb5f6caf766ac81...46ec3a
FLAG: SOLARCTF{YuG...80Z}
```

## 10. The flag

Why this step: to fix the result and the part that is easy to spoil.

The flag is assembled from 43 characters:

```
SOLARCTF{YuG...80Z}
```

I show the beginning and the end of the string on purpose, so it is visible that the flag was
really obtained.

Inside the body some characters are digits, not letters: a zero instead of the letter `O` and a
one instead of `I` occur in several places. Rewriting them as letters breaks the string, because the
check compares exact characters.

## 11. What did not work and why

- Speech recognition. There is speech at the start and at the end of the recording, but the data
  lives in the digital part, and speech-to-text over noise gives nothing.
- Searching for the flag with `strings`. Only the title and demo messages are visible in the code,
  while the 43 characters sit behind XOR and a permutation.
- The guess that `GALAKSIJA` means the Galaksija computer format. The name appears in the metadata
  and in the TAP header, but the container is a ZX Spectrum TAP and Z80 code runs inside.
- Full Spectrum emulation and reading all 16 KB. Most of the block is the byte `0x5A`, and the whole
  logic sits in a short loop, its tables and the date.
- Taking the low byte of the state as the key. The code uses the byte at `0x83C6`, that is bits
  16..23, and with the low byte the text does not come together.

## 12. In one paragraph

The recording turned out to be not a radio broadcast but a digital transmission of a program: a
1200-baud AFSK signal with tones at 1200 and 2400 Hz, 8N1 frames, synchronised on `0x55` at 14.99
seconds. The decoder restored a ZX Spectrum TAP of 16 659 bytes that matched the attached file
byte for byte, and the CRC carried inside the signal matched the one computed separately. The TAP
has four blocks: a BASIC loader with autostart at line 10 and 16 448 bytes of machine code at
`0x8000`. In that code I found a loop over 43 characters that takes the initial state `0x19870419`
from the task date, runs a generator with multiplier `0x41C64E6D` and increment `0x3039`, XORs the
ciphertext with the state byte from bits 16..23 and places the result according to the permutation
table.

## 13. Conclusions

- A hint in the task text can be a technical instruction: the story about broadcasting programs over
  radio immediately ruled out speech recognition.
- The format should be decided by the data, not by the file name: `ventilator303` points at a
  historical broadcast, while inside there is an ordinary ZX TAP.
- Checksums are a cheap verification along the whole path: the CRC in the signal header, the XOR
  checks in the TAP blocks and the byte comparison with the original file confirmed the decoder
  three times.
- Reconstructed math has to be verified numerically: the matching first state after the multiply
  and the addition confirmed both the multiplier and the increment.
- Character order is part of the algorithm: without the permutation table the same bytes give a
  jumble.
