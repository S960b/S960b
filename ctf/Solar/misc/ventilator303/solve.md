# Ventilator303: the path to the solution

## 0. The whole plan

Why this section: so the reader sees the chain down to the details and understands why every next
step is needed.

Given: an audio recording `ventilator303.wav` of 168 seconds and a task text about a Balkan station
that broadcast at night and passed something useful for home computers. The date in the text is
19 April 1987.

Goal: find the flag.

The plan came out like this:

1. Listen to the file and understand what it sounds like.
2. Check the hint about the station to know what to look for.
3. Build a spectrogram and find the signal frequencies.
4. Work out how bytes are encoded in the signal and write a decoder.
5. Verify the recovered file by its own structure and checksums.
6. Extract the machine code and find where the string is assembled.
7. Reconstruct the algorithm, decrypt the text and assemble the flag.

## 1. What is given and the first listen

Why this step: to pin down the file parameters and to understand what work is ahead.

The first thing I did was simply open the recording and listen to it end to end, without any tools.
At the start and at the end there is speech and short signals, and in the middle a long stretch of
a very characteristic squeal. I recognised that sound immediately: it is how a ZX Spectrum loading
tape sounded, and I had one in my childhood. A tape with a game passed the program into the computer
in exactly such tones, and a recording error meant the game would not load.

Hence the first and most useful guess: the recording carries a digital transmission of a program,
not speech, and it can be turned back into a file. I worked with that hypothesis from then on.

I check the file parameters with tools:

```
$ file ventilator303.wav
ventilator303.wav: RIFF (little-endian) data, WAVE audio, Microsoft PCM, 16 bit, mono 44100 Hz
$ python3 -c "import wave; w=wave.open('ventilator303.wav'); n=w.getnframes(); r=w.getframerate(); print(w.getnchannels(), w.getsampwidth(), r, n, round(n/r, 4))"
1 2 44100 7408810 168.0002
$ sha256sum ventilator303.wav
65c389277d5c21bb0dba559147884053f124756e2c8e35c8c5909bf4a70a05c5
```

What this means. `PCM` is a way to store sound without compression: numbers follow one another, and
each describes the position of the membrane at a moment in time. A sample rate of 44 100 means
there are 44 100 such numbers per second, `16 bit` means each number takes 16 bits, and `mono` means
one channel. There are 7 408 810 samples in total, that is 168.0002 seconds. I write the `sha256`
hash down so every later number belongs to exactly this file.

The work needs Python 3 with NumPy (the decoder uses it), `sox` for the spectrogram and `xxd` to
look at bytes. No Spectrum emulator is required.

## 2. The hint about the station

Why this step: to understand which broadcast the task text mentions and not to look for the wrong
thing.

I googled the name `ventilator303.wav` and the text about a Balkan station. The search leads to
Ventilator 202, hosted by Zoran Modli on Radio Beograd 202: in the eighties programs for home
computers really were broadcast, listeners taped them, and among those programs there were games
and even a flight simulator. That explains the wording about the flight simulator and the night
broadcast.

The date 19 April 1987 looks too precise to be decoration, so I wrote it down separately as a
possible key. It did come in useful later.

## 3. Spectrogram: looking for the frequencies

Why this step: to see which frequencies the signal consists of, because that decides how to decode
it.

A spectrogram is an image where the horizontal axis is time, the vertical axis is frequency, and
brightness shows loudness at that frequency. Two steady tones look like two horizontal bands, and
the frequencies are visible at once.

I googled how to build such an image, and the first answer was the `sox` utility: it has a ready
`spectrogram` mode. I install it and build the image with one command:

```
$ sox ventilator303.wav -n trim 15 0.25 spectrogram -x 1200 -y 512 -z 80 -o fsk_detail.png
```

