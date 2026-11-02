# Chik Chirik: recovering a message from a chirp transmitter

[По-русски](solve.ru.md)

## 0. Strategy

The input was a WAV recording. Listening to it did not directly reveal the flag. Its regular chirps suggested a digital modulation scheme. I treated the audio as an encoded data stream and separated the problem into layers.

The final pipeline was:

1. Recover the sequence of transmitted symbols from the WAV.
2. Identify the block code and correct symbol errors.
3. Model the pseudorandom transform.
4. Recover the generator's 128-bit internal state.
5. Undo the transform, parse the payload and check the CRC32.

The important point is that error correction and cryptographic-looking scrambling solve different problems. We need to know which layer acts first.

## 1. Demodulation and the symbol alphabet

The waveform carried symbols that could be represented in octal notation. A small alphabet is a natural fit for three-bit symbols, since each symbol can carry one value in the range 0 to 7.

The research scripts separated audio processing from decoding. `solve_chirp.py` started with `capture.wav`; `solve_from_octal.py` accepted an already demodulated stream in `capture.wav.octal.txt`. This separation was useful because a mistake in timing or frequency classification should not be confused with an error in the cryptographic reconstruction.

The exact signal frequencies and frame synchronization thresholds were not retained in the final text log. They belong to the original solver, not to a guessed replacement.

## 2. Identify RS(7,3) over GF(8)

One false lead was to treat the redundancy as a Hamming code. The recovered framing instead matched **Reed-Solomon RS(7,3)** over the field with 8 elements.

Each codeword contains 7 symbols, with 3 data symbols and 4 redundant symbols. The data capacity is therefore 9 bits per codeword.

If the plaintext is `L` bytes and has a four-byte CRC32 appended, the expected number of codewords is:

```text
B = ceil(8 * (L + 4) / 3)
```

Here the numerator counts bits in the message plus CRC, and the denominator is three bits per original data symbol. The exact implementation's framing turns those data symbols into seven-symbol RS blocks. This calculation helped check whether a decoded capture had a plausible length.

The recovered binary analysis identified the encoding routine around `0x211ac0`. Recognizing its finite-field operations was the turning point: once RS decoding produced clean symbols, errors from the recording stopped obscuring the later generator state.

## 3. xorshift128 is linear over GF(2)

The observed scrambler used xorshift128, a generator with four 32-bit state words. Its transitions consist of XOR and shifts. When modeled bit-by-bit, these are linear transformations over GF(2), the field with values 0 and 1.

If the 128 unknown initial state bits form a vector `s`, observed generator bits provide equations of the form

```text
A * s = b  (mod 2)
```

The critical observation is that we do not need to try all possible 128-bit seeds. We can build a binary linear system for the known state transition and solve it using Gaussian elimination. A full-rank system gives the unique state.

The research notes identified the state transition near `0x210f80` and the mixing logic near `0x220b29` to `0x220de3`. The recovered system reached rank 128 during testing.

One caution: xorshift is linear, but an unknown nonlinear permutation around it would invalidate a simple linear model. The original reverse engineering determined which recovered symbols yielded usable constraints before solving the system.

## 4. Undo the transform and check the CRC

With the initial state recovered, regenerate exactly the same pseudorandom sequence used by the transmitter. Apply the inverse mixing operation to recover payload bits, reconstruct bytes in the correct order and separate the final four bytes as CRC32.

A successful decode should pass both checks:

- The payload is valid text in the expected challenge format.
- The CRC32 agrees with the transmitted checksum.

The CRC is especially useful here. A plausible-looking fragment of text can be an accident, while a correct checksum gives an independent end-to-end validation.

## 5. Controlled tests and original task status

The archived tests included a 22-byte `A` message. The computed CRC was `c0bb810c`. Another controlled test recovered a full test phrase in the expected flag format using the exact 128-bit generator state. These were **tests**, not the actual contest flag.

The surviving report gives approximately 70 blocks for the short test and 144 blocks for a 50-byte capture. That is consistent with the size model and helps detect dropped symbols.

The decoder was packaged as `ctfcup_chirp_solver.zip`, with the following recorded commands:

```bash
python3 solve_chirp.py capture.wav
python3 solve_from_octal.py capture.wav.octal.txt
```

The user subsequently confirmed the original challenge solved, but the final literal flag from `capture.wav` is not in the surviving transcript. I therefore publish `ctfcup{...}` instead of substituting the controlled-seed test string.

## 6. What I would remember next time

When a signal looks random after demodulation, first check for a block code before attacking the PRNG. RS(7,3) and xorshift128 serve different purposes. The code corrects corrupted symbols; the state solver removes deterministic mixing. Keeping those layers separate made the task manageable.

This public note records the actual technique and the validation evidence; the precise demodulator source would need to be restored from the original solution archive for a bit-for-bit replay.
