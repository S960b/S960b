# Chik Chirik: demodulating a chirp transmitter

[По-русски](solve.ru.md)

## 0. What the audio contained

`capture.wav` was not speech. The signal was a sequence of frequency chirps with a fixed symbol length of 2048 samples, which is a digital modulation, not a recording of a flag read aloud. The transmitter binary in the same handout (`transmitter FLAG OUTPUT.wav`) confirmed it: it synthesises the recording from the flag and refuses payloads outside 8 to 4096 bytes.

The pipeline that finally worked, layer by layer:

1. Demodulate chirps into octal symbols.
2. Undo the block code, which turned out to be Reed-Solomon RS(7,3) over GF(8).
3. Model the scrambler, which is a linear xorshift128.
4. Recover its 128-bit state from linear equations over GF(2).
5. Undo the mixing, split the payload and verify the CRC32.

Why this order: error correction and scrambling solve different problems. Until symbols are clean, the scrambler looks random; until the scrambler is inverted, error correction cannot be validated.

## 1. Symbols from the waveform

Each chirp uses four frequency slopes and two phases, which gives eight states, and one symbol per 2048 samples. The recording starts with the preamble `24355342`, after which symbols come in groups of seven.

The demodulated stream was kept as a text of octal digits (`capture.wav.octal.txt`), so the audio part and the decoding part could be debugged separately. `scripts/decode_capture.py` is the demodulator, `scripts/solve_from_octal.py` accepts a stream that is already octal.

## 2. The block code is RS(7,3), not Hamming

The first reading was "blocks of seven with Hamming redundancy". That is wrong, and the mistake costs time: with Hamming the framing never lines up for the full recording.

The encoder near `0x211ac0` in the transmitter works over GF(8) with the reduction polynomial `x^3 + x + 1`. Seven symbols carry three data symbols and four redundant ones, so a codeword holds 9 bits of payload. `scripts/solve_chirp.py` builds the encoder table for all 512 triples and inverts it by matching the first three evaluations.

The frame law comes from the same routine: the number of blocks is

```text
B = ceil(8 * (L + 4) / 3)
```

where `L` is the payload length in bytes and the extra four bytes are the CRC32. For the captured file `B = 144`, hence `L = 50`. That number is derived, not guessed, which matters later when the payload length has to be known before the scrambler can be inverted.

## 3. The scrambler is linear, so the state is solvable

The mixing routine sits around `0x220b29` to `0x220de3` and the state update around `0x210f80`. It is xorshift128: four 32-bit words, only XOR and shifts. Every output bit is therefore a linear function of the 128 initial state bits, and the transcript of the recording leaks two of the three output bits per data symbol.

That turns key recovery into linear algebra:

```text
A * s = b  (mod 2)
```

Gaussian elimination over GF(2) reached rank 128 of 128, so the state is unique, not merely one of many. `scripts/gf2_gauss.py` is the small elimination helper used to check the rank while the equations were being assembled.

Why this step: brute forcing 128 bits is hopeless, while a rank-128 linear system is a solved problem. The whole challenge hinges on noticing that the generator is linear.

## 4. Decoding the recording

Two controlled tests came first, both produced with the original transmitter:

- 22 bytes of `A`: 70 blocks, recovered state `00000001 00000000 00000000 00000000`, payload `A` times 22, CRC32 `c0bb810c`.
- A 43-byte payload with seed `00112233445566778899aabbccddeeff`: recovered exactly, CRC32 matched.

Then the real capture:

```text
chirps: 1016  preamble: 24355342
FEC blocks: 144  invalid: 0
raw symbols: 432
probable length: 50
xorshift rank: 128 of 128
PRNG state: 8bf31bf8 886c0791 8d3e0dc3 1557904c
CRC32 (LE): 2a2617cc
```

`invalid: 0` means every RS codeword decoded without a residual error, and the payload text is a well formed `ctfcup{...}` with 4-byte CRC matching the transmitted one. The flag is masked here as `ctfcup{...}`; it contains `$` and `?`, so the alphabet is wider than the plain hex flags of the same event.

Run it yourself:

```bash
python3 scripts/solve_chirp.py capture.wav
python3 scripts/solve_from_octal.py capture.wav.octal.txt
```

The first form needs numpy and scipy for the WAV reading, the second only the standard library.

## 5. Dead ends

- Treating the redundancy as Hamming gave a consistent framing for short tests and broke on the real file.
- Trying to guess the 16 random seed bytes from the noise floor was a dead end for a simple reason: the seed never appears in the audio, it only drives the generator. The correct move was to solve for the state instead of searching for it.
- Differential tricks (comparing two runs of the same flag, or two flags with one changed character) fail because the mixing is data dependent, so a single byte change avalanches through the stream.

## 6. Lessons

- Check for a block code before attacking the pseudorandom layer.
- A linear generator with leaked output bits is a linear system, not a key search.
- Derive the payload length from the frame law; it removes one unknown from the whole chain.

One paragraph: the recording is a chirp modulation of an RS(7,3) code over GF(8) followed by a linear xorshift128 scrambler; demodulation gives 144 clean codewords, the frame law fixes the payload at 50 bytes, the leaked output bits give a rank-128 linear system for the generator state, and after inverting the mixing the payload plus CRC32 reproduces the flag.