What the parameters mean. `ventilator303.wav` is the input file, `-n` means the sound is not written
anywhere, it is only needed for the computation. `trim 15 0.25` takes a fragment that starts at
second 15 and lasts 0.25 seconds, that is the digital part and not speech. `spectrogram` is the
mode that builds the image. `-x 1200` sets the image width in pixels, `-y 512` the height, `-z 80`
the dynamic range in decibels, that is how quiet a detail still gets drawn. `-o fsk_detail.png` is
the output file name.

How `sox` computes it. The fragment is cut into short windows, and a Fourier transform is computed
for every window. A Fourier transform is a way to decompose a piece of signal into frequencies: it
takes a set of samples and returns how much energy falls on each frequency. Windows are needed
because the signal changes over time: computing the spectrum of the whole recording at once gives
an averaged picture, while a short window shows what sounds at that moment. The windows then follow
one another, and the set of spectra is assembled into the image.

The ready image is in `assets/fsk_detail.png`. In the digital part two horizontal bands are visible:
the lower one near 1.2 kHz and the upper one near 2.4 kHz. Below and above them there is nothing
stable, so the signal consists of two tones, not of noise and not of speech.

The arithmetic matters next. At 44 100 samples per second one period of a 1200 Hz tone occupies
`44100 / 1200 = 36.75` samples, and one period of a 2400 Hz tone occupies `44100 / 2400 = 18.375`
samples. So the distance between neighbouring zero crossings of the signal tells which tone sounds
at the moment. The exact values 1200 and 2400 Hz were later confirmed by the fact that a decoder
with this model restored the file byte for byte, but that is the next section.

## 4. What AFSK is and how to get bytes out of it

Why this step: to turn sound into a sequence of bits, and then into bytes.

Two tones for two bit values is `AFSK`, amplitude frequency shift keying, frequency shift keying.
Simply put, the transmitter and the receiver agree that one tone means zero and the other means one.
Here the lower tone of 1200 Hz means zero, the upper tone of 2400 Hz means one, and the speed is
1200 baud, that is 1200 bits per second.

I googled how such a signal is received. The general principle of AFSK demodulation is this: find
the moments where the signal crosses zero, measure the distance between crossings, derive the
frequency from it and turn that into a bit.

Next I looked for a ready decoder for such parameters and did not find one: ordinary modem programs
expect other frequencies and another data format. So I built my own from that principle, it takes
twenty lines and the whole behaviour is visible.

The second layer is `UART`, a way to pack bits into bytes. Every byte is sent as an `8N1` frame: one
start bit at zero level, eight data bits with the least significant first, and one stop bit at one
level. Ten bits per byte in total. `8N1` reads as eight data bits, no parity, one stop bit.

Hence the working approach, which I assembled from that description:

1. Read the samples as 16-bit integers.
2. Find samples where the signal crosses from negative to non-negative.
3. Use the intervals between crossings to tell 1200 Hz from 2400 Hz.
4. Read the bit in the middle of an interval, not at the edge, to avoid distortion.
5. Assemble frames of the form start zero, eight bits, stop one.
6. Find the start of the transmission by a preamble of repeated `0x55` bytes.

Why `0x55` is handy: in binary it is `01010101`, alternating zeros and ones, which makes bit
boundaries easy to catch. The implementation is in `scripts/01_wav_to_tap.py`:

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

What matters here. Synchronisation was found at 14.991542 seconds, before that there is speech and
signalling. The signal carries a text header, and it states `FORMAT: ZXTAP`, that is a ZX Spectrum
TAP. Besides, the header has a `CSUM` field, a checksum. It is `CRC-16`, a 16-bit checksum computed
by the `CCITT-FALSE` algorithm: polynomial `0x1021`, initial value `0xFFFF`, no bit reflection. The
point of such a sum is that any error in the bytes almost certainly breaks it, so it verifies
integrity.

I computed that CRC separately, with my own code over the produced file, and got exactly `0x88D9`.
Then I compared the result with the attached file:

