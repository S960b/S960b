# Ventilator303 (misc)

An audio forensics challenge. Given: `ventilator303.wav`, 168 seconds long, and a text about a
Balkan station that broadcast something useful for home computers at night, with the date 19 April
1987 in the description. The recording contains speech, signalling and a digital transmission of a
program.

About the recording:

- Uncompressed PCM, one channel, 16 bit, 44 100 Hz, about 14.8 MB. SHA-256
  `65c389277d5c21bb0dba559147884053f124756e2c8e35c8c5909bf4a70a05c5`.
- The digital part is AFSK with tones at 1200 and 2400 Hz at 1200 baud, 8N1 frames, synchronised on
  a preamble of `0x55` bytes at 14.99 seconds.
- The signal carries a text header `VENTILATOR-303 TAPE v1` with fields `FORMAT: ZXTAP`,
  `LEN: 16659` and `CSUM: 88D9`. That checksum is CRC-16/CCITT-FALSE over the whole file.

What the decoding produced:

- A ZX Spectrum TAP of 16 659 bytes, SHA-256
  `033e4ea05b182ba025468a8611c5bac7337a6388ab28b93037b4497977b45655`, byte-identical to the
  attached file.
- Four blocks: a BASIC header, 163 bytes of BASIC data, a code header and a 16 450-byte code block.
  The XOR of every block is zero.
- BASIC starts automatically at line 10, the 16 448 bytes of machine code load at `0x8000`, and
  15 378 of those bytes are `0x5A` padding.

What the code contains:

- A loop over 43 characters at `0x82A2` with the exit condition `CP 0x2B`.
- The generator seed at `0x83D4` is `0x19870419`, that is the date from the task text.
- The generator at `0x8202`: multiply by `0x41C64E6D` and add `0x3039` modulo `2^32`.
- The key byte comes from state bits 16..23 (address `0x83C6`), the ciphertext sits at `0x8403`,
  and the character order is set by the permutation table at `0x83D8`.

Files:

- `scripts/01_wav_to_tap.py` - AFSK and UART decoder, restores the TAP from the WAV
- `scripts/02_tap_to_flag.py` - checks the TAP blocks, extracts the Z80 code and decrypts the string
- `scripts/03_spectrum.py` - spectrogram of a fragment via numpy and matplotlib
- `assets/ventilator303.tap` - the recovered TAP
- `assets/fsk_detail.png` - spectrogram of the digital part, built by the third script

The 15 MB recording itself is not copied into the repository, it is part of the task input.

The full solving path is in `solve.md`.