```
$ sha256sum recovered.tap
033e4ea05b182ba025468a8611c5bac7337a6388ab28b93037b4497977b45655
$ cmp recovered.tap ventilator303.tap
```

`cmp` prints nothing, so the files are byte-identical. The decoder restored the original TAP with
not a single difference, and the bytes below can be trusted.

## 5. TAP format and block checks

Why this step: to confirm this is a real Spectrum file and to understand where the code sits.

I found the TAP format description by searching, it is on the Sinclair Wiki. The format is simple:
two length bytes of a block in little-endian order (low byte first), then the block itself, and so
on to the end of the file. Inside a block the first byte is the type: `0x00` for a header, `0xFF`
for data. The last byte of the block is a checksum: the result of the running exclusive or (XOR) of
all previous bytes of the block. The check is simple: XOR the whole block including that last byte,
and the result must be zero.

My script `scripts/02_tap_to_flag.py` parses the file, reads the blocks and verifies the sums. The
file yields four blocks. The first at offset `0x0000`, length 19 bytes, type `0x00`, a BASIC header.
The second at offset `0x0015`, length 163 bytes, type `0xFF`, the BASIC program itself. The third at
offset `0x00BA`, length 19 bytes, type `0x00`, a code header. The fourth at offset `0x00CF`, length
16 450 bytes, type `0xFF`, the machine code. The XOR of each of the four blocks is zero, so all
blocks are intact.

The headers say the program is called `GALAKSIJA`, the BASIC part is 161 bytes with autostart at
line 10, and the machine block is 16 448 bytes loaded at `0x8000`. The `file` utility confirms the
format independently:

```
$ file recovered.tap
recovered.tap: Spectrum .TAP data "GALAKSIJA " - BASIC program, autostart line 10
```

The word `GALAKSIJA` in the name is not proof of the computer format: the name was chosen by the
task authors, while the format is confirmed by the TAP header fields and valid blocks.

## 6. Extracting the machine code

Why this step: to move from the container to the program that assembles the string.

The machine block is arranged like this: the first byte `0xFF` is the block type, the last byte is
the XOR checksum, and between them lies the payload. The header of this block states a length of
16 448 bytes, and the arithmetic agrees: 1 type byte plus 16 448 data bytes plus 1 checksum byte
gives exactly the 16 450 bytes of the block length. So I cut off the first and the last byte and get
exactly the code the Spectrum would load into memory.

The load address `0x8000` means the first code byte lands in memory at `0x8000`, the second at
`0x8001` and so on. Hence the relation between a command address and its place in the file:
`file offset = Z80 address − 0x8000`. All the `xxd -s` numbers below come from this formula.

The script saves the code:

```
$ python3 scripts/02_tap_to_flag.py recovered.tap --dump-code z80_program.bin
Z80 CODE extracted: z80_program.bin 16448 bytes
$ xxd -g 1 -l 32 z80_program.bin
00000000: cd 8c 80 21 0c 83 22 7b 5c cd 03 81 cd 54 81 cd
```

How do we know this is code and not text? `CD nn nn` is the `CALL` command with a target address,
`21 nn nn` is `LD HL,nn`, `22 nn nn` is `LD (nn),HL`. I checked the command encoding against a Z80
instruction table, the kind found in any Z80 reference such as z80.info. `LD HL,nn` means "load the
number nn into register HL": HL is a 16-bit register, that is a cell inside the processor where a
number can be stored and used later.

To read the code it is convenient to use a disassembler:

```
$ z80dasm -a -l -t -g 0x8000 -o z80_program.asm z80_program.bin
```

The keys mean the following. `-g 0x8000` sets the base address, that is the address where the first
file byte lies; without it the disassembler assumes `0x0100` and every jump becomes meaningless.
`-a` prints instruction addresses, `-l` marks jump labels, `-t` shows the source bytes next to each
command. I did not invent the base `0x8000`: that address is written in the code block header of the
TAP.

The tool limitation has to be kept in mind: `z80dasm` does not know where code ends and data begins
and may decode a data table as instructions. So I look at the specific places the logic leads to,
and do not read the listing as linear text.

Useful strings and the contents of the block:

```
$ strings -a -n 6 z80_program.bin | head
VENTILATOR-303
GALAKSIJA DEMO
YOU TAPED US RIGHT
```

There is no plain flag in the strings. A second observation: 15 378 bytes out of 16 448 are filled
with the byte `0x5A`, the code of the letter `Z`. Such uniform padding means the interesting part of
the program sits at the beginning of the block, not spread across it.

## 7. Finding the decryption loop and the date

Why this step: to see which tables the program reads and where the key comes from.

Next I walked the listing from the entry point and looked for a comparison against a small number,
because printing a string is usually bounded by a counter. I found the place where the program reads
a byte at `0x83C2`, compares it with `0x2B` and exits when they are equal. In bytes it looks like
this:

```
$ xxd -g 1 -s 0x2b1 -l 6 z80_program.bin
000002b1: 3a c2 83 fe 2b c8
```

I decode it byte by byte, because each matters. `3A C2 83` is `LD A,(0x83C2)`: load into register A
the byte from memory at `0x83C2`, where the counter of processed characters lives. `FE 2B` is
`CP 0x2B`: compare A with the number `0x2B`, that is 43. `C8` is `RET Z`: return from the routine
if the previous comparison found equality. Together this is the loop exit condition: once 43
characters are processed, leave.

Why this find matters: the flag length is stated in the code itself, and it is 43 characters. Now
the search is for exactly 43 ciphertext bytes and a table of the same length.

I look at the data around it. The offset is computed by the formula from section 6, that is the
address minus `0x8000`.

```
$ xxd -g 1 -s 0x2a2 -l 16 z80_program.bin
000002a2: 21 d4 83 11 c4 83 01 04 00 ed b0 af 32 c2 83
$ xxd -g 1 -s 0x3d4 -l 4 z80_program.bin
000003d4: 19 04 87 19
$ xxd -g 1 -s 0x3d8 -l 43 z80_program.bin
```

The first line is the start of the loop: `LD HL,0x83D4`, `LD DE,0x83C4`, `LD BC,4`, `LDIR`. `LDIR`
copies BC bytes from the address in HL to the address in DE, so here four bytes of the initial state
move from `0x83D4` into the working area `0x83C4`. Then `XOR A` and `LD (0x83C2),A` zero the
counter.

The four bytes at `0x83D4` are `19 04 87 19`. They are read low byte first, so the number is
`0x19870419`. Compare that with the date from the task: 19.04.1987. The match is exact, so the date
works as the initial state of the generator, and it must be written as `0x19870419`, not as the
decimal `19870419`.

The table at `0x83D8` holds 43 bytes and is a permutation: sorted, its values are exactly the
numbers 0 to 42 with no repeats. A permutation is a rule that places a character not at its own
position but at the position it names. That is why plain XOR without the table gives a jumble.

## 8. Reconstructing the generator line by line

Why this step: without the state update there are no key bytes, and without the key not a single
character decrypts.

The generator routine starts at `0x8202`. It is small, so I give it in full with a comment on every
line. The left column is the address, then the bytes, then the command and the explanation.

```
8202  af            xor  a             ; A = 0, clear the accumulator
8203  21 c8 83      ld   hl,0x83C8     ; HL points at the low byte of the product
8206  77            ld   (hl),a        ; zero 0x83C8
8207  23            inc  hl            ; next byte
8208  77            ld   (hl),a        ; zero 0x83C9
8209  23            inc  hl
820a  77            ld   (hl),a        ; zero 0x83CA
820b  23            inc  hl
820c  77            ld   (hl),a        ; zero 0x83CB
820d  21 cc 83      ld   hl,0x83CC     ; HL points at the multiplier
8210  36 6d         ld   (hl),0x6D     ; low byte of the multiplier
8212  23            inc  hl
8213  36 4e         ld   (hl),0x4E     ; second byte
8215  23            inc  hl
8216  36 c6         ld   (hl),0xC6     ; third byte
8218  23            inc  hl
8219  36 41         ld   (hl),0x41     ; high byte
821b  21 c4 83      ld   hl,0x83C4     ; HL on the state
821e  11 d0 83      ld   de,0x83D0     ; DE on the working copy
8221  01 04 00      ld   bc,4          ; copy 4 bytes
8224  ed b0         ldir               ; state moved to 0x83D0
8226  06 20         ld   b,0x20        ; loop counter: 32 iterations
8228  af            xor  a             ; A = 0, the shifted-out bit lands here
8229  21 d3 83      ld   hl,0x83D3     ; HL on the high byte of the copy
822c  cb 1e         rr   (hl)          ; shift the byte right through carry
822e  2b            dec  hl            ; previous byte
822f  cb 1e         rr   (hl)          ; and that one too
8231  2b            dec  hl
8232  cb 1e         rr   (hl)
8234  2b            dec  hl
8235  cb 1e         rr   (hl)          ; carry now holds the lowest state bit
8237  30 2c         jr   nc,0x8265     ; if the bit was 0, skip the addition
8239  3a cc 83      ld   a,(0x83CC)    ; low byte of the multiplier
823c  5f            ld   e,a           ; E = it
823d  3a c8 83      ld   a,(0x83C8)    ; low byte of the product
8240  83            add  a,e           ; add the multiplier
8241  32 c8 83      ld   (0x83C8),a    ; store it back
8244  3a cd 83      ld   a,(0x83CD)    ; second byte of the multiplier
8247  5f            ld   e,a
8248  3a c9 83      ld   a,(0x83C9)    ; second byte of the product
824b  8b            adc  a,e           ; add with carry
824c  32 c9 83      ld   (0x83C9),a
824f  3a ce 83      ld   a,(0x83CE)
8252  5f            ld   e,a
8253  3a ca 83      ld   a,(0x83CA)
8256  8b            adc  a,e
8257  32 ca 83      ld   (0x83CA),a
825a  3a cf 83      ld   a,(0x83CF)    ; high byte of the multiplier
825d  5f            ld   e,a
825e  3a cb 83      ld   a,(0x83CB)    ; high byte of the product
8261  8b            adc  a,e
8262  32 cb 83      ld   (0x83CB),a
8265  af            xor  a             ; A = 0
8266  21 cc 83      ld   hl,0x83CC     ; HL on the multiplier
8269  cb 16         rl   (hl)          ; shift the byte left through carry
826b  23            inc  hl
826c  cb 16         rl   (hl)
826e  23            inc  hl
826f  cb 16         rl   (hl)
8271  23            inc  hl
8272  cb 16         rl   (hl)          ; the multiplier doubled
8274  10 b2         djnz 0x8228        ; B = B - 1, repeat until zero
8276  3a c8 83      ld   a,(0x83C8)    ; now add the constant 0x3039
8279  c6 39         add  a,0x39        ; low byte: +0x39
827b  32 c8 83      ld   (0x83C8),a
827e  3a c9 83      ld   a,(0x83C9)
8281  ce 30         adc  a,0x30        ; second byte: +0x30 and carry on
8283  32 c9 83      ld   (0x83C9),a
8286  3a ca 83      ld   a,(0x83CA)
8289  ce 00         adc  a,0            ; carry into the third byte
828b  32 ca 83      ld   (0x83CA),a
828e  3a cb 83      ld   a,(0x83CB)
8291  ce 00         adc  a,0            ; carry into the high byte
8293  32 cb 83      ld   (0x83CB),a
8296  21 c8 83      ld   hl,0x83C8     ; HL on the product
8299  11 c4 83      ld   de,0x83C4     ; DE on the state
829c  01 04 00      ld   bc,4
829f  ed b0         ldir               ; the result becomes the new state
82a1  c9            ret                ; back to the loop
```

What happens here in plain terms. Multiplying a 32-bit number by a 32-bit number in a processor that
can only add bytes is done the way long multiplication works on paper: on each of the 32 iterations
the lowest bit of the first number is examined, and if it is one, the second number is added to the
product, after which the first number is shifted right and the second shifted left, that is doubled.
After 32 iterations the product holds `state * multiplier` modulo `2^32`.

The commands `rr (hl)` and `rl (hl)` do exactly that: `rr` shifts a byte right and the bit that falls
out goes into the carry flag, `rl` shifts left and pulls the carry in. A chain of four such shifts
over neighbouring bytes shifts the whole 32-bit number. `ADD A,E` adds the low bytes, and `ADC A,E`
adds the following ones with the carry, so the carry travels from byte to byte and the number is
added as a whole rather than piecewise.

The constant is added the same bytewise way: `ADD A,0x39` and `ADC A,0x30` add `0x3039`, with `0x39`
in the low byte and `0x30` in the second, and two `ADC A,0` commands push the carry further. Since
the arithmetic is 32-bit, anything that leaves the high byte is simply lost, and that is exactly the
modulo `2^32`.

I verify that I read the commands correctly, numerically. The initial state is `0x19870419`, the
multiplier `0x41C64E6D`. The shift-and-add multiplication gives `0x86125CA5`, and ordinary
multiplication modulo `2^32` gives the same. After adding `0x3039` the result is `0x86128CDE`, and
that is exactly the state the solver shows on the first step. The routine is read correctly.

## 9. What a linear congruential generator is

Why this section: to name what was reconstructed and to explain how it works.

What came out is called a linear congruential generator, abbreviated LCG. It is the simplest kind of
pseudo-random number generator: it has a state, and every next state is computed by the formula

```
state = (a * state + c) mod m
```

where `a` is the multiplier, `c` the increment and `m` the modulus. Here `a = 0x41C64E6D`,
`c = 0x3039` and `m = 2^32`, because the processor adds 32-bit numbers and the extra bits are lost
by themselves. Those constants are well known: it is the standard pair from the Numerical Recipes
book, often used in teaching code and old programs.

The properties are simple. The state is always 32-bit, the next state depends only on the previous
one, and with the same initial value the sequence is always the same. That is why the task authors
could encrypt the text by XOR with the bytes of this sequence and be sure everyone who found the
date would get the same result. The word "pseudo-random" matters more than "random": the numbers
look random but are fully predictable once the initial value and the formula are known.

## 10. How bytes become letters

Why this section: to put the whole final stage together, because this is where understanding is
most often lost.

I go through the loop step by step, exactly as it is written in the code.

1. When the loop starts, four bytes at `0x83D4` are copied into the working area `0x83C4`. That is
   the initial generator state, `0x19870419`.
2. The counter at `0x83C2` is zeroed.
3. On every pass the counter is read, compared with 43, and when equal the loop ends.
4. The routine at `0x8202` is called, which performs one generator step. The new state stays at
   `0x83C4`.
5. A ciphertext byte is taken from the table at `0x8403`, at the index equal to the counter.
6. A byte is taken from the state at `0x83C6`. That is the third byte of the 32-bit number, that is
   bits 16 to 23, and it works as the key byte.
7. The ciphertext byte is combined with the key byte by XOR. XOR is exclusive or: the result is 1 if
   the bits differ and 0 if they are equal. The property that matters here is this: XOR with the
   same key twice returns the original value, which is why encryption and decryption are the same
   operation.
8. A position number is taken from the table at `0x83D8`, telling where to put this character.
9. The character is printed at that position on screen.

When assembling the string in Python the step 8 has to be honoured, otherwise the letters stand in
the wrong order:

```python
result[permutation[i]] = ciphertext[i] ^ key_byte
```

Here `permutation` is the 43 bytes of the table at `0x83D8`, `ciphertext` is the 43 bytes at
`0x8403`, and `key_byte` is the state byte from step 6.

The first five steps are easy to check by hand, and the solver prints them:

```
i=0 state=86128CDE key=12 cipher=3F plain='-' position=29
i=1 state=0E85CEBF key=85 cipher=B5 plain='0' position=12
i=2 state=5AB1698C key=B1 cipher=F6 plain='G' position=20
i=3 state=D9FAC8D5 key=FA cipher=CA plain='0' position=37
i=4 state=A2B698EA key=B6 cipher=F7 plain='A' position=3
```

This reads as follows. After the first generator step the state is `0x86128CDE`, the third byte of
that number is `0x12`, so the key is `0x12`. The ciphertext `0x3F` XOR `0x12` gives `0x2D`, the code
of the character `-`, and it goes to position 29. On the second step the state is `0x0E85CEBF`, the
key is `0x85`, `0xB5` turns into `0x30`, the character `0`, at position 12. And so on for all 43
characters.

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

## 11. The flag

Why this step: to fix the result and the part that is easy to spoil.

The string is assembled from 43 decrypted characters:

```
SOLARCTF{YuG...80Z}
```

I show the beginning and the end of the string on purpose, so it is visible that the flag was really
obtained.

Inside the body some characters are digits, not letters: a zero instead of the letter `O` and a one
instead of `I` occur in several places. Rewriting them as letters breaks the string, because the
check compares exact characters.

## 12. What did not work and why

- Speech recognition. There is speech at the start and at the end of the recording, but the data
  lives in the digital part, and speech-to-text over noise gives nothing.
- Searching for the flag with `strings`. Only the title and demo messages are visible in the code,
  while the 43 characters sit behind XOR and a permutation.
- The guess that `GALAKSIJA` means the Galaksija computer format. The name appears in the metadata
  and in the TAP header, but the container is a ZX Spectrum TAP and Z80 code runs inside.
- Full Spectrum emulation and reading all 16 KB. Most of the block is the byte `0x5A`, and the whole
  logic sits in a short loop, its tables and the date.
- Taking the low byte of the state as the key. The code uses the byte at `0x83C6`, that is bits 16
  to 23, and with the low byte the text does not come together.

## 13. In one paragraph

The recording turned out to be not a radio broadcast but a digital transmission of a program: a
1200-baud AFSK signal with tones at 1200 and 2400 Hz, 8N1 frames, synchronised on `0x55` at 14.99
seconds. The decoder restored a ZX Spectrum TAP of 16 659 bytes that matched the attached file byte
for byte, and the CRC carried inside the signal matched the one computed separately. The TAP has
four blocks: a BASIC loader with autostart at line 10 and 16 448 bytes of machine code at `0x8000`.
In that code I found a loop over 43 characters that takes the initial state `0x19870419` from the
task date, runs a generator with multiplier `0x41C64E6D` and increment `0x3039`, XORs the ciphertext
with the state byte from bits 16 to 23 and places the result according to the permutation table.

## 14. Conclusions

- The first action is listening: a recognisable cassette loading sound set the direction at once and
  saved hours that would have gone into speech recognition.
- A hint in the task text can be a technical instruction: the story about broadcasting programs over
  radio confirmed the guess.
- The format should be decided by the data, not by the file name: `ventilator303` points at a
  historical broadcast, while inside there is an ordinary ZX TAP.
- Checksums are a cheap verification along the whole path: the CRC in the signal header, the XOR
  checks in the TAP blocks and the byte comparison with the original file confirmed the decoder
  three times.
- Reconstructed math has to be verified numerically: the matching first state after the multiply and
  the addition confirmed the multiplier, the increment and the multiplication algorithm itself.
- Character order is part of the algorithm: without the permutation table the same bytes give a
  jumble.
